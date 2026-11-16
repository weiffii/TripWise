"""Однодневный exact-enumeration прототип; не подтверждает реальную навигацию."""

import argparse
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from itertools import permutations
import json
import sys
from zoneinfo import ZoneInfo

from ai_ds.data import load_pois, nonempty, number
from ai_ds.recommendation import RecommendationError, recommend, utc_microseconds

MODEL_VERSION = 'day-enumeration-v0.1'


class RoutingError(ValueError):
    def __init__(self, path, code, message=None):
        self.details = [{'path': path, 'code': code}]
        if message:
            self.details[0]['message'] = message
        super().__init__(code)

    def response(self):
        return {'schema_version': '0.1', 'error': {
            'code': 'ROUTING_VALIDATION_ERROR', 'details': self.details}}


def require(condition, path, code='INVALID_VALUE'):
    if not condition:
        raise RoutingError(path, code)


def seconds_us(value, path):
    require(number(value) and value >= 0, path)
    if type(value) is int:
        return value * 1_000_000
    # Десятичная строка float задаёт входную точность; Fraction не зависит
    # от глобального Decimal context и не округляет большие значения.
    micros = Fraction(str(value)) * 1_000_000
    require(micros.denominator == 1, path, 'SUBMICROSECOND_PRECISION')
    return micros.numerator


def validate_context(context, preferences):
    require(isinstance(context, dict), 'routing_context', 'EXPECTED_OBJECT')
    fields = ('day_start', 'day_end', 'endpoint_policy', 'declared_pace',
              'max_active_seconds', 'rest_after_visit_seconds')
    for field in fields:
        require(field in context, 'routing_context.' + field, 'MISSING_FIELD')
    require(preferences['date_start'] == preferences['date_end'], 'preferences',
            'ONLY_ONE_DAY_SUPPORTED')
    require(context['endpoint_policy'] == 'first_last_poi', 'routing_context.endpoint_policy')
    require(context['declared_pace'] == preferences['pace'], 'routing_context.declared_pace',
            'PACE_MISMATCH')
    zone = ZoneInfo(preferences['timezone'])
    times = []
    for field in ('day_start', 'day_end'):
        value = context[field]
        try:
            parsed = datetime.fromisoformat(value) if isinstance(value, str) and 'T' in value else None
            require(parsed is not None and parsed.utcoffset() is not None,
                    'routing_context.' + field, 'EXPECTED_AWARE_TIMESTAMP')
            local = parsed.astimezone(zone)
            require(parsed.date().isoformat() == preferences['date_start'] and
                    local.date() == parsed.date() and local.utcoffset() == parsed.utcoffset(),
                    'routing_context.' + field, 'DATE_OR_TIMEZONE_MISMATCH')
            parsed.astimezone(timezone.utc)
            times.append(parsed)
        except (ValueError, OverflowError) as error:
            if isinstance(error, RoutingError):
                raise
            raise RoutingError('routing_context.' + field, 'INVALID_TIMESTAMP') from error
    start, end = times
    window = utc_microseconds(end) - utc_microseconds(start)
    require(window > 0, 'routing_context.day_end', 'END_NOT_AFTER_START')
    active = seconds_us(context['max_active_seconds'], 'routing_context.max_active_seconds')
    rest = seconds_us(context['rest_after_visit_seconds'], 'routing_context.rest_after_visit_seconds')
    require(active > 0, 'routing_context.max_active_seconds')
    return start, zone, window, active, rest


def validate_matrix(matrix, preferences, by_id):
    require(isinstance(matrix, dict), 'travel_times', 'EXPECTED_OBJECT')
    for field in ('mode', 'basis', 'source_id', 'pairs'):
        require(field in matrix, 'travel_times.' + field, 'MISSING_FIELD')
    require(matrix['mode'] == preferences['transport'], 'travel_times.mode', 'MODE_MISMATCH')
    require(matrix['basis'] in ('test_matrix', 'routing_provider', 'geometric_estimate'),
            'travel_times.basis')
    require(nonempty(matrix['source_id']), 'travel_times.source_id')
    require(isinstance(matrix['pairs'], list), 'travel_times.pairs', 'EXPECTED_ARRAY')
    result = {}
    for index, pair in enumerate(matrix['pairs']):
        path = f'travel_times.pairs[{index}]'
        require(isinstance(pair, dict), path, 'EXPECTED_OBJECT')
        for field in ('from_poi_id', 'to_poi_id', 'status', 'duration_seconds', 'distance_meters'):
            require(field in pair, path + '.' + field, 'MISSING_FIELD')
        source, target = pair['from_poi_id'], pair['to_poi_id']
        require(nonempty(source) and nonempty(target), path, 'INVALID_POI_ID')
        require(source in by_id and target in by_id, path, 'UNKNOWN_POI_ID')
        require(source != target, path, 'SELF_TRANSITION')
        key = source, target
        require(key not in result, path, 'DUPLICATE_TRANSITION')
        require(pair['status'] in ('known', 'unknown', 'unreachable'), path + '.status')
        if pair['status'] == 'known':
            seconds_us(pair['duration_seconds'], path + '.duration_seconds')
            distance = pair['distance_meters']
            require(distance is None or (number(distance) and distance >= 0), path + '.distance_meters')
        else:
            require(pair['duration_seconds'] is None and pair['distance_meters'] is None,
                    path, 'UNKNOWN_OR_UNREACHABLE_REQUIRES_NULL')
        result[key] = pair
    return result


def build_route(preferences, pois, recommendation, travel_times, routing_context):
    """Полный POI-набор не изменяется; принимается валидный снимок ранжирования."""
    expected = recommend(preferences, pois)
    require(isinstance(recommendation, dict), 'recommendation', 'EXPECTED_OBJECT')
    for field in ('schema_version', 'model_version', 'required_poi_ids', 'excluded', 'constraint_checks'):
        require(recommendation.get(field) == expected[field], 'recommendation.' + field,
                'RECOMMENDATION_MISMATCH')
    items = recommendation.get('items')
    require(isinstance(items, list) and len(items) == len(expected['items']),
            'recommendation.items', 'INVALID_ITEM_COUNT')
    eligible = {p['id'].strip() for p in pois} - {p['poi_id'] for p in expected['excluded']}
    seen = set()
    reason_codes = {'PRIMARY_INTEREST_MATCH', 'SECONDARY_INTEREST_MATCH', 'RATING_SIGNAL',
                    'POPULARITY_SIGNAL', 'FRESHNESS_SIGNAL', 'FALLBACK_SELECTION'}
    for index, item in enumerate(items):
        path = f'recommendation.items[{index}]'
        require(isinstance(item, dict), path, 'EXPECTED_OBJECT')
        poi_id = item.get('poi_id')
        require(nonempty(poi_id) and poi_id in eligible and poi_id not in seen,
                path + '.poi_id', 'INVALID_OR_DUPLICATE_CANDIDATE')
        seen.add(poi_id)
        require(type(item.get('rank')) is int and item['rank'] == index + 1,
                path + '.rank', 'INVALID_RANK')
        require('score' in item and item['score'] is None, path + '.score')
        reasons = item.get('reasons')
        require(isinstance(reasons, list) and bool(reasons) and all(
            isinstance(reason, dict) and isinstance(reason.get('code'), str) and
            reason['code'] in reason_codes for reason in reasons), path + '.reasons')
        for reason in reasons:
            if reason['code'] in ('PRIMARY_INTEREST_MATCH', 'SECONDARY_INTEREST_MATCH'):
                categories = reason.get('categories')
                interest_field = ('primary_interests' if reason['code'] == 'PRIMARY_INTEREST_MATCH'
                                  else 'secondary_interests')
                poi = next(p for p in pois if p['id'].strip() == poi_id)
                matches = set(preferences[interest_field]).intersection(poi['categories'])
                require(isinstance(categories, list) and bool(categories) and
                        all(isinstance(category, str) for category in categories) and
                        len(set(categories)) == len(categories) and set(categories) == matches,
                        path + '.reasons.categories', 'INVALID_INTEREST_MATCH')
    warnings = recommendation.get('warnings')
    require(isinstance(warnings, list) and all(
        isinstance(w, dict) and nonempty(w.get('code')) and 'poi_id' in w and
        (w['poi_id'] is None or nonempty(w['poi_id'])) for w in warnings),
        'recommendation.warnings')
    by_id = {p['id'].strip(): p for p in pois}
    start, zone, window, active_limit, rest = validate_context(routing_context, preferences)
    matrix = validate_matrix(travel_times, preferences, by_id)
    ranked = [item['poi_id'] for item in recommendation['items']]
    required = set(preferences['required_poi_ids'])
    ids = sorted(set(ranked) | required)
    require(len(ids) <= 8, 'recommendation.items', 'CANDIDATE_LIMIT_EXCEEDED')
    durations = {i: None if by_id[i]['visit_duration_minutes'] is None
                 else by_id[i]['visit_duration_minutes'] * 60 * 1_000_000 for i in ids}
    best, best_key, best_travel = None, None, None
    unresolved = False
    for length in range(1, len(ids) + 1):
        for sequence in permutations(ids, length):
            selected = set(sequence)
            if not required <= selected:
                continue
            uncertain = any(durations[i] is None for i in sequence)
            active = sum(durations[i] for i in sequence if durations[i] is not None)
            travel = 0
            blocked = False
            for source, target in zip(sequence, sequence[1:]):
                pair = matrix.get((source, target))
                if pair is not None and pair['status'] == 'unreachable':
                    blocked = True
                    break
                if pair is None or pair['status'] == 'unknown':
                    uncertain = True
                else:
                    travel += seconds_us(pair['duration_seconds'], 'travel_times.duration_seconds')
            if blocked:
                continue
            active += travel
            elapsed = active + rest * (length - 1)
            # Известная часть — лишь нижняя граница, не подстановка неизвестного нулём.
            if active > active_limit or elapsed > window:
                continue
            if uncertain:
                unresolved = True
                continue
            key = tuple(i in selected for i in ranked)
            if (best is None or key > best_key or
                    (key == best_key and (travel < best_travel or
                     (travel == best_travel and sequence < best)))):
                best, best_key, best_travel = sequence, key, travel
    warnings = {'BUDGET_NOT_VERIFIED', 'OPENING_HOURS_NOT_VERIFIED',
                'HOTEL_TRANSFERS_NOT_INCLUDED', 'EXPLICIT_PACE_LIMITS',
                'MEAL_PLANNING_NOT_IMPLEMENTED'}
    if travel_times['basis'] == 'test_matrix':
        warnings.add('TEST_TRAVEL_TIMES')
    elif travel_times['basis'] == 'geometric_estimate':
        warnings.add('GEOMETRIC_TRAVEL_ESTIMATE')
    if any(by_id[i]['provenance']['is_synthetic'] for i in ids):
        warnings.add('SYNTHETIC_DATA')
    if unresolved:
        warnings.add('UNRESOLVED_CANDIDATE_DATA')
    if not ids:
        warnings.add('NO_CANDIDATES')
    if best is None:
        status = 'unknown' if unresolved or not ids else 'infeasible'
        time_check = 'unknown' if unresolved or not ids else 'failed'
        visits, legs, breaks = [], [], []
        warnings.add('NO_COMPLETE_SCHEDULE')
    else:
        status, time_check = 'unknown', 'passed'
        visits, legs, breaks = [], [], []
        current = 0

        def stamp(offset):
            return (start.astimezone(timezone.utc) + timedelta(microseconds=offset)).astimezone(zone).isoformat()

        for index, poi_id in enumerate(best):
            arrival = current
            current += durations[poi_id]
            visits.append({'poi_id': poi_id, 'start_at': stamp(arrival), 'end_at': stamp(current)})
            if by_id[poi_id]['coordinates'] is None:
                warnings.add('COORDINATES_UNKNOWN')
            if by_id[poi_id]['availability'] == 'unknown':
                warnings.add('AVAILABILITY_UNKNOWN')
            if index < len(best) - 1:
                if rest:
                    breaks.append({'start_at': stamp(current), 'end_at': stamp(current + rest)})
                    current += rest
                target = best[index + 1]
                pair = matrix[poi_id, target]
                leg = {k: pair[k] for k in ('from_poi_id', 'to_poi_id', 'duration_seconds', 'distance_meters')}
                leg.update({'basis': travel_times['basis'], 'source_id': travel_times['source_id'],
                            'geometry': None, 'navigation_url': None})
                legs.append(leg)
                if pair['distance_meters'] is None:
                    warnings.add('DISTANCE_UNKNOWN')
                current += seconds_us(pair['duration_seconds'], 'travel_times.duration_seconds')
    placed = {p['poi_id'] for p in visits}
    return {'schema_version': '0.1', 'model_version': MODEL_VERSION, 'status': status,
            'days': [{'date': preferences['date_start'], 'visits': visits, 'legs': legs, 'breaks': breaks}],
            'unplaced_required_poi_ids': sorted(required - placed), 'warnings': sorted(warnings),
            'constraint_checks': {'time': time_check, 'budget': 'not_checked',
                                  'opening_hours': 'unknown',
                                  'required_places': 'passed' if required <= placed else
                                  'unknown' if status == 'unknown' else 'failed'}}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='strict')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pois', required=True)
    parser.add_argument('--preferences', required=True)
    parser.add_argument('--scenario', required=True)
    args = parser.parse_args(argv)
    try:
        pois, preferences, scenario = (load_pois(p) for p in (args.pois, args.preferences, args.scenario))
        require(isinstance(scenario, dict), 'scenario', 'EXPECTED_OBJECT')
        for field in ('routing_context', 'travel_times'):
            require(field in scenario, 'scenario.' + field, 'MISSING_FIELD')
        recommendation = recommend(preferences, pois)
        result = build_route(preferences, pois, recommendation, scenario['travel_times'], scenario['routing_context'])
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except (RoutingError, RecommendationError) as error:
        print(json.dumps(error.response(), ensure_ascii=False), file=sys.stderr)
        return 1
    except (OSError, ValueError, OverflowError, RecursionError) as error:
        print(json.dumps({'schema_version': '0.1', 'error': {'code': 'INPUT_ERROR',
                         'details': [{'message': str(error)}]}}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
