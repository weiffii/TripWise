"""Искусственные ответы OpenRouter: без сети, ключей и платных запросов."""
from copy import deepcopy
import io
import json
import unittest
from urllib.error import HTTPError, URLError

from ai_ds.openrouter import MAX_RESPONSE_BYTES, OpenRouterError, OpenRouterInterpreter


class OpenRouterTests(unittest.TestCase):
    def setUp(self):
        self.payload = {'text': 'Сделай день спокойнее', 'timezone': 'Europe/Moscow',
                        'days': [{'date': '2026-11-17', 'visits': []}], 'catalog': [],
                        'required_poi_ids': []}
        self.decision = {'action': 'propose', 'operations': [
            {'op': 'set_day_pace', 'day': '2026-11-17', 'pace': 'relaxed'}], 'clarification': None}
        self.response = {'model': 'mock/free-model', 'choices': [
            {'finish_reason': 'stop', 'message': {'content': json.dumps(self.decision)}}]}

    def interpreter(self, value=None):
        def opener(request, timeout):
            return io.BytesIO(json.dumps(self.response if value is None else value).encode('utf-8'))
        return OpenRouterInterpreter(key='mock-secret-key', opener=opener)

    def test_success_json_and_input_not_mutated(self):
        before = deepcopy(self.payload)
        interpreter = self.interpreter()
        self.assertEqual(interpreter(self.payload), self.decision)
        self.assertEqual(self.payload, before)
        self.assertFalse(interpreter.last_metadata['paid_fallback_enabled'])
        self.assertEqual(interpreter.last_metadata['selected_model'], 'mock/free-model')

    def test_request_is_free_only_and_minimizes_context(self):
        captured = []
        def opener(request, timeout):
            captured.append((request, timeout))
            return io.BytesIO(json.dumps(self.response).encode())
        interpreter = OpenRouterInterpreter(key='mock-secret-key', timeout=12, opener=opener)
        self.payload['raw_snapshot'] = {'private': 'not-for-llm'}
        self.payload['owner_email'] = 'private@example.test'
        interpreter(self.payload)
        request, timeout = captured[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, 'https://openrouter.ai/api/v1/chat/completions')
        self.assertEqual(timeout, 12)
        self.assertEqual(body['model'], 'openrouter/free')
        self.assertNotIn('models', body)
        self.assertEqual(body['provider']['max_price'], {'prompt': 0, 'completion': 0, 'request': 0})
        self.assertEqual(body['provider']['data_collection'], 'deny')
        self.assertNotIn('not-for-llm', request.data.decode())
        self.assertNotIn('private@example.test', request.data.decode())
        self.assertNotIn('mock-secret-key', request.data.decode())

    def test_nested_snapshot_fields_are_not_sent(self):
        captured = []
        def opener(request, timeout):
            captured.append(json.loads(request.data))
            return io.BytesIO(json.dumps(self.response).encode())
        self.payload['days'] = [{'date': '2026-11-17', 'owner_secret': 'nested-private',
                                 'visits': [{'poi_id': 'test-id', 'notes': 'nested-private'}]}]
        self.payload['catalog'] = [{'id': 'test-id', 'categories': ['museums'],
                                    'provenance': {'secret': 'nested-private'}}]
        OpenRouterInterpreter(key='mock-secret-key', opener=opener)(self.payload)
        self.assertNotIn('nested-private', json.dumps(captured))
        self.assertIn('test-id', json.dumps(captured))

    def test_rich_context_includes_current_transport_without_private_fields(self):
        captured = []

        def opener(request, timeout):
            captured.append(json.loads(request.data)['messages'][-1]['content'])
            return io.BytesIO(json.dumps(self.response).encode())

        interpreter = OpenRouterInterpreter(key='mock-secret-key', opener=opener, rich_context=True)
        self.payload.update(transport='transit', transit_filters={
            'avoid_transit': ['metro'], 'require_transit': ['mcc'], 'private': 'hidden-value',
        })
        interpreter(self.payload)
        context = json.loads(captured[0])
        self.assertEqual(context['transport'], 'transit')
        self.assertEqual(context['transit_filters'], {'avoid_transit': ['metro'], 'require_transit': ['mcc']})
        self.assertNotIn('hidden-value', captured[0])
        self.payload['transit_filters']['require_transit'] = ['invalid-mode']
        with self.assertRaises(OpenRouterError) as caught:
            interpreter(self.payload)
        self.assertEqual(caught.exception.code, 'LLM_INVALID_CONTEXT')
        self.assertEqual(len(captured), 1)

    def test_no_key_makes_no_request(self):
        calls = []
        interpreter = OpenRouterInterpreter(key='', opener=lambda *args, **kw: calls.append(1))
        with self.assertRaises(OpenRouterError) as caught:
            interpreter(self.payload)
        self.assertEqual(caught.exception.code, 'LLM_KEY_NOT_CONFIGURED')
        self.assertEqual(calls, [])

    def test_http_statuses_no_retry_close_body_no_secret(self):
        for status, code in [(401, 'LLM_ACCESS_DENIED'), (402, 'LLM_ACCESS_DENIED'),
                             (403, 'LLM_ACCESS_DENIED'), (429, 'LLM_RATE_LIMITED'), (503, 'LLM_HTTP_ERROR')]:
            with self.subTest(status=status):
                stream, calls = io.BytesIO(b'mock-secret-key'), []
                def denied(request, timeout):
                    calls.append(1)
                    raise HTTPError('https://example.test/mock-secret-key', status, 'mock-secret-key', {}, stream)
                interpreter = OpenRouterInterpreter(key='mock-secret-key', opener=denied)
                with self.assertRaises(OpenRouterError) as caught:
                    interpreter(self.payload)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.status, status)
                self.assertNotIn('mock-secret-key', str(caught.exception.response()))
                self.assertEqual(calls, [1])
                self.assertTrue(stream.closed)

    def test_network_error_redaction(self):
        def failed(*args, **kwargs):
            raise URLError('mock-secret-key')
        with self.assertRaises(OpenRouterError) as caught:
            OpenRouterInterpreter(key='mock-secret-key', opener=failed)(self.payload)
        self.assertEqual(caught.exception.code, 'LLM_NETWORK_ERROR')
        self.assertNotIn('mock-secret-key', str(caught.exception))

    def test_refuse_truncated_or_filtered_completion(self):
        for reason in ['length', 'content_filter', 'error', None]:
            response = deepcopy(self.response)
            response['choices'][0]['finish_reason'] = reason
            with self.subTest(reason=reason), self.assertRaises(OpenRouterError) as caught:
                self.interpreter(response)(self.payload)
            self.assertEqual(caught.exception.code, 'LLM_INCOMPLETE_RESPONSE')

    def test_provider_errors_are_not_exposed(self):
        with self.assertRaises(OpenRouterError) as caught:
            self.interpreter({'error': {'message': 'mock-secret-key'}})(self.payload)
        self.assertNotIn('mock-secret-key', str(caught.exception))

    def test_json_is_strict_no_duplicate_or_nonfinite(self):
        for raw in [b'{"choices":[],"choices":[]}', b'{"value":NaN}', b'not json', b'\xff']:
            def opener(*args, **kwargs):
                return io.BytesIO(raw)
            with self.subTest(raw=raw), self.assertRaises(OpenRouterError):
                OpenRouterInterpreter(key='mock-secret-key', opener=opener)(self.payload)

    def test_oversize_and_credential_echo_rejected(self):
        for raw, code in [(b'x' * (MAX_RESPONSE_BYTES + 1), 'LLM_RESPONSE_TOO_LARGE'),
                          (b'mock-secret-key', 'LLM_CREDENTIAL_ECHO_REJECTED')]:
            def opener(*args, **kwargs):
                return io.BytesIO(raw)
            with self.assertRaises(OpenRouterError) as caught:
                OpenRouterInterpreter(key='mock-secret-key', opener=opener)(self.payload)
            self.assertEqual(caught.exception.code, code)

    def test_invalid_top_level_decisions(self):
        for decision in [[], {'action': 'propose', 'operations': [], 'clarification': None},
                         {'action': 'needs_clarification', 'operations': [], 'clarification': None},
                         {'action': 'execute_code', 'operations': [], 'clarification': None},
                         dict(self.decision, extra='command')]:
            response = deepcopy(self.response)
            response['choices'][0]['message']['content'] = json.dumps(decision)
            with self.subTest(decision=decision), self.assertRaises(OpenRouterError):
                self.interpreter(response)(self.payload)

    def test_clarification_is_preserved_without_execution(self):
        decision = {'action': 'needs_clarification', 'operations': [], 'clarification': 'Какой день?'}
        response = deepcopy(self.response)
        response['choices'][0]['message']['content'] = json.dumps(decision)
        self.assertEqual(self.interpreter(response)(self.payload), decision)

    def test_invalid_configuration_and_context(self):
        for options in [{'timeout': True}, {'timeout': float('nan')}, {'timeout': 0},
                        {'max_output_tokens': True}, {'max_output_tokens': 0}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                OpenRouterInterpreter(key='mock-secret-key', **options)
        for payload in [None, {}, {'text': ''}, {'text': 1}, {'text': 'hello', 'catalog': float('nan')}]:
            with self.subTest(payload=payload), self.assertRaises(OpenRouterError):
                self.interpreter()(payload)

    def test_metadata_reset_after_subsequent_failure(self):
        interpreter = self.interpreter()
        interpreter(self.payload)
        with self.assertRaises(OpenRouterError):
            interpreter({})
        self.assertIsNone(interpreter.last_metadata)


if __name__ == '__main__':
    unittest.main()
