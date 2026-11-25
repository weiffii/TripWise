"""Ограниченный поиск заполнения окон в маршруте через общий планировщик."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from math import ceil, floor, isfinite

from ai_ds.moscow_engine_adapter import fixed_sequence, timestamp
from ai_ds.moscow_road import coordinates
from ai_ds.moscow_transit import _distance
from ai_ds.route_assistant import AssistantError, sequences_of


LONG_GAP = 45
EDGE_GAP = 55
MAX_SCHEDULE_TRIALS = 36
MAX_SEARCHED_WINDOWS = 12
WINDOW_CANDIDATES = 4
SEARCH_RADII = (650, 1500, 3000, 5000)
LATITUDE_TILE = .01
LONGITUDE_TILE = .015

REASONS = {
    'rest': 'Свободное время оставлено в соответствии со спокойным темпом.',
    'meals': 'Сохранены приёмы пищи и минимальные интервалы между ними.',
    'opening': 'Проверенные варианты ограничены окнами работы мест.',
    'fixed_time': 'Время следующего посещения или приёма пищи закреплено пользователем.',
    'transport': 'У проверенных кандидатов перемещения не помещаются в окно или не подтверждены данными.',
    'budget': 'Стоимость проверенных кандидатов превышает оставшийся бюджет.',
    'time': 'Проверенные варианты не помещаются с сохранением посещений и дневных ограничений.',
    'unknown': 'Для проверенных кандидатов недостаточно данных о времени или стоимости.',
    'no_matching_pois': 'В проверенных окрестностях не найдено незапланированных мест по вашим интересам.',
    'required': 'Сначала требуется разрешить конфликт с обязательными посещениями.',
    'within_preference': 'Пауза находится в пределах запаса времени для выбранного темпа.',
    'search_limit': 'Достигнут вычислительный лимит пересчёта расписаний.',
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

    def candidates(self, gap, profile, context, used, budget_left, *, radii=(600, 1200, 2400),
                   limit=WINDOW_CANDIDATES, stats=None, checked=None, allow_any_categories=False,
                   nearby_sights=False):
        interests = list(dict.fromkeys(profile['primary_interests'] + profile['secondary_interests']))
        people = max(1, sum(context.get('moscow_group', {}).get(k, 0) for k in ('adult', 'child', 'senior')))
        total_budget = profile.get('budget', {}).get('amount')
        day_count = max(1, len(context.get('days', [])))
        constrained = _number(total_budget) and total_budget / people / day_count < 4000
        if allow_any_categories:
            # An explicit request for "any categories" must not silently fall
            # back to the original narrow interest shortlist.
            interests.extend(c for c in ('museums', 'history', 'nature', 'entertainment',
                                         'shopping', 'viewpoints', 'unusual') if c not in interests)
        if constrained or nearby_sights:
            # A free nearby park is a legitimate gap repair even if the primary
            # interest (for example theatre tickets) exceeds the trip budget.
            interests.extend(c for c in ('nature', 'history', 'viewpoints', 'unusual') if c not in interests)
        points = [coordinates(self.by_id[key]) for key in (gap.previous, gap.following) if key in self.by_id]
        anchor = context.get('day_anchors', {}).get(gap.day)
        if not points:
            points = [anchor or {'lat': 55.752, 'lon': 37.620}]
        elif len(points) == 2:
            a, b = points
            points.extend({coordinate: a[coordinate] + ratio * (b[coordinate] - a[coordinate])
                           for coordinate in ('lat', 'lon')} for ratio in (.25, .5, .75))
        group = context['moscow_group']
        people = sum(group.get(key, 0) for key in ('adult', 'child', 'senior'))
        day_meals = context.get('moscow_meals', {}).get(gap.day, {})
        weekday = date.fromisoformat(gap.day).weekday()
        primary = set(profile['primary_interests'])
        candidates, inspected, discarded = {}, set(used), Counter()
        for radius in radii:
            for key in self._neighborhood(points, interests, radius) - inspected:
                poi = self.by_id[key]
                location = coordinates(poi)
                distances = [_distance(location, point) for point in points]
                if min(distances) > radius:
                    continue
                inspected.add(key)
                unseen = checked is None or key not in checked
                if checked is not None:
                    checked.add(key)
                if (anchor and _distance(location, anchor) > anchor['radius_m']
                        and not (gap.kind == 'internal' and min(distances) <= min(500, radius))):
                    discarded['transport'] += unseen
                    continue
                data = poi['planning']
                is_food = 'average_check_rub' in data
                if is_food and ('snack' in day_meals or any(category != 'cafes' for category in interests)):
                    continue
                minutes = data.get('duration_minutes', {}).get('value')
                price = data.get('price_rub', {}).get('value')
                if not _number(minutes) or not _number(price):
                    discarded['unknown'] += unseen
                    continue
                minimum = max(20, floor(minutes * .9)) if not is_food else 50
                if minimum > gap.minutes:
                    discarded['time'] += unseen
                    continue
                if price * people > budget_left:
                    discarded['budget'] += unseen
                    continue
                hours = data.get('opening_hours', {})
                estimated = hours.get('value')
                if hours.get('source_type') == 'synthetic' and isinstance(estimated, list) and len(estimated) == 7:
                    opening = estimated[weekday]
                    if not opening or min(_clock_minutes(opening[1]), gap.end) - max(
                            _clock_minutes(opening[0]), gap.start) < minimum:
                        discarded['opening'] += unseen
                        continue
                categories = set(poi['categories'])
                detour = sum(distances[:2]) if len(points) > 2 else distances[0]
                sources = poi.get('catalog_signals', {})
                score = ((price * people, -len(primary & categories), round(detour / 200),
                          abs(gap.minutes - minimum - detour / 75), key) if constrained else
                         (-len(primary & categories), round(detour / 200),
                          -sum(bool(value) for value in sources.values()),
                          abs(gap.minutes - minimum - detour / 75), price, key))
                candidates[key] = (score, minimum, is_food)
            if len(candidates) >= limit:
                break
        if stats is not None:
            stats.update(discarded)
        ranked = sorted(candidates, key=lambda key: candidates[key][0])[:limit]
        reason = min(discarded, key=lambda key: (-discarded[key], key)) if discarded else 'no_matching_pois'
        return [(key, *candidates[key][1:]) for key in ranked], reason

    def _neighborhood(self, points, interests, radius):
        keys = set()
        for point in points:
            lat, lon = _tile(point)
            keys.update(self._nearby_tiles(lat, lon, tuple(interests), radius))
        return keys

    @lru_cache(maxsize=160)
    def _nearby_tiles(self, lat, lon, interests, radius):
        lat_span = ceil(radius / 111_000 / LATITUDE_TILE)
        lon_span = ceil(radius / 59_000 / LONGITUDE_TILE)
        return frozenset(key for category in interests
                         for dy in range(-lat_span, lat_span + 1)
                         for dx in range(-lon_span, lon_span + 1)
                         for key in self.tiles[category].get((lat + dy, lon + dx), ()))


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


def _merit(gaps):
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
    if set(candidate.get('uncovered_interests') or ()) - set(original.get('uncovered_interests') or ()):
        return False
    total = candidate['budget_summary']['known_total']
    if not _number(total) or total > profile['budget']['amount'] + .01:
        return False
    original_day = next(day for day in original['engine_days'] if day['date'] == gap.day)
    candidate_day = next(day for day in candidate['engine_days'] if day['date'] == gap.day)
    old_legs = {(event.get('from_poi_id'), event.get('to_poi_id'))
                for event in original_day['events'] if event['type'] == 'travel'}
    if any(event['type'] == 'travel' and event.get('mode') == 'walk'
           and event['end_min'] - event['start_min'] > 60
           and (event.get('from_poi_id'), event.get('to_poi_id')) not in old_legs
           for event in candidate_day['events']):
        return False
    pace = context.get('day_pace_overrides', {}).get(gap.day, profile['pace'])
    if pace == 'relaxed' and sum(event['type'] == 'visit' for event in candidate_day['events']) > max(
            4, sum(event['type'] == 'visit' for event in original_day['events'])):
        return False
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
    old_gaps, new_gaps = ([item for item in route_gaps(plan, context) if item.day == gap.day]
                         for plan in (original, candidate))
    old_score, new_score = _merit(old_gaps), _merit(new_gaps)
    slot = {'internal': 0, 'leading': 1, 'tail': 2}[gap.kind]
    if new_score[slot] >= old_score[slot] or new_score[:slot] > old_score[:slot] or sum(new_score) > sum(old_score):
        return False
    def longest_internal(gaps):
        return max((item.minutes for item in gaps if item.kind == 'internal'), default=0)
    if longest_internal(new_gaps) > max(60, longest_internal(old_gaps)):
        return False
    def time_spent(day, event_type):
        return sum(event['end_min'] - event['start_min'] for event in day['events'] if event['type'] == event_type)
    extra_travel = time_spent(candidate_day, 'travel') - time_spent(original_day, 'travel')
    extra_useful = sum(time_spent(candidate_day, kind) - time_spent(original_day, kind) for kind in ('visit', 'meal'))
    saved_idle = max(0, old_score[0] - new_score[0])
    # An existing visit can occupy the window without buying another ticket.
    # A small additional walk may be worth a substantially shorter wait.
    return extra_travel <= min(18, max(0, extra_useful // 2, saved_idle // 5))


def _durations(matrix):
    durations = {}
    matrices = matrix.get('profiles', {'default': matrix})
    for profile in matrices.values():
        for pair in profile['pairs']:
            seconds = pair['duration_seconds']
            if pair['status'] != 'known' or not _number(seconds):
                continue
            direction = pair['from_poi_id'], pair['to_poi_id']
            durations[direction] = min(seconds, durations.get(direction, seconds))
    return durations


def _travel_fit(matrix, gap, key, minimum):
    durations = _durations(matrix)
    outbound = durations.get((gap.previous, key)) if gap.previous else 0
    inbound = durations.get((key, gap.following)) if gap.following else 0
    return (_number(outbound) and _number(inbound) and
            minimum + ceil(outbound / 60) + ceil(inbound / 60) <= gap.minutes + gap.next_leg_minutes)


def _description(gap, reason, *, checked=0, attempts=0, rejections=None):
    changes = {'budget': 'Повышение общего бюджета расширит выбор мест в этом окне.',
               'opening': 'Более гибкое время дня или другие даты расширят пересечение с часами работы.',
               'transport': 'Другой разрешённый режим или более компактная группа мест могут уменьшить дорогу.',
               'time': 'Более гибкое время посещений даст планировщику больше вариантов.'}
    result = {'date': gap.day, 'start_at': timestamp(gap.day, gap.start), 'end_at': timestamp(gap.day, gap.end),
            'minutes': gap.minutes, 'kind': gap.kind, 'reason_code': reason,
            'reason': REASONS[reason], 'observed_reason': gap.observed_reason,
            'candidate_count': checked, 'attempts': attempts, 'rejections': dict(rejections or {})}
    if reason in changes:
        result['possible_adjustment'] = changes[reason]
    return result


def _placement_options(matrix, gap, candidates):
    """Небольшой beam по фактическим направленным времени, включая путь назад к следующему POI."""
    durations = _durations(matrix)
    capacity = gap.minutes + gap.next_leg_minutes
    maximum = min(5, max(1, capacity // 55))
    beam = [((), 0, 0)]
    levels = []
    for _ in range(maximum):
        following = []
        for keys, useful, road in beam:
            previous = keys[-1] if keys else gap.previous
            for key, minimum, food in candidates:
                if key in keys or (keys and food):
                    continue
                outbound = durations.get((previous, key), float('inf')) if previous else 0
                inbound = durations.get((key, gap.following), float('inf')) if gap.following else 0
                if not _number(outbound) or not _number(inbound):
                    continue
                legs = road + ceil(outbound / 60)
                total = useful + minimum + legs + ceil(inbound / 60)
                if total <= capacity:
                    following.append(((*keys, key), useful + minimum, legs))
        if not following:
            break
        beam = sorted(following, key=lambda row: (-(row[1] - row[2]), row[2], row[0]))[:12]
        levels.append(beam)
    if not levels:
        return []
    chains = [level[0][0] for level in reversed(levels[1:])]
    singles = {row[0][0] for row in levels[0]}
    return chains + [(key,) for key, _, _ in candidates if key in singles]


def _relocations(sequence, position, meals, excluded):
    for index in sorted(range(len(sequence)), key=lambda index: (abs(index - position), index)):
        point = sequence[index]
        if point in meals or point in excluded:
            continue
        other = list(sequence)
        other.pop(index)
        target = position - (index < position)
        other.insert(target, point)
        if other != sequence:
            yield other


def _replacement_options(sequence, position, candidates, protected, index, profile):
    primary = set(profile['primary_interests'])
    secondary = set(profile['secondary_interests'])
    def score(key):
        categories = set(index.by_id[key]['categories'])
        duration = index.by_id[key]['planning']['duration_minutes']['value']
        return len(primary & categories), len(secondary & categories), duration

    for slot in (position - 1, position):
        if not 0 <= slot < len(sequence) or sequence[slot] in protected:
            continue
        old = score(sequence[slot])
        if not _number(old[2]):
            continue
        for replacement, _, food in candidates:
            if not food and score(replacement)[:2] >= old[:2]:
                yield [*sequence[:slot], replacement, *sequence[slot + 1:]], replacement


def describe_edited_gaps(plan, context, previous):
    gaps = route_gaps(plan, context)
    ids = {key for day in sequences_of(plan).values() for key in day}
    return {
        'before': sum(gap.minutes > LONG_GAP for gap in route_gaps(previous, context)),
        'after': sum(gap.minutes > LONG_GAP for gap in gaps),
        'attempts': 0,
        'inserted_poi_ids': [key for key in previous.get('gap_diagnostics', {}).get('inserted_poi_ids', [])
                             if key in ids],
        'remaining': [_description(gap, _observed_reason(gap) or 'search_limit') for gap in gaps],
    }


def refresh_gap_report(plan, context, *, only_days=None):
    """Keep the search evidence when the final timing pass shortens a gap."""
    report = plan['gap_diagnostics']
    previous = report['remaining']
    gaps = [gap for gap in route_gaps(plan, context) if only_days is None or gap.day in only_days]
    remaining = []
    for gap in gaps:
        current = _description(gap, _observed_reason(gap) or 'search_limit')
        prior = next((item for item in previous if item['date'] == gap.day and item['kind'] == gap.kind
                      and item['start_at'] <= current['start_at'] < current['end_at'] <= item['end_at']), None)
        if prior:
            current = {**prior, **{key: current[key] for key in ('start_at', 'end_at', 'minutes')}}
        remaining.append(current)
    report.update(after=sum(gap.minutes > LONG_GAP for gap in gaps),
                  after_over_60=sum(gap.minutes > 60 for gap in gaps), remaining=remaining)


def _observed_reason(gap):
    text = (gap.observed_reason or '').casefold()
    if 'закрепл' in text or 'выбранного пользователем' in text:
        return 'fixed_time'
    if 'открыти' in text:
        return 'opening'
    if any(word in text for word in ('пищ', 'интервал', 'обеда', 'ужина', 'завтрака')):
        return 'meals'
    return None


def _try_schedule(state, current, context, trial, gap, profile, *, reflow=False):
    requested = {**state, 'route': current, 'routing_context': context,
                 'defer_visit_extensions': True, 'day_local_transport': True}
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


def repair_gaps(plan, profile, context, index, make_state, *, only_days=None,
                max_schedule_trials=MAX_SCHEDULE_TRIALS, max_searched_windows=MAX_SEARCHED_WINDOWS,
                allow_any_categories=False):
    """Расширяет список только возле окон и проверяет вставки тем же JS-движком."""
    selected_days = None if only_days is None else frozenset(only_days)
    initial = [gap for gap in route_gaps(plan, context)
               if selected_days is None or gap.day in selected_days]
    visited, rejected = set(), {}
    current, trials, searched, inserted = plan, 0, 0, []
    replaced = []
    counts = {}
    all_checked = set()

    @lru_cache(maxsize=8)
    def state_for(ids):
        return make_state(list(ids))

    while trials < max_schedule_trials and searched < max_searched_windows:
        options = sorted((gap for gap in route_gaps(current, context)
                          if (selected_days is None or gap.day in selected_days) and gap not in visited and
                          gap.minutes > (LONG_GAP if gap.kind == 'internal' else EDGE_GAP)),
                         key=lambda gap: (gap.kind != 'internal', -gap.minutes, gap.day, gap.start))
        if not options:
            break
        gap = options[0]
        visited.add(gap)
        pace = context.get('day_pace_overrides', {}).get(gap.day, profile['pace'])
        if pace == 'relaxed' and (gap.kind != 'internal' or gap.minutes < 90):
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
        meal_ids = {slot['poi_id'] for slot in active_context['moscow_meals'].get(gap.day, {}).values()}
        position = sequences[gap.day].index(gap.following) if gap.following else len(sequences[gap.day])
        retained = tuple(dict.fromkeys(key for values in sequences.values() for key in values))
        protected = meal_ids | set(profile['required_poi_ids']) | set(
            context.get('moscow_start_hints', {}).get(gap.day, {})) | set(
            context.get('visit_times', {}).get(gap.day, {}))
        tested, checked, reasons = set(), set(), Counter()
        started = trials
        accepted = False
        width = 9 if gap.minutes >= 120 else 5
        replacement_batch = None
        search = [(radius, False) for radius in SEARCH_RADII]
        if gap.kind == 'internal' and gap.minutes >= 90 and not allow_any_categories:
            search.extend([(650, True), (1500, True)])
        for radius, nearby_sights in search:
            candidates, _ = index.candidates(gap, profile, active_context, used | tested, remaining,
                                             radii=(radius,), limit=width, stats=reasons, checked=checked,
                                             allow_any_categories=allow_any_categories, nearby_sights=nearby_sights)
            if not candidates:
                continue
            options = {key: (minimum, food) for key, minimum, food in candidates}
            tested.update(options)
            state = state_for((*retained, *sorted(options)))
            base_context = {**active_context, **state['routing_context'], 'moscow_meals': active_context['moscow_meals']}
            replacement_batch = (state, base_context, candidates)
            paths = _placement_options(state['travel_times'], gap, candidates)
            reachable = {point for path in paths for point in path}
            reasons['transport'] += len(options.keys() - reachable)
            for keys in paths[:6]:
                if trials >= max_schedule_trials or trials - started >= 14:
                    break
                hints = base_context.get('moscow_start_hints', {})
                day_hints = {**hints.get(gap.day, {}), keys[0]: gap.start}
                trial_context = {**base_context, 'moscow_start_hints': {**hints, gap.day: day_hints}}
                if options[keys[0]][1]:
                    meals = base_context['moscow_meals']
                    trial_context['moscow_meals'] = {**meals, gap.day: {
                        **meals.get(gap.day, {}), 'snack': {'poi_id': keys[0]}}}
                old = sequences[gap.day]
                trial = dict(sequences, **{gap.day: [*old[:position], *keys, *old[position:]]})
                trials += 1
                changed, failure = _try_schedule(state, current, trial_context, trial, gap, profile)
                if not changed and gap.kind == 'internal' and gap.minutes > 60 and trials - started < 14 and (
                        trials < max_schedule_trials):
                    trials += 1
                    changed, failure = _try_schedule(state, current, trial_context, trial, gap, profile, reflow=True)
                if changed:
                    current = changed
                    inserted.extend(keys)
                    accepted = True
                    break
                reasons[failure] += 1
            if accepted or trials >= max_schedule_trials or trials - started >= 14:
                break
        if not accepted and gap.kind == 'internal' and len(retained) >= 2 and trials < max_schedule_trials:
            state = state_for(retained)
            base_context = {**active_context, **state['routing_context'], 'moscow_meals': active_context['moscow_meals']}
            reorders = _relocations(sequences[gap.day], position, meal_ids, protected)
            for reordered in (sequences[gap.day], *reorders):
                if trials >= max_schedule_trials or trials - started >= 18:
                    break
                trials += 1
                changed, failure = _try_schedule(state, current, base_context, dict(sequences, **{gap.day: reordered}),
                                                 gap, profile, reflow=True)
                if changed:
                    current = changed
                    accepted = True
                    break
                reasons[failure] += 1
        if not accepted and gap.kind == 'internal' and gap.minutes >= 90 and trials < max_schedule_trials:
            original_order = sequences[gap.day]
            adjacent = set(original_order[max(0, position - 1):position + 1]) - protected
            day_visits = next(day['visits'] for day in current['days'] if day['date'] == gap.day)
            refundable = max((row['price_rub'] for row in day_visits
                              if row['poi_id'] in adjacent and _number(row.get('price_rub'))), default=0)
            if refundable > 0:
                # The current balance alone is not a bound for a swap: the
                # removed optional ticket releases its price.
                choices, _ = index.candidates(gap, profile, active_context, used, remaining + refundable,
                                               radii=(650, 1500, 3000), limit=width, stats=reasons,
                                               checked=checked, allow_any_categories=allow_any_categories,
                                               nearby_sights=True)
                if choices:
                    state = state_for((*retained, *sorted(key for key, _, _ in choices)))
                    replacement_batch = (state, {**active_context, **state['routing_context'],
                                                  'moscow_meals': active_context['moscow_meals']}, choices)
            state, base_context, choices = replacement_batch or (None, None, ())
            for proposed, replacement in _replacement_options(original_order, position, choices, protected, index, profile):
                if trials >= max_schedule_trials:
                    break
                trials += 1
                changed, failure = _try_schedule(state, current, base_context, dict(sequences, **{gap.day: proposed}),
                                                 gap, profile, reflow=True)
                if changed:
                    current = changed
                    removed = next(key for key in original_order if key not in proposed)
                    replaced.append({'removed': removed, 'added': replacement})
                    accepted = True
                    break
                reasons[failure] += 1
        if not accepted:
            ranked = [reason for reason, count in sorted(reasons.items(), key=lambda entry: (-entry[1], entry[0]))
                      if count > 0]
            rejected[gap] = ranked[0] if ranked else 'no_matching_pois'
        counts[gap] = (len(checked), trials - started, reasons)
        all_checked.update(checked)
    remaining_gaps = [gap for gap in route_gaps(current, context)
                      if selected_days is None or gap.day in selected_days]
    diagnostics = []
    for gap in remaining_gaps:
        reason = rejected.get(gap) or _observed_reason(gap)
        if reason is None:
            pace = context.get('day_pace_overrides', {}).get(gap.day, profile['pace'])
            if pace == 'relaxed' and (gap.kind != 'internal' or gap.minutes < 90):
                reason = 'rest'
            elif gap.minutes <= (LONG_GAP if gap.kind == 'internal' else EDGE_GAP):
                reason = 'within_preference'
        checked, attempted, reasons = counts.get(gap, (0, 0, {}))
        diagnostics.append(_description(gap, reason or 'search_limit', checked=checked,
                                        attempts=attempted, rejections=reasons))
    return {**current, 'gap_diagnostics': {'before': sum(gap.minutes > LONG_GAP for gap in initial),
                                          'after': sum(gap.minutes > LONG_GAP for gap in remaining_gaps),
                                          'before_over_60': sum(gap.minutes > 60 for gap in initial),
                                          'after_over_60': sum(gap.minutes > 60 for gap in remaining_gaps),
                                          'attempts': trials, 'candidate_count': len(all_checked),
                                          'inserted_poi_ids': inserted, 'replacements': replaced,
                                          'remaining': diagnostics}}
