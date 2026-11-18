"""Искусственные contract fixtures, без внешних запросов и записи данных."""
from copy import deepcopy
import json
import unittest
from ai_ds.routing_v02_contract import RoutingV02Error, aware_timestamp, evaluate_budget, seconds_us, validate_context, validate_matrix
DAY = '2026-11-18'

def fixture():
    profile = {'date_start': DAY, 'date_end': DAY, 'timezone': 'Europe/Moscow', 'pace': 'balanced', 'transport': 'walking', 'budget': None}
    pois = {key: {'visit_duration_minutes': 30, 'visit_duration_basis': 'test_fixture', 'estimated_cost': None} for key in ('A', 'B')}
    context = {'schema_version': '0.2', 'endpoint_policy': 'first_last_poi', 'pace_policy_version': 'pace-policy-v0.2', 'unknown_duration_policy': 'strict', 'days': [{'date': DAY, 'day_start': DAY + 'T09:00:00+03:00', 'day_end': DAY + 'T18:00:00+03:00', 'meal_break': None, 'blocked_intervals': [], 'max_active_seconds': None}]}
    matrix = {'mode': 'walking', 'basis': 'test_matrix', 'source_id': 'mock', 'pairs': [{'from_poi_id': 'A', 'to_poi_id': 'B', 'status': 'known', 'duration_seconds': 0.1, 'distance_meters': 7}]}
    return profile, pois, context, matrix

def cost_row(key, value, *, meal=False):
    return {'date' if meal else 'poi_id': key, 'amount': value, 'basis': 'test_fixture', 'source_url': None, 'recorded_at': DAY + 'T10:00:00+03:00'}

def costs(context, visit=900, meal=200, complete=True):
    context['cost_context'] = {'currency': 'RUB', 'visit_costs': [cost_row('A', visit)], 'meal_costs': [cost_row(DAY, meal, meal=True)], 'coverage_complete': complete}

class ContractV02Tests(unittest.TestCase):
    def test_exact_pace_and_no_mutation(self):
        p, pois, c, m = fixture()
        before = deepcopy((p, pois, c, m))
        c['days'][0]['meal_break'] = {'kind': 'meal', 'start_at': DAY + 'T13:00:00+03:00', 'end_at': DAY + 'T13:45:00+03:00'}
        original = deepcopy(c)
        n = validate_context(c, p, pois)
        self.assertEqual(n['days'][0]['active_limit_us'], 20790 * 1000000)
        self.assertEqual(n['days'][0]['rest_us'], 900 * 1000000)
        self.assertEqual(c, original)
        self.assertEqual((p, pois, m), (before[0], before[1], before[3]))
    def test_all_paces_and_explicit_smaller_cap(self):
        p, pois, c, _ = fixture()
        for pace, alpha, rest in [('relaxed',55,1200),('balanced',70,900),('intensive',85,600)]:
            with self.subTest(pace=pace):
                p['pace'] = pace
                n = validate_context(c, p, pois)['days'][0]
                self.assertEqual(n['active_limit_us'], 32400 * 1000000 * alpha // 100)
                self.assertEqual(n['rest_us'], rest * 1000000)
        c['days'][0]['max_active_seconds'] = 0.1
        self.assertEqual(validate_context(c, p, pois)['days'][0]['active_limit_us'], 100000)
    def test_per_day_pace_override(self):
        p, pois, c, _ = fixture()
        c['day_pace_overrides'] = {DAY: 'relaxed'}
        self.assertEqual(validate_context(c, p, pois)['days'][0]['pace'], 'relaxed')
        self.assertEqual(p['pace'], 'balanced')
        c['day_pace_overrides']['2099-01-01'] = 'intensive'
        with self.assertRaises(RoutingV02Error): validate_context(c, p, pois)
    def test_no_three_day_limit_and_exact_date_coverage(self):
        p, pois, c, _ = fixture()
        p['date_end'] = '2026-11-21'
        c['days'] = [{'date': f'2026-11-{d}', 'day_start': f'2026-11-{d}T09:00:00+03:00', 'day_end': f'2026-11-{d}T18:00:00+03:00'} for d in range(18,22)]
        self.assertEqual(len(validate_context(c,p,pois)['days']),4)
        c['days'][1] = deepcopy(c['days'][0])
        with self.assertRaises(RoutingV02Error): validate_context(c,p,pois)
    def test_blocked_overlap_outside_and_empty_window(self):
        p, pois, c, _ = fixture()
        for intervals in [[('08:00','10:00')], [('10:00','12:00'),('11:00','13:00')], [('09:00','18:00')]]:
            with self.subTest(intervals=intervals):
                c['days'][0]['blocked_intervals'] = [{'start_at': DAY+'T'+a+':00+03:00', 'end_at': DAY+'T'+b+':00+03:00'} for a,b in intervals]
                with self.assertRaises(RoutingV02Error): validate_context(c,p,pois)
    def test_meal_blocked_not_subtracted_twice(self):
        p, pois, c, _ = fixture()
        interval = {'start_at': DAY+'T13:00:00+03:00','end_at':DAY+'T13:45:00+03:00'}
        c['days'][0]['meal_break'] = dict(interval, kind='meal')
        c['days'][0]['blocked_intervals'] = [interval]
        with self.assertRaises(RoutingV02Error) as error: validate_context(c,p,pois)
        self.assertEqual(error.exception.details[0]['code'],'INVALID_BLOCKED_INTERVALS')
    def test_dst_nonexistent_and_fall_back_absolute_window(self):
        p, pois, c, _ = fixture()
        p.update(date_start='2026-03-08',date_end='2026-03-08',timezone='America/New_York')
        c['days'] = [{'date':'2026-03-08','day_start':'2026-03-08T02:30:00-05:00','day_end':'2026-03-08T05:00:00-04:00'}]
        with self.assertRaises(RoutingV02Error): validate_context(c,p,pois)
        p.update(date_start='2026-11-01',date_end='2026-11-01')
        c['days'] = [{'date':'2026-11-01','day_start':'2026-11-01T01:00:00-04:00','day_end':'2026-11-01T02:00:00-05:00'}]
        self.assertEqual(validate_context(c,p,pois)['days'][0]['window_us'],7200*1000000)
    def test_duration_unknown_and_explicit_estimate_sidecar(self):
        p, pois, c, _ = fixture()
        pois['A']['visit_duration_minutes'] = None
        pois['A']['visit_duration_basis'] = None
        self.assertIsNone(validate_context(c,p,pois)['durations']['A'])
        c['duration_overrides'] = [{'poi_id':'A','minutes':0.5,'basis':'expert_estimate','method_version':'test-only','author':'fixture','recorded_at':DAY+'T10:00:00+03:00','evidence_url':None}]
        n = validate_context(c,p,pois)
        self.assertEqual(n['durations']['A'],30*1000000)
        self.assertIn('A',n['estimated_ids'])
        self.assertIsNone(pois['A']['visit_duration_minutes'])
        c['duration_overrides'][0].pop('author')
        with self.assertRaises(RoutingV02Error): validate_context(c,p,pois)
    def test_opening_unknown_closed_and_split(self):
        p, pois, c, _ = fixture()
        row = {'poi_id':'A','date':DAY,'timezone':'Europe/Moscow','intervals':None,'source_id':'mock','source_url':None,'retrieved_at':DAY+'T10:00:00+03:00','verified_at':None}
        c['opening_intervals'] = [row]
        self.assertIsNone(validate_context(c,p,pois)['openings']['A',DAY])
        row['intervals'] = []
        self.assertEqual(validate_context(c,p,pois)['openings']['A',DAY],[])
        row['intervals'] = [{'start_at':DAY+'T10:00:00+03:00','end_at':DAY+'T12:00:00+03:00'},{'start_at':DAY+'T14:00:00+03:00','end_at':DAY+'T16:00:00+03:00'}]
        self.assertEqual(len(validate_context(c,p,pois)['openings']['A',DAY]),2)
        row['intervals'][1]['start_at'] = DAY+'T11:00:00+03:00'
        with self.assertRaises(RoutingV02Error): validate_context(c,p,pois)
    def test_matrix_direction_precision_and_input_unchanged(self):
        p, pois, _, m = fixture()
        before = deepcopy(m)
        result = validate_matrix(m,p,pois)
        self.assertEqual(result['A','B']['duration_us'],100000)
        self.assertNotIn(('B','A'),result)
        self.assertEqual(m,before)
        self.assertEqual(seconds_us(10**60,'test'),10**66)
        for value in [True,-1,float('nan'),float('inf'),0.0000001]:
            with self.subTest(value=value):
                with self.assertRaises(RoutingV02Error): seconds_us(value,'test')
    def test_matrix_unknown_and_unreachable_require_null(self):
        p, pois, _, m = fixture()
        for status in ('unknown','unreachable'):
            m['pairs'][0].update(status=status,duration_seconds=None,distance_meters=None)
            self.assertIsNone(validate_matrix(m,p,pois)['A','B']['duration_us'])
            m['pairs'][0]['duration_seconds']=0
            with self.assertRaises(RoutingV02Error): validate_matrix(m,p,pois)
    def test_matrix_geometry_https_and_validity(self):
        p, pois, _, m = fixture()
        pair = m['pairs'][0]
        pair['geometry'] = {'type':'LineString','coordinates':[[37,55],[38,56]]}
        pair['navigation_url'] = 'https://example.test/route'
        m['valid_for'] = {'start_at':DAY+'T09:00:00+03:00','end_at':DAY+'T18:00:00+03:00'}
        self.assertEqual(validate_matrix(m,p,pois)['A','B']['geometry'],pair['geometry'])
        self.assertEqual(len(validate_matrix(m,p,pois)['A','B']['valid_for']),2)
        for url in ['http://example.test','https://user:password@example.test','https://example.test/ bad','file:///tmp/a']:
            pair['navigation_url']=url
            with self.assertRaises(RoutingV02Error): validate_matrix(m,p,pois)
        pair['navigation_url']=None
        pair['geometry']['coordinates'][0][0]=181
        with self.assertRaises(RoutingV02Error): validate_matrix(m,p,pois)
    def test_matrix_duplicate_self_and_provider_provenance(self):
        p, pois, _, m = fixture()
        m['pairs'].append(deepcopy(m['pairs'][0]))
        with self.assertRaises(RoutingV02Error): validate_matrix(m,p,pois)
        m['pairs'].pop(); m['pairs'][0]['to_poi_id']='A'
        with self.assertRaises(RoutingV02Error): validate_matrix(m,p,pois)
        m['pairs'][0]['to_poi_id']='B';m['basis']='routing_provider'
        with self.assertRaises(RoutingV02Error): validate_matrix(m,p,pois)
        m['retrieved_at']=DAY+'T10:00:00+03:00'
        self.assertEqual(validate_matrix(m,p,pois)['A','B']['basis'],'routing_provider')
    def test_budget_known_lower_bound_and_missing_meal(self):
        p, pois, c, _ = fixture()
        p['budget']={'currency':'RUB','amount':1000,'scope':'trip_food_and_entertainment'}
        costs(c)
        self.assertEqual(evaluate_budget({'A'},[DAY],p,validate_context(c,p,pois))['status'],'failed')
        costs(c,visit=900,meal=None)
        self.assertEqual(evaluate_budget({'A'},[DAY],p,validate_context(c,p,pois))['status'],'unknown')
        c['cost_context']['meal_costs']=[]
        self.assertEqual(evaluate_budget({'A'},[DAY],p,validate_context(c,p,pois))['status'],'unknown')
    def test_budget_complete_flag_cannot_hide_missing_visits_or_currency(self):
        p, pois, c, _ = fixture()
        p['budget']={'currency':'RUB','amount':1000,'scope':'trip_food_and_entertainment'}
        costs(c,visit=100,meal=100)
        result=evaluate_budget({'A','B'},[DAY],p,validate_context(c,p,pois))
        self.assertEqual(result['status'],'unknown')
        self.assertIn('visit:B',result['unknown_items'])
        c['cost_context']['visit_costs'][0]['currency']='USD'
        result=evaluate_budget({'A'},[DAY],p,validate_context(c,p,pois))
        self.assertEqual(result['status'],'unknown')
        self.assertIn('CURRENCY_MISMATCH',result['warnings'])
        self.assertEqual(result['known_total'],100)
    def test_budget_exact_decimal_and_pass_only_full_coverage(self):
        p, pois, c, _ = fixture()
        p['budget']={'currency':'RUB','amount':0.3,'scope':'trip_food_and_entertainment'}
        costs(c,visit=0.1,meal=0.2)
        result=evaluate_budget({'A'},[DAY],p,validate_context(c,p,pois))
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['known_total'],'0.3')
        json.dumps(result,allow_nan=False)
        c['cost_context']['coverage_complete']=False
        self.assertEqual(evaluate_budget({'A'},[DAY],p,validate_context(c,p,pois))['status'],'unknown')
        p['budget']=None
        self.assertEqual(evaluate_budget({'A'},[DAY],p,validate_context(c,p,pois))['status'],'not_checked')
    def test_cost_and_opening_duplicate_rejected(self):
        p, pois, c, _ = fixture()
        costs(c)
        c['cost_context']['visit_costs'] *= 2
        with self.assertRaises(RoutingV02Error): validate_context(c,p,pois)
        c['cost_context']=None
        row={'poi_id':'A','date':DAY,'timezone':'Europe/Moscow','intervals':[],'source_id':'mock','source_url':None,'retrieved_at':DAY+'T10:00:00+03:00','verified_at':None}
        c['opening_intervals']=[row,deepcopy(row)]
        with self.assertRaises(RoutingV02Error): validate_context(c,p,pois)
    def test_structured_errors_and_unsupported_policy(self):
        p, pois, c, _ = fixture()
        c['schema_version']='0.1'
        with self.assertRaises(RoutingV02Error) as error: validate_context(c,p,pois)
        response=error.exception.response()
        self.assertEqual(response['schema_version'],'0.2')
        self.assertEqual(response['error']['code'],'ROUTING_VALIDATION_ERROR')
        json.dumps(response,ensure_ascii=False)
        with self.assertRaises(RoutingV02Error): aware_timestamp(DAY+'T10:00:00','timestamp')

if __name__ == '__main__':
    unittest.main()
