"""Named Moscow POI edits: no free-provider usage and no false success."""
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from ai_ds.moscow_named_poi import named_poi_decision, resolve_name
from ai_ds.openrouter import OpenRouterError
from ai_ds.route_assistant import AssistantError, InMemoryRouteStore, sequences_of
from ai_ds.shanghai_assistant import node_executable
from backend.moscow import MoscowAssistant, MoscowRouteAssistant, moscow_interpretation

DATES = ['2026-11-21', '2026-11-22', '2026-11-23', '2026-11-24']
CATALOG = {
    'animal-theatre': {'name': 'Уголок дедушки Дурова', 'search_names': []},
    'alt-theatre': {'name': 'Театр зверей им. В. Л. Дурова', 'search_names': ['Уголок дедушки Дурова']},
    'nearby': {'name': 'Парк культуры Таганский'},
    'museum': {'name': 'Центральный музей'},
}


class NamedIntents(unittest.TestCase):
    def test_explicit_name_does_not_need_openrouter(self):
        result = named_poi_decision('Добавь Уголок дедушки Дурова', DATES, CATALOG)
        self.assertEqual(result['operations'], [{'op': 'add_named_poi', 'poi_id': 'animal-theatre',
                                                  'day': None, 'replace_if_needed': False}])
        self.assertEqual(moscow_interpretation(result), result)

    def test_exact_date_and_followup_auto_replacement(self):
        prior = [{'role': 'user', 'content': 'добавь Уголок дедушки Дурова'}]
        result = named_poi_decision('22, замени автоматически', DATES, CATALOG, [], prior)
        self.assertEqual(result['operations'][0]['day'], DATES[1])
        self.assertTrue(result['operations'][0]['replace_if_needed'])

    def test_requested_name_and_explicit_optional_replacement(self):
        text = ('Добавь Уголок дедушки Дурова на 22 ноября. '
                'Если не помещается, замени одно необязательное посещение.')
        result = named_poi_decision(text, DATES, CATALOG)
        self.assertEqual(result['action'], 'propose')
        self.assertEqual(result['operations'][0]['poi_id'], 'animal-theatre')
        self.assertEqual(result['operations'][0]['day'], DATES[1])
        self.assertTrue(result['operations'][0]['replace_if_needed'])

    def test_date_mention_in_direct_addition(self):
        result = named_poi_decision('Пожалуйста, добавь Уголок дедушки Дурова на 23 ноября', DATES, CATALOG)
        self.assertEqual(result['operations'][0]['day'], DATES[2])

    def test_ordinal_date_in_direct_addition(self):
        result = named_poi_decision('Добавь уголок дедушки дурова во второй день', DATES, CATALOG)
        self.assertEqual(result['operations'][0]['day'], DATES[1])

    def test_not_in_trip_day_is_not_silently_changed(self):
        result = named_poi_decision('добавь уголок дедушки дурова 30 ноября', DATES, CATALOG)
        self.assertEqual(result['action'], 'needs_clarification')

    def test_name_from_question_beats_history(self):
        prior = [{'role': 'user', 'content': 'Добавь Парк культуры Таганский'}]
        result = named_poi_decision('Почему не добавился Уголок дедушки Дурова?', DATES, CATALOG, history=prior)
        self.assertEqual(result['action'], 'needs_clarification')
        self.assertIn('Уголок дедушки Дурова', result['clarification'])
        self.assertNotIn('Парк культуры', result['clarification'])

    def test_already_planned_not_inserted_twice(self):
        result = named_poi_decision('добавь уголок дедушки дурова', DATES, CATALOG, current=['animal-theatre'])
        self.assertEqual(result['action'], 'needs_clarification')
        self.assertIn('уже есть', result['clarification'])

    def test_unavailable_v7_name_never_becomes_another_point(self):
        result = named_poi_decision('добавь уголок дедушки дурова', DATES, {'park':CATALOG['nearby']})
        self.assertEqual(result['action'], 'needs_clarification')
        self.assertIn('не нашёл', result['clarification'].lower())

    def test_common_alias_is_resolved_to_actual_catalog_id(self):
        result = named_poi_decision('добавь уголок дедушки дурова', DATES, {'alt-theatre':CATALOG['alt-theatre']})
        self.assertEqual(result['operations'][0]['poi_id'], 'alt-theatre')

    def test_competing_exact_names_require_clarification(self):
        points = {'a':{'name':'Театр Дурова'}, 'b':{'name':'Театр Дурова'}}
        result = named_poi_decision('добавь театр дурова', DATES, points)
        self.assertEqual(result['action'], 'needs_clarification')
        self.assertIn('несколько', result['clarification'])

    def test_category_only_passes_to_normal_interpreter(self):
        self.assertIsNone(named_poi_decision('Добавь парк', DATES, CATALOG))
        self.assertIsNone(named_poi_decision('Добавь недорогой парк', DATES, CATALOG))
        self.assertIsNone(named_poi_decision('Добавь кафе рядом', DATES, CATALOG))
        self.assertIsNone(named_poi_decision('Хочу увидеть больше музеев', DATES, CATALOG))

    def test_other_edits_are_not_silently_dropped(self):
        self.assertIsNone(named_poi_decision('добавь уголок дедушки дурова и убери все перерывы', DATES, CATALOG))

    def test_rate_limit_message_identifies_external_provider(self):
        from ai_ds.shanghai_assistant import human_error
        explanation=human_error('LLM_RATE_LIMITED')
        self.assertIn('OpenRouter',explanation)
        self.assertIn('429',explanation)

    def test_local_moscow_chat_available_without_api_key(self):
        with patch('backend.moscow.moscow_v7',return_value=([], 'test')):
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': ''}):
                service=MoscowAssistant()
                _,status=service.status()
                self.assertTrue(status['available'])
                self.assertEqual(status['mode'],'local')
                self.assertIn('Локальные команды',status['reason'])

    def test_forged_or_missing_authorization_rejected(self):
        with self.assertRaises(ValueError):
            moscow_interpretation({'action':'propose','operations':[
                {'op':'add_named_poi','poi_id':'animal-theatre','day':None,'replace_if_needed':'yes'}],
                'clarification':None})


class AuthorizedReplacement(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryRouteStore()
        self.agent = MoscowRouteAssistant(self.store, lambda _: None, moscow_preferences=object(),
                                         proposal_ttl_seconds=300, interpretation_parser=moscow_interpretation)
        self.state = {'pois': [{'id':v} for v in ['wanted','must','ordinary','meal']],
                      'preferences': {'required_poi_ids':['must']},
                      'routing_context': {'moscow_meals': {DATES[0]: {'lunch':{'poi_id':'meal'}}}}}
        self.original = {DATES[0]:['must','meal','ordinary']}

    def test_no_unauthorized_removal_when_no_space(self):
        self.agent._best_addition = Mock(return_value=None)
        with self.assertRaises(AssistantError) as error:
            self.agent._apply(self.state, deepcopy(self.original),
                              {'op':'add_named_poi','poi_id':'wanted','day':DATES[0], 'replace_if_needed':False})
        self.assertEqual(error.exception.code,'NO_FEASIBLE_NAMED_INSERTION')
        self.agent._best_addition.assert_called_once()

    def test_only_optional_stop_can_be_replaced(self):
        def best(state, sequences, poi_id, days):
            if sequences[DATES[0]] == ['must','meal']:
                result=deepcopy(sequences)
                result[DATES[0]].append('wanted')
                return result
            return None
        self.agent._best_addition = Mock(side_effect=best)
        seq=deepcopy(self.original)
        self.agent._apply(self.state, seq, {'op':'add_named_poi','poi_id':'wanted',
                                             'day':DATES[0],'replace_if_needed':True})
        self.assertEqual(seq[DATES[0]], ['must','meal','wanted'])
        self.assertNotIn('ordinary',seq[DATES[0]])


@unittest.skipUnless(node_executable(), 'requires installed Node scheduler')
class NamedIntegration(unittest.TestCase):
    def test_service_proposes_exact_named_poi_offline_and_confirms(self):
        # Reuse existing fixture's actual Node scheduling engine, not mock logic.
        from tests.test_moscow_agent_fill_integration_v3 import AssistantFillIntegrationTests, poi, DAY
        fixtures = AssistantFillIntegrationTests()
        fixtures.setUp()
        named = poi('animal-theatre',37.624)
        named['name'] = 'Уголок дедушки Дурова'
        named['search_names'] = []
        fixtures.points[named['id']] = named
        src = fixtures.source(['lunch','closing','animal-theatre'])
        service=MoscowAssistant.__new__(MoscowAssistant)
        model = Mock(side_effect=OpenRouterError('LLM_RATE_LIMITED'))
        service.interpreter=model
        service.configured=True
        service.snapshot='test'
        session={'moscow_route':deepcopy(fixtures.route), 'moscow_route_id':'r1',
                 'moscow_version':1, 'moscow_history':[], 'moscow_preferences':object(),
                 'route_options':{'transport':'walk', 'conversation':[]}}
        with patch('backend.moscow.indexes',return_value=(fixtures.points,{},{})), \
             patch('backend.moscow.moscow_inputs',return_value=src):
            result=service._request('user',session,'message',{'text':'Добавь Уголок дедушки Дурова',
                'route_id':'r1','base_version':1,'poi_snapshot_id':'test'})
            self.assertEqual(result['type'],'proposal')
            self.assertIn('НЕ сохранено', result['message'])
            self.assertIn('Уголок дедушки Дурова',[p['name'] for p in result['diff']['added']])
            self.assertEqual(session['moscow_route']['version'] if 'version' in session['moscow_route'] else 1,1)
            self.assertEqual(model.call_count,0,'Named command must not call OpenRouter')
            decided=service._request('user',session,'decide',{'proposal_id':result['proposal_id'],
                'base_version':result['base_version'],'decision':'confirm'})
            self.assertEqual(decided['type'],'confirmed')
            self.assertIn('animal-theatre',sequences_of(decided['plan'])[DAY])
            self.assertEqual(model.call_count,0)


if __name__=='__main__':
    unittest.main()
