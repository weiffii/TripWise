#!/usr/bin/env python3
"""Strict data QA; geospatial metrics are straight-line/proxy, never road checks."""
import argparse,csv,hashlib,json,math,collections,statistics
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree

CATS=['museums','history','nature','cafes','entertainment','shopping','viewpoints','unusual']
TARGETS=dict(zip(CATS,[170,580,460,2000,560,700,140,390]))
REQUIRED=['id','name','kind','categories','lat','lon','duration_minutes','price_cny','rating','review_count','opening','area','river_bank','season_months','weekend_crowd_level','weekday_crowd_level','pet_friendly','family_friendly','senior_friendly','names','name_ru','name_en','name_zh','provenance']
RINGS={'0_7':(0,7),'7_20':(7,20),'20_45':(20,45)}

def centered_xy(rows,clat=31.2304,clon=121.4737):
    return np.array([[(p['lon']-clon)*111200*math.cos(math.radians(clat)),(p['lat']-clat)*111200] for p in rows],dtype=float)

def main(root,base_path):
    orig=json.loads(base_path.read_text(encoding='utf8'))['pois']
    doc=json.loads((root/'data/demo/shanghai/pois.json').read_text(encoding='utf8'))
    pois=doc['pois']; new=pois[len(orig):]
    cf=json.loads((root/'config/shanghai.json').read_text(encoding='utf8'))
    with (root/cf['metro_source']).open(encoding='utf8',newline='') as f:
        stations=[dict(lat=float(r['lat']),lon=float(r['lon'])) for r in csv.DictReader(f)]
    a=np.asarray(centered_xy(pois));m=centered_xy(stations);xyc=np.linalg.norm(a,axis=1)
    metdist=cKDTree(m).query(a)[0]
    all_near=cKDTree(a).query(a,k=2)[0][:,1]
    foods=[p for p in pois if p['kind']=='restaurant']; other=[p for p in pois if p['kind']!='restaurant']
    cafe_dists=cKDTree(centered_xy(foods)).query(centered_xy(other))[0]
    counts=collections.Counter(p.get('primary_category') or p['categories'][0] for p in pois)
    before=collections.Counter(p.get('primary_category') or p['categories'][0] for p in orig)
    add=collections.Counter(p.get('primary_category') or p['categories'][0] for p in new)
    ring_counts={k:int(np.sum((xyc>=lo*1000)&(xyc<hi*1000))) for k,(lo,hi) in RINGS.items()}
    new_dist=xyc[len(orig):]
    new_rings={k:int(np.sum((new_dist>=lo*1000)&(new_dist<hi*1000))) for k,(lo,hi) in RINGS.items()}
    old_rings={k:ring_counts[k]-new_rings[k] for k in RINGS}
    new_ids=set(p['id'] for p in new);orig_ids=set(p['id'] for p in orig)
    round_coords=lambda pp: [(round(p['lat'],7),round(p['lon'],7)) for p in pp]
    duplicates=lambda pp: sum(n-1 for n in collections.Counter(round_coords(pp)).values() if n>1)
    missing=collections.Counter(k for p in pois for k in REQUIRED if k not in p)
    errors=[]
    def check(cond,message):
        if not cond:errors.append(message)
    check(len(orig)==2000,'input base not 2000')
    check(len(pois)==5000 and len(new)==3000,'catalog count is not 5000 = 2000+3000')
    check(len(set(p['id'] for p in pois))==5000,'ID collision')
    check(pois[:2000]==orig,'Original 2000 records modified')
    check(not(new_ids&orig_ids),'Synthetic IDs collide with original')
    check(dict(counts)==TARGETS,'Category totals incorrect')
    check(dict(add)==cf['category_additions'],'New category quotas incorrect')
    check(new_rings==cf['radial_additions'],'New radial quota incorrect')
    check(not missing,'Missing required fields: '+str(missing))
    check(all(isinstance(p['categories'],list) and p['categories'] for p in pois),'Empty categories')
    check(all(set(p['opening']['closed_weekdays'])<=set(range(1,8)) for p in pois),'closed_weekdays outside 1..7')
    check(all(p['opening']['open']<p['opening']['close'] for p in pois),'Invalid opening hour ordering')
    check(all(0<=p['price_cny']<=2000 and 2.0<=p['rating']<=5.0 and p['duration_minutes']>0 for p in pois),'Invalid price, rating or duration')
    check(all(p['names'].get('zh') and p['names'].get('en') and p['names'].get('ru') for p in new),'Missing multilingual names')
    check(all(p['provenance'].get('identity')=='synthetic_scenario_poi' for p in new),'New records without synthetic provenance')
    check(all(not p['synthetic_location']['physical_site_verified'] for p in new),'Claim of unverified geometry as verified')
    check(duplicates(new)==0,'New duplicated coordinates')
    check(all(cf['conservative_sampling_box_approx']['min_lat']<=p['lat']<=cf['conservative_sampling_box_approx']['max_lat'] and cf['conservative_sampling_box_approx']['min_lon']<=p['lon']<=cf['conservative_sampling_box_approx']['max_lon'] for p in new),'Generated coordinate outside conservative modeling bbox')
    original_sha=hashlib.sha256(json.dumps(orig,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    saved_sha=hashlib.sha256(json.dumps(pois[:len(orig)],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    output={
      'audit_date':'2026-11-19','passes':not errors,'errors':errors,
      'original_count':len(orig),'additional_synthetic_count':len(new),'total_count':len(pois),
      'originals_verbatim_preserved':orig==pois[:len(orig)],
      'original_poi_sha256':original_sha,'unchanged_original_poi_sha256':saved_sha,
      'unique_ids':len({p['id'] for p in pois}),
      'categories':{c:{'original':before[c],'added':add[c],'final':counts[c]} for c in CATS},
      'radial_km':{k:{'original':old_rings[k],'added':new_rings[k],'final':ring_counts[k]} for k in RINGS},
      'metro_source_station_count':len(stations),'metro_straight_line_distance_m':{'median':round(float(np.median(metdist)),1),'p90':round(float(np.quantile(metdist,.9)),1),'percent_under_1000':round(float(np.mean(metdist<=1000)*100),1)},
      'attraction_to_nearest_food_straight_line_m':{'median':round(float(np.median(cafe_dists)),1),'p90':round(float(np.quantile(cafe_dists,.9)),1)},
      'nearest_poi_distance_m':{'p1':round(float(np.quantile(all_near,.01)),1),'median':round(float(np.median(all_near)),1),'n_with_nearest_under_20m':int(sum(all_near<20))},
      'full_duplicate_coordinate_extra_points':duplicates(pois),'original_duplicate_coordinate_extra_points':duplicates(orig),'new_duplicate_coordinate_extra_points':duplicates(new),
      'unique_new_names_zh':len(set(p['name_zh'] for p in new)),
      'unique_new_names_ru':len(set(p['name_ru'] for p in new)),
      'new_museums_closed_on_one_or_more_weekdays':sum(bool(p['opening']['closed_weekdays']) for p in new if p['primary_category']=='museums'),
      'new_ratings_distinct':len(set(p['rating'] for p in new)),
      'new_durations_distinct':len(set(p['duration_minutes'] for p in new)),
      'new_station_kernel_count':sum(p['synthetic_location']['sample_component']=='metro_station_kernel' for p in new),
      'new_district_kernel_count':sum(p['synthetic_location']['sample_component']=='district_activity_kernel' for p in new),
      'new_osm_building_verified':0,'new_building_verified_share_percent':0.0,
      'new_street_access_verified':0,'new_street_access_unverified_share_percent':100.0,
      'total_synthetic_with_verified_buildings_share_percent':0.0,
      'authoritative_admin_polygon_verified':False,'water_polygon_verified':False,'road_network_verified':False,
      'note':'Metro distances and cafe distances are Euclidean locally; all new locations are synthetic, metro coordinates in archived reference not independently rechecked; bbox and approximate Huangpu line are not authoritative polygons.'
    }
    rep=root/'reports/shanghai_5000_qa.json';rep.parent.mkdir(parents=True,exist_ok=True)
    rep.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in output.items() if k in ['passes','errors','categories','radial_km','metro_straight_line_distance_m','attraction_to_nearest_food_straight_line_m','nearest_poi_distance_m','new_station_kernel_count','new_district_kernel_count']},ensure_ascii=False,indent=2))
    if errors:raise SystemExit(1)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--base',type=Path,required=True)
    a=ap.parse_args();main(a.root,a.base)
