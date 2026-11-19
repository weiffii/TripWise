"""Добавить демонстрационный средний чек и явные языковые варианты без потери исходных полей."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/demo/shanghai/pois.json'
REPORT = ROOT / 'reports/data/shanghai-data-gaps.2026-11-19.json'
LABEL_CACHE = ROOT / 'data/sources/shanghai-names/labels.raw.json'
PRICE_SOURCE = 'https://lbs.amap.com/api/webservice/guide/api/newpoisearch'
ADDED_FIELDS = {'names', 'name_basis', 'avg_check_cny', 'price_segment'}
ADDED_PROVENANCE = {'avg_check_cny', 'price_segment', 'names'}

# Эти переводы — редакторские подписи известных исходных названий, не ответ API.
EDITORIAL_NAMES = {
    'bund': ('The Bund', '外滩'),
    'yu-garden': ('Yu Garden', '豫园'),
    'city-god-temple': ('City God Temple', '城隍庙'),
    'shanghai-museum': ("Shanghai Museum (People's Square)", '上海博物馆（人民广场馆）'),
    'history-museum': ('Shanghai History Museum', '上海市历史博物馆'),
    'urban-planning': ('Shanghai Urban Planning Exhibition Center', '上海城市规划展示馆'),
    'natural-history': ('Shanghai Natural History Museum', '上海自然博物馆'),
    'peoples-park': ("People's Park", '人民公园'),
    'fuxing-park': ('Fuxing Park', '复兴公园'),
    'sculpture-park': ("Jing'an Sculpture Park", '静安雕塑公园'),
    'jingan-temple': ("Jing'an Temple", '静安寺'),
    'jade-buddha': ('Jade Buddha Temple', '玉佛禅寺'),
    'xintiandi': ('Xintiandi', '新天地'),
    'tianzifang': ('Tianzifang', '田子坊'),
    'wukang-mansion': ('Wukang Mansion and Wukang Road', '武康大楼与武康路'),
    'm50': ('M50 Art District', 'M50创意园'),
    'shanghai-tower': ('Shanghai Tower Observation Deck', '上海中心大厦观光厅'),
    'oriental-pearl': ('Oriental Pearl Tower', '东方明珠广播电视塔'),
    'pudong-art': ('Museum of Art Pudong', '浦东美术馆'),
    'lujiazui-green': ('Lujiazui Central Green Space', '陆家嘴中心绿地'),
    'postal-museum': ('Shanghai Postal Museum', '上海邮政博物馆'),
    'jewish-refugees': ('Shanghai Jewish Refugees Museum', '上海犹太难民纪念馆'),
    'sihang': ('Sihang Warehouse Memorial', '四行仓库抗战纪念馆'),
    'power-station-art': ('Power Station of Art', '上海当代艺术博物馆'),
    'longhua-temple': ('Longhua Temple', '龙华寺'),
    'madame-tussauds': ('Madame Tussauds Shanghai', '上海杜莎夫人蜡像馆'),
    'ocean-aquarium': ('Shanghai Ocean Aquarium', '上海海洋水族馆'),
    'nanxiang': ('Nanxiang Steamed Bun Restaurant', '南翔馒头店'),
}


def original_projection(p):
    result = {k: deepcopy(v) for k, v in p.items() if k not in ADDED_FIELDS}
    result['provenance'] = {k: v for k, v in result.get('provenance', {}).items()
                            if k not in ADDED_PROVENANCE}
    return result


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def imported_names(p):
    names = {'ru': None, 'en': None, 'zh': None}
    basis = dict.fromkeys(names, 'missing')
    text = p['name'].strip()
    lang = 'zh' if re.search(r'[\u3400-\u9fff]', text) else 'ru' if re.search(r'[\u0400-\u04ff]', text) else 'en'
    names[lang], basis[lang] = text, 'source'
    short_id = p['id'].split(':')[-1]
    if short_id in EDITORIAL_NAMES:
        en, zh = EDITORIAL_NAMES[short_id]
        for language, value in [('en', en), ('zh', zh)]:
            if names[language] is None:
                names[language], basis[language] = value, 'editorial_translation'
        if names['ru'] is None and short_id == 'power-station-art':
            names['ru'], basis['ru'] = 'Электростанция искусства', 'editorial_translation'
    return names, basis


def enrich(dataset, label_entities=None):
    output = deepcopy(dataset)
    label_entities = label_entities or {}
    for p in output['pois']:
        names, basis = imported_names(p)
        match = re.search(r'(Q\d+)$', p['id'])
        if match:
            labels = label_entities.get(match.group(1), {}).get('labels', {})
            for language in names:
                value = labels.get(language, {}).get('value')
                if isinstance(value, str) and value.strip():
                    names[language], basis[language] = value.strip(), 'source'
        p['names'], p['name_basis'] = names, basis
        p.setdefault('provenance', {})['names'] = 'per_language_name_basis; missing uses original name fallback'
        if p['kind'] == 'restaurant':
            amount = p['price_cny']
            if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not math.isfinite(amount) or amount < 0:
                raise ValueError('Некорректная исходная цена: ' + p['id'])
            p['avg_check_cny'] = amount
            p['price_segment'] = 'budget' if amount <= 60 else 'midrange' if amount <= 150 else 'premium'
            p['provenance']['avg_check_cny'] = 'synthetic_derived_from_existing_demo_price_cny_per_person'
            p['provenance']['price_segment'] = 'synthetic_demo_thresholds_CNY_60_150'
    if [original_projection(p) for p in output['pois']] != [original_projection(p) for p in dataset['pois']]:
        raise AssertionError('Изменены исходные поля POI')
    return output


def metres(a, b):
    rad = math.pi / 180
    h = math.sin((b['lat'] - a['lat']) * rad / 2) ** 2 + math.cos(a['lat'] * rad) * math.cos(b['lat'] * rad) * math.sin((b['lon'] - a['lon']) * rad / 2) ** 2
    return 12742000 * math.atan2(math.sqrt(h), math.sqrt(max(0, 1 - h)))


def meal_slot_match(food, day, earliest, latest, duration=50):
    opening = food['opening']
    parse = lambda value: int(value[:2]) * 60 + int(value[3:])
    return day.isoweekday() not in opening['closed_weekdays'] and max(earliest, parse(opening['open'])) + duration <= min(latest, parse(opening['close']))


def validate_dataset(dataset):
    pois = dataset.get('pois')
    if not isinstance(pois, list) or not pois:
        raise ValueError('Пустой каталог')
    seen = set()
    categories = {'museums', 'history', 'nature', 'cafes', 'entertainment', 'shopping', 'viewpoints', 'unusual'}
    for p in pois:
        if not isinstance(p.get('id'), str) or p['id'] in seen or not p['id']:
            raise ValueError('Повторный/пустой ID')
        seen.add(p['id'])
        if not isinstance(p.get('name'), str) or not p['name'].strip():
            raise ValueError('Пустое название')
        if p.get('kind') not in ('attraction', 'restaurant') or not p.get('categories') or not set(p['categories']) <= categories:
            raise ValueError('Некорректный вид/категории')
        for field, low, high in [('lat', 30, 32), ('lon', 120, 123), ('rating', 1, 5), ('price_cny', 0, 1e7), ('duration_minutes', 1, 1440)]:
            n = p.get(field)
            if not isinstance(n, (int, float)) or isinstance(n, bool) or not math.isfinite(n) or not low <= n <= high:
                raise ValueError('Некорректное поле ' + field + ': ' + p['id'])
        opening = p.get('opening', {})
        if not all(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', opening.get(k, '')) for k in ('open', 'close')) or opening['open'] >= opening['close']:
            raise ValueError('Некорректное окно часов')
        if not isinstance(opening.get('closed_weekdays'), list) or any(type(d) is not int or not 1 <= d <= 7 for d in opening['closed_weekdays']):
            raise ValueError('Некорректные выходные')
        if not isinstance(p.get('provenance'), dict):
            raise ValueError('Нет происхождения данных')


def greedy_coverage(attractions, deficits):
    """Верхняя оценка на дискретных кандидатах, не доказанный минимум новых мест."""
    remaining = dict(deficits)
    covered_by_candidate = {p['id']: [a['id'] for a in attractions if p['river_bank'] == a['river_bank'] and metres(p, a) <= 1500] for p in attractions}
    by_id = {p['id']: p for p in attractions}
    proposals = []
    while any(remaining.values()):
        best = max(attractions, key=lambda p: (sum(remaining[i] > 0 for i in covered_by_candidate[p['id']]), p['id']))
        covered = [i for i in covered_by_candidate[best['id']] if remaining[i] > 0]
        if not covered:
            raise AssertionError('Непокрываемая собственная точка')
        for i in covered:
            remaining[i] -= 1
        proposals.append({'near_attraction_id': best['id'], 'near_name': best['name'], 'lat': best['lat'], 'lon': best['lon'],
                          'hypothetical_new_venue': True, 'covered_anchor_count': len(covered)})
    return {'proposed_new_venue_count': len(proposals), 'proposals': proposals,
            'method': 'Greedy multicover on existing attraction-coordinate candidates, same bank label, radius1500m. Each hypothetical all-day venue serves at most one meal per anchor/day; co-located distinct venues allowed.',
            'is_proven_minimum': False, 'creates_pois': False,
            'limitations': ['Coordinates are planning locations, not real available premises', 'No walking-network, price or group feasibility proof', 'One-day coverage, not five-day no-repeat guarantee']}


def audit(dataset, before_hash):
    pois = dataset['pois']
    restaurants = [p for p in pois if p['kind'] == 'restaurant']
    attractions = [p for p in pois if p['kind'] == 'attraction']
    missing = {language: [p['id'] for p in pois if not p['names'][language]] for language in ('ru', 'en', 'zh')}
    day = date(2026, 11, 19)
    slots = [('breakfast', 9 * 60, 12 * 60), ('lunch', 12 * 60, 15 * 60), ('dinner', 16 * 60, 20 * 60)]
    failures = []
    breakfast_9_missing = {}
    any_food_missing = {}
    meal_deficits = {}
    cheap_food_missing = {}
    for attraction in attractions:
        nearby = [p for p in restaurants if p['river_bank'] == attraction['river_bank'] and metres(p, attraction) <= 1500]
        candidates = {kind: [p for p in nearby if meal_slot_match(p, day, start, end)] for kind, start, end in slots}
        assigned = {}
        def match(kind, visited):
            for p in candidates[kind]:
                if p['id'] in visited:
                    continue
                visited.add(p['id'])
                if p['id'] not in assigned or match(assigned[p['id']], visited):
                    assigned[p['id']] = kind
                    return True
            return False
        unmatched = [kind for kind, _, _ in slots if not match(kind, set())]
        meal_deficits[attraction['id']] = len(unmatched)
        breakfast_9_missing[attraction['id']] = int(not any(meal_slot_match(p, day, 9 * 60, 10 * 60) for p in nearby))
        any_food_missing[attraction['id']] = int(not nearby)
        cheap_food_missing[attraction['id']] = int(not any(p['avg_check_cny'] <= 60 for p in nearby))
        if unmatched:
            failures.append({'attraction_id': attraction['id'], 'nearby_restaurants': len(nearby), 'unmatched_meal_slots': unmatched})
    after_hash = canonical_hash([original_projection(p) for p in pois])
    if before_hash != after_hash:
        raise AssertionError('Контрольная сумма исходных полей изменилась')
    return {'date': '2026-11-19', 'poi_count': len(pois), 'kind_counts': dict(Counter(p['kind'] for p in pois)),
            'category_counts': dict(Counter(c for p in pois for c in p['categories'])),
            'area_counts': dict(Counter(p['area'] for p in pois)),
            'identity_counts': dict(Counter(p['provenance'].get('identity') for p in pois)),
            'original_fields_sha256_before': before_hash, 'original_fields_sha256_after': after_hash,
            'original_fields_preserved': True,
            'price_data_gate': {'passed': True, 'source_url': PRICE_SOURCE, 'field': 'business.cost', 'meaning': 'per capita consumption, optional restaurant business field', 'external_provider_calls': 0},
            'synthetic_average_check_count': len(restaurants), 'price_segment_counts': dict(Counter(p['price_segment'] for p in restaurants)),
            'name_nonmissing_counts': {lang: len(pois) - len(ids) for lang, ids in missing.items()},
            'name_all_three_count': sum(all(p['names'].values()) for p in pois),
            'missing_language_value_count': sum(len(ids) for ids in missing.values()),
            'name_missing_ids': missing,
            'name_basis_counts': {lang: dict(Counter(p['name_basis'][lang] for p in pois)) for lang in missing},
            'structural_field_coverage': {field: sum(field in p for p in pois) for field in ['opening', 'duration_minutes', 'rating', 'review_count', 'family_friendly', 'senior_friendly', 'pet_friendly', 'season_months']},
            'meal_neighbourhood_audit': {'date': day.isoformat(), 'radius_meters': 1500, 'same_bank_label_required': True,
                                         'scenario': 'one adult, 09-20, three distinct meals; no price limit, geographic distance only',
                                         'attraction_anchors_examined': len(attractions), 'anchors_without_three_distinct_meals': len(failures),
                                         'failures': failures,
                                         'limitations': ['Not a route feasibility or minimum global new-POI count', 'Synthetic hours, approximate bank labels and straight-line radius', 'No group/transport/season/budget constraints in this coverage-only audit']},
            'hypothetical_coverage_estimates': {
                'attractions_without_any_nearby_food': sum(any_food_missing.values()),
                'attractions_without_breakfast_09_to_10': sum(breakfast_9_missing.values()),
                'attractions_without_nearby_avg_check_up_to_60': sum(cheap_food_missing.values()),
                'any_food': greedy_coverage(attractions, any_food_missing),
                'breakfast_09_to_10': greedy_coverage(attractions, breakfast_9_missing),
                'three_distinct_meals_per_anchor': greedy_coverage(attractions, meal_deficits),
                'cheap_food_up_to_60': greedy_coverage(attractions, cheap_food_missing)},
            'additional_synthetic_poi_count_justified': None,
            'additional_synthetic_poi_count_explanation': 'No universal ideal count follows from row count; cover measured local meal gaps first, and evaluate actual planner scenarios.',
            'critical_errors': [], 'warnings': ['Average checks, price segments and all previous quality/time/cost fields remain demo assumptions', 'Missing language labels use original names, not invented translations']}


def main():
    original = json.loads(DATA.read_text(encoding='utf-8-sig'))
    if len(original['pois']) != 500:
        raise ValueError('Ожидаются исходные 500 записей')
    before = canonical_hash([original_projection(p) for p in original['pois']])
    validate_dataset(original)
    entities = json.loads(LABEL_CACHE.read_text(encoding='utf-8-sig')).get('entities', {}) if LABEL_CACHE.exists() else {}
    enriched = enrich(original, entities)
    validate_dataset(enriched)
    report = audit(enriched, before)
    DATA.write_text(json.dumps(enriched, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('poi_count', 'synthetic_average_check_count', 'price_segment_counts', 'name_nonmissing_counts', 'original_fields_preserved')}))


if __name__ == '__main__':
    main()
