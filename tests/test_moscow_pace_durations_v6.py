"""Moscow visit-duration semantics, independent of full V7 dataset."""
import unittest
from ai_ds.moscow_catalog import MODEL_VERSION, planning_fields
from ai_ds.moscow_engine_adapter import engine_input, timestamp


def row(name, category='shopping', **extra):
    return {'id': 'test:' + name, 'name': name,
            'primary_category': category, 'categories': [category], **extra}


class MoscowPaceDurations(unittest.TestCase):
    def test_major_shopping_centre_is_two_to_three_hours(self):
        for name in ('ТЦ Европейский', 'Торговый центр', 'ГУМ', 'Авиапарк', 'Афимолл', 'Молл', 'Shopping Mall'):
            profile = planning_fields(row(name))
            self.assertEqual(profile['visit_type']['value'], 'shopping_mall', name)
            self.assertGreaterEqual(profile['duration_minutes']['value'], 120)
            self.assertLessEqual(profile['duration_minutes']['value'], 180)
            self.assertEqual(profile['duration_minutes']['source_type'], 'synthetic')
            self.assertEqual(profile['opening_hours']['value'][2], ['10:00', '22:00'])
            self.assertEqual(profile['opening_hours']['source_type'], 'synthetic')

    def test_osm_mall_tag_classifies_without_name_keywords(self):
        profile = planning_fields(row('Арка', tags={'shop': 'mall'}))
        self.assertEqual(profile['visit_type']['value'], 'shopping_mall')

    def test_normal_shop_is_shorter_than_shopping_centre(self):
        small = planning_fields(row('Магазин сувениров'))
        self.assertEqual(small['visit_type']['value'], 'shop')
        self.assertLess(small['duration_minutes']['value'], 90)

    def test_museum_overrides_short_history_estimate(self):
        museum = planning_fields(row('Подземный музей', 'history'))
        self.assertEqual(museum['visit_type']['value'], 'museum')
        self.assertGreaterEqual(museum['duration_minutes']['value'], 90)
        another = planning_fields(row('Музей Императорского православного палестинского общества', 'museums'))
        self.assertGreaterEqual(another['duration_minutes']['value'], 90)

    def test_ordinary_historical_monument_not_assumed_museum(self):
        palace = planning_fields(row('Старинный дворец', 'history'))
        self.assertEqual(palace['visit_type']['value'], 'historic_site')
        self.assertLess(palace['duration_minutes']['value'], 100)

    def test_verified_source_duration_and_prices_preserved(self):
        profile = planning_fields(row('ТЦ Проверенный', duration_minutes=65, price_rub=340))
        self.assertEqual(profile['duration_minutes']['value'], 65)
        self.assertEqual(profile['duration_minutes']['source_type'], 'source')
        self.assertEqual(profile['price_rub']['value'], 340)
        self.assertEqual(profile['price_rub']['source_type'], 'source')

    def test_deterministic_with_new_model_version(self):
        sample = row('ТЦ Дружба', tags={'shop': 'mall'})
        self.assertIn('v3', MODEL_VERSION)
        self.assertEqual(planning_fields(sample), planning_fields(sample))

    def test_python_adapter_passes_duration_policy_and_user_lock(self):
        sample = row('ТЦ Европейский')
        planned = planning_fields(sample)
        poi = {'id':sample['id'], 'name':sample['name'], 'categories':sample['categories'],
               'coordinates':{'latitude':55.75,'longitude':37.62}, 'planning':planned, 'catalog_signals':{}}
        group = {'adult':1,'child':0,'senior':0,'pet':0}
        day = '2026-11-21'
        context = {'days':[{'date':day,'day_start':timestamp(day,540),'day_end':timestamp(day,1200)}],
                   'moscow_group':group,
                   'duration_overrides':[{'poi_id':sample['id'],'minutes':150}],
                   'moscow_user_duration_ids':[sample['id']],
                   'opening_intervals':[], 'cost_context':{'visit_costs':[{'poi_id':sample['id'],'amount':350}]}}
        profile = {'transport':'walking','pace':'balanced','date_start':day,
                   'primary_interests':['shopping'],'secondary_interests':[],
                   'required_poi_ids':[sample['id']],'budget':{'amount':15000}}
        dataset, options, _ = engine_input(profile,[poi],{'pairs':[]},context)
        self.assertEqual(dataset['pois'][0]['visit_type'],'shopping_mall')
        self.assertEqual(dataset['pois'][0]['duration_source'],'synthetic')
        self.assertTrue(dataset['pois'][0]['duration_locked'])
        self.assertEqual(dataset['pois'][0]['duration_minutes'],150)
        context['moscow_user_duration_ids']=[]
        dataset2, _, _ = engine_input(profile,[poi],{'pairs':[]},context)
        self.assertFalse(dataset2['pois'][0]['duration_locked'])


if __name__ == '__main__':
    unittest.main()
