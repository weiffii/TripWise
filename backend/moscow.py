from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
from functools import lru_cache
from heapq import nsmallest
from itertools import zip_longest
import json
from math import floor
import os
from pathlib import Path
import re

from fastapi import HTTPException

from ai_ds.moscow_catalog import load_moscow_v7
from ai_ds.moscow_road import PILOT_RAIL_CONFIG, coordinates, rail_inputs, travel_matrix
from ai_ds.moscow_transit import _distance
from ai_ds.moscow_engine_adapter import MATRIX_MODES, MODES, fixed_sequence, plan_city
from ai_ds.moscow_gap_repair import MoscowGapIndex, describe_edited_gaps, repair_gaps
from ai_ds.openrouter import OpenRouterInterpreter, SYSTEM_PROMPT
from ai_ds.recommendation import recommend
from ai_ds.route_assistant import AssistantError, InMemoryRouteStore, RouteAssistant, has_failed, parse_interpretation, require, sequences_of
from ai_ds.shanghai_assistant import ShanghaiAssistantService

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
    profiles = {
        label: cached(label, **({**filters, 'allow_walking': legacy} if label == 'transit' else {}))
        for label in selected
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


def geographic_shortlist(preferences, anchors):
    by_id, categories, _ = indexes()
    selected = dict.fromkeys(preferences.required_ids)
    interests = [category for category in (*preferences.primary_interests, *preferences.secondary_interests)
                 if category != 'cafes']
    places_per_area = {'relaxed': 6, 'balanced': 8, 'intensive': 10}[preferences.pace]
    by_day = []
    for anchor in anchors:
        previous = set(selected)
        for category in interests:
            selected.update(dict.fromkeys(nearby_places(anchor, categories.get(category, {}), 1, selected)))
        for index in range(max(0, places_per_area - len(interests)) if interests else 0):
            category = interests[index % len(interests)]
            selected.update(dict.fromkeys(nearby_places(anchor, categories.get(category, {}), 1, selected)))
        attractions = [key for key in selected if key not in previous]
        meals = nearby_places(anchor, categories.get('cafes', {}), 5, selected)
        selected.update(dict.fromkeys(meals))
        by_day.append([key for pair in zip_longest(attractions, meals) for key in pair if key is not None])
    if preferences.transport == 'mixed' and len(selected) > MAX_MIXED_CANDIDATES:
        # Сохраняем обязательные точки и распределяем оставшиеся места между днями.
        selected = dict.fromkeys(preferences.required_ids)
        for rank in range(max(map(len, by_day), default=0)):
            selected.update(dict.fromkeys(day[rank] for day in by_day if rank < len(day)))
            if len(selected) >= MAX_MIXED_CANDIDATES:
                break
        selected = list(selected)[:MAX_MIXED_CANDIDATES]
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
                  include_food=False, explicit_pois=None, pair_caches=None):
    if preferences.budget_currency != 'RUB':
        raise HTTPException(422, 'Для Москвы выберите бюджет в рублях.')
    pois = (shortlist(preferences, retained, query, prior_query) if explicit_pois is None
            else [indexes()[0][key] for key in dict.fromkeys([*preferences.required_ids, *explicit_pois])])
    anchors = []
    if include_food:
        anchors = geographic_days(preferences)
        if len(anchors) > 1:
            pois = geographic_shortlist(preferences, anchors)
        else:
            by_id, categories, _ = indexes()
            selected = {poi['id']: poi for poi in pois}
            for key in nearby_places(anchors[0], categories['cafes'], 6, selected):
                selected[key] = by_id[key]
            pois = list(selected.values())
    selected = selected_moscow_modes(preferences)
    mode = MATRIX_MODES[selected[0]] if selected else MATRIX_MODES.get(preferences.transport, 'public_transit')
    people = preferences.group.adult + preferences.group.child + preferences.group.senior
    amount = preferences.budget_amount * (people if preferences.budget_basis == 'per_person' else 1)
    first = preferences.start_date
    profile = dict(schema_version='0.1', destination={'country_code': 'RU', 'city': 'Москва'},
                   date_start=first.isoformat(), date_end=(first + timedelta(days=preferences.days - 1)).isoformat(),
                   timezone='Europe/Moscow', primary_interests=preferences.primary_interests,
                   secondary_interests=preferences.secondary_interests, required_poi_ids=preferences.required_ids,
                   pace=preferences.pace, transport=mode, top_k=max(1, min(len(pois), max(12, preferences.days * 3))),
                   budget={'amount': amount, 'currency': 'RUB', 'scope': 'trip_food_and_entertainment'})
    days, openings, durations, costs = [], [], [], []
    for offset in range(preferences.days):
        day = (first + timedelta(days=offset)).isoformat()
        days.append(dict(date=day, day_start=stamp(day, preferences.day_start),
                         day_end=stamp(day, preferences.day_end), meal_break=None))
    for poi in pois:
        key, fields, provenance = poi['id'], poi['planning'], poi['provenance']
        minutes = fields['duration_minutes']['value']
        if preferences.visit_minutes is not None and key in preferences.required_ids:
            minutes = preferences.visit_minutes
        durations.append(dict(poi_id=key, minutes=minutes, basis='expert_estimate',
                              method_version=fields['duration_minutes']['model_version'], author='TripWise',
                              recorded_at=provenance['retrieved_at'], evidence_url=provenance['source_url']))
        price = fields['price_rub']
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
                   optimize_local_order=True, moscow_has_pet=bool(preferences.group.pet), moscow_group=preferences.group.model_dump(),
                   duration_overrides=durations, opening_intervals=openings,
                   cost_context=dict(currency='RUB', coverage_complete=False, visit_costs=costs, meal_costs=[]))
    filters = transport_filters or {}
    matrix = chosen_moscow_matrix(pois, mode, selected, filters, pair_caches=pair_caches,
                                  legacy=preferences.transport_modes is None and bool(retained))
    if selected:
        context['moscow_transport_modes'] = selected
    if filters:
        context['moscow_transit_filters'] = filters
    if len(anchors) > 1:
        context['day_anchors'] = {day['date']: {**point, 'radius_m': DAY_RADIUS_METERS}
                                  for day, point in zip(days, anchors)}
    return profile, pois, recommend(profile, pois), matrix, context


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
    options = {'start_date': preferences.start_date.isoformat(), 'transport': preferences.transport}
    if preferences.transport_modes:
        options['transport_modes'] = preferences.transport_modes
    return plan, options, plan['poi_snapshot_id']


TRANSPORTS = {'walking': 'walk', 'car': 'taxi', 'public_transit': 'transit'}


class MoscowRouteAssistant(RouteAssistant):
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
        return {**super()._settings(state, day), 'transport': state['preferences']['transport'],
                'transport_modes': state['routing_context'].get('moscow_transport_modes'),
                'meals': state['routing_context'].get('moscow_meals', {}).get(day, {}),
                'transit_filters': state['routing_context'].get('moscow_transit_filters', {}),
                'start_hints': state['routing_context'].get('moscow_start_hints', {}).get(day, {})}

    def _apply(self, state, sequences, op):
        context = state['routing_context']
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
        prepared = {**state, 'routing_context': context}
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
                original_matrix = state['travel_times']
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
        if context.get('moscow_meal_pins'):
            result['moscow_meal_pins'] = context['moscow_meal_pins']
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
    return parse_interpretation(value, extra_operations={
        'set_transport': {'op', 'transport'}, 'move_poi': {'op', 'poi_id', 'day'},
        'set_transport_modes': {'op', 'modes'},
        'set_transit_preferences': {'op', 'avoid', 'require'},
        'optimize_day': {'op', 'day', 'goal'},
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
        prompt += ('\nprice_rub — ориентировочный чек одного человека. Для явной просьбы убрать '
                   'заведение дороже указанного порога удаляй подходящие по оценке запланированные '
                   'необязательные заведения. Если порог неоднозначен, уточни его.')
        super().__init__(data={'pois': []}, interpreter=OpenRouterInterpreter(rich_context=True, system_prompt=prompt))
        self.configured = bool(os.environ.get('OPENROUTER_API_KEY', '').strip())
        self.snapshot = moscow_v7()[1]

    def _request(self, actor, session, action, body):
        if action == 'register':
            from backend.shanghai import MoscowPreferences

            session['moscow_preferences'] = MoscowPreferences.model_validate(body['preferences'])
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
            route = session['moscow_route']
            retained = [visit['poi_id'] for day in route['days'] for visit in day['visits']]
            previous = next((line['content'] for line in reversed(session['moscow_history']) if line['role'] == 'user'), '')
            profile, pois, ranking, matrix, context = moscow_inputs(
                session['moscow_preferences'], retained, body['text'], previous, route.get('moscow_transit_filters'))
            context['moscow_original_hours'] = deepcopy(context['opening_intervals'])
            if route.get('moscow_meal_pins'):
                context['moscow_meal_pins'] = deepcopy(route['moscow_meal_pins'])
            for settings, day in zip(context['days'], route['days']):
                meal = next((entry for entry in day['breaks'] if entry['kind'] == 'meal'), None)
                if meal:
                    settings['meal_break'] = meal
                if day.get('pace') and day['pace'] != profile['pace']:
                    context.setdefault('day_pace_overrides', {})[day['date']] = day['pace']
                for kind, slot in route.get('moscow_meals', {}).get(day['date'], {}).items():
                    require(fix_meal_time(context, day['date'], slot['poi_id'], kind, slot['start_at'], slot['end_at']),
                            'MEAL_TIME_UNAVAILABLE')
            store = InMemoryRouteStore()
            store.register_route(actor, body['route_id'], preferences=profile, pois=pois, recommendation=ranking,
                                 travel_times=matrix, routing_context=context, poi_snapshot_id=self.snapshot,
                                 route=route, version=body['base_version'])
            agent = MoscowRouteAssistant(store, lambda payload: self.interpreter({
                **payload, 'conversation': session['moscow_history'][-12:],
            }), proposal_ttl_seconds=300, interpretation_parser=moscow_interpretation, allow_cost_edits=True)
            result = agent.propose(actor, {'schema_version': '0.2', **{key: body.get(key) for key in
                                    ('route_id', 'base_version', 'poi_snapshot_id', 'text')}})
            message = result.get('clarification') or 'Проверьте обновлённый маршрут.'
            session['moscow_history'] = [*session['moscow_history'], {'role': 'user', 'content': body['text']},
                                         {'role': 'assistant', 'content': message}][-24:]
            session['route_options'] = {**session['route_options'], 'conversation': session['moscow_history']}
            if result['validation_status'] != 'ready_for_confirmation':
                return {'type': 'clarification', 'message': message}
            diff = result['diff']
            require(diff['changed_days'], 'NO_ROUTE_CHANGE')
            session['moscow_assistant'] = agent
            names = {poi['id']: poi['name'] for poi in pois}
            changes = {}
            for action, first, second in [('added', 'after', 'before'), ('removed', 'before', 'after')]:
                changes[action] = [{'name': names[key], 'day': day} for day in diff['changed_days']
                                   for key in diff[first][day] if key not in diff[second][day]]
            changes['before_cost'] = route['budget_summary']['known_total']
            changes['after_cost'] = result['candidate_route']['budget_summary']['known_total']
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
                transport = session['moscow_proposed_transport']
                if transport:
                    update = {'transport': transport, 'transport_modes': session['moscow_proposed_modes']}
                    session['route_options'] = {**session['route_options'], **update}
                    session['moscow_preferences'] = session['moscow_preferences'].model_copy(update=update)
            return {'type': 'confirmed' if confirmed else 'rejected', 'version': result['version'],
                    'message': 'Маршрут обновлён.' if confirmed else 'Исходный маршрут сохранён.',
                    **({'plan': result['route'], 'options': session['route_options']} if confirmed else {})}
        require(False, 'UNKNOWN_ENDPOINT')


@lru_cache(maxsize=1)
def moscow_assistant():
    return MoscowAssistant()
