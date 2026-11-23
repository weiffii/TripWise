"""RouteAssistant preview/confirm smoke with a real Node scheduler and synthetic POIs.

No claim about live Moscow V7/OSM topology: fixture prices and routes are synthetic.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from ai_ds.moscow_engine_adapter import fixed_sequence, timestamp
from ai_ds.moscow_gap_repair import MoscowGapIndex, describe_edited_gaps, route_gaps
from ai_ds.route_assistant import InMemoryRouteStore, sequences_of
from ai_ds.shanghai_assistant import node_executable
from backend.moscow import MoscowAssistant, MoscowRouteAssistant, moscow_interpretation

DAY='2026-11-21'


def poi(key, longitude, *, cafe=False):
    planning={'duration_minutes':{'value':60 if not cafe else 50},'price_rub':{'value':0 if not cafe else 100},
      'opening_hours':{'source_type':'synthetic','value':[['09:00','21:00']]*7}}
    if cafe: planning['average_check_rub']={'value':100}
    return {'id':key,'name':key,'categories':['cafes' if cafe else 'history'],
      'coordinates':{'latitude':55.75,'longitude':longitude},'planning':planning,
      'provenance':{'is_synthetic':False}}


@unittest.skipUnless(node_executable(), 'requires existing Node scheduler')
class AssistantFillIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.lunch=poi('lunch',37.620,cafe=True)
        self.closing=poi('closing',37.630)
        self.additions=[poi(f'extra-{i}',37.620+i*.001) for i in range(1,8)]
        self.points={p['id']:p for p in [self.lunch,self.closing,*self.additions]}
        self.profile={'schema_version':'0.1','destination':{'city':'Москва','country_code':'RU'},
          'date_start':DAY,'date_end':DAY,'timezone':'Europe/Moscow','primary_interests':['history'],
          'secondary_interests':[],'required_poi_ids':['closing'],'transport':'walking',
          'pace':'balanced','budget':{'amount':2000,'currency':'RUB'}}
        self.group={'adult':1,'child':0,'senior':0,'pet':0}
        self.original_context={'days':[{'date':DAY,'day_start':timestamp(DAY,750),'day_end':timestamp(DAY,1215)}],
          'moscow_group':self.group,'poi_snapshot_id':'test'}
        self.meal={'poi_id':'lunch','start_at':timestamp(DAY,760),'end_at':timestamp(DAY,810)}
        self.route={'schema_version':'0.2','status':'unknown','poi_snapshot_id':'test',
          'budget_summary':{'known_total':100},'unplaced_required_poi_ids':[],
          'moscow_meals':{DAY:{'lunch':self.meal}},
          'days':[{'date':DAY,'pace':'balanced','visits':[self.meal,
             {'poi_id':'closing','start_at':timestamp(DAY,1159),'end_at':timestamp(DAY,1189)}], 'breaks':[]}],
          'engine_days':[{'date':DAY,'events':[
             {'type':'meal','meal_kind':'lunch','poi_id':'lunch','start_min':760,'end_min':810},
             {'type':'travel','start_min':1157,'end_min':1159},
             {'type':'visit','poi_id':'closing','start_min':1159,'end_min':1189}]}]}
        self.index=MoscowGapIndex(self.points.values())

    def source(self, ids):
        ids=list(dict.fromkeys(['closing',*ids]))
        context=deepcopy(self.original_context)
        context.update(duration_overrides=[{'poi_id':key,'minutes':self.points[key]['planning']['duration_minutes']['value']} for key in ids],
                       cost_context={'visit_costs':[{'poi_id':key,'amount':self.points[key]['planning']['price_rub']['value']} for key in ids]},
                       opening_intervals=[])
        matrix={'pairs':[{'from_poi_id':a,'to_poi_id':b,'status':'known','duration_seconds':120,
                          'distance_meters':90,'source_id':'fixture','basis':'routing_provider'}
                         for a in ids for b in ids if a!=b]}
        return self.profile,[self.points[key] for key in ids],{},matrix,context

    def test_request_fills_347_minute_gap_then_requires_confirm(self):
        src=self.source(['lunch','closing'])
        store=InMemoryRouteStore()
        store.register_route('demo','r1',preferences=src[0],pois=src[1],recommendation=src[2],
           travel_times=src[3],routing_context=src[4],poi_snapshot_id='test',route=self.route)
        decision={'action':'propose','operations':[{'op':'fill_gaps','days':[DAY]}], 'clarification':None}
        assistant=MoscowRouteAssistant(store,lambda payload:decision,moscow_preferences=object(),
           proposal_ttl_seconds=300,interpretation_parser=moscow_interpretation,allow_cost_edits=True)
        original=store.get_route('demo','r1')
        with patch('backend.moscow.moscow_inputs',side_effect=lambda prefs,*,explicit_pois,pair_caches:self.source(explicit_pois)), \
             patch('backend.moscow.gap_index',return_value=self.index):
            proposed=assistant.propose('demo',{'schema_version':'0.2','route_id':'r1',
                'base_version':1,'poi_snapshot_id':'test','text':'убери длинные перерывы'})
            self.assertEqual(proposed['validation_status'],'ready_for_confirmation')
            self.assertTrue(proposed['diff']['changed_days'])
            self.assertGreater(len(sequences_of(proposed['candidate_route'])[DAY]),2)
            self.assertEqual(store.get_route('demo','r1')['version'],1)
            self.assertEqual(store.get_route('demo','r1')['route'],original['route'])
            confirmed=assistant.decide('demo',{'proposal_id':proposed['proposal_id'],
                'base_version':1,'decision':'confirm'},allow_draft=True)
            self.assertEqual(confirmed['version'],2)
            self.assertEqual(sequences_of(confirmed['route']),sequences_of(proposed['candidate_route']))
            self.assertEqual(confirmed['route']['native_checks']['time'],'passed')
            self.assertEqual(confirmed['route']['native_checks']['budget'],'passed')
            self.assertEqual(confirmed['route']['unplaced_required_poi_ids'],[])

    def test_time_only_gap_repair_can_be_previewed_and_confirmed(self):
        self.original_context['days'][0]['day_start'] = timestamp(DAY, 900)
        key = self.additions[0]['id']
        order = {DAY: [key, 'closing']}
        profile, points, ranking, matrix, context = self.source(order[DAY])
        context['opening_intervals'] = [{'poi_id': 'closing', 'date': DAY, 'intervals': [
            {'start_at': timestamp(DAY, 1110), 'end_at': timestamp(DAY, 1215)}]}]
        base = fixed_sequence({'preferences': profile, 'pois': points, 'travel_times': matrix,
                               'routing_context': context, 'defer_visit_extensions': True,
                               'route': {'days': [{'date': DAY, 'pace': 'balanced', 'visits': [], 'breaks': []}]}}, order)
        self.assertTrue(any(gap.minutes > 60 and gap.kind == 'internal' for gap in route_gaps(base, context)))
        service = MoscowAssistant.__new__(MoscowAssistant)
        service.configured, service.snapshot = True, 'test'
        session = {'moscow_route': deepcopy(base), 'moscow_route_id': 'r1', 'moscow_version': 1,
                   'moscow_history': [], 'moscow_preferences': object(),
                   'route_options': {'transport': 'walk', 'conversation': []}}
        unchanged = {**base, 'gap_diagnostics': describe_edited_gaps(base, context, base)}
        with patch('backend.moscow.indexes', return_value=(self.points, {}, {})), \
             patch('backend.moscow.moscow_inputs', return_value=(profile, points, ranking, matrix, context)), \
             patch('backend.moscow.repair_gaps', return_value=unchanged):
            preview = service._request('actor', session, 'message', {
                'text': 'Убери длинные перерывы', 'route_id': 'r1', 'base_version': 1, 'poi_snapshot_id': 'test'})
            self.assertEqual(preview['type'], 'proposal', preview)
            self.assertEqual(preview['diff']['changed_days'], [DAY])
            self.assertEqual(sequences_of(preview['plan']), order)
            self.assertEqual(preview['plan']['days'][0]['visits'][0]['extension_minutes'], 60)
            self.assertEqual(session['moscow_route'], base)
            confirmed = service._request('actor', session, 'decide', {
                'proposal_id': preview['proposal_id'], 'base_version': 1, 'decision': 'confirm'})
            self.assertEqual(confirmed['type'], 'confirmed')
            self.assertEqual(confirmed['plan']['days'][0]['visits'][0]['extension_minutes'], 60)

if __name__=='__main__':unittest.main()
