"""Synthetic-only acceptance tests for the approved multiday model."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import unittest
from ai_ds.data import load_pois
from ai_ds.recommendation import recommend
from ai_ds.multiday import build_route, schedule_sequences, validate_route
from ai_ds.routing_v02_contract import RoutingV02Error
ROOT = Path(__file__).resolve().parents[1]

def fixture(count=3, days=1, duration=60):
    source = load_pois(ROOT / 'data/processed/pois.synthetic.validated.json')[0]
    pois = []
    for index in range(count):
        row = deepcopy(source)
        row.update(id=f'synthetic:multiday-{index:02d}', name=f'Synthetic multiday POI {index}', categories=['museums'], rating=None, popularity=None, visit_duration_minutes=duration, visit_duration_basis='test_fixture', estimated_cost=None, availability='unknown')
        row['provenance']['source_record_id'] = f'multiday-{index}'
        pois.append(row)
    profile = load_pois(ROOT / 'examples/preferences/history.json')
    profile.update(date_end=(date.fromisoformat(profile['date_start']) + timedelta(days=days-1)).isoformat(), primary_interests=['museums'], secondary_interests=[], top_k=count, required_poi_ids=[], pace='balanced')
    context = {'schema_version':'0.2','endpoint_policy':'first_last_poi','pace_policy_version':'pace-policy-v0.2','unknown_duration_policy':'strict','poi_snapshot_id':'synthetic_multiday_fixture','duration_overrides':[],'opening_intervals':[],'cost_context':None,'days':[]}
    for index in range(days):
        day = (date.fromisoformat(profile['date_start']) + timedelta(days=index)).isoformat()
        context['days'].append({'date':day,'day_start':day+'T09:00:00+03:00','day_end':day+'T15:00:00+03:00','meal_break':None,'blocked_intervals':[],'max_active_seconds':None})
    matrix = {'mode':'walking','basis':'test_matrix','source_id':'synthetic_multiday_matrix','retrieved_at':None,'valid_for':None,'pairs':[]}
    for source in pois:
        for target in pois:
            if source['id'] != target['id']:
                matrix['pairs'].append({'from_poi_id':source['id'],'to_poi_id':target['id'],'status':'known','duration_seconds':600,'distance_meters':700,'geometry':None,'navigation_url':'https://example.org/synthetic-navigation'})
    return profile, pois, matrix, context

class MultidayTests(unittest.TestCase):
    def setUp(self):
        self.profile,self.pois,self.matrix,self.context=fixture()
    def route(self):
        return build_route(self.profile,self.pois,recommend(self.profile,self.pois),self.matrix,self.context)
    def explicit(self,sequences):
        return schedule_sequences(self.profile,self.pois,recommend(self.profile,self.pois),self.matrix,self.context,sequences)
    def selected(self,route):
        return [v['poi_id'] for day in route['days'] for v in day['visits']]
    def test_two_days_required_no_duplicates(self):
        self.profile,self.pois,self.matrix,self.context=fixture(count=2,days=2,duration=120)
        for day in self.context['days']: day['day_end']=day['date']+'T12:00:00+03:00'
        self.profile['required_poi_ids']=[p['id'] for p in self.pois]
        route=self.route()
        self.assertEqual([len(d['visits']) for d in route['days']],[1,1])
        self.assertEqual(set(self.selected(route)),set(self.profile['required_poi_ids']))
        self.assertEqual(route['constraint_checks']['required_places'],'passed')
    def test_pace_changes_load_not_fixed_count(self):
        self.profile['pace']='relaxed'; relaxed=self.route()
        self.profile['pace']='intensive'; intensive=self.route()
        self.assertEqual(len(self.selected(relaxed)),2)
        self.assertEqual(len(self.selected(intensive)),3)
        self.assertEqual(relaxed['days'][0]['active_limit_seconds'],198*60)
        self.assertEqual(intensive['days'][0]['rest_after_visit_seconds'],600)
    def test_more_than_three_days_and_eight_candidates(self):
        self.profile,self.pois,self.matrix,self.context=fixture(count=10,days=4,duration=30)
        route=self.route()
        self.assertEqual(len(route['days']),4)
        self.assertEqual(len(self.selected(route)),10)
        self.assertEqual(len(set(self.selected(route))),10)
    def test_unknown_required_duration_never_zero_or_false_infeasible(self):
        self.pois[0].update(visit_duration_minutes=None,visit_duration_basis=None)
        self.profile['required_poi_ids']=[self.pois[0]['id']]
        route=self.route()
        self.assertEqual(route['status'],'unknown')
        self.assertEqual(route['constraint_checks']['time'],'unknown')
        self.assertEqual(self.selected(route),[])
        self.assertEqual(route['unplaced_required_poi_ids'],self.profile['required_poi_ids'])
    def test_proven_required_cannot_fit_any_day(self):
        self.pois[0]['visit_duration_minutes']=400
        self.profile['required_poi_ids']=[self.pois[0]['id']]
        route=self.route()
        self.assertEqual(route['status'],'infeasible')
        self.assertEqual(route['constraint_checks']['required_places'],'failed')
    def test_search_failure_is_unknown_even_for_unreachable_pairs(self):
        self.profile['required_poi_ids']=[p['id'] for p in self.pois]
        for pair in self.matrix['pairs']: pair.update(status='unreachable',duration_seconds=None,distance_meters=None,geometry=None,navigation_url=None)
        route=self.route()
        self.assertEqual(route['status'],'unknown')
        self.assertIn('SEARCH_NOT_PROVEN',route['warnings'])
        self.assertTrue(route['unplaced_required_poi_ids'])
    def test_inputs_unchanged_and_deterministic(self):
        before=deepcopy((self.profile,self.pois,self.matrix,self.context));first=self.route()
        self.pois.reverse();self.matrix['pairs'].reverse();second=self.route()
        self.assertEqual(first,second)
        self.pois.reverse();self.matrix['pairs'].reverse()
        self.assertEqual(before,(self.profile,self.pois,self.matrix,self.context))
    def test_fixed_meal_moves_activity_whole(self):
        day=self.context['days'][0]['date']
        self.context['days'][0]['meal_break']={'kind':'meal','start_at':day+'T10:30:00+03:00','end_at':day+'T11:00:00+03:00'}
        result=self.explicit({day:[p['id'] for p in self.pois[:2]]})
        self.assertEqual(result['days'][0]['visits'][1]['start_at'],day+'T11:00:00+03:00')
        self.assertTrue(any(b['kind']=='meal' for b in result['days'][0]['breaks']))
        self.assertEqual(result['days'][0]['active_seconds'],130*60)
    def test_exact_active_cap_and_one_microsecond_below(self):
        day=self.context['days'][0]['date'];self.context['days'][0]['max_active_seconds']=3600
        self.assertEqual(self.explicit({day:[self.pois[0]['id']]})['constraint_checks']['time'],'passed')
        self.context['days'][0]['max_active_seconds']=3599.999999
        self.assertEqual(self.explicit({day:[self.pois[0]['id']]})['constraint_checks']['time'],'failed')
    def test_explicit_duplicate_rejected(self):
        day=self.context['days'][0]['date']
        with self.assertRaises(RoutingV02Error): self.explicit({day:[self.pois[0]['id']]*2})
    def test_directed_unknown_not_reverse_inherited(self):
        day=self.context['days'][0]['date'];a,b=self.pois[:2]
        self.matrix['pairs']=[p for p in self.matrix['pairs'] if (p['from_poi_id'],p['to_poi_id'])!=(b['id'],a['id'])]
        self.assertEqual(self.explicit({day:[a['id'],b['id']]})['constraint_checks']['time'],'passed')
        self.assertEqual(self.explicit({day:[b['id'],a['id']]})['constraint_checks']['time'],'unknown')
    def opening(self,poi_id,day,intervals):
        self.context['opening_intervals'].append({'poi_id':poi_id,'date':day,'timezone':'Europe/Moscow','intervals':intervals,'source_id':'synthetic_hours','source_url':'https://example.org/synthetic-hours','retrieved_at':'2026-11-17T00:00:00+00:00','verified_at':None})
    def test_opening_wait_and_closed_day(self):
        day=self.context['days'][0]['date'];poi_id=self.pois[0]['id']
        self.opening(poi_id,day,[{'start_at':day+'T10:00:00+03:00','end_at':day+'T12:00:00+03:00'}])
        result=self.explicit({day:[poi_id]})
        self.assertEqual(result['days'][0]['visits'][0]['start_at'],day+'T10:00:00+03:00')
        self.assertEqual(result['constraint_checks']['opening_hours'],'passed')
        self.context['opening_intervals'][0]['intervals']=[];self.profile['required_poi_ids']=[poi_id]
        self.assertEqual(self.route()['status'],'infeasible')
    def test_split_opening_does_not_allow_visit_across_closed_gap(self):
        day=self.context['days'][0]['date'];poi_id=self.pois[0]['id']
        self.opening(poi_id,day,[{'start_at':day+'T09:00:00+03:00','end_at':day+'T09:30:00+03:00'},{'start_at':day+'T11:00:00+03:00','end_at':day+'T12:30:00+03:00'}])
        self.assertEqual(self.explicit({day:[poi_id]})['days'][0]['visits'][0]['start_at'],day+'T11:00:00+03:00')
    def test_geometry_link_and_source_pass_through(self):
        day=self.context['days'][0]['date'];a,b=self.pois[:2]
        geometry={'type':'LineString','coordinates':[[37.6,55.7],[37.61,55.71]]};self.matrix['pairs'][0]['geometry']=geometry
        result=self.explicit({day:[a['id'],b['id']]});leg=result['days'][0]['legs'][0]
        self.assertEqual(leg['geometry'],geometry)
        self.assertEqual(leg['navigation_url'],'https://example.org/synthetic-navigation')
        self.assertEqual(leg['source_id'],'synthetic_multiday_matrix')
        self.assertEqual(result['status'],'unknown')
    def test_expired_matrix_yields_unknown(self):
        day=self.context['days'][0]['date'];self.matrix['valid_for']={'start_at':'2026-11-16T00:00:00+00:00','end_at':'2026-11-16T23:00:00+00:00'}
        result=self.explicit({day:[p['id'] for p in self.pois[:2]]})
        self.assertEqual(result['constraint_checks']['time'],'unknown')
        self.assertTrue(any(d['code']=='MATRIX_NOT_VALID_FOR_TIME' for d in result['diagnostics']))
    def test_budget_proven_required_cost_exceeds_trip_limit(self):
        self.profile['budget']={'amount':700,'currency':'RUB','scope':'trip_food_and_entertainment'}
        self.profile['required_poi_ids']=[p['id'] for p in self.pois[:2]]
        self.context['cost_context']={'currency':'RUB','coverage_complete':False,'visit_costs':[{'poi_id':p['id'],'amount':500,'basis':'test_fixture','source_url':'https://example.org/synthetic-cost','recorded_at':'2026-11-17T00:00:00+00:00'} for p in self.pois[:2]],'meal_costs':[]}
        result=self.route()
        self.assertEqual(result['status'],'infeasible')
        self.assertEqual(result['constraint_checks']['budget'],'failed')
    def test_unknown_budget_is_not_passed(self):
        self.profile['budget']={'amount':10000,'currency':'RUB','scope':'trip_food_and_entertainment'}
        self.assertEqual(self.route()['constraint_checks']['budget'],'unknown')
    def test_validator_rejects_timestamp_and_false_feasible(self):
        result=self.route();args=(self.profile,self.pois,recommend(self.profile,self.pois),self.matrix,self.context)
        self.assertTrue(validate_route(result,*args)['valid'])
        result['days'][0]['visits'][0]['end_at']='2026-11-17T09:00:00+03:00'
        self.assertFalse(validate_route(result,*args)['valid'])
        result=self.route();result['status']='feasible'
        self.assertFalse(validate_route(result,*args)['valid'])
    def test_snapshot_mismatch_and_mode_mismatch_rejected(self):
        recommendation=recommend(self.profile,self.pois);recommendation['items'][0]['rank']=99
        with self.assertRaises(RoutingV02Error): build_route(self.profile,self.pois,recommendation,self.matrix,self.context)
        self.matrix['mode']='car'
        with self.assertRaises(RoutingV02Error): self.route()
    def test_day_pace_override_changes_only_target_day(self):
        self.profile,self.pois,self.matrix,self.context=fixture(count=2,days=2);target=self.context['days'][1]['date']
        self.context['day_pace_overrides']={target:'relaxed'};result=self.route()
        self.assertEqual([day['pace'] for day in result['days']],['balanced','relaxed'])
        self.assertEqual(result['days'][1]['rest_after_visit_seconds'],1200)
    def test_validator_rejects_modified_budget_and_removed_warnings(self):
        result=self.route();args=(self.profile,self.pois,recommend(self.profile,self.pois),self.matrix,self.context)
        result['budget_summary']['status']='passed'
        self.assertFalse(validate_route(result,*args)['valid'])
        result=self.route();result['warnings']=[]
        self.assertFalse(validate_route(result,*args)['valid'])
    def test_elapsed_includes_fixed_break_after_last_visit(self):
        day=self.context['days'][0]['date']
        self.context['days'][0]['meal_break']={'kind':'meal','start_at':day+'T13:00:00+03:00','end_at':day+'T13:45:00+03:00'}
        result=self.explicit({day:[self.pois[0]['id']]})['days'][0]
        self.assertEqual(result['visit_span_seconds'],3600)
        self.assertEqual(result['elapsed_seconds'],17100)
    def test_validator_rejects_unknown_model(self):
        result=self.route();result['model_version']='invented'
        with self.assertRaises(RoutingV02Error): validate_route(result,self.profile,self.pois,recommend(self.profile,self.pois),self.matrix,self.context)

    def test_validator_rejects_budget_passed_when_no_budget_was_checked(self):
        result=self.route()
        self.assertEqual(result['constraint_checks']['budget'],'not_checked')
        result['constraint_checks']['budget']='passed'
        self.assertFalse(validate_route(result,self.profile,self.pois,recommend(self.profile,self.pois),self.matrix,self.context)['valid'])
    def test_validator_rejects_missing_or_invalid_output_status_and_checks(self):
        for mutation in ['bad_status','missing_status','missing_checks','bad_check','unchecked_time','false_infeasible']:
            with self.subTest(mutation=mutation):
                result=self.route()
                if mutation=='bad_status': result['status']='garbage'
                elif mutation=='missing_status': result.pop('status')
                elif mutation=='missing_checks': result['constraint_checks']={}
                elif mutation=='bad_check': result['constraint_checks']['time']='invented'
                elif mutation=='false_infeasible': result['status']='infeasible'
                else: result['constraint_checks']['time']='not_checked'
                self.assertFalse(validate_route(result,self.profile,self.pois,recommend(self.profile,self.pois),self.matrix,self.context)['valid'])

if __name__=='__main__':
    unittest.main()
