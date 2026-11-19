"""Проверки реального HTTP и SQLite в изолированной временной базе."""

from contextlib import contextmanager
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, HTTPCookieProcessor, HTTPRedirectHandler, Request
import uuid


ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "TripWise-Test-2026"


@contextmanager
def test_database(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LiveServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="tripwise-auth-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.db_path = Path(cls.temp.name) / "accounts.sqlite3"
        cls.log = open(Path(cls.temp.name) / "server.log", "w+")
        cls.addClassCleanup(cls.log.close)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.addClassCleanup(cls.stop_server)
        cls.start_server()

    @classmethod
    def start_server(cls):
        env = os.environ.copy()
        env["TRIPWISE_DB_PATH"] = str(cls.db_path)
        cls.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(cls.port)],
            cwd=ROOT, env=env, stdout=cls.log, stderr=cls.log,
        )
        for _ in range(160):
            if cls.process.poll() is not None:
                raise AssertionError("Тестовый сервер не запустился")
            try:
                with build_opener().open(cls.base + "/api/health", timeout=0.3) as response:
                    if response.status == 200:
                        return
            except (URLError, TimeoutError):
                time.sleep(0.05)
        raise AssertionError("Тестовый сервер не ответил вовремя")

    @classmethod
    def stop_server(cls):
        if hasattr(cls, "process") and cls.process.poll() is None:
            cls.process.terminate()
            try:
                cls.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=5)

    def setUp(self):
        self.jar = CookieJar()
        self.client = build_opener(HTTPCookieProcessor(self.jar), NoRedirect())
        self.email = f"test-{uuid.uuid4().hex}@example.com"
        with test_database(self.db_path) as db:
            db.execute("DELETE FROM auth_attempts")

    def request(self, path, data=None, *, client=None, origin=None, headers=None, method=None):
        request_headers = dict(headers or {})
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            request_headers["Content-Type"] = "application/json"
            request_headers["Origin"] = self.base if origin is None else origin
        request = Request(self.base + path, data=body, headers=request_headers, method=method)
        try:
            response = (client or self.client).open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            result = json.loads(content) if "application/json" in response.headers.get("Content-Type", "") else content
            return response.code, result, response.headers

    def register(self, email=None, client=None):
        return self.request("/api/auth/register", {
            "email": email or self.email, "password": PASSWORD, "password_confirmation": PASSWORD,
        }, client=client)


class AccountTests(LiveServerTests):
    def test_register_and_private_page(self):
        status, data, headers = self.register()
        self.assertEqual(status, 201)
        self.assertEqual(data["user"]["email"], self.email)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=lax", headers["Set-Cookie"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        status, data, _ = self.request("/api/auth/me")
        self.assertEqual(status, 200)
        self.assertEqual(data["user"]["email"], self.email)
        status, body, _ = self.request("/trips")
        self.assertEqual(status, 200)
        self.assertIn("Мои поездки".encode(), body)

    def test_duplicate_normalized_email(self):
        self.assertEqual(self.register()[0], 201)
        self.assertEqual(self.register("  " + self.email.upper() + "  ")[0], 409)

    def test_validation_does_not_echo_password(self):
        for email, password, confirmation in [
            ("bad", PASSWORD, PASSWORD),
            (self.email, "short", "short"),
            (self.email, PASSWORD, "different-password"),
            (self.email, "x" * 129, "x" * 129),
        ]:
            with self.subTest(email_valid=email != "bad", length=len(password)):
                status, body, _ = self.request("/api/auth/register", {"email": email, "password": password, "password_confirmation": confirmation})
                self.assertEqual(status, 422)
                self.assertNotIn(password, json.dumps(body))
        with test_database(self.db_path) as db:
            self.assertIsNone(db.execute("SELECT id FROM users WHERE email = ?", (self.email,)).fetchone())

    def test_wrong_password_and_unknown_account(self):
        self.register()
        self.request("/api/auth/logout", {})
        for email in [self.email, "not-registered@example.com"]:
            status, body, _ = self.request("/api/auth/login", {"email": email, "password": "wrong-password"})
            self.assertEqual(status, 401)
            self.assertEqual(body["detail"], "Неверная почта или пароль.")
        self.assertEqual(self.request("/api/auth/me")[0], 401)

    def test_login_rotation_and_logout_revoke_token(self):
        self.register()
        old_token = next(iter(self.jar)).value
        self.assertEqual(self.request("/api/auth/login", {"email": self.email, "password": PASSWORD})[0], 200)
        new_token = next(iter(self.jar)).value
        self.assertNotEqual(new_token, old_token)
        anonymous = build_opener(NoRedirect())
        self.assertEqual(self.request("/api/auth/me", client=anonymous, headers={"Cookie": f"tripwise_session={old_token}"})[0], 401)
        self.assertEqual(self.request("/api/auth/logout", {})[0], 200)
        self.assertEqual(self.request("/api/auth/me")[0], 401)
        self.assertEqual(self.request("/api/auth/me", client=anonymous, headers={"Cookie": f"tripwise_session={new_token}"})[0], 401)

    def test_no_session_and_tampering(self):
        self.assertEqual(self.request("/api/auth/me")[0], 401)
        status, _, headers = self.request("/trips")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/")
        self.assertEqual(self.request("/api/auth/me", headers={"Cookie": "tripwise_session=fake-token"})[0], 401)

    def test_expired_session(self):
        self.register()
        token = next(iter(self.jar)).value
        with test_database(self.db_path) as db:
            db.execute("UPDATE sessions SET expires_at = ? WHERE token_hash = ?", (int(time.time()) - 1, hashlib.sha256(token.encode()).hexdigest()))
        self.assertEqual(self.request("/api/auth/me")[0], 401)

    def test_users_and_tokens_are_separate(self):
        self.register()
        second = build_opener(HTTPCookieProcessor(CookieJar()), NoRedirect())
        second_email = f"second-{uuid.uuid4().hex}@example.com"
        self.assertEqual(self.register(second_email, second)[0], 201)
        self.assertEqual(self.request("/api/auth/me")[1]["user"]["email"], self.email)
        self.assertEqual(self.request("/api/auth/me", client=second)[1]["user"]["email"], second_email)

    def test_database_has_salted_hashes_not_credentials(self):
        self.register()
        token = next(iter(self.jar)).value
        with test_database(self.db_path) as db:
            row = db.execute("SELECT id, password_hash FROM users WHERE email = ?", (self.email,)).fetchone()
            session = db.execute("SELECT token_hash FROM sessions WHERE user_id = ?", (row[0],)).fetchone()[0]
        self.assertTrue(row[1].startswith("scrypt$32768$8$3$"))
        self.assertNotIn(PASSWORD, row[1])
        self.assertNotEqual(token, session)
        self.assertEqual(session, hashlib.sha256(token.encode()).hexdigest())

    def test_cross_origin_and_missing_origin_rejected(self):
        data = {"email": self.email, "password": PASSWORD, "password_confirmation": PASSWORD}
        self.assertEqual(self.request("/api/auth/register", data, origin="https://other.example")[0], 403)
        self.assertEqual(self.request("/api/auth/register", data, origin="null")[0], 403)
        request = Request(self.base + "/api/auth/register", data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as error:
            self.client.open(request)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_attempt_limit(self):
        with test_database(self.db_path) as db:
            db.executemany("INSERT INTO auth_attempts VALUES (?, ?)", [("127.0.0.1", int(time.time()))] * 30)
        status, _, headers = self.request("/api/auth/login", {"email": self.email, "password": PASSWORD})
        self.assertEqual(status, 429)
        self.assertEqual(headers["Retry-After"], "60")

    def test_restart_preserves_account_and_session(self):
        self.register()
        self.stop_server()
        self.start_server()
        self.assertEqual(self.request("/api/auth/me")[0], 200)
        self.request("/api/auth/logout", {})
        self.assertEqual(self.request("/api/auth/login", {"email": self.email, "password": PASSWORD})[0], 200)

    def test_internal_files_stay_private(self):
        for path in ["/backend/pages/trips.html", "/runtime/tripwise.sqlite3", "/backend/accounts.py", "/.git/config"]:
            self.assertEqual(self.request(path)[0], 404)


if __name__ == "__main__":
    unittest.main()
