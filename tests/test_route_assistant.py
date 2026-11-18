"""Искусственные сценарии Assistant; провайдер HTTP только mock в памяти."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import unittest
from ai_ds.recommendation import recommend
from ai_ds.route_assistant import AssistantError, InMemoryRouteStore, RouteAssistant, parse_interpretation, sequences_of
DAY='2026-11-17';A='synthetic:museums-01';B='synthetic:history-02';C='synthetic:nature-03'
NOW=datetime(2026,11,18,9,tzinfo=timezone.utc)
ROOT=Path(__file__).resolve().parents[1]

def inputs():
    read=lambda p:json.loads((ROOT/p).read_text(encoding='utf-8'))
    profile=read('examples/preferences/history.json');profile['required_poi_ids'],profile['top_k']=[A],1
    pois=[p for p in read('data/processed/pois.synthetic.validated.json') if p['id'] in (A,B,C)]
    context={'schema_version':'0.2','endpoint_policy':'first_last_poi','pace_policy_version':'pace-policy-v0.2','unknown_duration_policy':'strict','poi_snapshot_id':'synthetic-v1','days':[{'date':DAY,'day_start':DAY+'T09:00:00+03:00','day_end':DAY+'T18:00:00+03:00','meal_break':None,'blocked_intervals':[],'max_active_seconds':None}],'day_pace_overrides':{},'duration_overrides':[],'opening_intervals':[],'cost_context':None}
    matrix={'mode':'walking','basis':'test_matrix','source_id':'test_fixture','retrieved_at':None,'valid_for':None,'pairs':[{'from_poi_id':a,'to_poi_id':b,'status':'known','duration_seconds':600,'distance_meters':700,'geometry':None,'navigation_url':None} for a in (A,B,C) for b in (A,B,C) if a!=b]}
    return dict(preferences=profile,pois=pois,recommendation=recommend(profile,pois),travel_times=matrix,routing_context=context,poi_snapshot_id='synthetic-v1')

def fake_schedule(state,sequences):
    placed=[p for ids in sequences.values() for p in ids]
    if len(placed)!=len(set(placed)) or not set(state['preferences']['required_poi_ids'])<=set(placed): raise ValueError('Invalid required or duplicate')
    result={'schema_version':'0.2','model_version':'synthetic_test_scheduler','status':'unknown','constraint_checks':{'time':'passed','required_places':'passed','opening_hours':'unknown','budget':'not_checked'},'warnings':['SYNTHETIC_DATA'],'days':[]}
    for day,ids in sequences.items():
        start=datetime.fromisoformat(day+'T09:00:00+03:00');visits=[]
        for index,poi_id in enumerate(ids):
            at=start+timedelta(minutes=40*index)
            visits.append({'poi_id':poi_id,'start_at':at.isoformat(),'end_at':(at+timedelta(minutes=30)).isoformat()})
        pace=state['routing_context'].get('day_pace_overrides',{}).get(day,state['preferences']['pace'])
        result['days'].append({'date':day,'visits':visits,'legs':[],'effective_pace':pace,'meal_break':next(d for d in state['routing_context']['days'] if d['date']==day).get('meal_break')})
    return result

def fake_validate(route,state):
    canonical=deepcopy(route);canonical.pop('route_id',None);canonical.pop('version',None)
    if canonical!=fake_schedule(state,sequences_of(route)): raise ValueError('Tampered schedule')
    return deepcopy(route['constraint_checks'])

def decision(*ops): return {'action':'propose','operations':list(ops),'clarification':None}
def pace_op(): return {'op':'set_day_pace','day':DAY,'pace':'relaxed'}

class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.data=inputs();state=deepcopy(self.data);state['route']=fake_schedule(state,{DAY:[A,B]})
        self.store=InMemoryRouteStore();self.store.register_route('alice','trip',route=state['route'],**self.data)
        self.now=NOW;self.output=decision(pace_op());self.payloads=[]
        def interpreter(payload):
            self.payloads.append(deepcopy(payload));return deepcopy(self.output)
        self.assistant=RouteAssistant(self.store,interpreter,proposal_ttl_seconds=60,clock=lambda:self.now,scheduler=fake_schedule,validator=fake_validate)
        self.request={'schema_version':'0.2','route_id':'trip','base_version':1,'poi_snapshot_id':'synthetic-v1','text':'Сделай день спокойнее'}
    def propose(self): return self.assistant.propose('alice',self.request)
    def confirmation(self,proposal,decision='confirm'): return {'proposal_id':proposal['proposal_id'],'base_version':1,'decision':decision}
    def test_preview_does_not_mutate_server_and_minimizes_payload(self):
        before=self.store.get_route('alice','trip');preview=self.propose()
        self.assertEqual(self.store.get_route('alice','trip'),before)
        self.assertEqual(preview['validation_status'],'ready_for_confirmation')
        self.assertTrue(preview['requires_draft_confirmation'])
        self.assertEqual(set(self.payloads[0]),{'text','days','catalog','required_poi_ids','timezone'})
        self.assertNotIn('provenance',json.dumps(self.payloads[0]));self.assertEqual(preview['diff']['changed_days'],[DAY])
    def test_confirm_requires_explicit_draft_and_is_idempotent(self):
        preview=self.propose();request=self.confirmation(preview)
        with self.assertRaisesRegex(AssistantError,'DRAFT_CONFIRMATION_REQUIRED'): self.assistant.decide('alice',request)
        saved=self.assistant.decide('alice',request,allow_draft=True)
        self.assertEqual(saved['version'],2);self.assertTrue(saved['is_draft'])
        self.assertEqual(self.assistant.decide('alice',request),saved)
        self.assertEqual(self.store.get_route('alice','trip')['preferences'],self.data['preferences'])
        self.assertEqual(self.store.get_route('alice','trip')['routing_context']['day_pace_overrides'],{DAY:'relaxed'})
    def test_actor_checked_on_initial_and_repeated_confirmation(self):
        with self.assertRaisesRegex(AssistantError,'ROUTE_NOT_FOUND'): self.assistant.propose('mallory',self.request)
        preview=self.propose();request=self.confirmation(preview);self.assistant.decide('alice',request,allow_draft=True)
        with self.assertRaisesRegex(AssistantError,'PROPOSAL_NOT_FOUND'): self.assistant.decide('mallory',request,allow_draft=True)
    def test_reject_preserves_original_and_cannot_then_confirm(self):
        before=self.store.get_route('alice','trip');preview=self.propose();request=self.confirmation(preview,'reject')
        result=self.assistant.decide('alice',request)
        self.assertEqual(result['decision'],'rejected');self.assertEqual(self.assistant.decide('alice',request),result)
        self.assertEqual(self.store.get_route('alice','trip'),before)
        with self.assertRaisesRegex(AssistantError,'PROPOSAL_ALREADY_DECIDED'): self.assistant.decide('alice',self.confirmation(preview),allow_draft=True)
    def test_required_remove_or_replace_is_atomic_failure(self):
        before=self.store.get_route('alice','trip')
        for bad in [{'op':'remove_poi','poi_id':A},{'op':'replace_poi','day':DAY,'target_poi_id':A,'desired_category':'nature'}]:
            self.output=decision(pace_op(),bad)
            with self.assertRaisesRegex(AssistantError,'REQUIRED_PLACE_PROTECTED'): self.propose()
            self.assertEqual(self.store.get_route('alice','trip'),before)
    def test_add_and_replace_outside_original_top_k(self):
        self.assertNotIn(C,[i['poi_id'] for i in self.data['recommendation']['items']])
        self.output=decision({'op':'add_poi','day':None,'poi_id':C});proposal=self.propose();self.assertIn(C,proposal['diff']['after'][DAY])
        self.output=decision({'op':'replace_poi','day':DAY,'target_poi_id':B,'desired_category':'nature'});proposal=self.propose()
        self.assertNotIn(B,proposal['diff']['after'][DAY]);self.assertIn(A,proposal['diff']['after'][DAY]);self.assertIn(C,proposal['diff']['after'][DAY])
    def test_unknown_ids_duplicate_and_day_are_rejected(self):
        for op,code in [({'op':'add_poi','day':DAY,'poi_id':'invented'},'UNKNOWN_POI_ID'),({'op':'add_poi','day':DAY,'poi_id':A},'DUPLICATE_VISIT'),({'op':'set_day_pace','day':'2026-11-19','pace':'relaxed'},'UNKNOWN_DAY')]:
            self.output=decision(op)
            with self.assertRaisesRegex(AssistantError,code): self.propose()
        self.assertEqual(self.store.get_route('alice','trip')['version'],1)
    def test_unknown_prices_clarify_without_provider(self):
        self.request['text']='Убери дорогие развлечения';result=self.propose()
        self.assertEqual(result['code'],'UNKNOWN_COST');self.assertEqual(self.payloads,[])
    def test_interpreter_clarification_for_ambiguous_day(self):
        self.output={'action':'needs_clarification','operations':[],'clarification':'Какой день изменить?'}
        self.assertEqual(self.propose()['validation_status'],'needs_clarification')
        self.assertEqual(self.store.get_route('alice','trip')['version'],1)
    def test_snapshot_and_version_strict_types(self):
        for version in (True,False,'1',1.0,None):
            self.request['base_version']=version
            with self.assertRaisesRegex(AssistantError,'INVALID_VERSION'): self.propose()
        self.request['base_version']=1;self.request['poi_snapshot_id']='client-invented-snapshot'
        with self.assertRaisesRegex(AssistantError,'SNAPSHOT_CONFLICT'): self.propose()
    def test_draft_flag_and_confirmation_version_strict_types(self):
        proposal=self.propose();request=self.confirmation(proposal)
        for flag in ('false',1,[],None):
            with self.assertRaisesRegex(AssistantError,'INVALID_DRAFT_CONFIRMATION'): self.assistant.decide('alice',request,allow_draft=flag)
        request['base_version']=True
        with self.assertRaisesRegex(AssistantError,'INVALID_CONFIRMATION'): self.assistant.decide('alice',request,allow_draft=True)
    def test_fingerprint_rejects_refreshed_matrix_same_route_version(self):
        proposal=self.propose();matrix=deepcopy(self.data['travel_times']);matrix['pairs'][0]['duration_seconds']+=1
        self.store.refresh_data('alice','trip',travel_times=matrix)
        with self.assertRaisesRegex(AssistantError,'INPUT_DATA_CHANGED'): self.assistant.decide('alice',self.confirmation(proposal),allow_draft=True)
        self.assertEqual(self.store.get_route('alice','trip')['version'],1)
    def test_two_previews_stale_second_confirmation(self):
        first,second=self.propose(),self.propose();self.assistant.decide('alice',self.confirmation(first),allow_draft=True)
        with self.assertRaisesRegex(AssistantError,'VERSION_CONFLICT'): self.assistant.decide('alice',self.confirmation(second),allow_draft=True)
    def test_expiry_and_explicit_ttl(self):
        proposal=self.propose();self.now+=timedelta(seconds=60)
        with self.assertRaisesRegex(AssistantError,'PROPOSAL_EXPIRED'): self.assistant.decide('alice',self.confirmation(proposal),allow_draft=True)
        for ttl in (True,0,-1,1.0):
            with self.assertRaisesRegex(AssistantError,'INVALID_PROPOSAL_TTL'): RouteAssistant(self.store,lambda p:None,proposal_ttl_seconds=ttl)
    def test_caller_tampering_does_not_change_saved_preview(self):
        proposal=self.propose();proposal['candidate_route']['days'][0]['visits'][0]['end_at']='invented'
        result=self.assistant.decide('alice',self.confirmation(proposal),allow_draft=True)
        self.assertNotEqual(result['route']['days'][0]['visits'][0]['end_at'],'invented')
    def test_confirm_reruns_validator(self):
        proposal=self.propose();self.assistant.validator=lambda route,state:{'valid':False,'errors':[{'code':'SCHEDULE_MISMATCH'}],'constraint_checks':{}}
        with self.assertRaises(AssistantError): self.assistant.decide('alice',self.confirmation(proposal),allow_draft=True)
        self.assertEqual(self.store.get_route('alice','trip')['version'],1)
    def test_provider_exception_is_redacted(self):
        def broken(payload): raise RuntimeError('https://secret.example/?key=credential')
        self.assistant.interpreter=broken
        with self.assertRaises(AssistantError) as error: self.propose()
        self.assertEqual(str(error.exception),'INTERPRETER_UNAVAILABLE');self.assertNotIn('credential',str(error.exception))
    def test_extra_request_fields_and_unsafe_model_ops(self):
        self.request['owner']='mallory'
        with self.assertRaisesRegex(AssistantError,'INVALID_FIELDS'): self.propose()
        self.request.pop('owner');self.output=decision({'op':'exec','code':'print(1)'})
        with self.assertRaisesRegex(AssistantError,'UNSUPPORTED_OPERATION'): self.propose()
    def test_strict_interpretation_fields_duplicates_nonfinite(self):
        bad=[decision(dict(pace_op(),hidden='execute')),{'action':[],'operations':[],'clarification':None},{'action':'propose','operations':[],'clarification':None},'{"action":"propose","action":"unsupported","operations":[],"clarification":"x"}','{"action":"propose","operations":[NaN],"clarification":null}']
        for value in bad:
            with self.subTest(value=value),self.assertRaises(AssistantError): parse_interpretation(value)

class AssistantIntegrationTests(unittest.TestCase):
    def make_assistant(self,output,*,interpreter=None):
        from ai_ds.multiday import schedule_sequences
        data=inputs();route=schedule_sequences(data['preferences'],data['pois'],data['recommendation'],data['travel_times'],data['routing_context'],{DAY:[A,B]})
        self.assertEqual(sequences_of(route),{DAY:[A,B]})
        store=InMemoryRouteStore();store.register_route('alice','trip',route=route,**data)
        assistant=RouteAssistant(store,interpreter or (lambda p:deepcopy(output)),proposal_ttl_seconds=120,clock=lambda:NOW)
        request={'schema_version':'0.2','route_id':'trip','base_version':1,'poi_snapshot_id':'synthetic-v1','text':'Сделай день спокойнее'}
        return store,assistant,request
    def test_real_scheduler_pace_and_confirm(self):
        store,assistant,request=self.make_assistant(decision(pace_op()));before=store.get_route('alice','trip')
        proposal=assistant.propose('alice',request);self.assertEqual(store.get_route('alice','trip'),before)
        self.assertEqual(proposal['candidate_route']['status'],'unknown')
        saved=assistant.decide('alice',{'proposal_id':proposal['proposal_id'],'base_version':1,'decision':'confirm'},allow_draft=True)
        self.assertEqual(saved['version'],2);self.assertEqual(store.get_route('alice','trip')['preferences'],before['preferences'])
        self.assertEqual(store.get_route('alice','trip')['routing_context']['day_pace_overrides'],{DAY:'relaxed'})
    def test_real_scheduler_adds_outside_original_top_k(self):
        store,assistant,request=self.make_assistant(decision({'op':'add_poi','day':DAY,'poi_id':C}));proposal=assistant.propose('alice',request)
        self.assertIn(C,sequences_of(proposal['candidate_route'])[DAY]);self.assertEqual(len(sequences_of(proposal['candidate_route'])[DAY]),3)
        self.assertEqual(store.get_route('alice','trip')['version'],1)
    def test_real_scheduler_replacement_and_meal(self):
        store,assistant,request=self.make_assistant(decision({'op':'replace_poi','day':DAY,'target_poi_id':B,'desired_category':'nature'},{'op':'set_meal_break','day':DAY,'start_at':DAY+'T13:00:00+03:00','end_at':DAY+'T13:45:00+03:00'}))
        proposal=assistant.propose('alice',request);ids=sequences_of(proposal['candidate_route'])[DAY]
        self.assertIn(A,ids);self.assertIn(C,ids);self.assertNotIn(B,ids)
        self.assertIsNone(store.get_route('alice','trip')['routing_context']['days'][0]['meal_break'])
    def test_invalid_meal_atomic_with_real_validator(self):
        store,assistant,request=self.make_assistant(decision(pace_op(),{'op':'set_meal_break','day':DAY,'start_at':DAY+'T08:00:00+03:00','end_at':DAY+'T08:45:00+03:00'}));before=store.get_route('alice','trip')
        with self.assertRaises(AssistantError): assistant.propose('alice',request)
        self.assertEqual(store.get_route('alice','trip'),before)
    def test_integrated_openrouter_mock_opener_no_network(self):
        from ai_ds.openrouter import OpenRouterInterpreter
        calls=[]
        def opener(request,timeout):
            body=json.loads(request.data.decode('utf-8'));payload=json.loads(body['messages'][1]['content']);calls.append(payload)
            self.assertEqual(set(payload),{'text','timezone','days','catalog','required_poi_ids'});self.assertEqual(payload['required_poi_ids'],[A])
            response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(decision(pace_op()),ensure_ascii=False)}}],'model':'synthetic-free-model'}
            return io.BytesIO(json.dumps(response,ensure_ascii=False).encode('utf-8'))
        interpreter=OpenRouterInterpreter(key='mock-secret',opener=opener)
        store,assistant,request=self.make_assistant(None,interpreter=interpreter);proposal=assistant.propose('alice',request)
        self.assertEqual(proposal['operations'],[pace_op()]);self.assertEqual(len(calls),1)
        self.assertNotIn('mock-secret',json.dumps(proposal,ensure_ascii=False));self.assertEqual(store.get_route('alice','trip')['version'],1)

if __name__=='__main__': unittest.main()
