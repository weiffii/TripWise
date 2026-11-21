import unittest
from unittest.mock import Mock, patch

from ai_ds.moscow_local_commands import local_command_decision
from ai_ds.moscow_road import coordinates
from ai_ds.moscow_transit import _distance
from backend.moscow import MoscowAssistant, indexes, plan_moscow
from backend.shanghai import MoscowPreferences


class ScreenshotChatTests(unittest.TestCase):
    def test_short_replacement_reply_keeps_target_and_date(self):
        dates = ['2026-11-21', '2026-11-22']
        history = [
            {'role': 'user', 'content': 'Добавь уголок дедушки дурова 22 числа'},
            {'role': 'assistant', 'content': 'Какое место или какая категория замены нужны?'},
        ]
        for text in ('любая', 'замени ЛЮБОЕ место 22 числа на уголок дедушки дурова'):
            result = local_command_decision(text, dates, indexes()[0], history=history)
            self.assertEqual(result['operations'], [{
                'op': 'replace_any_named', 'poi_id': 'moscow-cultural-0', 'day': dates[1],
            }])
        bare = local_command_decision('уголок дедушки дурова 22 числа', dates, indexes()[0])
        self.assertEqual(bare['operations'][0]['day'], dates[1])
        self.assertEqual(bare['operations'][0]['poi_id'], 'moscow-cultural-0')

    @patch.dict('os.environ', {'MOSCOW_OSRM_FOOT': '', 'MOSCOW_OSRM_CAR': '', 'OPENROUTER_API_KEY': ''})
    def test_replan_near_durov_offline_then_confirm(self):
        prefs = MoscowPreferences(
            city='moscow', timezone='Europe/Moscow', start_date='2026-11-21', days=2,
            day_start='09:00', day_end='20:00', pace='balanced', transport='walk',
            group=dict(adult=1, child=0, senior=0, pet=0), primary_interests=['history', 'nature'],
            secondary_interests=['museums'], required_ids=[], budget_amount=25000,
            budget_currency='RUB', budget_basis='per_group',
        )
        plan, options, snapshot = plan_moscow(prefs)
        service = MoscowAssistant()
        service.interpreter = Mock(side_effect=AssertionError('OpenRouter must not receive the local command'))
        token, state = service.session()
        state['route_options'] = options
        csrf = state['csrf']
        service.request(token, csrf, 'register', dict(request_id='register-1', route_id='route-1',
            version=1, preferences=prefs.model_dump(mode='json'), plan=plan))
        proposed = service.request(token, csrf, 'message', dict(request_id='message-1', route_id='route-1',
            base_version=1, poi_snapshot_id=snapshot,
            text='переделай маршрут 22 числа ближе к уголку дедушки дурова, чтобы не нужно было далеко ездить, можешь менять места'))
        self.assertEqual(proposed['type'], 'proposal', proposed)
        service.interpreter.assert_not_called()
        self.assertEqual(proposed['plan']['days'][0], plan['days'][0])
        self.assertIn('moscow-cultural-0', [visit['poi_id'] for visit in proposed['plan']['days'][1]['visits']])
        catalog = indexes()[0]
        anchor = coordinates(catalog['moscow-cultural-0'])
        def mean_distance(day):
            distances = [_distance(anchor, coordinates(catalog[row['poi_id']])) for row in day['visits']]
            return sum(distances) / len(distances)
        self.assertLess(mean_distance(proposed['plan']['days'][1]), mean_distance(plan['days'][1]))
        self.assertEqual(state['moscow_version'], 1)
        confirmed = service.request(token, csrf, 'decide', dict(request_id='decision-1', base_version=1,
            decision='confirm', proposal_id=proposed['proposal_id']))
        self.assertEqual(confirmed['type'], 'confirmed', confirmed)
        self.assertEqual(state['moscow_version'], 2)
