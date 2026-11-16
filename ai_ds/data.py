"""Проверка локальных POI по контракту 0.1 без сетевых запросов."""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sys
from urllib.parse import urlparse

CATEGORIES = frozenset(('museums', 'history', 'nature', 'cafes',
                        'entertainment', 'shopping', 'viewpoints', 'unusual'))
FIELDS = ('id', 'name', 'country_code', 'city', 'coordinates', 'categories',
          'rating', 'popularity', 'visit_duration_minutes', 'visit_duration_basis',
          'estimated_cost', 'opening_hours', 'availability', 'provenance')
PROVENANCE_FIELDS = ('source_id', 'source_record_id', 'source_url',
                     'retrieved_at', 'verified_at', 'is_synthetic')


def number(value):
    """Boolean не является числовой характеристикой POI."""
    return type(value) in (int, float) and (type(value) is int or math.isfinite(value))


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def timestamp(value):
    if not isinstance(value, str) or 'T' not in value:
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def valid_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
        return parsed.scheme in ('http', 'https') and bool(parsed.netloc)
    except ValueError:
        return False


def validate_poi(poi):
    """Возвращает ошибки полей; вход не изменяется."""
    issues = []

    def check(ok, field, code='INVALID_VALUE'):
        if not ok:
            issues.append({'field': field, 'code': code})

    if not isinstance(poi, dict):
        return [{'field': '', 'code': 'EXPECTED_OBJECT'}]
    for field in FIELDS:
        check(field in poi, field, 'MISSING_FIELD')
    for field in ('id', 'name', 'city'):
        if field in poi:
            check(nonempty(poi[field]), field)
    if 'country_code' in poi:
        check(isinstance(poi['country_code'], str) and
              re.fullmatch('[A-Z]{2}', poi['country_code']) is not None, 'country_code')
    coordinates = poi.get('coordinates')
    if coordinates is not None:
        if not isinstance(coordinates, dict):
            check(False, 'coordinates', 'EXPECTED_OBJECT')
        else:
            for field, limit in (('latitude', 90), ('longitude', 180)):
                value = coordinates.get(field)
                check(number(value) and -limit <= value <= limit, 'coordinates.' + field)
    categories = poi.get('categories')
    check(isinstance(categories, list) and bool(categories) and
          all(isinstance(c, str) and c in CATEGORIES for c in categories) and
          len(set(categories)) == len(categories), 'categories')
    rating = poi.get('rating')
    if rating is not None:
        valid = isinstance(rating, dict) and all(number(rating.get(k))
                    for k in ('value', 'scale_min', 'scale_max'))
        check(valid and rating['scale_min'] < rating['scale_max'] and
              rating['scale_min'] <= rating['value'] <= rating['scale_max'], 'rating')
    popularity = poi.get('popularity')
    if popularity is not None:
        check(isinstance(popularity, dict) and number(popularity.get('value')) and
              popularity['value'] >= 0 and nonempty(popularity.get('metric')), 'popularity')
    duration = poi.get('visit_duration_minutes')
    basis = poi.get('visit_duration_basis')
    check((duration is None and basis is None) or
          (type(duration) is int and duration > 0 and
           basis in ('source', 'expert_estimate', 'test_fixture')), 'visit_duration_minutes')
    cost = poi.get('estimated_cost')
    if cost is not None:
        check(isinstance(cost, dict) and number(cost.get('amount')) and cost['amount'] >= 0 and
              isinstance(cost.get('currency'), str) and
              re.fullmatch('[A-Z]{3}', cost['currency']) is not None, 'estimated_cost')
    check(poi.get('opening_hours') is None, 'opening_hours')
    check(poi.get('availability') in ('available', 'unavailable', 'unknown'), 'availability')
    provenance = poi.get('provenance')
    if not isinstance(provenance, dict):
        check(False, 'provenance', 'EXPECTED_OBJECT')
    else:
        for field in PROVENANCE_FIELDS:
            check(field in provenance, 'provenance.' + field, 'MISSING_FIELD')
        for field in ('source_id', 'source_record_id'):
            check(nonempty(provenance.get(field)), 'provenance.' + field)
        check(type(provenance.get('is_synthetic')) is bool, 'provenance.is_synthetic')
        for field in ('retrieved_at', 'verified_at'):
            value = provenance.get(field)
            check(value is None or timestamp(value), 'provenance.' + field)
        if provenance.get('is_synthetic') is False:
            check(provenance.get('retrieved_at') is not None, 'provenance.retrieved_at')
        url = provenance.get('source_url')
        check(url is None or valid_url(url), 'provenance.source_url')
    return issues


def normalize_poi(poi):
    result = deepcopy(poi)
    if isinstance(result, dict):
        for field in ('id', 'name', 'city'):
            if isinstance(result.get(field), str):
                result[field] = result[field].strip()
        provenance = result.get('provenance')
        if isinstance(provenance, dict):
            for field in ('source_id', 'source_record_id'):
                if isinstance(provenance.get(field), str):
                    provenance[field] = provenance[field].strip()
    return result


def process_pois(records):
    """Отделяет структурные ошибки, сохраняет предупреждения отдельно от POI."""
    if not isinstance(records, list):
        raise ValueError('Вход должен быть массивом POI')
    normalized = [normalize_poi(p) for p in records]
    ids = Counter(p['id'] for p in normalized if isinstance(p, dict) and nonempty(p.get('id')))
    issues, accepted, rejected = [], [], set()

    def issue(index, field, code, severity):
        p = normalized[index]
        poi_id = p.get('id') if isinstance(p, dict) and isinstance(p.get('id'), str) else None
        issues.append(dict(index=index, poi_id=poi_id, field=field, code=code, severity=severity))

    for index, poi in enumerate(normalized):
        errors = validate_poi(poi)
        if isinstance(poi, dict) and nonempty(poi.get('id')) and ids[poi['id']] > 1:
            errors.append({'field': 'id', 'code': 'DUPLICATE_ID'})
        for error in errors:
            issue(index, error['field'], error['code'], 'error')
        if errors:
            rejected.add(index)
            continue
        accepted.append(poi)
        for field in ('coordinates', 'rating', 'popularity', 'visit_duration_minutes',
                      'estimated_cost', 'opening_hours'):
            if poi[field] is None:
                issue(index, field, 'UNKNOWN_VALUE', 'warning')
        if poi['availability'] != 'available':
            issue(index, 'availability', 'AVAILABILITY_' + poi['availability'].upper(), 'warning')
        if poi['provenance']['is_synthetic']:
            issue(index, 'provenance.is_synthetic', 'SYNTHETIC_DATA', 'info')
        if poi['provenance']['verified_at'] is None:
            issue(index, 'provenance.verified_at', 'NOT_VERIFIED', 'warning')

    sources, names = defaultdict(list), defaultdict(list)
    for index, poi in enumerate(normalized):
        if index in rejected:
            continue
        provenance = poi['provenance']
        sources[(provenance['source_id'], provenance['source_record_id'])].append(index)
        names[' '.join(poi['name'].casefold().split())].append(index)
    for groups, field, code in ((sources, 'provenance', 'SOURCE_RECORD_CONFLICT'),
                                (names, 'name', 'POSSIBLE_DUPLICATE_NAME')):
        for indices in groups.values():
            if len(indices) > 1:
                for index in indices:
                    issue(index, field, code, 'warning')
    source_ids = {p['provenance']['source_id'] for p in accepted}
    synthetic_flags = {p['provenance']['is_synthetic'] for p in accepted}
    if len(source_ids) > 1 or len(synthetic_flags) > 1:
        raise ValueError('В версии 0.1 нельзя смешивать источники или реальные и синтетические POI')
    warnings = {i['index'] for i in issues if i['severity'] == 'warning'}
    report = {
        'schema_version': '0.1',
        'summary': {'input_count': len(records), 'accepted_count': len(accepted),
                    'rejected_count': len(rejected), 'records_with_warnings': len(warnings),
                    'records_without_issues': len(records) - len({i['index'] for i in issues}),
                    'issue_count': len(issues)},
        'source_ids': sorted(source_ids),
        'data_kind': ('synthetic' if True in synthetic_flags else 'real') if accepted else 'empty',
        'issues': issues,
        'routing_without_coordinates': [p['id'] for p in accepted if p['coordinates'] is None],
        'limitations': ['Географическое соответствие городу не проверено',
                       'Часы работы, актуальность фактов и выполнимость маршрута не проверены',
                       'Пригодность признаков для модели ранжирования не оценена'],
    }
    return accepted, report


def reject_constant(value):
    raise ValueError('Недопустимая JSON-константа: ' + value)


def load_pois(path):
    with Path(path).open(encoding='utf-8') as stream:
        return json.load(stream, parse_constant=reject_constant)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args(argv)
    try:
        paths = [p.resolve() for p in (args.input, args.output, args.report)]
        if len(set(paths)) != 3:
            raise ValueError('Пути входа, результата и отчёта должны различаться')
        for path in (args.output, args.report):
            if path.exists() and not args.overwrite:
                raise ValueError('Файл уже существует: ' + str(path))
        accepted, report = process_pois(load_pois(args.input))
        report['checked_at'] = datetime.now().astimezone().isoformat()
        report['input_path'] = str(args.input)
        # Сериализация до записи: не создаём файлы при ошибке входа.
        payloads = [(args.output, json.dumps(accepted, ensure_ascii=False, allow_nan=False, indent=2)),
                    (args.report, json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))]
        for path, payload in payloads:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('w' if args.overwrite else 'x', encoding='utf-8') as stream:
                stream.write(payload + '\n')
        print(json.dumps(report['summary'], ensure_ascii=False))
        return 1 if report['summary']['rejected_count'] else 0
    except (OSError, ValueError, OverflowError, RecursionError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
