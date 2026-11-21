"""Regression for the real Moscow V7 attraction and conversational named POIs.

The identity below is from V7 pois_real_expanded.csv at HEAD c78bb2ea;
full V7 JSON and live OSM path are not bundled with this source ZIP.
"""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from ai_ds.moscow_named_poi import named_poi_decision, resolve_name
from ai_ds.openrouter import OpenRouterError
from ai_ds.shanghai_assistant import node_executable
from ai_ds.route_assistant import sequences_of
from backend.moscow import MoscowAssistant

REAL_V7_ID = 'moscow-cultural-0'
REAL_V7_POI = {'name': 'Уголок дедушки Дурова', 'search_names': [],
               'coordinates': {'latitude': 55.77933, 'longitude': 37.62121},
               'categories': ['entertainment']}
DATES = ['2026-11-21', '2026-11-22', '2026-11-23']
CATALOG = {REAL_V7_ID: REAL_V7_POI,
           'another-durov': {'name':'Театриум Терезы Дуровой', 'search_names':[]},
           'durov-boulevard': {'name':'Дуровский бульвар','search_names':[]}}


class RealCatalogNameRegression(unittest.TestCase):
    def assert_named(self, text, day=None):
        result = named_poi_decision(text, DATES, CATALOG)
        self.assertIsNotNone(result, text)
        self.assertEqual(result['action'], 'propose', result)
        self.assertEqual(result['operations'], [
            {'op': 'add_named_poi', 'poi_id': REAL_V7_ID,
             'day': day, 'replace_if_needed': False}])

    def test_exact_screenshot_phrase_bypasses_ai(self):
        self.assert_named('дебил, я хочу в уголок дедушки дурова')

    def test_natural_phrasings(self):
        for phrase in ('Я хочу в Уголок дедушки Дурова',
                       'Хочу попасть в Уголок дедушки Дурова',
                       'Хочу сходить в Уголок дедушки Дурова',
                       'Очень хочу в Уголок Дурова',
                       'Пойдем в Уголок дедушки Дурова',
                       'Давай в Уголок дедушки Дурова',
                       'Внеси Уголок дедушки Дурова'):
            with self.subTest(phrase=phrase):
                self.assert_named(phrase)

    def test_planning_for_specified_day(self):
        self.assert_named('Я хочу в Уголок дедушки Дурова на 22 ноября', DATES[1])

    def test_negative_intent_is_not_addition(self):
        self.assertIsNone(named_poi_decision('Я не хочу в Уголок дедушки Дурова', DATES, CATALOG))

    def test_no_fabricated_venue_or_unrelated_durov_theatre(self):
        without = {key: value for key, value in CATALOG.items() if key != REAL_V7_ID}
        result = named_poi_decision('Я хочу в Уголок дедушки Дурова', DATES, without)
        self.assertEqual(result['action'], 'needs_clarification')
        self.assertIn('Не нашёл', result['clarification'])

    def test_compatible_alias_when_real_id_absent(self):
        other = {'venue': {'name': 'Театр зверей имени В. Л. Дурова'}}
        self.assertEqual(resolve_name('Уголок дедушки Дурова', other)[0], 'venue')

    def test_stale_known_id_cannot_point_to_another_durov_place(self):
        wrong = {REAL_V7_ID: {'name': 'Дуровский бульвар'}}
        result = named_poi_decision('Я хочу в Уголок дедушки Дурова', DATES, wrong)
        self.assertEqual(result['action'], 'needs_clarification')

    def test_no_silent_replacement(self):
        result = named_poi_decision('Я хочу в Уголок дедушки Дурова', DATES, CATALOG)
        self.assertFalse(result['operations'][0]['replace_if_needed'])

    def test_status_api_identifies_live_handler(self):
        from types import SimpleNamespace
        from backend.shanghai import assistant_status
        route = {'city': 'moscow', 'poi_snapshot_id': 'fixture-v7',
                 'plan': {'status': 'unknown'}, 'options': {'conversation': []}}
        service = SimpleNamespace(configured=True, snapshot='fixture-v7',
                                  mode='openrouter', llm_configured=False)
        with patch('backend.shanghai.owned_route', return_value=route), \
             patch('backend.shanghai.assistant_for', return_value=service), \
             patch('backend.shanghai.assistant_binding', return_value=('t', {'csrf':'token'})):
            status = assistant_status('route', object(), {'id':1})
        self.assertTrue(status['available'])
        self.assertEqual(status['agent_version'], 'moscow-offline-v9')
        self.assertEqual(status['mode'], 'local')

    def test_discourages_wrong_id_claim(self):
        wrong = {REAL_V7_ID: {'name': 'Парк Гагарина'}, **{k:v for k,v in CATALOG.items() if k != REAL_V7_ID}}
        result = named_poi_decision('Я хочу в Уголок дедушки Дурова', DATES, wrong)
        self.assertEqual(result['action'], 'needs_clarification')


@unittest.skipUnless(node_executable(), 'Node.js engine not installed')
class RealIdIntegration(unittest.TestCase):
    def test_exact_user_phrase_without_openrouter_end_to_end(self):
        from tests.test_moscow_agent_fill_integration_v3 import AssistantFillIntegrationTests, poi, DAY
        fixture = AssistantFillIntegrationTests()
        fixture.setUp()
        venue = poi(REAL_V7_ID, 37.624)
        venue.update(name='Уголок дедушки Дурова', search_names=[])
        fixture.points[REAL_V7_ID] = venue
        source = fixture.source(['lunch','closing',REAL_V7_ID])
        service = MoscowAssistant.__new__(MoscowAssistant)
        service.interpreter = Mock(side_effect=OpenRouterError('LLM_RATE_LIMITED'))
        service.configured = True
        service.snapshot = 'test'
        session = {'moscow_route': deepcopy(fixture.route), 'moscow_route_id':'r1',
                   'moscow_version':1, 'moscow_history':[], 'moscow_preferences':object(),
                   'route_options':{'transport':'walk', 'conversation':[]}}
        with patch('backend.moscow.indexes', return_value=(fixture.points, {}, {})), \
             patch('backend.moscow.moscow_inputs', return_value=source):
            reply = service._request('actor', session, 'message', {
                'text':'дебил, я хочу в уголок дедушки дурова',
                'route_id':'r1','base_version':1,'poi_snapshot_id':'test'})
            self.assertEqual(reply['type'],'proposal')
            self.assertIn('Уголок дедушки Дурова',
                          [item['name'] for item in reply['diff']['added']])
            self.assertIn('НЕ сохранено', reply['message'])
            self.assertEqual(service.interpreter.call_count, 0)
            confirmed = service._request('actor', session, 'decide', {
                'proposal_id':reply['proposal_id'], 'base_version':reply['base_version'],
                'decision':'confirm'})
            self.assertEqual(confirmed['type'],'confirmed')
            self.assertIn(REAL_V7_ID, sequences_of(confirmed['plan'])[DAY])
            self.assertEqual(service.interpreter.call_count, 0)


if __name__ == '__main__':
    unittest.main()
