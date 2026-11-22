"""Несколько сохранённых анкет. Рассчитанные маршруты AI/DS пока не подключены."""

import json
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.accounts import current_user, database, same_origin
from backend.drafts import DraftUpdate, TripPreferences, require_owner_context, require_revision, serialize_trip

router = APIRouter(prefix='/api/trips', tags=['Сохранённые поездки'])


class TripCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preferences: TripPreferences
    creation_key: UUID


class TripDelete(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(strict=True, ge=1)


def owned_trip(db, trip_id, user):
    row = db.execute('SELECT * FROM saved_trips WHERE id = ? AND user_id = ?', (trip_id, user['id'])).fetchone()
    if row is None:
        raise HTTPException(404, 'Поездка не найдена.')
    return row


@router.get('')
def list_trips(user: dict = Depends(current_user)):
    with database() as db:
        rows = db.execute('SELECT * FROM saved_trips WHERE user_id = ? ORDER BY updated_at DESC, id DESC', (user['id'],)).fetchall()
    return {'owner_context': str(user['id']), 'trips': [serialize_trip(row) for row in rows]}


@router.get('/{trip_id}')
def get_trip(trip_id: int, user: dict = Depends(current_user)):
    with database() as db:
        row = owned_trip(db, trip_id, user)
    return {'owner_context': str(user['id']), 'trip': serialize_trip(row)}


@router.post('', dependencies=[Depends(same_origin)], status_code=201)
def create_trip(data: TripCreate, response: Response, user: dict = Depends(current_user),
                owner_context: str | None = Header(default=None, alias='X-TripWise-Draft-Owner')):
    key = str(data.creation_key)
    payload = data.preferences.model_dump_json()
    now = int(time.time())
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        require_owner_context(owner_context, user)
        existing = db.execute('SELECT * FROM saved_trips WHERE user_id = ? AND creation_key = ?', (user['id'], key)).fetchone()
        if existing is not None:
            # Повтор потерянного ответа не создаёт ещё одну карточку.
            canonical = TripPreferences.model_validate_json(existing['preferences_json']).model_dump(mode='json')
            if canonical != json.loads(payload):
                raise HTTPException(409, 'Эта попытка создания уже сохранена с другими параметрами. Откройте сохранённую поездку.')
            response.status_code = 200
            trip = serialize_trip(existing)
        else:
            cursor = db.execute('''INSERT INTO saved_trips
                (user_id, preferences_json, revision, created_at, updated_at, creation_key)
                VALUES (?, ?, 1, ?, ?, ?)''', (user['id'], payload, now, now, key))
            trip = serialize_trip(owned_trip(db, cursor.lastrowid, user))
    return {'trip': trip}


@router.put('/{trip_id}', dependencies=[Depends(same_origin)])
def update_trip(trip_id: int, data: DraftUpdate, user: dict = Depends(current_user),
                owner_context: str | None = Header(default=None, alias='X-TripWise-Draft-Owner')):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        require_owner_context(owner_context, user)
        row = owned_trip(db, trip_id, user)
        require_revision(row['revision'], data.revision)
        db.execute('UPDATE saved_trips SET preferences_json = ?, revision = ?, updated_at = ? WHERE id = ? AND user_id = ?',
                   (data.preferences.model_dump_json(), row['revision'] + 1, int(time.time()), trip_id, user['id']))
        trip = serialize_trip(owned_trip(db, trip_id, user))
    return {'trip': trip}


@router.delete('/{trip_id}', dependencies=[Depends(same_origin)], status_code=204)
def delete_trip(trip_id: int, data: TripDelete, user: dict = Depends(current_user),
                owner_context: str | None = Header(default=None, alias='X-TripWise-Draft-Owner')):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        require_owner_context(owner_context, user)
        row = owned_trip(db, trip_id, user)
        require_revision(row['revision'], data.revision)
        db.execute('DELETE FROM saved_trips WHERE id = ? AND user_id = ?', (trip_id, user['id']))
    return Response(status_code=204)
