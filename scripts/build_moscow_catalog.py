#!/usr/bin/env python3
"""Build a limited, provenance-preserving Moscow cultural POI snapshot.

Input is a *transcription* of an externally published OSM-derived interactive
map (not an OSM .pbf extract). No fictional records or random geocoordinates.
Do not confuse polygon interior points with verified entrances.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data' / 'demo' / 'moscow'
ART = ROOT / 'artifacts'
REPORT = ROOT / 'reports'
SOURCE_URL = 'https://artemilinn.github.io/masters-urban-analytics/'
SOURCE_CODE = 'https://github.com/ArtemIlinn/masters-urban-analytics/blob/main/index.html'
SOURCE_COMMIT = '6d011539cdd244603095b389b0cd996ea214c4c5'
SOURCE_README = 'https://github.com/ArtemIlinn/masters-urban-analytics/blob/main/README.md'
CATS = ['museums','history','nature','cafes','entertainment','shopping','viewpoints','unusual']
CATEGORIES = {'M':'museums','G':'museums','T':'entertainment','C':'entertainment','N':'nature'}
TYPES = {'M':'museum','G':'gallery','T':'theatre','C':'cinema','N':'park_polygon'}
CENTER_CODES = {'02','2c','2d','2e','2i','2j','2k','2l','2m','3f'}
EXPECTED = {'M':365,'G':130,'T':271,'C':129,'N':386}


def norm(text: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', text.casefold().replace('ё','е'))).strip()


def coords_distance(a, b) -> float:
    phi1,phi2 = math.radians(a['lat']), math.radians(b['lat'])
    dl = math.radians(b['lon']-a['lon'])
    dh = math.radians(b['lat']-a['lat'])
    h = math.sin(dh/2)**2+math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    return 12742000*math.asin(min(1,math.sqrt(h)))


def dump(file: Path, value) -> None:
    file.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def input_rows():
    lines = (DATA/'source_rows.psv').read_text(encoding='utf-8').splitlines()
    seq = (DATA/'district_sequence.txt').read_text(encoding='utf-8').strip()
    codes = dict(line.split('|',1) for line in (DATA/'district_codes.psv').read_text(encoding='utf-8').splitlines())
    assert len(lines)==len(seq)//2==1281
    assert Counter(line.split('|')[3] for line in lines)==EXPECTED
    rows=[]
    for i,line in enumerate(lines):
        parts=line.split('|')
        if len(parts) not in (5,6):
            raise ValueError(f'Bad source row {i}: {line[:100]}')
        source_index,lat,lon,typ,name,*other=parts
        d_code = seq[i*2:i*2+2]
        dist = codes.get(d_code)
        name=name.strip()
        category=CATEGORIES[typ]
        reason=[]
        if not name or norm(name) in {'без названия','unnamed','no name'}:
            reason.append('unnamed_record')
        if dist is None:
            reason.append('outside_or_unmatched_124_district_reference')
        if typ=='N' and ('газон' in norm(name) or norm(name).startswith('площадь ') or ('игровая площадка' in norm(name))):
            reason.append('low_tourism_relevance')
        if typ in {'C','T'} and norm(name) in {'да','111'}:
            reason.append('ambiguous_business_name')
        # Important: center points were chosen *within* source OSM polygons,
        # not obtained from a verified entrance or router snap.
        original=[round(float(lat),6),round(float(lon),6)]
        lat,lon=original
        if not (55.0<=lat<=56.3 and 36.5<=lon<=38.2):
            reason.append('invalid_or_far_from_moscow_coordinate')
        primary=category
        additional=[]
        # Extra interest tags describe semantics of the original name, not
        # independent facts (e.g. a museum about unusual topics).
        nl=norm(name)
        if typ=='M' and any(t in nl for t in ('бункер','подземн','пинбол','иллюз','автомат','криптограф','магии','ретро')):
            additional.append('unusual')
        if typ=='M' and any(t in nl for t in ('истори','победы','войны','военн','археолог','усадьба','дома музе')):
            additional.append('history')
        if typ=='N' and any(t in nl for t in ('усадьба','заповедник')):
            additional.append('history')
        data_id = f'osm-derived-map-{source_index}'
        row={
            'id':f'moscow-cultural-{source_index}',
            'name_ru':name,
            'name_original':name,
            'alternative_names':[],
            'lat':lat,'lon':lon,
            'coordinate_method':'polygon_interior_point_derived' if typ=='N' else 'source_map_marker',
            'routing_entrance_lat':None,'routing_entrance_lon':None,
            'kind':'attraction',
            'source_type':TYPES[typ],
            'primary_category':primary,
            'categories':list(dict.fromkeys([primary]+additional)),
            'address':None,'district':dist,'district_code':None if d_code=='__' else d_code,
            'city_area':'central_administrative_okrug' if d_code in CENTER_CODES else ('old_moscow_other' if dist else 'unmatched'),
            'website':None,'opening_hours':None,'operating_status':'unknown',
            'price_rub':None,'rating':None,'review_count':None,'duration_minutes':None,
            'child_friendly':None,'senior_friendly':None,'pet_friendly':None,
            'wheelchair':None,'walking_access_verified':None,
            'source_ids':{'derived_map_feature_id':source_index, 'osm_id':None,'overture_id':None,'wikidata_id':None},
            'sources':[{'name':'OSM-derived HSE cultural map','url':SOURCE_URL,'source_code_url':SOURCE_CODE,'map_git_blob_sha':SOURCE_COMMIT,'source_feature_id':source_index,'original_osm_id':None,'snapshot_checked':'2026-11-19'}],
            'license_note':'ODbL 1.0 (underlying OSM); attribution required; source site is third-party OSM-derived work',
            'source_geometry_area_ha':float(other[0]) if typ=='N' and other and other[0] else None,
            'quality_level':'C' if reason else 'B',
            'quality_reasons':reason,
            'independent_confirmation':False,
            'entrance_verified':False,
            'source_verified_in_map':True,
            'estimated_fields':[],
            'potential_duplicates':[],
        }
        rows.append(row)
    return rows, codes


def find_duplicates(rows):
    # Exact same normalized name, category AND source-placed coordinate.
    # Do not remove nearby, separately located branches or venues in one mall.
    unique=[]; keyseen={}; dup=[]
    for row in rows:
        key=(norm(row['name_ru']),row['primary_category'],round(row['lat'],5),round(row['lon'],5))
        if key in keyseen and 'unnamed_record' not in row['quality_reasons']:
            dup.append({'discarded_id':row['id'],'kept_id':keyseen[key]['id'],'method':'identical_normalized_name_category_coordinates','distance_m':0})
            keyseen[key]['sources'].extend(row['sources'])
        else:
            unique.append(row)
            keyseen[key]=row
    # Geographically near similar names are review candidates, never auto-merged.
    possible=[]
    for i, a in enumerate(unique):
        if norm(a['name_ru'])=='без названия':continue
        for b in unique[i+1:]:
            if abs(a['lat']-b['lat'])>0.0015 or abs(a['lon']-b['lon'])>0.003:continue
            if a['primary_category']!=b['primary_category']:continue
            d=coords_distance(a,b)
            if d>120:continue
            na,nb=norm(a['name_ru']),norm(b['name_ru'])
            sim=SequenceMatcher(None,na,nb).ratio()
            if (na==nb and d<=120) or (sim>=.86 and d<=45 and len(na)>=7 and len(nb)>=7):
                item={'id_1':a['id'],'id_2':b['id'],'distance_m':round(d,1),'name_similarity':round(sim,3),'status':'manual_review_no_auto_merge'}
                possible.append(item)
                a['potential_duplicates'].append(b['id'])
                b['potential_duplicates'].append(a['id'])
    return unique,dup,possible


def main():
    DATA.mkdir(parents=True,exist_ok=True)
    ART.mkdir(exist_ok=True)
    REPORT.mkdir(exist_ok=True)
    candidates,codes=input_rows()
    unique,exact_removed,possible=find_duplicates(candidates)
    for x in unique:
        if x['potential_duplicates']:
            x['quality_reasons'].append('possible_duplicate_manual_review')
            x['quality_level']='C'
    unique.sort(key=lambda p:p['id'])
    quality=Counter(p['quality_level'] for p in unique)
    cat=Counter(p['primary_category'] for p in unique)
    allcats={c:cat.get(c,0) for c in CATS}
    center=[p for p in unique if p['district_code'] in CENTER_CODES]
    center_b=[p for p in center if p['quality_level']=='B']
    demo=[p for p in unique if p['quality_level']=='B']
    review=[p for p in unique if p['quality_level']=='C']
    num_russian=sum(bool(re.search(r'[А-Яа-яЁё]',p['name_ru'])) for p in unique)
    districts=Counter(p['district'] for p in unique if p['district'])
    stat={
        'data_status':'PARTIAL_OSM_DERIVED_CULTURAL_SNAPSHOT_NOT_COMPLETE_MOSCOW',
        'snapshot_date':'2026-11-19',
        'source_counts':{'HSE_map_raw_named_and_unnamed':len(candidates),'OSM_pbf_direct':None,'Overture':None,'data_mos_ru':None,'Wikidata':None},
        'candidate_type_counts':dict(Counter(p['source_type'] for p in candidates)),
        'candidate_total':len(candidates),'exact_duplicates_removed':len(exact_removed),
        'unique_total':len(unique), 'category_counts':allcats,
        'quality_counts':{'A':quality.get('A',0),'B':quality.get('B',0),'C':quality.get('C',0)},
        'possible_duplicate_pairs':len(possible),
        'districts_reference_total':len(codes),'districts_with_pois':len(districts),
        'unmatched_district':sum(not p['district'] for p in unique),
        'city_geofence_verified':'old_moscow_124_district_polygons_from_source_map; TINAO_not_included',
        'new_moscow_included':False,
        'central_okrug_unique':len(center),'central_okrug_quality_B':len(center_b),
        'demo_eligible_quality_B':len(demo),
        'russian_name_percent':round(100*num_russian/len(unique),2),
        'address_known_percent':round(100*sum(p['address'] is not None for p in unique)/len(unique),2),
        'opening_hours_known_percent':round(100*sum(p['opening_hours'] is not None for p in unique)/len(unique),2),
        'prices_known_percent':round(100*sum(p['price_rub'] is not None for p in unique)/len(unique),2),
        'source_native_osm_identifiers_present':0,
        'water_overlay_anomalies':None,'road_access_confirmed_count':None,
        'current_operating_confirmed_count':None,
        'geometry_anomaly_unmatched_124_district_count':sum(not p['district'] for p in unique),
        'geo_bbox_for_display':{'min_lon':min(x['lon'] for x in unique),'min_lat':min(x['lat'] for x in unique),'max_lon':max(x['lon'] for x in unique),'max_lat':max(x['lat'] for x in unique)},
        'district_counts':dict(sorted(districts.items(), key=lambda x: (-x[1],x[0]))),
        'center_category_counts':{c:sum(p['primary_category']==c for p in center) for c in CATS},
        'warning':'Numbers are from one old-Moscow cultural map, not city-wide OSM/Overture. No cafés or shops have been harvested.'
    }
    assert sum(allcats.values())==len(unique)
    assert sum(stat['quality_counts'].values())==len(unique)
    dump(DATA/'pois_candidates.json',{'source':SOURCE_CODE,'records':candidates})
    dump(DATA/'pois_real_master.json',{'schema':'tripwise.moscow.real.v1','generated_at':'2026-11-19','completeness':'partial','pois':unique})
    projection=[]
    for p in demo:
        # NO invented opening, prices, review counts. Distances/coords unchanged.
        projection.append({'id':p['id'],'name':p['name_ru'],'kind':p['kind'],'lat':p['lat'],'lon':p['lon'],
                           'categories':p['categories'],'primary_category':p['primary_category'],
                           'district':p['district'],'price_rub':None,'opening':None,'duration_minutes':None,
                           'rating':None,'review_count':None,'data_origin':'real_osm_derived_single_source',
                           'routing_coordinates_verified':False,'estimated':False,'source_id':p['source_ids']['derived_map_feature_id']})
    dump(DATA/'pois_tripwise_demo.json',{'schema':'tripwise.moscow.preview.v1','currency':'RUB',
      'compatibility':'preview_only_NOT_directly_runnable_in_shanghai_engine',
      'reason':'The Shanghai engine hardcodes 30-32 lat, 120-123 lon, CNY and requires opening/price fields; cannot run real Moscow records without engine change.',
      'pois':projection})
    fields=['id','name_ru','name_original','lat','lon','coordinate_method','primary_category','categories','source_type','district','city_area','address','opening_hours','operating_status','price_rub','rating','quality_level','quality_reasons','source_feature_id','source_url']
    with (DATA/'pois.csv').open('w',encoding='utf-8-sig',newline='') as fh:
        w=csv.DictWriter(fh,fieldnames=fields);w.writeheader()
        for p in unique:
            w.writerow({f:(', '.join(p[f]) if isinstance(p.get(f),list) else p.get(f)) if f in p else
                     (p['sources'][0]['source_feature_id'] if f=='source_feature_id' else SOURCE_CODE) for f in fields})
    dump(DATA/'pois.geojson',{'type':'FeatureCollection','name':'TripWise Moscow real cultural POIs (PARTIAL)',
        'features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[p['lon'],p['lat']]},
            'properties':{k:p[k] for k in ['id','name_ru','primary_category','categories','source_type','district','quality_level','coordinate_method']}}
            for p in unique]})
    with (DATA/'quality_review.csv').open('w',encoding='utf-8-sig',newline='') as fh:
        fields_review=['id','name_ru','lat','lon','source_type','district','reason','source_id']
        w=csv.DictWriter(fh,fieldnames=fields_review);w.writeheader()
        for p in review:
            w.writerow({'id':p['id'],'name_ru':p['name_ru'],'lat':p['lat'],'lon':p['lon'],
                'source_type':p['source_type'],'district':p['district'],
                'reason':';'.join(p['quality_reasons']),'source_id':p['source_ids']['derived_map_feature_id']})
    dump(DATA/'duplicates_removed.json',exact_removed)
    dump(DATA/'duplicates_review.json',possible)
    dump(DATA/'sources.json',[{'id':'hse-osm-cultural-map','name':'HSE 2026 OSM-derived Cultural Moscow map',
      'html_map':SOURCE_URL,'github_source':SOURCE_CODE,'readme':SOURCE_README,'map_blob_sha':SOURCE_COMMIT,
      'checked_on':'2026-11-19','input':'map markers + polygon interior points transcribed from published HTML',
      'coverage':'124 old-Moscow districts; no TINAO','underlying_data_license':'OpenStreetMap ODbL 1.0',
      'source_native_osm_id':'not available in map; only local map index',
      'verified_operating_status':False,'independent_confirmation':False,
      'note':'Underlying OSM records have not been re-fetched; the snapshot may include closed or stale places.'}])
    dump(DATA/'config.json',{'city':'Москва','country':'Россия','currency':'RUB','timezone':'Europe/Moscow','locale':'ru-RU',
        'primary_view_center':[55.751,37.618],'source_coverage':'old_moscow_124_districts_only',
        'all_admin_city_boundaries_verified':False,'new_moscow_supported':False,
        'categories':CATS,'iso_currency':'RUB'})
    dump(DATA/'category_mapping.json',{'source':'OSM features via third-party cultural map',
      'mapping':{'tourism=museum':'museums','tourism=gallery':'museums','amenity=theatre':'entertainment',
                 'amenity=cinema':'entertainment','leisure=park':'nature'},
      'source_types':TYPES,'additional_category_rules':'keyword hints for unusual/history retained with primary category unchanged',
      'uncollected_categories':['history as primary','cafes','shopping','viewpoints','unusual as primary'],
      'warning':'Source index lacks food, commerce and many sightseeing tags; do not interpret zeros as absence in Moscow.'})
    dump(REPORT/'MOSCOW_DATA_STATISTICS.json',stat)
    print('SUMMARY:',json.dumps({k:stat[k] for k in ('candidate_total','exact_duplicates_removed','unique_total','category_counts','quality_counts','possible_duplicate_pairs','districts_with_pois','central_okrug_unique','demo_eligible_quality_B')},ensure_ascii=False))
    print('REVIEW EXAMPLES:',[(p['id'],p['name_ru'],p['quality_reasons']) for p in review[:30]])
    return unique, demo, center, stat

if __name__=='__main__':
    main()
