"""Regression around the reported places on Moscow V7, without live OSRM or LLM."""
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from ai_ds.moscow_road import travel_matrix
from ai_ds.openrouter import OpenRouterError
from backend.moscow import MoscowAssistant, indexes, plan_moscow
from backend.shanghai import MoscowPreferences


MUSEUM = 'moscow-cultural-609'
GRILL = 'moscow-osm-node-3478472276'
PARK = 'moscow-cultural-p415'
SILVER_AGE = ['moscow-osm-node-12086701012', 'moscow-osm-node-13256127284']
OFFLINE = {'MOSCOW_OSRM_FOOT': '', 'MOSCOW_OSRM_CAR': ''}


def metrics(plan):
    max_walk, max_wait, max_meal_separation = 0, 0, 0
    raw_meal_windows = []
    for day in plan['engine_days']:
        events = day['events']
        max_walk = max([max_walk, *(row['end_min'] - row['start_min'] for row in events
                                   if row['type'] == 'travel' and row.get('mode') == 'walk')])
        max_wait = max([max_wait, *(b['start_min'] - a['end_min'] for a, b in zip(events, events[1:]))])
        meals = [i for i, row in enumerate(events) if row['type'] == 'meal']
        for start, end in zip(meals, meals[1:]):
            separation = events[end]['start_min'] - events[start]['end_min']
            max_meal_separation = max(max_meal_separation, separation)
            raw_meal_windows.append({
                'date': day['date'], 'meal_ids': [events[start]['poi_id'], events[end]['poi_id']],
                'elapsed_minutes': separation,
                'activities_in_between': [row['type'] for row in events[start + 1:end]],
            })
    return {'max_walk_minutes': max_walk, 'max_internal_idle_minutes': max_wait,
            'max_meal_to_meal_minutes': max_meal_separation, 'meal_intervals': raw_meal_windows,
            'unplaced_required': plan['unplaced_required_poi_ids']}


class MoscowRealReportedRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prefs = MoscowPreferences(
            city='moscow', timezone='Europe/Moscow', start_date='2026-11-24', days=1,
            day_start='09:00', day_end='21:00', pace='balanced', transport='walk',
            transport_modes=['walk'], group={'adult': 1, 'child': 0, 'senior': 0, 'pet': 0},
            primary_interests=['history', 'museums', 'nature'], secondary_interests=['cafes'],
            required_ids=[MUSEUM], budget_amount=8500, budget_currency='RUB', budget_basis='per_group',
        )
        with patch.dict(os.environ, OFFLINE):
            cls.plan, cls.options, cls.snapshot = plan_moscow(cls.prefs)
        cls.report = {'notes': 'Actual Moscow V7 POIs. No live OSRM or LLM; reconstructed profile.',
                      'profile': cls.prefs.model_dump(mode='json')}

    @classmethod
    def tearDownClass(cls):
        path = os.environ.get('TRIPWISE_REAL_ROUTE_REPORT')
        if path:
            Path(path).write_text(json.dumps(cls.report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')

    def test_reported_pairs_and_planned_transitions(self):
        points = indexes()[0]
        keys = [MUSEUM, GRILL, PARK, *SILVER_AGE]
        with patch.dict(os.environ, OFFLINE):
            pairs = travel_matrix([points[key] for key in keys], 'walking')['pairs']
            hard, _, _ = plan_moscow(self.prefs.model_copy(update={'required_ids': [MUSEUM, PARK]}))
        expected_pairs = {(GRILL, PARK), (MUSEUM, PARK), *((PARK, key) for key in SILVER_AGE)}
        measured = [row for row in pairs if (row['from_poi_id'], row['to_poi_id']) in expected_pairs]
        self.assertEqual(len(measured), 4)
        self.report['exact_poi_pair_estimates'] = [
            {'from_id': row['from_poi_id'], 'to_id': row['to_poi_id'],
             'walk_minutes': round(row['duration_seconds'] / 60, 1)
             if row.get('duration_seconds') is not None else None, 'basis': row.get('basis')}
            for row in measured]
        self.report['requested_museum'] = metrics(self.plan)
        self.report['requested_museum_and_park'] = metrics(hard)
        self.assertEqual(self.plan['unplaced_required_poi_ids'], [])
        self.assertLess(metrics(self.plan)['max_walk_minutes'], 112)
        self.assertLess(metrics(self.plan)['max_internal_idle_minutes'], 177)
        self.assertTrue(any(interval['activities_in_between'] for interval in metrics(self.plan)['meal_intervals']))
        for plan in (self.plan, hard):
            self.assertLessEqual(plan['budget_summary']['known_total'], self.prefs.budget_amount)
            for events in (day['events'] for day in plan['engine_days']):
                self.assertTrue(all(a['end_min'] <= b['start_min'] for a, b in zip(events, events[1:])))

    def test_offline_named_park_never_silently_changes_saved_route(self):
        self.report['offline_chat'] = {}
        for key, command in (
            ('quoted', 'Добавь «Парк имени 50-летия Октября» в первый день без удаления других мест.'),
            ('short_name', 'Добавь парк 50-летия Октября в первый день без удаления других мест.'),
        ):
            with self.subTest(command=key):
                self.report['offline_chat'][key] = self.check_named_park(command)

    def check_named_park(self, command):
        service = MoscowAssistant()
        service.configured = True
        service.interpreter = Mock(side_effect=OpenRouterError('LLM_RATE_LIMITED'))
        state = {'route_options': deepcopy(self.options)}
        with patch.dict(os.environ, OFFLINE):
            service._request('fixture', state, 'register', {
                'route_id': 'stress-route', 'version': 1, 'preferences': self.prefs.model_dump(mode='json'),
                'plan': deepcopy(self.plan)})
            outcome = service._request('fixture', state, 'message', {
                'route_id': 'stress-route', 'base_version': 1, 'poi_snapshot_id': self.snapshot,
                'text': command})
        reported = {key: outcome[key] for key in ('type', 'message', 'diff') if key in outcome}
        if outcome['type'] == 'proposal':
            reported['proposed_metrics'] = metrics(outcome['plan'])
            self.assertLessEqual(outcome['plan']['budget_summary']['known_total'], self.prefs.budget_amount)
            self.assertIn(MUSEUM, [row['poi_id'] for day in outcome['plan']['days'] for row in day['visits']])
        else:
            self.assertEqual(outcome['type'], 'clarification')
            self.assertIn('50-летия Октября', outcome['message'])
        self.assertEqual(service.interpreter.call_count, 0)
        self.assertEqual(state['moscow_version'], 1)
        self.assertEqual(state['moscow_route'], self.plan)
        return reported

    def test_report_budget_and_transport_variants(self):
        variants = {
            'walk_low_budget': {'budget_amount': 3000},
            'mixed_with_two_required': {'transport': 'mixed', 'transport_modes': ['walk', 'transit'],
                                        'required_ids': [MUSEUM, PARK]},
            'two_days_walk_four_required': {'days': 2, 'required_ids': [MUSEUM, PARK, GRILL, SILVER_AGE[1]]},
        }
        self.report['variants'] = {}
        for variant, overrides in variants.items():
            with self.subTest(variant=variant), patch.dict(os.environ, OFFLINE):
                preferences = self.prefs.model_copy(update=overrides)
                plan, _, _ = plan_moscow(preferences)
                result = metrics(plan)
                result['cost_rub'] = plan['budget_summary']['known_total']
                result['days'] = len(plan['engine_days'])
                self.report['variants'][variant] = result
                ids = {visit['poi_id'] for day in plan['days'] for visit in day['visits']}
                self.assertEqual(set(preferences.required_ids) - ids, set(result['unplaced_required']))
                self.assertLessEqual(result['cost_rub'], preferences.budget_amount)
                self.assertEqual(result['days'], preferences.days)
                self.assertTrue(all(leg['mode'] in preferences.transport_modes or
                                    leg.get('selection_reason') == 'short_local_walking_connector'
                                    for day in plan['days'] for leg in day['legs']))
