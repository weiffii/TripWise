from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_ds.data import load_pois
from ai_ds.moscow_catalog import load_moscow_v7
from ai_ds.moscow_transit import build_graph, build_matrix, route_between
from ai_ds.multiday import build_route
from ai_ds.recommendation import recommend
from test_multiday import fixture


RAIL = Path(__file__).resolve().parents[1] / 'data/sources/moscow-metro-osm-pilot.json'
ESTIMATES = {mode: {'meters_per_second': 10, 'wait_seconds': 120} for mode in ('metro', 'mcc', 'mcd')}


class MoscowV7RoutingTests(unittest.TestCase):
    def test_sqlite_trip_is_updated_only_after_confirmation(self):
        from backend import accounts, shanghai
        from backend.moscow import MoscowAssistant, plan_moscow

        preferences = shanghai.MoscowPreferences(
            city='moscow', timezone='Europe/Moscow', start_date='2026-11-20', days=1,
            day_start='09:00', day_end='19:00', pace='balanced', transport='walk',
            group={'adult': 1, 'child': 0, 'senior': 0, 'pet': 0},
            primary_interests=['museums'], secondary_interests=['nature'],
            required_ids=[], budget_amount=15000, budget_currency='RUB', budget_basis='per_group',
        )
        plan, options, snapshot = plan_moscow(preferences)
        day = plan['days'][0]
        poi_id = day['visits'][0]['poi_id']
        added = 'moscow-osm-relation-1577673'
        self.assertNotIn(added, [item['poi_id'] for item in day['visits']])
        service = MoscowAssistant()
        service.configured = True
        service.interpreter = lambda _: {'action': 'propose', 'operations': [
            {'op': 'remove_poi', 'poi_id': poi_id},
            {'op': 'add_poi', 'day': day['date'], 'poi_id': added},
        ], 'clarification': None}
        with (TemporaryDirectory() as directory,
              patch.object(accounts, 'DB_PATH', Path(directory) / 'trips.db'),
              patch.object(shanghai, 'assistant_for', return_value=service),
              patch.object(shanghai, '_bindings', {})):
            accounts.init_database()
            shanghai.init_routes()
            user = {'id': 1}
            with accounts.database() as db:
                db.execute("INSERT INTO users VALUES (1, 'test@example.test', 'fixture', 0)")
            trip = shanghai.store_trip(preferences, user, plan, options, snapshot)
            route_id = trip['route_id']
            request = SimpleNamespace(cookies={accounts.COOKIE_NAME: 'fixture-session'}, headers={})
            status = shanghai.assistant_status(route_id, request, user)
            request.headers['X-TripWise-CSRF'] = status['csrf_token']
            shanghai.apply_assistant(route_id, 'register', request, user, {'request_id': 'register-1'})
            proposed = shanghai.apply_assistant(route_id, 'message', request, user,
                dict(request_id='message-01', route_id=route_id, base_version=1, poi_snapshot_id=snapshot, text='Убери первое место и добавь Красную площадь.'))
            self.assertEqual(proposed['type'], 'proposal', proposed)
            self.assertEqual(proposed['diff']['added'][0]['day'], day['date'])
            self.assertIn(added, [item['poi_id'] for item in proposed['plan']['days'][0]['visits']])
            before = shanghai.owned_route(route_id, user['id'])
            self.assertEqual(before['plan'], plan)
            self.assertEqual(len(before['options']['conversation']), 2)
            shanghai.apply_assistant(route_id, 'decide', request, user,
                dict(request_id='decision-1', proposal_id=proposed['proposal_id'], base_version=1, decision='confirm'))
            after = shanghai.owned_route(route_id, user['id'])
            self.assertEqual(after['version'], 2)
            visited = {item['poi_id'] for item in after['plan']['days'][0]['visits']}
            self.assertNotIn(poi_id, visited)
            self.assertIn(added, visited)
            stale = shanghai.apply_assistant(route_id, 'message', request, user,
                dict(request_id='message-stale', route_id=route_id, base_version=1, poi_snapshot_id=snapshot, text='Убери место.'))
            self.assertEqual(stale['code'], 'VERSION_CONFLICT')
            self.assertEqual(shanghai.owned_route(route_id, user['id'])['version'], 2)

    def test_backend_plan_and_mock_llm_preview_confirmation(self):
        from backend.moscow import MoscowAssistant, plan_moscow
        from backend.shanghai import MoscowPreferences

        preferences = MoscowPreferences(
            city='moscow', timezone='Europe/Moscow', start_date='2026-11-20', days=3,
            day_start='09:00', day_end='19:00', pace='balanced', transport='walk',
            group={'adult': 1, 'child': 0, 'senior': 0, 'pet': 0},
            primary_interests=['museums', 'nature'], secondary_interests=['cafes'],
            required_ids=[], budget_amount=15000, budget_currency='RUB', budget_basis='per_group',
        )
        with patch.dict('os.environ', {'MOSCOW_OSRM_FOOT': '', 'OPENROUTER_API_KEY': ''}):
            plan, options, snapshot = plan_moscow(preferences)
            service = MoscowAssistant()
        self.assertFalse(service.status()[1]['available'])
        self.assertEqual(len(plan['days']), 3)
        self.assertTrue(all(day['visits'] and any(item['kind'] == 'meal' for item in day['breaks'])
                            for day in plan['days']))
        self.assertTrue(all(leg['basis'] == 'geometric_estimate' and leg['geometry'] is None
                            for day in plan['days'] for leg in day['legs']))
        day = plan['days'][0]
        removed = day['visits'][0]['poi_id']
        service.configured = True
        service.interpreter = lambda _: {'action': 'propose', 'operations': [
            {'op': 'remove_poi', 'poi_id': removed},
            {'op': 'set_meal_break', 'day': day['date'], 'start_at': day['date'] + 'T14:00:00+03:00',
             'end_at': day['date'] + 'T14:45:00+03:00'},
        ], 'clarification': None}
        token, state = service.session()
        state['route_options'] = options
        csrf = state['csrf']
        service.request(token, csrf, 'register', dict(request_id='register-1', route_id='route-1', version=1,
                        preferences=preferences.model_dump(mode='json'), plan=plan))
        proposal = service.request(token, csrf, 'message', dict(request_id='message-01', route_id='route-1',
                                   base_version=1, poi_snapshot_id=snapshot, text='Убери место и перенеси обед.'))
        self.assertEqual(proposal['type'], 'proposal', proposal)
        self.assertEqual(state['moscow_version'], 1)
        self.assertIn(removed, [item['poi_id'] for item in state['moscow_route']['days'][0]['visits']])
        confirmed = service.request(token, csrf, 'decide', dict(request_id='decision-1',
                                    proposal_id=proposal['proposal_id'], base_version=1, decision='confirm'))
        self.assertEqual(confirmed['version'], 2)
        first = confirmed['plan']['days'][0]
        self.assertNotIn(removed, [item['poi_id'] for item in first['visits']])
        self.assertTrue(any(item['kind'] == 'meal' and 'T14:00:' in item['start_at'] for item in first['breaks']))
        self.assertEqual(len(confirmed['options']['conversation']), 2)

    def test_transport_changes_on_confirmation_and_unavailable_mode_leaves_plan_intact(self):
        from backend import moscow, shanghai

        preferences = shanghai.MoscowPreferences(
            city='moscow', timezone='Europe/Moscow', start_date='2026-11-20', days=1,
            day_start='09:00', day_end='19:00', pace='balanced', transport='walk',
            group={'adult': 1, 'child': 0, 'senior': 0, 'pet': 0},
            primary_interests=['museums'], secondary_interests=['nature'], required_ids=[],
            budget_amount=15000, budget_currency='RUB', budget_basis='per_group',
        )
        real_travel_matrix = moscow.travel_matrix

        def fixture_matrix(pois, mode):
            matrix = real_travel_matrix(pois, 'walking')
            matrix['mode'] = mode
            if mode == 'car':
                by_id = {poi['id']: poi['coordinates'] for poi in pois}
                for pair in matrix['pairs']:
                    coordinates = [by_id[pair[key]] for key in ('from_poi_id', 'to_poi_id')]
                    pair.update(basis='routing_provider', source_id='test-road-provider',
                                geometry={'type': 'LineString', 'coordinates': [
                                    [point['longitude'], point['latitude']] for point in coordinates]})
            return matrix

        with (patch.dict('os.environ', {'MOSCOW_OSRM_FOOT': '', 'MOSCOW_RAIL_CONFIG': ''}),
              patch.object(moscow, 'travel_matrix', side_effect=fixture_matrix)):
            plan, options, snapshot = moscow.plan_moscow(preferences)
            service = moscow.MoscowAssistant()
            service.configured = True
            token, session = service.session()
            session['route_options'] = options
            csrf = session['csrf']
            service.request(token, csrf, 'register', dict(request_id='register-1', route_id='route-1', version=1,
                            preferences=preferences.model_dump(mode='json'), plan=plan))

            def request_transport(mode, version):
                service.interpreter = lambda _: {'action': 'propose', 'operations': [
                    {'op': 'set_transport', 'transport': mode},
                ], 'clarification': None}
                return service.request(token, csrf, 'message', dict(request_id='message-' + mode,
                                       route_id='route-1', base_version=version, poi_snapshot_id=snapshot,
                                       text='Измени транспорт.'))

            preview = request_transport('car', 1)
            self.assertEqual(preview['type'], 'proposal', preview)
            self.assertEqual(preview['diff']['transport'], 'taxi')
            self.assertEqual(session['route_options']['transport'], 'walk')
            legs = preview['plan']['days'][0]['legs']
            self.assertTrue(legs)
            self.assertTrue(all(leg['geometry']['type'] == 'LineString' for leg in legs))
            confirmed = service.request(token, csrf, 'decide', dict(request_id='decision-1',
                                        proposal_id=preview['proposal_id'], base_version=1, decision='confirm'))
            self.assertEqual(confirmed['type'], 'confirmed', confirmed)
            self.assertEqual(confirmed['options']['transport'], 'taxi')
            refused = request_transport('public_transit', 2)
            self.assertEqual(refused['code'], 'TRANSPORT_UNAVAILABLE')
            self.assertTrue(refused['message'])
            self.assertEqual(session['moscow_route'], confirmed['plan'])
            self.assertEqual(session['moscow_version'], 2)

    def test_real_poi_unknown_then_explicit_duration_for_one_and_three_days(self):
        ids = ['moscow-cultural-688', 'moscow-cultural-642']
        pois, snapshot = load_moscow_v7(poi_ids=ids)
        by_id = {poi['id']: poi for poi in pois}
        self.assertEqual(by_id[ids[0]]['categories'], ['museums'])
        self.assertEqual(set(by_id[ids[1]]['categories']), {'museums', 'history'})
        for day_count in (1, 3):
            with self.subTest(days=day_count):
                profile, _, matrix, context = fixture(count=1, days=day_count)
                profile.update(destination={'country_code': 'RU', 'city': 'Москва'}, timezone='Europe/Moscow',
                               required_poi_ids=[ids[0]])
                context['poi_snapshot_id'] = snapshot
                ranking = recommend(profile, pois)
                unknown = build_route(profile, pois, ranking, matrix, context)
                self.assertEqual(unknown['status'], 'unknown')
                self.assertFalse(any(day['visits'] for day in unknown['days']))
                context['duration_overrides'] = [{'poi_id': ids[0], 'minutes': 55, 'basis': 'expert_estimate',
                    'method_version': 'test-only', 'author': 'test fixture',
                    'recorded_at': '2026-11-20T00:00:00+00:00', 'evidence_url': None}]
                planned = build_route(profile, pois, ranking, matrix, context)
                self.assertEqual(len(planned['days']), day_count)
                self.assertEqual(sum(len(day['visits']) for day in planned['days']), 1)
                self.assertIn('OPENING_HOURS_NOT_VERIFIED', planned['warnings'])

    def test_directed_transfers_and_unknown_approaches(self):
        def relation(ref, mode, ids):
            return {'type': 'relation', 'tags': {'route': mode, 'ref': ref}, 'members': [
                {'type': 'node', 'ref': stop, 'role': 'stop'} for stop in ids]}

        nodes = [{'type': 'node', 'id': stop, 'lat': 55.75, 'lon': 37.60 + stop / 100} for stop in range(1, 7)]
        source = {'elements': nodes + [relation('1', 'subway', (1, 2)),
                                      relation('14', 'train', (3, 4)), relation('D1', 'train', (4, 6))]}
        self.assertIsNone(route_between(build_graph(source, ESTIMATES), {1: 30}, {6: 40}))
        graph = build_graph(source, ESTIMATES, [{'from_stop': 2, 'to_stop': 3,
                                               'duration_seconds': 180, 'source_id': 'fixture'}])
        matrix = build_matrix(graph, ['first', 'second'], {'first': {'enter': {'1': 30}},
                                                          'second': {'exit': {'6': 40}}})
        direct, reverse = matrix['pairs']
        self.assertEqual(reverse['status'], 'unknown')
        self.assertIsNone(reverse['duration_seconds'])
        steps = direct['transport_steps']
        self.assertEqual([step['line'] for step in steps if step['kind'] == 'rail_estimate'],
                         ['metro:1', 'mcc:14', 'mcd:1'])
        self.assertEqual(sum(step['duration_seconds'] for step in steps), direct['duration_seconds'])

    @unittest.skipUnless(RAIL.exists(), 'Нет реального снимка метро')
    def test_real_osm_consecutive_stops(self):
        source = load_pois(RAIL)
        graph = build_graph(source, ESTIMATES)
        start, finish = 6937381514, 6937381511
        duration, steps = route_between(graph, {start: 0}, {finish: 0})
        self.assertGreater(duration, 120)
        self.assertTrue(any(step['kind'] == 'rail_estimate' and step['line'] == 'metro:5' for step in steps))
