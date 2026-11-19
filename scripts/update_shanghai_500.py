"""Replace Shanghai demo POIs with 500 real OpenStreetMap places.

Run from repository root: python scripts/update_shanghai_500.py
Internet access is required for OpenStreetMap Overpass. The script is fail-safe:
if 500 distinct OSM places cannot be obtained, it leaves repo files unchanged.
"""
from __future__ import annotations
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/demo/shanghai/pois.json'
BUILD = ROOT / 'scripts/build_shanghai_demo.py'
BBOX = '(30.95,121.15,31.50,121.85)'
QUERY = f'''[out:json][timeout:180];(
  nwr["tourism"~"^(attraction|museum|gallery|viewpoint|artwork|zoo|theme_park|aquarium)$"]["name"]{BBOX};
  nwr["historic"]["name"]{BBOX};
  nwr["leisure"~"^(park|garden|nature_reserve)$"]["name"]{BBOX};
  nwr["amenity"~"^(restaurant|cafe|fast_food|food_court)$"]["name"]{BBOX};
);out center tags;'''
ENDPOINTS = (
    'https://overpass.kumi.systems/api/interpreter',
    'https://overpass-api.de/api/interpreter',
    'https://overpass.nchc.org.tw/api/interpreter',
)

def load_osm():
    errors = []
    payload = urllib.parse.urlencode({'data': QUERY}).encode()
    for endpoint in ENDPOINTS:
        try:
            req = urllib.request.Request(endpoint, data=payload, headers={'User-Agent':'TripWise-Demo-POI-Builder/1.0 (academic project)', 'Accept':'application/json'})
            with urllib.request.urlopen(req, timeout=220) as resp:
                data = json.load(resp)
            if 'elements' not in data:
                raise ValueError('No OSM elements')
            return data['elements'], endpoint
        except Exception as exc:
            errors.append(f'{endpoint}: {exc}')
    raise RuntimeError('Overpass unavailable: ' + '; '.join(errors))

def point(item):
    tags = item.get('tags') or {}
    name = (tags.get('name:en') or tags.get('name') or '').strip()
    if not name or len(name) > 110 or re.fullmatch(r'\d+', name):
        return None
    lat = item.get('lat', (item.get('center') or {}).get('lat'))
    lon = item.get('lon', (item.get('center') or {}).get('lon'))
    if not isinstance(lat, (float,int)) or not isinstance(lon,(float,int)):
        return None
    if not (30.95 <= lat <= 31.50 and 121.15 <= lon <= 121.85):
        return None
    amenity, tourism, leisure, historic = (tags.get(k,'') for k in ('amenity','tourism','leisure','historic'))
    is_food = amenity in ('restaurant','cafe','fast_food','food_court')
    if is_food:
        kind, cats, duration, price = 'restaurant', ['cafes'], 55, 65
    elif tourism in ('museum','gallery'):
        kind, cats, duration, price = 'attraction', ['museums','history'] if tourism == 'museum' else ['museums'], 95, 45
    elif leisure in ('park','garden','nature_reserve'):
        kind, cats, duration, price = 'attraction', ['nature','viewpoints'], 85, 0
    elif historic:
        kind, cats, duration, price = 'attraction', ['history'], 65, 20
    elif tourism in ('viewpoint','artwork'):
        kind, cats, duration, price = 'attraction', ['viewpoints','unusual'], 45, 0
    else:
        kind, cats, duration, price = 'attraction', ['entertainment','unusual'], 110, 80
    bank = 'east' if lon > 121.50 else 'west'  # planning approximation, not GIS determination
    area = tags.get('addr:suburb') or tags.get('addr:district') or tags.get('addr:city') or 'Шанхай'
    oid = f"osm:{item['type']}:{item['id']}"
    url = f"https://www.openstreetmap.org/{item['type']}/{item['id']}"
    return {
        'id': oid, 'name': name, 'kind': kind, 'categories': cats,
        'lat': round(lat,7), 'lon': round(lon,7),
        'duration_minutes': duration, 'price_cny': price,
        'rating': 4.3, 'review_count': 0,
        'opening': {'open':'09:00' if not is_food else '10:00', 'close':'19:00' if not is_food else '22:00', 'closed_weekdays':[]},
        'area': area, 'river_bank': bank, 'season_months':[],
        'weekend_crowd_level':'medium', 'weekday_crowd_level':'medium',
        'pet_friendly':False, 'family_friendly':True, 'senior_friendly':True,
        'price_multipliers':{'adult':1.0,'child':0.5,'senior':0.7}, 'pet_fee_cny':0,
        'description':f'Реальный объект OpenStreetMap: {name}. Время, стоимость, рейтинг и часы работы — тестовые оценки.',
        'source_url':url, 'coordinate_source_url':url,
        'provenance':{'identity':'osm','coordinates':'osm','rating':'synthetic','review_count':'synthetic','price':'synthetic','opening':'synthetic','duration':'planning_estimate','river_bank':'planning_assumption','seasonality':'synthetic','crowd':'synthetic','group_prices':'synthetic'}
    }

def select(elements):
    all_pois=[]
    used_names=set()
    for item in elements:
        p=point(item)
        if not p: continue
        # Exact name collision typically means OSM mapped separate entrances / duplicate areas.
        key=p['name'].casefold()
        if key in used_names: continue
        used_names.add(key)
        all_pois.append(p)
    food=sorted((p for p in all_pois if p['kind']=='restaurant'),key=lambda p:p['id'])
    sights=sorted((p for p in all_pois if p['kind']=='attraction'),key=lambda p:p['id'])
    if len(sights)<250 or len(food)<80 or len(all_pois)<500:
        raise RuntimeError(f'Not enough unique mapped places: {len(sights)} attractions, {len(food)} dining. Existing files unchanged.')
    # Limit food to 35%, retain variety of tourist attractions, deterministic.
    selected=sights[:350]+food[:150]
    if len(selected)<500: raise RuntimeError('Unable to reach 500; existing files unchanged.')
    assert len({p['id'] for p in selected}) == 500
    assert len({p['name'].casefold() for p in selected}) == 500
    return selected

def main():
    if not DATA.is_file() or not BUILD.is_file():
        raise SystemExit('Run this script inside the existing TripWise repo (feature/ai-ds).')
    elements, endpoint=load_osm()
    pois=select(elements)
    existing=json.loads(DATA.read_text(encoding='utf-8-sig'))
    builder=BUILD.read_text(encoding='utf-8-sig')
    anchor="len(data['pois']) == 100"
    if anchor not in builder and "len(data['pois']) == 500" not in builder:
        raise RuntimeError('Unexpected build script: refusing automated edit.')
    existing['pois']=pois
    existing['description']='500 реальных мест Шанхая по OpenStreetMap; остальные характеристики синтетические.'
    new_builder=builder.replace(anchor, "len(data['pois']) == 500").replace('Нужно ровно 100 демонстрационных объектов.', 'Нужно ровно 500 демонстрационных объектов.')
    # All validations complete before mutating files.
    DATA.write_text(json.dumps(existing,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    BUILD.write_text(new_builder,encoding='utf-8')
    subprocess.run([sys.executable,str(BUILD)],cwd=ROOT,check=True)
    print(f'OK: 500 real named locations with OSM coordinates; {Counter(p["kind"] for p in pois)}; source: {endpoint}')
    print('Changed: data/demo/shanghai/pois.json, scripts/build_shanghai_demo.py, artifacts/tripwise-shanghai.html (existing HTML)')

if __name__=='__main__':main()
