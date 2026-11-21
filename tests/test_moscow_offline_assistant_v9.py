"""Regression for robust offline Moscow chat requests and safe edit planning.

Fixtures use only existing catalog identifiers. Integration executes the real
Node schedule; tests do not imply live OSM or the full Moscow V7 was checked.
"""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from ai_ds.moscow_local_commands import local_command_decision
from ai_ds.moscow_named_poi import named_poi_decision, resolve_name
from ai_ds.route_assistant import AssistantError, InMemoryRouteStore, sequences_of
from ai_ds.openrouter import OpenRouterError
from ai_ds.shanghai_assistant import node_executable
from backend.moscow import MoscowAssistant, MoscowRouteAssistant, moscow_interpretation

DATES = ['2026-11-21', '2026-11-22', '2026-11-23']
CATALOG = {
    'moscow-cultural-0': {'name': 'Уголок дедушки Дурова', 'search_names': []},
    'teatrium': {'name': 'Театриум Терезы Дуровой', 'search_names': []},
    'boulevard': {'name': 'Дуровский бульвар', 'search_names': []},
    'modern': {'name': 'Музей современного искусства', 'search_names': []},
    'park': {'name': 'Парк Сокольники', 'search_names': []},
}


class OfflineIntentsV9(unittest.TestCase):
    def local(self, text, current=('modern',), modes=('walk', 'taxi')):
        return local_command_decision(text, DATES, CATALOG, current,
                                      selected_modes=list(modes))

    def test_bare_name_bypasses_llm(self):
        for text in ('уголок дедушки дурова', 'Уголак дедущки дурово',
                     'добави уголак дедущки дурово',
                     'дабавь уголак дедушки дурово',
                     'Хочу попасть в уголак дедушки дурово'):
            with self.subTest(text=text):
                result = self.local(text)
                self.assertEqual(result['operations'][0]['poi_id'], 'moscow-cultural-0')
                self.assertEqual(result['operations'][0]['op'], 'add_named_poi')

    def test_fuzzy_typo_not_unrelated_durov(self):
        self.assertEqual(resolve_name('уголак дедущки дурово', CATALOG)[0], 'moscow-cultural-0')
        self.assertEqual(resolve_name('театриум терезы дуровой', CATALOG)[0], 'teatrium')
        self.assertEqual(resolve_name('дуровский бульвар', CATALOG)[0], 'boulevard')

    def test_typo_ambiguity_does_not_invent_place(self):
        ambiguous = {'one': {'name': 'Музей Победы'},
                     'two': {'name': 'Музей Победы'}}
        value = local_command_decision('добавь музей победи', DATES, ambiguous)
        self.assertEqual(value['action'], 'needs_clarification')
        self.assertIn('несколько', value['clarification'].lower())

    def test_no_unrequested_place_for_unknown_name(self):
        result = self.local('добавь фонтан неизвестных приключений')
        self.assertEqual(result['action'], 'needs_clarification')
        self.assertIn('Не нашёл', result['clarification'])

    def test_name_versus_category(self):
        for command, category in [('добавь музей', 'museums'),
                                  ('добавь парк на 22 ноября', 'nature'),
                                  ('добавь торговый центр', 'shopping'),
                                  ('добавь смотровую', 'viewpoints')]:
            with self.subTest(command=command):
                response = self.local(command)
                self.assertEqual(response['operations'][0]['category'], category)
                self.assertEqual(response['operations'][0]['op'], 'add_category_poi')
                self.assertEqual(moscow_interpretation(response), response)
        self.assertEqual(self.local('добавь парк сокольники')['operations'][0]['poi_id'], 'park')

    def test_move_and_remove_existing_place_without_llm(self):
        move = self.local('перенеси музей современного искусства на 22 ноября')
        self.assertEqual(move['operations'], [{'op':'move_poi','poi_id':'modern',
                                               'day':DATES[1]}])
        self.assertEqual(moscow_interpretation(move), move)
        delete = self.local('убери Музей современного искусства')
        self.assertEqual(delete['operations'], [{'op':'remove_poi','poi_id':'modern'}])
        self.assertEqual(moscow_interpretation(delete), delete)
        self.assertEqual(self.local('перенеси музей современного искусства')['action'],
                         'needs_clarification')

    def test_set_pace_and_transport_without_llm(self):
        pace = self.local('сделай второй день спокойным')
        self.assertEqual(pace['operations'], [{'op':'set_day_pace','day':DATES[1],
                                               'pace':'relaxed'}])
        self.assertEqual(self.local('только пешком')['operations'][0]['modes'], ['walk'])
        self.assertEqual(self.local('убери такси')['operations'][0]['modes'], ['walk'])
        self.assertEqual(self.local('без такси', modes=('taxi',))['action'], 'needs_clarification')
        self.assertEqual(self.local('убери длинные перерывы')['operations'][0]['op'], 'fill_gaps')

    def test_compound_edit_keeps_all_clauses(self):
        result = self.local('перенеси музей современного искусства на 22 ноября и сделай темп спокойным')
        self.assertEqual(result['action'], 'propose')
        self.assertEqual([op['op'] for op in result['operations']],
                         ['move_poi','set_day_pace','set_day_pace','set_day_pace'])
        self.assertEqual(moscow_interpretation(result), result)
        # Do not execute half a compound request and silently ignore the rest.
        self.assertIsNone(self.local('добавь парк сокольники и переделай всё по-другому'))

    def test_negative_request_not_misread_as_add(self):
        self.assertIsNone(named_poi_decision('не хочу в уголок дедушки дурова', DATES, CATALOG))


class SafeRearrangementV9(unittest.TestCase):
    def setUp(self):
        self.agent = MoscowRouteAssistant(InMemoryRouteStore(), lambda _: None,
            moscow_preferences=object(), proposal_ttl_seconds=300,
            interpretation_parser=moscow_interpretation)
        self.sequences = {DATES[0]: ['must', 'lunch', 'flexible'], DATES[1]: ['second']}
        self.state = {'preferences': {'required_poi_ids': ['must']},
                      'routing_context': {'moscow_meals': {DATES[0]: {
                          'lunch': {'poi_id':'lunch'}}}},
                      'pois': [{'id':key,'categories':['nature']} for key in
                               ('must','lunch','flexible','second','wanted','park')],
                      'recommendation': {'items': [{'poi_id':'park'}]}}

    def test_relocate_optional_first_before_removal(self):
        def choose(_state, sequences, key, days):
            if key == 'wanted' and sequences[DATES[0]] == ['must', 'lunch']:
                out=deepcopy(sequences)
                out[DATES[0]].append('wanted')
                return out
            if key == 'flexible' and days == [DATES[1]] and 'flexible' not in sequences[DATES[0]]:
                out=deepcopy(sequences)
                out[DATES[1]].append('flexible')
                return out
            return None
        self.agent._best_addition = Mock(side_effect=choose)
        result=deepcopy(self.sequences)
        self.agent._apply(self.state,result,{'op':'add_named_poi', 'poi_id':'wanted',
                          'day':DATES[0], 'replace_if_needed':False})
        self.assertEqual(result[DATES[0]],['must','lunch','wanted'])
        self.assertEqual(result[DATES[1]],['second','flexible'])
        self.assertEqual(sum(len(x) for x in result.values()),5)

    def test_no_unauthorized_replacement_if_relocation_fails(self):
        self.agent._best_addition=Mock(return_value=None)
        with self.assertRaises(AssistantError) as error:
            self.agent._apply(self.state,deepcopy(self.sequences),
                {'op':'add_named_poi','poi_id':'wanted','day':DATES[0],
                 'replace_if_needed':False})
        self.assertEqual(error.exception.code,'NO_FEASIBLE_NAMED_INSERTION')

    def test_generic_category_uses_real_candidate_and_validator(self):
        self.agent._best_addition=Mock(return_value={**self.sequences,
                                             DATES[0]:[*self.sequences[DATES[0]],'park']})
        seq=deepcopy(self.sequences)
        self.agent._apply(self.state,seq,{'op':'add_category_poi','category':'nature','day':DATES[0]})
        self.assertIn('park',seq[DATES[0]])


@unittest.skipUnless(node_executable(), 'requires installed Node engine')
class LocalProviderBypassIntegrationV9(unittest.TestCase):
    def test_generic_park_uses_real_scheduler_without_provider(self):
        from tests.test_moscow_agent_fill_integration_v3 import AssistantFillIntegrationTests, poi
        fixture = AssistantFillIntegrationTests();fixture.setUp()
        park = poi('park-id', 37.624)
        park.update(name='Парк у дома', categories=['nature'])
        fixture.points[park['id']] = park
        source = fixture.source(['lunch', 'closing', 'park-id'])
        service = MoscowAssistant.__new__(MoscowAssistant)
        service.interpreter = Mock(side_effect=OpenRouterError('LLM_RATE_LIMITED'))
        service.configured = True
        service.snapshot = 'test'
        session = {'moscow_route': deepcopy(fixture.route), 'moscow_route_id': 'r1',
                   'moscow_version': 1, 'moscow_history': [], 'moscow_preferences': object(),
                   'route_options': {'transport':'walk','conversation': []}}
        with patch('backend.moscow.indexes', return_value=(fixture.points,{},{})), \
             patch('backend.moscow.moscow_inputs', return_value=source):
            result = service._request('actor', session, 'message',
                {'text': 'добавь парк', 'route_id':'r1', 'base_version':1, 'poi_snapshot_id':'test'})
            self.assertEqual(result['type'], 'proposal')
            self.assertIn('Парк у дома', [row['name'] for row in result['diff']['added']])
            self.assertEqual(service.interpreter.call_count, 0)
            self.assertEqual(session['moscow_version'], 1)  # preview is not applied
            confirmed = service._request('actor', session, 'decide', {
                'proposal_id': result['proposal_id'], 'base_version':result['base_version'],
                'decision':'confirm'})
            self.assertEqual(confirmed['type'],'confirmed')
            self.assertIn('park-id', next(iter(sequences_of(confirmed['plan']).values())))

    def test_bare_typo_goes_through_preview_and_confirmation_offline(self):
        from tests.test_moscow_agent_fill_integration_v3 import AssistantFillIntegrationTests,poi,DAY
        f=AssistantFillIntegrationTests();f.setUp()
        named=poi('moscow-cultural-0', 37.624)
        named['name']='Уголок дедушки Дурова'
        named['search_names']=[]
        f.points[named['id']]=named
        fixture=f.source(['lunch','closing','moscow-cultural-0'])
        service=MoscowAssistant.__new__(MoscowAssistant)
        service.interpreter=Mock(side_effect=OpenRouterError('LLM_RATE_LIMITED'))
        service.configured=True;service.snapshot='test'
        session={'moscow_route':deepcopy(f.route), 'moscow_route_id':'r1',
                 'moscow_version':1,'moscow_history':[], 'moscow_preferences':object(),
                 'route_options':{'transport':'walk','conversation':[]}}
        with patch('backend.moscow.indexes', return_value=(f.points,{},{})), \
             patch('backend.moscow.moscow_inputs', return_value=fixture):
            response=service._request('actor',session,'message',{
                'text':'уголак дедущки дурово','route_id':'r1','base_version':1,
                'poi_snapshot_id':'test'})
            self.assertEqual(response['type'],'proposal')
            self.assertEqual(service.interpreter.call_count,0)
            self.assertIn('Уголок дедушки Дурова', [item['name'] for item in response['diff']['added']])
            self.assertIn('НЕ сохранено',response['message'])
            confirmed=service._request('actor',session,'decide',{
                'proposal_id':response['proposal_id'], 'base_version':response['base_version'],
                'decision':'confirm'})
            self.assertEqual(confirmed['type'],'confirmed')
            self.assertIn('moscow-cultural-0',sequences_of(confirmed['plan'])[DAY])
            self.assertEqual(service.interpreter.call_count,0)

if __name__=='__main__':
    unittest.main()
