"""Повторная проверка геометрии одного сохранённого перехода."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ai_ds.moscow_road import travel_matrix
from backend.accounts import current_user, same_origin
from backend.moscow import indexes
from backend.shanghai import owned_route


router = APIRouter(tags=['Геометрия маршрута'])


class SegmentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(strict=True, ge=1)
    day: str
    leg_index: int = Field(strict=True, ge=0)


def step_signature(steps):
    return [(step['kind'], step.get('line'), step.get('from_stop'), step.get('to_stop')) for step in steps]


@router.post('/api/{collection}/{route_id}/leg-geometry', dependencies=[Depends(same_origin)])
def refresh_segment(collection: Literal['route-previews', 'planned-trips'], route_id: str,
                    data: SegmentRequest, request: Request, user: dict = Depends(current_user)):
    if route_id.startswith('preview-') != (collection == 'route-previews'):
        raise HTTPException(404, 'Маршрут не найден.')
    route = owned_route(route_id, user['id'], request)
    if data.version != route['version']:
        raise HTTPException(409, 'Маршрут изменился. Обновите страницу.')
    if route['city'] != 'moscow' or not route['poi_snapshot_id'].startswith('moscow-v7:'):
        raise HTTPException(422, 'Повторная проверка участка доступна для актуального маршрута Москвы.')
    plan = route['plan']
    legs = next((day['legs'] for day in plan['days'] if day['date'] == data.day), [])
    if data.leg_index >= len(legs):
        raise HTTPException(404, 'Переход не найден.')
    leg = legs[data.leg_index]
    start, end = leg['from_poi_id'], leg['to_poi_id']
    catalog = indexes()[0]
    mode = {'walk': 'walking', 'taxi': 'car', 'transit': 'public_transit'}.get(leg.get('mode'))
    if not mode or start not in catalog or end not in catalog:
        raise HTTPException(422, 'Недостаточно данных для проверки этого перехода.')
    filters = plan.get('moscow_transit_filters', {}) if mode == 'public_transit' else {}
    matrix = travel_matrix([catalog[start], catalog[end]], mode, allow_walking=mode != 'public_transit',
                           only_pairs={(start, end)}, **filters)
    pair = next(row for row in matrix['pairs'] if row['from_poi_id'] == start and row['to_poi_id'] == end)
    if pair.get('geometry') and pair.get('basis') == 'routing_provider':
        return {key: pair[key] for key in ('geometry', 'basis', 'distance_meters', 'duration_seconds')}
    previous = leg.get('transport_steps') or []
    for candidate in (pair, pair.get('required_transit_alternative') or {}):
        steps = candidate.get('transport_steps') or []
        if previous and step_signature(steps) == step_signature(previous) and any(
                new.get('geometry_verified') and not old.get('geometry_verified')
                for old, new in zip(previous, steps)):
            steps = [old if old.get('geometry') and not new.get('geometry') else new
                     for old, new in zip(previous, steps)]
            return {'transport_steps': steps, 'duration_seconds': candidate['duration_seconds']}
    raise HTTPException(503, 'Для этого перехода дорожная геометрия пока недоступна. Попробуйте позже.')
