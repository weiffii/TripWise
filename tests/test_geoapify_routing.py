"""Искусственные ответы Geoapify Routing; внешние запросы не выполняются."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from ai_ds.data import load_pois
from ai_ds.sources.geoapify import GeoapifyAccessError
from ai_ds.sources.geoapify_routing import collect_matrix, normalize_pair

ROOT = Path(__file__).resolve().parents[1]


def response(duration=120, distance=100):
    return {'type': 'FeatureCollection', 'features': [{'type': 'Feature',
        'properties': {'mode': 'walk', 'distance_units': 'Meters', 'time': duration, 'distance': distance},
        'geometry': {'type': 'MultiLineString', 'coordinates': [[[37.6, 55.7], [37.61, 55.71]]]}}]}


class GeoapifyRoutingTests(unittest.TestCase):
    def setUp(self):
        self.pois = load_pois(ROOT / 'data/processed/pois.synthetic.validated.json')[:3]

    def test_pair_geometry_units_direction_and_no_mutation(self):
        raw = response()
        before = deepcopy(raw)
        pair = normalize_pair(raw, 'a', 'b')
        self.assertEqual(raw, before)
        self.assertEqual(pair['from_poi_id'], 'a')
        self.assertEqual(pair['to_poi_id'], 'b')
        self.assertEqual(pair['duration_seconds'], 120)
        self.assertEqual(pair['distance_meters'], 100)
        self.assertEqual(pair['geometry']['type'], 'LineString')

    def test_disconnected_geometry_is_not_bridged(self):
        raw = response()
        raw['features'][0]['geometry']['coordinates'].append([[38, 56], [39, 57]])
        with self.assertRaises(ValueError):
            normalize_pair(raw, 'a', 'b')

    def test_invalid_route_data_rejected(self):
        for field, value in [('mode', 'drive'), ('distance_units', 'Miles'), ('time', True),
                             ('time', float('nan')), ('time', -1), ('distance', None)]:
            raw = response(); raw['features'][0]['properties'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                normalize_pair(raw, 'a', 'b')
        raw = response(); raw['features'][0]['geometry']['coordinates'][0][1][0] = 181
        with self.assertRaises(ValueError):
            normalize_pair(raw, 'a', 'b')
        with self.assertRaises(ValueError):
            normalize_pair({'type': 'FeatureCollection', 'features': []}, 'a', 'b')

    def test_full_directed_matrix_budget_raw_and_key_not_persisted(self):
        calls = []
        def opener(request, timeout):
            calls.append(parse_qs(urlparse(request.full_url).query))
            return io.BytesIO(json.dumps(response(duration=len(calls))).encode())
        before = deepcopy(self.pois)
        with tempfile.TemporaryDirectory() as tmp:
            path, matrix = collect_matrix(self.pois, key='mock-secret-key', root=tmp, opener=opener, test_mode=True)
            self.assertEqual(len(calls), 6)
            self.assertEqual(len(matrix['pairs']), 6)
            self.assertEqual(matrix['basis'], 'test_matrix')
            self.assertEqual(len({(p['from_poi_id'], p['to_poi_id']) for p in matrix['pairs']}), 6)
            self.assertEqual(len({p['duration_seconds'] for p in matrix['pairs']}), 6)
            metadata = json.loads((path / 'metadata.json').read_text(encoding='utf-8'))
            self.assertTrue(metadata['coverage_complete'])
            self.assertEqual(metadata['http_attempt_count'], 6)
            for file in path.iterdir():
                self.assertNotIn('mock-secret-key', file.read_text(encoding='utf-8'))
        self.assertEqual(self.pois, before)
        self.assertTrue(all(q['mode'] == ['walk'] and q['units'] == ['metric'] for q in calls))

    def test_budget_or_bad_input_rejected_before_network(self):
        calls = []
        def opener(*args, **kwargs):
            calls.append(1)
            raise AssertionError('Unexpected network')
        for limit in [True, 0, 7, 5]:
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                collect_matrix(self.pois, key='mock-secret-key', max_requests=limit, opener=opener)
        for pois in [self.pois[:1], self.pois + self.pois[:1]]:
            with self.assertRaises(ValueError):
                collect_matrix(pois, key='mock-secret-key', opener=opener)
        with self.assertRaises(ValueError):
            collect_matrix(self.pois, key='', opener=opener)
        with self.assertRaises(ValueError):
            collect_matrix(self.pois, key='mock-secret-key', opener=opener, test_mode='false')
        self.assertEqual(calls, [])

    def test_partial_network_failure_stops_without_retry(self):
        calls = []
        def opener(*args, **kwargs):
            calls.append(1)
            if len(calls) == 3:
                raise URLError('mock-secret-key')
            return io.BytesIO(json.dumps(response()).encode())
        with tempfile.TemporaryDirectory() as tmp:
            path, matrix = collect_matrix(self.pois, key='mock-secret-key', root=tmp, opener=opener, test_mode=True)
            metadata = json.loads((path / 'metadata.json').read_text(encoding='utf-8'))
            self.assertEqual(len(calls), 3)
            self.assertEqual(len(matrix['pairs']), 2)
            self.assertFalse(metadata['coverage_complete'])
            self.assertEqual(metadata['stop_reason'], 'request_failed')
            self.assertNotIn('mock-secret-key', json.dumps(metadata))

    def test_access_denial_stops_closes_and_retains_partial(self):
        for status in [402, 403, 429]:
            calls, stream = [], io.BytesIO(b'mock-secret-key')
            def opener(*args, **kwargs):
                calls.append(1)
                if len(calls) > 1:
                    raise HTTPError('https://example.test/mock-secret-key', status, 'mock-secret-key', {}, stream)
                return io.BytesIO(json.dumps(response()).encode())
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(GeoapifyAccessError) as caught:
                    collect_matrix(self.pois, key='mock-secret-key', root=tmp, opener=opener, test_mode=True)
                self.assertEqual(len(calls), 2)
                self.assertTrue(stream.closed)
                self.assertTrue(Path(caught.exception.snapshot).exists())
                self.assertNotIn('mock-secret-key', str(caught.exception))

    def test_first_failure_creates_no_snapshot(self):
        def opener(*args, **kwargs):
            raise URLError('mock-secret-key')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                collect_matrix(self.pois, key='mock-secret-key', root=tmp, opener=opener, test_mode=True)
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
