"""Ограниченный поиск заполнения окон в маршруте через общий планировщик."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from math import ceil, floor, isfinite

from ai_ds.moscow_engine_adapter import fixed_sequence, timestamp
from ai_ds.moscow_road import coordinates
from ai_ds.moscow_transit import _distance
from ai_ds.route_assistant import AssistantError, sequences_of


LONG_GAP = 45
EDGE_GAP = 75
MAX_SCHEDULE_TRIALS = 10
MAX_SEARCHED_WINDOWS = 6
WINDOW_CANDIDATES = 4
LATITUDE_TILE = .01
LONGITUDE_TILE = .015

REASONS = {
    'rest': 'Свободное время оставлено в соответствии со спокойным темпом.',
    'meals': 'Сохранены приёмы пищи и минимальные интервалы между ними.',
    'opening': 'Проверенные варианты ограничены окнами работы мест.',
    'transport': 'У проверенных кандидатов перемещения не помещаются в окно или не подтверждены данными.',
    'budget': 'Стоимость проверенных кандидатов превышает оставшийся бюджет.',
    'time': 'Проверенные варианты не помещаются с сохранением посещений и дневных ограничений.',
    'unknown': 'Для проверенных кандидатов недостаточно данных о времени или стоимости.',
    'no_matching_pois': 'В проверенных окрестностях не найдено незапланированных мест по вашим интересам.',
    'required': 'Сначала требуется разрешить конфликт с обязательными посещениями.',
    'within_preference': 'Пауза находится в пределах запаса времени для выбранного темпа.',
    'search_limit': 'Окно осталось после ограниченного поиска. Другие варианты могут существовать.',
}


def _minute(stamp):
    return _clock_minutes(stamp[11:16])


def _clock_minutes(clock):
    hours, minutes = map(int, clock.split(':'))
    return hours * 60 + minutes


def _tile(point):
    return floor(point['lat'] / LATITUDE_TILE), floor(point['lon'] / LONGITUDE_TILE)


def _number(value):
    return type(value) in (int, float) and isfinite(value)


@dataclass(frozen=True)
class Gap:
    day: str
    start: int
    end: int
    kind: str
    previous: str | None
    following: str | None
    observed_reason: str | None = None
    next_leg_minutes: int = 0

    @property
    def minutes(self):
        return self.end - self.start


class MoscowGapIndex:
    """Сетка WGS84 по категориям всего V7. Узкие запросы не сортируют весь каталог."""

    def __init__(self, pois):
        self.by_id = {point['id']: point for point in pois}
        self.tiles = defaultdict(lambda: defaultdict(list))
        for poi in self.by_id.values():
            grid = _tile(coordinates(poi))
            for category in poi['categories']:
                self.tiles[category][grid].append(poi['id'])

    def candidates(self, gap, profile, context, used, budget_left):
        interests = list(dict.fromkeys(profile['primary_interests'] + profile['secondary_interests']))
        points = [coordinates(self.by_id[key]) for key in (gap.previous, gap.following) if key in self.by_id]
        anchor = context.get('day_anchors', {}).get(gap.day)
        if not points:
            points = [anchor or {'lat': 55.752, 'lon': 37.620}]
        elif len(points) == 2:
            a, b = points
            points.append({coordinate: (a[coordinate] + b[coordinate]) / 2 for coordinate in ('lat', 'lon')})
        group = context['moscow_group']
        people = sum(group.get(key, 0) for key in ('adult', 'child', 'senior'))
        day_meals = context.get('moscow_meals', {}).get(gap.day, {})
        weekday = date.fromisoformat(gap.day).weekday()
        primary = set(profile['primary_interests'])
        candidates, inspected, discarded = {}, set(used), Counter()
        for radius in (600, 1200, 2400):
            for key in self._neighborhood(points, interests, radius) - inspected:
                poi = self.by_id[key]
                location = coordinates(poi)
                distances = [_distance(location, point) for point in points]
                if min(distances) > radius:
                    continue
                inspected.add(key)
                if anchor and _distance(location, anchor) > anchor['radius_m']:
                    continue
                data = poi['planning']
                is_food = 'average_check_rub' in data
                if is_food and ('snack' in day_meals or any(category != 'cafes' for category in interests)):
                    continue
                minutes = data.get('duration_minutes', {}).get('value')
                price = data.get('price_rub', {}).get('value')
                if not _number(minutes) or not _number(price):
                    discarded['unknown'] += 1
                    continue
                minimum = max(20, floor(minutes * .9)) if not is_food else 50
                if minimum > gap.minutes:
                    discarded['time'] += 1
                    continue
                if price * people > budget_left:
                    discarded['budget'] += 1
                    continue
                hours = data.get('opening_hours', {})
                estimated = hours.get('value')
                if hours.get('source_type') == 'synthetic' and isinstance(estimated, list) and len(estimated) == 7:
                    opening = estimated[weekday]
                    if not opening or min(_clock_minutes(opening[1]), gap.end) - max(
                            _clock_minutes(opening[0]), gap.start) < minimum:
                        discarded['opening'] += 1
                        continue
                categories = set(poi['categories'])
                detour = sum(distances[:2]) if len(points) == 3 else distances[0]
                sources = poi.get('catalog_signals', {})
                score = (-len(primary & categories), round(detour / 200), -sum(bool(value) for value in sources.values()),
                         abs(gap.minutes - minimum - detour / 75), price, key)
                candidates[key] = (score, minimum, is_food)
            if len(candidates) >= WINDOW_CANDIDATES:
                break
        ranked = sorted(candidates, key=lambda key: candidates[key][0])[:WINDOW_CANDIDATES]
        reason = min(discarded, key=lambda key: (-discarded[key], key)) if discarded else 'no_matching_pois'
        return [(key, *candidates[key][1:]) for key in ranked], reason

    def _neighborhood(self, points, interests, radius):
        keys = set()
        for point in points:
            lat, lon = _tile(point)
            lat_span = ceil(radius / 111_000 / LATITUDE_TILE)
            lon_span = ceil(radius / 59_000 / LONGITUDE_TILE)
            for category in interests:
                cells = self.tiles[category]
                for dy in range(-lat_span, lat_span + 1):
                    for dx in range(-lon_span, lon_span + 1):
                        keys.update(cells.get((lat + dy, lon + dx), ()))
        return keys


def route_gaps(plan, context):
    limits = {day['date']: (_minute(day['day_start']), _minute(day['day_end'])) for day in context['days']}
    reserved = {day['date']: [{'type': entry['kind'], 'start_min': _minute(entry['start_at']),
                              'end_min': _minute(entry['end_at'])}
                             for entry in day.get('breaks', []) if entry['kind'] in {'rest', 'meal'}]
                for day in plan.get('days', [])}
    result = []
    for day in plan.get('engine_days', []):
        key = day['date']
        window = limits[key]
        visits = [event for event in day['events'] if event['type'] in {'visit', 'meal'}]
        events = sorted([*day['events'], *reserved.get(key, [])], key=lambda event: event['start_min'])
        slots = zip([None, *events], [*events, None])
        for left, right in slots:
            start = left['end_min'] if left else window[0]
            end = right['start_min'] if right else window[1]
            if end - start <= 30:
                continue
            before = next((visit for visit in reversed(visits) if visit['end_min'] <= start), None)
            after = next((visit for visit in visits if visit['start_min'] >= end), None)
            kind = 'internal' if left and right else 'leading' if right else 'tail'
            source = right.get('waiting_reason') if right else None
            travel = right['end_min'] - right['start_min'] if right and right['type'] == 'travel' else 0
            result.append(Gap(key, start, end, kind, before and before['poi_id'],
                              after and after['poi_id'], source, travel))
    return result


def _merit(plan, context, day):
    gaps = [gap for gap in route_gaps(plan, context) if gap.day == day]
    return tuple(sum(max(0, gap.minutes - threshold) for gap in gaps if gap.kind == kind)
                 for kind, threshold in (('internal', LONG_GAP), ('leading', EDGE_GAP), ('tail', EDGE_GAP)))


def _repaired(original, candidate, gap, context, expected, profile):
    if sequences_of(candidate) != expected or candidate.get('unplaced_required_poi_ids'):
        return False
    checks = candidate.get('native_checks', {})
    if any(checks.get(key) == 'failed' for key in ('time', 'budget', 'opening_hours', 'transport', 'group',
                                                   'no_duplicates', 'meal_intervals', 'required_places')):
        return False
    if original.get('native_checks', {}).get('meals') == 'passed' and checks.get('meals') != 'passed':
        return False
    total = candidate['budget_summary']['known_total']
    if not _number(total) or total > profile['budget']['amount'] + .01:
        return False
    original_day = next(day for day in original['engine_days'] if day['date'] == gap.day)
    candidate_day = next(day for day in candidate['engine_days'] if day['date'] == gap.day)
    old_meals = {(event.get('meal_kind'), event['poi_id']) for event in original_day['events'] if event['type'] == 'meal'}
    new_meals = {(event.get('meal_kind'), event['poi_id']) for event in candidate_day['events'] if event['type'] == 'meal'}
    if not old_meals <= new_meals:
        return False
    old_breaks = next(day['breaks'] for day in original['days'] if day['date'] == gap.day)
    new_breaks = next(day['breaks'] for day in candidate['days'] if day['date'] == gap.day)
    if any(item['kind'] == 'meal' for item in old_breaks):
        if not any(kind == 'lunch' for kind, _ in new_meals) and not any(item['kind'] == 'meal' for item in new_breaks):
            return False
    if any(item['kind'] == 'rest' and item not in new_breaks for item in old_breaks):
        return False
    old_score, new_score = (_merit(item, context, gap.day) for item in (original, candidate))
    slot = {'internal': 0, 'leading': 1, 'tail': 2}[gap.kind]
    if new_score[slot] >= old_score[slot] or new_score[:slot] > old_score[:slot] or sum(new_score) > sum(old_score):
        return False
    def time_spent(day, event_type):
        return sum(event['end_min'] - event['start_min'] for event in day['events'] if event['type'] == event_type)
    extra_travel = time_spent(candidate_day, 'travel') - time_spent(original_day, 'travel')
    extra_useful = sum(time_spent(candidate_day, kind) - time_spent(original_day, kind) for kind in ('visit', 'meal'))
    return extra_travel <= min(18, max(0, extra_useful // 2))


def _travel_fit(matrix, gap, key, minimum):
    durations = {}
    matrices = matrix.get('profiles', {'default': matrix})
    for profile in matrices.values():
        for pair in profile['pairs']:
            seconds = pair['duration_seconds']
            if pair['status'] != 'known' or not _number(seconds):
                continue
            direction = pair['from_poi_id'], pair['to_poi_id']
            durations[direction] = min(seconds, durations.get(direction, seconds))
    outbound = durations.get((gap.previous, key)) if gap.previous else 0
    inbound = durations.get((key, gap.following)) if gap.following else 0
    return (_number(outbound) and _number(inbound) and
            minimum + ceil(outbound / 60) + ceil(inbound / 60) <= gap.minutes + gap.next_leg_minutes)


def _description(gap, reason):
    return {'date': gap.day, 'start_at': timestamp(gap.day, gap.start), 'end_at': timestamp(gap.day, gap.end),
            'minutes': gap.minutes, 'kind': gap.kind, 'reason_code': reason,
            'reason': REASONS[reason], 'observed_reason': gap.observed_reason}


def describe_edited_gaps(plan, context, previous):
    gaps = route_gaps(plan, context)
    ids = {key for day in sequences_of(plan).values() for key in day}
    return {
        'before': sum(gap.minutes > LONG_GAP for gap in route_gaps(previous, context)),
        'after': sum(gap.minutes > LONG_GAP for gap in gaps),
        'attempts': 0,
        'inserted_poi_ids': [key for key in previous.get('gap_diagnostics', {}).get('inserted_poi_ids', [])
                             if key in ids],
        'remaining': [_description(gap, 'opening' if 'открыти' in (gap.observed_reason or '').casefold()
                                   else 'search_limit') for gap in gaps],
    }


def _try_schedule(state, current, context, trial, gap, profile, *, reflow=False):
    requested = {**state, 'route': current, 'routing_context': context}
    if reflow:
        requested['reflow_days'] = {gap.day}
    try:
        changed = fixed_sequence(requested, trial)
    except AssistantError as error:
        return None, {'BUDGET_EXCEEDED': 'budget', 'OUTSIDE_OPENING_HOURS': 'opening',
                      'MEAL_TIME_UNAVAILABLE': 'meals', 'TRANSPORT_UNAVAILABLE': 'transport',
                      'DAY_END_EXCEEDED': 'time'}.get(error.code, 'search_limit')
    if not _repaired(current, changed, gap, context, trial, profile):
        return None, 'time'
    return {**current, **changed, 'ranking': current.get('ranking', [])}, None


def repair_gaps(plan, profile, context, index, make_state):
    """Расширяет список только возле окон и проверяет вставки тем же JS-движком."""
    initial = route_gaps(plan, context)
    visited, rejected = set(), {}
    current, trials, searched, inserted = plan, 0, 0, []
    while trials < MAX_SCHEDULE_TRIALS and searched < MAX_SEARCHED_WINDOWS:
        options = sorted((gap for gap in route_gaps(current, context) if gap not in visited and
                          gap.minutes > (LONG_GAP if gap.kind == 'internal' else EDGE_GAP)),
                         key=lambda gap: (gap.kind != 'internal', -gap.minutes, gap.day, gap.start))
        if not options:
            break
        gap = options[0]
        visited.add(gap)
        pace = context.get('day_pace_overrides', {}).get(gap.day, profile['pace'])
        if pace == 'relaxed':
            rejected[gap] = 'rest'
            continue
        if current.get('unplaced_required_poi_ids') or current.get('status') == 'infeasible':
            rejected[gap] = 'required'
            continue
        searched += 1
        sequences = sequences_of(current)
        used = set().union(*(set(items) for items in sequences.values()), context.get('moscow_excluded_ids', []))
        spent = current['budget_summary']['known_total']
        if not _number(spent):
            rejected[gap] = 'unknown'
            continue
        remaining = profile['budget']['amount'] - spent
        active_context = {**context, 'moscow_meals': current.get('moscow_meals', {})}
        candidates, reason = index.candidates(gap, profile, active_context, used, remaining)
        meal_ids = {slot['poi_id'] for slot in active_context['moscow_meals'].get(gap.day, {}).values()}
        position = sequences[gap.day].index(gap.following) if gap.following else len(sequences[gap.day])
        can_swap = (gap.kind == 'internal' and position > 0
                    and sequences[gap.day][position - 1] == gap.previous
                    and not {gap.previous, gap.following} & meal_ids)
        if not candidates and not can_swap:
            rejected[gap] = reason
            continue
        ids = list(dict.fromkeys([*(key for values in sequences.values() for key in values),
                                  *(candidate[0] for candidate in candidates)]))
        state = make_state(ids)
        base_context = {**active_context, **state['routing_context'], 'moscow_meals': active_context['moscow_meals']}
        rejected[gap] = 'transport' if candidates else reason
        accepted = False
        for key, minimum, food in candidates:
            if trials >= MAX_SCHEDULE_TRIALS:
                rejected[gap] = 'search_limit'
                break
            if not _travel_fit(state['travel_times'], gap, key, minimum):
                continue
            trial_context = base_context
            if food:
                meals = base_context['moscow_meals']
                trial_context = {**base_context, 'moscow_meals': {**meals, gap.day: {
                    **meals.get(gap.day, {}), 'snack': {'poi_id': key}}}}
            trial = dict(sequences, **{gap.day: [*sequences[gap.day][:position], key, *sequences[gap.day][position:]]})
            trials += 1
            changed, failure = _try_schedule(state, current, trial_context, trial, gap, profile)
            if changed:
                current = changed
                inserted.append(key)
                accepted = True
                break
            rejected[gap] = failure
        if not accepted and can_swap and trials < MAX_SCHEDULE_TRIALS:
            reordered = sequences[gap.day].copy()
            reordered[position - 1], reordered[position] = reordered[position], reordered[position - 1]
            trials += 1
            changed, failure = _try_schedule(state, current, base_context, dict(sequences, **{gap.day: reordered}),
                                             gap, profile, reflow=True)
            if changed:
                current = changed
            elif not candidates:
                rejected[gap] = failure
    remaining_gaps = route_gaps(current, context)
    diagnostics = []
    for gap in remaining_gaps:
        reason = rejected.get(gap)
        if reason is None and gap.observed_reason:
            reason = 'opening' if 'открыти' in gap.observed_reason.casefold() else (
                'meals' if 'пищ' in gap.observed_reason.casefold() else None)
        if reason is None:
            pace = context.get('day_pace_overrides', {}).get(gap.day, profile['pace'])
            if pace == 'relaxed':
                reason = 'rest'
            elif gap.minutes <= (LONG_GAP if gap.kind == 'internal' else EDGE_GAP):
                reason = 'within_preference'
        diagnostics.append(_description(gap, reason or 'search_limit'))
    return {**current, 'gap_diagnostics': {'before': sum(gap.minutes > LONG_GAP for gap in initial),
                                          'after': sum(gap.minutes > LONG_GAP for gap in remaining_gaps),
                                          'attempts': trials, 'inserted_poi_ids': inserted, 'remaining': diagnostics}}
