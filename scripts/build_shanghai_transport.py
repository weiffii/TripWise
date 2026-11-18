"""Офлайн: реальные факты метро и 30 явно синтетических автобусных маршрутов."""
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data/sources/shanghai-story-map/metro.raw.json'
POIS = ROOT / 'data/demo/shanghai/pois.json'
OUTPUT = ROOT / 'data/demo/shanghai/transport.json'
SOURCE_URL = 'https://shanghaistorymap.com/api/v1/metro.json'


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def distance(a, b):
    lat = math.radians((a['lat'] + b['lat']) / 2)
    return 111320 * math.hypot(a['lat'] - b['lat'],
                              (a['lon'] - b['lon']) * math.cos(lat))


def normalize_metro(raw):
    stations = []
    for row in raw['stations']:
        stations.append({'id': row['id'], 'name_zh': row['zh'], 'name_en': row['en'],
                         'lat': row['lat'], 'lon': row['lng'], 'lines': list(row['lines'])})
    lines = []
    for row in raw['items']:
        lines.append({'id': row['id'], 'name': row['en'], 'name_zh': row['zh'],
                      'color': row['color'], 'kind': row['kind'],
                      'branches': [{'station_ids': list(branch['stations']),
                                    'loop': bool(row.get('loop', False))}
                                   for branch in row['branches']]})
    transfers = []
    for row in raw.get('transfers', []):
        blocked = bool(row.get('blocked', False))
        transfers.append({'station_id': row['station'], 'line_ids': list(row['lines']),
                          'blocked': blocked, 'estimated_minutes': None if blocked else 5,
                          'time_basis': 'synthetic_planning_assumption'})
    return {'stations': stations, 'lines': lines, 'transfers': transfers,
            'source_url': SOURCE_URL, 'source_date': raw.get('as_of'),
            'provenance': {
                'source_generated_at': raw.get('generated'), 'raw_sha256': sha(RAW),
                'identity_and_topology': 'real_network_facts_from_third_party_compilation',
                'coordinates': 'source_WGS84_station_centres_not_exits',
                'line_geometry': 'connections_between_station_centres_not_track_geometry',
                'transfer_minutes': 'our_synthetic_constant_5_not_source_measurements',
                'licence': 'Published station-list CC0; broader feed terms not established; factual subset only',
                'attribution': 'Shanghai Metro station/network facts: Shanghai Story Map, https://shanghaistorymap.com/data/',
                'official_operator_independently_reached': False,
                'copied_source_segment_minutes_or_fares': False,
                'live_service_or_exit_information': False}}


def make_buses(dataset):
    pois = sorted(dataset['pois'], key=lambda row: row['id'])
    stops = []
    for index, poi in enumerate(pois):
        # Смещение учебной точки не претендует на реальный тротуар или остановку.
        stops.append({'id': f'DEMO-STOP-{index + 1:03d}',
                      'name': f'Учебная остановка у «{poi["name"]}»',
                      'lat': round(poi['lat'] + ((index % 3) - 1) * .00008, 6),
                      'lon': round(poi['lon'] + ((index % 5) - 2) * .00004, 6),
                      'river_bank': poi['river_bank'], 'near_poi_id': poi['id'],
                      'synthetic': True, 'coordinate_basis': 'synthetic_near_demo_POI'})
    clusters = [
        ('Народная площадь и центр', 'west', 31.232, 121.475),
        ('Цзинъань и западный центр', 'west', 31.230, 121.445),
        ('Сюйхуэй и Французская концессия', 'west', 31.198, 121.444),
        ('Хунцяо и Чаннин', 'west', 31.208, 121.367),
        ('Луцзяцзуй', 'east', 31.238, 121.512),
        ('Сэнчури-парк и Чжанцзян', 'east', 31.210, 121.566),
    ]
    colors = ['#3366CC', '#109618', '#994499', '#DC3912', '#0099C6', '#DD7711']
    lengths = [8, 9, 10, 12, 14]
    routes = []
    for cluster_index, (label, bank, lat, lon) in enumerate(clusters):
        centre = {'lat': lat, 'lon': lon}
        pool = sorted((s for s in stops if s['river_bank'] == bank),
                      key=lambda s: (distance(centre, s), s['id']))[:18]
        if len(pool) < max(lengths):
            raise ValueError('Not enough same-bank demonstration stops')
        for variant, length in enumerate(lengths):
            selected = [pool[variant % len(pool)]]
            remaining = [s for s in pool if s['id'] != selected[0]['id']]
            while len(selected) < length:
                next_stop = min(remaining, key=lambda s: (distance(selected[-1], s), s['id']))
                selected.append(next_stop)
                remaining.remove(next_stop)
            number = len(routes) + 1
            # Разреженный каталог POI не означает автобусный перегон в 9 км.
            # Промежуточные точки полностью вымышлены и не обозначают улицу.
            dense = [selected[0]]
            for anchor_index, (a, b) in enumerate(zip(selected, selected[1:])):
                parts = max(1, math.ceil(distance(a, b) / 1000))
                for part in range(1, parts):
                    fraction = part / parts
                    stop = {'id': f'DEMO-STOP-B{number:02d}-{anchor_index:02d}-{part:02d}',
                            'name': f'Учебная промежуточная остановка {number:02d}-{anchor_index:02d}-{part:02d}',
                            'lat': round(a['lat'] + (b['lat'] - a['lat']) * fraction, 6),
                            'lon': round(a['lon'] + (b['lon'] - a['lon']) * fraction, 6),
                            'river_bank': bank, 'synthetic': True,
                            'coordinate_basis': 'synthetic_interpolation_not_a_street_or_real_stop',
                            'anchor_stop_ids': [a['id'], b['id']]}
                    stops.append(stop)
                    dense.append(stop)
                dense.append(b)
                if len(dense) >= length:
                    break
            selected = dense[:length]
            routes.append({'id': f'DEMO-BUS-{number:02d}',
                           'name': f'Учебный автобус {number:02d} · {label}',
                           'stop_ids': [s['id'] for s in selected],
                           'color': colors[cluster_index], 'headway_minutes': [10, 12, 15, 18, 20][variant],
                           'fare_cny': 2, 'speed_kmh': 18, 'service_start': '06:00',
                           'service_end': '22:00', 'bidirectional': True,
                           'synthetic': True, 'river_bank': bank,
                           'synthetic_crossing_assumption': False,
                           'geometry_basis': 'schematic_stop_connections_not_street_route'})
    return {'synthetic': True, 'stops': stops, 'routes': routes,
            'provenance': {'kind': 'fully_synthetic_demo_bus_network',
                           'source_poi_file': POIS.relative_to(ROOT).as_posix(),
                           'source_poi_sha256': sha(POIS), 'generation_method': 'six_same_bank_geoclusters_five_nearest_neighbour_variants_max1km_gap_v1',
                           'route_numbers': 'invented_DEMO_ids_not_operating_bus_numbers',
                           'stops': 'invented_near_demonstration_POIs_not_real_bus_stops',
                           'service_headways_fares_speeds': 'synthetic_planning_assumptions',
                           'river_bank': 'inherited_demo_POI_label_not_independent_geographic_verification',
                           'street_geometry_verified': False}}


def validate(data):
    metro = data['metro']
    stations = {s['id']: s for s in metro['stations']}
    lines = {line['id']: line for line in metro['lines']}
    assert len(stations) == len(metro['stations'])
    assert len(lines) == len(metro['lines'])
    assert data['crs'] == 'EPSG:4326'
    for station in stations.values():
        assert 29 < station['lat'] < 33 and 119 < station['lon'] < 123
        assert station['lines'] and set(station['lines']) <= set(lines)
    segment_count = 0
    for line in lines.values():
        assert line['branches']
        for branch in line['branches']:
            assert len(branch['station_ids']) >= 2
            assert set(branch['station_ids']) <= set(stations)
            assert all(line['id'] in stations[s]['lines'] for s in branch['station_ids'])
            segment_count += len(branch['station_ids']) - 1
    for transfer in metro['transfers']:
        assert transfer['station_id'] in stations
        assert len(transfer['line_ids']) == 2 and len(set(transfer['line_ids'])) == 2
        assert set(transfer['line_ids']) <= set(stations[transfer['station_id']]['lines'])
        assert transfer['estimated_minutes'] == (None if transfer['blocked'] else 5)
    buses = data['buses']
    stops = {s['id']: s for s in buses['stops']}
    assert len(stops) == len(buses['stops'])
    assert len(buses['routes']) == 30
    assert {r['id'] for r in buses['routes']} == {f'DEMO-BUS-{n:02d}' for n in range(1, 31)}
    paths = set()
    for route in buses['routes']:
        assert route['synthetic'] is True
        assert 8 <= len(route['stop_ids']) <= 14
        assert len(set(route['stop_ids'])) == len(route['stop_ids'])
        assert set(route['stop_ids']) <= set(stops)
        assert {stops[s]['river_bank'] for s in route['stop_ids']} == {route['river_bank']}
        assert all(distance(stops[a], stops[b]) <= 1001 for a, b in
                   zip(route['stop_ids'], route['stop_ids'][1:]))
        path = min(tuple(route['stop_ids']), tuple(reversed(route['stop_ids'])))
        assert path not in paths
        paths.add(path)
        assert 10 <= route['headway_minutes'] <= 20
    return {'station_count': len(stations), 'line_count': len(lines),
            'branch_segment_count': segment_count, 'transfer_override_count': len(metro['transfers']),
            'bus_stop_count': len(stops), 'synthetic_bus_route_count': 30,
            'critical_errors': 0, 'network_requests': 0}


def main():
    before = sha(RAW), sha(POIS)
    data = {'schema_version': 'shanghai-transport-v1', 'crs': 'EPSG:4326',
            'metro': normalize_metro(load(RAW)), 'buses': make_buses(load(POIS))}
    result = validate(data)
    assert before == (sha(RAW), sha(POIS))
    data['validation'] = result
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
