from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
from functools import lru_cache
from heapq import nsmallest
from itertools import combinations, zip_longest
import json
from math import floor, isfinite
import os
from pathlib import Path
import re

from fastapi import HTTPException
from ai_ds.day_windows import DAY_WINDOW_FIELDS, MAX_TRIP_DAYS, minutes, valid_clock

from ai_ds.moscow_catalog import load_moscow_v7
from ai_ds.moscow_road import PILOT_RAIL_CONFIG, coordinates, rail_inputs, travel_matrix
from ai_ds.moscow_transit import _distance
from ai_ds.moscow_engine_adapter import MATRIX_MODES, MODES, fixed_sequence, plan_city, stretch_saved_visits
from ai_ds.moscow_gap_repair import MoscowGapIndex, describe_edited_gaps, repair_gaps, refresh_gap_report
from ai_ds.openrouter import OpenRouterError, OpenRouterInterpreter, SYSTEM_PROMPT
from ai_ds.recommendation import recommend
from ai_ds.route_assistant import AssistantError, InMemoryRouteStore, RouteAssistant, has_failed, parse_interpretation, require, sequences_of
from ai_ds.shanghai_assistant import BRIDGE_FAILURE_CODES, ShanghaiAssistantService, bridge_batch
from ai_ds.visit_time_intent import PIN_FIELDS, local_visit_pin, valid_pin

BASE = Path(__file__).resolve().parents[1] / 'examples/routing/v02'
CITY_CENTRE = {'lat': 55.752, 'lon': 37.620}
DAY_RADIUS_METERS = 2400
MAX_MIXED_CANDIDATES = 112


def selected_moscow_modes(preferences):
    if preferences.transport_modes is not None:
        return preferences.transport_modes
    if preferences.transport == 'mixed':
        return ['walk', 'taxi'] if preferences.group.pet else ['walk', 'transit', 'taxi']
    return None


def chosen_moscow_matrix(pois, mode, selected, filters=None, *, legacy=False, pair_caches=None):
    filters = filters or {}
    def cached(label, **kwargs):
        if pair_caches is not None:
            kwargs['pair_cache'] = pair_caches.setdefault(label, {})
        return travel_matrix(pois, MATRIX_MODES[label], **kwargs)
    if not selected:
        return cached(MODES[mode], **filters)
    # A short walking connection remains eligible in transit-only or taxi-only
    # itineraries; long inter-POI journeys still respect the exact selected modes.
    requested_profiles = list(dict.fromkeys([*selected, 'walk']))
    profiles = {
        label: cached(label, **({**filters, 'allow_walking': legacy} if label == 'transit' else {}))
        for label in requested_profiles
    }
    return {**profiles[MODES[mode]], 'profiles': profiles}


@lru_cache(maxsize=1)
def moscow_pois():
    # Идентификаторы уже сохранённых поездок старого пилота.
    return json.loads((BASE / 'moscow.reviewed-three.pois.json').read_text(encoding='utf-8-sig'))


@lru_cache(maxsize=1)
def moscow_v7():
    return load_moscow_v7(planning=True)


@lru_cache(maxsize=1)
def gap_index():
    return MoscowGapIndex(moscow_v7()[0])


def tokens(value):
    return set(re.findall(r'\w+', value.casefold().replace('ё', 'е')))


def cell(point):
    return floor(point['lat'] * 40), floor(point['lon'] * 25)


@lru_cache(maxsize=1)
def transport_cells():
    _, _, nodes, _ = rail_inputs(str(PILOT_RAIL_CONFIG))
    return {cell(node) for node in nodes.values()}


@lru_cache(maxsize=1)
def indexes():
    by_id, categories, words = {}, defaultdict(lambda: defaultdict(list)), defaultdict(set)
    for poi in moscow_v7()[0]:
        key = poi['id']
        by_id[key] = poi
        for category in poi['categories']:
            categories[category][cell(coordinates(poi))].append(key)
        for word in tokens(' '.join([poi['name'], *poi['search_names']])):
            if len(word) >= 4:
                words[word[:5]].add(key)
    return by_id, categories, words


def nearby_places(anchor, cells, limit, excluded=()):
    by_id = indexes()[0]
    lat, lon = cell(anchor)
    occupied = set(excluded)
    nearby_cells = sorted(cells, key=lambda grid: (max(abs(grid[0] - lat), abs(grid[1] - lon)), grid))
    rows = set()
    for grid in nearby_cells:
        rows.update(set(cells[grid]) - occupied)
        if len(rows) >= limit * 4:
            break
    def relevance(key):
        place = by_id[key]
        signals = place['catalog_signals']
        return (_distance(coordinates(place), anchor) - 180 * sum(signals.values()), key)
    return nsmallest(limit, rows, key=relevance)


def geographic_days(preferences):
    by_id, categories, _ = indexes()
    anchors = []
    for key in preferences.required_ids:
        point = coordinates(by_id[key])
        if all(_distance(point, other) >= DAY_RADIUS_METERS for other in anchors):
            anchors.append(point)
    if len(anchors) >= preferences.days:
        return anchors[:preferences.days]
    primary = preferences.primary_interests
    secondary = preferences.secondary_interests
    interests = [category for category in [*primary, *secondary] if category != 'cafes'] or ['cafes']
    stations = transport_cells() if preferences.transport in {'mixed', 'transit'} else set()
    candidate_cells = set().union(*(categories.get(category, {}) for category in interests))
    scores = []
    for lat, lon in candidate_cells:
        point = {'lat': (lat + .5) / 40, 'lon': (lon + .5) / 25}
        distance = _distance(point, CITY_CENTRE)
        if distance > 11_000:
            continue
        # Считаем доступные категории в ячейке и соседних кварталах с насыщением.
        neighbours = [(lat + dy, lon + dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
        quality = sum((3 if category in primary else 1) * min(5, sum(
            len(categories[category].get(grid, ())) for grid in neighbours)) for category in interests)
        quality += sum(min(4, len(categories[category].get((lat, lon), ()))) for category in interests)
        quality += min(4, len(categories.get('cafes', {}).get((lat, lon), ())))
        if (lat, lon) in stations:
            quality += 3
        scores.append((-quality, distance, lat, lon, point))
    if preferences.days == 1 and not anchors:
        return [CITY_CENTRE]
    for _, _, _, _, point in sorted(scores):
        if all(_distance(point, other) >= DAY_RADIUS_METERS * 1.35 for other in anchors):
            anchors.append(point)
            if len(anchors) == preferences.days:
                break
    return anchors or [CITY_CENTRE]


def _planning_price(poi):
    """Planning estimate only. Unknown is not the same as confirmed free."""
    fields = poi.get('planning', {})
    value = fields.get('average_check_rub', fields.get('price_rub', {})).get('value')
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value >= 0 else float('inf')


def daily_budget_per_person(preferences):
    group = preferences.group
    people = max(1, group.adult + group.child + group.senior)
    total = preferences.budget_amount * (people if preferences.budget_basis == 'per_person' else 1)
    return total / people / preferences.days


def _budget_constrained(preferences):
    return daily_budget_per_person(preferences) < 4000


def budget_friendly_places(anchor, cells, limit, excluded=(), *, meal_kind=None, dining_target=None,
                           visit_target=None, near_visits=(), meal_distances=None):
    """Return nearby, affordable AND experience-oriented V7 alternatives.

    The old nearest-N picker often returned several expensive restaurants,
    missing a canteen one block farther away. Keep a short distance-respecting
    Pareto pool; never pretend that an unverified attraction is free.
    """
    by_id = indexes()[0]
    lat, lon = cell(anchor)
    occupied = set(excluded)
    rows = set()
    for grid in sorted(cells, key=lambda tile: (max(abs(tile[0]-lat), abs(tile[1]-lon)), tile)):
        if max(abs(grid[0]-lat), abs(grid[1]-lon)) > 3:
            break
        rows.update(set(cells[grid]) - occupied)
        if len(rows) >= max(120, limit * 18) and meal_kind not in ('lunch', 'dinner'):
            break
    ranked = []
    for key in rows:
        place = by_id[key]
        if meal_kind:
            kind = place.get('planning', {}).get('meal_suitability', {}).get('value', {})
            food_type = place.get('planning', {}).get('food_type', {}).get('value')
            if not isinstance(kind, dict) or kind.get(meal_kind) not in ('likely', 'possible', True):
                continue
            if meal_kind in ('lunch', 'dinner') and food_type in ('coffee_shop', 'bakery', 'bar', 'pub', 'nightclub'):
                continue
        price = _planning_price(place)
        if price == float('inf'):
            continue
        # The three meal searches share an anchor and the same main visits.
        if meal_distances is not None and key in meal_distances:
            distance = meal_distances[key]
        else:
            location = coordinates(place)
            anchor_distance = _distance(location, anchor)
            distance = None
            if anchor_distance <= 5000:
                distance = (.25 * anchor_distance + .75 * min(_distance(location, point) for point in near_visits)
                            if near_visits else anchor_distance)
            if meal_distances is not None:
                meal_distances[key] = distance
        if distance is not None:
            ranked.append((key, distance, price))
    if not ranked:
        return []
    # Reserve cheapest, closest, and affordable close-by choices. Cost is per
    # person; number of travellers is accounted for by the engine later.
    def cheap_key(item):
        key, distance, price = item
        return price, distance, key

    def near_key(item):
        key, distance, price = item
        return distance, price, key

    def balanced_key(item):
        key, distance, price = item
        return distance / 8 + price, distance, key
    if visit_target is not None:
        # Keep nearby and inexpensive options, while admitting visits that
        # would otherwise be excluded solely because their ticket costs more.
        matching = nsmallest(max(1, limit // 2), ranked, key=lambda item: (
            item[1] / 12 + abs(item[2] - visit_target), item[1], item[0]))
        return list(dict.fromkeys([min(ranked, key=near_key)[0], *(key for key, _, _ in matching),
                                   min(ranked, key=cheap_key)[0],
                                   *(key for key, _, _ in nsmallest(limit, ranked, key=balanced_key))]))[:limit]
    # A generous budget is permission to consider an enjoyable full-service
    # dinner, *not* a signal to fill the route with the cheapest fast food.
    # Price alone is not a quality claim: restaurant type, meal suitability,
    # nearby geography and catalog evidence are separate scoring signals.
    if dining_target is not None and dining_target > 0 and meal_kind:
        desirable = {'breakfast': {'bakery': 3, 'cafe': 3, 'coffee_shop': 2, 'restaurant': 2,
                                  'canteen': 1, 'fast_food': -2},
                     'lunch': {'restaurant': 4, 'cafe': 3, 'canteen': 1,
                               'food_court': 1, 'fast_food': -2},
                     'dinner': {'restaurant': 5, 'cafe': 3, 'food_court': 0,
                                'canteen': 0, 'fast_food': -4}}.get(meal_kind, {})
        target = min(dining_target, {'breakfast': 1400, 'lunch': 2000, 'dinner': 2600}.get(meal_kind, 2000))

        def experience(item):
            key, metres, price = item
            poi = by_id[key]
            planning = poi.get('planning') or {}
            venue = planning.get('food_type', {}).get('value')
            suitability = planning.get('meal_suitability', {}).get('value') or {}
            suitability_bonus = 1 if suitability.get(meal_kind) == 'likely' else 0
            # Spending target is a soft, capped preference, never a minimum
            # purchase and never proof that the venue is good.
            price_mismatch = abs(min(price, target * 1.5) - target) / 260
            return (desirable.get(venue, 0) * 3 + suitability_bonus * 2
                    - price_mismatch - metres / 350)
        recommended = sorted(ranked, key=lambda item: (-experience(item), item[1], item[0]))
        full_service = [key for key, _, _ in recommended if
                        by_id[key]['planning']['food_type']['value'] == 'restaurant']
        first = full_service[:max(1, limit - 2)] if meal_kind != 'breakfast' else []
        chosen = list(dict.fromkeys([*first, min(ranked, key=cheap_key)[0], min(ranked, key=near_key)[0],
                                     *(key for key, _, _ in recommended)]))
        return chosen[:limit]
    cheap = nsmallest(max(2, (limit + 1) // 2), ranked, key=cheap_key)
    near = nsmallest(max(2, limit // 3), ranked, key=near_key)
    balanced = nsmallest(limit, ranked, key=balanced_key)
    result = dict.fromkeys([*(k for k, _, _ in cheap),
                             *(k for k, _, _ in near),
                             *(k for k, _, _ in balanced)])
    return list(result)[:limit]


def geographic_shortlist(preferences, anchors):
    """Balanced geographic POI pool with affordable meals for every day."""
    by_id, categories, _ = indexes()
    selected = dict.fromkeys(preferences.required_ids)
    interests = list(dict.fromkeys(c for c in (*preferences.primary_interests, *preferences.secondary_interests)
                                   if c != 'cafes'))
    budget_per_person_day = daily_budget_per_person(preferences)
    constrained = _budget_constrained(preferences)
    visit_target = (min(1800, budget_per_person_day * .17)
                    if not constrained and getattr(preferences, 'route_priority', 'balanced') != 'cost' else None)
    per_area = {'relaxed': 9, 'balanced': 14, 'intensive': 18}[preferences.pace]
    required_locations = [coordinates(by_id[key]) for key in preferences.required_ids]
    by_day = []
    for anchor in anchors:
        day = []
        def include(keys):
            for key in keys:
                if key not in selected:
                    selected[key] = None
                    day.append(key)
        if interests:
            for i, category in enumerate(interests):
                count = max(3, per_area // len(interests) + (i < per_area % len(interests)))
                include(budget_friendly_places(anchor, categories.get(category, {}), count, selected,
                                               visit_target=visit_target))
        # A tight budget is a reason to relax *nonmandatory* interest categories,
        # not a reason to leave an entire day without affordable visits.
        if constrained:
            for category in ('nature', 'history', 'viewpoints', 'unusual'):
                if category not in interests:
                    include(budget_friendly_places(anchor, categories.get(category, {}), 3, selected))
        # Collect meal-specific alternatives, including low-price canteens and
        # quick-service cafés, rather than only the five closest restaurants.
        main_locations = sorted((point for point in required_locations
                                 if _distance(point, anchor) <= DAY_RADIUS_METERS),
                                key=lambda point: _distance(point, anchor))[:2]
        main_locations.extend(coordinates(by_id[key]) for key in day[:3])
        food_cells = categories.get('cafes', {})
        food_choices = {}
        meal_distances = {}
        for meal in ('breakfast', 'lunch', 'dinner'):
            dining_target = (None if budget_per_person_day < 2800 else
                             budget_per_person_day * {'breakfast': .09, 'lunch': .14, 'dinner': .20}[meal])
            choices = budget_friendly_places(anchor, food_cells, 5, selected,
                                             meal_kind=meal, dining_target=dining_target,
                                             near_visits=main_locations, meal_distances=meal_distances)
            food_choices[meal] = choices
            selected.update(dict.fromkeys(choices))
        # Interleave meals and sights before the global cutoff. Previously
        # distant days could exhaust the matrix before any meal was admitted.
        lunch, dinner, breakfast = (food_choices[kind] for kind in ('lunch', 'dinner', 'breakfast'))
        by_day.append([*lunch[:1], *dinner[:1], *day[:2], *lunch[1:2], *dinner[1:2],
                       *breakfast[:1], *day[2:], *lunch[2:], *dinner[2:], *breakfast[1:]])
    # Round-robin preserves each day's geographic and meal choices. The matrix
    # scales quadratically; protect mandatory stops and retain a bounded pool.
    if len(selected) > MAX_MIXED_CANDIDATES:
        ordered = dict.fromkeys(preferences.required_ids)
        for index in range(max(map(len, by_day), default=0)):
            for day in by_day:
                if index < len(day):
                    ordered[day[index]] = None
                    if len(ordered) >= max(MAX_MIXED_CANDIDATES, len(preferences.required_ids)):
                        break
            if len(ordered) >= max(MAX_MIXED_CANDIDATES, len(preferences.required_ids)):
                break
        selected = ordered
    return [by_id[key] for key in selected]


def shortlist(preferences, retained=(), query='', prior_query=''):
    by_id, categories, words = indexes()
    ids = list(dict.fromkeys([*preferences.required_ids, *retained]))
    if not set(ids) <= by_id.keys():
        raise HTTPException(422, 'Место отсутствует в каталоге Москвы V7.')
    anchors = [coordinates(by_id[key]) for key in preferences.required_ids] or [CITY_CENTRE]
    def location(key):
        return min(_distance(coordinates(by_id[key]), anchor) for anchor in anchors)
    if query:
        terms = tokens(query)
        older_terms = tokens(prior_query) - terms
        matches = defaultdict(int)
        for term in terms | older_terms:
            if len(term) >= 4:
                candidates = words.get(term[:5], ())
                for key in candidates:
                    matches[key] += 4 if term in terms else 1
        ids.extend(sorted(matches, key=lambda key: (-matches[key], location(key), key))[:5])
        named = sorted((key for key in retained if key in matches), key=lambda key: (-matches[key], key))
        if named:
            anchors = [coordinates(by_id[key]) for key in named[:3]]
        elif retained and not preferences.required_ids:
            anchors = [coordinates(by_id[key]) for key in retained]
        category_words = {
            'музе': 'museums', 'парк': 'nature', 'сад': 'nature', 'истор': 'history',
            'кафе': 'cafes', 'рестор': 'cafes', 'необыч': 'unusual', 'смотров': 'viewpoints',
            'магазин': 'shopping', 'театр': 'entertainment',
        }
        requested_categories = {category for word in terms for prefix, category in category_words.items()
                                if word.startswith(prefix)}
        for category in sorted(requested_categories):
            for anchor in anchors[:5]:
                ids.extend(nearby_places(anchor, categories.get(category, {}), 2, ids))
    interests = preferences.primary_interests + preferences.secondary_interests
    for category in interests:
        cells = categories.get(category, {})
        limit = max(1, (max(12, preferences.days * 3) if not query else 8) // len(interests))
        rows = set()
        for anchor in anchors:
            lat, lon = cell(anchor)
            nearby = sorted(cells, key=lambda grid: (max(abs(grid[0] - lat), abs(grid[1] - lon)), grid))
            count = 0
            for grid in nearby:
                rows.update(cells[grid])
                count += len(cells[grid])
                if count >= limit * 4:
                    break
        ids.extend(nsmallest(limit, rows, key=lambda key: (location(key), key)))
    ids = list(dict.fromkeys(ids))
    if preferences.transport == 'mixed':
        protected = len(set(preferences.required_ids) | set(retained))
        ids = ids[:max(MAX_MIXED_CANDIDATES, protected)]
    return [by_id[key] for key in ids]


def stamp(day, clock):
    return f'{day}T{clock}:00+03:00'


WEEKDAYS = ('Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su')


def source_hours(raw, weekday):
    if raw == '24/7':
        return ('00:00', '23:59')
    if not isinstance(raw, str):
        return None
    windows = []
    for rule in raw.split(';'):
        match = re.fullmatch(r'(?:(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))? )?'
                             r'(?:((?:[01]\d|2[0-3]):[0-5]\d)-((?:[01]\d|2[0-3]):[0-5]\d|24:00)|off)', rule.strip())
        if match is None:
            return None
        first, last, opens, closes = match.groups()
        if first and last and WEEKDAYS.index(last) < WEEKDAYS.index(first):
            return None
        if opens and opens >= closes:
            return None
        if first is None or WEEKDAYS.index(first) <= weekday <= WEEKDAYS.index(last or first):
            windows.append((opens, '23:59' if closes == '24:00' else closes) if opens else ())
    if not windows:
        return ()
    return windows[0] if len(windows) == 1 else None


def moscow_inputs(preferences, retained=(), query='', prior_query='', transport_filters=None, *,
                  include_food=False, explicit_pois=None, pair_caches=None, timetable_only=False):
    if preferences.budget_currency != 'RUB':
        raise HTTPException(422, 'Для Москвы выберите бюджет в рублях.')
    pois = (shortlist(preferences, retained, query, prior_query) if explicit_pois is None
            else [indexes()[0][key] for key in dict.fromkeys([*preferences.required_ids, *explicit_pois])])
    anchors = []
    if include_food:
        anchors = geographic_days(preferences)
        pois = geographic_shortlist(preferences, anchors)
    selected = selected_moscow_modes(preferences)
    mode = MATRIX_MODES[selected[0]] if selected else MATRIX_MODES.get(preferences.transport, 'public_transit')
    people = preferences.group.adult + preferences.group.child + preferences.group.senior
    amount = preferences.budget_amount * (people if preferences.budget_basis == 'per_person' else 1)
    first = preferences.start_date
    profile = dict(schema_version='0.2', destination={'country_code': 'RU', 'city': 'Москва'},
                   date_start=first.isoformat(), date_end=(first + timedelta(days=preferences.days - 1)).isoformat(),
                   timezone='Europe/Moscow', primary_interests=preferences.primary_interests,
                   secondary_interests=preferences.secondary_interests, required_poi_ids=preferences.required_ids,
                   pace=preferences.pace, transport=mode, top_k=max(1, min(len(pois), max(12, preferences.days * 3))),
                   budget={'amount': amount, 'currency': 'RUB', 'scope': 'trip_food_and_entertainment'})
    days, openings, durations, costs = [], [], [], []
    for offset in range(preferences.days):
        day = (first + timedelta(days=offset)).isoformat()
        individual = (preferences.day_settings or {}).get(first + timedelta(days=offset), {})
        days.append(dict(date=day, day_start=stamp(day, individual.get('day_start', preferences.day_start)),
                         day_end=stamp(day, individual.get('day_end', preferences.day_end)), meal_break=None))
    for poi in pois:
        key, fields, provenance = poi['id'], poi['planning'], poi['provenance']
        minutes = fields['duration_minutes']['value']
        if preferences.visit_minutes is not None and key in preferences.required_ids:
            minutes = preferences.visit_minutes
        durations.append(dict(poi_id=key, minutes=minutes, basis='expert_estimate',
                              method_version=fields['duration_minutes']['model_version'], author='TripWise',
                              recorded_at=provenance['retrieved_at'], evidence_url=provenance['source_url']))
        price = fields.get('average_check_rub', fields['price_rub']) if 'cafes' in poi['categories'] else fields['price_rub']
        value = price['value']
        costs.append(dict(poi_id=key, amount=value * people if type(value) in (int, float) else None,
                          basis='source' if price['source_type'] == 'source' else 'expert_estimate',
                          source_url=provenance['source_url'], recorded_at=provenance['retrieved_at']))
        hours = fields['opening_hours']
        for offset, entry in enumerate(days):
            day = entry['date']
            weekday = (first.weekday() + offset) % 7
            estimated = hours['source_type'] == 'synthetic'
            # Для обязательных мест оценка часов не доказывает закрытие.
            if estimated and key in preferences.required_ids:
                continue
            window = hours['value'][weekday] if estimated else source_hours(hours['value'], weekday)
            if window is None and not estimated:
                continue
            openings.append(dict(poi_id=key, date=day, timezone=profile['timezone'],
                                 intervals=[] if not window else [dict(start_at=stamp(day, window[0]), end_at=stamp(day, window[1]))],
                                 source_id=hours['model_version'] if estimated else provenance['source_id'],
                                 retrieved_at=provenance['retrieved_at'], source_url=None if estimated else provenance['source_url']))
    context = dict(schema_version='0.2', endpoint_policy='first_last_poi', pace_policy_version='pace-policy-v0.2',
                   unknown_duration_policy='strict', poi_snapshot_id=moscow_v7()[1], days=days,
                   # An explicitly entered visit duration is a hard constraint for every pace.
                   moscow_user_duration_ids=(list(preferences.required_ids) if preferences.visit_minutes is not None else []),
                   optimize_local_order=True, moscow_has_pet=bool(preferences.group.pet), moscow_group=preferences.group.model_dump(),
                   route_priority=preferences.route_priority,
                   duration_overrides=durations, opening_intervals=openings,
                   cost_context=dict(currency='RUB', coverage_complete=False, visit_costs=costs, meal_costs=[]))
    filters = transport_filters or {}
    matrix = None if timetable_only else chosen_moscow_matrix(
        pois, mode, selected, filters, pair_caches=pair_caches,
        legacy=preferences.transport_modes is None and bool(retained))
    if selected:
        context['moscow_transport_modes'] = selected
    if filters:
        context['moscow_transit_filters'] = filters
    if len(anchors) > 1:
        context['day_anchors'] = {day['date']: {**point, 'radius_m': DAY_RADIUS_METERS}
                                  for day, point in zip(days, anchors)}
    return profile, pois, None if timetable_only else recommend(profile, pois), matrix, context


def fix_meal_time(context, date, key, kind, start, end):
    hours = next((row for row in context['opening_intervals'] if row['poi_id'] == key and row['date'] == date), None)
    if not start < end or hours and not any(window['start_at'] <= start < end <= window['end_at'] for window in hours['intervals']):
        return False
    if hours:
        hours['intervals'] = [{'start_at': start, 'end_at': end}]
    if kind == 'lunch':
        next(row for row in context['days'] if row['date'] == date)['meal_break'] = None
    context.setdefault('moscow_meals', {}).setdefault(date, {})[kind] = {
        'poi_id': key, 'start_at': start, 'end_at': end,
    }
    return True


def plan_moscow(preferences):
    pair_caches = {}
    inputs = moscow_inputs(preferences, include_food=True, pair_caches=pair_caches)
    try:
        plan = plan_city(inputs[0], inputs[1], inputs[3], inputs[4])
    except AssistantError as error:
        if error.code in {'ROUTER_RUNTIME_UNAVAILABLE', 'ROUTER_UNAVAILABLE'}:
            raise HTTPException(503, 'Маршрутизатор недоступен. Проверьте запуск Node.js.') from error
        raise HTTPException(422, 'Маршрутизатор не смог построить план с этими параметрами.') from error
    def make_repair_state(ids):
        profile, places, _, matrix, context = moscow_inputs(preferences, explicit_pois=ids, pair_caches=pair_caches)
        return {'preferences': profile, 'pois': places, 'travel_times': matrix, 'routing_context': context}

    plan = repair_gaps(plan, inputs[0], inputs[4], gap_index(), make_repair_state)
    if any(gap['kind'] == 'internal' and gap['minutes'] > 60
           for gap in plan['gap_diagnostics']['remaining']):
        ids = [row['poi_id'] for day in plan['days'] for row in day['visits']]
        profile, places, _, _, context = moscow_inputs(preferences, explicit_pois=ids, timetable_only=True)
        try:
            plan = stretch_saved_visits(plan, profile, places, context)
            refresh_gap_report(plan, context)
        except AssistantError as error:
            if error.code not in {'ROUTER_RUNTIME_UNAVAILABLE', 'ROUTER_UNAVAILABLE'}:
                raise
    options = {'start_date': preferences.start_date.isoformat(), 'transport': preferences.transport}
    if preferences.transport_modes:
        options['transport_modes'] = preferences.transport_modes
    return plan, options, plan['poi_snapshot_id']


TRANSPORTS = {'walking': 'walk', 'car': 'taxi', 'public_transit': 'transit'}


class MoscowRouteAssistant(RouteAssistant):
    def __init__(self, *args, moscow_preferences=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.moscow_preferences = moscow_preferences

    def _recenter_day(self, state, sequences, day, anchor):
        require(day in sequences, 'UNKNOWN_DAY')
        context = state['routing_context']
        require(not context.get('moscow_meal_pins', {}).get(day), 'MEAL_TIME_UNAVAILABLE')
        other = {key for date, ids in sequences.items() if date != day for key in ids}
        require(anchor not in other, 'DUPLICATE_VISIT')
        required = set(state['preferences']['required_poi_ids'])
        locked = required.intersection(sequences[day]) | {anchor}
        pool = context.get('recenter_candidates', {}).get(anchor)
        require(pool is not None, 'NAMED_POI_NOT_IN_CATALOG')
        candidates = set(pool) | locked
        catalog = [poi for poi in state['pois'] if poi['id'] in candidates - other]
        profile = deepcopy(state['preferences'])
        profile.update(date_start=day, date_end=day, required_poi_ids=sorted(locked),
                       pace=context.get('day_pace_overrides', {}).get(day, profile['pace']))
        native_days = state['route'].get('engine_days', [])
        require(len(native_days) == len(sequences), 'INVALID_ROUTE_SNAPSHOT')
        profile['budget']['amount'] = max(0, profile['budget']['amount'] - sum(
            row['total_cost_cny'] for row in native_days if row['date'] != day))
        day_settings = next(row for row in context['days'] if row['date'] == day)
        local = {**context, 'days': [day_settings], 'day_anchors': {
            day: {**coordinates(indexes()[0][anchor]), 'radius_m': 1500}}}
        candidate = plan_city(profile, catalog, state['travel_times'], local)
        proposed = sequences_of(candidate)[day]
        require(locked <= set(proposed), 'NO_FEASIBLE_NAMED_INSERTION')
        sequences[day] = proposed
        context.setdefault('moscow_meals', {})[day] = candidate.get('moscow_meals', {}).get(day, {})
        events = candidate['engine_days'][0]['events']
        context.setdefault('moscow_start_hints', {})[day] = {
            row['poi_id']: row['start_min'] for row in events if 'poi_id' in row}

    def _fill_gaps(self, state, sequences, days):
        """Replan requested days using the V7 spatial index and the SAME scheduler.

        The candidate is recomputed against a complete, expanded matrix. All
        edits still go through RouteAssistant preview and confirm validation.
        """
        require(isinstance(days, list) and 1 <= len(days) <= MAX_TRIP_DAYS and
                len(days) == len(set(days)) and all(day in sequences for day in days), 'UNKNOWN_DAY')
        require(self.moscow_preferences is not None, 'ROUTER_UNAVAILABLE')
        base = state['route']
        require(sequences_of(base) == sequences, 'ROUTE_SEQUENCE_MISMATCH')
        original_context = state['routing_context']
        saved_meals = deepcopy(base.get('moscow_meals', {}))
        caches = {}

        def make_state(ids):
            profile, pois, ranking, matrix, context = moscow_inputs(
                self.moscow_preferences, explicit_pois=ids, pair_caches=caches)
            context['moscow_original_hours'] = deepcopy(context['opening_intervals'])
            context['moscow_meals'] = deepcopy(saved_meals)
            for date, meals in saved_meals.items():
                for kind, slot in meals.items():
                    # Preserve actual booked meal windows and do not manufacture
                    # a free meal or move a committed restaurant silently.
                    if slot.get('start_at') and slot.get('end_at'):
                        require(fix_meal_time(context, date, slot['poi_id'], kind,
                                              slot['start_at'], slot['end_at']), 'MEAL_TIME_UNAVAILABLE')
            for key in ('day_pace_overrides', 'day_anchors', 'moscow_meal_pins',
                        'moscow_transit_filters', 'moscow_start_hints', 'visit_times'):
                if key in original_context:
                    context[key] = deepcopy(original_context[key])
            return {'preferences': profile, 'pois': pois, 'recommendation': ranking,
                    'travel_times': matrix, 'routing_context': context}

        # Explicitly budget the search across requested dates. A 400-minute gap
        # on day 2 must not consume all 48 trials and starve days 3 and 4.
        result, per_day, inserted = base, [], []
        checked_total = attempts_total = 0
        for date in days:
            result = repair_gaps(result, state['preferences'], original_context,
                                 gap_index(), make_state, only_days=[date],
                                 max_schedule_trials=16, max_searched_windows=5,
                                 allow_any_categories=True)
            report = result.get('gap_diagnostics', {})
            per_day.extend(report.get('remaining', []))
            inserted.extend(report.get('inserted_poi_ids', []))
            attempts_total += report.get('attempts', 0)
            checked_total += report.get('candidate_count', 0)
            saved_meals = deepcopy(result.get('moscow_meals', saved_meals))
        from ai_ds.moscow_gap_repair import route_gaps, LONG_GAP
        before = [gap for gap in route_gaps(base, original_context) if gap.day in days]
        after = [gap for gap in route_gaps(result, original_context) if gap.day in days]
        result['gap_diagnostics'] = {
            'before': sum(gap.minutes > LONG_GAP for gap in before),
            'after': sum(gap.minutes > LONG_GAP for gap in after),
            'attempts': attempts_total, 'candidate_count': checked_total,
            'inserted_poi_ids': list(dict.fromkeys(inserted)),
            'remaining': per_day,
        }
        updated = sequences_of(result)
        expanded = make_state(tuple(dict.fromkeys(k for seq in updated.values() for k in seq))) if updated != sequences else state
        if any(gap['kind'] == 'internal' and gap['minutes'] > 60 for gap in per_day):
            result = stretch_saved_visits(deepcopy(result), expanded['preferences'], expanded['pois'],
                                           expanded['routing_context'], only_days=days)
            refresh_gap_report(result, original_context, only_days=days)
        require(result['days'] != base['days'], 'NO_FEASIBLE_GAP_FILL')
        state.update({key: deepcopy(expanded[key]) for key in
                      ('preferences', 'pois', 'recommendation', 'travel_times', 'routing_context')})
        # Full route stays in memory for the deterministic revalidation and
        # proposed preview; the authoritative JS scheduler builds it again.
        state['route'] = deepcopy(result)
        sequences.clear()
        sequences.update(updated)

    def _payload(self, state, text):
        result = super()._payload(state, text)
        selected = state['routing_context'].get('moscow_transport_modes')
        result['transport'] = 'mixed' if selected and len(selected) > 1 else TRANSPORTS[state['preferences']['transport']]
        if selected:
            result['transport_modes'] = selected
        filters = state['routing_context'].get('moscow_transit_filters')
        if filters:
            result['transit_filters'] = filters
        names = {poi['id']: poi['name'] for poi in state['pois']}
        cafes = {poi['id']: poi['planning']['average_check_rub']['value'] for poi in state['pois']
                 if 'average_check_rub' in poi['planning']}
        for entry in result['catalog']:
            price = cafes.get(entry['poi_id'])
            if type(price) in (int, float):
                entry.update(kind='restaurant', price_rub=price)
        for number, (payload, day) in enumerate(zip(result['days'], state['route']['days']), 1):
            meals = {slot['poi_id']: kind for kind, slot in state['route'].get('moscow_meals', {}).get(day['date'], {}).items()}
            payload['day_number'] = number
            payload['visits'] = [{
                'poi_id': row['poi_id'], 'name': names[row['poi_id']], 'visit_number': index,
                'start_time': row['start_at'][11:16], 'end_time': row['end_at'][11:16],
                **({'type': 'meal', 'meal_kind': meals[row['poi_id']]} if row['poi_id'] in meals else {'type': 'visit'}),
            } for index, row in enumerate(day['visits'], 1)]
        return result

    def _settings(self, state, day):
        window = next(item for item in state['routing_context']['days'] if item['date'] == day)
        return {**super()._settings(state, day), 'transport': state['preferences']['transport'],
                'window': (window['day_start'], window['day_end']),
                'transport_modes': state['routing_context'].get('moscow_transport_modes'),
                'meals': state['routing_context'].get('moscow_meals', {}).get(day, {}),
                'transit_filters': state['routing_context'].get('moscow_transit_filters', {}),
                'start_hints': state['routing_context'].get('moscow_start_hints', {}).get(day, {})}

    @staticmethod
    def _chat_leg_times(state):
        matrix = state['travel_times']
        selected = set(state['routing_context'].get('moscow_transport_modes') or
                       [MODES[state['preferences']['transport']]])
        profiles = matrix.get('profiles') or {next(iter(selected)): matrix}
        times = {}
        for label, profile in profiles.items():
            for row in profile['pairs']:
                seconds = row.get('duration_seconds')
                if row.get('status') != 'known' or seconds is None:
                    continue
                local_walk = label == 'walk' and (row.get('distance_meters') or 0) <= (
                    600 if selected == {'taxi'} else 1000)
                if label not in selected and not local_walk:
                    continue
                pair = row['from_poi_id'], row['to_poi_id']
                times[pair] = min(times.get(pair, float('inf')), seconds)
        return times

    def _meal_variants(self, state, sequences, dates, times, protected_id, base):
        context = state['routing_context']
        protected = set(state['preferences']['required_poi_ids']) | set(state.get('named_added_ids', ())) | {protected_id}
        points = {point['id']: point.get('planning', {}) for point in state['pois']}
        people = sum(context.get('moscow_group', {}).get(key, 0) for key in ('adult', 'child', 'senior'))
        party_budget = state['preferences'].get('budget', {}).get('amount', 0)
        comfortable_budget = party_budget >= 4000 * max(1, people) * len(sequences)
        # One starting sequence and up to three local repair paths. Keep the
        # existing sequence available for the authoritative schedule check.
        beam = [(0, sequences, {})]
        for date in dates:
            existing_pairs = set(zip(base[date], base[date][1:]))
            pins = context.get('moscow_meal_pins', {}).get(date, {})
            for kind, slot in context.get('moscow_meals', {}).get(date, {}).items():
                old = slot['poi_id']
                if kind not in ('breakfast', 'lunch', 'dinner') or kind in pins or old in protected:
                    continue
                variants = []
                for score, current, changes in beam:
                    variants.append((score, current, changes))
                    seq = current[date]
                    if old not in seq or len(seq) <= 1:
                        continue
                    index = seq.index(old)
                    before = seq[index - 1] if index else None
                    after = seq[index + 1] if index + 1 < len(seq) else None
                    if all(pair in existing_pairs for pair in ((before, old), (old, after)) if all(pair)):
                        continue
                    def edges(key):
                        return [times.get((a, b), 7200) for a, b in ((before, key), (key, after)) if a and b]
                    old_edges = edges(old)
                    if max(old_edges) < 20 * 60:
                        continue
                    in_use = {key for ids in current.values() for key in ids}
                    candidates = []
                    old_type = points.get(old, {}).get('food_type', {}).get('value')
                    for key, fields in points.items():
                        suitability = fields.get('meal_suitability', {}).get('value') or {}
                        food_type = fields.get('food_type', {}).get('value')
                        if (key in in_use or suitability.get(kind) not in ('likely', 'possible', True)
                                or kind != 'breakfast' and food_type in (
                                    'coffee_shop', 'bakery', 'dessert', 'bar', 'pub', 'nightclub')):
                            continue
                        if (comfortable_budget and kind != 'breakfast' and old_type == 'restaurant'
                                and food_type not in ('restaurant', 'cafe')):
                            continue
                        legs = edges(key)
                        if max(legs) >= max(old_edges) or sum(legs) + 5 * 60 >= sum(old_edges):
                            continue
                        if not self._named_open_days(context, key, [date]):
                            continue
                        candidates.append((sum(legs), key))
                    for seconds, key in sorted(candidates)[:3]:
                        replaced = seq[:index] + [key] + seq[index + 1:]
                        variants.append((score + seconds - sum(old_edges), {**current, date: replaced},
                                         {**changes, (date, kind): {**slot, 'poi_id': key}}))
                beam = sorted(variants, key=lambda row: row[0])[:3]
        return [(sequences, {}), *((seq, changes) for _, seq, changes in beam if changes)]

    def _best_addition(self, state, sequences, poi_id, days):
        if not state.get('route') or not state.get('travel_times'):
            return super()._best_addition(state, sequences, poi_id, days)
        base = sequences_of(state['route'])
        times = self._chat_leg_times(state)
        choices = []
        for day in days:
            for position in range(len(sequences[day]) + 1):
                seq = sequences[day]
                trial = {**sequences, day: seq[:position] + [poi_id] + seq[position:]}
                changed = [date for date in trial if trial[date] != base[date]]
                for variant, meals in self._meal_variants(state, trial, changed, times, poi_id, base):
                    trial_state = {**state, 'chat_insertion_days': changed}
                    if meals:
                        context = deepcopy(state['routing_context'])
                        trial_state['routing_context'] = context
                        for (date, kind), slot in meals.items():
                            context['moscow_meals'][date][kind] = slot
                            context.setdefault('moscow_start_hints', {}).setdefault(date, {})[slot['poi_id']] = minutes(
                                slot['start_at'][11:16])
                    try:
                        plan = self._candidate(trial_state, variant)
                    except AssistantError as error:
                        if error.code in BRIDGE_FAILURE_CODES:
                            raise
                        continue
                    except (ValueError, KeyError, TypeError, OverflowError):
                        continue
                    choices.append(((*self._addition_score(plan, day), day, position), variant, trial_state))
        if choices:
            _, variant, best = min(choices, key=lambda row: row[0])
            state['routing_context'] = best['routing_context']
            state['chat_insertion_days'] = best['chat_insertion_days']
            return variant
        return None

    @staticmethod
    def _guarded_insertion_matrix(state, sequences):
        dates = state.get('chat_insertion_days')
        if not dates:
            return state['travel_times']
        base = sequences_of(state['route'])
        extra = set()
        for date in dates:
            old, new = base[date], sequences[date]
            extra.update(set(zip(new, new[1:])) - set(zip(old, old[1:])))
        group = state['routing_context'].get('moscow_group', {})
        factor = 1.2 if group.get('senior') else 1.12 if group.get('child') else 1
        def allow(pair, label):
            walk = label == 'walk' or label == 'transit' and not pair.get('transport_steps')
            ids = pair['from_poi_id'], pair['to_poi_id']
            return not walk or ids not in extra or (pair.get('duration_seconds') or 0) * factor <= 3600
        matrix = state['travel_times']
        label = MODES[state['preferences']['transport']]
        if not matrix.get('profiles'):
            return {**matrix, 'pairs': [row for row in matrix['pairs'] if allow(row, label)]}
        profiles = {key: {**profile, 'pairs': [row for row in profile['pairs'] if allow(row, key)]}
                    for key, profile in matrix['profiles'].items()}
        return {**matrix, **profiles.get(label, {}), 'profiles': profiles}

    @staticmethod
    def _check_insertion_geography(state, plan):
        dates = state.get('chat_insertion_days')
        if not dates:
            return
        base = sequences_of(state['route'])
        points = {p['id']: coordinates(p) for p in state['pois'] if 'coordinates' in p}
        for day in plan['days']:
            date = day['date']
            if date not in dates:
                continue
            old = base[date]
            previous_pairs = set(zip(old, old[1:]))
            legs = day['legs']
            for inbound, outbound in zip(legs, legs[1:]):
                a, b = inbound['from_poi_id'], inbound['to_poi_id']
                c = outbound['to_poi_id']
                durations = inbound['duration_seconds'] or 0, outbound['duration_seconds'] or 0
                if ((a, b) in previous_pairs or (b, c) in previous_pairs or
                        min(durations) < 25 * 60 or sum(durations) < 75 * 60 or
                        not {a, b, c} <= points.keys()):
                    continue
                direct = _distance(points[a], points[c])
                outward = min(_distance(points[a], points[b]), _distance(points[b], points[c]))
                # The user does not gain anything by leaving a neighbourhood
                # and immediately returning for two neighbouring stops.
                require(direct > outward * .45, 'CHAT_DETOUR_EXCEEDED')

    @staticmethod
    def _named_protected(state):
        context = state['routing_context']
        return (set(state['preferences']['required_poi_ids']) |
                {slot['poi_id'] for meals in context.get('moscow_meals', {}).values()
                 for slot in meals.values()} |
                {poi_id for pins in context.get('visit_times', {}).values() for poi_id in pins} |
                set(state.get('named_added_ids', ())))

    @staticmethod
    def _addition_score(candidate, day):
        excess_wait = sum(max(0, gap['minutes'] - 60)
                          for gap in candidate.get('gap_diagnostics', {}).get('remaining', [])
                          if gap['kind'] == 'internal')
        return (excess_wait, *RouteAssistant._addition_score(candidate, day))

    @staticmethod
    def _named_open_days(context, poi_id, days):
        windows = {item['date']: item for item in context.get('days', [])}
        openings = {item['date']: item['intervals'] for item in context.get('opening_intervals', [])
                    if item['poi_id'] == poi_id}
        return [day for day in days if day not in windows or openings.get(day) is None or any(
            max(interval['start_at'], windows[day]['day_start']) <
            min(interval['end_at'], windows[day]['day_end']) for interval in openings[day])]

    def _replace_for_named_addition(self, state, sequences, wanted, targets, *, one_only=False):
        protected = self._named_protected(state)
        ranking = {row['poi_id']: i for i, row in enumerate(state.get('recommendation', {}).get('items', []))}
        auto_filled = set(state.get('route', {}).get('gap_diagnostics', {}).get('inserted_poi_ids', []))
        ordered = {day: sorted((key for key in sequences[day] if key not in protected),
                               key=lambda key: (key not in auto_filled, -ranking.get(key, len(ranking)), key))
                   for day in targets}
        depth = min(max(map(len, ordered.values()), default=0), 1 if one_only else 4)
        trials = 0
        # Smallest removal sets first. Interleave days so a packed day cannot
        # consume the entire search budget before trying the next day.
        for count in range(1, depth + 1):
            sets = [combinations(ordered[day], count) for day in targets]
            for row in zip_longest(*sets):
                for day, removed in zip(targets, row):
                    if removed is None:
                        continue
                    trials += 1
                    if trials > 40:
                        return None
                    reduced = {**sequences, day: [poi for poi in sequences[day] if poi not in removed]}
                    attempt = self._best_addition(state, reduced, wanted, [day])
                    if attempt is not None:
                        return attempt
        return None

    def _relocate_for_named_addition(self, state, sequences, wanted, targets):
        """Try moving a flexible stop to a different day before removing it.

        Bounded to avoid expensive full-matrix search. Each intermediate and
        final change is checked by the existing authoritative JS scheduler.
        Meals and required stops are never relocated automatically.
        """
        if len(sequences) < 2:
            return None
        protected = self._named_protected(state)
        trials = 0
        for day in targets:
            others = sorted((key for key in sequences if key != day),
                            key=lambda key: (len(sequences[key]), key))[:3]
            for old in reversed(sequences[day]):
                if old in protected or old == wanted:
                    continue
                for destination in others:
                    trials += 1
                    if trials > 10:
                        return None
                    reduced = deepcopy(sequences)
                    reduced[day].remove(old)
                    trial = {**state, 'routing_context': deepcopy(state['routing_context'])}
                    moved = self._best_addition(trial, reduced, old, [destination])
                    if moved is None:
                        continue
                    inserted = self._best_addition(trial, moved, wanted, [day])
                    if inserted is not None:
                        state.update(trial)
                        return inserted
        return None

    def _apply(self, state, sequences, op):
        if op['op'] == 'pin_visit_time':
            day, key = op['day_date'], op['poi_id']
            require(key in sequences.get(day, []), 'TARGET_DAY_MISMATCH')
            optimized = fixed_sequence(state, sequences, optimize=op)
            sequences[day] = sequences_of(optimized)[day]
            context = state['routing_context']
            context.setdefault('visit_times', {}).setdefault(day, {})[key] = op['start_time']
            events = next(item['events'] for item in optimized['engine_days'] if item['date'] == day)
            context.setdefault('moscow_start_hints', {})[day] = {
                event['poi_id']: event['start_min'] for event in events if 'poi_id' in event
            }
            return
        if op['op'] == 'set_day_window':
            day = op['day']
            require(day in list(sequences)[1:-1], 'MIDDLE_DAY_REQUIRED')
            context = state['routing_context']
            window = next(item for item in context['days'] if item['date'] == day)
            start = op['start_time'] or window['day_start'][11:16]
            end = op['end_time'] or window['day_end'][11:16]
            require(minutes(end) - minutes(start) >= 60, 'INVALID_DAY_WINDOW')
            if (start, end) == (window['day_start'][11:16], window['day_end'][11:16]):
                return
            window.update(day_start=stamp(day, start), day_end=stamp(day, end))
            state.setdefault('reflow_days', []).append(day)
            context.get('moscow_start_hints', {}).pop(day, None)
            window['meal_break'] = None
            protected = set(state['preferences']['required_poi_ids'])
            protected.update(context.get('visit_times', {}).get(day, {}))
            pinned = context.get('moscow_meal_pins', {}).get(day, {})
            protected.update(slot['poi_id'] for kind, slot in context.get('moscow_meals', {}).get(day, {}).items()
                             if kind in pinned)
            trial = sequences[day][:]
            removable = [key for key in reversed(trial) if key not in protected]
            for drop in [None, *removable]:
                if drop is not None:
                    trial.remove(drop)
                if not trial and sequences[day]:
                    break
                trial_context = deepcopy(context)
                meals = trial_context.get('moscow_meals', {}).get(day, {})
                for kind, slot in list(meals.items()):
                    if slot['poi_id'] not in trial:
                        del meals[kind]
                try:
                    fixed_sequence({**state, 'routing_context': trial_context}, {**sequences, day: trial})
                except AssistantError as error:
                    if error.code not in {'DAY_END_EXCEEDED', 'OUTSIDE_OPENING_HOURS', 'MEAL_TIME_UNAVAILABLE',
                                          'TRANSPORT_UNAVAILABLE', 'EDIT_CONSTRAINT_FAILED'}:
                        raise
                    continue
                sequences[day] = trial
                return
            raise AssistantError('DAY_END_EXCEEDED')
        if op['op'] == 'add_category_poi':
            # Interpret generic attraction requests locally, but do not create
            # a second scheduler. Select existing shortlist POIs and validate
            # every insertion through the normal preview/confirm pipeline.
            category, day = op['category'], op['day']
            require(category in {'museums', 'history', 'nature', 'entertainment',
                                 'shopping', 'viewpoints', 'unusual'}, 'INVALID_CATEGORY')
            require(day is None or day in sequences, 'UNKNOWN_DAY')
            placed = {key for ids in sequences.values() for key in ids}
            candidates = [poi['id'] for poi in state['pois']
                          if category in poi['categories'] and poi['id'] not in placed]
            # The recommendation's ranking is already generated from interests
            # and available catalog signals; never equate expense with quality.
            ranks = {item['poi_id']: i for i, item in
                     enumerate(state.get('recommendation', {}).get('items', []))}
            candidates.sort(key=lambda key: (ranks.get(key, 10**6), key))
            targets = [day] if day is not None else list(sequences)
            for key in candidates[:18]:
                attempt = self._best_addition(state, sequences, key, targets)
                if attempt is not None:
                    sequences.clear()
                    sequences.update(attempt)
                    return
            raise AssistantError('NO_FEASIBLE_INSERTION')
        if op['op'] == 'fill_gaps':
            return self._fill_gaps(state, sequences, op['days'])
        context = state['routing_context']
        if op['op'] == 'recenter_day':
            return self._recenter_day(state, sequences, op['day'], op['poi_id'])
        if op['op'] in ('add_named_poi', 'replace_any_named'):
            key, day = op['poi_id'], op['day']
            require(key in {poi['id'] for poi in state['pois']}, 'NAMED_POI_NOT_IN_CATALOG')
            require(not any(key in seq for seq in sequences.values()), 'DUPLICATE_VISIT')
            targets = [day] if day is not None else list(sequences)
            require(all(date in sequences for date in targets), 'UNKNOWN_DAY')
            targets = self._named_open_days(context, key, targets)
            forced = op['op'] == 'replace_any_named'
            choice = None if forced else self._best_addition(state, sequences, key, targets)
            if choice is None and not forced:
                choice = self._relocate_for_named_addition(state, sequences, key, targets)
            if choice is None and (forced or op['replace_if_needed']):
                choice = self._replace_for_named_addition(state, sequences, key, targets, one_only=forced)
            require(choice is not None, 'NO_FEASIBLE_NAMED_INSERTION')
            state.setdefault('named_added_ids', []).append(key)
            sequences.clear()
            sequences.update(choice)
            return
        if op['op'] == 'add_poi':
            try:
                return super()._apply(state, sequences, op)
            except AssistantError as error:
                if error.code != 'NO_FEASIBLE_INSERTION':
                    raise
                # Автоматическое заполнение окна уступает явному запросу.
                # Удаление выбранной автоматически остановки видно в предпросмотре.
                fillers = set(state['route'].get('gap_diagnostics', {}).get('inserted_poi_ids', []))
                protected = set(state['preferences']['required_poi_ids'])
                protected.update(slot['poi_id'] for meals in context.get('moscow_meals', {}).values()
                                 for slot in meals.values())
                days = [op['day']] if op['day'] else list(sequences)
                for day in days:
                    for key in reversed(sequences[day]):
                        if key not in fillers or key in protected:
                            continue
                        reduced = {**sequences, day: [value for value in sequences[day] if value != key]}
                        attempt = self._best_addition(state, reduced, op['poi_id'], [day])
                        if attempt:
                            sequences.update(attempt)
                            return
                raise
        if op['op'] == 'optimize_day':
            day, goal = op['day'], op['goal']
            require(day in sequences, 'UNKNOWN_DAY')
            require(goal in ('travel', 'waiting', 'walking'), 'INVALID_OPTIMIZATION_GOAL')
            baseline = fixed_sequence(state, sequences)
            optimized = fixed_sequence(state, sequences, optimize=op)
            before = day_metric(baseline, day, goal)
            require(day_metric(optimized, day, goal) < before, 'NO_ROUTE_IMPROVEMENT')
            sequences[day] = sequences_of(optimized)[day]
            events = next(item['events'] for item in optimized['engine_days'] if item['date'] == day)
            context.setdefault('moscow_start_hints', {})[day] = {
                event['poi_id']: event['start_min'] for event in events if 'poi_id' in event
            }
            context.setdefault('moscow_optimization_checks', {})[day] = (goal, before)
            return
        if op['op'] == 'set_transit_preferences':
            require(not context['moscow_has_pet'], 'TRANSPORT_UNAVAILABLE')
            allowed = {'metro', 'mcc', 'mcd', 'tram', 'bus', 'electric_bus'}
            values = [op[key] for key in ('avoid', 'require')]
            require(all(isinstance(value, list) and all(isinstance(mode, str) and mode in allowed for mode in value)
                        for value in values), 'TRANSPORT_UNAVAILABLE')
            avoid, required = map(set, values)
            require(not avoid & required, 'TRANSPORT_UNAVAILABLE')
            filters = {'avoid_transit': sorted(avoid), 'require_transit': sorted(required)}
            mode = state['preferences']['transport']
            selected = context.get('moscow_transport_modes')
            if 'transit' not in (selected or [MODES[mode]]):
                require(required, 'NO_ROUTE_CHANGE')
                if selected:
                    selected = [*selected, 'transit']
                    context['moscow_transport_modes'] = selected
                else:
                    mode = 'public_transit'
                    state['preferences']['transport'] = mode
            state['travel_times'] = chosen_moscow_matrix(state['pois'], mode, selected, filters)
            context['moscow_transit_filters'] = filters
            state['requested_transport'] = 'explicit_modes' if selected else 'public_transit'
            state['filters_only'] = True
            return
        if op['op'] == 'move_poi':
            day, key = op['day'], op['poi_id']
            require(day in sequences, 'UNKNOWN_DAY')
            source = next((date for date, ids in sequences.items() if key in ids), None)
            require(source is not None, 'TARGET_NOT_IN_ROUTE')
            require(source != day, 'NO_ROUTE_CHANGE')
            require(key not in context.get('visit_times', {}).get(source, {}), 'PINNED_VISIT_PROTECTED')
            reduced = deepcopy(sequences)
            reduced[source].remove(key)
            attempt = self._best_addition(state, reduced, key, [day])
            require(attempt is not None, 'NO_FEASIBLE_INSERTION')
            sequences.update(attempt)
            return
        if op['op'] == 'set_meal_break':
            day = op['day']
            lunch = context.get('moscow_meals', {}).get(day, {}).get('lunch')
            if lunch:
                original = next(row for row in context['moscow_original_hours']
                                if row['poi_id'] == lunch['poi_id'] and row['date'] == day)
                row = next(row for row in context['opening_intervals']
                           if row['poi_id'] == lunch['poi_id'] and row['date'] == day)
                row['intervals'] = deepcopy(original['intervals'])
                require(fix_meal_time(context, day, lunch['poi_id'], 'lunch', op['start_at'], op['end_at']),
                        'MEAL_TIME_UNAVAILABLE')
                context.setdefault('moscow_meal_pins', {}).setdefault(day, {})['lunch'] = op['start_at'][11:16]
                return
            raise AssistantError('MEAL_TIME_UNAVAILABLE')
        if op['op'] == 'set_transport_modes':
            selected = op['modes']
            require(isinstance(selected, list) and 1 <= len(selected) <= 3
                    and all(isinstance(label, str) and label in MATRIX_MODES for label in selected)
                    and len(set(selected)) == len(selected), 'TRANSPORT_UNAVAILABLE')
            require(not context['moscow_has_pet'] or selected != ['transit'], 'TRANSPORT_UNAVAILABLE')
            mode = MATRIX_MODES[selected[0]]
            state['travel_times'] = chosen_moscow_matrix(state['pois'], mode, selected)
            state['preferences']['transport'] = mode
            context['moscow_transport_modes'] = selected
            context.pop('moscow_transit_filters', None)
            state['requested_transport'] = 'explicit_modes'
            state.pop('filters_only', None)
            return
        if op['op'] != 'set_transport':
            return super()._apply(state, sequences, op)
        mode = op['transport']
        require(isinstance(mode, str) and mode in TRANSPORTS, 'TRANSPORT_UNAVAILABLE')
        matrix = travel_matrix(state['pois'], mode)
        if mode == 'public_transit':
            require(not context['moscow_has_pet'], 'TRANSPORT_UNAVAILABLE')
            require(any(pair.get('transport_steps') for pair in matrix['pairs']), 'TRANSPORT_UNAVAILABLE')
        else:
            require(any(pair['basis'] == 'routing_provider' and pair['status'] == 'known'
                        for pair in matrix['pairs']), 'TRANSPORT_UNAVAILABLE')
        state['preferences']['transport'] = mode
        state['travel_times'] = matrix
        context.pop('moscow_transit_filters', None)
        context.pop('moscow_transport_modes', None)
        state['requested_transport'] = mode
        state.pop('filters_only', None)

    def _candidate(self, state, sequences):
        context = deepcopy(state['routing_context'])
        for day, meals in list(context.get('moscow_meals', {}).items()):
            for kind, slot in list(meals.items()):
                if slot['poi_id'] not in sequences[day]:
                    if kind == 'lunch':
                        settings = next(row for row in context['days'] if row['date'] == day)
                        settings['meal_break'] = {key: slot[key] for key in ('start_at', 'end_at')} | {'kind': 'meal'}
                    del meals[kind]
        prepared = {**state, 'routing_context': context,
                    'travel_times': self._guarded_insertion_matrix(state, sequences)}
        def recompute(candidate_state):
            candidate = fixed_sequence(candidate_state, sequences)
            require(sequences_of(candidate) == sequences and not candidate['unplaced_required_poi_ids']
                    and not has_failed(candidate['constraint_checks']), 'ROUTE_CONSTRAINT_FAILED')
            return candidate

        result = recompute(prepared)
        mode = state.get('requested_transport')
        filters = context.get('moscow_transit_filters', {})
        if filters:
            required = set(filters['require_transit'])
            if not required <= transit_modes(result):
                original_matrix = prepared['travel_times']
                profiles = original_matrix.get('profiles')
                transit_matrix = profiles['transit'] if profiles else original_matrix
                pairs = transit_matrix['pairs']
                used_pairs = {(row['from_poi_id'], row['to_poi_id']) for day in result['days'] for row in day['legs']}
                alternatives = [(row['required_transit_alternative']['duration_seconds'] - (row['duration_seconds'] or 0), index)
                                for index, row in enumerate(pairs) if row.get('required_transit_alternative')
                                and (row['from_poi_id'], row['to_poi_id']) in used_pairs]
                for _, index in sorted(alternatives):
                    pair = pairs[index]
                    replacement = {**pair, **pair['required_transit_alternative']}
                    matrix = {**transit_matrix, 'pairs': pairs[:index] + [replacement] + pairs[index + 1:]}
                    if profiles:
                        chosen_pair = pair['from_poi_id'], pair['to_poi_id']
                        forced = {'transit': matrix}
                        for label, profile in profiles.items():
                            if label != 'transit':
                                forced[label] = {**profile, 'pairs': [
                                    row for row in profile['pairs']
                                    if (row['from_poi_id'], row['to_poi_id']) != chosen_pair]}
                        matrix = {**forced[MODES[state['preferences']['transport']]], 'profiles': forced}
                    try:
                        result = recompute({**prepared, 'travel_times': matrix})
                    except AssistantError as error:
                        if error.code in BRIDGE_FAILURE_CODES:
                            raise
                        continue
                    except ValueError:
                        continue
                    if required <= transit_modes(result):
                        break
            modes = transit_modes(result)
            require(required <= modes and not set(filters['avoid_transit']) & modes, 'TRANSPORT_UNAVAILABLE')
            result['moscow_transit_filters'] = filters
        if mode:
            legs = [leg for day in result['days'] for leg in day['legs']]
            if mode == 'explicit_modes':
                require(legs and all(leg['mode'] in context['moscow_transport_modes'] for leg in legs), 'TRANSPORT_UNAVAILABLE')
            elif mode == 'public_transit' and not state.get('filters_only'):
                require(any(leg.get('transport_steps') for leg in legs), 'TRANSPORT_UNAVAILABLE')
            elif mode != 'public_transit':
                require(legs and all(leg['basis'] == 'routing_provider' for leg in legs), 'TRANSPORT_UNAVAILABLE')
        self._check_insertion_geography(state, result)
        if context.get('moscow_meal_pins'):
            result['moscow_meal_pins'] = context['moscow_meal_pins']
        if context.get('visit_times'):
            result['visit_times'] = context['visit_times']
        result['moscow_day_windows'] = {day['date']: {
            'day_start': day['day_start'][11:16], 'day_end': day['day_end'][11:16],
        } for day in context['days']}
        for day, (goal, before) in context.get('moscow_optimization_checks', {}).items():
            require(day_metric(result, day, goal) < before, 'NO_ROUTE_IMPROVEMENT')
        result['gap_diagnostics'] = describe_edited_gaps(result, context, state['route'])
        return result


def transit_modes(plan):
    return {step['line'].split(':')[0] for day in plan['days'] for leg in day['legs']
            for step in leg.get('transport_steps', []) if step['kind'] == 'rail_estimate'}


def day_metric(plan, date, goal):
    events = next(day['events'] for day in plan['engine_days'] if day['date'] == date)
    if goal == 'waiting':
        return sum(max(0, event['start_min'] - previous['end_min']) for previous, event in zip(events, events[1:]))
    return sum(event['planned_minutes'] for event in events
               if event['type'] == 'travel' and (goal != 'walking' or event['mode'] == 'walk'))


def moscow_interpretation(value):
    if isinstance(value, dict) and value.get('action') == 'propose':
        for op in value.get('operations', []):
            if isinstance(op, dict) and op.get('op') == 'add_named_poi':
                require(type(op.get('replace_if_needed')) is bool and
                        (op.get('day') is None or isinstance(op['day'], str) and
                         re.fullmatch(r'\d{4}-\d{2}-\d{2}', op['day'])), 'INVALID_NAMED_POI_OPERATION')
            if isinstance(op, dict) and op.get('op') == 'add_category_poi':
                require(op.get('category') in {'museums', 'history', 'nature',
                        'entertainment', 'shopping', 'viewpoints', 'unusual'} and
                        (op.get('day') is None or isinstance(op['day'], str) and
                         re.fullmatch(r'\d{4}-\d{2}-\d{2}', op['day'])),
                        'INVALID_CATEGORY')
            if isinstance(op, dict) and op.get('op') == 'set_day_window':
                require(any(op.get(key) is not None for key in ('start_time', 'end_time')) and
                        all(op.get(key) is None or valid_clock(op[key]) for key in ('start_time', 'end_time')),
                        'INVALID_DAY_WINDOW')
            if isinstance(op, dict) and op.get('op') == 'pin_visit_time':
                require(valid_pin(op), 'INVALID_VISIT_TIME')
            if isinstance(op, dict) and op.get('op') == 'fill_gaps':
                require(isinstance(op.get('days'), list) and 1 <= len(op['days']) <= MAX_TRIP_DAYS and
                        len(op['days']) == len(set(op['days'])) and
                        all(isinstance(day, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', day)
                            for day in op['days']), 'INVALID_DAY')
    return parse_interpretation(value, extra_operations={
        'add_named_poi': {'op', 'poi_id', 'day', 'replace_if_needed'},
        'replace_any_named': {'op', 'poi_id', 'day'},
        'recenter_day': {'op', 'poi_id', 'day'},
        'add_category_poi': {'op', 'category', 'day'},
        'set_transport': {'op', 'transport'}, 'move_poi': {'op', 'poi_id', 'day'},
        'set_transport_modes': {'op', 'modes'},
        'set_transit_preferences': {'op', 'avoid', 'require'},
        'optimize_day': {'op', 'day', 'goal'},
        'fill_gaps': {'op', 'days'},
        'set_day_window': DAY_WINDOW_FIELDS,
        'pin_visit_time': PIN_FIELDS,
    })


class MoscowAssistant(ShanghaiAssistantService):
    def __init__(self):
        prompt = SYSTEM_PROMPT.replace('город, transport, бюджет или required.',
            'город, бюджет или required. Для явной смены транспорта используй set_transport: '
            'op, transport (walking|car|public_transit). Для явного запрета или включения отдельных '
            'сочетаний транспорта используй set_transport_modes: op, modes (массив walk, transit, taxi). Для отдельных '
            'видов транспорта используй set_transit_preferences: op, avoid, require. '
            'Два массива содержат значения metro, mcc, mcd, tram, bus, electric_bus. '
            'Поля transport и transit_filters описывают текущий выбор. Чтобы снять фильтры, передай два пустых массива. '
            'Для переноса уже запланированного места между днями используй move_poi: op, poi_id, day (YYYY-MM-DD). '
            'Для сокращения дороги, ожидания или пеших переходов в указанном дне используй optimize_day: '
            'op, day (YYYY-MM-DD), goal (travel|waiting|walking).')
        prompt += ('\nДля запроса заполнить длинные перерывы или пустые дни можешь использовать '
                   'fill_gaps: op, days (список дат ISO), на существующем московском каталоге. '
                   'Не выбирай POI сам, не обещай устранить все ожидания. Учитывай бюджет, '
                   'питание и заданные виды транспорта.')
        prompt += ('\nДля закрепления визита используй pin_visit_time: op, day_date (ISO), poi_id '
                   'существующего визита и start_time HH:MM.\n'
                   'Для изменения дневных часов используй set_day_window: op, day (ISO только для '
                   'промежуточного дня), start_time, end_time (HH:MM или null для сохранения одного времени).')
        prompt += ('\nprice_rub — ориентировочный чек одного человека. Для явной просьбы убрать '
                   'заведение дороже указанного порога удаляй подходящие по оценке запланированные '
                   'необязательные заведения. Если порог неоднозначен, уточни его.')
        super().__init__(data={'pois': []}, interpreter=OpenRouterInterpreter(rich_context=True, system_prompt=prompt))
        # Deterministic named-place edits must keep working when the provider
        # is rate-limited or OPENROUTER_API_KEY is absent.
        self.llm_configured = bool(os.environ.get('OPENROUTER_API_KEY', '').strip())
        self.configured = True
        self.snapshot = moscow_v7()[1]

    def status(self, token=None):
        token, result = super().status(token)
        if not self.llm_configured:
            result['mode'] = 'local'
            result['reason'] = ('Локальные команды добавления места и заполнения пауз работают без OpenRouter. '
                                'Для сложных запросов нужен доступный ИИ.')
        return token, result

    @staticmethod
    def _retain_local_meals(retained, operations, route, preferences, by_id, cells):
        if not cells or not route.get('moscow_meals'):
            return
        inserted = list(dict.fromkeys(op['poi_id'] for op in operations
            if op.get('op') in ('add_named_poi', 'add_poi', 'replace_any_named', 'move_poi')
            and op.get('poi_id') in by_id))
        if not inserted:
            return
        budget = daily_budget_per_person(preferences)
        kinds = set().union(*(meals.keys() for meals in route['moscow_meals'].values()))
        for key in inserted[:3]:
            point = coordinates(by_id[key])
            for kind, weight in (('breakfast', .09), ('lunch', .14), ('dinner', .20)):
                if kind in kinds:
                    target = budget * weight if budget >= 2800 else None
                    retained.extend(budget_friendly_places(point, cells, 4, retained, meal_kind=kind,
                                                           dining_target=target))

    @staticmethod
    def _propose_named_fallback(agent, actor, request, direct_decision, propose, dates, pois, context,
                                retained, history_text, modes=()):
        operations = (direct_decision or {}).get('operations', [])
        named = operations[0] if len(operations) == 1 else {}
        move = named.get('op') == 'move_poi'
        can_fallback = move or named.get('op') in {'add_named_poi', 'replace_any_named'}
        try:
            return agent.propose(actor, request), None
        except AssistantError as error:
            if not can_fallback or error.code != ('NO_FEASIBLE_INSERTION' if move else 'NO_FEASIBLE_NAMED_INSERTION'):
                raise
        poi_id, date = named['poi_id'], named['day']
        by_id = {poi['id']: poi for poi in pois}
        wanted = by_id[poi_id]
        transport_option = (' Если подходит, разрешите общественный транспорт или такси и повторите запрос.'
                            if list(modes) == ['walk'] else '')
        if move:
            source = next(day['date'] for day in agent.store.get_route(actor, request['route_id'])['route']['days']
                          if any(row['poi_id'] == poi_id for row in day['visits']))
            others = agent._named_open_days(context, poi_id, [day for day in dates if day not in (date, source)])
            for alternative in others:
                result = propose({**named, 'day': alternative})
                if result is not None:
                    return result, 'move_other_day'
            return {'validation_status': 'needs_clarification',
                    'clarification': f'Перенести «{wanted["name"]}» на {date} с текущими ограничениями '
                                     f'не получилось. Посещение осталось на {source}.{transport_option}'}, None
        allow_removal = not re.search(
            r'\b(?:без (?:замен\w*|удалени\w*|изменени\w*)|не (?:убира\w*|удаля\w*|заменя\w*|меня\w*))\b',
            history_text, re.I)

        def try_op(poi_id, date, replace):
            return propose({'op': 'add_named_poi', 'poi_id': poi_id, 'day': date,
                            'replace_if_needed': replace})

        if date is not None and len(dates) > 1:
            result = try_op(poi_id, None, False)
            if result is not None:
                return result, 'other_day'
        if allow_removal and named['op'] == 'add_named_poi' and not named['replace_if_needed']:
            result = try_op(poi_id, date, True)
            if result is not None:
                return result, 'remove_optional'
        if allow_removal and named['op'] == 'add_named_poi' and date is not None and len(dates) > 1:
            result = try_op(poi_id, None, True)
            if result is not None:
                return result, 'other_day'
        categories = set(wanted.get('categories', []))
        if date is not None:
            alternate_days = [date]
        else:
            alternate_days = dates
        alternatives = [poi for poi in pois
                        if poi['id'] not in retained and poi['id'] != poi_id
                        and categories.intersection(poi.get('categories', []))
                        and 'coordinates' in poi and 'coordinates' in wanted
                        and agent._named_open_days(context, poi['id'], alternate_days)]
        alternatives.sort(key=lambda poi: (_distance(coordinates(wanted), coordinates(poi)), poi['id']))
        for alternate in alternatives[:3]:
            result = try_op(alternate['id'], date, False)
            if result is not None:
                return result, 'other_place'
        name = wanted.get('name') or poi_id
        allowed_days = agent._named_open_days(context, poi_id, dates)
        if allowed_days:
            suggestion = ('Могу проверить «' + name + '» на ' + ', '.join(allowed_days[:3]) +
                          ' с более широким окном дня. Укажите новую границу прогулки или выберите другое место.')
        else:
            suggestion = ('Часы работы «' + name + '» не пересекаются с окнами поездки. '
                          'Выберите другой день поездки или назовите другое место.')
        return {'validation_status': 'needs_clarification',
                'clarification': 'В проверенных вариантах разместить «' + name +
                '» при текущих ограничениях не удалось. ' + suggestion + transport_option}, None

    def _request(self, actor, session, action, body):
        if action == 'register':
            from backend.shanghai import MoscowPreferences

            session['moscow_preferences'] = MoscowPreferences.model_validate(body['preferences'])
            saved_windows = session['route_options'].get('day_settings') or {}
            if saved_windows:
                settings = {**(session['moscow_preferences'].day_settings or {}),
                            **{date.fromisoformat(day): window for day, window in saved_windows.items()}}
                session['moscow_preferences'] = session['moscow_preferences'].model_copy(update={'day_settings': settings})
            transport = session['route_options'].get('transport')
            if transport in TRANSPORTS.values() or transport == 'mixed':
                session['moscow_preferences'] = session['moscow_preferences'].model_copy(update={
                    'transport': transport, 'transport_modes': session['route_options'].get('transport_modes')})
            session['moscow_route'] = body['plan']
            session['moscow_route_id'] = body['route_id']
            session['moscow_version'] = body['version']
            session['moscow_history'] = session['route_options'].get('conversation', [])
            return {'type': 'registered', 'route_id': body['route_id'], 'version': body['version'], 'poi_snapshot_id': self.snapshot}
        require('moscow_route' in session and self.configured, 'SESSION_REQUIRED')
        if action == 'message':
            require(isinstance(body.get('text'), str) and 0 < len(body['text'].strip()) <= 3000, 'INVALID_MESSAGE')
            require(body.get('route_id') == session['moscow_route_id'] and
                    body.get('base_version') == session['moscow_version'], 'VERSION_CONFLICT')
            require(body.get('poi_snapshot_id') == self.snapshot, 'SNAPSHOT_CONFLICT')
            route = session['moscow_route']
            retained = [visit['poi_id'] for day in route['days'] for visit in day['visits']]
            previous = next((line['content'] for line in reversed(session['moscow_history']) if line['role'] == 'user'), '')
            from ai_ds.moscow_local_commands import local_command_decision
            date_list = [day['date'] for day in route['days']]
            prefs = session['moscow_preferences']
            mode_choice = getattr(prefs, 'transport_modes', None)
            if mode_choice is None:
                mode_choice = {'walking': ['walk'], 'car': ['taxi'],
                               'public_transit': ['transit'],
                               'mixed': ['walk', 'transit', 'taxi']}.get(
                                   getattr(prefs, 'transport', 'mixed'), [])
                mode_choice = mode_choice or [prefs.transport]
            catalog = indexes()[0]
            pin_days = [{'date': day['date'], 'visits': [
                {'name': catalog[row['poi_id']]['name'], 'poi_id': row['poi_id']}
                for row in day['visits'] if row['poi_id'] in catalog and row['poi_id'] not in {
                    meal['poi_id'] for meal in route.get('moscow_meals', {}).get(day['date'], {}).values()}]}
                for day in route['days']]
            direct_decision = local_visit_pin(body['text'], pin_days) or local_command_decision(
                body['text'], date_list, indexes()[0], retained,
                session['moscow_history'], mode_choice)
            if direct_decision and direct_decision['action'] != 'propose':
                message = direct_decision['clarification']
                session['moscow_history'] = [*session['moscow_history'],
                    {'role':'user','content':body['text']}, {'role':'assistant','content':message}][-24:]
                session['route_options'] = {**session['route_options'], 'conversation': session['moscow_history']}
                return {'type':'clarification', 'message':message}
            if direct_decision:
                # Exact and typo-resolved POIs must reach the actual scheduler,
                # not disappear from a geographically limited shortlist.
                retained = list(dict.fromkeys([*retained, *(
                    op['poi_id'] for op in direct_decision['operations']
                    if op['op'] in ('add_named_poi', 'add_poi', 'replace_any_named', 'recenter_day'))]))
                recenter_candidates = {}
                by_id, cells, _ = indexes()
                for op in direct_decision['operations']:
                    if op['op'] != 'recenter_day':
                        continue
                    key = op['poi_id']
                    anchor = coordinates(by_id[key])
                    interests = set(prefs.primary_interests + prefs.secondary_interests) | {'cafes'}
                    nearby = [poi_id for category in sorted(interests) for poi_id in nearby_places(
                        anchor, cells.get(category, {}), 6 if category == 'cafes' else 4)]
                    recenter_candidates[key] = nearby
                    retained.extend(nearby)
                category_ops = [op for op in direct_decision['operations']
                                if op['op'] == 'add_category_poi']
                if category_ops:
                    by_id, cells, _ = indexes()
                    # Pick neighborhood candidates for the requested day,
                    # rather than assuming the original interests shortlist
                    # contains the requested category at all.
                    for op in category_ops:
                        matching = next((day for day in route['days']
                                         if day['date'] == op['day']), None)
                        neighbors = (matching or {}).get('visits', [])
                        anchor_key = next((row['poi_id'] for row in neighbors
                                           if row['poi_id'] in by_id), None)
                        anchor = coordinates(by_id[anchor_key]) if anchor_key else CITY_CENTRE
                        retained.extend(nearby_places(anchor, cells.get(op['category'], {}),
                                                     8, retained))
                    retained = list(dict.fromkeys(retained))
                self._retain_local_meals(retained, direct_decision['operations'], route, prefs,
                                         by_id, cells.get('cafes', {}))
            pair_caches = {}
            def inputs(ids):
                profile, pois, ranking, matrix, context = moscow_inputs(
                    prefs, ids, body['text'], previous, route.get('moscow_transit_filters'), pair_caches=pair_caches)
                context['moscow_original_hours'] = deepcopy(context['opening_intervals'])
                if direct_decision:
                    context['recenter_candidates'] = recenter_candidates
                for key in ('moscow_meal_pins', 'visit_times'):
                    if route.get(key):
                        context[key] = deepcopy(route[key])
                for settings, day in zip(context['days'], route['days']):
                    meal = next((entry for entry in day['breaks'] if entry['kind'] == 'meal'), None)
                    if meal:
                        settings['meal_break'] = meal
                    if day.get('pace') and day['pace'] != profile['pace']:
                        context.setdefault('day_pace_overrides', {})[day['date']] = day['pace']
                    for kind, slot in route.get('moscow_meals', {}).get(day['date'], {}).items():
                        require(fix_meal_time(context, day['date'], slot['poi_id'], kind, slot['start_at'], slot['end_at']),
                                'MEAL_TIME_UNAVAILABLE')
                return profile, pois, ranking, matrix, context

            profile, pois, ranking, matrix, context = inputs(retained)
            store = InMemoryRouteStore()
            from ai_ds.moscow_assistant_intents import gap_fill_decision
            def interpret(payload):
                from ai_ds.moscow_assistant_intents import interpret_with_retry
                dates = [day['date'] for day in payload['days']]
                if direct_decision is not None:
                    return direct_decision
                direct = gap_fill_decision(payload['text'], dates)
                if direct is not None:
                    return direct
                return interpret_with_retry(self.interpreter, payload,
                                            session['moscow_history'])
            agent = MoscowRouteAssistant(store, interpret,
                moscow_preferences=session['moscow_preferences'],
                proposal_ttl_seconds=300, interpretation_parser=moscow_interpretation,
                allow_cost_edits=True)
            fallback_decision = direct_decision
            if direct_decision is None:
                payload = agent._payload({'preferences': profile, 'pois': pois,
                                          'routing_context': context, 'route': route}, body['text'])
                try:
                    decision = moscow_interpretation(interpret(payload))
                except AssistantError:
                    raise
                except OpenRouterError as error:
                    raise AssistantError(error.code) from None
                except Exception:
                    raise AssistantError('INTERPRETER_UNAVAILABLE') from None
                included = {poi['id'] for poi in pois}
                missing = list(dict.fromkeys(op['poi_id'] for op in decision.get('operations', [])
                                             if op['op'] == 'add_poi' and op['poi_id'] in catalog
                                             and op['poi_id'] not in included))
                _, cells, _ = indexes()
                self._retain_local_meals(missing, decision.get('operations', []), route, prefs,
                                         catalog, cells.get('cafes', {}))
                missing = [key for key in missing if key not in included]
                if missing:
                    retained.extend(missing)
                    profile, pois, ranking, matrix, context = inputs(retained)
                operations = decision.get('operations', [])
                if len(operations) == 1 and operations[0]['op'] == 'add_poi' and operations[0]['poi_id'] in catalog:
                    named = {**operations[0], 'op': 'add_named_poi', 'replace_if_needed': False}
                    decision = {**decision, 'operations': [named]}
                    fallback_decision = decision
                agent.interpreter = lambda _: decision
            store.register_route(actor, body['route_id'], preferences=profile, pois=pois, recommendation=ranking,
                                 travel_times=matrix, routing_context=context, poi_snapshot_id=self.snapshot,
                                 route=route, version=body['base_version'])
            request = {'schema_version': '0.2', **{key: body.get(key) for key in
                       ('route_id', 'base_version', 'poi_snapshot_id', 'text')}}

            def retry_named(op):
                agent.interpreter = lambda _: {'action': 'propose', 'operations': [op], 'clarification': None}
                try:
                    return agent.propose(actor, request)
                except AssistantError as error:
                    if error.code not in {'NO_FEASIBLE_NAMED_INSERTION', 'NO_FEASIBLE_INSERTION'}:
                        raise
                    return None

            with bridge_batch():
                result, compromise = self._propose_named_fallback(
                    agent, actor, request, fallback_decision, retry_named, date_list, pois, context,
                    set(retained), body['text'], mode_choice)
            message = result.get('clarification') or 'Предложение рассчитано, но ещё не применено: проверьте изменения и подтвердите их.'
            session['moscow_history'] = [*session['moscow_history'], {'role': 'user', 'content': body['text']},
                                         {'role': 'assistant', 'content': message}][-24:]
            session['route_options'] = {**session['route_options'], 'conversation': session['moscow_history']}
            if result['validation_status'] != 'ready_for_confirmation':
                return {'type': 'clarification', 'message': message}
            diff = result['diff']
            requested_fill = next((op['days'] for op in result['operations'] if op['op'] == 'fill_gaps'), None)
            if requested_fill:
                previous_days = {day['date']: day for day in route['days']}
                timing_changes = [day['date'] for day in result['candidate_route']['days']
                                  if day['date'] in requested_fill and day != previous_days[day['date']]]
                diff['changed_days'] = list(dict.fromkeys([*diff['changed_days'], *timing_changes]))
            pins = [op for op in result['operations'] if op['op'] == 'pin_visit_time']
            require(diff['changed_days'] or pins and result['candidate_route'].get('visit_times') != route.get('visit_times'),
                    'NO_ROUTE_CHANGE')
            named_ops = [op for op in result['operations'] if op['op'] in ('add_named_poi', 'replace_any_named', 'recenter_day')]
            if named_ops:
                # Never claim success unless every requested named POI appears.
                wanted_ids = [op['poi_id'] for op in named_ops]
                require(all(any(key in diff['after'][day] for day in diff['changed_days'])
                            for key in wanted_ids), 'NAMED_POI_NOT_ADDED')
                wanted_names = [indexes()[0][key]['name'] for key in wanted_ids]
            if requested_fill:
                improved = [date for date in requested_fill if date in diff['changed_days']]
                remaining = [date for date in requested_fill if date not in diff['changed_days']]
                has_new_visits = any(set(diff['after'][day]) - set(diff['before'][day]) for day in improved)
                message = ('Проверены дополнительные посещения и время остановок. ' if has_new_visits
                           else 'Скорректировано время запланированных посещений. ')
                message += 'Дни с изменениями: ' + ', '.join(improved) + '.'
                if remaining:
                    message += (' Для дней ' + ', '.join(remaining) +
                                ' не нашлось выполнимого способа сократить паузы. Проверьте предпросмотр.')
            session['moscow_assistant'] = agent
            names = {poi['id']: poi['name'] for poi in pois}
            # A gap-fill proposal may insert V7 candidates outside the initial
            # shortlist. Resolve their labels from the same indexed catalog;
            # otherwise the chat crashes with KeyError after successful routing.
            expanded_catalog = indexes()[0]
            changes = {'changed_days': sorted(set(diff['changed_days']) | {op['day_date'] for op in pins})}
            for action, first, second in [('added', 'after', 'before'), ('removed', 'before', 'after')]:
                changes[action] = [{'name': names.get(key) or expanded_catalog.get(key, {}).get('name', key), 'day': day}
                                   for day in diff['changed_days']
                                   for key in diff[first][day] if key not in diff[second][day]]
            changes['before_cost'] = route['budget_summary']['known_total']
            changes['after_cost'] = result['candidate_route']['budget_summary']['known_total']
            if named_ops:
                named_details = []
                for key, name in zip(wanted_ids, wanted_names):
                    visit = next((row, day['date']) for day in result['candidate_route']['days']
                                 for row in day['visits'] if row['poi_id'] == key)
                    named_details.append({'name': name, 'day': visit[1], 'time': visit[0]['start_at'][11:16]})
                changes['named_additions'] = named_details
                added = ', '.join(f'«{row["name"]}» на {row["day"]} в {row["time"]}' for row in named_details)
                original_op = ((fallback_decision or {}).get('operations') or [{}])[0]
                original_name = names.get(original_op.get('poi_id'))
                if compromise == 'other_place':
                    message = f'«{original_name}» не удалось разместить. Вариант рядом: {added}.'
                elif compromise == 'other_day' and named_details[0]['day'] != original_op.get('day'):
                    message = f'На запрошенную дату не получилось. Вариант с другой датой: {added}.'
                else:
                    if compromise == 'other_day':
                        compromise = 'keep_existing'
                    message = f'Предлагаю добавить {added}.'
                after_ids = {key for day in diff['after'].values() for key in day}
                removed = [names.get(key) or expanded_catalog.get(key, {}).get('name', key)
                           for day in diff['changed_days'] for key in diff['before'][day] if key not in after_ids]
                if removed:
                    message += ' Из расписания уйдут: ' + ', '.join('«' + name + '»' for name in removed) + '.'
                message += ' Предложение ещё НЕ сохранено. Проверьте изменения и подтвердите его.'
                changes['named_compromise'] = compromise
            if compromise == 'move_other_day':
                op = result['operations'][0]
                original = fallback_decision['operations'][0]['day']
                message = (f'Перенести «{names[op["poi_id"]]}» на {original} не получилось. '
                           f'В выполнимом варианте посещение назначено на {op["day"]}. '
                           'Предложение ещё НЕ сохранено.')
            changes['meal_replacements'] = []
            for day in diff['changed_days']:
                for kind, slot in result['candidate_route'].get('moscow_meals', {}).get(day, {}).items():
                    old = route.get('moscow_meals', {}).get(day, {}).get(kind)
                    if old and old['poi_id'] != slot['poi_id']:
                        label = lambda key: names.get(key) or expanded_catalog.get(key, {}).get('name', key)
                        changes['meal_replacements'].append({'day': day, 'kind': kind,
                            'before': label(old['poi_id']), 'after': label(slot['poi_id']),
                            'time': slot['start_at'][11:16]})
            for meal in changes['meal_replacements']:
                label = {'breakfast': 'Завтрак', 'lunch': 'Обед', 'dinner': 'Ужин'}.get(meal['kind'], 'Приём пищи')
                message += f' {label} {meal["day"]} в {meal["time"]} — «{meal["after"]}» рядом с остановками.'
            session['moscow_history'][-1]['content'] = message
            windows = result['candidate_route'].get('moscow_day_windows', {})
            changes['day_windows'] = [{'day': op['day'], **windows[op['day']]}
                                      for op in result['operations'] if op['op'] == 'set_day_window']
            session['moscow_proposed_windows'] = windows if changes['day_windows'] else None
            changes['visit_times'] = [{'name': catalog[op['poi_id']]['name'], 'day': op['day_date'],
                                       'start_time': op['start_time']} for op in pins]
            if route.get('engine_days'):
                changes['optimization'] = [
                    {'day': op['day'], 'goal': op['goal'],
                     'before': day_metric(route, op['day'], op['goal']),
                     'after': day_metric(result['candidate_route'], op['day'], op['goal'])}
                    for op in result['operations'] if op['op'] == 'optimize_day'
                ]
            move = next((op for op in reversed(result['operations'])
                         if op['op'] in {'set_transport', 'set_transport_modes', 'set_transit_preferences'}), None)
            chosen_modes = result['candidate_route'].get('moscow_transport_modes')
            if result['candidate_route'].get('moscow_transit_filters'):
                changes['transit_filters'] = result['candidate_route']['moscow_transit_filters']
            proposed = None
            if move:
                if chosen_modes:
                    proposed = 'mixed' if len(chosen_modes) > 1 else chosen_modes[0]
                    changes['transport_modes'] = chosen_modes
                else:
                    proposed = TRANSPORTS.get(move.get('transport', 'public_transit'))
            session['moscow_proposed_transport'] = proposed
            session['moscow_proposed_modes'] = chosen_modes if move else None
            changes['transport'] = session['moscow_proposed_transport']
            return {'type': 'proposal', 'plan': result['candidate_route'], 'message': message, 'diff': changes,
                    'proposal_id': result['proposal_id'], 'base_version': result['base_version']}
        if action == 'decide':
            agent = session.get('moscow_assistant')
            require(agent is not None, 'SESSION_REQUIRED')
            result = agent.decide(actor, {key: body.get(key) for key in ('proposal_id', 'base_version', 'decision')}, allow_draft=True)
            confirmed = result['decision'] == 'confirmed'
            if confirmed:
                session.update(moscow_route=result['route'], moscow_version=result['version'])
                windows = session.get('moscow_proposed_windows')
                if windows:
                    session['route_options']['day_settings'] = windows
                    session['moscow_preferences'] = session['moscow_preferences'].model_copy(update={
                        'day_settings': {date.fromisoformat(day): window for day, window in windows.items()},
                    })
                transport = session['moscow_proposed_transport']
                if transport:
                    update = {'transport': transport, 'transport_modes': session['moscow_proposed_modes']}
                    session['route_options'] = {**session['route_options'], **update}
                    session['moscow_preferences'] = session['moscow_preferences'].model_copy(update=update)
            return {'type': 'confirmed' if confirmed else 'rejected', 'version': result['version'],
                    'message': 'Изменения сохранены; обновлённое расписание и карта пересчитаны.' if confirmed else 'Исходный маршрут сохранён.',
                    **({'plan': result['route'], 'options': session['route_options']} if confirmed else {})}
        require(False, 'UNKNOWN_ENDPOINT')


@lru_cache(maxsize=1)
def moscow_assistant():
    return MoscowAssistant()
