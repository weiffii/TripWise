"""Компактный доступ к исходным карточкам, без пересчёта параметров маршрутизации."""

from functools import lru_cache
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=2)
def city_places(city):
    paths = {
        'moscow': 'data/demo/moscow/v7/pois_real_master_expanded.json',
        'shanghai': 'data/demo/shanghai/pois.json',
    }
    with (ROOT / paths[city]).open(encoding='utf-8-sig') as source:
        points = json.load(source)['pois']
    return {point['id']: point for point in points}


@lru_cache(maxsize=1)
def legacy_places():
    from backend.moscow import moscow_pois
    return {point['id']: point for point in moscow_pois()}


def place_summary(point):
    coordinates = point.get('coordinates') or {}
    return {
        'id': point['id'],
        'name': (point.get('names') or {}).get('ru') or point['name'],
        'kind': point.get('kind'),
        'lat': point.get('lat', coordinates.get('latitude')),
        'lon': point.get('lon', coordinates.get('longitude')),
    }
