from datetime import date
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from backend.shanghai import place_details


class TripPlaceDetailsTests(unittest.TestCase):
    def test_moscow_hours_distinguish_closed_unknown_and_estimated(self):
        def point(raw, kind):
            return {'planning': {'opening_hours': {'value': raw, 'source_type': kind}}}

        catalog = {
            'source': point('Mo-Fr 10:00-19:00; Sa-Su off', 'source'),
            'estimate': point([None, ['10:00', '20:00'], None, None, None, None, None], 'synthetic'),
            'multiple': point('Mo-Fr 10:00-13:00; Mo-Fr 14:00-18:00', 'source'),
        }
        with patch('backend.moscow.indexes', return_value=(catalog,)), patch('backend.moscow.moscow_pois', return_value=[]):
            monday, tuesday, saturday = date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 28)
            self.assertEqual(place_details('moscow', 'source', monday)['opening'], ('10:00', '19:00'))
            self.assertEqual(place_details('moscow', 'source', saturday)['opening'], ())
            self.assertEqual(place_details('moscow', 'estimate', tuesday)['opening'], ['10:00', '20:00'])
            self.assertEqual(place_details('moscow', 'estimate', tuesday)['opening_basis'], 'synthetic')
            self.assertEqual(place_details('moscow', 'multiple', monday)['opening_text'],
                             'Mo-Fr 10:00-13:00; Mo-Fr 14:00-18:00')
            with self.assertRaises(HTTPException) as error:
                place_details('moscow', 'absent', monday)
            self.assertEqual(error.exception.status_code, 404)

    def test_shanghai_date_override_and_separate_ticket_rates(self):
        point = {'id': 'museum', 'kind': 'attraction', 'price_cny': 300,
                 'price_multipliers': {'senior': .6}, 'pet_fee_cny': 15,
                 'opening': {'open': '09:00', 'close': '18:00', 'closed_weekdays': [1],
                             'by_date': {'2026-11-23': {'open': '12:00', 'closed': False}}}}
        with patch('backend.shanghai.assistant', return_value=SimpleNamespace(data={'pois': [point]})):
            result = place_details('shanghai', 'museum', date(2026, 11, 23))
            self.assertEqual(result['opening'], ['12:00', '18:00'])
            self.assertEqual(result['unit_prices'], {'adult': 300, 'child': 150, 'senior': 180, 'pet': 15})
