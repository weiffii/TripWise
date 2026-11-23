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
        self.assertEqual(self.local('сделай темп помедленнее')['operations'],
                         [{'op': 'set_day_pace', 'day': day, 'pace': 'relaxed'} for day in DATES])
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
    def test_insertion_prefers_a_short_pause_over_a_shorter_ride(self):
        day = DATES[0]
        def candidate(wait, travel):
            return {
                'days': [{'date': day, 'visits': [{'end_at': f'{day}T15:00:00+03:00'}],
                          'legs': [{'duration_seconds': travel}]}],
                'gap_diagnostics': {'remaining': [{'kind': 'internal', 'minutes': wait}]},
            }

        self.assertLess(MoscowRouteAssistant._addition_score(candidate(37, 780), day),
                        MoscowRouteAssistant._addition_score(candidate(132, 480), day))

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

    def test_replacement_checks_smaller_sets_and_protects_pinned_visits(self):
        day = DATES[0]
        source = {day: ['must', 'lunch', 'pinned', 'first', 'second', 'third']}
        self.state['routing_context']['visit_times'] = {day: {'pinned': '14:00'}}
        self.state['pois'].extend({'id': key} for key in ('pinned', 'first', 'third'))
        tested = []

        def schedule(_state, sequences, key, _days):
            tested.append(set(source[day]) - set(sequences[day]))
            if {'first', 'second'} <= tested[-1]:
                return {day: [*sequences[day], key]}

        self.agent._best_addition = Mock(side_effect=schedule)
        chosen = deepcopy(source)
        self.agent._apply(self.state, chosen,
                          {'op': 'add_named_poi', 'poi_id': 'wanted', 'day': day, 'replace_if_needed': True})
        self.assertEqual(chosen[day], ['must', 'lunch', 'pinned', 'third', 'wanted'])
        self.assertEqual(tested[:4], [set(), {'first'}, {'second'}, {'third'}])
        self.assertTrue(all(not trial & {'must', 'lunch', 'pinned'} for trial in tested))

    def test_no_removals_requested_skips_destructive_compromise(self):
        self.agent.propose = Mock(side_effect=AssistantError('NO_FEASIBLE_NAMED_INSERTION'))
        retry = Mock(return_value=None)
        result, compromise = MoscowAssistant._propose_named_fallback(
            self.agent, 'actor', {}, {'operations': [
                {'op': 'add_named_poi', 'poi_id': 'wanted', 'day': DATES[0], 'replace_if_needed': False},
            ]}, retry, DATES, self.state['pois'], self.state['routing_context'], set(),
            'Добавь exact место, не удаляй остальные')
        retry.assert_called_once_with(
            {'op': 'add_named_poi', 'poi_id': 'wanted', 'day': None, 'replace_if_needed': False})
        self.assertEqual(result['validation_status'], 'needs_clarification')
        self.assertIsNone(compromise)

    def test_generic_category_uses_real_candidate_and_validator(self):
        self.agent._best_addition=Mock(return_value={**self.sequences,
                                             DATES[0]:[*self.sequences[DATES[0]],'park']})
        seq=deepcopy(self.sequences)
        self.agent._apply(self.state,seq,{'op':'add_category_poi','category':'nature','day':DATES[0]})
        self.assertIn('park',seq[DATES[0]])


@unittest.skipUnless(node_executable(), 'requires installed Node engine')
class LocalProviderBypassIntegrationV9(unittest.TestCase):
    def test_edit_replaces_remote_dinner_and_rejects_new_two_hour_walks(self):
        from ai_ds.moscow_engine_adapter import fixed_sequence, timestamp
        from backend.moscow import fix_meal_time
        from tests.test_moscow_agent_fill_integration_v3 import AssistantFillIntegrationTests, DAY, poi

        fixture = AssistantFillIntegrationTests()
        fixture.setUp()
        park = poi('requested-park', 37.48)
        old = poi('old-dinner', 37.62, cafe=True)
        nearby = poi('nearby-dinner', 37.481, cafe=True)
        nearby['planning'].update(food_type={'value': 'restaurant'},
                                  meal_suitability={'value': {'dinner': 'likely'}})
        for place in (park, old, nearby):
            fixture.points[place['id']] = place
        profile, places, ranking, matrix, context = fixture.source(
            ['lunch', 'closing', park['id'], old['id'], nearby['id']])
        for row in matrix['pairs']:
            a, b = row['from_poi_id'], row['to_poi_id']
            if park['id'] in (a, b) and nearby['id'] not in (a, b):
                row['duration_seconds'] = 30 * 60 if (a, b) == ('closing', park['id']) else 119 * 60
            if nearby['id'] in (a, b) and park['id'] not in (a, b):
                row['duration_seconds'] = 34 * 60 if (a, b) == ('closing', nearby['id']) else 119 * 60
        self.assertTrue(fix_meal_time(context, DAY, 'lunch', 'lunch',
                                      fixture.meal['start_at'], fixture.meal['end_at']))
        self.assertTrue(fix_meal_time(context, DAY, old['id'], 'dinner',
                                      timestamp(DAY, 1110), timestamp(DAY, 1160)))
        context['moscow_start_hints'] = {DAY: {'closing': 870, old['id']: 1110}}
        state = {'preferences': profile, 'pois': places, 'recommendation': ranking,
                 'travel_times': matrix, 'routing_context': context, 'route': fixture.route}
        base = {DAY: ['lunch', 'closing', old['id']]}
        state['route'] = fixed_sequence(state, base)
        agent = MoscowRouteAssistant(InMemoryRouteStore(), lambda _: None, proposal_ttl_seconds=300)
        chosen = agent._best_addition(state, base, park['id'], [DAY])
        self.assertEqual(chosen[DAY], ['lunch', 'closing', park['id'], nearby['id']])
        result = agent._candidate(state, chosen)
        self.assertEqual(result['moscow_meals'][DAY]['dinner']['poi_id'], nearby['id'])
        dinner_start = result['moscow_meals'][DAY]['dinner']['start_at']
        self.assertLessEqual(timestamp(DAY, 1080), dinner_start)
        self.assertLessEqual(dinner_start, timestamp(DAY, 1110))
        self.assertTrue(all(leg['duration_seconds'] <= 3600 for leg in result['days'][0]['legs']))

        blocked = deepcopy(state)
        blocked['routing_context'] = deepcopy(context)
        for row in blocked['travel_times']['pairs']:
            if row['from_poi_id'] == 'closing' and row['to_poi_id'] in (park['id'], nearby['id']):
                row['duration_seconds'] = 112 * 60
        self.assertIsNone(agent._best_addition(blocked, base, park['id'], [DAY]))

        mixed = deepcopy(blocked)
        taxi = deepcopy(blocked['travel_times'])
        for row in taxi['pairs']:
            if row['from_poi_id'] == 'closing' and row['to_poi_id'] in (park['id'], nearby['id']):
                row['duration_seconds'] = 30 * 60
        mixed['routing_context']['moscow_transport_modes'] = ['walk', 'taxi']
        mixed['travel_times']['profiles'] = {'walk': blocked['travel_times'], 'taxi': taxi}
        mixed_sequence = agent._best_addition(mixed, base, park['id'], [DAY])
        self.assertIsNotNone(mixed_sequence)
        self.assertIn(park['id'], mixed_sequence[DAY])
        self.assertTrue(any(leg['mode'] == 'taxi' for leg in agent._candidate(mixed, mixed_sequence)['days'][0]['legs']))

        pinned = {**state, 'routing_context': deepcopy(context)}
        pinned['routing_context']['moscow_meal_pins'] = {DAY: {'dinner': '18:30'}}
        self.assertIsNone(agent._best_addition(pinned, base, park['id'], [DAY]))

    def test_budget_compromise_names_two_removed_stops_before_confirmation(self):
        from ai_ds.moscow_engine_adapter import fixed_sequence
        from backend.moscow import fix_meal_time
        from tests.test_moscow_agent_fill_integration_v3 import AssistantFillIntegrationTests, DAY, poi

        fixture = AssistantFillIntegrationTests()
        fixture.setUp()
        first, second = fixture.additions[:2]
        for place in (first, second):
            place['planning']['price_rub']['value'] = 800
        named = poi('animal-theatre', 37.624)
        named.update(name='Уголок дедушки Дурова', search_names=[])
        named['planning']['price_rub']['value'] = 1750
        fixture.points[named['id']] = named
        ids = ['lunch', first['id'], second['id'], 'closing']
        profile, pois, ranking, matrix, context = fixture.source([*ids, named['id']])
        assert fix_meal_time(context, DAY, 'lunch', 'lunch', fixture.meal['start_at'], fixture.meal['end_at'])
        base = fixed_sequence({'preferences': profile, 'pois': pois, 'recommendation': ranking,
                               'travel_times': matrix, 'routing_context': context, 'route': fixture.route},
                              {DAY: ids})
        service = MoscowAssistant.__new__(MoscowAssistant)
        service.interpreter = Mock(side_effect=OpenRouterError('LLM_RATE_LIMITED'))
        service.configured, service.snapshot = True, 'test'
        session = {'moscow_route': deepcopy(base), 'moscow_route_id': 'r1', 'moscow_version': 1,
                   'moscow_history': [], 'moscow_preferences': object(),
                   'route_options': {'transport': 'walk', 'conversation': []}}
        with patch('backend.moscow.indexes', return_value=(fixture.points, {}, {})), \
             patch('backend.moscow.moscow_inputs', return_value=fixture.source([*ids, named['id']])):
            result = service._request('actor', session, 'message', {
                'text': 'Добавь Уголок дедушки Дурова', 'route_id': 'r1', 'base_version': 1,
                'poi_snapshot_id': 'test'})
            self.assertEqual(result['type'], 'proposal')
            self.assertEqual(result['diff']['named_compromise'], 'remove_optional')
            self.assertEqual({row['name'] for row in result['diff']['removed']}, {first['name'], second['name']})
            self.assertIn('Уголок дедушки Дурова', result['message'])
            self.assertIn(DAY, result['message'])
            self.assertEqual(session['moscow_route'], base)
            confirmed = service._request('actor', session, 'decide', {
                'proposal_id': result['proposal_id'], 'base_version': 1, 'decision': 'confirm'})
        self.assertEqual(set(sequences_of(confirmed['plan'])[DAY]), {'lunch', 'closing', named['id']})
        self.assertLessEqual(confirmed['plan']['budget_summary']['known_total'], 2000)

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
