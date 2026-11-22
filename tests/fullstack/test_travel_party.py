"""Реальный HTTP: валюта, состав группы и совместимость старых анкет."""

import json

from test_accounts import LiveServerTests, test_database
from test_drafts import valid_preferences


class TravelPartyTests(LiveServerTests):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.register()[0], 201)
        self.owner_context = self.request('/api/trip-draft')[1]['owner_context']

    def preferences(self):
        preferences = valid_preferences()
        preferences['traveler_counts'] = {'adults': 1, 'children': 1, 'seniors': 1}
        return preferences

    def save(self, preferences, revision=0):
        return self.request('/api/trip-draft', {
            'preferences': preferences, 'revision': revision,
        }, method='PUT', headers={'X-TripWise-Draft-Owner': self.owner_context})

    def test_currency_roundtrip_keeps_exact_amount_without_conversion(self):
        for revision, currency in enumerate(['RUB', 'USD', 'EUR', 'CNY']):
            with self.subTest(currency=currency):
                preferences = self.preferences()
                preferences['budget_currency'] = currency
                status, saved, _ = self.save(preferences, revision)
                self.assertEqual(status, 200)
                self.assertEqual(saved['draft']['preferences']['budget_amount'], '15000.50')
                loaded = self.request('/api/trip-draft')[1]['draft']['preferences']
                self.assertEqual(loaded, preferences)

    def test_legacy_put_missing_counts_is_all_adults(self):
        preferences = valid_preferences()
        preferences.pop('traveler_counts', None)
        status, saved, _ = self.save(preferences)
        self.assertEqual(status, 200)
        self.assertEqual(saved['draft']['preferences']['traveler_counts'], {
            'adults': 3, 'children': 0, 'seniors': 0,
        })
        with test_database(self.db_path) as db:
            raw = db.execute("SELECT preferences_json FROM saved_trips WHERE user_id = ? AND creation_key = 'legacy-v1'",
                             (self.owner_context,)).fetchone()[0]
        self.assertIn('traveler_counts', json.loads(raw))

    def test_legacy_get_normalizes_response_without_database_write(self):
        preferences = valid_preferences()
        preferences.pop('traveler_counts', None)
        raw = json.dumps(preferences)
        # Seed a genuine pre-migration archive, then run startup migration.
        self.stop_server()
        with test_database(self.db_path) as db:
            db.execute('INSERT INTO trip_drafts VALUES (?, ?, ?, ?)',
                       (self.owner_context, raw, 7, 1795000000))
            db.execute("DELETE FROM schema_migrations WHERE name = 'multi-trips-v1'")
        self.start_server()
        for _ in range(2):
            status, result, _ = self.request('/api/trip-draft')
            self.assertEqual(status, 200)
            self.assertEqual(result['draft']['revision'], 7)
            self.assertEqual(result['draft']['updated_at'], 1795000000)
            self.assertEqual(result['draft']['preferences']['traveler_counts'], {
                'adults': 3, 'children': 0, 'seniors': 0,
            })
        with test_database(self.db_path) as db:
            self.assertEqual(db.execute(
                'SELECT preferences_json, revision, updated_at FROM trip_drafts WHERE user_id = ?',
                (self.owner_context,),
            ).fetchone(), (raw, 7, 1795000000))
            self.assertEqual(db.execute(
                "SELECT preferences_json, revision, updated_at FROM saved_trips WHERE user_id = ? AND creation_key = 'legacy-v1'",
                (self.owner_context,),
            ).fetchone(), (raw, 7, 1795000000))

    def test_invalid_party_does_not_replace_saved_values(self):
        self.assertEqual(self.save(self.preferences())[0], 200)
        for counts in [
            None, {}, {'adults': 3, 'children': 0},
            {'adults': 1, 'children': 0, 'seniors': 0},
            {'adults': 0, 'children': 0, 'seniors': 0},
            {'adults': True, 'children': 1, 'seniors': 1},
            {'adults': '1', 'children': 1, 'seniors': 1},
            {'adults': 1.5, 'children': 1, 'seniors': 1},
            {'adults': -1, 'children': 2, 'seniors': 2},
            {'adults': 101, 'children': 0, 'seniors': 0},
            {'adults': 1, 'children': 1, 'seniors': 1, 'other': 0},
        ]:
            with self.subTest(counts=counts):
                preferences = self.preferences()
                preferences['traveler_counts'] = counts
                self.assertEqual(self.save(preferences, 1)[0], 422)
        loaded = self.request('/api/trip-draft')[1]['draft']
        self.assertEqual(loaded['revision'], 1)
        self.assertEqual(loaded['preferences'], self.preferences())

    def test_total_limit_applies_to_combined_group(self):
        preferences = self.preferences()
        preferences['travelers'] = 100
        preferences['traveler_counts'] = {'adults': 40, 'children': 30, 'seniors': 30}
        self.assertEqual(self.save(preferences)[0], 200)
        preferences['travelers'] = 101
        preferences['traveler_counts']['adults'] = 41
        self.assertEqual(self.save(preferences, 1)[0], 422)

    def test_no_unapproved_age_or_accompaniment_restrictions(self):
        preferences = self.preferences()
        preferences['traveler_counts'] = {'adults': 0, 'children': 3, 'seniors': 0}
        self.assertEqual(self.save(preferences)[0], 200)

    def test_unknown_currency_rejected(self):
        for currency in ['ZZZ', 'XXX', 'rub', '', None, 1]:
            preferences = self.preferences()
            preferences['budget_currency'] = currency
            with self.subTest(currency=currency):
                self.assertEqual(self.save(preferences)[0], 422)
        self.assertIsNone(self.request('/api/trip-draft')[1]['draft'])

    def test_party_and_currency_survive_restart(self):
        preferences = self.preferences()
        preferences['budget_currency'] = 'EUR'
        self.assertEqual(self.save(preferences)[0], 200)
        self.stop_server()
        self.start_server()
        self.assertEqual(self.request('/api/trip-draft')[1]['draft']['preferences'], preferences)
