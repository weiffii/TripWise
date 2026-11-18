"""Объяснимый baseline lexicographic-v0.1. Порядок не является маршрутом."""

from datetime import date, datetime, timezone
from fractions import Fraction
import re

from ai_ds.data import CATEGORIES, nonempty, number, process_pois

MODEL_VERSION = 'lexicographic-v0.1'


def normalized_rating(rating):
    """Та же линейная формула без переполнения промежуточных float."""
    value, low, high = (Fraction(rating[k]) for k in ('value', 'scale_min', 'scale_max'))
    return (value - low) / (high - low)


def utc_microseconds(value):
    """Точный ключ момента, не ограниченный годами представления UTC datetime."""
    offset = value.utcoffset()
    if offset is None:
        raise ValueError('Дата должна иметь часовой пояс')
    local = (((value.toordinal() * 24 + value.hour) * 60 + value.minute) * 60
             + value.second) * 1_000_000 + value.microsecond
    return local - ((offset.days * 86400 + offset.seconds) * 1_000_000
                    + offset.microseconds)


class RecommendationError(ValueError):
    def __init__(self, details):
        super().__init__('Ошибка входа рекомендаций')
        self.details = details

    def response(self):
        return {'schema_version': '0.1', 'error': {
            'code': 'VALIDATION_ERROR', 'details': self.details}}


def validate_preferences(profile):
    """Проверка профиля без сетевых запросов и изменения входа."""
    errors = []

    def check(ok, path, code='INVALID_VALUE'):
        if not ok:
            errors.append({'path': 'preferences.' + path, 'code': code})

    if not isinstance(profile, dict):
        return [{'path': 'preferences', 'code': 'EXPECTED_OBJECT'}]
    fields = ('schema_version', 'destination', 'date_start', 'date_end', 'timezone',
              'primary_interests', 'secondary_interests', 'pace', 'transport',
              'budget', 'required_poi_ids', 'top_k')
    for field in fields:
        check(field in profile, field, 'MISSING_FIELD')
    check(profile.get('schema_version') == '0.1', 'schema_version')
    destination = profile.get('destination')
    check(isinstance(destination, dict) and nonempty(destination.get('city')) and
          isinstance(destination.get('country_code'), str) and
          re.fullmatch('[A-Z]{2}', destination['country_code']) is not None, 'destination')
    dates = []
    for field in ('date_start', 'date_end'):
        value = profile.get(field)
        try:
            parsed = date.fromisoformat(value) if isinstance(value, str) else None
            valid = parsed is not None and parsed.isoformat() == value
        except ValueError:
            parsed, valid = None, False
        check(valid, field)
        dates.append(parsed if valid else None)
    if all(dates):
        check(dates[0] <= dates[1], 'date_end', 'END_BEFORE_START')
    # Проверка названия без обязательной установки tzdata на Windows.
    zone = profile.get('timezone')
    check(nonempty(zone), 'timezone')
    if nonempty(zone):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            check(False, 'timezone', 'TIMEZONE_UNAVAILABLE')
    interests = []
    for field in ('primary_interests', 'secondary_interests'):
        value = profile.get(field)
        valid = (isinstance(value, list) and
                 all(isinstance(c, str) and c in CATEGORIES for c in value) and
                 len(set(value)) == len(value))
        check(valid, field)
        interests.append(set(value) if valid else set())
    primary = profile.get('primary_interests')
    check(isinstance(primary, list) and 1 <= len(primary) <= 2,
          'primary_interests', 'EXPECTED_ONE_OR_TWO_CATEGORIES')
    check(not interests[0] & interests[1], 'secondary_interests', 'INTERESTS_OVERLAP')
    check(profile.get('pace') in ('relaxed', 'balanced', 'intensive'), 'pace')
    check(profile.get('transport') in ('walking', 'car', 'public_transit'), 'transport')
    budget = profile.get('budget')
    if budget is not None:
        check(isinstance(budget, dict) and number(budget.get('amount')) and
              budget['amount'] >= 0 and isinstance(budget.get('currency'), str) and
              re.fullmatch('[A-Z]{3}', budget['currency']) is not None and
              budget.get('scope') == 'trip_food_and_entertainment', 'budget')
    required = profile.get('required_poi_ids')
    check(isinstance(required, list) and all(nonempty(i) for i in required) and
          len(set(required)) == len(required), 'required_poi_ids')
    check(type(profile.get('top_k')) is int and profile['top_k'] > 0, 'top_k')
    return errors


def recommend(preferences, pois, *, evaluated_at=None):
    """Вернуть ответ контракта; evaluated_at позволяет воспроизводить тесты."""
    errors = validate_preferences(preferences)
    if errors:
        raise RecommendationError(errors)
    try:
        accepted, report = process_pois(pois)
    except ValueError as error:
        raise RecommendationError([{'path': 'pois', 'code': 'INVALID_DATASET',
                                    'message': str(error)}]) from error
    if report['summary']['rejected_count']:
        raise RecommendationError([
            {'path': f"pois[{i['index']}].{i['field']}", 'code': i['code']}
            for i in report['issues'] if i['severity'] == 'error'])
    now = evaluated_at if evaluated_at is not None else datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError('evaluated_at должен иметь часовой пояс')
    now_key = utc_microseconds(now)
    by_id = {p['id']: p for p in accepted}
    destination = preferences['destination']

    def same_city(p):
        return (p['country_code'] == destination['country_code'] and
                p['city'].strip().casefold() == destination['city'].strip().casefold())

    required_errors = []
    for poi_id in preferences['required_poi_ids']:
        p = by_id.get(poi_id)
        code = ('UNKNOWN_REQUIRED_POI' if p is None else
                'REQUIRED_POI_CONFLICT' if p['availability'] == 'unavailable' or not same_city(p)
                else None)
        if code:
            required_errors.append({'path': 'preferences.required_poi_ids',
                                    'code': code, 'poi_id': poi_id})
    if required_errors:
        raise RecommendationError(required_errors)
    candidates, excluded = [], []
    for p in accepted:
        code = ('OUTSIDE_DESTINATION' if not same_city(p) else
                'UNAVAILABLE' if p['availability'] == 'unavailable' else None)
        if code:
            excluded.append({'poi_id': p['id'], 'code': code})
        else:
            candidates.append(p)
    warnings = [{'code': code, 'poi_id': None} for code in
                ('BUDGET_NOT_VERIFIED', 'OPENING_HOURS_NOT_VERIFIED', 'ROUTE_NOT_VERIFIED')]
    metrics = {p['popularity']['metric'] for p in candidates if p['popularity'] is not None}
    use_popularity = len(metrics) <= 1
    if not use_popularity:
        warnings.append({'code': 'POPULARITY_NOT_COMPARABLE', 'poi_id': None})
    scales = {(p['rating']['scale_min'], p['rating']['scale_max'])
              for p in candidates if p['rating'] is not None}
    if len(scales) > 1:
        warnings.append({'code': 'RATING_SCALE_COMPARABILITY_ASSUMED', 'poi_id': None})
    primary, secondary = set(preferences['primary_interests']), set(preferences['secondary_interests'])
    if not candidates:
        warnings.append({'code': 'NO_CANDIDATES_FOR_DESTINATION', 'poi_id': None})
    elif not any(primary.intersection(p['categories']) for p in candidates):
        warnings.append({'code': 'NO_PRIMARY_INTEREST_MATCH', 'poi_id': None})
    scored = []
    for p in sorted(candidates, key=lambda p: p['id']):
        rating, popularity = p['rating'], p['popularity']
        r = normalized_rating(rating) if rating is not None else None
        u = popularity['value'] if use_popularity and popularity is not None else None
        verified = p['provenance']['verified_at']
        f = None
        if verified is not None:
            checked = datetime.fromisoformat(verified)
            checked_key = utc_microseconds(checked)
            if checked_key > now_key:
                warnings.append({'code': 'FUTURE_VERIFICATION_DATE', 'poi_id': p['id']})
            else:
                f = checked_key
        for field in ('rating', 'popularity'):
            if p[field] is None:
                warnings.append({'code': field.upper() + '_UNKNOWN', 'poi_id': p['id']})
        if f is None:
            warnings.append({'code': 'FRESHNESS_NOT_VERIFIED', 'poi_id': p['id']})
        if p['provenance']['is_synthetic']:
            warnings.append({'code': 'SYNTHETIC_DATA', 'poi_id': p['id']})
        if p['availability'] == 'unknown':
            warnings.append({'code': 'AVAILABILITY_UNKNOWN', 'poi_id': p['id']})
        if p['coordinates'] is None:
            warnings.append({'code': 'COORDINATES_UNKNOWN', 'poi_id': p['id']})
        matches_primary = sorted(primary.intersection(p['categories']))
        matches_secondary = sorted(secondary.intersection(p['categories']))
        # Подстановка ниже служит только сравнению после флага наличия;
        # неизвестные признаки не записываются нулями в POI или ответ.
        key = (len(matches_primary), len(matches_secondary), r is not None,
               r if r is not None else 0, u is not None, u if u is not None else 0,
               f is not None, f if f is not None else 0)
        reasons = []
        for code, matches in (('PRIMARY_INTEREST_MATCH', matches_primary),
                              ('SECONDARY_INTEREST_MATCH', matches_secondary)):
            if matches:
                reasons.append({'code': code, 'categories': matches})
        if not matches_primary and not matches_secondary:
            reasons.append({'code': 'FALLBACK_SELECTION'})
        for code, present in (('RATING_SIGNAL', r is not None),
                              ('POPULARITY_SIGNAL', u is not None), ('FRESHNESS_SIGNAL', f is not None)):
            if present:
                reasons.append({'code': code})
        scored.append((key, p['id'], reasons))
    # Стабильная сортировка сохраняет ID по возрастанию при равенстве ключей.
    scored.sort(key=lambda row: row[0], reverse=True)
    items = [{'poi_id': poi_id, 'rank': rank, 'score': None, 'reasons': reasons}
             for rank, (_, poi_id, reasons) in enumerate(scored[:preferences['top_k']], 1)]
    return {'schema_version': '0.1', 'model_version': MODEL_VERSION, 'items': items,
            'excluded': sorted(excluded, key=lambda p: p['poi_id']),
            'required_poi_ids': list(preferences['required_poi_ids']), 'warnings': warnings,
            'constraint_checks': dict.fromkeys(('budget', 'opening_hours', 'route_feasibility'),
                                               'not_checked')}
