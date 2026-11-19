"""Offline QA for Shanghai synthetic placement and embedded 2000-POI HTML."""
import hashlib
import importlib.util
import json
import math
from collections import Counter
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('refresh_shanghai',ROOT/'scripts/refresh_shanghai_html.py')
REFRESH=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REFRESH)


class RebalanceQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=json.loads((ROOT/'data/demo/shanghai/pois.json').read_text(encoding='utf-8'))
        cls.pois=cls.data['pois']

    def test_catalog_identifiers_and_categories(self):
        self.assertEqual(len(self.pois),2000)
        self.assertEqual(len({p['id'] for p in self.pois}),2000)
        self.assertEqual(sum(p['kind']=='restaurant' for p in self.pois),850)
        self.assertEqual(sum('museums' in p['categories'] for p in self.pois),90)
        self.assertEqual(sum('history' in p['categories'] for p in self.pois),695)
        self.assertEqual(sum(bool(p.get('synthetic_museum')) for p in self.pois),79)

    def test_coordinates_inside_urban_shanghai_envelope(self):
        for p in self.pois:
            self.assertTrue(31.08 <= p['lat'] <= 31.38,p['id'])
            self.assertTrue(121.31 <= p['lon'] <= 121.64,p['id'])
        self.assertEqual(len({(p['lat'],p['lon']) for p in self.pois[500:]}),1500)

    def test_each_new_poi_near_a_preexisting_site(self):
        anchors={p['id']:p for p in self.pois[:500]}
        for p in self.pois[500:]:
            meta=p['synthetic_location'];a=anchors[meta['anchor_id']]
            self.assertEqual(meta['anchor_lat'],a['lat'])
            self.assertEqual(meta['anchor_lon'],a['lon'])
            distance=math.hypot((p['lat']-a['lat'])*111320,
                                (p['lon']-a['lon'])*111320*math.cos(math.radians(a['lat'])))
            self.assertLessEqual(distance,5.05,(p['id'],distance))
            self.assertEqual(p['provenance']['coordinates'],'synthetic_near_existing_poi_anchor')
            self.assertIn('original_coordinates_before_synthetic_relocation',p)
            self.assertFalse(meta['road_clearance_verified'])

    def test_no_more_mega_cluster(self):
        # A coarse ~3 km cell used to have 890 objects; max now far below that.
        cells=Counter((round(p['lat']/.03),round(p['lon']/.03)) for p in self.pois)
        self.assertLessEqual(max(cells.values()),150)
        self.assertGreaterEqual(len(cells),60)

    def test_synthetic_museum_provenance_not_real_claims(self):
        for p in self.pois:
            if p.get('synthetic_museum'):
                self.assertIn('museums',p['categories'])
                self.assertIn('history',p['categories'])
                self.assertTrue(p['name'].startswith('Музей городской памяти'))
                self.assertIn('original_catalog_name_before_museum_scenario',p)
                self.assertIn('synthetic',p['provenance']['identity'])
                self.assertIn('СЦЕНАРИЙ',p['description'])

    def test_embedded_data_matches_json_and_is_repatchable(self):
        for path,name in [('tripwise-shanghai.html','DATA'),
                          ('tripwise-shanghai-coverage.html','POIS')]:
            html=(ROOT/'artifacts'/path).read_text(encoding='utf-8')
            start=html.index('const '+name+'=')+len('const '+name+'=')
            embedded,_=json.JSONDecoder().raw_decode(html[start:])
            pois=embedded['pois'] if name=='DATA' else embedded
            self.assertEqual(len(pois),2000)
            self.assertEqual(sum('museums' in p['categories'] for p in pois),90)
            self.assertEqual({p['id']:(p['lat'],p['lon']) for p in pois},
                             {p['id']:(p['lat'],p['lon']) for p in self.pois})
            repatched=REFRESH.patch_literal(html,name,self.data)
            self.assertEqual(repatched,html,'Embedding the same data should be idempotent')


if __name__=='__main__':
    unittest.main()
