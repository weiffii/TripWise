"""Offline integration checks for the Moscow-specific model without huge V7 dataset."""
import unittest
from datetime import date

from ai_ds.moscow_fares import estimate_fare
from ai_ds.moscow_engine_adapter import engine_input, saved_plan, timestamp
from backend.shanghai import MoscowPreferences

DAY = '2026-11-21'
GROUP = {'adult': 2, 'child': 1, 'senior': 0, 'pet': 0}


def prefs(**overrides):
    data = dict(city='moscow', timezone='Europe/Moscow', start_date=DAY, days=3,
                day_start='09:00', day_end='20:00', pace='balanced', transport='walk',
                transport_modes=['walk'], group=dict(adult=1, child=0, senior=0, pet=0),
                primary_interests=['museums'], secondary_interests=['cafes'], required_ids=[],
                budget_amount=5000., budget_currency='RUB', budget_basis='per_group',
                day_settings={DAY: {'day_start': '14:10', 'day_end': '20:00'},
                              '2026-11-22': {'day_start': '09:00', 'day_end': '20:00'},
                              '2026-11-23': {'day_start': '09:00', 'day_end': '19:00'}})
    data.update(overrides)
    return MoscowPreferences.model_validate(data)


class WindowsAndFares(unittest.TestCase):
    def test_api_keeps_distinct_day_windows(self):
        item = prefs()
        self.assertEqual(item.day_settings[date(2026, 11, 22)]['day_start'], '09:00')
        self.assertEqual(item.options()['day_settings']['2026-11-22']['day_start'], '09:00')

    def test_api_rejects_invalid_day_windows(self):
        with self.assertRaises(ValueError):
            prefs(day_settings={'2026-11-22': {'day_start': '19:30', 'day_end': '20:00'}})
        with self.assertRaises(ValueError):
            prefs(day_settings={'2026-12-21': {'day_start': '09:00', 'day_end': '20:00'}})

    def test_old_preference_payload_remains_compatible(self):
        self.assertIsNone(prefs(day_settings=None).day_settings)

    def test_moscow_fares_do_not_treat_missing_cost_as_zero(self):
        self.assertIsNone(estimate_fare('taxi', distance_meters=None, driving_minutes=12, group=GROUP)['rub'])
        self.assertEqual(estimate_fare('walk', distance_meters=1000, driving_minutes=10, group=GROUP)['rub'], 0)
        transit = estimate_fare('transit', distance_meters=None, driving_minutes=12, group=GROUP)
        self.assertEqual(transit['rub'], 2 * 75 + 38)

    def test_taxi_time_and_group_capacity(self):
        solo = estimate_fare('taxi', distance_meters=5000, driving_minutes=15,
                             group={'adult':1,'child':0,'senior':0,'pet':0})
        family = estimate_fare('taxi', distance_meters=5000, driving_minutes=15,
                               group={'adult':5,'child':0,'senior':0,'pet':0})
        self.assertGreater(solo['rub'], 400)
        self.assertEqual(family['rub'], solo['rub'] * 2)
        self.assertEqual(solo['additional_minutes'], 8)

    def test_adapter_passes_paid_fares_to_actual_optimizer(self):
        pois = [dict(id=k, name=k, coordinates={'latitude':55.75,'longitude':lon},
                    categories=['museums' if k=='a' else 'cafes'],
                    planning={'food_type':{'value':'cafe'},'meal_suitability':{'value':{'lunch':'likely'}}},
                    catalog_signals={}) for k,lon in [('a',37.62),('b',37.68)]]
        group = GROUP
        context = {'days':[{'date':DAY,'day_start':timestamp(DAY,540),'day_end':timestamp(DAY,1200)}],
                   'duration_overrides':[{'poi_id':i['id'],'minutes':50} for i in pois],
                   'cost_context':{'visit_costs':[{'poi_id':'a','amount':300,'basis':'expert_estimate'},
                                                   {'poi_id':'b','amount':900,'basis':'expert_estimate'}]},
                   'opening_intervals':[], 'moscow_group':group,
                   'moscow_transport_modes':['transit','taxi']}
        pair = lambda mode:{'from_poi_id':'a','to_poi_id':'b','status':'known',
                            'distance_meters':5000,'duration_seconds':1200,
                            'source_id':'fixture','basis':'routing_provider',
                            **({'transport_steps':[{'kind':'rail_estimate','line':'metro:1'}]} if mode=='transit' else {})}
        matrix={'profiles':{mode:{'pairs':[pair(mode)]} for mode in ['walk','transit','taxi']}}
        profile={'transport':'public_transit','pace':'balanced','date_start':DAY,
                 'primary_interests':['museums'],'secondary_interests':['cafes'],
                 'required_poi_ids':[],'budget':{'amount':6000}}
        data, options, rows = engine_input(profile,pois,matrix,context)
        self.assertEqual(options['transport_modes'],['transit','taxi'])
        self.assertGreater(data['leg_profiles']['transit']['a']['b']['transport_cost_cny'],0)
        self.assertGreater(data['leg_profiles']['taxi']['a']['b']['transport_cost_cny'],
                           data['leg_profiles']['transit']['a']['b']['transport_cost_cny'])
        self.assertGreater(data['leg_profiles']['taxi']['a']['b']['planned_minutes'],20)
        self.assertEqual(rows['a','b','transit']['transport_cost_basis'],'synthetic_fare_estimate')

    def test_saved_plan_has_separate_breakdown_without_double_meal(self):
        ctx = {'days':[{'date':DAY,'day_start':timestamp(DAY,540),'day_end':timestamp(DAY,1200)}],
               'poi_snapshot_id':'fixture',
               'cost_context':{'visit_costs':[{'poi_id':'food','basis':'expert_estimate'},
                                                 {'poi_id':'museum','basis':'source'}]},
               'moscow_group':{'adult':1,'child':0,'senior':0,'pet':0}}
        rows={('food','museum','transit'):{'status':'known','mode':'transit','basis':'geometric_estimate',
                                          'transport_cost_rub':75,'transport_cost_basis':'synthetic_fare_estimate'}}
        native={'days':[{'date':DAY,'events':[
            {'type':'meal','poi_id':'food','meal_kind':'lunch','start_min':720,'end_min':770,'price_cny':250},
            {'type':'travel','from_poi_id':'food','to_poi_id':'museum','leg_profile':'transit',
             'mode':'transit','start_min':770,'end_min':800,'planned_minutes':30},
            {'type':'visit','poi_id':'museum','start_min':800,'end_min':850,'price_cny':100},
        ]}], 'model_version':'fixture','status':'demo','summary':{'total_cost_cny':425,'budget_cny':2000},
                'checks':{'time':'passed','required_places':'passed'}}
        result=saved_plan(native,ctx,rows,'balanced')
        b=result['days'][0]['cost_breakdown']
        self.assertEqual((b['visits_rub'],b['meals_rub'],b['transport_rub'],b['total_rub']), (100,250,75,425))
        self.assertEqual(result['budget_summary']['remaining'],1575)
        self.assertEqual(result['budget_summary']['status'],'estimated')
        self.assertNotIn('meal', [b['kind'] for b in result['days'][0]['breaks']])

    def test_actual_python_to_node_bridge_uses_shared_optimizer(self):
        from ai_ds.moscow_engine_adapter import plan_city
        points = [dict(id=name, name=name, categories=['museums' if name != 'food' else 'cafes'],
                       coordinates={'latitude':55.75,'longitude':lon},
                       planning={'food_type':{'value':'canteen'} if name=='food' else {},
                                 'meal_suitability':{'value':{'breakfast':'possible','lunch':'likely','dinner':'likely'}} if name=='food' else {}},
                       catalog_signals={}) for name,lon in [('a',37.62),('b',37.621),('food',37.622)]]
        context={'days':[{'date':DAY,'day_start':timestamp(DAY,540),'day_end':timestamp(DAY,1200)}],
                 'duration_overrides':[{'poi_id':p['id'],'minutes':60} for p in points],
                 'cost_context':{'visit_costs':[{'poi_id':p['id'],'amount':400 if p['id']=='food' else 200,
                                                'basis':'expert_estimate'} for p in points]},
                 'moscow_group':{'adult':1,'child':0,'senior':0,'pet':0},
                 'opening_intervals':[],'poi_snapshot_id':'mock-snapshot'}
        matrix={'pairs':[{'from_poi_id':a['id'],'to_poi_id':b['id'],'status':'known',
                          'duration_seconds':180,'distance_meters':300,'source_id':'test',
                          'basis':'routing_provider'} for a in points for b in points if a!=b]}
        profile={'date_start':DAY,'date_end':DAY,'pace':'balanced','transport':'walking',
                 'primary_interests':['museums'],'secondary_interests':['cafes'],
                 'required_poi_ids':['a'],'budget':{'amount':2000}}
        result=plan_city(profile,points,matrix,context)
        self.assertEqual(result['engine'],'shared-js')
        self.assertEqual(result['constraint_checks']['required_places'],'passed')
        self.assertEqual(result['days'][0]['cost_breakdown']['total_rub'],result['budget_summary']['estimated_total'])

if __name__ == '__main__': unittest.main()
