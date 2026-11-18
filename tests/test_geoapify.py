"""Artificial Geoapify mock responses; no external requests."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from ai_ds.sources.geoapify import collect, fetch_json, normalize, resolve_city


def feature(place_id='test-museum'):
    return {'type': 'Feature', 'properties': {'place_id': place_id, 'name': 'Mock museum',
            'country_code': 'ru', 'categories': ['entertainment.museum']},
            'geometry': {'type': 'Point', 'coordinates': [37.6, 55.7]}}


class GeoapifyTests(unittest.TestCase):
    def test_normalization_and_nulls(self):
        rows, report = normalize({'type': 'FeatureCollection', 'features': [feature()]},
            city='Moscow', country_code='RU', retrieved_at='2026-11-17T12:00:00+03:00')
        self.assertEqual(rows[0]['categories'], ['museums'])
        self.assertEqual(rows[0]['coordinates']['latitude'], 55.7)
        for key in ['rating', 'popularity', 'opening_hours', 'visit_duration_minutes', 'estimated_cost']:
            self.assertIsNone(rows[0][key])
        self.assertFalse(rows[0]['provenance']['is_synthetic'])

    def test_bad_country_category_and_duplicates(self):
        bad = feature(); bad['properties']['country_code'] = 'jp'
        rows, report = normalize({'type': 'FeatureCollection', 'features': [bad, feature(), feature()]},
            city='Moscow', country_code='RU', retrieved_at='2026-11-17T12:00:00Z')
        self.assertEqual(rows, [])
        self.assertEqual(report['quality']['summary']['rejected_count'], 2)
        self.assertEqual(report['normalization_rejected'][0]['code'], 'COUNTRY_NOT_CONFIRMED')

    def test_key_and_error_redaction(self):
        with self.assertRaises(ValueError):
            fetch_json('/v2/places', {}, '')
        def denied(*a, **kw):
            raise URLError('secret-key')
        with self.assertRaises(ValueError) as caught:
            fetch_json('/v2/places', {}, 'secret-key', opener=denied)
        self.assertNotIn('secret-key', str(caught.exception))
        def forbidden(*a, **kw):
            raise HTTPError('https://example/?apiKey=secret-key', 403, 'secret-key', {}, io.BytesIO())
        with self.assertRaises(ValueError) as caught:
            fetch_json('/v2/places', {}, 'secret-key', opener=forbidden)
        self.assertNotIn('secret-key', str(caught.exception))

    def test_mock_pipeline_and_secret_not_persisted(self):
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            data = {'results': [{'city': 'Moscow', 'country_code': 'ru', 'place_id': 'boundary'}]} if '/geocode/' in request.full_url else {'type': 'FeatureCollection', 'features': [feature()]}
            return io.BytesIO(json.dumps(data).encode('utf-8'))
        with tempfile.TemporaryDirectory() as tmp:
            path, count = collect('Moscow', 'RU', key='secret-key', opener=opener, root=Path(tmp))
            self.assertEqual(count, 1)
            for p in path.iterdir():
                self.assertNotIn('secret-key', p.read_text(encoding='utf-8'))
        self.assertEqual(len(calls), 2)

    def test_invalid_limit_and_ambiguous_city(self):
        with self.assertRaises(ValueError):
            collect('Moscow', 'RU', limit=True)
        with self.assertRaises(ValueError):
            resolve_city('Moscow', 'RU', 'key', opener=lambda *a, **kw: io.BytesIO(b'{"results":[]}'))


    def test_paginated_offsets_dedup_and_target(self):
        calls = []
        def opener(request, timeout):
            q = parse_qs(urlparse(request.full_url).query)
            if '/geocode/' in request.full_url:
                return io.BytesIO(b'{"results":[{"city":"Moscow","country_code":"ru","place_id":"boundary"}]}')
            calls.append(q)
            offset = int(q['offset'][0])
            features = [feature('id-' + str(i)) for i in range(offset, offset + 100)]
            return io.BytesIO(json.dumps({'type':'FeatureCollection','features':features}).encode())
        with tempfile.TemporaryDirectory() as tmp:
            path, count = collect('Moscow', 'RU', limit=101, key='secret-key', opener=opener, root=tmp)
            rows = json.loads((path/'pois.validated.json').read_text())
            self.assertEqual(count, 101)
            self.assertEqual(len({r['id'] for r in rows}), 101)
            self.assertIn('100', [q['offset'][0] for q in calls])
            metadata = json.loads((path/'metadata.json').read_text())
            self.assertLessEqual(metadata['reserved_credit_upper_estimate'], 61)
            self.assertLessEqual(metadata['http_attempt_count'], 13)

    def test_budget_stops_without_extra_requests(self):
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            data = {'results':[{'city':'Moscow','country_code':'ru','place_id':'boundary'}]} if '/geocode/' in request.full_url else {'type':'FeatureCollection','features':[feature('id-' + str(i)) for i in range(100)]}
            return io.BytesIO(json.dumps(data).encode())
        with tempfile.TemporaryDirectory() as tmp:
            path, count = collect('Moscow','RU',limit=1000,key='secret-key',opener=opener,root=tmp,max_credits=6)
            metadata = json.loads((path/'metadata.json').read_text())
            self.assertEqual(len(calls),2)
            self.assertEqual(metadata['stop_reason'],'budget_limit')
            self.assertEqual(metadata['reserved_credit_upper_estimate'],6)

    def test_partial_failure_preserves_completed_page(self):
        calls=[]
        def opener(request, timeout):
            calls.append(request.full_url)
            if '/geocode/' in request.full_url:
                return io.BytesIO(b'{"results":[{"city":"Moscow","country_code":"ru","place_id":"boundary"}]}')
            if len(calls)>2:
                raise URLError('secret-key')
            return io.BytesIO(json.dumps({'type':'FeatureCollection','features':[feature('id-' + str(i)) for i in range(100)]}).encode())
        with tempfile.TemporaryDirectory() as tmp:
            path,count=collect('Moscow','RU',limit=1000,key='secret-key',opener=opener,root=tmp)
            self.assertEqual(count,100)
            self.assertEqual(json.loads((path/'metadata.json').read_text())['stop_reason'],'request_failed')
            self.assertEqual(len(calls),3)

    def test_quota_denial_stops_and_preserves_partial(self):
        from ai_ds.sources.geoapify import GeoapifyAccessError
        calls=[]
        def opener(request,timeout):
            calls.append(1)
            if len(calls)==1:
                return io.BytesIO(b'{"results":[{"city":"Moscow","country_code":"ru","place_id":"boundary"}]}')
            if len(calls)>2:
                raise HTTPError('secret-key',429,'secret-key',{},io.BytesIO())
            return io.BytesIO(json.dumps({'type':'FeatureCollection','features':[feature('id-' + str(i)) for i in range(100)]}).encode())
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(GeoapifyAccessError) as caught:
                collect('Moscow','RU',limit=1000,key='secret-key',opener=opener,root=tmp)
            self.assertEqual(caught.exception.status,429)
            self.assertTrue(Path(caught.exception.snapshot).exists())
            self.assertEqual(len(calls),3)
            self.assertNotIn('secret-key',str(caught.exception))


if __name__ == '__main__':
    unittest.main()
