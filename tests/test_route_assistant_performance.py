from difflib import SequenceMatcher
from itertools import product
import subprocess
import unittest
from unittest.mock import patch

from ai_ds import shanghai_assistant as bridge
from ai_ds.moscow_named_poi import _name_features, _typo_score, resolve_name
from ai_ds.route_assistant import AssistantError


class NameMatchingRegressionTests(unittest.TestCase):
    def test_primary_shortcut_preserves_ambiguity_when_title_has_same_name(self):
        points = {'exact': {'name': 'Музей Москвы'},
                  'title': {'name': 'Музей имени Москвы'},
                  'alias': {'name': 'Музей Садового кольца', 'search_names': ['Музей Москвы']}}
        self.assertEqual(resolve_name('Музей Москвы', points),
                         ('ambiguous', ['Музей Москвы', 'Музей имени Москвы', 'Музей Садового кольца']))
        del points['title']
        self.assertEqual(resolve_name('Музей Москвы', points), ('exact', 'Музей Москвы'))

    def test_upper_bounds_match_exhaustive_typo_scoring(self):
        names = ('Уголок дедушки Дурова', 'уголак дедущки дурово', 'Театриум Терезы Дуровой',
                 'Дуровский бульвар', 'музей мосвкы', 'Музей Москвы', 'Музей археологии Москвы',
                 'несуществующее место', 'Зарядье', 'парк Зарядъе', 'Красная площадь',
                 'краснпя плошать', 'aaaaaaaaab', 'baaaaaaaaa', '1234', 'храм')

        def exhaustive(phrase, name, query, words):
            if not query or not words or len(query) == 1 and len(query[0]) < 7:
                return 0
            strengths = [max(SequenceMatcher(None, token, word).ratio() for word in words) for token in query]
            strength = sum(strengths) / len(query)
            if sum(value >= .76 for value in strengths) < min(2, len(query)) or strength < .82:
                return 0
            ratio = SequenceMatcher(None, phrase, name).ratio()
            if ratio < .50 and len(query) >= 2:
                return 0
            return round(520 + 290 * sum(strengths) / len(query) + 100 * ratio - 35 * max(0, len(words) - len(query)))

        for phrase, name in product(names, repeat=2):
            with self.subTest(phrase=phrase, name=name):
                clean_phrase, _, _, query, _ = _name_features(phrase)
                clean_name, _, _, words, _ = _name_features(name)
                self.assertEqual(_typo_score(clean_phrase, clean_name, query, words),
                                 exhaustive(clean_phrase, clean_name, query, words))


@unittest.skipUnless(bridge.node_executable(), 'requires existing Node scheduler')
class RouteBridgeBatchTests(unittest.TestCase):
    def payload(self, label):
        return {'action': 'extend_visits', 'dataset': {'currency': 'CNY'}, 'options': {},
                'plan': {'days': [{'label': label}]}}

    def test_bridge_frames_error_and_unicode_results_in_one_process(self):
        runner = subprocess.Popen
        with patch.object(bridge.subprocess, 'Popen', wraps=runner) as spawn, bridge.bridge_batch():
            for label in ('Русский\nперенос', '上海'):
                with self.assertRaises(AssistantError) as failed:
                    bridge.bridge_call({'action': 'unknown'})
                self.assertEqual(failed.exception.code, 'INVALID_REQUEST')
                self.assertEqual(bridge.bridge_call(self.payload(label)), {'days': [{'label': label}]})
            process = bridge._BRIDGE_BATCH.get().process
            self.assertIsNone(process.poll())
            spawn.assert_called_once()
        self.assertIsNotNone(process.poll())
        self.assertTrue(process.stdin.closed and process.stdout.closed)

    def test_timeout_has_no_retry_inside_batch_and_next_batch_recovers(self):
        runner = subprocess.Popen
        with bridge.bridge_batch(), patch.object(bridge.subprocess, 'Popen', wraps=runner) as spawn:
            self.assertEqual(bridge.bridge_call(self.payload('before')), {'days': [{'label': 'before'}]})
            process = bridge._BRIDGE_BATCH.get().process
            # Reading without a submitted request blocks until Node is terminated.
            with patch.object(bridge._BridgeBatch, 'exchange', side_effect=lambda process, raw: process.stdout.readline()), \
                    patch.object(bridge, '_BRIDGE_TIMEOUT', .05):
                for _ in range(2):
                    with self.assertRaises(AssistantError) as failed:
                        bridge.bridge_call(self.payload('during'))
                    self.assertEqual(failed.exception.code, 'ROUTER_UNAVAILABLE')
            self.assertIsNotNone(process.poll())
            self.assertTrue(process.stdin.closed and process.stdout.closed)
            spawn.assert_called_once()
        with bridge.bridge_batch():
            self.assertEqual(bridge.bridge_call(self.payload('after')), {'days': [{'label': 'after'}]})

    def test_identical_trial_is_reused_only_within_one_batch(self):
        payload = {'action': 'schedule', 'dataset': {'currency': 'RUB'}, 'sequences': {'day': ['x']}}
        original = {'plan': {'days': []}}
        with patch.object(bridge._BridgeBatch, 'exchange', return_value='{"plan":{"days":[]}}\n') as exchange:
            with bridge.bridge_batch():
                result = bridge.bridge_call(payload)
                result['plan']['days'].append('mutation')
                self.assertEqual(bridge.bridge_call(payload), original)
                exchange.assert_called_once()
                changed = {**payload, 'dataset': {'currency': 'CNY'}}
                self.assertEqual(bridge.bridge_call(changed), original)
                self.assertEqual(exchange.call_count, 2)
            with bridge.bridge_batch():
                self.assertEqual(bridge.bridge_call(payload), original)
                self.assertEqual(exchange.call_count, 3)
