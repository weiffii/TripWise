"""Проекция Moscow V7 в контракт POI 0.1 без подстановки неизвестных фактов."""

import csv
from hashlib import sha256
import json
import re
from pathlib import Path

from ai_ds.data import valid_url, validate_poi


CATALOG = Path(__file__).resolve().parents[1] / 'data/demo/moscow/v7'


MODEL_VERSION = 'moscow-planning-estimates-v3'


def _food_type(row):
    """Определение класса еды, без предположений о реально проверенном меню."""
    tags = row.get('tags') or row.get('osm_tags') or {}
    if not isinstance(tags, dict):
        tags = {}
    source = str(row.get('source_type') or tags.get('amenity') or tags.get('shop') or '').lower()
    name = str(row.get('name') or '').casefold()
    brand = str(tags.get('brand') or '').casefold()
    if re.search(r'\b(?:дринкит|drinkit|one price coffee|cofix|кофикс|starbucks|stars coffee)\b',
                 f'{name} {brand}'):
        return 'coffee_shop'
    if source in ('pastry', 'confectionery', 'ice_cream') or re.search(r'\b(?:baskin[-\s]?robbins|баскин[-\s]?роббинс)\b', name):
        return 'dessert'
    for code, terms in (
        ('canteen', ('столовая', 'буфет', 'canteen')),
        ('food_court', ('фудкорт', 'фуд-корт', 'food court')),
        ('bakery', ('пекарня', 'булочная', 'хлебная мануфактура', 'bakery')),
        ('fast_food', ('fast_food', 'fast food', 'фастфуд', 'бургер', 'шаурма',
                       'burger king', 'kfc', 'ростикс', 'rostics', 'вкусно и точка',
                       'вкусно — и точка', 'вкусно - и точка')),
        ('coffee_shop', ('coffee_shop', 'coffee', 'кофейня', 'кофе с собой', 'coffee to go')),
        ('restaurant', ('restaurant', 'ресторан', 'бистро')),
        ('cafe', ('cafe', 'кафе')),
    ):
        if source == code or source in terms or any(term in name for term in terms if len(term) > 3 or term == 'kfc'):
            return code
    if source in ('bar', 'pub', 'nightclub'):
        return source
    return 'cafe'  # осторожное предположение, не подтверждённая классификация


def _meal_suitability(kind):
    # Значения - оценки пригодности, не доказательство наличия меню.
    return {
        'canteen': {'breakfast': 'possible', 'lunch': 'likely', 'dinner': 'possible'},
        'food_court': {'breakfast': 'unlikely', 'lunch': 'likely', 'dinner': 'likely'},
        'bakery': {'breakfast': 'likely', 'lunch': 'unlikely', 'dinner': 'unlikely'},
        'dessert': {'breakfast': 'unlikely', 'lunch': 'unlikely', 'dinner': 'unlikely'},
        'fast_food': {'breakfast': 'possible', 'lunch': 'likely', 'dinner': 'likely'},
        'coffee_shop': {'breakfast': 'possible', 'lunch': 'unlikely', 'dinner': 'unlikely'},
        'restaurant': {'breakfast': 'possible', 'lunch': 'likely', 'dinner': 'likely'},
        'cafe': {'breakfast': 'possible', 'lunch': 'possible', 'dinner': 'possible'},
        'bar': {'breakfast': 'unlikely', 'lunch': 'unlikely', 'dinner': 'possible'},
        'pub': {'breakfast': 'unlikely', 'lunch': 'possible', 'dinner': 'possible'},
        'nightclub': {'breakfast': 'unlikely', 'lunch': 'unlikely', 'dinner': 'unlikely'},
    }[kind]


def _visit_type(row, category):
    """Separate a full shopping mall or museum from a small shop or monument.

    Labels and OpenStreetMap tags are *signals*, not verified visit durations.
    This runs only on the normalized planning layer, never mutating Moscow V7.
    """
    tags = row.get('tags') or row.get('osm_tags') or {}
    if not isinstance(tags, dict):
        tags = {}
    name = str(row.get('name') or '').casefold()
    source = str(row.get('source_type') or '').casefold()
    categories = set(row.get('categories') or ())
    mall_tags = (str(tags.get('shop', '')).casefold() in {'mall', 'department_store'} or
                 str(tags.get('building', '')).casefold() in {'retail', 'shopping_mall'} or
                 source in {'mall', 'shopping_mall', 'shopping_center', 'shopping_centre', 'department_store'})
    mall_name = bool(re.search(
        r'\b(?:трц|трк|тц|торговый\s+центр|торгово[-\s]?развлекательный\s+(?:центр|комплекс)|'
        r'торговый\s+комплекс|торговый\s+дом|молл|универмаг|гум|цум|mall|'
        r'shopping\s+(?:center|centre|mall)|department\s+store|аутлет|outlet)\b', name))
    # Some well-known centres are indexed only by their brand name. These
    # hints are used solely when a POI is already classified as shopping.
    known_large_malls = bool(re.search(
        r'\b(?:авиапарк|афимолл|метрополис|океания|саларис|мега|вегас|'
        r'европейский|ривьера|белая\s+дача)\b', name))
    known_malls = known_large_malls or bool(re.search(
        r'\b(?:атриум|капитолий|охотный\s+ряд|цветной|цдм|галерея\s+москва|хорошо)\b', name))
    museum = ('museums' in categories or category == 'museums' or
              bool(re.search(r'\b(?:музей|музея|музее|музеи|museum|галерея|gallery|'
                             r'экспозиция|выставочный\s+центр)\b', name)) or
              source == 'museum' or tags.get('tourism') == 'museum')
    if museum:
        major = bool(re.search(r'\b(?:третьяковск\w*|эрмитаж|государственный\s+исторический\s+музей|'
                               r'пушкинский\s+музей|музей[-\s]?заповедник)\b', name))
        return 'large_museum' if major else 'museum'
    if category == 'shopping' or 'shopping' in categories:
        if mall_tags or mall_name or known_malls:
            area = row.get('source_geometry_area_ha')
            footprint_is_large = type(area) in (int, float) and area >= 4
            if known_large_malls or footprint_is_large:
                return 'large_shopping_mall'
            return 'shopping_mall'
        if re.search(r'\b(?:рынок|маркет|market|ярмарка|базар)\b', name):
            return 'market'
        return 'shop'
    if category == 'history':
        return 'historic_site'
    if category == 'nature':
        return 'park'
    if category == 'entertainment':
        return 'entertainment'
    if category == 'viewpoints':
        return 'viewpoint'
    if category == 'unusual':
        return 'unusual'
    return 'general'


def planning_fields(row):
    """Стабильные оценки со статусом происхождения; исходные факты не заменяем."""
    seed = int.from_bytes(sha256(str(row['id']).encode('utf-8')).digest()[:4], 'big')
    category = row.get('primary_category') or row['categories'][0]
    food_kind = _food_type(row) if category == 'cafes' else None
    # Не назначаем неизвестным объектам стоимость 0: бесплатность должна быть доказана.
    ranges = {
        'cafes': (35, 650), 'museums': (55, 450), 'nature': (40, 100),
        'history': (25, 250), 'entertainment': (55, 750),
        'shopping': (30, 250), 'viewpoints': (20, 250), 'unusual': (30, 300),
    }
    minutes, price = ranges.get(category, (30, 350))
    visit_type = None if category == 'cafes' else _visit_type(row, category)
    # Typical time actually spent inside the venue, before traveller pace is
    # applied. These are deterministic *estimates*, not opening-hour facts.
    # The old 25-50 minute estimates for museums and shopping centres were
    # unrealistically short and could create implausibly dense itineraries.
    visit_bases = {
        'large_museum': (150, 10),  # 2h30-3h15, major collection
        'museum': (90, 8),         # 1h30-2h05, regular exhibition
        'shopping_mall': (140, 7), # 2h20-2h50, adjusted by pace below
        'large_shopping_mall': (195, 7),  # 3h15-3h45, adjusted by pace below
        'market': (75, 6),
        'shop': (35, 6),
        'historic_site': (45, 7),
        'park': (70, 9),
        'entertainment': (80, 9),
        'viewpoint': (30, 6),
        'unusual': (55, 8),
        'general': (60, 7),
    }
    food_specs = {
        'canteen': (30, 400), 'food_court': (35, 500), 'bakery': (20, 260),
        'fast_food': (25, 400), 'coffee_shop': (25, 310), 'restaurant': (65, 1100), 'dessert': (20, 260),
        'cafe': (45, 650), 'bar': (55, 850), 'pub': (60, 850), 'nightclub': (90, 1400),
    }
    if food_kind:
        minutes, price = food_specs[food_kind]
    area = row.get('source_geometry_area_ha')
    if visit_type:
        base, steps = visit_bases[visit_type]
        area_bonus = 20 if visit_type == 'park' and isinstance(area, (int, float)) and area >= 20 else 0
        duration = base + 5 * (seed % steps) + area_bonus
    else:
        duration = minutes + 5 * (seed % 6)
    start = (8 if category in ('nature', 'cafes') else 10) + seed % 2
    end = (22 if category in ('cafes', 'entertainment') else 19) + seed % 3
    if visit_type in ('shopping_mall', 'large_shopping_mall'):
        # Synthetic opening window; do not imply that real mall hours were checked.
        start, end = 10, 22
    if food_kind == 'coffee_shop' or food_kind == 'bakery':
        start, end = 8, 20
    elif food_kind in ('bar', 'pub', 'nightclub'):
        start, end = 14, 23
    closed = {seed % 2} if category == 'museums' and seed % 4 else set()
    hours = [[f'{start:02}:00', f'{min(end, 23):02}:00'] if day not in closed else None for day in range(7)]
    price += 50 * (seed % 7)
    estimates = {'duration_minutes': duration, 'price_rub': price,
                 'opening_hours': hours,
                 'preferred_period': 'afternoon' if category == 'viewpoints' else 'daytime'}
    if visit_type:
        estimates['visit_type'] = visit_type
    if food_kind:
        estimates['average_check_rub'] = price
        estimates['food_type'] = food_kind
        estimates['meal_suitability'] = _meal_suitability(food_kind)
        estimates['price_segment'] = 'budget' if price <= 600 else 'midrange' if price <= 1200 else 'expensive'
    fields = {}
    for key, estimate in estimates.items():
        provided = row.get(key)
        if key == 'price_segment' and provided is None and row.get('price_category') is not None:
            provided = row['price_category']
        fields[key] = {
            'value': provided if provided is not None else estimate,
            'source_type': 'source' if provided is not None else 'synthetic',
            'estimation_method': None if provided is not None else 'category_type_area_stable_hash',
            'confidence': None if provided is not None else 'low',
            'model_version': MODEL_VERSION,
        }
    # A genuine explicit free-entry flag overrides only an unverified estimate.
    tags = row.get('tags') or row.get('osm_tags') or {}
    if isinstance(tags, dict) and (tags.get('fee') == 'no' or tags.get('charge') == '0') and row.get('price_rub') is None:
        fields['price_rub'].update(value=0, source_type='source', estimation_method=None, confidence=None)
    return fields


def load_moscow_v7(directory=CATALOG, *, poi_ids=None, planning=False):
    directory = Path(directory)
    raw = (directory / 'pois_real_master_expanded.json').read_bytes()
    review = (directory / 'museum_reclassification.csv').read_bytes()
    sources = (directory / 'sources.json').read_bytes()
    metadata = json.loads(sources)
    document = json.loads(raw)
    classifications = {row['id']: row['reason'] for row in csv.DictReader(review.decode('utf-8-sig').splitlines())}
    records = []
    selected = None if poi_ids is None else set(poi_ids)
    for row in document['pois']:
        if selected is not None and row['id'] not in selected:
            continue
        categories = row['categories']
        reason = classifications.get(row['id'])
        if reason == 'contemporary_art':
            categories = ['museums']
        elif reason == 'history' and 'museums' in categories:
            categories = list(dict.fromkeys([*categories, 'history']))
        osm = row['id'].startswith('osm:') or any(
            source.get('name') == 'OpenStreetMap via Geofabrik' for source in row.get('sources') or [])
        retrieved = (metadata['osm']['replication_timestamp_utc'] if osm else
                     document['retrieved_at'] + 'T00:00:00+00:00')
        source_url = next((source['url'] for source in row.get('sources') or []
                           if valid_url(source.get('url'))), None)
        item = {
            'id': row['id'], 'name': row['name'], 'city': 'Москва', 'country_code': 'RU',
            'coordinates': {'latitude': row['lat'], 'longitude': row['lon']}, 'categories': categories,
            'rating': None, 'popularity': None, 'visit_duration_minutes': None,
            'visit_duration_basis': None, 'estimated_cost': None, 'opening_hours': None,
            'availability': 'unknown',
            'provenance': {'source_id': 'moscow_v7',
                           'source_record_id': row['id'], 'source_url': source_url or (row['source'] if valid_url(row['source']) else None),
                           'retrieved_at': retrieved, 'verified_at': None, 'is_synthetic': False},
        }
        errors = validate_poi(item)
        if errors:
            raise ValueError(f'{row["id"]}: {errors}')
        if planning:
            item['planning'] = planning_fields(row)
            item['search_names'] = row.get('alternative_names') or []
            item['area'] = row.get('city_area')
            item['catalog_signals'] = {
                'multiple_sources': len(row.get('sources') or []) > 1,
                'has_hours': bool(row.get('opening_hours')),
                'verified_entrance': bool(row.get('entrance_verified') or row.get('verified_entrance')),
            }
        records.append(item)
    ids = {row['id'] for row in records}
    if len(ids) != len(records) or (selected is not None and selected != ids):
        raise ValueError('Дубли или неизвестные ID в Moscow V7.')
    digest = sha256(b'\0'.join(part.replace(b'\r\n', b'\n') for part in (raw, review, sources)))
    version = ':' + MODEL_VERSION if planning else ''
    return records, 'moscow-v7:' + digest.hexdigest() + version
