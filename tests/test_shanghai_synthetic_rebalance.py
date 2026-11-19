"""Shanghai final synthetic catalogue regression checks. Run: python -m unittest tests/test_shanghai_synthetic_rebalance.py"""
from __future__ import annotations
import datetime
import importlib.util
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = {'history','nature','museums','cafes','entertainment','shopping','viewpoints','unusual'}

def meters(a,b):
    lat = math.radians((a['lat']+b['lat'])/2)
    return math.hypot((a['lat']-b['lat'])*111_320,(a['lon']-b['lon'])*111_320*math.cos(lat))

def cell(p):return (round(p['lat']/.03),round(p['lon']/.03))

class ShanghaiFinalQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=json.loads((ROOT/'data/demo/shanghai/pois.json').read_text(encoding='utf8'))
        cls.p=cls.data['pois'];cls.orig=cls.p[:500];cls.new=cls.p[500:]

    def test_identifiers_categories_types_and_prices(self):
        p=self.p
        self.assertEqual(len(p),2000)
        self.assertEqual(len({v['id'] for v in p}),2000)
        self.assertEqual(sum(v['kind']=='restaurant' for v in p),850)
        self.assertEqual(sum(v['kind']=='attraction' for v in p),1150)
        totals=Counter(c for v in p for c in v['categories'])
        self.assertEqual(totals,{'museums':90,'history':385,'nature':140,'cafes':850,'entertainment':200,'shopping':444,'viewpoints':92,'unusual':115})
        for v in p:
            self.assertEqual(len(set(v['categories'])),len(v['categories']))
            self.assertTrue(set(v['categories']) <= CATEGORIES,v['id'])
            self.assertEqual(v['kind']=='restaurant','cafes' in v['categories'],v['id'])
            self.assertIsInstance(v['price_cny'],(int,float))
            self.assertTrue(0<=v['price_cny']<=1000,v['id'])
            self.assertGreater(v['duration_minutes'],0,v['id'])
            self.assertEqual(v['names']['ru'],v['name_ru'],v['id'])
            self.assertEqual(v['names']['en'],v['name_en'],v['id'])
            self.assertEqual(v['names']['zh'],v['name_zh'],v['id'])

    def test_spatial_spacing_no_five_meter_shadow_duplicates(self):
        base=self.orig
        byid={x['id']:x for x in base}
        self.assertEqual(len({(p['lat'],p['lon']) for p in self.new}),1500)
        bins=defaultdict(list)
        for p in self.new:
            loc=p['synthetic_location']
            self.assertEqual(loc['method'],'deterministic_hex_parcels_inside_same_bank_poi_triangles_v2')
            self.assertFalse(loc['building_footprint_verified'])
            self.assertFalse(loc['road_clearance_verified'])
            self.assertFalse(loc['water_clearance_verified'])
            self.assertEqual(p['provenance']['coordinates'],'synthetic_urban_parcel_unverified')
            self.assertEqual(len(loc['reference_poi_ids']),3)
            self.assertTrue(all(byid[i]['river_bank']==p['river_bank'] for i in loc['reference_poi_ids']))
            self.assertTrue(78<=loc['distance_to_nearest_reference_m']<=425,p['id'])
            self.assertTrue(31.08<=p['lat']<=31.38,p['id'])
            self.assertTrue(121.31<=p['lon']<=121.64,p['id'])
            x=round(p['lon']*95000/100)
            y=round(p['lat']*111000/100)
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    for v in bins[(x+dx,y+dy)]:
                        self.assertGreaterEqual(meters(p,v),100,(p['id'],v['id']))
            bins[(x,y)].append(p)
        # Limit cell crowding but do not flatten densely touristic inner-city zones.
        counts=Counter(cell(p) for p in self.p)
        self.assertLessEqual(max(counts.values()),150)
        self.assertGreaterEqual(len(counts),65)

    def test_calendar_sunday_and_all_weekdays(self):
        sunday=datetime.date(2026,11,22)
        saturday=datetime.date(2026,11,21)
        self.assertEqual(sunday.isoweekday(),7)
        self.assertEqual(saturday.isoweekday(),6)
        scenarios=[p for p in self.p if p.get('synthetic_museum')]
        self.assertEqual(len(scenarios),79)
        self.assertTrue(all(p['opening']['closed_weekdays']==[7] for p in scenarios))
        for p in self.p:
            closed=p['opening']['closed_weekdays']
            self.assertTrue(all(type(x) is int and 1<=x<=7 for x in closed),p['id'])
            for key in ('open','close'):
                self.assertRegex(p['opening'][key],r'^\d\d:\d\d$')
            hh1,mm1=map(int,p['opening']['open'].split(':'))
            hh2,mm2=map(int,p['opening']['close'].split(':'))
            self.assertTrue(0<=hh1<24 and 0<=mm1<60 and 0<=hh2<=24 and 0<=mm2<60 and (hh1,mm1)<(hh2,mm2),p['id'])
            self.assertTrue(all(type(m) is int and 1<=m<=12 for m in p['season_months']),p['id'])

    def test_scenarios_are_declared_fictional_and_have_distinct_titles(self):
        synth=[p for p in self.p if p.get('synthetic_experience')]
        self.assertEqual(len(synth),389) # 79 museums + 110 nature + 70 viewpoints + 130 entertainment
        self.assertEqual(len({p['name'] for p in synth}),len(synth))
        for p in synth:
            self.assertIn('ВЫМЫШЛЕННЫЙ',p['description'])
            self.assertFalse(p['synthetic_experience']['venue_verified'])
            self.assertIn('original_catalog_name_before_scenario',p)
            self.assertIn('synthetic',p['provenance']['identity'])
            self.assertTrue(p['name'].endswith('(демо)') or p['name'].startswith('Демо-музей'))
        museums=[p for p in synth if p['synthetic_experience']['type']=='museum']
        self.assertEqual(len(museums),79)
        self.assertGreaterEqual(len(set(p['synthetic_experience']['theme'] for p in museums)),30)

    def test_food_coverage_and_five_day_readiness(self):
        food=[p for p in self.p if p['kind']=='restaurant']
        self.assertGreaterEqual(sum(int(p['opening']['open'][:2])<=8 for p in food),200)
        self.assertGreaterEqual(sum(int(p['opening']['close'][:2])>=22 for p in food),400)
        # O(n^2) only for 981 new attraction x 850 restaurants, no third-party dependencies.
        foods_by_cell=defaultdict(list)
        for q in food:foods_by_cell[(round(q['lat']/.01),round(q['lon']/.01))].append(q)
        for p in self.new:
            if p['kind']!='attraction':continue
            ix=round(p['lat']/.01);iy=round(p['lon']/.01)
            local=[q for dx in (-1,0,1) for dy in (-1,0,1) for q in foods_by_cell[(ix+dx,iy+dy)]]
            self.assertTrue(local,p['id'])
            self.assertLess(min(meters(p,q) for q in local),510,p['id'])
        for cat,minn in [('museums',70),('history',300),('nature',120),('entertainment',150),('viewpoints',80),('shopping',300)]:
            self.assertGreaterEqual(sum(cat in p['categories'] for p in self.p),minn)

    def test_html_embeddings_exact_and_idempotent(self):
        spec=importlib.util.spec_from_file_location('refresh_shanghai',ROOT/'scripts/refresh_shanghai_html.py')
        refresh=importlib.util.module_from_spec(spec);spec.loader.exec_module(refresh)
        for rel,var in [('artifacts/tripwise-shanghai.html','DATA'),('artifacts/tripwise-shanghai-coverage.html','POIS')]:
            html=(ROOT/rel).read_text(encoding='utf-8')
            start=html.index('const '+var+'=')+len('const '+var+'=')
            result,_=json.JSONDecoder().raw_decode(html[start:])
            embedded=result['pois'] if var=='DATA' else result
            self.assertEqual(len(embedded),2000)
            if var=='DATA':
                self.assertEqual(embedded,self.p)
                self.assertEqual(result.get('transport'),self.data.get('transport'))
            else:
                self.assertEqual([(p['id'],p['lat'],p['lon'],p['categories']) for p in embedded],
                                 [(p['id'],p['lat'],p['lon'],p['categories']) for p in self.p])
            self.assertEqual(refresh.patch_literal(html,var,self.data),html)

if __name__=='__main__':unittest.main()
