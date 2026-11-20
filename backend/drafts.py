"""Параметры поездки Fullstack: это черновик анкеты, не маршрут AI/DS."""

import json
import time
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.accounts import current_user, database, same_origin


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
    schema_version: Literal["fullstack-draft-0.1"]
    country_code: Literal["RU"]
    city: Literal["Москва"]
    timezone: Literal["Europe/Moscow"]
    first_walk_at: AwareDatetime
    last_walk_end_at: AwareDatetime
    travelers: int = Field(strict=True, ge=1, le=100)
    traveler_counts: TravelerCounts
    budget_amount: Decimal = Field(ge=0, le=1_000_000_000, decimal_places=2, allow_inf_nan=False)
    budget_currency: Literal["RUB", "USD", "EUR", "CNY"]
    budget_basis: Literal["per_person", "group"]
    budget_scope: Literal["trip_food_and_entertainment"]
    primary_interests: list[Category] = Field(min_length=1, max_length=2)
    secondary_interests: list[Category] = Field(max_length=8)
    pace: Literal["relaxed", "balanced", "intensive"]
    transport_modes: list[Literal["walking", "public_transit", "car"]] = Field(min_length=1, max_length=3)
    # Пока нет согласованного каталога реальных POI — не принимаем выдуманные ID.
    required_poi_ids: list[str] = Field(max_length=0)

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

    @field_validator("first_walk_at", "last_walk_end_at")
    @classmethod
    def moscow_time(cls, value):
        if value.utcoffset() != value.astimezone(ZoneInfo("Europe/Moscow")).utcoffset():
            raise ValueError("Укажите время Москвы.")
        return value

    @model_validator(mode="after")
    def consistent_preferences(self):
        counts = self.traveler_counts
        if counts.adults + counts.children + counts.seniors != self.travelers:
            raise ValueError("Сумма взрослых, детей и пожилых должна совпадать с количеством путешественников.")
        if self.first_walk_at >= self.last_walk_end_at:
            raise ValueError("Окончание последней прогулки должно быть позже начала первой.")
        for values in [self.primary_interests, self.secondary_interests, self.transport_modes]:
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
    with database() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS trip_drafts (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                preferences_json TEXT NOT NULL,
                revision INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)


def serialize_draft(row):
    if row is None:
        return None
    # Старые анкеты дополняются только в ответе; чтение не переписывает SQLite
    # и не меняет revision, поэтому не создаёт ложный конфликт другой вкладке.
    preferences = TripPreferences.model_validate_json(row["preferences_json"])
    return {"preferences": preferences.model_dump(mode="json"), "revision": row["revision"], "updated_at": row["updated_at"]}


@router.get("")
def get_draft(user: dict = Depends(current_user)):
    with database() as db:
        row = db.execute("SELECT * FROM trip_drafts WHERE user_id = ?", (user["id"],)).fetchone()
    return {"owner_context": str(user["id"]), "draft": serialize_draft(row)}


@router.put("", dependencies=[Depends(same_origin)])
def save_draft(
    data: DraftUpdate,
    user: dict = Depends(current_user),
    owner_context: str | None = Header(default=None, alias="X-TripWise-Draft-Owner"),
):
    if owner_context is None:
        raise HTTPException(
            428, "Обновите страницу анкеты перед сохранением. Несохранённые изменения при обновлении будут потеряны.",
            headers={"X-TripWise-Draft-Error": "CONTEXT_REQUIRED"},
        )
    payload = data.preferences.model_dump_json()
    now = int(time.time())
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        # Контекст — предусловие старой формы, не способ выбрать владельца записи.
        # Владельца по-прежнему определяет только текущий серверный сеанс.
        if owner_context != str(user["id"]):
            raise HTTPException(
                409, "Аккаунт изменился. Сохранение остановлено. Войдите в прежний аккаунт в другой вкладке и нажмите «Готово» для повторной попытки.",
                headers={"X-TripWise-Draft-Error": "ACCOUNT_CHANGED"},
            )
        row = db.execute("SELECT revision FROM trip_drafts WHERE user_id = ?", (user["id"],)).fetchone()
        actual_revision = row["revision"] if row else 0
        if data.revision != actual_revision:
            raise HTTPException(409, "Анкета уже изменена в другой вкладке. Обновите страницу, чтобы загрузить сохранённую версию.")
        revision = actual_revision + 1
        db.execute("""
            INSERT INTO trip_drafts(user_id, preferences_json, revision, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                preferences_json = excluded.preferences_json,
                revision = excluded.revision,
                updated_at = excluded.updated_at
        """, (user["id"], payload, revision, now))
    return {"draft": {"preferences": json.loads(payload), "revision": revision, "updated_at": now}}
