"""Валидация opt-in Routing 0.2 без изменения контрактов и POI 0.1."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from ai_ds.data import nonempty, number
from ai_ds.recommendation import utc_microseconds
SCHEMA_VERSION = '0.2'
PACE_POLICY_VERSION = 'pace-policy-v0.2'
PACE_POLICIES = {'relaxed': (55, 1200), 'balanced': (70, 900), 'intensive': (85, 600)}

class RoutingV02Error(ValueError):
    def __init__(self, path, code='INVALID_VALUE', message=None):
        self.details = [{'path': path, 'code': code}]
        if message is not None:
            self.details[0]['message'] = message
        super().__init__(code)
    def response(self):
        return {'schema_version': SCHEMA_VERSION, 'error': {'code': 'ROUTING_VALIDATION_ERROR', 'details': deepcopy(self.details)}}

def require(condition, path, code='INVALID_VALUE'):
    if not condition:
        raise RoutingV02Error(path, code)

def quantity_us(value, factor, path):
    require(number(value) and value >= 0, path)
    result = Fraction(value if type(value) is int else str(value)) * factor * 1000000
    require(result.denominator == 1, path, 'SUBMICROSECOND_PRECISION')
    return result.numerator

def seconds_us(value, path):
    return quantity_us(value, 1, path)

def aware_timestamp(value, path, *, zone=None, day=None):
    require(isinstance(value, str) and 'T' in value, path, 'EXPECTED_AWARE_TIMESTAMP')
    try:
        result = datetime.fromisoformat(value)
        require(result.utcoffset() is not None, path, 'EXPECTED_AWARE_TIMESTAMP')
        result.astimezone(timezone.utc)
        if zone is not None:
            local = result.astimezone(zone)
            require(local.replace(tzinfo=None) == result.replace(tzinfo=None) and local.utcoffset() == result.utcoffset(), path, 'DATE_OR_TIMEZONE_MISMATCH')
        if day is not None:
            require(result.date().isoformat() == day, path, 'DATE_OR_TIMEZONE_MISMATCH')
        return result
    except (ValueError, OverflowError) as error:
        if isinstance(error, RoutingV02Error):
            raise
        raise RoutingV02Error(path, 'INVALID_TIMESTAMP') from error

def _date(value, path):
    try:
        result = date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        result = None
    require(result is not None and result.isoformat() == value, path, 'INVALID_DATE')
    return result

def _url(value, path, *, https_only=False):
    require(isinstance(value, str) and not any(ord(c) < 33 or c.isspace() for c in value), path, 'INVALID_URL')
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in (('https',) if https_only else ('http', 'https')) and bool(parsed.hostname) and parsed.username is None and parsed.password is None and parsed.port != 0)
    except ValueError:
        valid = False
    require(valid, path, 'INVALID_HTTPS_URL' if https_only else 'INVALID_URL')

def _interval(value, path, zone=None, day=None):
    require(isinstance(value, dict), path, 'EXPECTED_OBJECT')
    start = aware_timestamp(value.get('start_at'), path + '.start_at', zone=zone, day=day)
    end = aware_timestamp(value.get('end_at'), path + '.end_at', zone=zone, day=day)
    require(utc_microseconds(start) < utc_microseconds(end), path, 'END_NOT_AFTER_START')
    return start, end

def _provenance(row, path, *, opening=False):
    if opening:
        require(nonempty(row.get('source_id')), path + '.source_id')
        aware_timestamp(row.get('retrieved_at'), path + '.retrieved_at')
        if row.get('verified_at') is not None:
            aware_timestamp(row['verified_at'], path + '.verified_at')
    else:
        require(isinstance(row.get('basis'), str) and row['basis'] in ('source', 'expert_estimate', 'test_fixture'), path + '.basis')
        aware_timestamp(row.get('recorded_at'), path + '.recorded_at')
    require('source_url' in row, path + '.source_url', 'MISSING_FIELD')
    if row['source_url'] is not None:
        _url(row['source_url'], path + '.source_url')

def _cost_context(value, dates, by_id):
    if value is None:
        return None
    path = 'routing_context.cost_context'
    require(isinstance(value, dict), path, 'EXPECTED_OBJECT')
    require(isinstance(value.get('currency'), str) and re.fullmatch('[A-Z]{3}', value['currency']) is not None, path + '.currency')
    require(type(value.get('coverage_complete')) is bool, path + '.coverage_complete')
    for name, key_field, valid_ids in (('visit_costs', 'poi_id', by_id), ('meal_costs', 'date', dates)):
        rows = value.get(name)
        require(isinstance(rows, list), path + '.' + name, 'EXPECTED_ARRAY')
        seen = set()
        for index, row in enumerate(rows):
            p = f'{path}.{name}[{index}]'
            require(isinstance(row, dict), p, 'EXPECTED_OBJECT')
            key = row.get(key_field)
            require(isinstance(key, str) and key in valid_ids, p + '.' + key_field, 'UNKNOWN_REFERENCE')
            require(key not in seen, p, 'DUPLICATE_COST')
            seen.add(key)
            require('amount' in row and (row['amount'] is None or (number(row['amount']) and row['amount'] >= 0)), p + '.amount')
            if 'currency' in row:
                require(isinstance(row['currency'], str) and re.fullmatch('[A-Z]{3}', row['currency']) is not None, p + '.currency')
            _provenance(row, p)
    return deepcopy(value)

def validate_context(context, preferences, by_id):
    """Нормализованные days/sidecars; профиль и POI ранее валидируются recommend."""
    require(isinstance(context, dict), 'routing_context', 'EXPECTED_OBJECT')
    for field, expected in (('schema_version', '0.2'), ('endpoint_policy', 'first_last_poi'), ('pace_policy_version', PACE_POLICY_VERSION), ('unknown_duration_policy', 'strict')):
        require(context.get(field) == expected, 'routing_context.' + field, 'UNSUPPORTED_VERSION_OR_POLICY')
    require(isinstance(by_id, dict), 'pois', 'EXPECTED_OBJECT')
    first, last = (_date(preferences.get(field), 'preferences.' + field) for field in ('date_start', 'date_end'))
    require(first <= last, 'preferences.date_end', 'END_BEFORE_START')
    require(isinstance(preferences.get('pace'), str) and preferences['pace'] in PACE_POLICIES, 'preferences.pace')
    try:
        zone = ZoneInfo(preferences['timezone'])
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError) as error:
        raise RoutingV02Error('preferences.timezone', 'TIMEZONE_UNAVAILABLE') from error
    rows = context.get('days')
    require(isinstance(rows, list), 'routing_context.days', 'EXPECTED_ARRAY')
    require(len(rows) == (last - first).days + 1, 'routing_context.days', 'DATE_RANGE_MISMATCH')
    dates = {(first + timedelta(days=i)).isoformat() for i in range(len(rows))}
    overrides = context.get('day_pace_overrides', {})
    require(isinstance(overrides, dict), 'routing_context.day_pace_overrides', 'EXPECTED_OBJECT')
    for day, pace in overrides.items():
        require(day in dates and isinstance(pace, str) and pace in PACE_POLICIES, 'routing_context.day_pace_overrides', 'INVALID_DAY_PACE_OVERRIDE')
    seen, days = set(), []
    for index, row in enumerate(rows):
        p = f'routing_context.days[{index}]'
        require(isinstance(row, dict), p, 'EXPECTED_OBJECT')
        day = row.get('date')
        require(isinstance(day, str) and day in dates and day not in seen, p + '.date', 'DATE_RANGE_MISMATCH')
        seen.add(day)
        require('pace' not in row, p + '.pace', 'USE_DAY_PACE_OVERRIDES')
        start = aware_timestamp(row.get('day_start'), p + '.day_start', zone=zone, day=day)
        end = aware_timestamp(row.get('day_end'), p + '.day_end', zone=zone, day=day)
        window = utc_microseconds(end) - utc_microseconds(start)
        require(window > 0, p + '.day_end', 'END_NOT_AFTER_START')
        blocked = []
        meal = row.get('meal_break')
        if meal is not None:
            require(isinstance(meal, dict) and meal.get('kind') == 'meal', p + '.meal_break')
            a, b = _interval(meal, p + '.meal_break', zone, day)
            blocked.append({'start': a, 'end': b, 'kind': 'meal'})
        intervals = row.get('blocked_intervals', [])
        require(isinstance(intervals, list), p + '.blocked_intervals', 'EXPECTED_ARRAY')
        for i, interval in enumerate(intervals):
            a, b = _interval(interval, f'{p}.blocked_intervals[{i}]', zone, day)
            blocked.append({'start': a, 'end': b, 'kind': 'blocked'})
        blocked.sort(key=lambda item: utc_microseconds(item['start']))
        previous = utc_microseconds(start)
        for item in blocked:
            a, b = utc_microseconds(item['start']), utc_microseconds(item['end'])
            require(previous <= a < b <= utc_microseconds(end), p, 'INVALID_BLOCKED_INTERVALS')
            previous = b
        available = window - sum(utc_microseconds(i['end']) - utc_microseconds(i['start']) for i in blocked)
        require(available > 0, p, 'NO_AVAILABLE_WINDOW')
        pace = overrides.get(day, preferences['pace'])
        alpha, rest = PACE_POLICIES[pace]
        active = available * alpha // 100
        if row.get('max_active_seconds') is not None:
            cap = seconds_us(row['max_active_seconds'], p + '.max_active_seconds')
            require(cap > 0, p + '.max_active_seconds')
            active = min(active, cap)
        require(active > 0, p, 'NO_AVAILABLE_WINDOW')
        days.append({'date': day, 'start': start, 'end': end, 'window_us': window, 'active_limit_us': active, 'rest_us': rest * 1000000, 'pace': pace, 'blocked': blocked})
    days.sort(key=lambda row: row['date'])
    durations = {key: None if poi['visit_duration_minutes'] is None else quantity_us(poi['visit_duration_minutes'], 60, 'pois.' + key + '.visit_duration_minutes') for key, poi in by_id.items()}
    estimated = {key for key, poi in by_id.items() if poi.get('visit_duration_basis') == 'expert_estimate'}
    duration_rows = context.get('duration_overrides', [])
    require(isinstance(duration_rows, list), 'routing_context.duration_overrides', 'EXPECTED_ARRAY')
    seen = set()
    for index, row in enumerate(duration_rows):
        p = f'routing_context.duration_overrides[{index}]'
        require(isinstance(row, dict), p, 'EXPECTED_OBJECT')
        key = row.get('poi_id')
        require(isinstance(key, str) and key in by_id, p + '.poi_id', 'UNKNOWN_POI_ID')
        require(key not in seen, p, 'DUPLICATE_DURATION')
        seen.add(key)
        require(row.get('basis') == 'expert_estimate', p + '.basis')
        for field in ('method_version', 'author'):
            require(nonempty(row.get(field)), p + '.' + field)
        aware_timestamp(row.get('recorded_at'), p + '.recorded_at')
        require('evidence_url' in row, p + '.evidence_url', 'MISSING_FIELD')
        if row['evidence_url'] is not None:
            _url(row['evidence_url'], p + '.evidence_url')
        duration = quantity_us(row.get('minutes'), 60, p + '.minutes')
        require(duration > 0, p + '.minutes')
        durations[key] = duration
        estimated.add(key)
    openings = {}
    opening_rows = context.get('opening_intervals', [])
    require(isinstance(opening_rows, list), 'routing_context.opening_intervals', 'EXPECTED_ARRAY')
    for index, row in enumerate(opening_rows):
        p = f'routing_context.opening_intervals[{index}]'
        require(isinstance(row, dict), p, 'EXPECTED_OBJECT')
        key, day = row.get('poi_id'), row.get('date')
        require(isinstance(key, str) and key in by_id, p + '.poi_id', 'UNKNOWN_POI_ID')
        require(isinstance(day, str) and day in dates, p + '.date', 'UNKNOWN_DATE')
        require((key, day) not in openings, p, 'DUPLICATE_OPENING_DAY')
        require(row.get('timezone') == preferences['timezone'], p + '.timezone', 'TIMEZONE_MISMATCH')
        _provenance(row, p, opening=True)
        intervals = row.get('intervals')
        require('intervals' in row and (intervals is None or isinstance(intervals, list)), p + '.intervals')
        if intervals is None:
            openings[key, day] = None
        else:
            parsed = [_interval(interval, f'{p}.intervals[{i}]', zone, day) for i, interval in enumerate(intervals)]
            parsed.sort(key=lambda item: utc_microseconds(item[0]))
            for left, right in zip(parsed, parsed[1:]):
                require(utc_microseconds(left[1]) <= utc_microseconds(right[0]), p, 'OVERLAPPING_OPENING_INTERVALS')
            openings[key, day] = parsed
    snapshot = context.get('poi_snapshot_id')
    require(snapshot is None or nonempty(snapshot), 'routing_context.poi_snapshot_id')
    return {'days': days, 'zone': zone, 'durations': durations, 'estimated_ids': estimated, 'openings': openings, 'cost_context': _cost_context(context.get('cost_context'), dates, by_id), 'by_id': by_id, 'poi_snapshot_id': snapshot, 'raw': deepcopy(context)}

def validate_matrix(matrix, preferences, by_id):
    """Направленная матрица; отсутствующее не превращается в нулевую длительность."""
    path = 'travel_times'
    require(isinstance(matrix, dict), path, 'EXPECTED_OBJECT')
    require(matrix.get('mode') == preferences.get('transport'), path + '.mode', 'MODE_MISMATCH')
    require(isinstance(matrix.get('basis'), str) and matrix['basis'] in ('test_matrix', 'routing_provider', 'geometric_estimate'), path + '.basis')
    require(nonempty(matrix.get('source_id')), path + '.source_id')
    retrieved = matrix.get('retrieved_at')
    if retrieved is not None:
        aware_timestamp(retrieved, path + '.retrieved_at')
    if matrix['basis'] == 'routing_provider':
        require(retrieved is not None, path + '.retrieved_at', 'MISSING_FIELD')
    validity = None if matrix.get('valid_for') is None else _interval(matrix['valid_for'], path + '.valid_for')
    rows = matrix.get('pairs')
    require(isinstance(rows, list), path + '.pairs', 'EXPECTED_ARRAY')
    result = {}
    for index, pair in enumerate(rows):
        p = f'{path}.pairs[{index}]'
        require(isinstance(pair, dict), p, 'EXPECTED_OBJECT')
        source, target = pair.get('from_poi_id'), pair.get('to_poi_id')
        require(isinstance(source, str) and source in by_id and isinstance(target, str) and target in by_id, p, 'UNKNOWN_POI_ID')
        require(source != target, p, 'SELF_TRANSITION')
        require((source, target) not in result, p, 'DUPLICATE_TRANSITION')
        status = pair.get('status')
        require(isinstance(status, str) and status in ('known', 'unknown', 'unreachable'), p + '.status')
        require('duration_seconds' in pair and 'distance_meters' in pair, p, 'MISSING_FIELD')
        duration, distance = pair['duration_seconds'], pair['distance_meters']
        if status == 'known':
            duration_us = seconds_us(duration, p + '.duration_seconds')
            require(distance is None or (number(distance) and distance >= 0), p + '.distance_meters')
        else:
            require(duration is None and distance is None, p, 'UNKNOWN_OR_UNREACHABLE_REQUIRES_NULL')
            duration_us = None
        geometry = pair.get('geometry')
        if geometry is not None:
            require(isinstance(geometry, dict) and geometry.get('type') == 'LineString', p + '.geometry')
            points = geometry.get('coordinates')
            require(isinstance(points, list) and len(points) >= 2, p + '.geometry.coordinates')
            for point in points:
                require(isinstance(point, list) and len(point) == 2 and number(point[0]) and -180 <= point[0] <= 180 and number(point[1]) and -90 <= point[1] <= 90, p + '.geometry.coordinates')
        url = pair.get('navigation_url')
        if url is not None:
            _url(url, p + '.navigation_url', https_only=True)
        normalized = deepcopy(pair)
        normalized.update(duration_us=duration_us, geometry=deepcopy(geometry), navigation_url=url, valid_for=validity, retrieved_at=retrieved, basis=matrix['basis'], source_id=matrix['source_id'])
        result[source, target] = normalized
    return result

def evaluate_budget(selected_ids, dates, preferences, context):
    """Известная сумма — нижняя граница, питание каждого дня проверяется отдельно."""
    budget = preferences.get('budget')
    if budget is None:
        return {'status': 'not_checked', 'known_total': None, 'currency': None, 'unknown_items': [], 'warnings': []}
    currency, cost = budget['currency'], context['cost_context']
    visits = {row['poi_id']: row for row in cost['visit_costs']} if cost else {}
    meals = {row['date']: row for row in cost['meal_costs']} if cost else {}
    total, unknown, warnings = Fraction(0), [], set()
    def add(row, item, default_currency):
        nonlocal total
        if row is None or row.get('amount') is None:
            unknown.append(item)
        elif row.get('currency', default_currency) != currency:
            unknown.append(item)
            warnings.add('CURRENCY_MISMATCH')
        else:
            value = row['amount']
            total += Fraction(value if type(value) is int else str(value))
            if row.get('basis') == 'expert_estimate':
                warnings.add('ESTIMATED_COST')
            elif row.get('basis') == 'test_fixture':
                warnings.add('TEST_COST_DATA')
    for key in sorted(set(selected_ids)):
        row = visits.get(key)
        if row is None:
            row = context['by_id'][key].get('estimated_cost')
            default = row.get('currency') if row else currency
        else:
            default = cost['currency']
        add(row, 'visit:' + key, default)
    for day in sorted(set(dates)):
        add(meals.get(day), 'meal:' + day, cost['currency'] if cost else currency)
    if not (cost and cost['coverage_complete']):
        unknown.append('scope:trip_food_and_entertainment')
    limit = Fraction(budget['amount'] if type(budget['amount']) is int else str(budget['amount']))
    status = 'failed' if total > limit else 'unknown' if unknown else 'passed'
    if total.denominator == 1:
        known_total = total.numerator
    else:
        whole, remainder = divmod(total.numerator, total.denominator)
        digits = []
        while remainder:
            digit, remainder = divmod(remainder * 10, total.denominator)
            digits.append(str(digit))
        known_total = str(whole) + '.' + ''.join(digits)
    return {'status': status, 'known_total': known_total, 'currency': currency, 'unknown_items': unknown, 'warnings': sorted(warnings)}
