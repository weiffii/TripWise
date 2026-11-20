import json
from pathlib import Path
from unittest import TestCase

from ai_ds.moscow_transit import build_graph, route_between
from scripts.build_moscow_rail_snapshot import build_approach, build_snapshot, build_station_entrances


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
        self.assertEqual(result['coverage'], {'relations_by_mode': {'mcd': 1}, 'stops': 2,
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
        self.assertEqual(len(approaches), 3)
        for paired in approaches.values():
            self.assertEqual(set(paired), {'enter', 'exit'})
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
