"""Populate the existing Shanghai demo catalogue with 500 NAMED OSM locations.

No third-party Python libraries. Real coordinates are OSM node positions/way centers,
not fabricated entrances. Synthetic estimates remain explicitly labelled.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / 'data/demo/shanghai/pois.json'
OVERPASS = [
    'https://overpass.kumi.systems/api/interpreter',
    'https://overpass.private.coffee/api/interpreter',
    'https://overpass.nchc.org.tw/api/interpreter',
]
# Avoid extending the set with arbitrary offices, apartments or unnamed objects.
FILTERS = [
    ('tourism', 'museum|gallery|attraction|viewpoint|artwork|zoo|aquarium|theme_park'),
    ('historic', 'monument|memorial|temple|archaeological_site|building|fort|castle|tomb|city_gate'),
    ('leisure', 'park|garden|nature_reserve|amusement_arcade|sports_centre'),
    ('amenity', 'restaurant|cafe|food_court|theatre|cinema|arts_centre|place_of_worship'),
    ('shop', 'mall|department_store'),
]
CATEGORIES = ('museums', 'history', 'nature', 'cafes', 'entertainment', 'shopping', 'viewpoints', 'unusual')
QUOTA = {'museums': 50, 'history': 65, 'nature': 65, 'cafes': 120,
         'entertainment': 60, 'shopping': 60, 'viewpoints': 30, 'unusual': 50}
BOX = (31.02, 121.24, 31.46, 121.79)  # Shanghai urban area and nearby districts


def query_text():
    pieces = []
    for key, pattern in FILTERS:
        pieces.append(f'  nwr["{key}"~"^({pattern})$"]["name"]({BOX[0]},{BOX[1]},{BOX[2]},{BOX[3]});')
    return '[out:json][timeout:175];\n(\n' + '\n'.join(pieces) + '\n);\nout center tags;'


def fetch_osm(timeout=195):
    payload = urllib.parse.urlencode({'data': query_text()}).encode()
    errors = []
    for endpoint in OVERPASS:
        try:
            request = urllib.request.Request(endpoint, payload, headers={
                'User-Agent': 'TripWise-POI-research/0.1 (educational use; OSM attribution)',
                'Accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded',
            })
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(45_000_000 + 1)
                if len(raw) > 45_000_000:
                    raise ValueError('OSM result exceeds 45 MB guard')
                result = json.loads(raw)
                if not isinstance(result.get('elements'), list):
                    raise ValueError('Overpass did not return an elements array')
                print(f'Overpass: {endpoint}; {len(result["elements"])} objects', file=sys.stderr)
                return result
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
            errors.append(f'{endpoint}: {error}')
    raise RuntimeError('Unable to fetch verifiable named OSM objects. No synthetic replacement was generated.\n' + '\n'.join(errors))


def first_category(tags):
    tourism = tags.get('tourism', '')
    amenity = tags.get('amenity', '')
    leisure = tags.get('leisure', '')
    shop = tags.get('shop', '')
    if amenity in ('restaurant', 'cafe', 'food_court'):
        return 'cafes'
    if tourism in ('museum', 'gallery'):
        return 'museums'
    if tags.get('historic') or amenity == 'place_of_worship':
        return 'history'
    if leisure in ('park', 'garden', 'nature_reserve'):
        return 'nature'
    if tourism == 'viewpoint':
        return 'viewpoints'
    if shop in ('mall', 'department_store'):
        return 'shopping'
    if tourism in ('zoo', 'aquarium', 'theme_park') or amenity in ('theatre', 'cinema', 'arts_centre') or leisure in ('amusement_arcade', 'sports_centre'):
        return 'entertainment'
    return 'unusual' if tourism in ('artwork', 'attraction') else None


def osm_poi(obj):
    if not isinstance(obj, dict) or obj.get('type') not in ('node', 'way', 'relation') or not isinstance(obj.get('id'), int):
        return None
    tags = obj.get('tags') or {}
    category = first_category(tags)
    names = [tags.get('name:ru'), tags.get('name:en'), tags.get('name')]
    name = next((s.strip() for s in names if isinstance(s, str) and len(s.strip()) >= 3), None)
    if not category or not name or len(name) > 100 or name.lower().startswith(('unnamed', '无名', '未命名')):
        return None
    centre = obj.get('center', {})
    lat, lon = obj.get('lat', centre.get('lat')), obj.get('lon', centre.get('lon'))
    if not isinstance(lat, (float, int)) or not isinstance(lon, (float, int)) or not (BOX[0] <= lat <= BOX[2] and BOX[1] <= lon <= BOX[3]):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    kind = 'restaurant' if category == 'cafes' else 'attraction'
    other = []
    if category == 'history' and tags.get('tourism') == 'museum': other.append('museums')
    if category == 'unusual' and tags.get('tourism') == 'artwork': other.append('museums')
    if category == 'viewpoints' and tags.get('natural'): other.append('nature')
    opening = {'open': '08:00' if kind == 'restaurant' else '09:00',
               'close': '22:00' if kind == 'restaurant' else '20:00', 'closed_weekdays': []}
    osm_type, osm_id = obj['type'], obj['id']
    # These are estimates, not prices, hours, popularity or verified accessibility.
    return {
        'id': f'osm:{osm_type}/{osm_id}', 'name': name, 'kind': kind,
        'categories': [category] + other, 'lat': round(float(lat), 7), 'lon': round(float(lon), 7),
        'duration_minutes': 60 if kind == 'restaurant' else (110 if category == 'museums' else 75),
        'price_cny': 60 if kind == 'restaurant' else 0,
        'rating': 4.2, 'review_count': 0,
        'opening': opening, 'area': tags.get('addr:district') or tags.get('addr:subdistrict') or 'Шанхай',
        'river_bank': 'east' if lon > 121.495 else 'west',
        'season_months': [], 'weekend_crowd_level': 'medium', 'weekday_crowd_level': 'low',
        'pet_friendly': False, 'family_friendly': True, 'senior_friendly': True,
        'price_multipliers': {'adult': 1, 'child': 0.65, 'senior': 0.9},
        'pet_fee_cny': 0,
        'description': f'Реальный объект по данным OpenStreetMap. Название и координаты взяты из OSM; время, цена, доступность, сезонность и рейтинг — демонстрационные допущения.',
        'source_url': f'https://www.openstreetmap.org/{osm_type}/{osm_id}',
        'coordinate_source_url': f'https://www.openstreetmap.org/{osm_type}/{osm_id}',
        'provenance': {'identity': 'osm_named_feature', 'coordinates': 'OSM_feature_geometry',
                       'rating': 'synthetic', 'review_count': 'synthetic', 'price': 'synthetic',
                       'opening': 'synthetic', 'duration': 'planning_estimate',
                       'river_bank': 'planning_assumption', 'seasonality': 'synthetic',
                       'crowd': 'synthetic', 'group_prices': 'synthetic'},
    }


def grid_key(p):
    return (int((p['lat'] - BOX[0]) / .02), int((p['lon'] - BOX[1]) / .02))


def compile_dataset(original, osm, target=500):
    if target != 500: raise ValueError('For this task use exactly 500 objects.')
    if not isinstance(original.get('pois'), list): raise ValueError('Original catalogue missing')
    seen_id, seen_name, output = set(), set(), []
    # Preserve the currently verified identities (even if no longer present in a snapshot).
    preserved = [copy.deepcopy(p) for p in original['pois']
                 if p.get('provenance', {}).get('coordinates') in ('OSM_feature_centroid_verified', 'OSM_feature_geometry')
                 and p.get('provenance', {}).get('identity') in ('real_place_source_checked', 'osm_named_feature')]
    for p in preserved:
        key = re.sub(r'\s+', ' ', p['name']).casefold().strip()
        if p['id'] not in seen_id and key not in seen_name:
            output.append(p); seen_id.add(p['id']); seen_name.add(key)
    candidates = []
    for elem in osm.get('elements', []):
        p = osm_poi(elem)
        if p and p['id'] not in seen_id:
            key = re.sub(r'\s+', ' ', p['name']).casefold().strip()
            if key not in seen_name: candidates.append(p)
    # Deterministic spatial diversity, not an unverified 'top 500' ranking.
    candidates.sort(key=lambda p: (p['id']))
    used = set()
    by_cat = {c: [] for c in CATEGORIES}
    for p in candidates:
        by_cat[p['categories'][0]].append(p)
    per_cell = Counter(grid_key(p) for p in output)
    def choose_from(pool):
        eligible = [p for p in pool if p['id'] not in used and p['id'] not in seen_id
                    and re.sub(r'\s+', ' ', p['name']).casefold().strip() not in seen_name]
        if not eligible: return False
        p = min(eligible, key=lambda p: (per_cell[grid_key(p)], p['id']))
        used.add(p['id']); seen_id.add(p['id'])
        seen_name.add(re.sub(r'\s+', ' ', p['name']).casefold().strip())
        output.append(p); per_cell[grid_key(p)] += 1
        return True
    # Quotas are for diversity, not factual quality or popularity.
    for c in CATEGORIES:
        existing = sum(c in p.get('categories', []) for p in output)
        for _ in range(max(0, QUOTA[c] - existing)):
            if len(output) >= target or not choose_from(by_cat[c]): break
    while len(output) < target and choose_from(candidates): pass
    if len(output) < target:
        raise ValueError(f'Only {len(output)} unique named real sites found, need 500. Input remains unchanged.')
    data = copy.deepcopy(original)
    data['pois'] = output[:target]
    data['description'] = ('500 уникальных именованных объектов Шанхая из OpenStreetMap и ранее проверенных записей. '
        'OSM-координаты — центры геометрии объектов (не обязательно входы). Цены, часы, рейтинг, '
        'скидки, доступность и сезонность — синтетические сценарии, не проверенные факты.')
    data['data_license'] = 'OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)'
    validate(data)
    return data


def validate(data):
    pois = data['pois']
    if len(pois) != 500: raise ValueError('Expected precisely 500 POIs')
    ids = [p['id'] for p in pois]
    names = [re.sub(r'\s+', ' ', p['name']).casefold().strip() for p in pois]
    if len(set(ids)) != 500 or len(set(names)) != 500: raise ValueError('Duplicate name or ID')
    for p in pois:
        if not (isinstance(p['name'], str) and p['name'].strip() and
                30.9 < p['lat'] < 31.5 and 121.1 < p['lon'] < 121.9 and
                p.get('coordinate_source_url') and p['coordinate_source_url'].startswith('https://')):
            raise ValueError('Invalid name, coordinate, or provenance: ' + str(p.get('id')))
        if not (p['provenance']['coordinates'] in ('OSM_feature_centroid_verified', 'OSM_feature_geometry')):
            raise ValueError('Unverified coordinate included: '+p['id'])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, default=DEFAULT_DATA)
    ap.add_argument('--output', type=Path, default=None, help='Default overwrites existing catalog ONLY after complete validation')
    ap.add_argument('--overpass-json', type=Path, help='Use previously downloaded Overpass response, no network (testing/replay)')
    ap.add_argument('--timeout', type=int, default=195)
    args = ap.parse_args(argv)
    source = args.input
    destination = args.output or source
    original = json.loads(source.read_text(encoding='utf-8-sig'))
    osm = json.loads(args.overpass_json.read_text(encoding='utf-8')) if args.overpass_json else fetch_osm(args.timeout)
    data = compile_dataset(original, osm)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'saved': str(destination), 'count': 500,
        'osm_derived': sum(p['id'].startswith('osm:') for p in data['pois']),
        'per_category': dict(Counter(p['categories'][0] for p in data['pois']))}, ensure_ascii=False))

if __name__ == '__main__': main()
