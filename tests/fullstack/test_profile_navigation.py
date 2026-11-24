"""HTTP-проверки профиля и навигации; ответ дорожного сервиса контролируемый."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import build_opener

from test_accounts import LiveServerTests, PASSWORD, test_database


STUB_SERVER = '''
import json
from io import BytesIO
import backend.navigation as nav
nav.catalog = lambda city, include_legacy=False: [{'id':'destination', 'name':'Тестовая цель', 'lat':55.751, 'lon':37.62}]
def upstream(url, timeout):
    if '/0.0,0.0;' in url:
        raise OSError('controlled unavailable')
    body={'code':'Ok','waypoints':[{'distance':2},{'distance':3}], 'routes':[{
        'geometry':{'type':'LineString','coordinates':[[37.61,55.75],[37.62,55.751]]},
        'duration':120, 'distance':180, 'legs':[{'steps':[
            {'maneuver':{'type':'depart'},'name':'Тестовая улица','distance':160},
            {'maneuver':{'type':'arrive'},'distance':0}]}]}]}
    return BytesIO(json.dumps(body).encode())
nav.urlopen=upstream
import uvicorn
uvicorn.run('backend.main:app', host='127.0.0.1', port=PORT, log_level='warning')
'''


class ProfileTests(LiveServerTests):
    def setUp(self):
        super().setUp()
        self.register()
        self.owner = self.request('/api/auth/me')[1]['owner_context']

    def profile(self, name, **kwargs):
        return self.request('/api/auth/profile', {'display_name': name}, method='PUT',
                            headers={'X-TripWise-Draft-Owner': self.owner}, **kwargs)

    def test_name_edit_me_login_and_restart(self):
        self.assertEqual(self.request('/api/auth/me')[1]['user']['display_name'], '')
        status, data, _ = self.profile('  Юлия   Иванова  ')
        self.assertEqual(status, 200)
        self.assertEqual(data['user']['display_name'], 'Юлия Иванова')
        self.stop_server()
        self.start_server()
        self.assertEqual(self.request('/api/auth/me')[1]['user']['display_name'], 'Юлия Иванова')
        self.request('/api/auth/logout', {})
        self.assertEqual(self.request('/api/auth/login', {'email': self.email, 'password': PASSWORD})[1]['user']['display_name'], 'Юлия Иванова')
        self.assertEqual(self.profile('')[0], 200)

    def test_invalid_name_origin_and_unauthenticated(self):
        for name in [None, True, 'a' * 81, 'line\nbreak', 'hidden\x00text']:
            with self.subTest(name=name):
                self.assertEqual(self.profile(name)[0], 422)
        self.assertEqual(self.profile('Имя', origin='https://other.example')[0], 403)
        self.request('/api/auth/logout', {})
        self.assertEqual(self.profile('Имя')[0], 401)

    def test_owner_context_required_and_account_switch_cannot_edit_other_profile(self):
        status, _, headers = self.request('/api/auth/profile', {'display_name':'Missing'}, method='PUT')
        self.assertEqual(status, 428)
        self.assertEqual(headers['X-TripWise-Draft-Error'], 'CONTEXT_REQUIRED')
        self.assertEqual(self.profile('Первое имя')[0], 200)
        old_owner = self.owner
        self.register('other-profile-' + self.email)
        other_me = self.request('/api/auth/me')[1]
        status, _, headers = self.profile('Не должно сохраниться')
        self.assertEqual(status, 409)
        self.assertEqual(headers['X-TripWise-Draft-Error'], 'ACCOUNT_CHANGED')
        self.assertEqual(self.request('/api/auth/me')[1]['user']['display_name'], '')
        with test_database(self.db_path) as db:
            self.assertEqual(db.execute('SELECT display_name FROM users WHERE id=?', (old_owner,)).fetchone()[0], 'Первое имя')
        self.owner = other_me['owner_context']
        self.assertEqual(self.profile('Второе имя')[0], 200)

    def test_registration_optional_name_is_persisted(self):
        email = 'profile-' + self.email
        status, data, _ = self.request('/api/auth/register', {
            'email': email, 'password': PASSWORD, 'password_confirmation': PASSWORD, 'display_name': ' Анна ',
        })
        self.assertEqual(status, 201)
        self.assertEqual(data['user']['display_name'], 'Анна')
        self.assertEqual(self.request('/api/auth/me')[1]['user']['display_name'], 'Анна')


class NavigationTests(LiveServerTests):
    @classmethod
    def start_server(cls):
        env = os.environ.copy()
        env['TRIPWISE_DB_PATH'] = str(cls.db_path)
        cls.process = subprocess.Popen([sys.executable, '-c', STUB_SERVER.replace('PORT', str(cls.port))],
                                       cwd=Path(__file__).resolve().parents[2], env=env, stdout=cls.log, stderr=cls.log)
        for _ in range(200):
            if cls.process.poll() is not None:
                raise AssertionError('Тестовый сервер не запустился')
            try:
                with build_opener().open(cls.base + '/api/health', timeout=.3) as response:
                    if response.status == 200:
                        return
            except (URLError, TimeoutError):
                time.sleep(.05)
        raise AssertionError('Тестовый сервер не ответил')

    def setUp(self):
        super().setUp()
        self.register()
        user = self.request('/api/auth/me')[1]['user']
        self.route_id = 'navigation-' + str(user['id'])
        plan = {'days': [{'date': '2026-11-24', 'legs': [{'from_poi_id':'start', 'to_poi_id':'destination'}]}]}
        with test_database(self.db_path) as db:
            db.execute('INSERT INTO planned_trips VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (self.route_id, user['id'], 'moscow', 1, 'synthetic-contract-test', '{}', '{}', json.dumps(plan), 1, 1))
        self.path = '/api/planned-trips/' + self.route_id + '/navigate'
        self.body = {'version':1, 'day':'2026-11-24', 'leg_index':0, 'origin':{'lat':55.75,'lon':37.61}}

    def test_navigation_has_provider_steps_and_server_selected_target(self):
        status, data, headers = self.request(self.path, self.body)
        self.assertEqual(status, 200)
        self.assertEqual(data['destination']['poi_id'], 'destination')
        self.assertEqual(data['origin'], self.body['origin'])
        self.assertEqual(data['geometry']['type'], 'LineString')
        self.assertEqual(data['distance_meters'], 180)
        self.assertEqual(data['duration_seconds'], 120)
        self.assertIn('Начните движение', data['steps'][0])
        self.assertEqual(data['steps'][-1], 'Вы у места назначения')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.request(self.path, {**self.body, 'destination': {'lat':0,'lon':0}})[0], 422)

    def test_bad_coordinates_version_and_leg(self):
        for origin in [{'lat':91,'lon':37}, {'lat':True,'lon':37}, {'lat':'55.75','lon':37}, {'lat':0,'lon':0}]:
            self.assertEqual(self.request(self.path, {**self.body,'origin':origin})[0], 422)
        self.assertEqual(self.request(self.path, {**self.body,'version':2})[0], 409)
        self.assertEqual(self.request(self.path, {**self.body,'leg_index':1})[0], 404)
        self.assertEqual(self.request(self.path, {**self.body,'day':'2026-11-25'})[0], 404)

    def test_owner_auth_and_origin_guards(self):
        self.assertEqual(self.request(self.path, self.body, origin='https://other.example')[0], 403)
        self.register('other-' + self.email)
        self.assertEqual(self.request(self.path, self.body)[0], 404)
        self.request('/api/auth/logout', {})
        self.assertEqual(self.request(self.path, self.body)[0], 401)


class MigrationAndProviderTests(unittest.TestCase):
    def test_old_database_migration_preserves_user_and_session(self):
        import backend.accounts as accounts
        with tempfile.TemporaryDirectory(prefix='tripwise-profile-migration-') as folder:
            path = Path(folder) / 'old.sqlite3'
            with test_database(path) as db:
                db.executescript('''CREATE TABLE users(id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL, created_at INTEGER NOT NULL);
                    CREATE TABLE sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL);''')
                db.execute('INSERT INTO users VALUES (1,?,?,1)', ('old@example.com', 'unchanged-hash'))
                db.execute('INSERT INTO sessions VALUES (?,?,?)', ('unchanged-token-hash', 1, 9999999999))
            with patch.object(accounts, 'DB_PATH', path):
                accounts.init_database()
                accounts.init_database()
            with test_database(path) as db:
                self.assertEqual(db.execute('SELECT id,email,password_hash,created_at,display_name FROM users').fetchone(),
                                 (1,'old@example.com','unchanged-hash',1,''))
                self.assertEqual(db.execute('SELECT * FROM sessions').fetchone(), ('unchanged-token-hash',1,9999999999))

    def test_provider_failure_does_not_invent_geometry_or_steps(self):
        from backend.navigation import provider_route
        from fastapi import HTTPException
        origin, destination = {'lat':55.75,'lon':37.61}, {'lat':55.751,'lon':37.62}
        for body in [b'{bad json', json.dumps({'code':'Ok','routes':[]}).encode()]:
            with patch('backend.navigation.urlopen', return_value=BytesIO(body)):
                with self.assertRaises(HTTPException) as error:
                    provider_route(origin, destination, 'walking')
                self.assertEqual(error.exception.status_code, 503)
        with patch('backend.navigation.urlopen', side_effect=OSError('unavailable')):
            with self.assertRaises(HTTPException) as error:
                provider_route(origin, destination, 'walking')
            self.assertEqual(error.exception.status_code, 503)
        with patch('backend.navigation.urlopen', return_value=BytesIO(b'{"code":"NoRoute"}')):
            with self.assertRaises(HTTPException) as error:
                provider_route(origin, destination, 'walking')
            self.assertEqual(error.exception.status_code, 422)
