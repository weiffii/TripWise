import unittest
from unittest.mock import patch

from fastapi import HTTPException

from ai_ds.day_windows import local_day_window
from ai_ds.moscow_road import travel_matrix
from backend.moscow import MoscowAssistant, plan_moscow
from backend.route_previews import engine_preferences, form_from_engine


def form(end='2026-12-04', overrides=None):
    return dict(
        schema_version='fullstack-draft-0.2', country_code='RU', city='Москва', timezone='Europe/Moscow',
        first_walk_at='2026-11-21T12:00', last_walk_end_at=f'{end}T17:00',
        day_settings=overrides or {}, travelers=1, traveler_counts={'adults': 1, 'children': 0, 'seniors': 0},
        budget_amount='90000', budget_currency='RUB', budget_basis='group',
        budget_scope='trip_food_visits_and_transport', primary_interests=['museums', 'history'],
        secondary_interests=['nature'], pace='balanced', transport_modes=['walking'], required_poi_ids=[],
    )


class DayWindowsTests(unittest.TestCase):
    def test_local_command_handles_two_digit_day_and_rejects_boundaries(self):
        dates = [f'2026-11-{day:02}' for day in range(10, 24)]
        decision = local_day_window('сделай 12-й день с 10:00 до 17:00', dates)
        self.assertEqual(decision['operations'], [{
            'op': 'set_day_window', 'day': dates[11], 'start_time': '10:00', 'end_time': '17:00',
        }])
        self.assertEqual(local_day_window('первый день с 10:00 до 17:00', dates)['action'], 'needs_clarification')
        self.assertIsNone(local_day_window('перенеси обед на 12 день с 12:00 до 14:00', dates))

    def test_form_roundtrip_and_limits(self):
        raw = form(overrides={'2026-12-01': {'day_start': '10:30', 'day_end': '17:30'}})
        saved, prefs = engine_preferences(raw)
        self.assertEqual(prefs.days, 14)
        preferences = prefs.model_dump(mode='json')
        self.assertEqual(preferences['day_settings']['2026-12-01']['day_start'], '10:30')
        self.assertEqual(preferences['day_settings']['2026-11-21']['day_start'], '12:00')
        self.assertEqual(preferences['day_settings']['2026-12-04']['day_end'], '17:00')
        reopened = form_from_engine({'preferences': preferences, 'options': {
            '_fullstack_preferences': saved, 'day_settings': {'2026-12-01': {'day_start': '11:00'}},
        }})
        self.assertEqual(reopened['day_settings']['2026-12-01'], {'day_start': '11:00', 'day_end': '17:30'})
        for invalid in (form('2026-12-05'), form(overrides={'2026-12-04': {'day_end': '11:00'}}),
                        form(overrides={'2026-11-22': {'day_start': '20:30'}})):
            with self.assertRaises(HTTPException):
                engine_preferences(invalid)

    @patch.dict('os.environ', {'MOSCOW_OSRM_FOOT': '', 'MOSCOW_OSRM_CAR': '', 'OPENROUTER_API_KEY': ''})
    def test_local_moscow_chat_persists_window_only_after_confirmation(self):
        _, prefs = engine_preferences(form('2026-11-23'))
        plan, options, snapshot = plan_moscow(prefs)
        service = MoscowAssistant()
        token, state = service.session()
        state['route_options'] = options
        csrf = state['csrf']
        service.request(token, csrf, 'register', dict(request_id='register-window', route_id='route-1',
            version=1, preferences=prefs.model_dump(mode='json'), plan=plan))
        def recovered_matrix(*args, **kwargs):
            matrix = travel_matrix(*args, **kwargs)
            # Simulate a provider change between saving and opening the editor.
            for leg in matrix['pairs']:
                leg['source_id'] = 'changed-provider'
            return matrix

        with patch('backend.moscow.travel_matrix', side_effect=recovered_matrix):
            proposed = service.request(token, csrf, 'message', dict(request_id='window-01', route_id='route-1',
                base_version=1, poi_snapshot_id=snapshot, text='Второй день с 10:00 до 19:00'))
        self.assertEqual(proposed['type'], 'proposal', proposed)
        self.assertEqual(state['moscow_version'], 1)
        self.assertEqual(proposed['plan']['days'][0], plan['days'][0])
        self.assertEqual(proposed['plan']['days'][2], plan['days'][2])
        day = proposed['plan']['days'][1]
        self.assertTrue(day['visits'])
        self.assertTrue(all(row['start_at'][11:16] >= '10:00' and row['end_at'][11:16] <= '19:00'
                            for field in ('visits', 'legs') for row in day[field]))
        saved = service.request(token, csrf, 'decide', dict(request_id='decision-window', base_version=1,
            decision='confirm', proposal_id=proposed['proposal_id']))
        self.assertEqual(saved['type'], 'confirmed', saved)
        self.assertEqual(saved['options']['day_settings']['2026-11-22'],
                         {'day_start': '10:00', 'day_end': '19:00'})
