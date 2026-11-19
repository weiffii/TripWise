"""Чат/API: mock LLM, настоящий JS scheduler; внешних запросов нет."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
import urllib.request
import urllib.error
import http.cookiejar

from ai_ds.shanghai_assistant import ShanghaiAssistantService, parse_operations, bridge_call, PROMPT
from ai_ds.openrouter import OpenRouterInterpreter
from ai_ds.route_assistant import AssistantError

ROOT = Path(__file__).resolve().parents[1]


class Replies:
    def __init__(self, values):
        self.values, self.payloads = list(values), []
    def __call__(self, payload):
        self.payloads.append(deepcopy(payload))
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)


def proposal(*ops):
    return {'action': 'propose', 'operations': list(ops), 'clarification': None}


class AssistantAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.options = {'primary_interests': ['history', 'museums'], 'secondary_interests': ['nature'], 'days': 2,
                       'pace': 'balanced', 'transport': 'mixed', 'start_date': '2026-11-20', 'day_start': '09:00',
                       'day_end': '20:00', 'budget_cny': 4000, 'group': {'adult': 1, 'child': 0, 'senior': 0, 'pet': 0}, 'required_ids': []}
        source = "const fs=require('fs'),api=require('./ai_ds/web_demo/shanghai-engine.js');const d=JSON.parse(fs.readFileSync('data/demo/shanghai/pois.json','utf8'));d.transport=JSON.parse(fs.readFileSync('data/demo/shanghai/transport.json','utf8'));const p=api.planTrip(d," + json.dumps(cls.options) + ");p.ranking=[];process.stdout.write(JSON.stringify(p));"
        result = subprocess.run([shutil.which('node'), '-e', source], cwd=ROOT, capture_output=True, encoding='utf-8', timeout=30)
        if result.returncode:
            raise RuntimeError(result.stderr)
        cls.plan = json.loads(result.stdout)
        cls.target = cls.plan['days'][0]['visit_ids'][0]
        cls.date = cls.plan['days'][0]['date']

    def setup_service(self, responses):
        interpreter = Replies(responses)
        service = ShanghaiAssistantService(interpreter=interpreter, mode='mock')
        token, status = service.status()
        response = service.request(token, status['csrf_token'], 'register', {'plan': self.plan, 'options': self.options, 'request_id': 'register-001'})
        self.assertEqual(response['type'], 'registered', response)
        return service, token, status['csrf_token'], response, interpreter

    def message(self, service, token, csrf, state, text='Убери первую достопримечательность', rid='message-001'):
        return service.request(token, csrf, 'message', {'route_id': state['route_id'], 'base_version': state['version'],
                               'poi_snapshot_id': state['poi_snapshot_id'], 'text': text, 'request_id': rid})

    def decide(self, service, token, csrf, p, decision='confirm', rid='decision-001'):
        return service.request(token, csrf, 'decide', {'proposal_id': p['proposal_id'], 'base_version': p['base_version'], 'decision': decision, 'request_id': rid})

    def test_preview_confirm_real_js_and_preserves_other_day(self):
        s, t, c, r, llm = self.setup_service([proposal({'op': 'remove_poi', 'poi_id': self.target})])
        p = self.message(s, t, c, r)
        self.assertEqual(p['type'], 'proposal', p)
        before = s.sessions[t]['assistant'].store.get_route(t, r['route_id'])
        self.assertEqual(before['version'], 1)
        self.assertIn(self.target, before['route']['native_route']['selected_ids'])
        self.assertEqual(p['plan']['days'][1], self.plan['days'][1])
        confirmed = self.decide(s, t, c, p)
        self.assertEqual(confirmed['type'], 'confirmed', confirmed)
        self.assertEqual(confirmed['version'], 2)
        self.assertNotIn(self.target, confirmed['plan']['selected_ids'])
        self.assertTrue(bridge_call({'action': 'validate', 'options': confirmed['options'], 'plan': confirmed['plan']})['valid'])

    def test_reject_keeps_snapshot_and_confirm_replay_idempotent(self):
        s, t, c, r, llm = self.setup_service([proposal({'op': 'remove_poi', 'poi_id': self.target})])
        p = self.message(s, t, c, r)
        result = self.decide(s, t, c, p, 'reject')
        self.assertEqual(result['type'], 'rejected')
        self.assertEqual(s.sessions[t]['assistant'].store.get_route(t, r['route_id'])['route']['native_route'], r['plan'])
        self.assertEqual(r['plan']['days'], self.plan['days'])
        self.assertEqual(self.decide(s, t, c, p, 'reject'), result)

    def test_message_retry_does_not_repeat_llm(self):
        s, t, c, r, llm = self.setup_service([proposal({'op': 'remove_poi', 'poi_id': self.target})])
        first = self.message(s, t, c, r)
        self.assertEqual(self.message(s, t, c, r), first)
        self.assertEqual(len(llm.payloads), 1)

    def test_clarification_retains_original_user_intent(self):
        s, t, c, r, llm = self.setup_service([{'action': 'needs_clarification', 'operations': [], 'clarification': 'В какой день?'}, proposal({'op': 'remove_poi', 'poi_id': self.target})])
        self.assertEqual(self.message(s, t, c, r, text='Убери музей')['type'], 'clarification')
        second = self.message(s, t, c, r, text='В первый', rid='message-002')
        self.assertEqual(second['type'], 'proposal', second)
        self.assertEqual(llm.payloads[1]['conversation'][0]['content'], 'Убери музей')
        self.assertEqual(llm.payloads[1]['conversation'][1]['content'], 'В какой день?')

    def test_complex_request_is_atomic(self):
        s, t, c, r, llm = self.setup_service([proposal({'op': 'remove_poi', 'poi_id': self.target}, {'op': 'set_day_pace', 'day': self.date, 'pace': 'relaxed'})])
        p = self.message(s, t, c, r, text='Убери первую достопримечательность и сделай этот день спокойнее')
        self.assertEqual(p['type'], 'proposal', p)
        self.assertNotIn(self.target, p['plan']['selected_ids'])
        self.assertEqual(p['options']['day_settings'][self.date]['pace'], 'relaxed')

    def test_failed_compound_request_does_not_apply_easy_part(self):
        s, t, c, r, llm = self.setup_service([proposal({'op': 'remove_poi', 'poi_id': self.target}, {'op': 'set_day_end', 'day': self.date, 'end_time': '09:01'})])
        p = self.message(s, t, c, r)
        self.assertEqual(p['type'], 'error', p)
        self.assertIn(self.target, s.sessions[t]['assistant'].store.get_route(t, r['route_id'])['route']['native_route']['selected_ids'])

    def test_stale_confirmation_is_rejected(self):
        op = proposal({'op': 'remove_poi', 'poi_id': self.target})
        s, t, c, r, llm = self.setup_service([op, op])
        a = self.message(s, t, c, r)
        b = self.message(s, t, c, r, rid='message-002')
        self.assertEqual(self.decide(s, t, c, a)['type'], 'confirmed')
        stale = self.decide(s, t, c, b, rid='decision-002')
        self.assertEqual(stale['code'], 'VERSION_CONFLICT')

    def test_llm_unavailable_preserves_route(self):
        s, t, c, r, llm = self.setup_service([RuntimeError('provider unavailable')])
        answer = self.message(s, t, c, r)
        self.assertEqual(answer['type'], 'unavailable')
        self.assertEqual(s.sessions[t]['assistant'].store.get_route(t, r['route_id'])['version'], 1)

    def test_no_cross_session_access(self):
        s, t, c, r, llm = self.setup_service([])
        other, status = s.status()
        result = self.message(s, other, status['csrf_token'], r)
        self.assertEqual(result['code'], 'ROUTE_NOT_FOUND')

    def test_csrf_and_idempotency_conflict(self):
        s, t, c, r, llm = self.setup_service([])
        with self.assertRaises(AssistantError):
            self.message(s, t, 'bad-token', r)
        with self.assertRaises(AssistantError):
            s.request(t, c, 'register', {'plan': self.plan, 'options': {**self.options, 'days': 1}, 'request_id': 'register-001'})

    def test_context_is_minimized_and_visit_type_is_preserved(self):
        s, t, c, r, llm = self.setup_service([{'action': 'needs_clarification', 'operations': [], 'clarification': 'Какое место?'}])
        self.message(s, t, c, r)
        payload = llm.payloads[0]
        self.assertLessEqual(len(payload['catalog']), 60)
        self.assertLessEqual(sum(len(d['visits']) for d in payload['days']), 60)
        self.assertTrue(all(v['type'] in ('visit', 'meal') for d in payload['days'] for v in d['visits']))
        self.assertNotIn('pois', payload)

    def test_real_interpreter_minimizes_actual_rich_payload_with_numeric_prices(self):
        sent = []
        decision = {'action': 'needs_clarification', 'operations': [], 'clarification': 'Какой день изменить?'}
        def opener(request, timeout):
            sent.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'model': 'mock/free-model', 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(decision)}}]}).encode())
        interpreter = OpenRouterInterpreter(key='mock-unit-test-secret', system_prompt=PROMPT, rich_context=True, opener=opener)
        service = ShanghaiAssistantService(interpreter=interpreter, mode='mock')
        token, status = service.status()
        state = service.request(token, status['csrf_token'], 'register', {'plan': self.plan, 'options': self.options, 'request_id': 'rich-register-001'})
        response = self.message(service, token, status['csrf_token'], state)
        self.assertEqual(response['type'], 'clarification', response)
        self.assertEqual(len(sent), 1)
        safe = json.loads(sent[0]['messages'][1]['content'])
        self.assertTrue(all(type(p['price_cny']) in (int, float) for p in safe['catalog']))
        self.assertLessEqual(len(safe['catalog']), 60)
        self.assertTrue(all('type' in v and 'name' in v for d in safe['days'] for v in d['visits']))
        self.assertNotIn('mock-unit-test-secret', json.dumps(sent))

    def test_invalid_and_invented_operations_rejected(self):
        with self.assertRaises(AssistantError):
            parse_operations(proposal({'op': 'execute', 'code': 'anything'}))
        s, t, c, r, llm = self.setup_service([proposal({'op': 'add_poi', 'day': None, 'poi_id': 'invented', 'desired_category': None, 'near_poi_id': None})])
        self.assertEqual(self.message(s, t, c, r)['code'], 'UNKNOWN_POI_ID')

    def test_untrusted_prices_and_checks_cannot_register(self):
        s = ShanghaiAssistantService(interpreter=Replies([]), mode='mock')
        t, status = s.status(); bad = deepcopy(self.plan)
        next(e for d in bad['days'] for e in d['events'] if e['type'] == 'visit')['price_cny'] = -10
        bad['checks'] = {k: 'passed' for k in bad['checks']}
        result = s.request(t, status['csrf_token'], 'register', {'options': self.options, 'plan': bad, 'request_id': 'register-002'})
        self.assertEqual(result['type'], 'error')

    def test_client_summary_and_status_are_derived_from_verified_events(self):
        s = ShanghaiAssistantService(interpreter=Replies([]), mode='mock')
        t, status = s.status(); claims = deepcopy(self.plan)
        claims['summary']['visit_count'] = 0
        claims['selected_ids'] = []
        claims['status'] = 'infeasible'
        response = s.request(t, status['csrf_token'], 'register', {'options': self.options, 'plan': claims, 'request_id': 'claims-register-001'})
        self.assertEqual(response['type'], 'registered', response)
        self.assertEqual(response['plan']['days'], self.plan['days'])
        self.assertEqual(response['plan']['summary']['visit_count'], self.plan['summary']['visit_count'])
        self.assertEqual(response['plan']['selected_ids'], self.plan['selected_ids'])
        self.assertEqual(response['plan']['status'], 'demo')

    def test_transport_snapshot_drift_rejects_without_file_mutation(self):
        s, t, c, r, llm = self.setup_service([])
        path = next(iter(s.input_hashes)); s.input_hashes[path] = 'changed-expected-hash'
        result = self.message(s, t, c, r)
        self.assertEqual(result['code'], 'INPUT_DATA_CHANGED')

    def test_http_status_cookie_csrf_and_origin(self):
        spec = importlib.util.spec_from_file_location('demo_server_test', ROOT / 'scripts/serve_shanghai_demo.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        service = ShanghaiAssistantService(interpreter=Replies([]), mode='mock')
        server = module.create_server(0, assistant_service=service)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            base = 'http://127.0.0.1:' + str(server.server_port)
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            with opener.open(base + '/api/assistant/status') as response:
                status = json.load(response); self.assertIn('HttpOnly', response.headers['Set-Cookie']); self.assertEqual(status['mode'], 'mock')
            body = json.dumps({'options': self.options, 'plan': self.plan, 'request_id': 'http-register-001'}).encode()
            request = urllib.request.Request(base + '/api/assistant/register', data=body, headers={'Content-Type': 'application/json', 'X-TripWise-CSRF': status['csrf_token'], 'Origin': 'https://foreign.invalid'})
            with self.assertRaises(urllib.error.HTTPError) as error:
                opener.open(request)
            self.assertEqual(error.exception.code, 403)
            request = urllib.request.Request(base + '/api/assistant/register', data=body, headers={'Content-Type': 'application/json', 'X-TripWise-CSRF': status['csrf_token'], 'Origin': base})
            with opener.open(request) as response:
                self.assertEqual(json.load(response)['type'], 'registered')
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
