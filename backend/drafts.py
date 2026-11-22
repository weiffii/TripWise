"""Параметры поездки Fullstack: это черновик анкеты, не маршрут AI/DS."""

import json
import time
from decimal import Decimal
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from backend.accounts import current_user, database, same_origin
from backend.catalog import CITIES, CURRENCY_CODES


Category = Literal["museums", "history", "nature", "cafes", "entertainment", "shopping", "viewpoints", "unusual"]
router = APIRouter(prefix="/api/trip-draft", tags=["Черновик анкеты"])


class TravelerCounts(BaseModel):
    """Состав компании без неподтверждённых возрастных границ и тарифов."""

    model_config = ConfigDict(extra="forbid")
    adults: int = Field(strict=True, ge=0, le=100)
    children: int = Field(strict=True, ge=0, le=100)
    seniors: int = Field(strict=True, ge=0, le=100)


class TripPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["fullstack-draft-0.1", "fullstack-draft-0.2"]
    country_code: str = Field(strict=True)
    city: str = Field(strict=True)
    timezone: str = Field(strict=True)
    first_walk_at: datetime
    last_walk_end_at: datetime
    travelers: int = Field(strict=True, ge=1, le=100)
    traveler_counts: TravelerCounts
    budget_amount: Decimal = Field(ge=0, le=1_000_000_000, decimal_places=2, allow_inf_nan=False)
    budget_currency: str = Field(strict=True)
    budget_basis: Literal["per_person", "group"]
    budget_scope: Literal["trip_food_and_entertainment", "trip_food_visits_and_transport"]
    primary_interests: list[Category] = Field(min_length=1, max_length=8)
    secondary_interests: list[Category] = Field(max_length=8)
    pace: Literal["relaxed", "balanced", "intensive"]
    transport_modes: list[Literal["walking", "public_transit", "car"]] = Field(min_length=1, max_length=3)
    # Это локальные ID справочника интерфейса, не ID модуля AI/DS или OSM.
    required_poi_ids: list[str] = Field(max_length=20)

    @model_validator(mode="before")
    @classmethod
    def legacy_traveler_counts(cls, value):
        # Только старое отсутствующее поле получает обратно совместимое значение.
        # Явные null, неполный состав или несовпадение суммы остаются ошибками.
        if isinstance(value, dict) and "traveler_counts" not in value:
            value = {**value, "traveler_counts": {
                "adults": value.get("travelers"), "children": 0, "seniors": 0,
            }}
        return value

    @field_validator("first_walk_at", "last_walk_end_at", mode="before")
    @classmethod
    def require_iso_datetime(cls, value):
        if not isinstance(value, str) or "T" not in value:
            raise ValueError("Выберите дату и время прогулки.")
        return value

    @field_validator("budget_currency")
    @classmethod
    def known_currency(cls, value):
        if value not in CURRENCY_CODES:
            raise ValueError("Выберите валюту из справочника.")
        return value

    @model_validator(mode="after")
    def consistent_preferences(self, info: ValidationInfo):
        destination = CITIES.get((self.country_code, self.city))
        if destination is None or destination["timezone"] != self.timezone:
            raise ValueError("Выберите согласованные страну, город и часовой пояс из справочника.")
        if self.schema_version == "fullstack-draft-0.1":
            # Ранние клиенты отправляют московское время с явным UTC+3.
            if (self.country_code, self.city, self.timezone) != ("RU", "Москва", "Europe/Moscow"):
                raise ValueError("Для новых направлений используйте версию анкеты 0.2.")
            for value in [self.first_walk_at, self.last_walk_end_at]:
                if value.tzinfo is None or value.utcoffset() != value.astimezone(ZoneInfo("Europe/Moscow")).utcoffset():
                    raise ValueError("Укажите время Москвы с часовым поясом.")
        else:
            zone = ZoneInfo(self.timezone)
            for value in [self.first_walk_at, self.last_walk_end_at]:
                if value.tzinfo is not None:
                    raise ValueError("Версия 0.2 принимает местное время без UTC-смещения.")
                # Проверка round-trip отвергает пропущенные часы весной, а два
                # разных допустимых UTC-инстанта — неоднозначные часы осенью.
                instants = set()
                for fold in (0, 1):
                    instant = value.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
                    if instant.astimezone(zone).replace(tzinfo=None) == value:
                        instants.add(instant)
                if len(instants) != 1:
                    raise ValueError("Выберите другое время: этот час пропущен или повторяется при переводе часов.")
        allowed_ids = {poi["id"] for poi in destination["pois"]}
        if not (info.context or {}).get('route_generation') and any(poi_id not in allowed_ids for poi_id in self.required_poi_ids):
            raise ValueError("Обязательные места должны относиться к выбранному городу.")
        counts = self.traveler_counts
        if counts.adults + counts.children + counts.seniors != self.travelers:
            raise ValueError("Сумма взрослых, детей и пожилых должна совпадать с количеством путешественников.")
        if self.first_walk_at >= self.last_walk_end_at:
            raise ValueError("Окончание последней прогулки должно быть позже начала первой.")
        for values in [self.primary_interests, self.secondary_interests, self.transport_modes, self.required_poi_ids]:
            if len(values) != len(set(values)):
                raise ValueError("Не повторяйте выбранные варианты.")
        if set(self.primary_interests) & set(self.secondary_interests):
            raise ValueError("Главные и дополнительные интересы не должны совпадать.")
        return self


class DraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(strict=True, ge=0)
    preferences: TripPreferences


def init_drafts():
    """Однократный перенос старых анкет без удаления исходного архива."""
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""
            CREATE TABLE IF NOT EXISTS trip_drafts (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                preferences_json TEXT NOT NULL,
                revision INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS saved_trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                preferences_json TEXT NOT NULL,
                revision INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                creation_key TEXT NOT NULL,
                UNIQUE(user_id, creation_key)
            )
        """)
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)")
        migrated = db.execute("SELECT 1 FROM schema_migrations WHERE name = ?", ("multi-trips-v1",)).fetchone()
        if not migrated:
            db.execute("""
                INSERT INTO saved_trips(user_id, preferences_json, revision, created_at, updated_at, creation_key)
                SELECT user_id, preferences_json, revision, updated_at, updated_at, 'legacy-v1'
                FROM trip_drafts
            """)
            db.execute("INSERT INTO schema_migrations(name) VALUES (?)", ("multi-trips-v1",))


def serialize_trip(row):
    if row is None:
        return None
    preferences = TripPreferences.model_validate_json(row["preferences_json"])
    return {"id": row["id"], "preferences": preferences.model_dump(mode="json"),
            "revision": row["revision"], "created_at": row["created_at"], "updated_at": row["updated_at"]}


def serialize_draft(row):
    trip = serialize_trip(row)
    return None if trip is None else {key: trip[key] for key in ("preferences", "revision", "updated_at")}


def require_owner_context(owner_context, user):
    if owner_context is None:
        raise HTTPException(428, "Обновите страницу перед сохранением. Несохранённые изменения при обновлении будут потеряны.",
                            headers={"X-TripWise-Draft-Error": "CONTEXT_REQUIRED"})
    if owner_context != str(user["id"]):
        raise HTTPException(409, "Аккаунт изменился. Войдите в прежний аккаунт в другой вкладке и повторите сохранение.",
                            headers={"X-TripWise-Draft-Error": "ACCOUNT_CHANGED"})


def require_revision(actual_revision, requested_revision):
    if actual_revision != requested_revision:
        raise HTTPException(409, "Поездка уже изменена в другой вкладке. Обновите страницу, чтобы загрузить сохранённую версию.")


@router.get("")
def get_draft(user: dict = Depends(current_user)):
    # Старый endpoint остаётся привязан к одной legacy-записи, а не последней
    # поездке: открытая старая вкладка не должна менять другую новую поездку.
    with database() as db:
        row = db.execute("SELECT * FROM saved_trips WHERE user_id = ? AND creation_key = 'legacy-v1'", (user["id"],)).fetchone()
    return {"owner_context": str(user["id"]), "draft": serialize_draft(row)}


@router.put("", dependencies=[Depends(same_origin)])
def save_draft(data: DraftUpdate, user: dict = Depends(current_user),
               owner_context: str | None = Header(default=None, alias="X-TripWise-Draft-Owner")):
    payload = data.preferences.model_dump_json()
    now = int(time.time())
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        require_owner_context(owner_context, user)
        row = db.execute("SELECT * FROM saved_trips WHERE user_id = ? AND creation_key = 'legacy-v1'", (user["id"],)).fetchone()
        actual_revision = row["revision"] if row else 0
        require_revision(actual_revision, data.revision)
        revision = actual_revision + 1
        db.execute("""
            INSERT INTO saved_trips(user_id, preferences_json, revision, created_at, updated_at, creation_key)
            VALUES (?, ?, ?, ?, ?, 'legacy-v1')
            ON CONFLICT(user_id, creation_key) DO UPDATE SET
                preferences_json = excluded.preferences_json,
                revision = excluded.revision,
                updated_at = excluded.updated_at
        """, (user["id"], payload, revision, now, now))
    return {"draft": {"preferences": json.loads(payload), "revision": revision, "updated_at": now}}
