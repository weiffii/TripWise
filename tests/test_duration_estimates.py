"""Плановые synthetic-сценарии для согласованного минимума час на место."""
from copy import deepcopy
from pathlib import Path
import unittest
from ai_ds.data import load_pois
from ai_ds.duration_estimates import estimate_unknown_durations

ROOT = Path(__file__).resolve().parents[1]
STAMP = '2026-11-18T12:00:00+03:00'


class DurationEstimateTests(unittest.TestCase):
    def setUp(self):
        self.pois = load_pois(ROOT / 'data/processed/pois.synthetic.validated.json')[:2]
        for poi in self.pois:
            poi.update(visit_duration_minutes=None, visit_duration_basis=None)

    def estimate(self, **kwargs):
        return estimate_unknown_durations(self.pois, recorded_at=STAMP, **kwargs)

    def test_minimum_hour_and_multiple_categories_max(self):
        self.pois[0]['categories'] = ['cafes', 'viewpoints']
        self.pois[1]['categories'] = ['museums', 'entertainment']
        rows, report = self.estimate()
        by_id = {r['poi_id']: r for r in rows}
        self.assertEqual(by_id[self.pois[0]['id']]['minutes'], 60)
        self.assertEqual(by_id[self.pois[1]['id']]['minutes'], 90)
        self.assertFalse(report['are_verified_visit_durations'])
        self.assertEqual(report['input_data_kind'], 'synthetic')

    def test_no_mutation_and_repeatable_explicit_time(self):
        original = deepcopy(self.pois)
        self.assertEqual(self.estimate(), self.estimate())
        self.assertEqual(self.pois, original)
        rows, _ = self.estimate()
        self.assertTrue(all(r['basis'] == 'expert_estimate' for r in rows))
        self.assertTrue(all(r['evidence_url'] is None for r in rows))

    def test_known_shorter_source_duration_preserved(self):
        self.pois[0].update(visit_duration_minutes=30, visit_duration_basis='source')
        rows, report = self.estimate()
        self.assertNotIn(self.pois[0]['id'], [r['poi_id'] for r in rows])
        self.assertEqual(self.pois[0]['visit_duration_minutes'], 30)
        self.assertIn('KNOWN_DURATION_PRESERVED', [r['code'] for r in report['skipped']])

    def test_unavailable_and_explicit_review_exclusion(self):
        self.pois[0]['availability'] = 'unavailable'
        rows, report = self.estimate(excluded_poi_ids=[self.pois[1]['id']])
        self.assertEqual(rows, [])
        self.assertEqual({s['code'] for s in report['skipped']}, {'UNAVAILABLE', 'EXPLICITLY_EXCLUDED'})

    def test_bad_provenance_and_exclusions_rejected(self):
        for kwargs in [{'author': ''}, {'recorded_at': '2026-11-18'},
                       {'excluded_poi_ids': ['invented']}, {'excluded_poi_ids': 'id'}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                estimate_unknown_durations(self.pois, **dict({'recorded_at': STAMP}, **kwargs))

    def test_invalid_input_not_silently_accepted(self):
        self.pois[0]['categories'] = ['invented']
        with self.assertRaises(ValueError):
            self.estimate()

    def test_empty_dataset_and_context_sidecar_compatibility(self):
        rows, report = estimate_unknown_durations([], recorded_at=STAMP)
        self.assertEqual(rows, [])
        self.assertEqual(report['estimated_count'], 0)
        from ai_ds.routing_v02_contract import validate_context
        profile = load_pois(ROOT / 'examples/preferences/history.json')
        profile.update(date_start='2026-11-18', date_end='2026-11-18')
        context = {'schema_version': '0.2', 'endpoint_policy': 'first_last_poi',
            'pace_policy_version': 'pace-policy-v0.2', 'unknown_duration_policy': 'strict',
            'days': [{'date': '2026-11-18', 'day_start': STAMP,
                      'day_end': '2026-11-18T18:00:00+03:00'}],
            'duration_overrides': self.estimate()[0]}
        normalized = validate_context(context, profile, {p['id']: p for p in self.pois})
        self.assertTrue(all(v >= 3600 * 1000000 for v in normalized['durations'].values()))
        self.assertEqual(len(normalized['estimated_ids']), 2)


if __name__ == '__main__':
    unittest.main()
