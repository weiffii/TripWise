"""Адаптер к отдельным профилям OSRM Москвы."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import json
from math import ceil, isfinite
import os
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError
from urllib.request import urlopen

from ai_ds.moscow_transit import _distance, build_graph, build_matrix


MAX_SNAP_METERS = 180
MAX_ENTRANCE_SNAP_METERS = 40
MAX_RESPONSE_BYTES = 512 * 1024
STATION_SEARCH_METERS = 1000
MAX_STATION_ENTRANCES = 3
PLATFORM_ACCESS_ESTIMATE_SECONDS = 120
PILOT_RAIL_CONFIG = Path(__file__).resolve().parents[1] / 'data/sources/moscow-rail-config.json'


@lru_cache(maxsize=2)
def rail_inputs(config_path):
    path = Path(config_path)
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    snapshot = json.loads((path.parent / config['osm_snapshot']).read_text(encoding='utf-8-sig'))

    def section(name, default):
        value = config.get(name, default)
        if isinstance(value, str):
            data = json.loads((path.parent / value).read_text(encoding='utf-8-sig'))
            return data.get(name, data) if isinstance(data, dict) else data
        return value

    graph = build_graph(snapshot, config['estimates'], section('transfers', []))
    nodes = {node['id']: node for node in snapshot['elements'] if node['type'] == 'node'}
    return graph, section('approaches', {}), nodes, section('station_entrances', {})


def rail_pairs(pois, endpoint=None, window=0):
    config = os.environ.get('MOSCOW_RAIL_CONFIG', str(PILOT_RAIL_CONFIG))
    if not config:
        return {}
    graph, approaches, nodes, entrances = rail_inputs(config)
    access = {}
    normalized = {}
    for poi, directions in approaches.items():
        for direction in ('enter', 'exit'):
            slots = normalized.setdefault(poi, {}).setdefault(direction, {})
            for stop, value in directions.get(direction, {}).items():
                if isinstance(value, dict):
                    slots[str(stop)] = value['duration_seconds']
                    access[poi, direction, int(stop)] = {
                        key: deepcopy(value[key]) for key in (
                            'geometry', 'entrance_source_id', 'distance_meters', 'source_url',
                            'external_duration_seconds', 'internal_duration_seconds',
                        ) if key in value
                    }
                else:
                    slots[str(stop)] = value
    candidates = routed_station_access(pois, entrances, graph, endpoint, window) if entrances and endpoint else {}
    for (poi, direction, stop), leg in candidates.items():
        slots = normalized.setdefault(poi, {}).setdefault(direction, {})
        if str(stop) not in slots:
            slots[str(stop)] = leg['duration_seconds']
            access[poi, direction, stop] = leg
    pairs = {}
    for pair in build_matrix(graph, [poi['id'] for poi in pois], normalized)['pairs']:
        steps = pair['transport_steps']
        if not any(step['kind'] == 'rail_estimate' for step in steps):
            continue
        for step in steps:
            if step['kind'] == 'walk_access':
                key = pair['from_poi_id'], 'enter', step['to_stop']
            elif step['kind'] == 'walk_egress':
                key = pair['to_poi_id'], 'exit', step['from_stop']
            else:
                key = None
            if key in access:
                step.update(access[key])
            for key in ('from_stop', 'to_stop'):
                if step[key] in nodes:
                    node = nodes[step[key]]
                    step[key + '_location'] = {'name': node.get('tags', {}).get('name'),
                                               'coordinates': [node['lon'], node['lat']]}
        pairs[pair['from_poi_id'], pair['to_poi_id']] = {
            'status': 'known', 'duration_seconds': ceil(pair['duration_seconds']) + 90,
            'distance_meters': None, 'transport_steps': steps,
            'basis': 'geometric_estimate', 'source_id': 'osm-rail-estimated-time',
        }
    return pairs


def coordinates(poi):
    return {'lat': poi['coordinates']['latitude'], 'lon': poi['coordinates']['longitude']}


def routed_station_access(pois, entrances, graph, endpoint, window):
    """Связывает POI с указанными входами, сохраняя раздельные направления."""
    board = {stop for (stop, trip), links in graph.items()
             if trip is None and any(edge[0] == 'wait_estimate' for edge in links.values())}
    alight = {stop for links in graph.values() for (stop, trip), edge in links.items()
              if trip is None and edge[0] == 'alight'}
    by_entrance = {}
    for stop, points in entrances.items():
        stop = int(stop)
        if stop not in board and stop not in alight:
            continue
        for point in points:
            position = point['latitude'], point['longitude']
            source_id = point['source_id']
            if not source_id or not (-90 <= position[0] <= 90 and -180 <= position[1] <= 180):
                continue
            directions = by_entrance.setdefault((position, source_id), {'enter': set(), 'exit': set()})
            if point.get('can_enter') is True and stop in board:
                directions['enter'].add(stop)
            if point.get('can_exit') is True and stop in alight:
                directions['exit'].add(stop)

    def candidate_requests():
        for poi in pois:
            location = coordinates(poi)
            origin = location['lat'], location['lon']
            for direction in ('enter', 'exit'):
                nearby = sorted(
                    (_distance(location, {'lat': position[0], 'lon': position[1]}), position, source_id)
                    for (position, source_id), directions in by_entrance.items() if directions[direction]
                )[:MAX_STATION_ENTRANCES]
                for distance, position, source_id in nearby:
                    if distance <= STATION_SEARCH_METERS:
                        start, end = (origin, position) if direction == 'enter' else (position, origin)
                        yield poi['id'], position, source_id, direction, start, end

    def resolve(request):
        poi_id, position, source_id, direction, start, end = request
        radii = (MAX_SNAP_METERS, MAX_ENTRANCE_SNAP_METERS)
        route = road_pair(endpoint, start, end, window, radii if direction == 'enter' else radii[::-1])
        if not route or route['status'] != 'known':
            return []
        leg = {
            'duration_seconds': route['duration_seconds'] + PLATFORM_ACCESS_ESTIMATE_SECONDS,
            'geometry': route['geometry'],
            'entrance_source_id': source_id,
            'distance_meters': route['distance_meters'],
            'external_duration_seconds': route['duration_seconds'],
            'internal_duration_seconds': PLATFORM_ACCESS_ESTIMATE_SECONDS,
        }
        return [((poi_id, direction, stop), leg) for stop in by_entrance[position, source_id][direction]]

    access = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        for entries in executor.map(resolve, candidate_requests()):
            for key, leg in entries:
                old = access.get(key)
                if old is None or leg['duration_seconds'] < old['duration_seconds']:
                    access[key] = leg
    return access


def nonnegative(value):
    return type(value) in (int, float) and isfinite(value) and value >= 0


def valid_geometry(geometry):
    points = geometry.get('coordinates')
    return geometry.get('type') == 'LineString' and isinstance(points, list) and len(points) >= 2 and all(
        isinstance(point, list) and len(point) == 2 and
        all(type(value) in (int, float) for value in point) and
        -180 <= point[0] <= 180 and -90 <= point[1] <= 90 for point in points
    )


def missing_pair(status):
    return dict(status=status, duration_seconds=None, distance_meters=None,
                basis='routing_provider', source_id='moscow-osrm')


@lru_cache(maxsize=4096)
def road_pair(endpoint, origin, target, cache_window, radii=(MAX_SNAP_METERS, MAX_SNAP_METERS)):
    points = ';'.join(f'{point[1]},{point[0]}' for point in (origin, target))
    radius_query = ';'.join(map(str, radii))
    url = f'{endpoint.rstrip("/")}/{points}?overview=full&geometries=geojson&radiuses={radius_query}'
    try:
        try:
            with urlopen(url, timeout=3) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            if error.code != 400:
                return None
            with error:
                raw = error.read(MAX_RESPONSE_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_RESPONSE_BYTES:
        return missing_pair('unknown')
    try:
        result = json.loads(raw)
        code = result['code']
        if code == 'NoRoute':
            return missing_pair('unreachable')
        if code != 'Ok':
            return missing_pair('unknown')
        route = result['routes'][0]
        waypoints = result['waypoints']
        if len(waypoints) != 2 or any(not nonnegative(point['distance']) or
                                     point['distance'] > radius for point, radius in zip(waypoints, radii)):
            return missing_pair('unknown')
        if not (nonnegative(route['duration']) and nonnegative(route['distance']) and
                valid_geometry(route['geometry'])):
            return missing_pair('unknown')
        return dict(status='known', duration_seconds=ceil(route['duration']) + 90,
                    distance_meters=round(route['distance']), geometry=route['geometry'],
                    basis='routing_provider', source_id='moscow-osrm')
    except (ValueError, KeyError, IndexError, TypeError, AttributeError, OverflowError):
        return missing_pair('unknown')


def travel_matrix(pois, mode):
    endpoint = os.environ.get({'walking': 'MOSCOW_OSRM_FOOT', 'car': 'MOSCOW_OSRM_CAR',
                               'public_transit': 'MOSCOW_OSRM_FOOT'}[mode])
    rows = [(a, b) for a in pois for b in pois if a['id'] != b['id']]
    now = datetime.now(timezone.utc).isoformat()
    window = int(monotonic() // 300)
    first = None
    rail = {}

    def provider(row):
        start, end = map(coordinates, row)
        return road_pair(endpoint, (start['lat'], start['lon']), (end['lat'], end['lon']), window)

    def pair(row, data=None):
        a, b = row
        by_rail = rail.get((a['id'], b['id']))
        if by_rail and (data is None or data['status'] != 'known' or
                        by_rail['duration_seconds'] < data['duration_seconds']):
            data = by_rail
        if data is None:
            distance = _distance(coordinates(a), coordinates(b))
            data = dict(status='known', duration_seconds=ceil(distance * 1.45 / (5 if mode == 'car' else 1.05)) + 180,
                        distance_meters=None, basis='geometric_estimate', source_id='moscow-straight-line-estimate')
        return dict(from_poi_id=a['id'], to_poi_id=b['id'], **data)

    if endpoint and rows:
        # При недоступном локальном сервере не запускаем серию одинаковых тайм-аутов.
        first = provider(rows[0])
    if mode == 'public_transit':
        rail = rail_pairs(pois, endpoint if first is not None else None, window)
    if endpoint and rows:
        if first is not None:
            with ThreadPoolExecutor(max_workers=8) as executor:
                pairs = [pair(rows[0], first), *map(pair, rows[1:], executor.map(provider, rows[1:]))]
        else:
            pairs = [pair(row) for row in rows]
    else:
        pairs = [pair(row) for row in rows]
    basis = ('routing_provider' if pairs and all(pair['basis'] == 'routing_provider' for pair in pairs)
             else 'geometric_estimate')
    return dict(mode=mode, basis=basis, source_id='moscow-directional-matrix',
                retrieved_at=now, pairs=pairs)
