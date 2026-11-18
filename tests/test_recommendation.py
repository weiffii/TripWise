"""Независимые контрольные порядки и ошибки рекомендаций."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ai_ds.data import load_pois
from ai_ds.demo import main
from ai_ds.recommendation import RecommendationError, recommend, validate_preferences

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.pois = load_pois(ROOT / 'data/processed/pois.synthetic.validated.json')
        self.profile = load_pois(ROOT / 'examples/preferences/history.json')

    def result(self, pois=None, profile=None):
        return recommend(self.profile if profile is None else profile,
                         self.pois if pois is None else pois, evaluated_at=NOW)

    def ids(self, result):
        return [p['poi_id'] for p in result['items']]

    def pair(self):
        a = deepcopy(self.pois[0]); b = deepcopy(a)
        a['id'] = 'synthetic:a'; b['id'] = 'synthetic:b'
        b['provenance']['source_record_id'] = 'b'
        a['categories'] = ['museums']; b['categories'] = ['museums']
        return a, b

    def test_history_expected_order(self):
        self.assertEqual(self.ids(self.result()), [
            'synthetic:museums-01', 'synthetic:museums-09', 'synthetic:history-02',
            'synthetic:history-10', 'synthetic:nature-03'])

    def test_nature_expected_order(self):
        profile = load_pois(ROOT / 'examples/preferences/nature.json')
        self.assertEqual(self.ids(self.result(profile=profile)), [
            'synthetic:unusual-08', 'synthetic:nature-03', 'synthetic:nature-11',
            'synthetic:museums-09', 'synthetic:museums-01'])

    def test_primary_dominates_secondary_and_quality(self):
        a, b = self.pair()
        a['rating']['value'] = 0
        b['categories'] = ['nature']; b['rating']['value'] = 5
        self.assertEqual(self.ids(self.result([b, a])), [a['id'], b['id']])

    def test_secondary_precedes_rating(self):
        a, b = self.pair()
        a['categories'].append('nature'); a['rating']['value'] = 0
        b['rating']['value'] = 5
        self.assertEqual(self.ids(self.result([b, a])), [a['id'], b['id']])

    def test_rating_scales_and_missing(self):
        a, b = self.pair()
        a['rating'] = dict(value=80, scale_min=0, scale_max=100)
        b['rating'] = dict(value=4, scale_min=0, scale_max=5)
        self.assertEqual(self.ids(self.result([b, a])), [a['id'], b['id']])
        a['rating'] = None; b['rating']['value'] = 0
        self.assertEqual(self.ids(self.result([a, b])), [b['id'], a['id']])

    def test_extreme_rating_scale_preserves_order(self):
        high, low = self.pair()
        high['id'], low['id'] = 'z-high', 'a-low'
        high['rating'] = dict(value=0, scale_min=-1e308, scale_max=1e308)
        low['rating'] = dict(value=-5e307, scale_min=-1e308, scale_max=1e308)
        self.assertEqual(self.ids(self.result([low, high])), ['z-high', 'a-low'])

    def test_popularity_and_incomparable_metric(self):
        a, b = self.pair(); b['popularity']['value'] = 1000
        self.assertEqual(self.ids(self.result([a, b])), [b['id'], a['id']])
        b['popularity']['metric'] = 'visitors'
        result = self.result([b, a])
        self.assertEqual(self.ids(result), [a['id'], b['id']])
        self.assertIn('POPULARITY_NOT_COMPARABLE', [w['code'] for w in result['warnings']])
        self.assertFalse(any(r['code'] == 'POPULARITY_SIGNAL'
                             for p in result['items'] for r in p['reasons']))

    def test_freshness_and_future(self):
        a, b = self.pair()
        a['provenance']['verified_at'] = '2026-11-14T12:00:00+00:00'
        b['provenance']['verified_at'] = '2026-11-15T12:00:00+00:00'
        self.assertEqual(self.ids(self.result([a, b])), [b['id'], a['id']])
        b['provenance']['verified_at'] = '2026-11-18T12:00:00+00:00'
        self.assertEqual(self.ids(self.result([a, b])), [a['id'], b['id']])

    def test_extreme_dates_and_equivalent_offsets(self):
        a, b = self.pair()
        a['provenance']['verified_at'] = '0001-01-01T00:00:00+14:00'
        b['provenance']['verified_at'] = '0001-01-01T00:00:00+00:00'
        self.assertEqual(self.ids(self.result([a, b])), [b['id'], a['id']])
        a['provenance']['verified_at'] = '2026-11-15T12:00:00.000001+03:00'
        b['provenance']['verified_at'] = '2026-11-15T09:00:00.000001+00:00'
        self.assertEqual(self.ids(self.result([b, a])), [a['id'], b['id']])
        b['provenance']['verified_at'] = '2026-11-15T09:00:00.000002+00:00'
        self.assertEqual(self.ids(self.result([a, b])), [b['id'], a['id']])

    def test_ties_repeatability_and_immutability(self):
        a, b = self.pair(); before = deepcopy([a, b])
        self.assertEqual(self.result([a, b]), self.result([b, a]))
        self.assertEqual([a, b], before)

    def test_empty_and_large_k(self):
        self.assertEqual(self.result([])['items'], [])
        profile = deepcopy(self.profile); profile['top_k'] = 100
        result = self.result(profile=profile)
        self.assertEqual(len(result['items']), 15)
        self.assertEqual(len(set(self.ids(result))), 15)
        self.assertTrue(all(p['score'] is None for p in result['items']))

    def test_profile_errors(self):
        for field in self.profile:
            p = deepcopy(self.profile); del p[field]
            self.assertTrue(validate_preferences(p), field)
        for field, value in [('primary_interests', []), ('primary_interests', [{}]),
                             ('primary_interests', ['museums'] * 2),
                             ('secondary_interests', ['museums']), ('top_k', True),
                             ('required_poi_ids', [{}]), ('date_end', '2026-11-01'),
                             ('timezone', 'Invalid/Zone'), ('budget', {'amount': -1})]:
            p = deepcopy(self.profile); p[field] = value
            with self.assertRaises(RecommendationError):
                self.result(profile=p)

    def test_required_outside_top_k_and_conflicts(self):
        p = deepcopy(self.profile); p['top_k'] = 1
        p['required_poi_ids'] = ['synthetic:shopping-06']
        result = self.result(profile=p)
        self.assertEqual(result['required_poi_ids'], p['required_poi_ids'])
        self.assertNotIn(p['required_poi_ids'][0], self.ids(result))
        for poi_id, expected in [('missing', 'UNKNOWN_REQUIRED_POI'),
                                 ('synthetic:unusual-16', 'REQUIRED_POI_CONFLICT')]:
            p['required_poi_ids'] = [poi_id]
            with self.assertRaises(RecommendationError) as caught:
                self.result(profile=p)
            self.assertEqual(caught.exception.details[0]['code'], expected)

    def test_destination_and_required_destination(self):
        a, b = self.pair(); b['city'] = 'Другой город'
        self.assertEqual(self.ids(self.result([a, b])), [a['id']])
        p = deepcopy(self.profile); p['required_poi_ids'] = [b['id']]
        with self.assertRaises(RecommendationError):
            self.result([a, b], p)

    def test_invalid_pois_not_silently_dropped(self):
        a, b = self.pair(); b['categories'] = []
        with self.assertRaises(RecommendationError):
            self.result([a, b])
        with self.assertRaises(RecommendationError):
            self.result([a, a])

    def test_budget_and_explanations(self):
        result = self.result()
        self.assertTrue(all(v == 'not_checked' for v in result['constraint_checks'].values()))
        self.assertEqual(result['items'][0]['reasons'][0], {
            'code': 'PRIMARY_INTEREST_MATCH', 'categories': ['history', 'museums']})
        p = deepcopy(self.profile)
        p['budget'] = {'amount': 0, 'currency': 'RUB', 'scope': 'trip_food_and_entertainment'}
        self.assertEqual(self.ids(self.result(profile=p)), self.ids(result))
        a, _ = self.pair(); a['categories'] = ['shopping']
        self.assertIn({'code': 'FALLBACK_SELECTION'}, self.result([a])['items'][0]['reasons'])

    def test_cli_success_and_errors(self):
        args = ['--pois', str(ROOT / 'data/processed/pois.synthetic.validated.json'),
                '--preferences', str(ROOT / 'examples/preferences/history.json')]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(args), 0)
        self.assertEqual(json.loads(out.getvalue())['model_version'], 'lexicographic-v0.1')
        self.assertEqual(err.getvalue(), '')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.json'; path.write_text('{}', encoding='utf-8')
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(args[:-1] + [str(path)]), 1)
                self.assertEqual(main(args[:-1] + [str(path.parent / 'missing')]), 2)

    def test_cli_utf8_bytes_with_cp1251_environment(self):
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'cp1251'
        env['PYTHONUTF8'] = '0'
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            pois_path, profile_path = directory / 'pois.json', directory / 'profile.json'
            poi = deepcopy(self.pois[0]); poi['id'] = 'synthetic:музей'
            poi['city'] = 'Тестовый город'
            profile = deepcopy(self.profile)
            profile['destination']['city'] = 'Тестовый город'
            pois_path.write_text(json.dumps([poi], ensure_ascii=False), encoding='utf-8')
            profile_path.write_text(json.dumps(profile, ensure_ascii=False), encoding='utf-8')
            args = [sys.executable, '-B', '-m', 'ai_ds.demo', '--pois', str(pois_path),
                    '--preferences', str(profile_path)]
            result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.decode('utf-8'))['items'][0]['poi_id'],
                             'synthetic:музей')
            result = subprocess.run(args[:-1] + [str(directory / 'нет.json')],
                                    cwd=ROOT, env=env, capture_output=True)
            self.assertEqual(result.returncode, 2)
            error = json.loads(result.stderr.decode('utf-8'))
            self.assertIn('нет.json', error['error']['details'][0]['message'])
            profile['required_poi_ids'] = ['несуществующий']
            profile_path.write_text(json.dumps(profile, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True)
            self.assertEqual(result.returncode, 1)
            error = json.loads(result.stderr.decode('utf-8'))
            self.assertEqual(error['error']['details'][0]['poi_id'], 'несуществующий')


    def test_missing_primary_category_is_explicit_not_fabricated(self):
        profile=deepcopy(self.profile)
        profile['primary_interests']=['unusual']
        profile['secondary_interests']=[]
        result=self.result(pois=self.pois[:3],profile=profile)
        self.assertTrue(result['items'])
        self.assertIn('NO_PRIMARY_INTEREST_MATCH',[w['code'] for w in result['warnings']])
        for item in result['items']:
            self.assertIn('FALLBACK_SELECTION',[r['code'] for r in item['reasons']])
            self.assertNotIn('PRIMARY_INTEREST_MATCH',[r['code'] for r in item['reasons']])

    def test_no_city_candidates_has_explicit_warning(self):
        result=self.result(pois=[])
        self.assertEqual(result['items'],[])
        self.assertIn('NO_CANDIDATES_FOR_DESTINATION',[w['code'] for w in result['warnings']])

if __name__ == '__main__':
    unittest.main()
