from datetime import timedelta
from functools import lru_cache
import json
import os
from pathlib import Path

from fastapi import HTTPException

from ai_ds.multiday import build_route
from ai_ds.recommendation import recommend
from ai_ds.route_assistant import InMemoryRouteStore, RouteAssistant, require
from ai_ds.openrouter import OpenRouterInterpreter
from ai_ds.shanghai_assistant import ShanghaiAssistantService


BASE = Path(__file__).resolve().parents[1] / 'examples/routing/v02'


def read(kind):
    return json.loads((BASE / f'moscow.reviewed-three.{kind}.json').read_text(encoding='utf-8-sig'))


@lru_cache(maxsize=1)
def moscow_pois():
    return read('pois')


def moscow_inputs(preferences):
    if preferences.transport != 'walk' or preferences.budget_currency != 'RUB':
        raise HTTPException(422, 'Для пилота Москвы доступны пешие переходы и бюджет в рублях.')
    pois = moscow_pois()
    if not set(preferences.required_ids) <= {poi['id'] for poi in pois}:
        raise HTTPException(422, 'Обязательное место не входит в проверенный каталог Москвы.')
    scenario = read('scenario')
    context = scenario['routing_context']
    profile = read('preferences')
    people = preferences.group.adult + preferences.group.child + preferences.group.senior
    amount = preferences.budget_amount * (people if preferences.budget_basis == 'per_person' else 1)
    profile.update(date_start=preferences.start_date.isoformat(),
                   date_end=(preferences.start_date + timedelta(days=preferences.days - 1)).isoformat(),
                   primary_interests=preferences.primary_interests, secondary_interests=preferences.secondary_interests,
                   required_poi_ids=preferences.required_ids, pace=preferences.pace)
    profile['budget']['amount'] = amount
    for row in context['cost_context']['visit_costs']:
        row['amount'] *= people
    context['days'] = []
    for index in range(preferences.days):
        day = (preferences.start_date + timedelta(days=index)).isoformat()
        context['days'].append({'date': day, 'day_start': f'{day}T{preferences.day_start}:00+03:00',
                                'day_end': f'{day}T{preferences.day_end}:00+03:00', 'meal_break': None,
                                'blocked_intervals': [], 'max_active_seconds': None})
    return profile, pois, recommend(profile, pois), scenario['travel_times'], context


def plan_moscow(preferences):
    inputs = moscow_inputs(preferences)
    plan = build_route(*inputs)
    return plan, {'start_date': preferences.start_date.isoformat()}, plan['poi_snapshot_id']


class MoscowAssistant(ShanghaiAssistantService):
    def __init__(self):
        super().__init__(data={'pois': []}, interpreter=OpenRouterInterpreter())
        self.configured = bool(os.environ.get('OPENROUTER_API_KEY', '').strip())
        self.snapshot = read('scenario')['routing_context']['poi_snapshot_id']

    def _request(self, actor, session, action, body):
        if action == 'register':
            from backend.shanghai import MoscowPreferences

            profile, pois, recommendation, matrix, context = moscow_inputs(MoscowPreferences.model_validate(body['preferences']))
            store = InMemoryRouteStore()
            store.register_route(actor, body['route_id'], preferences=profile, pois=pois, recommendation=recommendation,
                                 travel_times=matrix, routing_context=context, poi_snapshot_id=self.snapshot,
                                 route=body['plan'], version=body['version'])
            session['moscow_assistant'] = RouteAssistant(store, self.interpreter, proposal_ttl_seconds=300)
            return {'type': 'registered', 'route_id': body['route_id'], 'version': body['version'], 'poi_snapshot_id': self.snapshot}
        agent = session.get('moscow_assistant')
        require(agent is not None and self.configured, 'SESSION_REQUIRED')
        if action == 'message':
            require(isinstance(body.get('text'), str) and 0 < len(body['text'].strip()) <= 3000, 'INVALID_MESSAGE')
            result = agent.propose(actor, {'schema_version': '0.2', **{key: body.get(key) for key in
                                    ('route_id', 'base_version', 'poi_snapshot_id', 'text')}})
            if result['validation_status'] != 'ready_for_confirmation':
                return {'type': 'clarification', 'message': result.get('clarification') or 'Уточните пожелание.'}
            return {'type': 'proposal', 'plan': result['candidate_route'], 'message': 'Проверьте обновлённый маршрут.',
                    'proposal_id': result['proposal_id'], 'base_version': result['base_version']}
        if action == 'decide':
            result = agent.decide(actor, {key: body.get(key) for key in ('proposal_id', 'base_version', 'decision')}, allow_draft=True)
            confirmed = result['decision'] == 'confirmed'
            return {'type': 'confirmed' if confirmed else 'rejected', 'version': result['version'],
                    'message': 'Маршрут обновлён.' if confirmed else 'Исходный маршрут сохранён.',
                    **({'plan': result['route'], 'options': session['route_options']} if confirmed else {})}
        require(False, 'UNKNOWN_ENDPOINT')


@lru_cache(maxsize=1)
def moscow_assistant():
    return MoscowAssistant()
