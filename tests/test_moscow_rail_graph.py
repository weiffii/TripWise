import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ai_ds.moscow_road import travel_matrix
from ai_ds.moscow_transit import build_graph, route_between
from scripts.build_moscow_rail_snapshot import build_approach, build_snapshot, build_station_entrances, route_way_segments


SOURCES = Path(__file__).resolve().parents[1] / 'data/sources'


def node(identifier):
    return {'id': identifier, 'type': 'node', 'lat': 55.5, 'lon': 37.5 + identifier / 100}


def relation(identifier, ref, stops, route='subway'):
    return {'id': identifier, 'type': 'relation',
            'tags': {'type': 'route', 'route': route, 'ref': ref},
            'members': [{'type': 'node', 'ref': stop, 'role': 'stop'} for stop in stops]}


class MoscowRailGraphTests(TestCase):
    def test_real_metro_and_d2_topology_with_documented_transfers(self):
        snapshot = json.loads((SOURCES / 'moscow-rail-osm.json').read_text(encoding='utf8'))
        transfer_data = json.loads((SOURCES / 'moscow-rail-transfer-pilot.json').read_text(encoding='utf8'))
        self.assertEqual(snapshot['coverage']['missing_stop_ids'], [])
        graph = build_graph(snapshot, {
            'metro': {'meters_per_second': 10, 'wait_seconds': 240},
            'mcd': {'meters_per_second': 15, 'wait_seconds': 480},
            'mcc': {'meters_per_second': 13, 'wait_seconds': 360},
        }, transfer_data['transfers'])
        # Топология и наличие пересадок подтверждены источниками. Время подхода в тесте условное.
        for destination, lines in ((297148175, ['metro:1', 'metro:5']),
                                   (8815720351, ['metro:1', 'metro:5', 'mcd:2'])):
            seconds, steps = route_between(graph, {6938823552: 300}, {destination: 300})
            self.assertEqual([step['line'] for step in steps if step['kind'] == 'wait_estimate'], lines)
            self.assertEqual(seconds, sum(step['duration_seconds'] for step in steps))
            transfers = [step for step in steps if step['kind'] == 'walk_transfer']
            self.assertEqual(len(transfers), len(lines) - 1)
            self.assertTrue(all(step['source_id'].startswith('https://transport.mos.ru/') for step in transfers))

    def test_mcc_directions_and_documented_transfer_from_metro(self):
        snapshot = json.loads((SOURCES / 'moscow-rail-osm.json').read_text(encoding='utf8'))
        transfers = json.loads((SOURCES / 'moscow-rail-transfer-pilot.json').read_text(encoding='utf8'))['transfers']
        graph = build_graph(snapshot, {
            'metro': {'meters_per_second': 11, 'wait_seconds': 180},
            'mcd': {'meters_per_second': 15, 'wait_seconds': 480},
            'mcc': {'meters_per_second': 13, 'wait_seconds': 360},
        }, transfers)
        by_id = {item['id']: item for item in snapshot['elements'] if item['type'] == 'relation'}
        for relation_id in (6548266, 6548267):
            stops = [member['ref'] for member in by_id[relation_id]['members']]
            self.assertEqual(len(set(stops)), 31)
            self.assertEqual(stops[0], stops[-1])
            _, steps = route_between(graph, {stops[0]: 0}, {stops[1]: 0})
            rides = [step for step in steps if step['kind'] == 'rail_estimate']
            self.assertEqual([step['route_relation_id'] for step in rides], [relation_id])
            self.assertTrue(rides[0]['geometry_verified'])
            self.assertGreater(rides[0]['distance_meters'], 0)

        _, steps = route_between(graph, {6938823552: 300}, {253745256: 300})
        self.assertEqual([step['line'] for step in steps if step['kind'] == 'wait_estimate'], ['metro:1', 'mcc:14'])
        transfer = next(step for step in steps if step['kind'] == 'walk_transfer')
        self.assertEqual(transfer['from_stop'], 6938823563)
        self.assertEqual(transfer['to_stop'], 4379061182)
        self.assertIsNotNone(route_between(graph, {6938823552: 300}, {253745256: 300},
                                            require_transit=('metro', 'mcc')))
        self.assertIsNone(route_between(graph, {6938823552: 300}, {253745256: 300}, avoid_transit=('metro',)))
        self.assertIsNone(route_between(graph, {6938823552: 300}, {253745256: 300},
                                         require_transit=('metro', 'mcc'), avoid_transit=('mcc',)))

    def test_short_turn_requires_reboarding_even_on_same_line(self):
        elements = [node(i) for i in (1, 2, 3)]
        elements += [relation(100, '4', (1, 2)), relation(101, '4', (2, 3))]
        graph = build_graph({'elements': elements}, {'metro': {'meters_per_second': 12, 'wait_seconds': 90}})
        _, steps = route_between(graph, {1: 0}, {3: 0})
        self.assertEqual([step['kind'] for step in steps].count('wait_estimate'), 2)
        self.assertIsNone(route_between(graph, {3: 0}, {1: 0}))

    def test_transfer_only_path_does_not_hide_real_ride(self):
        elements = [node(i) for i in (1, 2)]
        elements.append(relation(100, '1', (1, 2)))
        graph = build_graph({'elements': elements}, {'metro': {'meters_per_second': 12, 'wait_seconds': 90}},
                            [{'from_stop': 1, 'to_stop': 2, 'duration_seconds': 60, 'source_id': 'fixture'}])
        _, steps = route_between(graph, {1: 10}, {2: 10})
        self.assertIn('rail_estimate', [step['kind'] for step in steps])

    def test_snapshot_preserves_directions_and_reports_missing_stops(self):
        elements = [node(i) for i in (1, 2, 8)]
        elements += [relation(100, 'D4A', (1, 2, 3), route='train'),
                     relation(101, '14', (2, 8), route='bus')]
        raw = {'elements': elements, 'osm3s': {'timestamp_osm_base': '2026-11-20T00:00:00Z'}}
        result = build_snapshot(raw)
        self.assertEqual(result['coverage'], {'relations_by_mode': {'bus': 1, 'mcd': 1}, 'stops': 3,
                                               'missing_stop_ids': [3]})
        self.assertEqual([m['ref'] for m in result['elements'][0]['members']], [1, 2, 3])
        with self.assertRaises(ValueError):
            build_snapshot({**raw, 'remark': 'timeout'})

    def test_entrance_requires_stop_area_membership_and_preserves_direction(self):
        entrance = {**node(9), 'tags': {'railway': 'subway_entrance', 'entrance': 'exit'}}
        unlinked = {**node(8), 'tags': {'railway': 'subway_entrance'}}
        closed = {**node(10), 'tags': {'railway': 'subway_entrance', 'access': 'private'}}
        area = {'type': 'relation', 'id': 42, 'tags': {'public_transport': 'stop_area'}, 'members': [
            {'ref': 1, 'type': 'node', 'role': 'stop'},
            {'ref': 9, 'type': 'node', 'role': ''},
            {'ref': 10, 'type': 'node', 'role': ''},
        ]}
        result = build_station_entrances([entrance, unlinked, closed, area], {1, 2})
        self.assertEqual(list(result), ['1'])
        self.assertEqual(len(result['1']), 1)
        row = result['1'][0]
        self.assertEqual(row['source_id'], 'osm:node:9')
        self.assertEqual(row['stop_area_url'], 'https://www.openstreetmap.org/relation/42')
        self.assertTrue(row['can_exit'])
        self.assertFalse(row['can_enter'])

    def test_pilot_approaches_use_allowed_entrance_and_preserve_time_breakdown(self):
        stops = json.loads((SOURCES / 'moscow-rail-entrances.json').read_text(encoding='utf8'))['station_entrances']
        approaches = json.loads((SOURCES / 'moscow-rail-approaches-pilot.json').read_text(encoding='utf8'))['approaches']
        self.assertIn('moscow-cultural-188', approaches)
        for paired in approaches.values():
            self.assertTrue(paired.keys() and paired.keys() <= {'enter', 'exit'})
            for direction, details in paired.items():
                for stop_id, row in details.items():
                    entrance = next(item for item in stops[stop_id] if item['source_id'] == row['entrance_source_id'])
                    self.assertTrue(entrance[f'can_{direction}'])
                    self.assertEqual(row['duration_seconds'],
                                     row['external_duration_seconds'] + row['internal_duration_seconds'])
                    self.assertEqual(row['geometry']['type'], 'LineString')
                    self.assertGreater(row['distance_meters'], 0)
        with self.assertRaises(ValueError):
            build_approach({}, {'can_enter': False}, 'enter', 'fixture', 120)

    def test_bundled_tram_graph_uses_osrm_snapshots_in_each_direction(self):
        points = [
            {'id': 'moscow-osm-node-3001979916', 'coordinates': {'latitude': 55.883361, 'longitude': 37.643869}},
            {'id': 'moscow-cultural-p161', 'coordinates': {'latitude': 55.8235, 'longitude': 37.61818}},
        ]
        environment = {'MOSCOW_RAIL_CONFIG': str(SOURCES / 'moscow-rail-config.json'), 'MOSCOW_OSRM_FOOT': ''}
        with patch.dict('os.environ', environment):
            matrix = travel_matrix(points, 'public_transit')
        self.assertEqual(len(matrix['pairs']), 2)
        for pair, route_id in zip(matrix['pairs'], (540139, 540033)):
            self.assertEqual(pair['basis'], 'geometric_estimate')
            steps = pair['transport_steps']
            self.assertEqual([leg['line'] for leg in steps if leg['kind'] == 'wait_estimate'], ['tram:17'])
            rides = [leg for leg in steps if leg['kind'] == 'rail_estimate']
            self.assertEqual({leg['route_relation_id'] for leg in rides}, {route_id})
            self.assertTrue(all(leg['geometry_verified'] for leg in rides))
            for step in (steps[0], steps[-1]):
                self.assertEqual(step['geometry']['type'], 'LineString')
                self.assertEqual(step['internal_duration_seconds'], 0)

    def test_bundled_bus_graph_uses_directed_platforms_and_exact_osm_ways(self):
        points = [
            {'id': 'moscow-osm-node-3235951146', 'coordinates': {'latitude': 55.7071471, 'longitude': 37.4008772}},
            {'id': 'moscow-cultural-c33', 'coordinates': {'latitude': 55.74448, 'longitude': 37.56543}},
        ]
        environment = {'MOSCOW_RAIL_CONFIG': str(SOURCES / 'moscow-rail-config.json'), 'MOSCOW_OSRM_FOOT': ''}
        with patch.dict('os.environ', environment):
            matrix = travel_matrix(points, 'public_transit')
        for pair, route_id in zip(matrix['pairs'], (3253708, 3255156)):
            steps = pair['transport_steps']
            self.assertEqual([leg['line'] for leg in steps if leg['kind'] == 'wait_estimate'], ['bus:М20'])
            rides = [leg for leg in steps if leg['kind'] == 'rail_estimate']
            self.assertEqual(len(rides), 25)
            self.assertTrue(all(leg['geometry_verified'] and leg['route_relation_id'] == route_id for leg in rides))
            self.assertEqual(steps[0]['geometry']['type'], 'LineString')
            self.assertEqual(steps[-1]['geometry']['type'], 'LineString')

    def test_bundled_electric_bus_uses_documented_outer_ring(self):
        points = [
            {'id': 'moscow-osm-node-7658470896', 'coordinates': {'latitude': 55.7316779, 'longitude': 37.6379929}},
            {'id': 'moscow-osm-node-10031344142', 'coordinates': {'latitude': 55.773518, 'longitude': 37.6080415}},
        ]
        environment = {'MOSCOW_RAIL_CONFIG': str(SOURCES / 'moscow-rail-config.json'), 'MOSCOW_OSRM_FOOT': ''}
        with patch.dict('os.environ', environment):
            matrix = travel_matrix(points, 'public_transit', require_transit=('electric_bus',))
        steps = matrix['pairs'][0]['transport_steps']
        self.assertEqual([leg['line'] for leg in steps if leg['kind'] == 'wait_estimate'], ['electric_bus:Б'])
        rides = [leg for leg in steps if leg['kind'] == 'rail_estimate']
        self.assertEqual(len(rides), 21)
        self.assertTrue(all(leg['geometry_verified'] and leg['route_relation_id'] == 1343003 for leg in rides))
        self.assertEqual(steps[0]['geometry']['type'], 'LineString')
        self.assertEqual(steps[-1]['geometry']['type'], 'LineString')

    def test_way_geometry_requires_exact_stop_and_known_chain(self):
        route = relation(100, '1', (1, 3))
        stop_members = route['members'].copy()
        route['members'].extend([{'type': 'way', 'ref': 5, 'role': ''},
                                 {'type': 'way', 'ref': 6, 'role': ''}])
        points = {i: node(i) for i in range(1, 5)}
        legs = route_way_segments(route, stop_members, {5: {'nodes': [2, 1]}, 6: {'nodes': [2, 3]}}, points)
        self.assertEqual(legs[0]['geometry']['coordinates'],
                         [[points[i]['lon'], points[i]['lat']] for i in (1, 2, 3)])
        self.assertEqual(route_way_segments(route, stop_members, {5: {'nodes': [2, 1]}, 6: {'nodes': [4, 3]}}, points), [])
        legs[0]['geometry']['coordinates'][-1] = [0, 0]
        graph = build_graph({'elements': [*points.values(), {**route, 'segments': legs}]},
                            {'metro': {'meters_per_second': 10, 'wait_seconds': 100}})
        _, steps = route_between(graph, {1: 0}, {3: 0})
        self.assertNotIn('geometry', next(row for row in steps if row['kind'] == 'rail_estimate'))
