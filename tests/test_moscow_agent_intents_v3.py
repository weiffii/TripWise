"""Regression tests for deterministic multi-day gap requests and safe LLM recovery."""
import unittest
from unittest.mock import Mock

from ai_ds.moscow_assistant_intents import gap_fill_decision, interpret_with_retry
from ai_ds.openrouter import OpenRouterError
from backend.moscow import moscow_interpretation

DATES = ['2026-11-21', '2026-11-22', '2026-11-23', '2026-11-24']


class GapIntentTests(unittest.TestCase):
    def test_original_four_day_user_phrase_does_not_call_llm(self):
        parsed = gap_fill_decision('в 2, 3, 4 день длинные перерывы, любые места и категории', DATES)
        self.assertEqual(parsed['action'], 'propose')
        self.assertEqual(parsed['operations'], [{'op': 'fill_gaps', 'days': DATES[1:]}])
        self.assertEqual(moscow_interpretation(parsed), parsed)

    def test_ordinal_days(self):
        parsed = gap_fill_decision('Заполни свободные окна во второй и третий дни', DATES)
        self.assertEqual(parsed['operations'][0]['days'], DATES[1:3])

    def test_ordinal_range(self):
        parsed = gap_fill_decision('Устрани длинные ожидания во 2-4 дни', DATES)
        self.assertEqual(parsed['operations'][0]['days'], DATES[1:4])

    def test_all_days(self):
        parsed = gap_fill_decision('убери огромные перерывы во все дни', DATES)
        self.assertEqual(parsed['operations'][0]['days'], DATES)

    def test_out_of_range_clarifies_not_silent(self):
        parsed = gap_fill_decision('в пятый день большие перерывы', DATES)
        self.assertEqual(parsed['action'], 'needs_clarification')

    def test_other_edits_are_not_swallowed(self):
        self.assertIsNone(gap_fill_decision('убери перерывы и измени транспорт на такси', DATES))
        self.assertIsNone(gap_fill_decision('добавь парк во второй день', DATES))
        self.assertIsNone(gap_fill_decision('заполни большие перерывы только музеями', DATES))

    def test_any_categories_are_really_available_to_gap_repair(self):
        from ai_ds.moscow_gap_repair import MoscowGapIndex, Gap
        def point(key, category):
            return {'id':key,'name':key,'categories':[category],
                'coordinates':{'latitude':55.75,'longitude':37.62},
                'planning':{'duration_minutes':{'value':30},'price_rub':{'value':0},
                            'opening_hours':{'source_type':'synthetic','value':[['09:00','20:00']]*7}}}
        index=MoscowGapIndex([point('museum','museums'),point('shop','shopping')])
        gap=Gap('2026-11-22',600,750,'tail',None,None)
        profile={'primary_interests':['museums'],'secondary_interests':[],'budget':{'amount':10000}}
        context={'moscow_group':{'adult':1,'child':0,'senior':0},'days':[{'date':'2026-11-22'}]}
        selected=index.candidates(gap,profile,context,set(),1000,limit=10)[0]
        expanded=index.candidates(gap,profile,context,set(),1000,limit=10,allow_any_categories=True)[0]
        self.assertEqual([key for key,*_ in selected],['museum'])
        self.assertIn('shop',[key for key,*_ in expanded])

    def test_rejects_invalid_day_list(self):
        parsed = {'action':'propose', 'operations':[{'op':'fill_gaps', 'days':['2026-11-22','2026-11-22']}], 'clarification':None}
        with self.assertRaises(ValueError):
            moscow_interpretation(parsed)

    def test_incomplete_model_answer_retried_once_with_smaller_context(self):
        response = {'action': 'propose', 'operations': [{'op': 'optimize_day', 'day': DATES[1], 'goal': 'waiting'}], 'clarification': None}
        model = Mock(side_effect=[OpenRouterError('LLM_INCOMPLETE_RESPONSE'), response])
        original = {'text': 'сократи переходы', 'days': [{'date': d} for d in DATES], 'catalog': [{'poi_id': str(i)} for i in range(60)]}
        self.assertEqual(interpret_with_retry(model, original, [{'role':'user','content':'x'}]*10), response)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(len(model.call_args.args[0]['catalog']), 20)
        self.assertEqual(len(model.call_args.args[0]['conversation']), 3)
        self.assertEqual(model.call_args.args[0]['text'], original['text'])

    def test_nontruncation_error_does_not_retry(self):
        model = Mock(side_effect=OpenRouterError('LLM_RATE_LIMITED'))
        with self.assertRaises(OpenRouterError):
            interpret_with_retry(model, {'text':'test','days':[],'catalog':[]}, [])
        self.assertEqual(model.call_count,1)


if __name__ == '__main__':
    unittest.main()
