"""Локальный tile proxy: только whitelist, HTTP-кеш и проверенные PNG. Без сети."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('shanghai_local_server', ROOT / 'scripts/serve_shanghai_demo.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
PNG = b'\x89PNG\r\n\x1a\nfixture bytes'


class Response:
    def __init__(self, headers=None, body=PNG):
        self.headers = {'Content-Type': 'image/png', **(headers or {})}
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return self.body[:size]


def handler(path):
    value = object.__new__(server.DemoHandler)
    value.path = path
    value.headers = {'Referer': 'http://127.0.0.1:8766/tripwise-shanghai.html'}
    value.wfile = io.BytesIO()
    value.response_headers = {}
    value.status = None
    value.send_response = lambda status: setattr(value, 'status', status)
    value.send_header = lambda key, body: value.response_headers.update({key: body})
    value.end_headers = lambda: None
    value.send_error = lambda status, *args: setattr(value, 'status', status)
    return value


class TileProxyTests(unittest.TestCase):
    def test_only_valid_tile_coordinates_are_allowed(self):
        self.assertEqual(server.tile_coordinates('/tiles/3/4/2.png'), (3, 4, 2))
        for path in ['/tiles/20/1/1.png', '/tiles/2/4/1.png', '/tiles/2/-1/1.png',
                     '/tiles/2/1/4.png', '/tiles/../../AGENTS.md',
                     '/tiles/2/1/1.png/../x', '/tiles/2/1/1.jpg', '/tiles/https://example.com']:
            self.assertIsNone(server.tile_coordinates(path), path)

    def test_valid_identity_referer_and_fresh_cache_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            opener = Mock(return_value=Response({'Cache-Control': 'public, max-age=600', 'ETag': 'tile-v1'}))
            body, meta, cached = server.get_tile(3, 4, 2, cache_dir=temp, opener=opener,
                                                 now=1000, referer='http://127.0.0.1:8766/tripwise-shanghai.html')
            self.assertEqual(body, PNG)
            self.assertFalse(cached)
            request = opener.call_args.args[0]
            self.assertEqual(request.full_url, 'https://tile.openstreetmap.org/3/4/2.png')
            headers = {key.lower(): value for key, value in request.header_items()}
            self.assertEqual(headers['user-agent'], server.TILE_USER_AGENT)
            self.assertEqual(headers['referer'], 'http://127.0.0.1:8766/tripwise-shanghai.html')
            again = server.get_tile(3, 4, 2, cache_dir=temp, opener=opener, now=1100)
            self.assertTrue(again[2])
            self.assertEqual(opener.call_count, 1)

    def test_default_ttl_is_seven_days_only_when_server_does_not_specify_one(self):
        self.assertEqual(server._cache_metadata({}, 1000)['expires_at'], 1000 + 604800)
        self.assertEqual(server._cache_metadata({'Cache-Control': 'max-age=120', 'Age': '30'}, 1000)['expires_at'], 1090)
        self.assertEqual(server._cache_metadata({'Cache-Control': 'no-cache'}, 1000)['expires_at'], 1000)

    def test_expired_cache_sends_conditional_request_and_handles_304(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Mock(return_value=Response({'Cache-Control': 'max-age=60', 'ETag': 'v1', 'Last-Modified': 'Thu, 19 Nov 2026 00:00:00 GMT'}))
            server.get_tile(1, 1, 0, cache_dir=temp, opener=first, now=1000)
            def not_modified(request, timeout):
                headers = {key.lower(): value for key, value in request.header_items()}
                self.assertEqual(headers['if-none-match'], 'v1')
                self.assertIn('if-modified-since', headers)
                raise urllib.error.HTTPError(request.full_url, 304, 'Not Modified', {'Cache-Control': 'max-age=300'}, None)
            body, metadata, cached = server.get_tile(1, 1, 0, cache_dir=temp, opener=not_modified, now=1061)
            self.assertEqual(body, PNG)
            self.assertEqual(metadata['expires_at'], 1361)

    def test_no_store_and_html_errors_are_not_cached_as_tiles(self):
        with tempfile.TemporaryDirectory() as temp:
            server.get_tile(1, 1, 0, cache_dir=temp, opener=lambda *a, **k: Response({'Cache-Control': 'no-store'}), now=1000)
            self.assertEqual(list(Path(temp).rglob('*.png')), [])
            for response in [Response({'Content-Type': 'text/html'}, b'<h1>blocked</h1>'), Response(body=b'not a png')]:
                with self.assertRaises(ValueError):
                    server.get_tile(1, 1, 0, cache_dir=temp, opener=lambda *a, value=response, **k: value, now=1000)
            self.assertEqual(list(Path(temp).rglob('*.png')), [])

    def test_handler_does_not_expose_repository_or_arbitrary_urls(self):
        for path in ['/AGENTS.md', '/.git/config', '/data/demo/shanghai/pois.json', '/tiles/2/4/0.png', '/tiles/%2e%2e/config']:
            value = handler(path)
            with patch.object(server, 'get_tile') as fetch:
                value.do_GET()
                self.assertEqual(value.status, 404)
                fetch.assert_not_called()

    def test_proxy_serves_png_same_origin_and_health_has_no_private_paths(self):
        value = handler('/tiles/3/4/2.png')
        with patch.object(server, 'get_tile', return_value=(PNG, {'expires_at': 9999999999}, True)) as fetch:
            value.do_GET()
        self.assertEqual(value.status, 200)
        self.assertEqual(value.response_headers['Content-Type'], 'image/png')
        self.assertEqual(value.wfile.getvalue(), PNG)
        self.assertEqual(fetch.call_args.kwargs['referer'], value.headers['Referer'])
        health = handler('/health')
        health.do_GET()
        payload = json.loads(health.wfile.getvalue())
        self.assertTrue(payload['tile_proxy'])
        self.assertEqual(payload['version'], 'tile-proxy-v1')
        self.assertNotIn('root', payload)

    def test_network_failure_is_an_explicit_proxy_error(self):
        value = handler('/tiles/3/4/2.png')
        with patch.object(server, 'get_tile', side_effect=urllib.error.URLError('offline')):
            value.do_GET()
        self.assertEqual(value.status, 502)


if __name__ == '__main__':
    unittest.main()
