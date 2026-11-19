import csv
import json
import re
import unittest
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'/'demo'/'moscow'
REP=ROOT/'reports'
ART=ROOT/'artifacts'

class CatalogChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.master=json.loads((DATA/'pois_real_master.json').read_text(encoding='utf-8'))
        cls.demo=json.loads((DATA/'pois_tripwise_demo.json').read_text(encoding='utf-8'))
        cls.stats=json.loads((REP/'MOSCOW_DATA_STATISTICS.json').read_text(encoding='utf-8'))
        cls.records=cls.master['pois']

    def test_sizes(self):
        self.assertEqual(self.stats['candidate_total'],1281)
        self.assertEqual(len(self.records), self.stats['unique_total'])
        self.assertEqual(self.stats['candidate_total']-self.stats['exact_duplicates_removed'],len(self.records))
        self.assertEqual(len(self.demo['pois']),self.stats['demo_eligible_quality_B'])

    def test_one_real_source(self):
        originals=(DATA/'source_rows.psv').read_text(encoding='utf-8').splitlines()
        source_ids={line.split('|',1)[0] for line in originals}
        self.assertEqual(len(originals),1281)
        for p in self.records:
            self.assertIn(str(p['source_ids']['derived_map_feature_id']),source_ids)
            self.assertIsNone(p['source_ids']['osm_id'])
            self.assertEqual(p['sources'][0]['map_git_blob_sha'],'6d011539cdd244603095b389b0cd996ea214c4c5')
            self.assertFalse(p['independent_confirmation'])
            self.assertIn(p['coordinate_method'],('source_map_marker','polygon_interior_point_derived'))

    def test_coordinates_names_unique(self):
        ids=[p['id'] for p in self.records]
        self.assertEqual(len(ids),len(set(ids)))
        for p in self.records:
            self.assertIsInstance(p['name_ru'],str)
            self.assertTrue(p['name_ru'].strip())
            self.assertTrue(55<p['lat']<56.3)
            self.assertTrue(36.5<p['lon']<38.2)
            self.assertIn(p['quality_level'],('A','B','C'))
            self.assertIsNone(p['routing_entrance_lat'])
            self.assertIsNone(p['routing_entrance_lon'])

    def test_unknown_values_not_fabricated(self):
        for p in self.records:
            for k in ('price_rub','opening_hours','rating','review_count','duration_minutes','address'):
                self.assertIsNone(p[k],(p['id'],k))
            self.assertEqual(p['operating_status'],'unknown')
        self.assertIn('NOT_directly_runnable_in_shanghai_engine',self.demo['compatibility'])
        for p in self.demo['pois']:
            self.assertIsNone(p['opening'])
            self.assertIsNone(p['price_rub'])

    def test_counts(self):
        cats=Counter(p['primary_category'] for p in self.records)
        for c,n in self.stats['category_counts'].items():
            self.assertEqual(cats[c],n)
        self.assertEqual(Counter(p['quality_level'] for p in self.records),{k:v for k,v in self.stats['quality_counts'].items() if v})
        self.assertEqual(sum(bool(p['district']) for p in self.records),len(self.records)-self.stats['unmatched_district'])

    def test_geojson_csv(self):
        geo=json.loads((DATA/'pois.geojson').read_text(encoding='utf-8'))
        self.assertEqual(len(geo['features']),len(self.records))
        with (DATA/'pois.csv').open(encoding='utf-8-sig',newline='') as file:
            self.assertEqual(sum(1 for _ in csv.DictReader(file)),len(self.records))

    def test_html_selfcontained_data_and_png(self):
        html=(ART/'tripwise-moscow-coverage.html').read_text(encoding='utf-8')
        self.assertEqual(html.count('const POINTS='),1)
        self.assertIn(self.records[0]['name_ru'],html)
        self.assertIn(self.records[-1]['name_ru'],html)
        self.assertIn('drawOffline()',html)
        self.assertTrue((ART/'moscow-poi-coverage.png').stat().st_size>30000)
        center=(ART/'tripwise-moscow-verified.html').read_text(encoding='utf-8')
        self.assertIn('Центр Москвы',center)
        self.assertIn('Только выбранные записи уровня B',center)

if __name__=='__main__':unittest.main()
