"""Unit tests for TripWise's deterministic Moscow planning projection (no V7 file needed)."""
import unittest

from ai_ds.moscow_catalog import MODEL_VERSION, planning_fields


class MoscowPlanningEstimatesV2(unittest.TestCase):
    def row(self, name='Объект', category='cafes', **extra):
        return {'id': 'osm:node:1234', 'name': name, 'categories': [category],
                'primary_category': category, **extra}

    def test_repeated_runs_are_identical(self):
        row = self.row('Кофейня Парк')
        self.assertEqual(planning_fields(row), planning_fields(row))
        self.assertIn('v3', MODEL_VERSION)

    def test_cafe_types_have_distinct_prices(self):
        coffees = planning_fields(self.row('Кофейня'))
        restaurants = planning_fields(self.row('Ресторан'))
        self.assertEqual(coffees['food_type']['value'], 'coffee_shop')
        self.assertEqual(restaurants['food_type']['value'], 'restaurant')
        self.assertLess(coffees['average_check_rub']['value'], restaurants['average_check_rub']['value'])

    def test_coffee_shop_dinner_unsuitable(self):
        fields = planning_fields(self.row('Кофейня'))
        self.assertEqual(fields['meal_suitability']['value']['dinner'], 'unlikely')

    def test_canteen_lunch_likely(self):
        fields = planning_fields(self.row('Городская столовая'))
        self.assertEqual(fields['meal_suitability']['value']['lunch'], 'likely')

    def test_unknown_entry_is_not_free(self):
        fields = planning_fields(self.row('Лесопарк', 'nature'))
        self.assertGreater(fields['price_rub']['value'], 0)
        self.assertEqual(fields['price_rub']['source_type'], 'synthetic')

    def test_verified_free_tag(self):
        fields = planning_fields(self.row('Парк', 'nature', tags={'fee':'no'}))
        self.assertEqual(fields['price_rub']['value'], 0)
        self.assertEqual(fields['price_rub']['source_type'], 'source')

    def test_original_prices_preserved(self):
        row = self.row('Ресторан', price_rub=1900, average_check_rub=1600,
                       opening_hours=[['12:00','22:00']]*7)
        fields = planning_fields(row)
        self.assertEqual(fields['price_rub']['value'], 1900)
        self.assertEqual(fields['average_check_rub']['value'], 1600)
        self.assertEqual(fields['opening_hours']['source_type'], 'source')
        self.assertEqual(fields['price_rub']['source_type'], 'source')

    def test_estimates_marked_synthetic(self):
        fields = planning_fields(self.row())
        self.assertEqual(fields['meal_suitability']['source_type'], 'synthetic')
        self.assertEqual(fields['meal_suitability']['confidence'], 'low')


if __name__ == '__main__':
    unittest.main()
