"""Аккаунты и серверные сеансы. Только SQLite и стандартная библиотека Python."""

from contextlib import contextmanager
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import unicodedata

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("TRIPWISE_DB_PATH", ROOT / "runtime" / "tripwise.sqlite3"))
COOKIE_NAME = "tripwise_session"
SESSION_SECONDS = 7 * 24 * 60 * 60
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 3
router = APIRouter(prefix="/api/auth", tags=["Аккаунт"])


@contextmanager
def database():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with database() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_attempts (
                client TEXT NOT NULL,
                attempted_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS attempts_client_time
                ON auth_attempts(client, attempted_at);
        """)
        columns = {row['name'] for row in db.execute('PRAGMA table_info(users)')}
        if 'display_name' not in columns:
            db.execute("ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
    DB_PATH.chmod(0o600)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=bytes.fromhex(salt),
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=64 * 1024 * 1024,
    ).hex()
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    algorithm, n, r, p, salt, digest = stored.split("$")
    if (algorithm, int(n), int(r), int(p)) != ("scrypt", SCRYPT_N, SCRYPT_R, SCRYPT_P):
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing", "00" * 16)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128, repr=False, json_schema_extra={"format": "password"})

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        value = value.strip().lower()
        # Ограниченный формат для первого локального варианта: обычная ASCII-почта.
        # Проверка формата не подтверждает владение почтовым ящиком.
        if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", value):
            raise ValueError("Некорректный адрес электронной почты")
        local = value.split("@")[0]
        if len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local:
            raise ValueError("Некорректный адрес электронной почты")
        return value


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(strict=True, max_length=80)

    @field_validator('display_name', mode='before')
    @classmethod
    def clean_display_name(cls, value):
        if not isinstance(value, str):
            return value
        if any(unicodedata.category(char) in {'Cc', 'Cs'} for char in value):
            raise ValueError('Имя не должно содержать управляющие символы.')
        return ' '.join(value.split())


class Registration(Credentials):
    password: str = Field(min_length=8, max_length=128, repr=False, json_schema_extra={"format": "password"})
    password_confirmation: str = Field(min_length=8, max_length=128, repr=False, json_schema_extra={"format": "password"})
    display_name: str = Field(default='', strict=True, max_length=80)

    @field_validator('display_name', mode='before')
    @classmethod
    def clean_display_name(cls, value):
        return ProfileUpdate.clean_display_name(value)

    @model_validator(mode="after")
    def matching_passwords(self):
        if self.password != self.password_confirmation:
            raise ValueError("Пароли не совпадают")
        return self


def same_origin(request: Request):
    # Браузерный fetch посылает Origin. Не принимаем запросы изменения с чужих сайтов.
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if request.headers.get("origin") != expected:
        raise HTTPException(403, "Откройте TripWise по адресу сервера и повторите действие.")


def limit_attempts(request: Request):
    client = request.client.host if request.client else "unknown"
    now = int(time.time())
    # 30 попыток регистрации/входа в минуту на адрес, сохраняются после перезапуска.
    # Для публичного многопользовательского развёртывания политика требует доработки.
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM auth_attempts WHERE attempted_at <= ?", (now - 60,))
        count = db.execute("SELECT COUNT(*) FROM auth_attempts WHERE client = ?", (client,)).fetchone()[0]
        if count >= 30:
            raise HTTPException(429, "Слишком много попыток. Подождите минуту.", headers={"Retry-After": "60"})
        db.execute("INSERT INTO auth_attempts VALUES (?, ?)", (client, now))


def current_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or len(token) > 128:
        raise HTTPException(401, "Войдите в аккаунт.")
    with database() as db:
        row = db.execute("""
            SELECT users.id, users.email, users.display_name FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
        """, (token_hash(token), int(time.time()))).fetchone()
    if row is None:
        raise HTTPException(401, "Войдите в аккаунт.")
    return dict(row)


def start_session(db, user_id, old_token):
    now = int(time.time())
    db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
    if old_token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(old_token),))
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (token_hash(token), user_id, now + SESSION_SECONDS))
    return token


def set_session_cookie(response: Response, request: Request, token: str):
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True,
        secure=request.url.scheme == "https", samesite="lax", path="/",
    )
    response.headers["Cache-Control"] = "no-store"


@router.post("/register", status_code=201, dependencies=[Depends(same_origin), Depends(limit_attempts)])
def register(data: Registration, request: Request, response: Response):
    password_hash = hash_password(data.password)
    with database() as db:
        try:
            cursor = db.execute("INSERT INTO users(email, password_hash, created_at, display_name) VALUES (?, ?, ?, ?)",
                                (data.email, password_hash, int(time.time()), data.display_name))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Этот адрес уже зарегистрирован. Перейдите во «Вход».") from None
        user = {"id": cursor.lastrowid, "email": data.email, "display_name": data.display_name}
        token = start_session(db, user["id"], request.cookies.get(COOKIE_NAME))
    set_session_cookie(response, request, token)
    return {"user": user}


@router.post("/login", dependencies=[Depends(same_origin), Depends(limit_attempts)])
def login(data: Credentials, request: Request, response: Response):
    with database() as db:
        row = db.execute("SELECT id, email, display_name, password_hash FROM users WHERE email = ?", (data.email,)).fetchone()
    # Для неизвестного пользователя выполняем ту же дорогую операцию.
    stored = row["password_hash"] if row else DUMMY_PASSWORD_HASH
    valid = verify_password(data.password, stored)
    if row is None or not valid:
        raise HTTPException(401, "Неверная почта или пароль.")
    with database() as db:
        token = start_session(db, row["id"], request.cookies.get(COOKIE_NAME))
    set_session_cookie(response, request, token)
    return {"user": {"id": row["id"], "email": row["email"], "display_name": row['display_name']}}


@router.get("/me")
def me(response: Response, user: dict = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return {"user": user, "owner_context": str(user['id'])}


@router.put('/profile', dependencies=[Depends(same_origin)])
def update_profile(data: ProfileUpdate, response: Response, user: dict = Depends(current_user),
                   owner_context: str | None = Header(default=None, alias='X-TripWise-Draft-Owner')):
    # Импорт после загрузки модулей: drafts сам использует accounts.
    from backend.drafts import require_owner_context
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        require_owner_context(owner_context, user)
        db.execute('UPDATE users SET display_name = ? WHERE id = ?', (data.display_name, user['id']))
    response.headers['Cache-Control'] = 'no-store'
    return {'user': {**user, 'display_name': data.display_name}}


@router.post("/logout", dependencies=[Depends(same_origin)])
def logout(request: Request, response: Response):
    with database() as db:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(request.cookies.get(COOKIE_NAME, "")),))
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=request.url.scheme == "https")
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok"}
