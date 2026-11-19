"""Rebalance existing Shanghai demo points using recorded OSM-venue anchors.

Keeps all IDs and rows; repositions only 1500 appended POIs within a few metres
of existing 500 urban POI anchors. No external API is used. 79 former historical
sites become explicitly *synthetic* museum scenarios (dual-tagged history/museums).

The positions are plausible simulation anchors, NOT verified building footprints,
road clearance, venue entrances or claims that relocated named sites are real.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

ORIGINAL_COUNT = 500
TARGET_COUNT = 2000
NEW_MUSEUM_SCENARIOS = 79
# Lat/lon correspond to original 500 OSM/landmark locations around metro Shanghai.
# We never invent unanchored random GPS points around the city or in rivers.


def hashval(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode('utf-8')).digest()[:8], 'big')


def is_museum_candidate(p: dict) -> bool:
    zh = str(p.get('name_zh', p.get('names', {}).get('zh', '')))
    # Former buildings and residences may plausibly contain indoor exhibitions.
    # Avoid bridges, towers, outdoor ruins and other sites unsuitable for museums.
    is_building = any(key in zh for key in ('旧址', '故居', '会馆', '图书馆', '馆', '宅', '银行', '大楼'))
    is_implausible = any(key in zh for key in ('桥', '井', '河', '水塔', '古塔', '墓', '公园', '遗址', '雕像', '码头'))
    return 'history' in p['categories'] and is_building and not is_implausible


def area_name(anchor: dict) -> str:
    lat, lon = anchor['lat'], anchor['lon']
    if lat < 31.18:
        band = 'южный городской пояс'
    elif lat < 31.26:
        band = 'центральный городской пояс'
    else:
        band = 'северный городской пояс'
    if lon < 121.425:
        sector = 'запад'
    elif lon < 121.5:
        sector = 'центр'
    else:
        sector = 'восток'
    return f'Шанхай, {band}, {sector} (синтетическая привязка)'


def anchor_pool(p: dict, anchors: list[dict]) -> list[dict]:
    categories = set(p['categories'])
    if 'museums' in categories:
        group = {'museums', 'history', 'unusual', 'entertainment', 'shopping'}
    elif 'history' in categories:
        group = {'history', 'museums', 'unusual', 'entertainment', 'shopping'}
    elif 'nature' in categories:
        group = {'nature', 'unusual', 'viewpoints'}
    elif 'viewpoints' in categories:
        group = {'viewpoints', 'museums', 'unusual', 'history'}
    elif 'cafes' in categories or p.get('kind') == 'restaurant':
        group = {'cafes', 'shopping'}
    elif 'shopping' in categories:
        group = {'shopping', 'cafes', 'entertainment'}
    else:
        group = {'entertainment', 'unusual', 'shopping', 'history'}
    candidates = [a for a in anchors if group.intersection(a['categories'])]
    assert len(candidates) >= 10, (p['id'], group)
    return candidates


def offset_meters(center: dict, place_id: str, iteration: int = 0) -> tuple[float, float, float]:
    # Conservative 1.25-4.8m offset around a known venue marker. Such markers
    # plausibly lie inside a commercial/cultural site, but road topology is unknown.
    hv = hashval(place_id + ':jitter:' + str(iteration))
    radius = 1.25 + ((hv >> 16) % 355) / 100  # [1.25, 4.79] metres
    angle = 2 * math.pi * (hv % 1_000_003) / 1_000_003
    lat = center['lat'] + radius * math.sin(angle) / 111_320
    lon = center['lon'] + radius * math.cos(angle) / (111_320 * math.cos(math.radians(center['lat'])))
    return round(lat, 7), round(lon, 7), round(radius, 2)


def rebalance(data: dict) -> tuple[dict, list[dict]]:
    data = copy.deepcopy(data)
    poi = data['pois']
    if len(poi) != TARGET_COUNT or len({p['id'] for p in poi}) != TARGET_COUNT:
        raise ValueError('Expected 2000 uniquely identified Shanghai POIs.')
    originals, additions = poi[:ORIGINAL_COUNT], poi[ORIGINAL_COUNT:]
    if any('source_dataset' in p for p in originals):
        raise ValueError('The first 500 rows no longer match the pilot anchor set.')
    if any('source_dataset' not in p for p in additions):
        raise ValueError('The 1500 appended rows are not the expected dataset.')
    original_anchors = [(p['lat'], p['lon']) for p in originals]
    for p in originals:
        if not (30.85 < p['lat'] < 31.6 and 121.0 < p['lon'] < 122.0):
            raise ValueError(f'Unexpected original anchor outside Shanghai: {p["id"]}')

    # Deterministically pick former buildings; not bridges or monuments.
    museum_candidates = [p for p in additions if is_museum_candidate(p)]
    museum_candidates.sort(key=lambda x: (hashval('museum|' + x['id']), x['id']))
    if len(museum_candidates) < NEW_MUSEUM_SCENARIOS:
        raise ValueError(f'Only {len(museum_candidates)} potential historical buildings for museum scenarios')
    to_museum = {p['id'] for p in museum_candidates[:NEW_MUSEUM_SCENARIOS]}
    for p in additions:
        if p['id'] not in to_museum:
            continue
        p['categories'] = list(dict.fromkeys([*p['categories'], 'museums']))
        p['synthetic_museum'] = True
        p['museum_classification'] = 'synthetic_interpretive_exhibition_scenario'
        p['original_catalog_name_before_museum_scenario'] = p['name']
        p['original_catalog_names_before_museum_scenario'] = dict(p.get('names', {}))
        # A synthetic museum identity, explicitly NOT a claim of a real institution.
        # Keep the original heritage identity separately for audit/reversibility.
        short_name = p['name'].strip()[:48]
        number = p['id'].split('-h')[-1]
        p['name'] = 'Музей городской памяти «' + short_name + '» (демо)'
        p['names'] = {
            'ru': p['name'],
            'en': 'Shanghai City Memory Museum (demo ' + number + ')',
            'zh': '上海城市记忆展馆（演示' + number + '）',
        }
        p['name_ru'], p['name_en'], p['name_zh'] = p['names']['ru'], p['names']['en'], p['names']['zh']
        p['description'] = ('МУЗЕЙ — УЧЕБНЫЙ СЦЕНАРИЙ. Создана вымышленная музейно-'
                            'выставочная площадка по мотивам исторического объекта. '
                            'Название и новое местоположение синтетические; '
                            'существование реального музея не подразумевается.')
        p['provenance']['identity'] = 'synthetic_museum_scenario_inspired_by_catalog_site'
        p['duration_minutes'] = max(90, min(135, int(p.get('duration_minutes', 105))))
        p['opening'] = {'open': '09:00', 'close': '18:00', 'closed_weekdays': [0]}
        p['provenance']['museum_category'] = 'synthetic_demo_reclassification_not_verified'

    anchor_hits = Counter()
    anchor_cat_hits = Counter()
    cell_hits = Counter()
    occupied = {(round(p['lat'], 7), round(p['lon'], 7)) for p in originals}
    changed = []
    # First distribute scarcity categories (nature, viewpoints, museums), then history,
    # food and shopping. This reduces artificial piles in a single point.
    order = sorted(additions, key=lambda p: (
        0 if 'nature' in p['categories'] else
        1 if 'viewpoints' in p['categories'] else
        2 if 'museums' in p['categories'] else
        3 if 'history' in p['categories'] else
        4 if 'shopping' in p['categories'] else
        5 if 'cafes' in p['categories'] else 6,
        hashval('assignment:' + p['id'])
    ))
    for p in order:
        pool = anchor_pool(p, originals)
        key_group = ('museums' if 'museums' in p['categories'] else
                     'history' if 'history' in p['categories'] else
                     'cafes' if 'cafes' in p['categories'] else
                     'shopping' if 'shopping' in p['categories'] else
                     p['categories'][0])
        # Each real anchor corresponds to a plausible site, not a random road point.
        # Penalise reuse within category and crowding within 1km sectors.
        def cost(a):
            cell = (round(a['lat'] / .012), round(a['lon'] / .012))
            noise = hashval(p['id'] + ':anchor:' + a['id']) % 10000 / 10000
            return (anchor_cat_hits[(key_group, a['id'])] * 5.0
                    + anchor_hits[a['id']] * 2.5
                    + cell_hits[cell] * .12
                    + noise * .6)
        anchor = min(pool, key=cost)
        lat = lon = rad = None
        for attempt in range(120):
            lat, lon, rad = offset_meters(anchor, p['id'], attempt)
            if (lat, lon) not in occupied:
                break
        else:
            raise ValueError(f'Cannot find unique synthetic coordinate for {p["id"]}')
        occupied.add((lat, lon))
        old_lat, old_lon = p['lat'], p['lon']
        p['lat'], p['lon'] = lat, lon
        p['area'] = area_name(anchor)
        p['river_bank'] = anchor.get('river_bank', 'east' if lon >= 121.5 else 'west')
        p['original_coordinates_before_synthetic_relocation'] = {'lat':old_lat, 'lon':old_lon}
        p['original_coordinate_source_url'] = p.pop('coordinate_source_url', None)
        p['synthetic_location'] = {
            'method': 'offset_within_5m_of_existing_urban_POI_anchor',
            'anchor_id': anchor['id'],
            'anchor_lat': anchor['lat'],
            'anchor_lon': anchor['lon'],
            'distance_to_anchor_m': rad,
            'building_footprint_verified': False,
            'road_clearance_verified': False,
            'note': 'Demonstration-only urban placement: not a verified venue or entry point.',
        }
        p['coordinate_review'] = {
            'status': 'synthetic_near_existing_poi',
            'reason': 'Replaced artificial SW concentration with locations around existing pilot POIs; road and property geometry not checked.',
        }
        p['provenance']['coordinates'] = 'synthetic_near_existing_poi_anchor'
        p['provenance']['river_bank'] = 'copied_from_synthetic_anchor_area'
        if not p.get('synthetic_museum'):
            p['description'] = ('Синтетическое размещение для тестирования TripWise: название '
                                'основано на внешнем каталоге, координаты перенесены '
                                'к существующей городской POI-точке. Это НЕ проверенный '
                                'адрес, вход, режим работы или маршрут.')
        anchor_hits[anchor['id']] += 1
        anchor_cat_hits[(key_group, anchor['id'])] += 1
        cell_hits[(round(anchor['lat'] / .012), round(anchor['lon'] / .012))] += 1
        changed.append({'id': p['id'], 'name': p['name'], 'kind': p['kind'],
                        'categories': ','.join(p['categories']),
                        'old_lat': old_lat, 'old_lon': old_lon,
                        'new_lat': lat, 'new_lon': lon, 'anchor_id': anchor['id'],
                        'distance_m': rad, 'synthetic_museum': bool(p.get('synthetic_museum'))})

    if [(p['lat'],p['lon']) for p in originals] != original_anchors:
        raise AssertionError('Original 500 POI coordinates changed')
    if len(set((p['lat'], p['lon']) for p in additions)) != 1500:
        raise AssertionError('Duplicate generated positions')
    if not (len(to_museum) == NEW_MUSEUM_SCENARIOS and
            sum('museums' in p['categories'] for p in poi) == 90 and
            sum(p['kind']=='restaurant' for p in poi) == 850):
        raise AssertionError('Category or kind totals unexpectedly changed')
    assert all((31.08 <= p['lat'] <= 31.38 and 121.31 <= p['lon'] <= 121.64) for p in poi)
    data['description'] = (
        'Синтетический пилот Shanghai/TripWise: 500 исходных POI + 1500 перенесённых '
        'к реальным городским POI-ориентирам в пределах ~5 м. Не использовать для '
        'навигации, проверенных адресов, анализа дорожного покрытия или оценки '
        'наличия конкретного музея. 79 дополнительных музейных классификаций '
        'созданы искусственно как учебные сценарии.')
    data['synthetic_placement_notice'] = {
        'total':2000,'unchanged_original':500,'redistributed':1500,
        'new_synthetic_museum_scenarios':NEW_MUSEUM_SCENARIOS,
        'location_method':'near_existing_pilot_poi',
        'verified_off_roads':False,
        'verified_inside_buildings':False,
        'not_real_navigation':True,
    }
    return data, changed


def main():
    import csv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=Path('data/demo/shanghai/pois.json'))
    parser.add_argument('--output',type=Path,default=Path('data/demo/shanghai/pois.json'))
    parser.add_argument('--audit-csv',type=Path,default=Path('reports/data/shanghai_synthetic_relocation.csv'))
    args=parser.parse_args()
    src=json.loads(args.input.read_text(encoding='utf-8-sig'))
    # Avoid mixing transformations from an already patched file in repeated executions.
    if any('synthetic_location' in p for p in src['pois'][ORIGINAL_COUNT:]):
        raise SystemExit('Already generated; run from the ORIGINAL GitHub JSON, not the processed copy.')
    generated,changed=rebalance(src)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(generated,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    args.audit_csv.parent.mkdir(parents=True,exist_ok=True)
    with args.audit_csv.open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=changed[0].keys())
        writer.writeheader();writer.writerows(sorted(changed,key=lambda p:p['id']))
    print(json.dumps({'pois':len(generated['pois']), 'changed':len(changed),
                      'museums':sum('museums' in p['categories'] for p in generated['pois']),
                      'museum_synthetic':sum(p.get('synthetic_museum',False) for p in generated['pois']),
                      'output':str(args.output)},ensure_ascii=False))


if __name__=='__main__':
    main()
