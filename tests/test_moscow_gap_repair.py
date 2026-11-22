from unittest import TestCase, skipUnless

from ai_ds.moscow_engine_adapter import timestamp
from ai_ds.moscow_gap_repair import Gap, MoscowGapIndex, _relocations, _travel_fit, repair_gaps, route_gaps
from ai_ds.shanghai_assistant import node_executable


DAY = '2026-11-21'


def poi(key, longitude, *, price=100, opened=True, category='history'):
    return {'id': key, 'name': key, 'categories': [category], 'coordinates': {'latitude': 55.75, 'longitude': longitude},
            'planning': {'duration_minutes': {'value': 30}, 'price_rub': {'value': price},
                         'opening_hours': {'source_type': 'synthetic',
                                           'value': [(['09:00', '19:00'] if opened else None)] * 7}}}


class MoscowGapRepairTests(TestCase):
    @skipUnless(node_executable(), 'Для регрессии используется общий Node.js планировщик')
    def test_two_nearby_sights_fill_the_reported_177_minute_meal_window(self):
        points = {key: poi(key, 37.620 + i / 1000, category='history' if i in (1, 2) else 'cafes')
                  for i, key in enumerate(('lunch', 'museum', 'gallery', 'dinner'))}
        for key in ('lunch', 'dinner'):
            points[key]['planning']['average_check_rub'] = {'value': 100}
        profile = {'primary_interests': ['history'], 'secondary_interests': [], 'transport': 'walking',
                   'date_start': DAY, 'required_poi_ids': [], 'pace': 'balanced', 'budget': {'amount': 2000}}
        lunch = {'poi_id': 'lunch', 'start_at': timestamp(DAY, 854), 'end_at': timestamp(DAY, 914)}
        dinner = {'poi_id': 'dinner', 'start_at': timestamp(DAY, 1094), 'end_at': timestamp(DAY, 1154)}
        meals = {DAY: {'lunch': lunch, 'dinner': dinner}}
        context = {'days': [{'date': DAY, 'day_start': timestamp(DAY, 720), 'day_end': timestamp(DAY, 1170)}],
                   'moscow_group': {'adult': 1, 'child': 0, 'senior': 0},
                   'poi_snapshot_id': '177-minute-fixture',
                   'moscow_meal_pins': {DAY: {'lunch': '14:14', 'dinner': '18:14'}}}
        route = {
            'schema_version': '0.2', 'status': 'unknown', 'budget_summary': {'known_total': 200},
            'moscow_meals': meals,
            'days': [{'date': DAY, 'pace': 'balanced', 'visits': [lunch, dinner], 'legs': [], 'breaks': []}],
            'engine_days': [{'date': DAY, 'total_cost_cny': 200, 'events': [
                {'type': 'meal', 'meal_kind': 'lunch', 'poi_id': 'lunch', 'start_min': 854, 'end_min': 914},
                {'type': 'travel', 'from_poi_id': 'lunch', 'to_poi_id': 'dinner', 'start_min': 1091, 'end_min': 1094},
                {'type': 'meal', 'meal_kind': 'dinner', 'poi_id': 'dinner', 'start_min': 1094, 'end_min': 1154},
            ]}],
        }

        def state(ids):
            return {'preferences': profile, 'pois': [points[key] for key in ids],
                    'travel_times': {'pairs': [
                        {'from_poi_id': left, 'to_poi_id': right, 'status': 'known', 'duration_seconds': 180,
                         'distance_meters': 125, 'source_id': 'fixture', 'basis': 'routing_provider'}
                        for left in ids for right in ids if left != right]},
                    'routing_context': {**context, 'opening_intervals': [],
                                        'duration_overrides': [{'poi_id': key, 'minutes': 75} for key in ids],
                                        'cost_context': {'visit_costs': [{'poi_id': key, 'amount': 100} for key in ids]}}}

        self.assertIn(177, [gap.minutes for gap in route_gaps(route, context) if gap.kind == 'internal'])
        result = repair_gaps(route, profile, context, MoscowGapIndex(points.values()), state)
        self.assertEqual(set(result['gap_diagnostics']['inserted_poi_ids']), {'museum', 'gallery'})
        self.assertEqual(result['moscow_meals'], meals)
        self.assertFalse(any(gap.kind == 'internal' and gap.minutes > 60 for gap in route_gaps(result, context)))

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

    def test_internal_gap_at_last_visit_can_relocate_without_stale_slot(self):
        # Резервный обед может стоять после последнего посещения в engine_days.
        self.assertEqual(list(_relocations(['a', 'b'], 2, set(), {})), [['b', 'a']])
        self.assertEqual(list(_relocations(['a'], 1, set(), {})), [])

    @skipUnless(node_executable(), 'Для регрессии используется общий Node.js планировщик')
    def test_fills_347_minute_gap_with_multiple_interested_pois(self):
        restaurant = poi('lunch', 37.620, category='cafes')
        restaurant['planning']['average_check_rub'] = {'value': 100}
        closing = poi('closing', 37.630)
        additions = [poi(f'candidate-{i}', 37.620 + i / 1000) for i in range(1, 7)]
        for point in additions:
            point['planning']['duration_minutes']['value'] = 60
        points = {point['id']: point for point in (restaurant, closing, *additions)}
        profile = {'primary_interests': ['history'], 'secondary_interests': [], 'transport': 'walking',
                   'date_start': DAY, 'required_poi_ids': ['closing'], 'pace': 'balanced', 'budget': {'amount': 2000}}
        day_context = {'date': DAY, 'day_start': timestamp(DAY, 750), 'day_end': timestamp(DAY, 1215)}
        meal = {'poi_id': 'lunch', 'start_at': timestamp(DAY, 760), 'end_at': timestamp(DAY, 810)}
        context = {'days': [day_context], 'moscow_group': {'adult': 1, 'child': 0, 'senior': 0, 'pet': 0},
                   'poi_snapshot_id': 'test'}
        route = {
            'schema_version': '0.2', 'status': 'unknown', 'budget_summary': {'known_total': 200},
            'moscow_meals': {DAY: {'lunch': meal}},
            'days': [{'date': DAY, 'pace': 'balanced', 'visits': [meal, {'poi_id': 'closing',
                      'start_at': timestamp(DAY, 1159), 'end_at': timestamp(DAY, 1189)}], 'breaks': []}],
            'engine_days': [{'date': DAY, 'total_cost_cny': 200, 'events': [
                {'type': 'meal', 'meal_kind': 'lunch', 'poi_id': 'lunch', 'start_min': 760, 'end_min': 810},
                {'type': 'travel', 'start_min': 1157, 'end_min': 1159},
                {'type': 'visit', 'poi_id': 'closing', 'start_min': 1159, 'end_min': 1189},
            ]}],
        }

        def state(ids):
            matrix = {'pairs': [
                {'from_poi_id': left, 'to_poi_id': right, 'status': 'known', 'duration_seconds': 120,
                 'distance_meters': 90, 'source_id': 'fixture', 'basis': 'routing_provider'}
                for left in ids for right in ids if left != right]}
            data = {**context, 'duration_overrides': [
                {'poi_id': key, 'minutes': points[key]['planning']['duration_minutes']['value']} for key in ids],
                'cost_context': {'visit_costs': [{'poi_id': key, 'amount': 100} for key in ids]},
                'opening_intervals': []}
            return {'preferences': profile, 'pois': [points[key] for key in ids],
                    'travel_times': matrix, 'routing_context': data}

        self.assertIn(347, [gap.minutes for gap in route_gaps(route, context)])
        result = repair_gaps(route, profile, context, MoscowGapIndex(points.values()), state)
        self.assertGreaterEqual(len(result['gap_diagnostics']['inserted_poi_ids']), 4)
        self.assertFalse(any(gap.kind == 'internal' and gap.minutes > 45 for gap in route_gaps(result, context)))
        self.assertEqual(result['native_checks']['time'], 'passed')
        self.assertEqual(result['native_checks']['required_places'], 'passed')

class FreeFallbackGapTests(TestCase):
    def test_free_nature_candidate_can_fill_cheap_trip_with_unaffordable_interest(self):
        index = MoscowGapIndex([
            poi('paid-theatre', 37.621, price=1600, category='entertainment'),
            poi('free-square', 37.622, price=0, category='nature'),
            poi('unknown-entry', 37.623, price=None, category='nature'),
        ])
        profile = {'primary_interests': ['entertainment'], 'secondary_interests': [],
                   'budget': {'amount': 0}}
        ctx = {'moscow_group': {'adult': 1, 'child': 0, 'senior': 0},
               'days': [{'date': DAY}]}
        gap = Gap(DAY, 600, 840, 'tail', None, None)
        results, _ = index.candidates(gap, profile, ctx, set(), 0)
        self.assertEqual([row[0] for row in results], ['free-square'])
