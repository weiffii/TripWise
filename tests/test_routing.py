"""Контрольные расписания, границы и неизвестные данные однодневного Routing."""

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ai_ds.data import load_pois
from ai_ds.recommendation import recommend
from ai_ds.routing import RoutingError, build_route, seconds_us

ROOT = Path(__file__).resolve().parents[1]


class RoutingTests(unittest.TestCase):
    def setUp(self):
        rows = load_pois(ROOT / 'data/processed/pois.synthetic.validated.json')
        self.pois = [deepcopy(rows[0]), deepcopy(rows[1])]
        self.profile = load_pois(ROOT / 'examples/preferences/history.json')
        self.profile['top_k'] = 1
        self.profile['required_poi_ids'] = [self.pois[1]['id']]
        self.context = dict(day_start='2026-11-17T09:00:00+03:00',
                            day_end='2026-11-17T12:00:00+03:00',
                            endpoint_policy='first_last_poi', declared_pace='balanced',
                            max_active_seconds=9000, rest_after_visit_seconds=900)
        self.matrix = dict(mode='walking', basis='test_matrix', source_id='synthetic_route_fixture', pairs=[
            dict(from_poi_id=self.pois[0]['id'], to_poi_id=self.pois[1]['id'],
                 status='known', duration_seconds=600, distance_meters=700),
            dict(from_poi_id=self.pois[1]['id'], to_poi_id=self.pois[0]['id'],
                 status='known', duration_seconds=900, distance_meters=850)])

    def route(self):
        return build_route(self.profile, self.pois, recommend(self.profile, self.pois),
                           self.matrix, self.context)

    def ids(self, route):
        return [v['poi_id'] for v in route['days'][0]['visits']]

    def test_exact_schedule_required_outside_top_k(self):
        route = self.route()
        self.assertEqual(self.ids(route), [p['id'] for p in self.pois])
        day = route['days'][0]
        self.assertEqual(day['visits'][1]['start_at'], '2026-11-17T09:55:00+03:00')
        self.assertEqual(day['visits'][1]['end_at'], '2026-11-17T10:40:00+03:00')
        self.assertEqual(day['breaks'], [dict(start_at='2026-11-17T09:30:00+03:00',
                                            end_at='2026-11-17T09:45:00+03:00')])
        self.assertEqual(route['constraint_checks']['time'], 'passed')
        self.assertEqual(route['status'], 'unknown')
        self.assertEqual(route['unplaced_required_poi_ids'], [])

    def test_exact_window_and_active_limits(self):
        self.context['day_end'] = '2026-11-17T10:40:00+03:00'
        self.context['max_active_seconds'] = 5100
        self.assertEqual(len(self.ids(self.route())), 2)
        self.profile['required_poi_ids'] = [p['id'] for p in self.pois]
        self.context['day_end'] = '2026-11-17T10:39:59+03:00'
        route = self.route()
        self.assertEqual(route['status'], 'infeasible')
        self.assertEqual(route['constraint_checks']['time'], 'failed')
        self.assertEqual(route['unplaced_required_poi_ids'], sorted(self.profile['required_poi_ids']))

    def test_active_budget_counts_visits_and_travel_not_rest(self):
        self.context['max_active_seconds'] = 5099
        self.assertEqual(self.ids(self.route()), [self.pois[1]['id']])
        self.context['max_active_seconds'] = 5100
        self.assertEqual(len(self.ids(self.route())), 2)

    def test_directed_and_unreachable_pairs(self):
        self.matrix['pairs'][0].update(status='unreachable', duration_seconds=None, distance_meters=None)
        self.assertEqual(self.ids(self.route()), [p['id'] for p in reversed(self.pois)])
        self.matrix['pairs'][1].update(status='unreachable', duration_seconds=None, distance_meters=None)
        self.profile['required_poi_ids'] = [p['id'] for p in self.pois]
        self.assertEqual(self.route()['status'], 'infeasible')

    def test_missing_and_unknown_are_not_unreachable(self):
        self.profile['required_poi_ids'] = [p['id'] for p in self.pois]
        self.matrix['pairs'] = []
        self.assertEqual(self.route()['status'], 'unknown')
        self.matrix['pairs'] = [dict(from_poi_id=self.pois[0]['id'], to_poi_id=self.pois[1]['id'],
                                   status='unknown', duration_seconds=None, distance_meters=None)]
        self.assertEqual(self.route()['status'], 'unknown')

    def test_unknown_duration_and_known_impossibility(self):
        self.profile['required_poi_ids'] = [p['id'] for p in self.pois]
        self.pois[0]['visit_duration_minutes'] = None
        self.pois[0]['visit_duration_basis'] = None
        self.assertEqual(self.route()['status'], 'unknown')
        self.context['max_active_seconds'] = 60
        self.assertEqual(self.route()['status'], 'infeasible')

    def test_null_distance_warning_and_budget_not_checked(self):
        self.matrix['pairs'][0]['distance_meters'] = None
        self.profile['budget'] = dict(amount=0, currency='USD', scope='trip_food_and_entertainment')
        route = self.route()
        self.assertIn('DISTANCE_UNKNOWN', route['warnings'])
        self.assertEqual(route['constraint_checks']['budget'], 'not_checked')
        self.assertEqual(route['constraint_checks']['opening_hours'], 'unknown')
        self.assertEqual(route['status'], 'unknown')

    def test_no_input_mutation_and_permutation_stability(self):
        before = deepcopy((self.pois, self.profile, self.matrix, self.context))
        first = self.route()
        self.assertEqual(before, (self.pois, self.profile, self.matrix, self.context))
        self.pois.reverse(); self.matrix['pairs'].reverse()
        self.assertEqual(self.route(), first)

    def test_travel_tie_resolves_ids(self):
        for pair in self.matrix['pairs']:
            pair['duration_seconds'] = 600
        self.assertEqual(self.ids(self.route()), sorted(p['id'] for p in self.pois))

    def test_recommendation_consistency(self):
        rec = recommend(self.profile, self.pois)
        rec['items'][0]['rank'] = True
        with self.assertRaises(RoutingError):
            build_route(self.profile, self.pois, rec, self.matrix, self.context)

    def test_snapshot_warnings_are_not_recomputed_for_equality(self):
        rec = recommend(self.profile, self.pois)
        rec['warnings'].append({'code': 'FUTURE_VERIFICATION_DATE', 'poi_id': self.pois[0]['id']})
        route = build_route(self.profile, self.pois, rec, self.matrix, self.context)
        self.assertEqual(len(self.ids(route)), 2)
        rec['model_version'] = 'unknown-model'
        with self.assertRaises(RoutingError):
            build_route(self.profile, self.pois, rec, self.matrix, self.context)

    def test_snapshot_interest_reasons_require_actual_categories(self):
        rec = recommend(self.profile, self.pois)
        for categories in (None, [], ['nature'], ['museums', 'museums'], [{}]):
            with self.subTest(categories=categories):
                altered = deepcopy(rec)
                altered['items'][0]['reasons'] = [{'code': 'PRIMARY_INTEREST_MATCH'}]
                if categories is not None:
                    altered['items'][0]['reasons'][0]['categories'] = categories
                with self.assertRaises(RoutingError):
                    build_route(self.profile, self.pois, altered, self.matrix, self.context)
        altered = deepcopy(rec)
        altered['items'][0]['reasons'] = [{'code': 'SECONDARY_INTEREST_MATCH', 'categories': ['nature']}]
        with self.assertRaises(RoutingError):
            build_route(self.profile, self.pois, altered, self.matrix, self.context)
        self.assertEqual(len(self.ids(self.route())), 2)

    def test_seconds_conversion_is_exact_for_huge_int_and_decimal_float(self):
        value = 10 ** 30 + 1
        self.assertEqual(seconds_us(value, 'test'), value * 1_000_000)
        self.assertEqual(seconds_us(0.123456, 'test'), 123456)
        self.assertEqual(seconds_us(1e30, 'test'), 10 ** 36)
        with self.assertRaises(RoutingError):
            seconds_us(0.1234567, 'test')

    def test_bad_context(self):
        for field, value in [('day_start', '2026-11-17T09:00:00'),
                             ('day_end', '2026-11-17T09:00:00+03:00'),
                             ('day_start', '2026-11-18T09:00:00+03:00'),
                             ('day_start', '2026-11-17T09:00:00+00:00'),
                             ('declared_pace', 'relaxed'), ('endpoint_policy', 'hotel'),
                             ('max_active_seconds', True), ('max_active_seconds', float('inf')),
                             ('rest_after_visit_seconds', -1), ('rest_after_visit_seconds', 0.0000001)]:
            with self.subTest(field=field, value=value):
                context = deepcopy(self.context); context[field] = value
                with self.assertRaises(RoutingError):
                    build_route(self.profile, self.pois, recommend(self.profile, self.pois), self.matrix, context)

    def test_multiday_rejected(self):
        self.profile['date_end'] = '2026-11-18'
        with self.assertRaises(RoutingError):
            self.route()

    def test_bad_matrix(self):
        for field, value in [('duration_seconds', True), ('duration_seconds', float('nan')),
                             ('duration_seconds', -1), ('distance_meters', float('inf')),
                             ('status', 'missing'), ('from_poi_id', 'unknown')]:
            matrix = deepcopy(self.matrix); matrix['pairs'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(RoutingError):
                build_route(self.profile, self.pois, recommend(self.profile, self.pois), matrix, self.context)
        self.matrix['pairs'].append(deepcopy(self.matrix['pairs'][0]))
        with self.assertRaises(RoutingError):
            self.route()

    def test_mode_and_null_numeric_validation(self):
        self.matrix['mode'] = 'car'
        with self.assertRaises(RoutingError):
            self.route()
        self.matrix['mode'] = 'walking'
        self.matrix['pairs'][0]['status'] = 'unknown'
        with self.assertRaises(RoutingError):
            self.route()

    def test_zero_rest_decimal_seconds_and_huge_active_int(self):
        self.context['rest_after_visit_seconds'] = 0
        self.context['max_active_seconds'] = 10 ** 400
        self.matrix['pairs'][0]['duration_seconds'] = 0.125
        route = self.route()
        self.assertEqual(route['days'][0]['breaks'], [])
        self.assertEqual(route['days'][0]['visits'][1]['start_at'], '2026-11-17T09:30:00.125000+03:00')

    def test_empty_and_candidate_limit(self):
        self.profile['required_poi_ids'] = []
        empty = build_route(self.profile, [], recommend(self.profile, []),
                            dict(mode='walking', basis='test_matrix', source_id='fixture', pairs=[]), self.context)
        self.assertEqual(empty['status'], 'unknown')
        self.assertEqual(empty['days'][0]['visits'], [])
        self.pois = load_pois(ROOT / 'data/processed/pois.synthetic.validated.json')
        self.profile['top_k'] = 9
        with self.assertRaises(RoutingError) as caught:
            self.route()
        self.assertEqual(caught.exception.details[0]['code'], 'CANDIDATE_LIMIT_EXCEEDED')

    def test_cli_utf8_success_and_error(self):
        args = [sys.executable, '-B', '-m', 'ai_ds.routing', '--pois',
                str(ROOT / 'data/processed/pois.synthetic.validated.json'), '--preferences',
                str(ROOT / 'examples/preferences/history.json'), '--scenario',
                str(ROOT / 'examples/routing/day.synthetic.json')]
        env = dict(os.environ, PYTHONIOENCODING='cp1251', PYTHONUTF8='0')
        result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        route = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(route['status'], 'unknown')
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / 'нет.json')
            result = subprocess.run(args[:-1] + [missing], cwd=ROOT, env=env, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('нет.json', result.stderr.decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
