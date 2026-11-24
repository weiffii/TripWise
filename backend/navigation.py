"""Путь от выбранной пользователем координаты к месту выбранного перехода.

Используется тот же OSRM-сервис, что уже подключён картой напарника.
Ни геокодирования адресов, ни выдуманных прямых линий при отказе сервиса нет.
"""

import json
from math import isfinite
from os import environ
from typing import Literal
from urllib.error import URLError
from urllib.request import urlopen

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.accounts import current_user, same_origin
from backend.shanghai import catalog, owned_route

router = APIRouter(tags=['Навигация'])
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class Position(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lat: float = Field(strict=True, ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(strict=True, ge=-180, le=180, allow_inf_nan=False)


class NavigationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(strict=True, ge=1)
    day: str = Field(min_length=10, max_length=10)
    leg_index: int = Field(strict=True, ge=0)
    origin: Position
    mode: Literal['walking', 'car'] = 'walking'


def nonnegative(value):
    return type(value) in (int, float) and isfinite(value) and value >= 0


def instruction(step):
    maneuver = step.get('maneuver') or {}
    kind, modifier = maneuver.get('type'), maneuver.get('modifier')
    turns = {'left': 'Поверните налево', 'right': 'Поверните направо',
             'slight left': 'Плавно поверните налево', 'slight right': 'Плавно поверните направо',
             'sharp left': 'Резко поверните налево', 'sharp right': 'Резко поверните направо',
             'uturn': 'Развернитесь', 'straight': 'Продолжайте прямо'}
    if kind == 'arrive':
        text = 'Вы у места назначения'
    elif kind == 'depart':
        text = 'Начните движение'
    elif kind in {'roundabout', 'rotary', 'roundabout turn'}:
        text = 'На круговом движении следуйте по маршруту'
        exit_number = maneuver.get('exit')
        if type(exit_number) is int and exit_number > 0:
            text += f', съезд {exit_number}'
    else:
        text = turns.get(modifier, 'Продолжайте движение по маршруту')
    name = step.get('name')
    if isinstance(name, str) and name.strip() and kind != 'arrive':
        text += ' · ' + name[:200]
    distance = step.get('distance')
    if nonnegative(distance) and distance > 0:
        text += f' · {round(distance)} м'
    return text


def provider_route(origin, destination, mode, city=None):
    profile = 'routed-foot' if mode == 'walking' else 'routed-car'
    locations = f"{origin['lon']},{origin['lat']};{destination['lon']},{destination['lat']}"
    query = '?overview=full&geometries=geojson&steps=true&generate_hints=false&radiuses=180;180'
    if city == 'moscow':
        profile_name, port = ('FOOT', 5001) if mode == 'walking' else ('CAR', 5002)
        local = environ.get(f'MOSCOW_OSRM_{profile_name}', f'http://127.0.0.1:{port}/route/v1/driving')
        try:
            return _read_provider(local.rstrip('/') + '/' + locations + query, 3, 'moscow-osrm')
        except HTTPException:
            pass  # A local extract can miss the selected start; the existing public provider can cover it.
    public = f'https://routing.openstreetmap.de/{profile}/route/v1/driving/'
    return _read_provider(public + locations + query, 8, 'routing.openstreetmap.de/osrm')


def _read_provider(url, timeout, source):
    try:
        with urlopen(url, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError('Response too large')
        body = json.loads(raw)
        if body.get('code') == 'NoRoute':
            raise HTTPException(422, 'Дорожный сервис не нашёл путь. Выберите другую начальную точку.')
        route = body['routes'][0]
        geometry = route['geometry']
        coordinates = geometry['coordinates']
        waypoints = body['waypoints']
        if (body.get('code') != 'Ok' or geometry.get('type') != 'LineString'
                or not isinstance(coordinates, list) or len(coordinates) < 2
                or not all(isinstance(p, list) and len(p) == 2
                           and all(type(v) in (int, float) and isfinite(v) for v in p)
                           and -180 <= p[0] <= 180 and -90 <= p[1] <= 90 for p in coordinates)
                or not nonnegative(route['duration']) or not nonnegative(route['distance'])
                or len(waypoints) != 2
                or any(not nonnegative(p['distance']) or p['distance'] > 180 for p in waypoints)):
            raise ValueError('Invalid route')
        steps = [instruction(step) for leg in route.get('legs', []) for step in leg.get('steps', [])]
        return {'geometry': geometry, 'distance_meters': route['distance'],
                'duration_seconds': route['duration'], 'steps': steps,
                'source_id': source, 'basis': 'routing_provider'}
    except HTTPException:
        raise
    except (OSError, URLError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise HTTPException(503, 'Дорожный сервис временно недоступен. Путь и повороты не рассчитаны; попробуйте позже.') from None


@router.post('/api/{collection}/{route_id}/navigate', dependencies=[Depends(same_origin)])
def navigate(collection: Literal['route-previews', 'planned-trips'], route_id: str,
             data: NavigationRequest, request: Request, user: dict = Depends(current_user)):
    if route_id.startswith('preview-') != (collection == 'route-previews'):
        raise HTTPException(404, 'Маршрут не найден.')
    route = owned_route(route_id, user['id'], request)
    if route['version'] != data.version:
        raise HTTPException(409, 'Маршрут изменился. Откройте актуальную версию.')
    day = next((day for day in route['plan'].get('days', []) if day['date'] == data.day), None)
    legs = ([row for row in day['events'] if row.get('type') == 'travel']
            if day and 'events' in day else (day or {}).get('legs', []))
    if data.leg_index >= len(legs):
        raise HTTPException(404, 'Переход не найден.')
    leg = legs[data.leg_index]
    place = next((p for p in catalog(route['city'], include_legacy=True) if p['id'] == leg['to_poi_id']), None)
    if place is None:
        raise HTTPException(422, 'Координаты места назначения недоступны.')
    destination = {'lat': place['lat'], 'lon': place['lon'], 'poi_id': place['id'], 'name': place['name']}
    try:
        Position.model_validate({'lat': destination['lat'], 'lon': destination['lon']})
    except ValueError:
        raise HTTPException(422, 'Координаты места назначения не подтверждены.') from None
    origin = data.origin.model_dump()
    if abs(origin['lat'] - destination['lat']) > 1 or abs(origin['lon'] - destination['lon']) > 1.5:
        raise HTTPException(422, 'Выберите начальную точку в районе города поездки.')
    result = provider_route(origin, destination, data.mode, route['city'])
    return {**result, 'mode': data.mode, 'origin': origin, 'destination': destination,
            'day': data.day, 'leg_index': data.leg_index, 'route_version': route['version']}
