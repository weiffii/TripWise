"""Готовит локальный снимок рельсовых остановок из выгрузки OSM."""

import argparse
from collections import Counter
import json
from math import ceil, isfinite
from pathlib import Path

from ai_ds.moscow_transit import _line, STOP_ROLES


def build_station_entrances(elements, route_stop_ids):
    """Связывает вход с платформой по членству в OSM stop_area."""
    nodes = {item['id']: item for item in elements if item['type'] == 'node'}
    by_stop = {}
    for area in elements:
        if area['type'] != 'relation' or area.get('tags', {}).get('public_transport') != 'stop_area':
            continue
        members = [member for member in area.get('members', []) if member['type'] == 'node']
        stop_ids = {member['ref'] for member in members if member.get('role') in STOP_ROLES}
        entrances = []
        for member in members:
            node = nodes.get(member['ref'], {})
            tags = node.get('tags', {})
            if tags.get('railway') not in ('subway_entrance', 'train_station_entrance'):
                continue
            if tags.get('access') in ('no', 'private') or not -90 <= node['lat'] <= 90 or not -180 <= node['lon'] <= 180:
                continue
            entrances.append({
                'latitude': node['lat'], 'longitude': node['lon'],
                'source_id': f'osm:node:{node["id"]}',
                'source_url': f'https://www.openstreetmap.org/node/{node["id"]}',
                'stop_area_url': f'https://www.openstreetmap.org/relation/{area["id"]}',
                'name': area['tags'].get('name'), 'ref': tags.get('ref'),
                'can_enter': tags.get('entrance') != 'exit',
                'can_exit': tags.get('entrance') != 'entrance',
            })
        if entrances:
            for key in stop_ids & route_stop_ids:
                by_stop.setdefault(str(key), []).extend(entrances)
    return {key: sorted(value, key=lambda row: row['source_id']) for key, value in sorted(by_stop.items())}


def build_approach(response, entrance, direction, source_url, internal_seconds):
    """Сохраняет измеримый уличный отрезок отдельно от оценки внутри станции."""
    if direction not in ('enter', 'exit') or not entrance[f'can_{direction}']:
        raise ValueError('У входа не подтверждено указанное направление прохода.')
    if response.get('code') != 'Ok' or len(response.get('routes', [])) != 1:
        raise ValueError('Ответ OSRM не содержит пешего маршрута.')
    waypoints = response.get('waypoints', [])
    if len(waypoints) != 2 or any(not 0 <= point['distance'] <= 35 for point in waypoints):
        raise ValueError('Концы не привязаны к пешеходной сети в пределах 35 м.')
    route = response['routes'][0]
    geometry = route['geometry']
    if geometry.get('type') != 'LineString' or len(geometry.get('coordinates', [])) < 2:
        raise ValueError('Ответ OSRM не содержит линии пешего маршрута.')
    if not all(isfinite(value) and value >= 0 for value in
               (route['duration'], route['distance'], internal_seconds)):
        raise ValueError('Недопустимая длительность или длина пешего маршрута.')
    seconds = ceil(route['duration'])
    return {
        'duration_seconds': seconds + internal_seconds,
        'external_duration_seconds': seconds,
        'internal_duration_seconds': internal_seconds,
        'geometry': geometry,
        'entrance_source_id': entrance['source_id'],
        'source_url': source_url,
        'distance_meters': round(route['distance']),
    }


def build_snapshot(document):
    if document.get('remark'):
        raise ValueError('Overpass вернул предупреждение: снимок может быть неполным.')
    timestamp = document.get('osm3s', {}).get('timestamp_osm_base')
    retrieved_at = document.get('retrieved_at')
    if not (timestamp or retrieved_at):
        raise ValueError('В исходной выгрузке отсутствует дата состояния или получения.')

    elements = document['elements']
    routes, stop_ids, mode_counts = [], set(), Counter()
    for element in elements:
        tags = element.get('tags', {})
        line = _line(tags) if element['type'] == 'relation' and tags.get('type') == 'route' else None
        if not line:
            continue
        members = [member for member in element['members']
                   if member['type'] == 'node' and member.get('role') in STOP_ROLES]
        if len({member['ref'] for member in members}) < 2:
            continue
        record = {key: element[key] for key in ('type', 'id', 'version', 'timestamp') if key in element}
        routes.append({**record, 'tags': tags, 'members': members})
        stop_ids.update(member['ref'] for member in members)
        mode_counts[line.split(':', 1)[0]] += 1

    nodes = [{key: node[key] for key in ('type', 'id', 'lat', 'lon', 'tags', 'version', 'timestamp') if key in node}
             for node in elements if node['type'] == 'node' and node['id'] in stop_ids]
    if not routes or not nodes:
        raise ValueError('В выгрузке нет пригодных остановок рельсовых маршрутов.')
    if len({route['id'] for route in routes}) != len(routes):
        raise ValueError('В выгрузке продублированы идентификаторы маршрутов.')
    if len({node['id'] for node in nodes}) != len(nodes):
        raise ValueError('В выгрузке продублированы идентификаторы остановок.')
    if any(type(node.get('lat')) not in (int, float) or type(node.get('lon')) not in (int, float)
           or not 54 <= node['lat'] <= 57 or not 35 <= node['lon'] <= 40 for node in nodes):
        raise ValueError('В выгрузке есть остановка вне ожидаемого региона.')

    station_entrances = build_station_entrances(elements, stop_ids)
    return {
        'source': 'https://www.openstreetmap.org/',
        'attribution': '© OpenStreetMap contributors',
        'license': 'https://opendatacommons.org/licenses/odbl/1-0/',
        **({'query': 'data/queries/overpass/moscow-rail.overpassql', 'timestamp_osm_base': timestamp} if timestamp else {}),
        **({'retrieved_at': retrieved_at, 'source_urls': document.get('source_urls', [])} if retrieved_at else {}),
        **({'station_entrances': station_entrances} if station_entrances else {}),
        'coverage': {'relations_by_mode': dict(sorted(mode_counts.items())),
                     'stops': len(nodes), 'missing_stop_ids': sorted(stop_ids - {node['id'] for node in nodes})},
        'elements': sorted(routes, key=lambda route: route['id']) + sorted(nodes, key=lambda node: node['id']),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='Исходный JSON Overpass или пакетная выгрузка OSM API')
    parser.add_argument('output', type=Path, help='Нормализованный JSON маршрутов и остановок')
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error('Укажите отдельный путь для нормализованного снимка.')
    snapshot = build_snapshot(json.loads(args.input.read_text(encoding='utf-8-sig')))
    args.output.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    print(json.dumps(snapshot['coverage'], ensure_ascii=False))


if __name__ == '__main__':
    main()
