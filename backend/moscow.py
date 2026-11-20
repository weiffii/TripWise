from collections import defaultdict
from datetime import timedelta
from functools import lru_cache
from heapq import nsmallest
import json
from math import floor
import os
from pathlib import Path
import re

from fastapi import HTTPException

from ai_ds.moscow_catalog import load_moscow_v7
from ai_ds.moscow_road import coordinates, travel_matrix
from ai_ds.moscow_transit import _distance
from ai_ds.multiday import build_route
from ai_ds.openrouter import OpenRouterInterpreter, SYSTEM_PROMPT
from ai_ds.recommendation import recommend
from ai_ds.route_assistant import InMemoryRouteStore, RouteAssistant, parse_interpretation, require
from ai_ds.shanghai_assistant import ShanghaiAssistantService

BASE = Path(__file__).resolve().parents[1] / 'examples/routing/v02'


@lru_cache(maxsize=1)
def moscow_pois():
    # Идентификаторы уже сохранённых поездок старого пилота.
    return json.loads((BASE / 'moscow.reviewed-three.pois.json').read_text(encoding='utf-8-sig'))


@lru_cache(maxsize=1)
def moscow_v7():
    return load_moscow_v7(planning=True)


def tokens(value):
    return set(re.findall(r'\w+', value.casefold().replace('ё', 'е')))


def cell(point):
    return floor(point['lat'] * 40), floor(point['lon'] * 25)


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


def shortlist(preferences, retained=(), query='', prior_query=''):
    by_id, categories, words = indexes()
    ids = list(dict.fromkeys([*preferences.required_ids, *retained]))
    if not set(ids) <= by_id.keys():
        raise HTTPException(422, 'Место отсутствует в каталоге Москвы V7.')
    anchors = [coordinates(by_id[key]) for key in preferences.required_ids] or [{'lat': 55.752, 'lon': 37.620}]
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
    return [by_id[key] for key in dict.fromkeys(ids)]


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
                             r'((?:[01]\d|2[0-3]):[0-5]\d)-((?:[01]\d|2[0-3]):[0-5]\d|24:00)', rule.strip())
        if match is None:
            return None
        first, last, opens, closes = match.groups()
        if first is None or WEEKDAYS.index(first) <= weekday <= WEEKDAYS.index(last or first):
            if opens >= closes:
                return None
            windows.append((opens, '23:59' if closes == '24:00' else closes))
    return windows[0] if len(windows) == 1 else None


def moscow_inputs(preferences, retained=(), query='', prior_query=''):
    if preferences.budget_currency != 'RUB':
        raise HTTPException(422, 'Для Москвы выберите бюджет в рублях.')
    pois = shortlist(preferences, retained, query, prior_query)
    mode = {'walk': 'walking', 'taxi': 'car', 'transit': 'public_transit', 'mixed': 'public_transit'}[preferences.transport]
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
        meal = None
        if preferences.day_start <= '13:00' and preferences.day_end >= '14:30':
            meal = dict(kind='meal', start_at=stamp(day, '13:15'), end_at=stamp(day, '14:00'))
        days.append(dict(date=day, day_start=stamp(day, preferences.day_start),
                         day_end=stamp(day, preferences.day_end), meal_break=meal))
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
                                 intervals=[] if window is None else [dict(start_at=stamp(day, window[0]), end_at=stamp(day, window[1]))],
                                 source_id=hours['model_version'] if estimated else provenance['source_id'],
                                 retrieved_at=provenance['retrieved_at'], source_url=None if estimated else provenance['source_url']))
    context = dict(schema_version='0.2', endpoint_policy='first_last_poi', pace_policy_version='pace-policy-v0.2',
                   unknown_duration_policy='strict', poi_snapshot_id=moscow_v7()[1], days=days,
                   optimize_local_order=True,
                   duration_overrides=durations, opening_intervals=openings,
                   cost_context=dict(currency='RUB', coverage_complete=False, visit_costs=costs, meal_costs=[]))
    return profile, pois, recommend(profile, pois), travel_matrix(pois, mode), context


def plan_moscow(preferences):
    inputs = moscow_inputs(preferences)
    plan = build_route(*inputs)
    plan['suggested_poi_ids'] = [item['poi_id'] for item in inputs[2]['items']]
    return plan, {'start_date': preferences.start_date.isoformat(), 'transport': preferences.transport}, plan['poi_snapshot_id']


TRANSPORTS = {'walking': 'walk', 'car': 'taxi', 'public_transit': 'transit'}


class MoscowRouteAssistant(RouteAssistant):
    def _settings(self, state, day):
        return {**super()._settings(state, day), 'transport': state['preferences']['transport']}

    def _apply(self, state, sequences, op):
        if op['op'] != 'set_transport':
            return super()._apply(state, sequences, op)
        mode = op['transport']
        require(isinstance(mode, str) and mode in TRANSPORTS, 'TRANSPORT_UNAVAILABLE')
        matrix = travel_matrix(state['pois'], mode)
        if mode == 'public_transit':
            require(any(pair.get('transport_steps') for pair in matrix['pairs']), 'TRANSPORT_UNAVAILABLE')
        else:
            require(any(pair['basis'] == 'routing_provider' and pair['status'] == 'known'
                        for pair in matrix['pairs']), 'TRANSPORT_UNAVAILABLE')
        state['preferences']['transport'] = mode
        state['travel_times'] = matrix
        state['requested_transport'] = mode

    def _candidate(self, state, sequences):
        result = super()._candidate(state, sequences)
        mode = state.get('requested_transport')
        if mode:
            legs = [leg for day in result['days'] for leg in day['legs']]
            if mode == 'public_transit':
                require(any(leg.get('transport_steps') for leg in legs), 'TRANSPORT_UNAVAILABLE')
            else:
                require(legs and all(leg['basis'] == 'routing_provider' for leg in legs), 'TRANSPORT_UNAVAILABLE')
        return result


def moscow_interpretation(value):
    return parse_interpretation(value, extra_operations={'set_transport': {'op', 'transport'}})


class MoscowAssistant(ShanghaiAssistantService):
    def __init__(self):
        prompt = SYSTEM_PROMPT.replace('город, transport, бюджет или required.',
            'город, бюджет или required. Для явной смены транспорта используй set_transport: '
            'op, transport (walking|car|public_transit). Для запрета метро при сохранении общественного '
            'транспорта объясни, что фильтр отдельных видов транспорта недоступен.')
        super().__init__(data={'pois': []}, interpreter=OpenRouterInterpreter(rich_context=True, system_prompt=prompt))
        self.configured = bool(os.environ.get('OPENROUTER_API_KEY', '').strip())
        self.snapshot = moscow_v7()[1]

    def _request(self, actor, session, action, body):
        if action == 'register':
            from backend.shanghai import MoscowPreferences

            session['moscow_preferences'] = MoscowPreferences.model_validate(body['preferences'])
            transport = session['route_options'].get('transport')
            if transport in TRANSPORTS.values() or transport == 'mixed':
                session['moscow_preferences'] = session['moscow_preferences'].model_copy(update={'transport': transport})
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
                session['moscow_preferences'], retained, body['text'], previous)
            for settings, day in zip(context['days'], route['days']):
                meal = next((entry for entry in day['breaks'] if entry['kind'] == 'meal'), None)
                if meal:
                    settings['meal_break'] = meal
                if day.get('pace') and day['pace'] != profile['pace']:
                    context.setdefault('day_pace_overrides', {})[day['date']] = day['pace']
            store = InMemoryRouteStore()
            store.register_route(actor, body['route_id'], preferences=profile, pois=pois, recommendation=ranking,
                                 travel_times=matrix, routing_context=context, poi_snapshot_id=self.snapshot,
                                 route=route, version=body['base_version'])
            agent = MoscowRouteAssistant(store, lambda payload: self.interpreter({
                **payload, 'conversation': session['moscow_history'][-12:],
            }), proposal_ttl_seconds=300, interpretation_parser=moscow_interpretation)
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
            move = next((op['transport'] for op in reversed(result['operations']) if op['op'] == 'set_transport'), None)
            session['moscow_proposed_transport'] = TRANSPORTS.get(move)
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
                    session['route_options'] = {**session['route_options'], 'transport': transport}
                    session['moscow_preferences'] = session['moscow_preferences'].model_copy(update={'transport': transport})
            return {'type': 'confirmed' if confirmed else 'rejected', 'version': result['version'],
                    'message': 'Маршрут обновлён.' if confirmed else 'Исходный маршрут сохранён.',
                    **({'plan': result['route'], 'options': session['route_options']} if confirmed else {})}
        require(False, 'UNKNOWN_ENDPOINT')


@lru_cache(maxsize=1)
def moscow_assistant():
    return MoscowAssistant()
