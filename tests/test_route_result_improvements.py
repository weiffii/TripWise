import unittest
from unittest.mock import patch

from fastapi import HTTPException

from ai_ds.moscow_catalog import planning_fields
from ai_ds.visit_time_intent import local_visit_pin
from backend.moscow import MoscowAssistant, indexes, plan_moscow
from backend.route_geometry import SegmentRequest, refresh_segment
from backend.route_previews import engine_preferences
from test_trip_day_windows import form


class RouteResultImprovementsTests(unittest.TestCase):
    def test_coffee_bakery_and_dessert_are_not_full_meals(self):
        for name, source, expected in [
            ('Дринкит', 'cafe', 'coffee_shop'),
            ('Хлебная мануфактура № 1', 'bakery', 'bakery'),
            ('Baskin-Robbins', 'cafe', 'dessert'),
        ]:
            with self.subTest(name=name):
                fields = planning_fields(dict(id=name, name=name, source_type=source, categories=['cafes']))
                self.assertEqual(fields['food_type']['value'], expected)
                for kind in ('lunch', 'dinner'):
                    self.assertEqual(fields['meal_suitability']['value'][kind], 'unlikely')

    def test_pin_resolves_scheduled_visit_and_handles_wrong_day(self):
        days = [{'date': '2026-11-21', 'visits': [{'name': 'Музей Москвы', 'poi_id': 'm'}]},
                {'date': '2026-11-22', 'visits': []}]
        decision = local_visit_pin('Закрепи Музей Москвы в 9:30', days)
        self.assertEqual(decision['operations'], [
            {'op': 'pin_visit_time', 'day_date': '2026-11-21', 'poi_id': 'm', 'start_time': '09:30'}])
        self.assertEqual(local_visit_pin('Закрепи Музей Москвы на 2-й день в 9:30', days)['action'],
                         'needs_clarification')

    @patch.dict('os.environ', {'MOSCOW_OSRM_FOOT': '', 'MOSCOW_OSRM_CAR': '', 'OPENROUTER_API_KEY': ''})
    def test_moscow_pin_preview_and_confirmation(self):
        _, preferences = engine_preferences(form('2026-11-23'))
        plan, options, snapshot = plan_moscow(preferences)
        service = MoscowAssistant()
        token, state = service.session()
        state['route_options'] = options
        csrf = state['csrf']
        route_id = 'pin-test'
        service.request(token, csrf, 'register', dict(request_id='pin-register', route_id=route_id,
            version=1, preferences=preferences.model_dump(mode='json'), plan=plan))
        date = plan['days'][1]['date']
        visit = next(event for event in plan['engine_days'][1]['events'] if event['type'] == 'visit')
        poi_id = visit['poi_id']
        start = '%02d:%02d' % divmod(visit['start_min'], 60)
        name = indexes()[0][poi_id]['name']
        proposed = service.request(token, csrf, 'message', dict(request_id='pin-message', route_id=route_id,
            base_version=1, poi_snapshot_id=snapshot, text=f'Закрепи {name} {date} в {start}'))
        self.assertEqual(proposed['type'], 'proposal', proposed)
        self.assertNotIn('visit_times', state['moscow_route'])
        self.assertEqual(proposed['plan']['visit_times'], {date: {poi_id: start}})
        self.assertEqual(proposed['plan']['days'][0], plan['days'][0])
        self.assertEqual(proposed['plan']['days'][2], plan['days'][2])
        confirmed = service.request(token, csrf, 'decide', dict(request_id='pin-confirm', base_version=1,
            decision='confirm', proposal_id=proposed['proposal_id']))
        self.assertEqual(confirmed['type'], 'confirmed', confirmed)
        self.assertEqual(confirmed['plan']['visit_times'][date][poi_id], start)

    @patch('backend.route_geometry.indexes', return_value=({'a': {'id': 'a'}, 'b': {'id': 'b'}},))
    @patch('backend.route_geometry.travel_matrix')
    @patch('backend.route_geometry.owned_route')
    def test_geometry_retries_only_requested_pair(self, route, matrix, _indexes):
        leg = {'from_poi_id': 'a', 'to_poi_id': 'b', 'mode': 'walk', 'duration_seconds': 720}
        date = '2026-11-21'
        route.return_value = {'city': 'moscow', 'poi_snapshot_id': 'moscow-v7:hash', 'version': 3,
                              'plan': {'days': [{'date': date, 'legs': [leg]}]}}
        matrix.return_value = {'pairs': [{**leg, 'geometry': {'type': 'LineString',
            'coordinates': [[37.6, 55.7], [37.62, 55.72]]}, 'basis': 'routing_provider',
            'duration_seconds': 600, 'distance_meters': 700}]}
        data = SegmentRequest(version=3, day=date, leg_index=0)
        result = refresh_segment('planned-trips', 'test', data, None, {'id': 1})
        self.assertEqual(result['duration_seconds'], 600)
        self.assertEqual(leg['duration_seconds'], 720)
        matrix.assert_called_once_with([{'id': 'a'}, {'id': 'b'}], 'walking', allow_walking=True,
                                       only_pairs={('a', 'b')})
        with self.assertRaises(HTTPException) as error:
            refresh_segment('planned-trips', 'test', data.model_copy(update={'version': 2}), None, {'id': 1})
        self.assertEqual(error.exception.status_code, 409)
