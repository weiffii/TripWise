"""Открыть локальный Shanghai Lab через HTTP; доступен только готовый HTML."""
from __future__ import annotations
import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import threading
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / 'artifacts/tripwise-shanghai.html'


class DemoHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return
        if urlsplit(self.path).path not in ('/', '/tripwise-shanghai.html'):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not HTML.is_file():
        from build_shanghai_demo import build
        build()
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), DemoHandler)
    except OSError:
        server = ThreadingHTTPServer(('127.0.0.1', 0), DemoHandler)
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
