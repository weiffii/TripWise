import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import patch

from backend.moscow import budget_friendly_places, cell, geographic_shortlist, _budget_constrained
from ai_ds.moscow_catalog import planning_fields


def record(key, name, price, food_type, lon):
    return {'id': key, 'name': name, 'categories': ['cafes'],
            'coordinates': {'latitude': 55.75, 'longitude': lon},
            'planning': {'price_rub': {'value': price},
                         'food_type': {'value': food_type},
                         'meal_suitability': {'value': {'breakfast': 'possible',
                                                        'lunch': 'likely', 'dinner': 'likely'}}}}


class BudgetExperienceSelection(unittest.TestCase):
    def setUp(self):
        self.anchor = {'lat': 55.75, 'lon': 37.62}
        self.records = [
            record('burger', 'Бургер Кинг', 350, 'fast_food', 37.6201),
            record('canteen', 'Столовая', 250, 'canteen', 37.6202),
            record('restaurant1', 'Семейный ресторан', 1600, 'restaurant', 37.6203),
            record('restaurant2', 'Ресторан с кухней', 1800, 'restaurant', 37.6204),
            record('restaurant3', 'Ресторан другой', 1200, 'restaurant', 37.6205),
            record('cafe1', 'Кафе', 1200, 'cafe', 37.6206),
            record('cafe2', 'Кафе поблизости', 1000, 'cafe', 37.6207),
        ]
        by_id = {p['id']: p for p in self.records}
        categories = defaultdict(lambda: defaultdict(list))
        for p in self.records:
            categories['cafes'][cell({'lat': 55.75, 'lon': p['coordinates']['longitude']})].append(p['id'])
        self.index = patch('backend.moscow.indexes', return_value=(by_id, categories, {}))
        self.index.start(); self.addCleanup(self.index.stop)
        self.cells = categories['cafes']

    def test_generous_budget_pool_includes_full_service_dinner(self):
        ids = budget_friendly_places(self.anchor, self.cells, 5, meal_kind='dinner', dining_target=1900)
        self.assertTrue(any(p.startswith('restaurant') for p in ids), ids)
        self.assertIn('canteen', ids) # fallback is still available
        self.assertEqual(len(ids), len(set(ids)))

    def test_constrained_budget_preserves_cheap_dinner(self):
        ids = budget_friendly_places(self.anchor, self.cells, 2, meal_kind='dinner')
        self.assertIn('canteen', ids)

    def test_quick_service_brand_is_not_mistaken_for_full_service(self):
        for name in ('Бургер Кинг', 'Burger King', 'KFC'):
            data = {'id': 'sample:' + name, 'name': name,
                    'categories': ['cafes'], 'primary_category': 'cafes',
                    'source_type': 'restaurant'}
            fields = planning_fields(data)
            self.assertEqual(fields['food_type']['value'], 'fast_food', name)
            self.assertEqual(fields['food_type']['source_type'], 'synthetic')

    def test_group_and_person_budget_consistent(self):
        def prefs(amount, basis):
            return SimpleNamespace(required_ids=[], primary_interests=['cafes'], secondary_interests=[],
                                   pace='balanced', budget_amount=amount, budget_basis=basis,
                                   days=3, transport='walk', group=SimpleNamespace(adult=2,child=0,senior=0))
        self.assertEqual(_budget_constrained(prefs(30000,'per_person')),
                         _budget_constrained(prefs(60000,'per_group')))
        one = [p['id'] for p in geographic_shortlist(prefs(30000,'per_person'), [self.anchor])]
        two = [p['id'] for p in geographic_shortlist(prefs(60000,'per_group'), [self.anchor])]
        self.assertEqual(one, two)
        self.assertTrue(any(id.startswith('restaurant') for id in one), one)


if __name__ == '__main__':
    unittest.main()
