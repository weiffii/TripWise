"""Предпросмотр/сохранение: настоящий HTTP и временная SQLite.

Для этих контрактных тестов генератор заменён контролируемым ответом.
Живой алгоритм проверяется отдельно: эти тесты не доказывают качество маршрута.
"""
from copy import deepcopy
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from urllib.request import build_opener, HTTPCookieProcessor
from urllib.error import URLError

from test_accounts import LiveServerTests, NoRedirect, test_database
from test_drafts import valid_preferences


STUB_SERVER = '''
import backend.shanghai as sh
sh.catalog = lambda city: [{'id': city + '-known', 'name': 'Контрактный тестовый объект'}]
def build(p):
    return {'status':'feasible','days':[],'ranking':[]}, p.options(), 'synthetic-contract-test'
sh.build_plan = build
import uvicorn
uvicorn.run('backend.main:app', host='127.0.0.1', port=PORT, log_level='warning')
'''


def form(city='Москва'):
    p = valid_preferences()
    p.update(schema_version='fullstack-draft-0.2', first_walk_at='2026-11-24T10:00:00',
             last_walk_end_at='2026-11-25T18:00:00', city=city,
             country_code='RU' if city == 'Москва' else 'CN',
             timezone='Europe/Moscow' if city == 'Москва' else 'Asia/Shanghai')
    return p


class RoutePreviewTests(LiveServerTests):
    @classmethod
    def start_server(cls):
        env = os.environ.copy()
        env['TRIPWISE_DB_PATH'] = str(cls.db_path)
        env.pop('OPENROUTER_API_KEY', None)
        cls.process = subprocess.Popen([sys.executable, '-c', STUB_SERVER.replace('PORT', str(cls.port))],
                                       cwd=Path(__file__).resolve().parents[2], env=env, stdout=cls.log, stderr=cls.log)
        for _ in range(160):
            if cls.process.poll() is not None:
                cls.log.seek(0)
                raise AssertionError('Тестовый сервер не запустился: ' + cls.log.read())
            try:
                with build_opener().open(cls.base + '/api/health', timeout=.3) as r:
                    if r.status == 200:
                        return
            except (URLError, TimeoutError):
                time.sleep(.05)
        raise AssertionError('Нет ответа временного сервера')

    def setUp(self):
        super().setUp()
        self.assertEqual(self.register()[0], 201)
        self.owner = self.request('/api/trips')[1]['owner_context']
        self.headers = {'X-TripWise-Draft-Owner': self.owner}

    def preview(self, preferences=None, source=None, **kwargs):
        body = {'form_preferences': preferences or form()}
        if source:
            body['source_preview_id'] = source
        return self.request('/api/route-previews', body, headers=self.headers, **kwargs)

    def save(self, p, version=None, **kwargs):
        return self.request('/api/route-previews/' + p['preview_id'] + '/save',
                            {'version': version or p['version']}, headers=self.headers, **kwargs)

    def test_build_does_not_persist_and_save_is_idempotent(self):
        status, p, _ = self.preview()
        self.assertEqual(status, 201)
        self.assertTrue(p['is_preview'])
        self.assertEqual(self.request('/api/planned-trips')[1]['trips'], [])
        with test_database(self.db_path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM saved_trips WHERE user_id=?', (int(self.owner),)).fetchone()[0], 0)
        status, first, _ = self.save(p)
        self.assertEqual(status, 200)
        self.assertFalse(first['is_preview'])
        self.assertEqual(first['form_preferences']['budget_scope'], 'trip_food_visits_and_transport')
        self.assertEqual(self.save(p)[1]['route_id'], first['route_id'])
        self.assertEqual(len(self.request('/api/planned-trips')[1]['trips']), 1)
        self.assertEqual(first['form_preferences']['primary_interests'], form()['primary_interests'])

    def test_two_routes_edit_fork_and_delete_are_independent(self):
        first = self.save(self.preview()[1])[1]
        second = self.save(self.preview(form('Шанхай'))[1])[1]
        status, fork, _ = self.request('/api/planned-trips/' + first['route_id'] + '/preview',
                                       {'version': first['version']}, headers=self.headers)
        self.assertEqual(status, 201)
        edited = form()
        edited['budget_amount'] = '18000.00'
        status, rebuilt, _ = self.preview(edited, fork['preview_id'])
        self.assertEqual(status, 201)
        self.assertEqual(self.request('/api/planned-trips/' + first['route_id'])[1]['form_preferences']['budget_amount'], form()['budget_amount'])
        saved = self.save(rebuilt)[1]
        self.assertEqual(saved['route_id'], first['route_id'])
        self.assertEqual(saved['version'], 2)
        self.assertEqual(saved['form_preferences']['budget_amount'], '18000.00')
        self.assertEqual(self.request('/api/planned-trips/' + second['route_id'])[1]['version'], 1)
        self.assertEqual(self.request('/api/planned-trips/' + second['route_id'], {'version': 1}, method='DELETE', headers=self.headers)[0], 204)
        self.assertEqual(self.request('/api/planned-trips/' + second['route_id'])[0], 404)
        self.assertEqual(len(self.request('/api/planned-trips')[1]['trips']), 1)

    def test_conflicts_preserve_source(self):
        saved = self.save(self.preview()[1])[1]
        path = '/api/planned-trips/' + saved['route_id']
        one = self.request(path + '/preview', {'version': 1}, headers=self.headers)[1]
        two = self.request(path + '/preview', {'version': 1}, headers=self.headers)[1]
        self.assertEqual(self.save(one)[0], 200)
        self.assertEqual(self.save(two)[0], 409)
        self.assertEqual(self.request(path, {'version': 1}, method='DELETE', headers=self.headers)[0], 409)
        self.assertEqual(self.request(path)[1]['version'], 2)

    def test_preview_bound_to_exact_session_and_owner(self):
        p = self.preview()[1]
        path = '/api/route-previews/' + p['preview_id']
        other = build_opener(HTTPCookieProcessor(CookieJar()), NoRedirect())
        self.register('preview-other@example.com', other)
        self.assertEqual(self.request(path, client=other)[0], 404)
        self.assertEqual(self.save(p, client=other)[0], 409)
        self.request('/api/auth/logout', {})
        self.request('/api/auth/login', {'email': self.email, 'password': 'TripWise-Test-2026'})
        self.assertEqual(self.request(path)[0], 404)

    def test_origin_owner_and_foreign_saved_guards(self):
        self.assertEqual(self.preview(origin='https://other.example')[0], 403)
        self.assertEqual(self.request('/api/route-previews', {'form_preferences': form()})[0], 428)
        saved = self.save(self.preview()[1])[1]
        other = build_opener(HTTPCookieProcessor(CookieJar()), NoRedirect())
        self.register('guard-other@example.com', other)
        context = self.request('/api/trips', client=other)[1]['owner_context']
        path = '/api/planned-trips/' + saved['route_id']
        self.assertEqual(self.request(path, client=other)[0], 404)
        self.assertEqual(self.request(path + '/preview', {'version': 1}, headers={'X-TripWise-Draft-Owner': context}, client=other)[0], 404)
        self.assertEqual(self.request(path, {'version': 1}, method='DELETE', headers={'X-TripWise-Draft-Owner': context}, client=other)[0], 404)

    def test_adapter_rejects_inactive_currency_long_dates_and_unknown_ids(self):
        for key, value in [('city', 'Париж'), ('budget_currency', 'USD'),
                           ('last_walk_end_at', '2026-12-10T18:00:00'), ('required_poi_ids', ['local:moscow:red-square'])]:
            data = form()
            data[key] = value
            with self.subTest(field=key):
                self.assertEqual(self.preview(data)[0], 422)
        data = form('Шанхай')
        data['required_poi_ids'] = ['shanghai-known']
        self.assertEqual(self.preview(data)[0], 201)
        self.assertEqual(self.request('/api/planned-trips')[1]['trips'], [])

    def test_all_interests_and_per_group_mapping_preserved(self):
        data = form('Шанхай')
        data.update(primary_interests=['museums','history','nature','cafes','entertainment','shopping','viewpoints','unusual'],
                    secondary_interests=[], budget_basis='group', budget_currency='CNY')
        status, p, _ = self.preview(data)
        self.assertEqual(status, 201)
        self.assertEqual(p['preferences']['primary_interests'], data['primary_interests'])
        self.assertEqual(p['preferences']['budget_basis'], 'per_group')
        self.assertEqual(p['preferences']['group']['adult'], 3)
        self.assertEqual(p['preferences']['transport_modes'], ['walk','transit'])
        self.assertEqual(p['preferences']['day_settings']['2026-11-24']['day_start'], '10:00')
        self.assertEqual(p['preferences']['day_settings']['2026-11-25']['day_end'], '18:00')


class PreviewExpiryTests(unittest.TestCase):
    def test_expired_cache_is_not_readable(self):
        from fastapi import HTTPException
        from starlette.requests import Request
        from backend.route_previews import _previews, preview_owned
        req = Request({'type': 'http', 'headers': [(b'cookie', b'tripwise_session=synthetic')]})
        key = 'preview-synthetic-expired'
        _previews[key] = {'expires_at': 0}
        with self.assertRaises(HTTPException) as error:
            preview_owned(key, 1, req)
        self.assertEqual(error.exception.status_code, 404)
        self.assertNotIn(key, _previews)

    def test_confirmed_assistant_change_and_conversation_stay_in_memory(self):
        from unittest.mock import patch
        from starlette.requests import Request
        from backend.route_previews import (_previews, _put_preview, apply_preview_change,
                                             append_preview_conversation, preview_owned)
        req = Request({'type': 'http', 'headers': [(b'cookie', b'tripwise_session=synthetic-memory-only')]})
        user = {'id': 987654}
        route = {'city': 'moscow', 'version': 1, 'plan': {'days': []}, 'options': {}}
        p = _put_preview(route, {'city': 'Москва'}, user, req)
        key = p['preview_id']
        try:
            with patch('backend.route_previews.database', side_effect=AssertionError('SQL before Save')):
                append_preview_conversation(key, user['id'], req, 1,
                                            {'text': 'Добавить музей', 'request_id': 'test-message'}, 'Предлагаю изменения')
                apply_preview_change(key, user, req, 1, {'days': [{'visits': ['synthetic-test-place']}]}, {'pace': 'relaxed'})
                changed = preview_owned(key, user['id'], req)
            self.assertEqual(changed['version'], 2)
            self.assertEqual(changed['plan']['days'][0]['visits'], ['synthetic-test-place'])
            self.assertEqual(len(changed['options']['conversation']), 2)
            self.assertEqual(changed['form_preferences']['city'], 'Москва')
        finally:
            _previews.pop(key, None)
