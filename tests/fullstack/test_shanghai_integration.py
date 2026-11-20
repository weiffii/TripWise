from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from ai_ds.shanghai_assistant import ShanghaiAssistantService
from backend import accounts, shanghai
from backend.main import app


PREFERENCES = {
    'city': 'shanghai', 'timezone': 'Asia/Shanghai', 'start_date': '2026-12-05',
    'days': 2, 'day_start': '09:00', 'day_end': '19:00', 'pace': 'balanced',
    'transport': 'mixed', 'group': {'adult': 2, 'child': 0, 'senior': 0, 'pet': 0},
    'primary_interests': ['museums'], 'secondary_interests': ['nature'], 'required_ids': [],
    'budget_amount': 400, 'budget_currency': 'CNY', 'budget_basis': 'per_person',
}
ENDPOINT = '/api/planned-trips'
ORIGIN = {'Origin': 'http://testserver'}


class ShanghaiIntegrationTests(unittest.TestCase):
    def setUp(self):
        storage = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.object(accounts, 'DB_PATH', Path(storage) / 'test.sqlite3'))
        self.client = self.enterContext(TestClient(app))
        self.other = self.enterContext(TestClient(app))

    def register(self, client, email):
        response = client.post('/api/auth/register', headers=ORIGIN, json={
            'email': email, 'password': 'TripWise123!', 'password_confirmation': 'TripWise123!',
        })
        self.assertEqual(response.status_code, 201, response.text)

    def test_auth_and_city_boundaries(self):
        self.assertEqual(self.client.get(ENDPOINT).status_code, 401)
        self.register(self.client, 'pilot@example.org')
        self.assertEqual(self.client.get('/trip/shanghai').status_code, 200)
        self.assertEqual(self.client.post(ENDPOINT, headers=ORIGIN, json={**PREFERENCES, 'city': 'moscow'}).status_code, 422)
        self.assertEqual(self.client.post(ENDPOINT, json=PREFERENCES).status_code, 403)

    @unittest.skipUnless(shutil.which('node'), 'Требуется Node.js')
    def test_route_owner_preview_reject_confirm_and_reopen(self):
        self.register(self.client, 'owner@example.org')
        self.register(self.other, 'other@example.org')
        target = None

        def interpreter(_payload):
            return {'action': 'propose', 'operations': [{'op': 'remove_poi', 'poi_id': target}], 'clarification': None}

        with patch.object(shanghai, 'assistant', return_value=ShanghaiAssistantService(interpreter=interpreter, mode='mock')):
            built = self.client.post(ENDPOINT, headers=ORIGIN, json=PREFERENCES)
            self.assertEqual(built.status_code, 201, built.text[:1000])
            initial = built.json()
            self.assertEqual(len(initial['plan']['days']), 2)
            self.assertEqual(initial['options']['budget_cny'], 800)
            target = initial['plan']['days'][0]['visit_ids'][0]
            path = ENDPOINT + '/' + initial['route_id']
            assistant_path = path + '/assistant/'
            self.assertEqual(self.other.get(path).status_code, 404)
            self.assertEqual(self.other.get(assistant_path + 'status').status_code, 404)
            self.assertEqual(self.other.get(ENDPOINT).json()['trips'], [])
            status = self.client.get(assistant_path + 'status')
            self.assertNotIn('set-cookie', status.headers)
            headers = {**ORIGIN, 'X-TripWise-CSRF': status.json()['csrf_token']}
            registered = self.client.post(assistant_path + 'register', headers=headers, json={
                'request_id': 'register-1', 'plan': {'untrusted': True}, 'options': {'budget_cny': 1},
            }).json()
            self.assertEqual(registered['type'], 'registered', registered)
            for decision in ('reject', 'confirm'):
                preview = self.client.post(assistant_path + 'message', headers=headers, json={
                    'route_id': registered['route_id'], 'base_version': registered['version'],
                    'poi_snapshot_id': registered['poi_snapshot_id'], 'text': 'Убери первое посещение',
                    'request_id': 'preview-' + decision,
                }).json()
                self.assertEqual(preview['type'], 'proposal', preview)
                self.assertEqual(self.client.get(path).json(), initial)
                payload = {'proposal_id': preview['proposal_id'], 'base_version': preview['base_version'],
                           'decision': decision, 'request_id': 'decision-' + decision}
                result = self.client.post(assistant_path + 'decide', headers=headers, json=payload).json()
                self.assertEqual(result['type'], 'rejected' if decision == 'reject' else 'confirmed', result)
                if decision == 'reject':
                    self.assertEqual(self.client.get(path).json(), initial)
            self.assertEqual(self.client.post(assistant_path + 'decide', headers=headers, json=payload).json(), result)
            self.assertEqual(self.client.post(assistant_path + 'decide', headers=headers, json={
                **payload, 'request_id': 'retry-confirm-with-new-id',
            }).json(), result)
            saved = self.client.get(path).json()
            self.assertEqual(saved['version'], 2)
            self.assertNotIn(target, saved['plan']['selected_ids'])
            self.assertEqual(saved['plan']['days'][1], initial['plan']['days'][1])
            self.assertEqual(self.client.get(ENDPOINT).json()['trips'][0]['version'], 2)
