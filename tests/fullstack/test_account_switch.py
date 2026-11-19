"""Независимый QA: устаревшая анкета не должна сохраняться в другом аккаунте.

Это HTTP-симуляция двух вкладок с общим cookie jar. Реальный браузер здесь
не запускается: локальные поля и revision вкладки A удерживаются в памяти,
а вход во вкладке B заменяет общую cookie перед отправкой старого запроса.
Тесты ожидают безопасного поведения и до исправления должны падать.
"""

from copy import deepcopy
import uuid

from test_accounts import LiveServerTests, PASSWORD
from test_drafts import valid_preferences


class AccountSwitchTests(LiveServerTests):
    def put_draft(self, preferences, revision, owner_context):
        return self.request('/api/trip-draft', {
            'preferences': preferences, 'revision': revision,
        }, method='PUT', headers={'X-TripWise-Draft-Owner': owner_context})

    def switch_to_new_account(self):
        self.assertEqual(self.request('/api/auth/logout', {})[0], 200)
        email_b = f'qa-switch-b-{uuid.uuid4().hex}@example.com'
        self.assertEqual(self.register(email_b)[0], 201)

    def assert_stale_save_rejected(self, stale_preferences, stale_revision, stale_owner_context, baseline_b):
        status, _, headers = self.put_draft(stale_preferences, stale_revision, stale_owner_context)
        current_b = self.request('/api/trip-draft')[1]['draft']
        self.assertTrue(
            400 <= status < 500 and current_b == baseline_b,
            f'Устаревший запрос A отправлен после входа B: HTTP {status}; '
            f'черновик B изменился: {current_b != baseline_b}. '
            'Ожидается отказ без изменения данных нового аккаунта.',
        )
        self.assertEqual(status, 409)
        self.assertEqual(headers['X-TripWise-Draft-Error'], 'ACCOUNT_CHANGED')

    def test_empty_stale_form_cannot_create_draft_for_new_account(self):
        self.assertEqual(self.register()[0], 201)
        loaded_a = self.request('/api/trip-draft')[1]
        self.assertIsNone(loaded_a['draft'])
        stale_owner_context = loaded_a['owner_context']
        stale_revision = 0
        stale_preferences = valid_preferences()
        stale_preferences['budget_amount'] = '40000.99'
        self.switch_to_new_account()
        baseline_b = self.request('/api/trip-draft')[1]['draft']
        self.assertIsNone(baseline_b)
        self.assert_stale_save_rejected(stale_preferences, stale_revision, stale_owner_context, baseline_b)

    def test_same_revision_stale_form_cannot_overwrite_new_account_draft(self):
        self.assertEqual(self.register()[0], 201)
        preferences_a = valid_preferences()
        preferences_a['travelers'] = 7
        owner_a = self.request('/api/trip-draft')[1]['owner_context']
        self.assertEqual(self.put_draft(preferences_a, 0, owner_a)[0], 200)
        loaded_a = deepcopy(self.request('/api/trip-draft')[1]['draft'])
        self.switch_to_new_account()
        preferences_b = valid_preferences()
        preferences_b['travelers'] = 1
        preferences_b['budget_amount'] = '3000'
        owner_b = self.request('/api/trip-draft')[1]['owner_context']
        self.assertNotEqual(owner_a, owner_b)
        self.assertEqual(self.put_draft(preferences_b, 0, owner_b)[0], 200)
        baseline_b = self.request('/api/trip-draft')[1]['draft']
        self.assertEqual(loaded_a['revision'], baseline_b['revision'])
        self.assert_stale_save_rejected(loaded_a['preferences'], loaded_a['revision'], owner_a, baseline_b)

    def test_missing_context_is_rejected_without_creating_draft(self):
        self.assertEqual(self.register()[0], 201)
        status, _, headers = self.request('/api/trip-draft', {
            'preferences': valid_preferences(), 'revision': 0,
        }, method='PUT')
        self.assertEqual(status, 428)
        self.assertEqual(headers['X-TripWise-Draft-Error'], 'CONTEXT_REQUIRED')
        self.assertIsNone(self.request('/api/trip-draft')[1]['draft'])

    def test_wrong_context_does_not_select_another_owner(self):
        self.assertEqual(self.register()[0], 201)
        owner = self.request('/api/trip-draft')[1]['owner_context']
        status, _, headers = self.put_draft(valid_preferences(), 0, owner + '-wrong')
        self.assertEqual(status, 409)
        self.assertEqual(headers['X-TripWise-Draft-Error'], 'ACCOUNT_CHANGED')
        self.assertIsNone(self.request('/api/trip-draft')[1]['draft'])

    def test_same_account_relogin_keeps_loaded_context_valid(self):
        self.assertEqual(self.register()[0], 201)
        owner = self.request('/api/trip-draft')[1]['owner_context']
        self.assertEqual(self.request('/api/auth/logout', {})[0], 200)
        self.assertEqual(self.put_draft(valid_preferences(), 0, owner)[0], 401)
        self.assertEqual(self.request('/api/auth/login', {'email': self.email, 'password': PASSWORD})[0], 200)
        self.assertEqual(self.request('/api/trip-draft')[1]['owner_context'], owner)
        self.assertEqual(self.put_draft(valid_preferences(), 0, owner)[0], 200)

    def test_context_does_not_bypass_revision_conflict(self):
        self.assertEqual(self.register()[0], 201)
        owner = self.request('/api/trip-draft')[1]['owner_context']
        self.assertEqual(self.put_draft(valid_preferences(), 0, owner)[0], 200)
        baseline = self.request('/api/trip-draft')[1]['draft']
        updated = valid_preferences()
        updated['travelers'] = 8
        status, _, headers = self.put_draft(updated, 0, owner)
        self.assertEqual(status, 409)
        self.assertIsNone(headers.get('X-TripWise-Draft-Error'))
        self.assertEqual(self.request('/api/trip-draft')[1]['draft'], baseline)
