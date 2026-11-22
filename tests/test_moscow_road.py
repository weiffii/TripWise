import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from ai_ds.moscow_catalog import load_moscow_v7
from ai_ds.moscow_road import PILOT_RAIL_CONFIG, rail_pairs, road_pair, travel_matrix
from ai_ds.moscow_transit import build_graph


class MoscowRoadTests(unittest.TestCase):
    def setUp(self):
        road_pair.cache_clear()
        self.pois = [
            {'id': 'a', 'coordinates': {'latitude': 55.752, 'longitude': 37.62}},
            {'id': 'b', 'coordinates': {'latitude': 55.7539, 'longitude': 37.624}},
        ]

    def test_no_segment_preserves_other_directions(self):
        no_segment = b'{"code":"NoSegment"}'
        geometry = {'type': 'LineString', 'coordinates': [[37.624, 55.7539], [37.62, 55.752]]}
        valid = {'code': 'Ok', 'routes': [{'duration': 610.2, 'distance': 811, 'geometry': geometry}],
                 'waypoints': [{'distance': 3}, {'distance': 5}]}
        missing = HTTPError('http://example.test', 400, 'NoSegment', None, io.BytesIO(no_segment))
        with (patch.dict('os.environ', {'MOSCOW_OSRM_FOOT': 'http://example.test'}),
              patch('ai_ds.moscow_road.urlopen', side_effect=[missing, io.BytesIO(json.dumps(valid).encode())])):
            first, reverse = travel_matrix(self.pois, 'walking')['pairs']
        self.assertEqual(first['status'], 'unknown')
        self.assertIsNone(first['duration_seconds'])
        self.assertEqual(reverse['basis'], 'routing_provider')
        self.assertEqual(reverse['duration_seconds'], 701)
        self.assertEqual(reverse['distance_meters'], 811)
        self.assertEqual(reverse['geometry'], geometry)

    def test_unreachable_is_not_replaced_with_straight_line(self):
        def no_route(url, *, timeout):
            raise HTTPError(url, 400, 'NoRoute', None, io.BytesIO(b'{"code":"NoRoute"}'))

        with (patch.dict('os.environ', {'MOSCOW_OSRM_CAR': 'http://example.test'}),
              patch('ai_ds.moscow_road.urlopen', side_effect=no_route)):
            pairs = travel_matrix(self.pois, 'car')['pairs']
        self.assertEqual([pair['status'] for pair in pairs], ['unreachable', 'unreachable'])

    def test_provider_outage_marks_estimated_times(self):
        with (patch.dict('os.environ', {'MOSCOW_OSRM_FOOT': 'http://example.test'}),
              patch('ai_ds.moscow_road.urlopen', side_effect=TimeoutError) as request):
            pairs = travel_matrix(self.pois, 'walking')['pairs']
        self.assertEqual(request.call_count, 1)
        self.assertTrue(all(pair['basis'] == 'geometric_estimate' and pair['distance_meters'] is None
                            and 'geometry' not in pair for pair in pairs))

    def test_default_local_provider_recovers_without_waiting_for_cache(self):
        geometry = {'type': 'LineString', 'coordinates': [[37.62, 55.752], [37.624, 55.7539]]}
        valid = json.dumps({'code': 'Ok', 'routes': [{'duration': 400, 'distance': 500, 'geometry': geometry}],
                            'waypoints': [{'distance': 3}, {'distance': 5}]}).encode()
        with (patch.dict('os.environ', {}, clear=True),
              patch('ai_ds.moscow_road.monotonic', return_value=100),
              patch('ai_ds.moscow_road.urlopen', side_effect=[
                  TimeoutError, io.BytesIO(valid), io.BytesIO(valid)]) as request):
            offline = travel_matrix(self.pois, 'walking')
            restored = travel_matrix(self.pois, 'walking')
        self.assertEqual(offline['basis'], 'geometric_estimate')
        self.assertEqual(restored['basis'], 'routing_provider')
        self.assertTrue(request.call_args_list[1].args[0].startswith('http://127.0.0.1:5001/route/'))

    def test_rail_access_obeys_entrance_directions_and_preserves_geometry(self):
        nodes = {1: {'type': 'node', 'id': 1, 'lat': 55.752, 'lon': 37.621},
                 2: {'type': 'node', 'id': 2, 'lat': 55.7539, 'lon': 37.623}}
        route = {'type': 'relation', 'id': 12, 'tags': {'route': 'subway', 'ref': '1'},
                 'members': [{'type': 'node', 'ref': stop, 'role': 'stop'} for stop in nodes]}
        graph = build_graph({'elements': [*nodes.values(), route]},
                            {'metro': {'meters_per_second': 10, 'wait_seconds': 90}})
        enter_point = (55.752, 37.6205)
        exit_point = (55.7539, 37.6235)
        entrances = {
            '1': [{'latitude': enter_point[0], 'longitude': enter_point[1], 'source_id': 'fixture-enter',
                   'can_enter': True, 'can_exit': False}],
            '2': [{'latitude': exit_point[0], 'longitude': exit_point[1], 'source_id': 'fixture-exit',
                   'can_enter': False, 'can_exit': True}],
        }

        def mock_road(endpoint, start, finish, window, radii):
            self.assertNotEqual(start, enter_point)
            self.assertNotEqual(finish, exit_point)
            self.assertEqual(radii, (180, 40) if finish == enter_point else (40, 180))
            return {'status': 'known', 'duration_seconds': 100, 'distance_meters': 45,
                    'geometry': {'type': 'LineString', 'coordinates': [list(start[::-1]), list(finish[::-1])]}}

        with (patch.dict('os.environ', {'MOSCOW_RAIL_CONFIG': 'fixture'}),
              patch('ai_ds.moscow_road.rail_inputs', return_value=(graph, {}, nodes, entrances)),
              patch('ai_ds.moscow_road.road_pair', side_effect=mock_road)):
            result = rail_pairs(self.pois, 'http://example.test')
        self.assertIn(('a', 'b'), result)
        access, *_, egress = result['a', 'b']['transport_steps']
        self.assertEqual(access['entrance_source_id'], 'fixture-enter')
        self.assertEqual(egress['entrance_source_id'], 'fixture-exit')
        self.assertEqual(access['geometry']['coordinates'][-1], list(enter_point[::-1]))
        self.assertEqual(egress['geometry']['coordinates'][0], list(exit_point[::-1]))
        self.assertEqual(access['duration_seconds'], 220)
        self.assertEqual(access['internal_duration_seconds'], 120)

    def test_bundled_rail_access_and_mcd_transfer_without_road_server(self):
        source = Path(__file__).resolve().parents[1] / 'data/sources'
        origin, destination = 'moscow-osm-node-5076035172', 'moscow-osm-relation-9464465'
        pois, _ = load_moscow_v7(poi_ids=[origin, destination])
        config = {
            'osm_snapshot': str(source / 'moscow-rail-osm.json'),
            'transfers': str(source / 'moscow-rail-transfer-pilot.json'),
            'station_entrances': str(source / 'moscow-rail-entrances.json'),
            'approaches': str(source / 'moscow-rail-approaches-pilot.json'),
            'estimates': {mode: {'meters_per_second': 11, 'wait_seconds': 180} for mode in ('metro', 'mcc', 'mcd')},
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'rail.json'
            path.write_text(json.dumps(config), encoding='utf-8')
            with (patch.dict('os.environ', {'MOSCOW_RAIL_CONFIG': str(path), 'MOSCOW_OSRM_FOOT': ''}),
                  patch('ai_ds.moscow_road.urlopen', side_effect=AssertionError('unexpected network request'))):
                matrix = travel_matrix(pois, 'public_transit')
        row = next(row for row in matrix['pairs'] if row['from_poi_id'] == origin)
        steps = row['transport_steps']
        self.assertEqual(row['basis'], 'geometric_estimate')
        self.assertEqual(steps[0]['kind'], 'walk_access')
        self.assertEqual(steps[-1]['kind'], 'walk_egress')
        self.assertEqual(steps[0]['geometry']['type'], 'LineString')
        self.assertEqual(steps[-1]['geometry']['type'], 'LineString')
        self.assertIn('mcd:2', {step['line'] for step in steps if step['kind'] == 'rail_estimate'})
        self.assertTrue(any(step['kind'] == 'walk_transfer' and step['source_id'] for step in steps))

    def test_required_transit_is_kept_as_an_alternative_to_faster_walking(self):
        origin, destination = 'moscow-osm-node-5076035172', 'moscow-osm-relation-9464465'
        pois, _ = load_moscow_v7(poi_ids=[origin, destination])
        walking = {'status': 'known', 'basis': 'routing_provider', 'source_id': 'test-road',
                   'duration_seconds': 100, 'distance_meters': 85,
                   'geometry': {'type': 'LineString', 'coordinates': [[37.62, 55.75], [37.625, 55.751]]}}
        with (patch.dict('os.environ', {'MOSCOW_OSRM_FOOT': 'http://example.test',
                                       'MOSCOW_RAIL_CONFIG': str(PILOT_RAIL_CONFIG)}),
              patch('ai_ds.moscow_road.road_pair', return_value=walking),
              patch('ai_ds.moscow_road.routed_station_access', return_value={})):
            matrix = travel_matrix(pois, 'public_transit', require_transit=('mcd',))
            excluded = travel_matrix(pois, 'public_transit', avoid_transit=('metro',), require_transit=('mcd',))
        row = next(row for row in matrix['pairs'] if row['from_poi_id'] == origin)
        self.assertEqual(row['duration_seconds'], 100)
        self.assertEqual(row['geometry'], walking['geometry'])
        transit = row['required_transit_alternative']
        self.assertGreater(transit['duration_seconds'], row['duration_seconds'])
        self.assertIn('mcd:2', {step['line'] for step in transit['transport_steps']})
        row = next(row for row in excluded['pairs'] if row['from_poi_id'] == origin)
        self.assertNotIn('required_transit_alternative', row)
