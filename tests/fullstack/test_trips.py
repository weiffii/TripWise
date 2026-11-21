"""CRUD нескольких анкет, изоляция, версии, повторы и сохранная миграция по HTTP."""

from http.cookiejar import CookieJar
import json
from urllib.request import build_opener, HTTPCookieProcessor
from uuid import uuid4

from test_accounts import LiveServerTests, NoRedirect, test_database
from test_drafts import valid_preferences


class TripTests(LiveServerTests):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.register()[0], 201)
        self.owner = self.request('/api/trips')[1]['owner_context']

    def write(self, path, data, method='POST', *, owner=None, **kwargs):
        headers = {'X-TripWise-Draft-Owner': self.owner if owner is None else owner}
        return self.request(path, data, method=method, headers=headers, **kwargs)

    def create(self, *, preferences=None, key=None, **kwargs):
        return self.write('/api/trips', {'preferences': preferences or valid_preferences(),
                          'creation_key': key or str(uuid4())}, **kwargs)

    def test_create_two_edit_one_delete_one_preserves_other_after_restart(self):
        first = self.create()[1]['trip']
        second_data = valid_preferences()
        second_data['budget_currency'] = 'EUR'
        second = self.create(preferences=second_data)[1]['trip']
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(len(self.request('/api/trips')[1]['trips']), 2)
        edited = valid_preferences()
        edited['primary_interests'] = ['history', 'museums', 'nature']
        edited['secondary_interests'] = []
        status, result, _ = self.write(f"/api/trips/{first['id']}", {'revision': 1, 'preferences': edited}, 'PUT')
        self.assertEqual(status, 200)
        self.assertEqual(result['trip']['revision'], 2)
        self.assertEqual(self.request(f"/api/trips/{second['id']}")[1]['trip']['preferences']['budget_currency'], 'EUR')
        self.assertEqual(self.write(f"/api/trips/{first['id']}", {'revision': 2}, 'DELETE')[0], 204)
        self.stop_server()
        self.start_server()
        trips = self.request('/api/trips')[1]['trips']
        self.assertEqual([trip['id'] for trip in trips], [second['id']])
        self.assertEqual(self.request(f"/api/trips/{first['id']}")[0], 404)

    def test_creation_key_retries_are_idempotent_but_different_payload_conflicts(self):
        key = str(uuid4())
        status, created, _ = self.create(key=key)
        self.assertEqual(status, 201)
        status, retried, _ = self.create(key=key)
        self.assertEqual(status, 200)
        self.assertEqual(retried, created)
        changed = valid_preferences()
        changed['budget_amount'] = '999'
        self.assertEqual(self.create(key=key, preferences=changed)[0], 409)
        self.assertEqual(len(self.request('/api/trips')[1]['trips']), 1)
        self.assertEqual(self.create(key='legacy-v1')[0], 422)

    def test_revision_conflicts_do_not_edit_or_delete(self):
        trip = self.create()[1]['trip']
        path = f"/api/trips/{trip['id']}"
        self.assertEqual(self.write(path, {'revision': 0, 'preferences': valid_preferences()}, 'PUT')[0], 409)
        self.assertEqual(self.write(path, {'revision': 2}, 'DELETE')[0], 409)
        self.assertEqual(self.request(path)[1]['trip'], trip)

    def test_foreign_ids_hidden_and_never_modified(self):
        trip = self.create()[1]['trip']
        other = build_opener(HTTPCookieProcessor(CookieJar()), NoRedirect())
        self.assertEqual(self.register(f'other-{uuid4().hex}@example.com', other)[0], 201)
        context = self.request('/api/trips', client=other)[1]['owner_context']
        path = f"/api/trips/{trip['id']}"
        self.assertEqual(self.request(path, client=other)[0], 404)
        self.assertEqual(self.write(path, {'revision': 1, 'preferences': valid_preferences()}, 'PUT', client=other, owner=context)[0], 404)
        self.assertEqual(self.write(path, {'revision': 1}, 'DELETE', client=other, owner=context)[0], 404)
        self.assertEqual(self.request(path)[1]['trip'], trip)

    def test_owner_context_and_origin_required_for_all_writes(self):
        trip = self.create()[1]['trip']
        payloads = [('/api/trips', {'preferences': valid_preferences(), 'creation_key': str(uuid4())}, 'POST'),
                    (f"/api/trips/{trip['id']}", {'preferences': valid_preferences(), 'revision': 1}, 'PUT'),
                    (f"/api/trips/{trip['id']}", {'revision': 1}, 'DELETE')]
        for path, payload, method in payloads:
            with self.subTest(method=method):
                self.assertEqual(self.request(path, payload, method=method)[0], 428)
                self.assertEqual(self.write(path, payload, method, owner='wrong-account')[0], 409)
                self.assertEqual(self.write(path, payload, method, origin='https://other.example')[0], 403)
        self.assertEqual(self.request(f"/api/trips/{trip['id']}")[1]['trip'], trip)

    def test_anonymous_cannot_list_read_create_update_or_delete(self):
        anonymous = build_opener(NoRedirect())
        trip = self.create()[1]['trip']
        path = f"/api/trips/{trip['id']}"
        self.assertEqual(self.request('/api/trips', client=anonymous)[0], 401)
        self.assertEqual(self.request(path, client=anonymous)[0], 401)
        self.assertEqual(self.create(client=anonymous)[0], 401)
        self.assertEqual(self.write(path, {'revision': 1, 'preferences': valid_preferences()}, 'PUT', client=anonymous)[0], 401)
        self.assertEqual(self.write(path, {'revision': 1}, 'DELETE', client=anonymous)[0], 401)

    def test_private_responses_are_no_store_and_validation_sanitized(self):
        trip = self.create()[1]['trip']
        for path in ['/api/trips', f"/api/trips/{trip['id']}"]:
            self.assertEqual(self.request(path)[2]['Cache-Control'], 'no-store')
        data = valid_preferences()
        data['unexpected'] = 'private-input-value'
        status, body, headers = self.create(preferences=data)
        self.assertEqual(status, 422)
        self.assertNotIn('private-input-value', json.dumps(body))
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_legacy_archive_migrates_once_alias_targets_same_record_and_delete_does_not_resurrect(self):
        raw = json.dumps(valid_preferences(), ensure_ascii=False, indent=3)
        self.stop_server()
        with test_database(self.db_path) as db:
            db.execute('INSERT INTO trip_drafts VALUES (?, ?, ?, ?)', (self.owner, raw, 7, 1795000000))
            db.execute("DELETE FROM schema_migrations WHERE name='multi-trips-v1'")
        self.start_server()
        legacy = self.request('/api/trip-draft')[1]['draft']
        migrated = self.request('/api/trips')[1]['trips']
        self.assertEqual(len(migrated), 1)
        trip = migrated[0]
        self.assertEqual(trip['revision'], 7)
        self.assertEqual(trip['created_at'], 1795000000)
        self.assertEqual(trip['updated_at'], 1795000000)
        self.assertEqual(trip['preferences'], legacy['preferences'])
        with test_database(self.db_path) as db:
            canonical = db.execute('SELECT preferences_json FROM saved_trips WHERE id=?', (trip['id'],)).fetchone()[0]
            self.assertEqual(canonical, raw)
        extra = self.create()[1]['trip']
        edited = valid_preferences()
        edited['budget_currency'] = 'EUR'
        status, _, _ = self.write('/api/trip-draft', {'revision': 7, 'preferences': edited}, 'PUT')
        self.assertEqual(status, 200)
        self.assertEqual(self.request(f"/api/trips/{trip['id']}")[1]['trip']['preferences']['budget_currency'], 'EUR')
        self.assertEqual(self.request(f"/api/trips/{extra['id']}")[1]['trip']['preferences']['budget_currency'], 'RUB')
        self.assertEqual(self.write(f"/api/trips/{trip['id']}", {'revision': 8}, 'DELETE')[0], 204)
        self.stop_server()
        self.start_server()
        self.assertIsNone(self.request('/api/trip-draft')[1]['draft'])
        self.assertEqual([t['id'] for t in self.request('/api/trips')[1]['trips']], [extra['id']])
        with test_database(self.db_path) as db:
            self.assertEqual(db.execute('SELECT preferences_json, revision, updated_at FROM trip_drafts WHERE user_id=?', (self.owner,)).fetchone(), (raw,7,1795000000))
