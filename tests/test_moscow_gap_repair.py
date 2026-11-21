from unittest import TestCase

from ai_ds.moscow_gap_repair import Gap, MoscowGapIndex, _travel_fit, route_gaps


DAY = '2026-11-21'


def poi(key, longitude, *, price=100, opened=True, category='history'):
    return {'id': key, 'categories': [category], 'coordinates': {'latitude': 55.75, 'longitude': longitude},
            'planning': {'duration_minutes': {'value': 30}, 'price_rub': {'value': price},
                         'opening_hours': {'source_type': 'synthetic',
                                           'value': [(['09:00', '19:00'] if opened else None)] * 7}}}


class MoscowGapRepairTests(TestCase):
    def test_whole_catalog_index_finds_only_local_feasible_interested_candidate(self):
        index = MoscowGapIndex([
            poi('previous', 37.62), poi('following', 37.63),
            poi('outside_shortlist', 37.625), poi('used', 37.621),
            poi('expensive', 37.621, price=800), poi('far', 37.9),
            poi('closed', 37.622, opened=False), poi('wrong_interest', 37.62, category='shopping'),
        ])
        profile = {'primary_interests': ['history'], 'secondary_interests': []}
        context = {'moscow_group': {'adult': 2, 'child': 0, 'senior': 0}}
        gap = Gap(DAY, 600, 700, 'internal', 'previous', 'following')
        candidates, _ = index.candidates(gap, profile, context, {'previous', 'following', 'used'}, 400)
        self.assertEqual([item[0] for item in candidates], ['outside_shortlist'])

    def test_transport_lower_bound_considers_each_permitted_profile(self):
        def leg(origin, target):
            return {'from_poi_id': origin, 'to_poi_id': target, 'status': 'known', 'duration_seconds': 300}

        gap = Gap(DAY, 600, 650, 'internal', 'a', 'b')
        options = {'profiles': {'transit': {'pairs': []}, 'walk': {'pairs': [leg('a', 'x'), leg('x', 'b')]}}}
        self.assertTrue(_travel_fit(options, gap, 'x', 40))
        self.assertFalse(_travel_fit(options, gap, 'x', 41))

    def test_reserved_lunch_splits_a_gap_without_becoming_available_time(self):
        events = [{'type': 'visit', 'poi_id': 'a', 'start_min': 570, 'end_min': 720},
                  {'type': 'visit', 'poi_id': 'b', 'start_min': 835, 'end_min': 900}]
        plan = {'days': [{'date': DAY, 'breaks': [{'kind': 'meal',
                 'start_at': f'{DAY}T13:00:00+03:00', 'end_at': f'{DAY}T13:30:00+03:00'}]}],
                'engine_days': [{'date': DAY, 'events': events}]}
        context = {'days': [{'date': DAY, 'day_start': f'{DAY}T09:30:00+03:00',
                             'day_end': f'{DAY}T15:00:00+03:00'}]}
        self.assertEqual([(gap.start, gap.end) for gap in route_gaps(plan, context)], [(720, 780)])
