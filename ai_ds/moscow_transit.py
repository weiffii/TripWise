from collections import defaultdict
from heapq import heappop, heappush
from itertools import count
from math import asin, cos, isfinite, radians, sin, sqrt
import re


STOP_ROLES = frozenset({'stop', 'stop_entry_only', 'stop_exit_only'})


def _distance(first, second):
    lat, lon = map(radians, (first['lat'], first['lon']))
    other_lat, other_lon = map(radians, (second['lat'], second['lon']))
    value = sin((other_lat-lat)/2)**2 + cos(lat)*cos(other_lat)*sin((other_lon-lon)/2)**2
    return 12_742_000 * asin(sqrt(min(1, value)))


def _duration(value):
    if type(value) not in (int, float) or not isfinite(value) or value < 0:
        raise ValueError('Недопустимое время перехода')
    return value


def _line(tags):
    route = tags.get('route')
    ref = tags.get('ref', '').strip().upper().replace('Д', 'D')
    mcd = re.fullmatch(r'(?:D|МЦD[- ]?)([1-4])[AА]?', ref)
    if route in ('train', 'light_rail') and mcd:
        return 'mcd:' + mcd[1]
    if route in ('train', 'light_rail', 'subway') and ref in ('14', 'МЦК'):
        return 'mcc:14'
    if route == 'bus' and ref and tags.get('electric_bus') == 'yes':
        return 'electric_bus:' + ref
    if route in ('tram', 'bus') and ref:
        return route + ':' + ref
    return 'metro:' + ref if route == 'subway' and ref else None


def _track_geometry(segment, origin, target):
    geometry = segment.get('geometry', {})
    if not isinstance(geometry, dict) or geometry.get('type') != 'LineString':
        return None
    points = geometry.get('coordinates')
    if not isinstance(points, list) or len(points) < 2 or any(
            not isinstance(point, list) or len(point) != 2 or
            any(type(value) not in (int, float) or not isfinite(value) for value in point) or
            not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90) for point in points):
        return None
    if points[0] != [origin['lon'], origin['lat']] or points[-1] != [target['lon'], target['lat']]:
        return None
    return geometry


def build_graph(document, policy, transfers=()):
    graph = defaultdict(dict)
    nodes = {item['id']: item for item in document['elements'] if item['type'] == 'node'}
    station_ids = set()
    for index, relation in enumerate(document['elements']):
        if relation['type'] != 'relation':
            continue
        line = _line(relation.get('tags', {}))
        if not line:
            continue
        # Варианты одной линии имеют собственные последовательности остановок.
        # Переход между ними требует выхода и нового ожидания, как при смене поезда.
        trip = (line, relation.get('id', index))
        estimates = policy.get(line.split(':')[0])
        if estimates is None:
            continue
        speed = _duration(estimates['meters_per_second'])
        if not speed:
            raise ValueError('Нулевая расчётная скорость')
        wait = _duration(estimates['wait_seconds'])
        segments = {(row['from_stop'], row['to_stop']): row for row in relation.get('segments', [])}
        stops = [(member['ref'], member['role']) for member in relation['members']
                 if member['type'] == 'node' and member.get('role') in STOP_ROLES]
        for stop, role in stops:
            if stop not in nodes:
                continue
            station_ids.add(stop)
            if role != 'stop_exit_only':
                graph[stop, None][stop, trip] = ('wait_estimate', wait)
            if role != 'stop_entry_only':
                graph[stop, trip][stop, None] = ('alight', 0)
        for (origin, _), (target, _) in zip(stops, stops[1:]):
            if origin != target and origin in nodes and target in nodes:
                start, end = nodes[origin], nodes[target]
                geometry = _track_geometry(segments.get((origin, target), {}), start, end)
                if geometry:
                    points = [{'lon': lon, 'lat': lat} for lon, lat in geometry['coordinates']]
                    distance = sum(_distance(a, b) for a, b in zip(points, points[1:]))
                    details = {'geometry': geometry, 'geometry_verified': True,
                               'distance_meters': round(distance),
                               'geometry_source_url': f'https://www.openstreetmap.org/relation/{trip[1]}'}
                    edge = ('rail_estimate', max(1, round(distance / speed)), details)
                else:
                    edge = ('rail_estimate', max(1, round(_distance(start, end) / speed)))
                graph[origin, trip][target, trip] = edge
    for link in transfers:
        origin, target = link['from_stop'], link['to_stop']
        if origin not in station_ids or target not in station_ids or not link['source_id']:
            raise ValueError('Пересадка без подтверждённой связи')
        graph[origin, None][target, None] = ('walk_transfer', _duration(link['duration_seconds']), link['source_id'])
    if not graph:
        raise ValueError('Не найдены остановки рельсовых маршрутов')
    return graph


def route_between(graph, entrances, exits, *, avoid_transit=(), require_transit=()):
    start, finish = (None, 'start'), (None, 'finish')
    head = {(stop, None): ('walk_access', _duration(seconds)) for stop, seconds in entrances.items() if (stop, None) in graph}
    tail = {(stop, None): ('walk_egress', _duration(seconds)) for stop, seconds in exits.items()}
    avoided = set(avoid_transit)
    required = {mode: 1 << (index + 1) for index, mode in enumerate(sorted(set(require_transit)))}
    if avoided & required.keys():
        return None
    objective = 1 | sum(required.values())
    serial, best, parents = count(), {(start, 0): 0}, {}
    queue = [(0, next(serial), start, 0)]
    while queue:
        duration, _, vertex, used = heappop(queue)
        state = (vertex, used)
        if duration != best[state]:
            continue
        if vertex == finish:
            steps = []
            while state[0] != start:
                vertex = state[0]
                previous_state, edge = parents[state]
                previous = previous_state[0]
                trip = vertex[1] or previous[1]
                if previous == start or vertex == finish:
                    trip = None
                step = {'kind': edge[0], 'duration_seconds': edge[1], 'from_stop': previous[0],
                        'to_stop': vertex[0], 'line': trip[0] if trip else ''}
                if trip:
                    step['route_relation_id'] = trip[1]
                if edge[0] == 'walk_transfer':
                    step['source_id'] = edge[2]
                elif edge[0] == 'rail_estimate' and len(edge) > 2:
                    step.update(edge[2])
                steps.append(step)
                state = previous_state
            return duration, list(reversed(steps))
        neighbours = head if vertex == start else graph.get(vertex, {})
        if used == objective and vertex in tail:
            neighbours = {**neighbours, finish: tail[vertex]}
        for target, edge in neighbours.items():
            mode = target[1][0].split(':', 1)[0] if isinstance(target[1], tuple) else None
            if mode in avoided:
                continue
            used_modes = used | 1 | required.get(mode, 0) if edge[0] == 'rail_estimate' else used
            target_state = (target, used_modes)
            candidate = duration + edge[1]
            if candidate < best.get(target_state, float('inf')):
                best[target_state], parents[target_state] = candidate, (state, edge)
                heappush(queue, (candidate, next(serial), target, used_modes))
    return None


def build_matrix(graph, poi_ids, approaches, *, avoid_transit=(), require_transit=()):
    pairs = []
    for origin in poi_ids:
        for target in poi_ids:
            if origin == target:
                continue
            entrance = approaches.get(origin, {}).get('enter', {})
            exit = approaches.get(target, {}).get('exit', {})
            route = route_between(graph, {int(stop): seconds for stop, seconds in entrance.items()},
                                  {int(stop): seconds for stop, seconds in exit.items()},
                                  avoid_transit=avoid_transit, require_transit=require_transit)
            pairs.append({'from_poi_id': origin, 'to_poi_id': target,
                          'status': 'known' if route is not None else 'unknown',
                          'duration_seconds': None if route is None else route[0], 'distance_meters': None,
                          'transport_steps': [] if route is None else route[1]})
    return {'schema_version': '0.2', 'mode': 'public_transit', 'basis': 'geometric_estimate',
            'source_id': 'osm_rail_topology_with_estimated_time', 'pairs': pairs}
