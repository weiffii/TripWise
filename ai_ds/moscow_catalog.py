"""Проекция Moscow V7 в контракт POI 0.1 без подстановки неизвестных фактов."""

import csv
from hashlib import sha256
import json
from pathlib import Path

from ai_ds.data import valid_url, validate_poi


CATALOG = Path(__file__).resolve().parents[1] / 'data/demo/moscow/v7'


MODEL_VERSION = 'moscow-planning-estimates-v1'


def planning_fields(row):
    seed = int.from_bytes(sha256(row['id'].encode()).digest()[:4], 'big')
    category = row.get('primary_category') or row['categories'][0]
    ranges = {
        'cafes': (35, 700), 'museums': (55, 450), 'nature': (40, 0),
        'history': (25, 250), 'entertainment': (55, 750),
        'shopping': (30, 500), 'viewpoints': (20, 250), 'unusual': (30, 300),
    }
    minutes, price = ranges.get(category, (30, 350))
    if row.get('source_type') == 'fast_food':
        minutes, price = 20, 400
    area = row.get('source_geometry_area_ha')
    duration = minutes + 5 * (seed % 6) + (20 if isinstance(area, (int, float)) and area >= 20 else 0)
    start = (8 if category in ('nature', 'cafes') else 10) + seed % 2
    end = (22 if category in ('cafes', 'entertainment') else 19) + seed % 3
    closed = {seed % 2} if category == 'museums' and seed % 4 else set()
    hours = [[f'{start:02}:00', f'{min(end, 23):02}:00'] if day not in closed else None for day in range(7)]
    estimates = {'duration_minutes': duration, 'price_rub': price + 50 * (seed % 7),
                 'opening_hours': hours, 'preferred_period': 'afternoon' if category == 'viewpoints' else 'daytime'}
    if category == 'cafes':
        estimates['average_check_rub'] = row.get('price_rub') if row.get('price_rub') is not None else estimates['price_rub']
    return {key: {'value': row.get(key) if row.get(key) is not None else estimate,
                  'source_type': 'source' if row.get(key) is not None else 'synthetic',
                  'estimation_method': None if row.get(key) is not None else 'category_type_area_stable_hash',
                  'confidence': None if row.get(key) is not None else 'low',
                  'model_version': MODEL_VERSION}
            for key, estimate in estimates.items()}


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
