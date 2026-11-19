"""Синтетические ценовые признаки и честное происхождение языковых подписей."""
from copy import deepcopy
import importlib.util
import math
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('shanghai_enrichment', ROOT / 'scripts/enrich_shanghai_demo.py')
enrichment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(enrichment)


def fixture():
    return {'pois': [{'id': 'demo:cafe', 'name': '测试餐馆', 'kind': 'restaurant', 'categories': ['cafes'],
                      'lat': 31.23, 'lon': 121.47, 'price_cny': 60, 'rating': 4.5, 'duration_minutes': 55,
                      'opening': {'open': '08:00', 'close': '22:00', 'closed_weekdays': []},
                      'river_bank': 'west', 'provenance': {'identity': 'synthetic_fixture'}}]}


class ShanghaiEnrichmentTests(unittest.TestCase):
    def test_derived_price_preserves_original_fields_input_and_provenance(self):
        data = fixture()
        before = deepcopy(data)
        output = enrichment.enrich(data)
        self.assertEqual(data, before)
        self.assertEqual(enrichment.original_projection(output['pois'][0]), before['pois'][0])
        self.assertEqual(output['pois'][0]['avg_check_cny'], 60)
        self.assertIn('synthetic', output['pois'][0]['provenance']['avg_check_cny'])
        self.assertEqual(enrichment.enrich(output), output)

    def test_demo_segment_boundaries_are_explicit(self):
        for price, segment in [(0, 'budget'), (60, 'budget'), (60.01, 'midrange'), (150, 'midrange'), (150.01, 'premium')]:
            with self.subTest(price=price):
                data = fixture()
                data['pois'][0]['price_cny'] = price
                self.assertEqual(enrichment.enrich(data)['pois'][0]['price_segment'], segment)

    def test_unknown_translations_stay_missing_instead_of_becoming_fake_labels(self):
        p = enrichment.enrich(fixture())['pois'][0]
        self.assertEqual(p['names']['zh'], '测试餐馆')
        self.assertIsNone(p['names']['ru'])
        self.assertIsNone(p['names']['en'])
        self.assertEqual(p['name_basis']['ru'], 'missing')

    def test_supplied_source_labels_are_keyed_by_exact_entity_id(self):
        data = fixture()
        data['pois'][0]['id'] = 'demo:wikidata-Q123'
        cache = {'Q123': {'labels': {'en': {'value': 'Fixture cafe'}}}, 'Q124': {'labels': {'ru': {'value': 'Другой объект'}}}}
        p = enrichment.enrich(data, cache)['pois'][0]
        self.assertEqual(p['names']['en'], 'Fixture cafe')
        self.assertEqual(p['name_basis']['en'], 'source')
        self.assertIsNone(p['names']['ru'])

    def test_invalid_source_prices_and_duplicate_ids_are_rejected(self):
        for value in [-1, math.nan, math.inf, True]:
            data = fixture()
            data['pois'][0]['price_cny'] = value
            with self.assertRaises(ValueError):
                enrichment.validate_dataset(data)
        data = fixture()
        data['pois'].append(deepcopy(data['pois'][0]))
        with self.assertRaises(ValueError):
            enrichment.validate_dataset(data)

    def test_multicover_does_not_let_one_new_venue_supply_three_distinct_meals(self):
        p = fixture()['pois'][0]
        result = enrichment.greedy_coverage([p], {p['id']: 3})
        self.assertEqual(result['proposed_new_venue_count'], 3)
        self.assertFalse(result['is_proven_minimum'])
        self.assertFalse(result['creates_pois'])

    def test_one_venue_can_cover_neighbouring_anchor_gaps(self):
        a = fixture()['pois'][0]
        b = dict(a, id='nearby', lat=31.2301)
        result = enrichment.greedy_coverage([a, b], {a['id']: 1, b['id']: 1})
        self.assertEqual(result['proposed_new_venue_count'], 1)


if __name__ == '__main__':
    unittest.main()
