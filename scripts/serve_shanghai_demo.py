"""Локальный Shanghai Lab: готовый HTML и строго ограниченный OSM tile proxy."""
from __future__ import annotations
import argparse
from email.utils import parsedate_to_datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import threading
import tempfile
import webbrowser
import sys
from http.cookies import SimpleCookie

if str(ROOT if 'ROOT' in globals() else Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / 'artifacts/tripwise-shanghai.html'
TILE_CACHE = ROOT / 'artifacts/osm-tile-cache'
TILE_USER_AGENT = 'TripWiseAcademicDemo/0.2 (+https://github.com/weiffii/TripWise)'
DEFAULT_TILE_TTL = 7 * 24 * 60 * 60
_TILE_LOCK = threading.Lock()


def tile_coordinates(path):
    """Разрешить только фиксированную форму пути; это не произвольный HTTP proxy."""
    match = re.fullmatch(r'/tiles/(\d{1,2})/(\d{1,10})/(\d{1,10})\.png', path)
    if not match:
        return None
    z, x, y = map(int, match.groups())
    return (z, x, y) if 0 <= z <= 19 and 0 <= x < 2 ** z and 0 <= y < 2 ** z else None


def _cache_metadata(headers, now):
    headers = {key.lower(): value for key, value in headers.items()}
    control = headers.get('cache-control', '') or ''
    age = max(0, int(headers.get('age', '0'))) if str(headers.get('age', '0')).isdigit() else 0
    maximum = re.search(r'(?:^|,)\s*max-age\s*=\s*"?(\d+)', control, re.I)
    expires = now + DEFAULT_TILE_TTL
    if maximum:
        expires = now + max(0, int(maximum.group(1)) - age)
    elif headers.get('expires'):
        try:
            expires = parsedate_to_datetime(headers['expires']).timestamp()
        except (TypeError, ValueError, OverflowError):
            pass
    if 'no-cache' in control.lower() or 'no-store' in control.lower():
        expires = now
    return {'expires_at': expires, 'cache_control': control,
            'etag': headers.get('etag'), 'last_modified': headers.get('last-modified')}


def get_tile(z, x, y, *, cache_dir=None, opener=None, now=None, referer=None):
    """Получить только запрошенный пользователем тайл, с обычным HTTP-кешированием.

    Нет предварительной загрузки районов или обхода набора координат. Если сервер
    не задаёт срок кеша, используется рекомендованный минимум семь дней.
    """
    if tile_coordinates(f'/tiles/{z}/{x}/{y}.png') != (z, x, y):
        raise ValueError('Invalid tile coordinates')
    now = time.time() if now is None else now
    cache_dir = Path(cache_dir) if cache_dir is not None else TILE_CACHE
    base = cache_dir / str(z) / str(x)
    image_path, meta_path = base / f'{y}.png', base / f'{y}.json'
    with _TILE_LOCK:
        body, metadata = None, {}
        try:
            metadata = json.loads(meta_path.read_text(encoding='utf-8'))
            if not isinstance(metadata, dict) or not isinstance(metadata.get('expires_at'), (int, float)):
                metadata = {}
            body = image_path.read_bytes()
            if not body.startswith(b'\x89PNG\r\n\x1a\n') or metadata.get('body_sha256') not in (None, hashlib.sha256(body).hexdigest()):
                body = None
        except (OSError, ValueError, TypeError):
            pass
        if body is not None and metadata.get('expires_at', 0) > now:
            return body, metadata, True
        headers = {'User-Agent': TILE_USER_AGENT, 'Accept': 'image/png'}
        if referer:
            origin = urlsplit(referer)
            if origin.scheme == 'http' and origin.hostname in ('127.0.0.1', 'localhost', '::1'):
                headers['Referer'] = referer
        if body is not None:
            if metadata.get('etag'):
                headers['If-None-Match'] = metadata['etag']
            if metadata.get('last_modified'):
                headers['If-Modified-Since'] = metadata['last_modified']
        # z/x/y are validated integers; no user-supplied domain or filesystem path.
        request = urllib.request.Request(f'https://tile.openstreetmap.org/{z}/{x}/{y}.png', headers=headers)
        open_url = opener or urllib.request.urlopen
        try:
            with open_url(request, timeout=12) as response:
                if response.headers.get('Content-Type', '').split(';')[0].lower() != 'image/png':
                    raise ValueError('OSM did not return a PNG tile')
                fresh_body = response.read(1024 * 1024 + 1)
                if len(fresh_body) > 1024 * 1024 or not fresh_body.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('Invalid OSM tile')
                metadata = _cache_metadata(response.headers, now)
                body = fresh_body
        except urllib.error.HTTPError as error:
            if error.code != 304 or body is None:
                error.close()
                raise
            refreshed = {'Cache-Control': metadata.get('cache_control', ''),
                         'ETag': metadata.get('etag'), 'Last-Modified': metadata.get('last_modified')}
            refreshed.update(dict(error.headers.items()))
            metadata = _cache_metadata(refreshed, now)
            error.close()
        if 'no-store' not in metadata.get('cache_control', '').lower():
            base.mkdir(parents=True, exist_ok=True)
            metadata['body_sha256'] = hashlib.sha256(body).hexdigest()
            # Unique temporary files also tolerate two launcher processes sharing the cache.
            with tempfile.NamedTemporaryFile(dir=base, prefix='.tile-', suffix='.part', delete=False) as temporary:
                temporary.write(body)
                temporary_path = Path(temporary.name)
            temporary_path.replace(image_path)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=base, prefix='.meta-', suffix='.part', delete=False) as temporary:
                json.dump(metadata, temporary)
                temporary_path = Path(temporary.name)
            temporary_path.replace(meta_path)
        return body, metadata, False


class DemoHandler(BaseHTTPRequestHandler):
    def _assistant_json(self, value, status=200, token=None):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if token:
            self.send_header('Set-Cookie', 'tripwise_session=' + token + '; Path=/; HttpOnly; SameSite=Strict; Max-Age=3600')
        self.end_headers(); self.wfile.write(body)

    def _assistant_session_token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            return cookie['tripwise_session'].value if 'tripwise_session' in cookie else None
        except Exception:
            return None

    def _assistant_origin_valid(self):
        host = self.headers.get('Host', '')
        expected = '127.0.0.1:' + str(self.server.server_port)
        local = 'localhost:' + str(self.server.server_port)
        if host not in (expected, local):
            return False
        origin = self.headers.get('Origin')
        return origin is None or origin == 'http://' + host

    def do_POST(self):
        from ai_ds.route_assistant import AssistantError
        from ai_ds.shanghai_assistant import human_error
        path = urlsplit(self.path).path
        service = getattr(self.server, 'assistant_service', None)
        if not path.startswith('/api/assistant/') or service is None:
            self.send_error(404); return
        try:
            if not self._assistant_origin_valid():
                raise AssistantError('ORIGIN_REJECTED')
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                raise AssistantError('JSON_REQUIRED')
            length = self.headers.get('Content-Length', '')
            if not length.isdigit() or not 0 < int(length) <= 2 * 1024 * 1024:
                raise AssistantError('REQUEST_TOO_LARGE')
            self.connection.settimeout(10)
            from ai_ds.route_assistant import unique_pairs, reject_constant
            body = json.loads(self.rfile.read(int(length)), object_pairs_hook=unique_pairs, parse_constant=reject_constant)
            result = service.request(self._assistant_session_token(), self.headers.get('X-TripWise-CSRF'), path.rsplit('/', 1)[-1], body)
            self._assistant_json(result)
        except AssistantError as error:
            self._assistant_json({'type': 'error', 'code': error.code, 'message': human_error(error.code)}, 403 if error.code in ('CSRF_REJECTED', 'ORIGIN_REJECTED', 'SESSION_REQUIRED') else 400)
        except (ValueError, TypeError, OSError):
            self._assistant_json({'type': 'error', 'code': 'INVALID_REQUEST', 'message': 'Запрос не удалось обработать. Исходный маршрут сохранён.'}, 400)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/assistant/status':
            from ai_ds.route_assistant import AssistantError
            service = getattr(self.server, 'assistant_service', None)
            if service is None:
                self._assistant_json({'available': False, 'reason': 'API помощника не запущен.'}, 503); return
            if not self._assistant_origin_valid():
                self._assistant_json({'type': 'error', 'code': 'ORIGIN_REJECTED', 'message': 'Недопустимый источник запроса.'}, 403); return
            try:
                token, result = service.status(self._assistant_session_token())
                self._assistant_json(result, token=token)
            except AssistantError:
                self._assistant_json({'available': False, 'reason': 'Сервис временно занят.'}, 503)
            return
        if path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return
        if path == '/health':
            body = b'{"service":"tripwise-shanghai","version":"tile-proxy-v1","tile_proxy":true}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith('/tiles/'):
            coordinates = tile_coordinates(path)
            if coordinates is None:
                self.send_error(404)
                return
            try:
                body, metadata, cached = get_tile(*coordinates, referer=self.headers.get('Referer'))
            except (OSError, ValueError, urllib.error.URLError):
                self.send_error(502, 'OSM map tile is temporarily unavailable; route points remain usable')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(body)))
            cache_control = metadata.get('cache_control', '')
            self.send_header('Cache-Control', 'no-store' if 'no-store' in cache_control.lower() else
                             f'public, max-age={max(0, int(metadata.get("expires_at", 0) - time.time()))}')
            self.send_header('X-TripWise-Tile-Source', 'cache' if cached else 'upstream')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)
            return
        if path not in ('/', '/tripwise-shanghai.html'):
            self.send_error(404)
            return
        if not HTML.is_file():
            self.send_error(404, 'Build the local Shanghai HTML first')
            return
        body = HTML.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def create_server(port=8765, *, assistant_service=None):
    """Инъекция mock interpreter допустима только из явно тестового запуска."""
    if assistant_service is None:
        from ai_ds.shanghai_assistant import ShanghaiAssistantService
        assistant_service = ShanghaiAssistantService()
    server = ThreadingHTTPServer(('127.0.0.1', port), DemoHandler)
    server.assistant_service = assistant_service
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--assistant-mock', type=Path, help='Явный тестовый JSON со списком ответов mock LLM; не для живого демо')
    args = parser.parse_args()
    if not HTML.is_file():
        from build_shanghai_demo import build
        build()
    assistant_service = None
    if args.assistant_mock:
        from ai_ds.shanghai_assistant import ShanghaiAssistantService, mock_interpreter_file
        assistant_service = ShanghaiAssistantService(interpreter=mock_interpreter_file(args.assistant_mock), mode='mock')
    try:
        server = create_server(args.port, assistant_service=assistant_service)
    except OSError:
        server = create_server(0, assistant_service=assistant_service)
    url = 'http://127.0.0.1:%d/tripwise-shanghai.html' % server.server_port
    print(url, flush=True)
    if not args.no_browser:
        threading.Timer(.3, partial(webbrowser.open, url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
