"""Нормализация сохранённого OSM snapshot по контракту 0.1, без сети."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

from ai_ds.data import CATEGORIES, load_pois, nonempty, process_pois, timestamp, validate_poi
from ai_ds.sources.overpass import CITIES, LICENSE_URL, ROOT, admin_relation, query_path, validate_document

MAPPING_PATH = ROOT / 'data/mappings/osm_categories.v0.1.json'


def read_snapshot(directory):
    """Проверить provenance и связь query/response с metadata до нормализации."""
    directory = Path(directory)
    metadata = load_pois(directory / 'metadata.json')
    if not isinstance(metadata, dict):
        raise ValueError('metadata должен быть объектом')
    city = metadata.get('city_key')
    if city not in CITIES or metadata.get('city') != CITIES[city]['name']:
        raise ValueError('Город snapshot не подтверждён')
    if (metadata.get('source_id') != 'openstreetmap' or metadata.get('is_synthetic') is not False or
            metadata.get('country_code') != 'RU' or metadata.get('license') != 'ODbL-1.0' or
            metadata.get('selection_basis') != 'overpass_map_to_area_from_verified_admin_relation' or
            not timestamp(metadata.get('retrieved_at'))):
        raise ValueError('Неверный provenance snapshot')
    body = (directory / 'response.json').read_bytes()
    query = (directory / 'query.overpassql').read_text(encoding='utf-8')
    if (hashlib.sha256(body).hexdigest() != metadata.get('response_sha256') or
            hashlib.sha256(query.encode('utf-8')).hexdigest() != metadata.get('query_sha256')):
        raise ValueError('Snapshot не соответствует контрольным суммам')
    if query != query_path(city).read_text(encoding='utf-8'):
        raise ValueError('Snapshot использует неподтверждённый запрос отбора')
    document = load_pois(directory / 'response.json')
    validate_document(document)
    relation = admin_relation(document, city)
    if (metadata.get('admin_relation_id') != relation['id'] or
            metadata.get('admin_tags') != relation['tags']):
        raise ValueError('Административная relation не соответствует metadata')
    return document, metadata


def categories_from_tags(tags, mapping):
    categories = set()
    for rule in mapping['rules']:
        if tags.get(rule['key']) in rule['values']:
            if rule['category'] not in CATEGORIES:
                raise ValueError('Неизвестная категория mapping')
            categories.add(rule['category'])
    return sorted(categories)


def normalize_snapshot(document, metadata, *, limit=24, mapping=None):
    """Преобразовать проверенный snapshot; тесты передают явно искусственный документ."""
    if type(limit) is not int or not 1 <= limit <= 24:
        raise ValueError('limit должен быть целым числом от 1 до 24')
    mapping = mapping if mapping is not None else load_pois(MAPPING_PATH)
    if mapping.get('source') != 'openstreetmap' or mapping.get('schema_version') != '0.1':
        raise ValueError('Неподдерживаемый mapping')
    city = metadata['city_key']
    relation = admin_relation(document, city)
    seen, candidates, rejected, geometry = {}, [], [], {}
    for index, element in enumerate(document['elements']):
        if element.get('type') == 'relation' and element.get('id') == relation['id']:
            continue
        kind, osm_id = element.get('type'), element.get('id')
        if kind not in ('node', 'way', 'relation') or type(osm_id) is not int or osm_id <= 0:
            rejected.append({'index': index, 'code': 'INVALID_OSM_ID'})
            continue
        key = f'{kind}/{osm_id}'
        if key in seen:
            # Один объект может попасть в несколько категориальных out-блоков.
            if seen[key] != element:
                raise ValueError('Конфликт повторных OSM объектов: ' + key)
            continue
        seen[key] = element
        tags = element.get('tags', {})
        if not isinstance(tags, dict):
            rejected.append({'osm_id': key, 'code': 'INVALID_TAGS'})
            continue
        name = tags.get('name:ru') or tags.get('name')
        categories = categories_from_tags(tags, mapping)
        if not nonempty(name) or not categories:
            rejected.append({'osm_id': key, 'code': 'MISSING_NAME_OR_UNMAPPED_CATEGORY'})
            continue
        point = element if kind == 'node' else element.get('center', {})
        if not isinstance(point, dict):
            point = {}
        coordinates = ({'latitude': point.get('lat'), 'longitude': point.get('lon')}
                       if 'lat' in point and 'lon' in point else None)
        poi = {
            'id': f'osm:{kind}:{osm_id}', 'name': name.strip(), 'country_code': 'RU',
            'city': CITIES[city]['name'], 'coordinates': coordinates, 'categories': categories,
            'rating': None, 'popularity': None, 'visit_duration_minutes': None,
            'visit_duration_basis': None, 'estimated_cost': None, 'opening_hours': None,
            'availability': 'unknown',
            'provenance': {'source_id': 'openstreetmap', 'source_record_id': key,
                           'source_url': f'https://www.openstreetmap.org/{key}',
                           'retrieved_at': metadata['retrieved_at'], 'verified_at': None,
                           'is_synthetic': False},
        }
        errors = validate_poi(poi)
        if errors:
            rejected.append({'osm_id': key, 'code': 'CONTRACT_VALIDATION_ERROR', 'details': errors})
            continue
        if coordinates is None:
            rejected.append({'osm_id': key, 'code': 'MISSING_COORDINATES_FOR_REAL_SELECTION'})
            continue
        candidates.append(poi)
        geometry[poi['id']] = {'basis': 'osm_node' if kind == 'node' else 'bounding_box_center',
                              'is_verified_entrance': False,
                              'osm_timestamp': element.get('timestamp'),
                              'osm_version': element.get('version')}
    # Детерминированный циклический отбор по реально представленным категориям;
    # это покрытие данных, а не рекомендательное ранжирование.
    candidates.sort(key=lambda p: p['id'])
    selected, selected_ids = [], set()
    groups = {category: [p for p in candidates if category in p['categories']]
              for category in sorted(CATEGORIES)}
    while len(selected) < limit:
        added = False
        for category, group in groups.items():
            while group and group[0]['id'] in selected_ids:
                group.pop(0)
            if group and len(selected) < limit:
                poi = group.pop(0)
                selected.append(poi)
                selected_ids.add(poi['id'])
                added = True
        if not added:
            break
    accepted, quality = process_pois(selected)
    if quality['summary']['rejected_count']:
        raise ValueError('Нормализованный результат нарушает контракт')
    counts = Counter(category for p in accepted for category in p['categories'])
    coverage = {
        'schema_version': '0.1', 'source_id': 'openstreetmap', 'is_synthetic': False,
        'city': CITIES[city]['name'], 'city_key': city, 'retrieved_at': metadata['retrieved_at'],
        'admin_relation_id': relation['id'], 'admin_tags': relation['tags'],
        'spatial_selection': metadata['selection_basis'],
        'raw_element_count': len(document['elements']), 'unique_poi_element_count': len(seen),
        'valid_candidate_count': len(candidates), 'selected_count': len(accepted), 'target_limit': limit,
        'category_counts': {c: counts[c] for c in sorted(CATEGORIES)},
        'rejected': rejected, 'geometry': {p['id']: geometry[p['id']] for p in accepted},
        'rating_matching_candidates': [{'poi_id': p['id'], 'name': p['name'],
                                        'source_url': p['provenance']['source_url']}
                                       for p in accepted[:10]],
        'license': 'ODbL-1.0', 'license_url': LICENSE_URL,
        'attribution': '© OpenStreetMap contributors',
        'limitations': ['Отбор по OSM admin area не подтверждает вход объекта',
                       'Центр bounding box way/relation не является подтверждённым входом',
                       'Часы работы сохранены только в raw; контракт содержит null',
                       'rating/popularity/price/duration неизвестны и оставлены null',
                       'Время правки OSM не является verified_at'],
    }
    return accepted, coverage


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=('openstreetmap',), default='openstreetmap')
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=24)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args(argv)
    try:
        paths = [args.output.resolve(), args.report.resolve()]
        raw_paths = [(args.snapshot / name).resolve() for name in
                     ('response.json', 'query.overpassql', 'metadata.json')]
        if len(set(paths)) != 2 or any(p in raw_paths for p in paths):
            raise ValueError('Выходы должны различаться и не перезаписывать raw snapshot')
        for path in paths:
            if path.exists() and not args.overwrite:
                raise ValueError('Файл уже существует: ' + str(path))
        document, metadata = read_snapshot(args.snapshot)
        accepted, report = normalize_snapshot(document, metadata, limit=args.limit)
        report['snapshot_path'] = str(args.snapshot)
        if not accepted:
            raise ValueError('Snapshot не содержит подходящих POI; реальный набор не создан')
        for path, value in ((args.output, accepted), (args.report, report)):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('w' if args.overwrite else 'x', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.write('\n')
        print(json.dumps({'selected_count': len(accepted), 'city': report['city']}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
