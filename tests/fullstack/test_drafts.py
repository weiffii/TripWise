"""Черновик анкеты: валидация, владельцы и сохранность по настоящему HTTP."""

from http.cookiejar import CookieJar
from urllib.request import build_opener, HTTPCookieProcessor

from test_accounts import LiveServerTests, NoRedirect


def valid_preferences():
    return {
        "schema_version": "fullstack-draft-0.1", "country_code": "RU", "city": "Москва", "timezone": "Europe/Moscow",
        "first_walk_at": "2026-11-20T15:00:00+03:00", "last_walk_end_at": "2026-11-22T18:00:00+03:00",
        "travelers": 3, "budget_amount": "15000.50", "budget_currency": "RUB", "budget_basis": "per_person",
        "budget_scope": "trip_food_and_entertainment", "primary_interests": ["history", "museums"],
        "secondary_interests": ["nature"], "pace": "balanced", "transport_modes": ["walking", "public_transit"],
        "required_poi_ids": [],
    }


class DraftTests(LiveServerTests):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.register()[0], 201)
        self.owner_context = self.request('/api/trip-draft')[1]['owner_context']

    def save(self, preferences=None, revision=0, *, owner_context=None, **kwargs):
        context = self.owner_context if owner_context is None else owner_context
        return self.request('/api/trip-draft', {
            "revision": revision, "preferences": preferences if preferences is not None else valid_preferences(),
        }, method="PUT", headers={'X-TripWise-Draft-Owner': context}, **kwargs)

    def test_draft_roundtrip_preserves_group_budget_and_modes(self):
        self.assertIsNone(self.request('/api/trip-draft')[1]['draft'])
        status, data, _ = self.save()
        self.assertEqual(status, 200)
        self.assertEqual(data['draft']['revision'], 1)
        status, loaded, headers = self.request('/api/trip-draft')
        self.assertEqual(status, 200)
        self.assertEqual(loaded['draft']['preferences'], {
            **valid_preferences(),
            'traveler_counts': {'adults': 3, 'children': 0, 'seniors': 0},
        })
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_update_and_conflict_do_not_lose_saved_data(self):
        self.save()
        updated = valid_preferences()
        updated['travelers'] = 4
        updated['budget_basis'] = 'group'
        self.assertEqual(self.save(updated, 1)[1]['draft']['revision'], 2)
        status, _, _ = self.save(valid_preferences(), 1)
        self.assertEqual(status, 409)
        loaded = self.request('/api/trip-draft')[1]['draft']
        self.assertEqual(loaded['preferences']['travelers'], 4)
        self.assertEqual(loaded['preferences']['budget_basis'], 'group')

    def test_other_user_cannot_read_or_replace_draft(self):
        self.save()
        other = build_opener(HTTPCookieProcessor(CookieJar()), NoRedirect())
        self.assertEqual(self.register('draft-other@example.com', other)[0], 201)
        other_loaded = self.request('/api/trip-draft', client=other)[1]
        self.assertIsNone(other_loaded['draft'])
        other_data = valid_preferences()
        other_data['travelers'] = 1
        self.assertEqual(self.save(other_data, client=other, owner_context=other_loaded['owner_context'])[0], 200)
        self.assertEqual(self.request('/api/trip-draft')[1]['draft']['preferences']['travelers'], 3)

    def test_anonymous_draft_access_denied(self):
        client = build_opener(NoRedirect())
        self.assertEqual(self.request('/api/trip-draft', client=client)[0], 401)
        self.assertEqual(self.save(client=client)[0], 401)
        self.assertEqual(self.request('/trip/new', client=client)[0], 303)

    def test_dates_and_moscow_timezone(self):
        for value in ['2026-11-20T14:59:00+03:00', '2026-11-20T15:00:00+03:00', '2026-11-22T18:00:00', '2026-11-22T18:00:00+00:00', 1795000000]:
            data = valid_preferences()
            data['last_walk_end_at'] = value
            with self.subTest(end=value):
                status, body, _ = self.save(data)
                self.assertEqual(status, 422)
                self.assertIn('анкеты', body['detail'])
                self.assertNotIn('пароль', body['detail'])

    def test_invalid_numeric_inputs(self):
        for field, value in [('travelers', True), ('travelers', 0), ('travelers', 1.5), ('travelers', 101), ('budget_amount', '-1'), ('budget_amount', '12.345'), ('budget_amount', 'NaN'), ('budget_amount', True)]:
            data = valid_preferences()
            data[field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(self.save(data)[0], 422)

    def test_interests_and_transport_constraints(self):
        for field, value in [
            ('primary_interests', []), ('primary_interests', ['nature', 'history', 'museums']),
            ('primary_interests', ['history', 'history']), ('secondary_interests', ['history']),
            ('secondary_interests', ['nonexistent']), ('transport_modes', []),
            ('transport_modes', ['walking', 'walking']), ('transport_modes', ['plane']),
        ]:
            data = valid_preferences()
            data[field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(self.save(data)[0], 422)

    def test_city_poi_ids_and_user_id_are_not_invented(self):
        for field, value in [('city', 'Париж'), ('country_code', 'FR'), ('required_poi_ids', ['invented-poi']), ('user_id', 1)]:
            data = valid_preferences()
            data[field] = value
            self.assertEqual(self.save(data)[0], 422)

    def test_invalid_update_preserves_previous_draft(self):
        self.save()
        invalid = valid_preferences()
        invalid['budget_amount'] = '-3'
        self.assertEqual(self.save(invalid, 1)[0], 422)
        loaded = self.request('/api/trip-draft')[1]['draft']
        self.assertEqual(loaded['preferences']['budget_amount'], '15000.50')
        self.assertEqual(loaded['revision'], 1)

    def test_restart_preserves_all_draft_values(self):
        self.save()
        self.stop_server()
        self.start_server()
        self.assertEqual(self.request('/api/trip-draft')[1]['draft']['preferences'], {
            **valid_preferences(),
            'traveler_counts': {'adults': 3, 'children': 0, 'seniors': 0},
        })

    def test_origin_required_for_save(self):
        self.assertEqual(self.save(origin='https://other.example')[0], 403)
        self.assertIsNone(self.request('/api/trip-draft')[1]['draft'])

    def test_page_available_only_as_protected_route(self):
        status, content, _ = self.request('/trip/new')
        self.assertEqual(status, 200)
        self.assertIn('Начало первой прогулки'.encode(), content)
        self.assertEqual(self.request('/backend/pages/questionnaire.html')[0], 404)
