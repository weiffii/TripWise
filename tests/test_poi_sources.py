"""Сетевые сценарии на искусственных mock-ответах, без внешних запросов."""

from contextlib import contextmanager
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError, URLError

from ai_ds.sources.overpass import (CITIES, admin_relation, download, fetch,
                                    query_path, retry_seconds)
from ai_ds.normalize_pois import read_snapshot


def mock_document(city='moscow'):
    """Явно искусственный Overpass payload исключительно для unit-тестов."""
    return {'elements': [{'type': 'relation', 'id': 999999999,
                          'tags': {'type': 'boundary', 'boundary': 'administrative',
                                   'admin_level': '4', 'ISO3166-2': CITIES[city]['iso'],
                                   'name': CITIES[city]['name']}}]}


@contextmanager
def mock_response(payload):
    yield io.BytesIO(json.dumps(payload).encode('utf-8'))


class SourceTests(unittest.TestCase):
    def test_fetch_headers_timeout_and_body(self):
        calls = []
        def opener(request, timeout):
            calls.append((request, timeout))
            return mock_response(mock_document())
        body = fetch('test query', opener=opener)
        self.assertEqual(json.loads(body), mock_document())
        request, timeout = calls[0]
        self.assertEqual(request.method, 'POST')
        self.assertIn('TripWise', request.get_header('User-agent'))
        self.assertEqual(timeout, 30)
        self.assertIn(b'data=test+query', request.data)

    def test_429_retry_after(self):
        calls, sleeps = [], []
        def opener(request, timeout):
            calls.append(1)
            if len(calls) == 1:
                raise HTTPError(request.full_url, 429, 'limited', {'Retry-After': '3'}, None)
            return mock_response(mock_document())
        fetch('query', opener=opener, sleep=sleeps.append)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [3])

    def test_permanent_errors_do_not_retry(self):
        for code in (400, 403):
            calls = []
            def opener(request, timeout):
                calls.append(1)
                raise HTTPError(request.full_url, code, 'error', {}, None)
            with self.assertRaises(HTTPError):
                fetch('query', opener=opener, sleep=lambda _: self.fail('unexpected sleep'))
            self.assertEqual(len(calls), 1)

    def test_network_failure_bounded(self):
        calls, sleeps = [], []
        def opener(request, timeout):
            calls.append(1)
            raise URLError('mock denied')
        with self.assertRaises(URLError):
            fetch('query', opener=opener, sleep=sleeps.append)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [1])

    def test_server_error_retry_bounded(self):
        calls = []
        def opener(request, timeout):
            calls.append(1)
            raise HTTPError(request.full_url, 504, 'timeout', {}, None)
        with self.assertRaises(HTTPError):
            fetch('query', opener=opener, sleep=lambda _: None)
        self.assertEqual(len(calls), 2)

    def test_retry_after_date_and_large_delay(self):
        now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
        self.assertEqual(retry_seconds('Mon, 16 Nov 2026 12:00:10 GMT', 0, now), 10)
        with self.assertRaises(ValueError):
            retry_seconds('120', 0)

    def test_malformed_and_partial_payloads(self):
        for payload in ({}, {'elements': [None]}, {'elements': [{'tags': []}]},
                        {'elements': [], 'remark': 'timeout'}, {'elements': [], 'remark': 1}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                fetch('query', opener=lambda *a, **kw: mock_response(payload))

    def test_admin_tags_and_wrong_city(self):
        for city in CITIES:
            self.assertEqual(admin_relation(mock_document(city), city)['id'], 999999999)
        with self.assertRaises(ValueError):
            admin_relation(mock_document(), 'saint-petersburg')
        payload = mock_document(); payload['elements'][0]['tags']['admin_level'] = '6'
        with self.assertRaises(ValueError):
            admin_relation(payload, 'moscow')

    def test_download_snapshot_exclusive_and_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            opener = lambda *a, **kw: mock_response(mock_document())
            first = download('moscow', directory, opener=opener)
            second = download('moscow', directory, opener=opener)
            self.assertNotEqual(first, second)
            document, metadata = read_snapshot(first)
            self.assertEqual(document, mock_document())
            self.assertEqual(metadata['license'], 'ODbL-1.0')
            self.assertEqual((first / 'query.overpassql').read_text(encoding='utf-8'),
                             query_path('moscow').read_text(encoding='utf-8'))
            (first / 'response.json').write_text('{}', encoding='utf-8')
            with self.assertRaises(ValueError):
                read_snapshot(first)

    def test_failed_download_creates_no_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            def opener(*args, **kwargs):
                raise URLError('mock network error')
            with self.assertRaises(URLError):
                download('moscow', directory, opener=opener, sleep=lambda _: None)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
