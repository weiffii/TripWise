from datetime import date, time, timedelta
from functools import lru_cache
import json
import secrets
import threading
from time import time as timestamp
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from ai_ds.route_assistant import AssistantError, reject_constant, unique_pairs
from ai_ds.shanghai_assistant import ROOT, ShanghaiAssistantService, bridge_call, human_error
from backend.accounts import COOKIE_NAME, current_user, database, same_origin, token_hash

router = APIRouter(dependencies=[Depends(current_user)], tags=['Шанхай'])
Count = Annotated[int, Field(strict=True, ge=0, le=30)]
Interest = Literal['museums', 'history', 'nature', 'cafes', 'entertainment', 'shopping', 'viewpoints', 'unusual']
TravelMode = Literal['walk', 'transit', 'taxi']
FX = {'CNY': 1, 'RUB': .087, 'USD': 7.10, 'EUR': 7.75}


class Group(BaseModel):
    model_config = ConfigDict(extra='forbid')
    adult: Count
    child: Count
    senior: Count
    pet: Count


class ShanghaiPreferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    city: Literal['shanghai']
    timezone: Literal['Asia/Shanghai']
    start_date: date
    days: Annotated[int, Field(strict=True, ge=1, le=5)]
    day_start: Annotated[str, Field(pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')]
    day_end: Annotated[str, Field(pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')]
    day_settings: dict[date, dict[str, str]] | None = None
    pace: Literal['relaxed', 'balanced', 'intensive']
    transport: Literal['mixed', 'walk', 'transit', 'taxi']
    transport_modes: list[TravelMode] | None = Field(default=None, min_length=1, max_length=3)
    group: Group
    primary_interests: list[Interest] = Field(max_length=8)
    secondary_interests: list[Interest] = Field(max_length=8)
    required_ids: list[Annotated[str, Field(min_length=1, max_length=512)]] = Field(max_length=60)
    budget_amount: float = Field(strict=True, ge=0, le=10_000_000, allow_inf_nan=False)
    budget_currency: Literal['CNY', 'RUB', 'USD', 'EUR']
    budget_basis: Literal['per_group', 'per_person']

    @model_validator(mode='after')
    def consistent(self):
        people = self.group.adult + self.group.child + self.group.senior
        interests = self.primary_interests + self.secondary_interests
        start = time.fromisoformat(self.day_start)
        end = time.fromisoformat(self.day_end)
        if not people or not interests or len(set(interests)) != len(interests):
            raise ValueError('Проверьте состав группы и интересы.')
        if end.hour * 60 + end.minute - start.hour * 60 - start.minute < 60:
            raise ValueError('Дневное окно должно быть не короче часа.')
        if self.start_date > date.max - timedelta(days=self.days):
            raise ValueError('Недопустимая дата поездки.')
        dates = {self.start_date + timedelta(days=i) for i in range(self.days)}
        for day, settings in (self.day_settings or {}).items():
            if day not in dates or not settings or not set(settings).issubset({'day_start', 'day_end'}):
                raise ValueError('Недопустимая индивидуальная настройка дня.')
            start_value = settings.get('day_start', self.day_start)
            end_value = settings.get('day_end', self.day_end)
            try:
                start_minutes = time.fromisoformat(start_value)
                end_minutes = time.fromisoformat(end_value)
            except (TypeError, ValueError):
                raise ValueError('Неверное время индивидуального дня.') from None
            if ((end_minutes.hour * 60 + end_minutes.minute) -
                    (start_minutes.hour * 60 + start_minutes.minute)) < 60:
                raise ValueError('Индивидуальное дневное окно должно быть от 60 минут.')
        if self.group.pet and self.transport == 'transit':
            raise ValueError('Питомцы не поддерживаются в режиме общественного транспорта.')
        if self.transport_modes is not None:
            expected = self.transport_modes[0] if len(self.transport_modes) == 1 else 'mixed'
            if len(set(self.transport_modes)) != len(self.transport_modes) or expected != self.transport:
                raise ValueError('Проверьте сочетание выбранных способов передвижения.')
        if self.options()['budget_cny'] > 10_000_000:
            raise ValueError('Бюджет превышает предел маршрутизатора.')
        return self

    def options(self):
        result = self.model_dump(mode='json', exclude={'city', 'timezone', 'budget_amount', 'budget_currency', 'budget_basis'})
        people = self.group.adult + self.group.child + self.group.senior
        result['budget_cny'] = round(self.budget_amount * FX[self.budget_currency] * (people if self.budget_basis == 'per_person' else 1), 2)
        result['interests'] = self.primary_interests + self.secondary_interests
        if self.transport_modes is None:
            result.pop('transport_modes')
        return result


@lru_cache(maxsize=1)
def assistant():
    return ShanghaiAssistantService()


class MoscowPreferences(ShanghaiPreferences):
    city: Literal['moscow']
    timezone: Literal['Europe/Moscow']
    visit_minutes: Annotated[int, Field(strict=True, ge=15, le=480)] | None = None


def init_routes():
    with database() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS planned_trips (
            route_id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            city TEXT NOT NULL, version INTEGER NOT NULL, poi_snapshot_id TEXT NOT NULL,
            preferences TEXT NOT NULL, options TEXT NOT NULL, plan TEXT NOT NULL,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        )''')
        db.execute('CREATE INDEX IF NOT EXISTS planned_trips_owner ON planned_trips(owner_id, updated_at)')


def owned_route(route_id, owner_id):
    with database() as db:
        row = db.execute('SELECT * FROM planned_trips WHERE route_id=? AND owner_id=?', (route_id, owner_id)).fetchone()
    if row is None:
        raise HTTPException(404, 'Маршрут не найден.')
    return {key: json.loads(row[key]) if key in {'plan', 'options', 'preferences'} else row[key]
            for key in row.keys() if key != 'owner_id'}


@router.get('/api/planned-trips')
def trips(user: dict = Depends(current_user)):
    with database() as db:
        rows = db.execute('''SELECT route_id, city, version, created_at, updated_at,
                            json_extract(options, '$.start_date') AS start_date
                            FROM planned_trips WHERE owner_id=? ORDER BY updated_at DESC, route_id''', (user['id'],))
        return {'trips': [dict(row) for row in rows]}


@router.get('/api/planned-trips/{route_id}')
def read_trip(route_id: str, user: dict = Depends(current_user)):
    return owned_route(route_id, user['id'])


@router.post('/api/planned-trips', status_code=201, dependencies=[Depends(same_origin)])
def plan_trip(preferences: Annotated[ShanghaiPreferences | MoscowPreferences, Field(discriminator='city')],
              user: dict = Depends(current_user)):
    if preferences.city == 'moscow':
        from backend.moscow import plan_moscow
        plan, options, snapshot = plan_moscow(preferences)
        return store_trip(preferences, user, plan, options, snapshot)
    service = assistant()
    options = preferences.options()
    known = {poi['id'] for poi in service.data['pois']}
    if not set(options['required_ids']) <= known:
        raise HTTPException(422, 'Обязательное место отсутствует в каталоге Шанхая.')
    try:
        plan = bridge_call({'action': 'plan', 'options': options})
    except AssistantError as error:
        raise HTTPException(503 if 'UNAVAILABLE' in error.code else 422, human_error(error.code)) from error
    return store_trip(preferences, user, plan, options, service.snapshot)


def store_trip(preferences, user, plan, options, snapshot):
    route_id, now = uuid4().hex, int(timestamp())
    with database() as db:
        db.execute('INSERT INTO planned_trips VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)', (
            route_id, user['id'], preferences.city, snapshot, preferences.model_dump_json(),
            json.dumps(options), json.dumps(plan), now, now,
        ))
    return owned_route(route_id, user['id'])


@router.get('/trip/shanghai', include_in_schema=False)
def shanghai_page(route: str | None = None):
    from urllib.parse import urlencode
    return RedirectResponse('/trip/result?' + urlencode({'route': route}) if route else '/trip/new?city=shanghai')


@router.get('/api/planned-trips/catalog/{city}')
def catalog(city: Literal['moscow', 'shanghai'], include_legacy: bool = False):
    from backend.moscow import moscow_pois, moscow_v7
    places = moscow_v7()[0] if city == 'moscow' else assistant().data['pois']
    if city == 'moscow' and include_legacy:
        places = [*places, *moscow_pois()]
    return [{'id': poi['id'], 'name': (poi.get('names') or {}).get('ru') or poi['name'], 'kind': poi.get('kind'),
             'lat': poi.get('lat', (poi.get('coordinates') or {}).get('latitude')),
             'lon': poi.get('lon', (poi.get('coordinates') or {}).get('longitude'))} for poi in places]


_bindings = {}
_bindings_lock = threading.Lock()


def assistant_binding(request, route, create):
    service = assistant_for(route)
    key = (route['city'], token_hash(request.cookies[COOKIE_NAME]), route['route_id'], route['version'])
    with _bindings_lock, service.lock:
        for old_key, old_token in list(_bindings.items()):
            if old_key[0] == route['city'] and old_token not in service.sessions:
                del _bindings[old_key]
        if key not in _bindings and not create:
            raise HTTPException(409, 'Версия маршрута изменилась. Откройте поездку заново.')
        token, state = service.session(_bindings.get(key))
        _bindings[key] = token
        return token, state


def assistant_for(route):
    if route['city'] == 'moscow':
        from backend.moscow import moscow_assistant
        return moscow_assistant()
    return assistant()


@router.get('/api/planned-trips/{route_id}/assistant/status')
def assistant_status(route_id: str, request: Request, user: dict = Depends(current_user)):
    route = owned_route(route_id, user['id'])
    service = assistant_for(route)
    if not service.configured:
        return {'available': False, 'reason': 'Задайте OPENROUTER_API_KEY в окружении терминала, из которого запускается FastAPI, и перезапустите сервер. Без ключа маршруты работают, чат отключён.'}
    if route['poi_snapshot_id'] != service.snapshot or route['plan']['status'] == 'infeasible':
        return {'available': False, 'reason': 'Для этой версии маршрута чат недоступен. Постройте новый выполнимый маршрут.'}
    try:
        _, state = assistant_binding(request, route, True)
    except AssistantError as error:
        raise HTTPException(503, human_error(error.code)) from error
    response = {'available': True, 'csrf_token': state['csrf'], 'mode': service.mode,
                'conversation': route['options'].get('conversation', [])}
    if route['city'] == 'moscow':
        # Let the browser confirm which server-side named-POI handler is live;
        # stale FastAPI processes otherwise make fresh ZIP installs appear broken.
        response['agent_version'] = 'moscow-offline-v9'
        response['mode'] = 'local' if not service.llm_configured else service.mode
    return response


@router.post('/api/planned-trips/{route_id}/assistant/{action}', dependencies=[Depends(same_origin)])
async def assistant_action(route_id: str, action: Literal['register', 'message', 'decide'], request: Request,
                           user: dict = Depends(current_user)):
    if request.headers.get('content-type', '').split(';')[0] != 'application/json':
        raise HTTPException(415, 'Требуется JSON.')
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 1024 * 1024:
            raise HTTPException(413, 'Слишком большой запрос.')
        data.extend(chunk)
    try:
        body = json.loads(data, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    except (ValueError, AssistantError) as error:
        raise HTTPException(400, 'Некорректный JSON.') from error
    if not isinstance(body, dict):
        raise HTTPException(400, 'Некорректный запрос.')
    return await run_in_threadpool(apply_assistant, route_id, action, request, user, body)


def apply_assistant(route_id, action, request, user, body):
    route = owned_route(route_id, user['id'])
    service = assistant_for(route)
    if route['poi_snapshot_id'] != service.snapshot:
        raise HTTPException(409, 'Каталог изменился. Постройте новый маршрут.')
    try:
        token, state = assistant_binding(request, route, False)
        with state['lock']:
            csrf = request.headers.get('X-TripWise-CSRF', '')
            if not secrets.compare_digest(state['csrf'].encode(), csrf.encode()):
                raise AssistantError('CSRF_REJECTED')
            current = owned_route(route_id, user['id'])
            if current['version'] != route['version']:
                raise HTTPException(409, 'Маршрут уже изменён. Откройте поездку заново.')
            if action == 'register':
                if state.get('registration'):
                    return state['registration']
                body = {'request_id': body.get('request_id'), 'plan': route['plan'], 'options': route['options']}
                if route['city'] == 'moscow':
                    body.update({field: route[field] for field in ('preferences', 'route_id', 'version')})
                    state['route_options'] = route['options']
            if route['city'] == 'moscow' and action == 'message':
                state['moscow_history'] = current['options'].get('conversation', [])
            result = service.request(token, csrf, action, body)
            if (route['city'] == 'moscow' and action == 'message' and isinstance(body.get('text'), str)
                    and body.get('route_id') == route_id and body.get('base_version') == route['version']
                    and 0 < len(body['text'].strip()) <= 3000 and result.get('message')):
                append_moscow_conversation(route_id, user['id'], route['version'], body, result['message'])
            if result['type'] == 'registered':
                state['registration'] = {field: result[field] for field in ('type', 'route_id', 'version', 'poi_snapshot_id')}
                return state['registration']
            if result['type'] == 'confirmed' and result['version'] <= state['registration']['version']:
                if result['version'] != state['registration']['version']:
                    raise HTTPException(409, 'Маршрут уже изменён. Откройте поездку заново.')
                return result
            if result['type'] == 'confirmed':
                plan = {**result['plan'], 'ranking': route['plan'].get('ranking', [])}
                with database() as db:
                    changed = db.execute('''UPDATE planned_trips SET version=version+1, plan=?,
                                            options=CASE WHEN city='moscow' THEN json_set(?, '$.conversation',
                                              json(COALESCE(json_extract(options, '$.conversation'), '[]'))) ELSE ? END,
                                            updated_at=?
                                            WHERE route_id=? AND owner_id=? AND version=?''', (
                        json.dumps(plan), json.dumps(result['options']), json.dumps(result['options']),
                        int(timestamp()), route_id, user['id'], route['version'],
                    )).rowcount
                if not changed:
                    raise HTTPException(409, 'Маршрут уже изменён. Откройте поездку заново.')
                state['registration']['version'] = result['version']
                session_key = token_hash(request.cookies[COOKIE_NAME])
                with _bindings_lock:
                    _bindings.pop((route['city'], session_key, route_id, route['version']), None)
                    _bindings[(route['city'], session_key, route_id, route['version'] + 1)] = token
            return result
    except AssistantError as error:
        raise HTTPException(403 if error.code in {'SESSION_REQUIRED', 'CSRF_REJECTED'} else 400, human_error(error.code)) from error


def append_moscow_conversation(route_id, owner_id, version, body, message):
    # Запись нескольких вкладок сериализуется после LLM, без блокировки базы на время запроса.
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT options FROM planned_trips WHERE route_id=? AND owner_id=? AND version=?',
                         (route_id, owner_id, version)).fetchone()
        if row is None:
            raise HTTPException(409, 'Маршрут уже изменён. Откройте поездку заново.')
        options = json.loads(row['options'])
        history = options.get('conversation', [])
        if any(item.get('request_id') == body['request_id'] for item in history):
            return
        history.extend([{'role': 'user', 'content': body['text'].strip()},
                        {'role': 'assistant', 'content': message, 'request_id': body['request_id']}])
        options['conversation'] = history[-24:]
        db.execute('UPDATE planned_trips SET options=? WHERE route_id=? AND owner_id=?',
                   (json.dumps(options), route_id, owner_id))
