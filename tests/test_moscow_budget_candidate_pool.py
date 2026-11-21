"""No master JSON needed: test that geographic candidate selection has cheap options."""
import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import patch

from backend.moscow import (
    _budget_constrained, budget_friendly_places, cell, geographic_shortlist,
)


def record(key, lat, lon, category, price, kind=None, meals=None):
    fields = {'price_rub': {'value': price}}
    if kind:
        fields['food_type'] = {'value': kind}
        fields['meal_suitability'] = {'value': meals or {k: 'likely' for k in ('breakfast', 'lunch', 'dinner')}}
    return {'id': key, 'name': key, 'coordinates': {'latitude': lat, 'longitude': lon},
            'categories': [category], 'planning': fields,
            'catalog_signals': {'multiple_sources': False, 'has_hours': False, 'verified_entrance': False}}


class BudgetPoolTests(unittest.TestCase):
    def setUp(self):
        self.anchor = {'lat': 55.75, 'lon': 37.62}
        self.rows = [
            record('expensive', 55.75, 37.6201, 'cafes', 2200, 'restaurant'),
            record('cheap-canteen', 55.754, 37.621, 'cafes', 330, 'canteen'),
            record('coffee-dinner-wrong', 55.751, 37.621, 'cafes', 150, 'coffee_shop',
                   {'breakfast': 'likely', 'lunch': 'unlikely', 'dinner': 'unlikely'}),
            record('free-park', 55.752, 37.623, 'nature', 0),
            record('low-cost-history', 55.755, 37.624, 'history', 60),
            record('ticket-1', 55.751, 37.62, 'entertainment', 1400),
            record('ticket-2', 55.756, 37.625, 'entertainment', 1600),
        ]
        cat = defaultdict(lambda: defaultdict(list))
        for point in self.rows:
            cat[point['categories'][0]][cell({'lat': point['coordinates']['latitude'],
                                               'lon': point['coordinates']['longitude']})].append(point['id'])
        self.indexer = patch('backend.moscow.indexes', side_effect=lambda: ({r['id']: r for r in self.rows}, cat, {}))
        self.indexer.start()
        self.addCleanup(self.indexer.stop)

    def test_dinner_shortlist_finds_canteen_not_coffee_shop(self):
        selected = budget_friendly_places(self.anchor, self._cells('cafes'), 2, meal_kind='dinner')
        self.assertIn('cheap-canteen', selected)
        self.assertNotIn('coffee-dinner-wrong', selected)

    def test_normalized_pool_never_calls_unknown_price_free(self):
        self.rows.append(record('unknown', 55.754, 37.622, 'history', None))
        picks = budget_friendly_places(self.anchor, self._cells('history'), 5)
        self.assertNotIn('unknown', picks)

    def test_small_budget_adds_non_interest_free_and_cheap_candidates(self):
        prefs = SimpleNamespace(required_ids=[], primary_interests=['entertainment'], secondary_interests=[],
                                pace='balanced', budget_amount=1500, budget_basis='per_group', days=3,
                                transport='walk', group=SimpleNamespace(adult=1, child=0, senior=0))
        self.assertTrue(_budget_constrained(prefs))
        actual = [p['id'] for p in geographic_shortlist(prefs, [self.anchor])]
        self.assertIn('free-park', actual)
        self.assertIn('cheap-canteen', actual)
        self.assertIn('ticket-1', actual)  # Keep interest; engine decides feasibility.

    def _cells(self, category):
        by_id = {r['id']: r for r in self.rows}
        out = defaultdict(list)
        for item in self.rows:
            if category not in item['categories']:
                continue
            grid = cell({'lat': item['coordinates']['latitude'], 'lon': item['coordinates']['longitude']})
            out[grid].append(item['id'])
        return out


if __name__ == '__main__':
    unittest.main()
