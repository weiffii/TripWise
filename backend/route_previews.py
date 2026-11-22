"""Предпросмотр маршрута: память сеанса до явного сохранения пользователем."""
from copy import deepcopy
from datetime import datetime, time, timedelta
import json
import threading
from time import time as timestamp
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.accounts import COOKIE_NAME, current_user, database, same_origin, token_hash
from backend.drafts import TripPreferences, require_owner_context
from ai_ds.day_windows import MAX_TRIP_DAYS

router = APIRouter(tags=['Предпросмотр маршрута'])
PREVIEW_TTL = 3600
MAX_PREVIEWS = 128
_lock = threading.RLock()
_previews = {}


class PreviewBuild(BaseModel):
    model_config = ConfigDict(extra='forbid')
    form_preferences: dict
    source_preview_id: str | None = Field(default=None, min_length=1, max_length=128)


class RouteVersion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(strict=True, ge=1)


def session_key(request):
    return token_hash(request.cookies[COOKIE_NAME])


def _prune():
    now = timestamp()
    for key in list(_previews):
        if _previews[key]['expires_at'] <= now:
            del _previews[key]


def _entry(preview_id, user_id, request):
    _prune()
    row = _previews.get(preview_id)
    if row is None or row['user_id'] != user_id or row['session'] != session_key(request):
        raise HTTPException(404, 'Предпросмотр недоступен или истёк. Постройте маршрут заново.')
    return row


def preview_owned(preview_id, user_id, request):
    with _lock:
        return deepcopy(_entry(preview_id, user_id, request)['route'])


def public_route(route, user):
    return {**deepcopy(route), 'owner_context': str(user['id'])}


def _put_preview(route, form, user, request, source=None):
    with _lock:
        _prune()
        if len(_previews) >= MAX_PREVIEWS:
            raise HTTPException(503, 'Слишком много открытых предпросмотров. Повторите позже.')
        preview_id = 'preview-' + uuid4().hex
        route = {**deepcopy(route), 'route_id': preview_id, 'preview_id': preview_id,
                 'is_preview': True, 'form_preferences': deepcopy(form)}
        _previews[preview_id] = {'route': route, 'user_id': user['id'], 'session': session_key(request),
                                'expires_at': timestamp() + PREVIEW_TTL, 'source': deepcopy(source),
                                'saved_route_id': None, 'saved_version': None}
        return public_route(route, user)


def form_from_engine(route):
    """Обратимый интерфейс старого сохранённого маршрута для новой анкеты."""
    if route['options'].get('_fullstack_preferences'):
        return merge_form_windows(route['options']['_fullstack_preferences'], route['options'])
    p = route['preferences']
    if p['group'].get('pet'):
        raise HTTPException(422, 'Эта старая поездка содержит питомца. Новая анкета пока не поддерживает изменение таких поездок.')
    start = datetime.fromisoformat(p['start_date'])
    end = start + timedelta(days=p['days'] - 1)
    settings = {**(p.get('day_settings') or {}), **(route['options'].get('day_settings') or {})}
    first_time = settings.get(start.date().isoformat(), {}).get('day_start', p['day_start'])
    last_time = settings.get(end.date().isoformat(), {}).get('day_end', p['day_end'])
    counts = {'adults': p['group']['adult'], 'children': p['group']['child'], 'seniors': p['group']['senior']}
    modes = p.get('transport_modes') or (['walk', 'transit', 'taxi'] if p['transport'] == 'mixed' else [p['transport']])
    form = {'schema_version': 'fullstack-draft-0.2', 'country_code': 'RU' if p['city'] == 'moscow' else 'CN',
            'city': 'Москва' if p['city'] == 'moscow' else 'Шанхай', 'timezone': p['timezone'],
            'first_walk_at': start.date().isoformat() + 'T' + first_time,
            'last_walk_end_at': end.date().isoformat() + 'T' + last_time,
            'travelers': sum(counts.values()), 'traveler_counts': counts,
            'budget_amount': str(p['budget_amount']), 'budget_currency': p['budget_currency'],
            'budget_basis': 'group' if p['budget_basis'] == 'per_group' else 'per_person',
            'budget_scope': 'trip_food_visits_and_transport', 'primary_interests': p['primary_interests'],
            'secondary_interests': p['secondary_interests'], 'pace': p['pace'],
            'transport_modes': [{'walk': 'walking', 'transit': 'public_transit', 'taxi': 'car'}[m] for m in modes],
            'required_poi_ids': p['required_ids']}
    return merge_form_windows(form, {'day_settings': settings})


def merge_form_windows(form, options):
    result = deepcopy(form)
    first, last = form['first_walk_at'][:10], form['last_walk_end_at'][:10]
    overrides = result.get('day_settings') or {}
    for day, settings in (options.get('day_settings') or {}).items():
        if first < day < last:
            window = {key: settings[key] for key in ('day_start', 'day_end') if key in settings}
            if window and (day in overrides or window.get('day_start', '09:00') != '09:00'
                           or window.get('day_end', '21:00') != '21:00'):
                overrides[day] = {**overrides.get(day, {}), **window}
    if overrides:
        result['day_settings'] = overrides
    return result


def engine_preferences(raw):
    from backend.shanghai import ShanghaiPreferences, MoscowPreferences, catalog
    try:
        form = TripPreferences.model_validate(raw, context={'route_generation': True})
        form.budget_scope = 'trip_food_visits_and_transport'
    except ValidationError:
        raise HTTPException(422, 'Проверьте поля анкеты: время, компанию, бюджет и выбранные интересы.') from None
    active = {('RU', 'Москва'): 'moscow', ('CN', 'Шанхай'): 'shanghai'}
    city = active.get((form.country_code, form.city))
    if city is None:
        raise HTTPException(422, 'Это направление в разработке. Сейчас доступны Москва и Шанхай.')
    supported = {'RUB'} if city == 'moscow' else {'RUB', 'USD', 'EUR', 'CNY'}
    if form.budget_currency not in supported:
        raise HTTPException(422, 'Для Москвы выберите рубли; для Шанхая доступны рубли, доллары, евро и юани.')
    first, last = form.first_walk_at, form.last_walk_end_at
    days = (last.date() - first.date()).days + 1
    if not 1 <= days <= MAX_TRIP_DAYS:
        raise HTTPException(422, 'Маршрутизатор поддерживает поездки от 1 до 14 календарных дней.')
    if any(value > 30 for value in form.traveler_counts.model_dump().values()):
        raise HTTPException(422, 'Сейчас маршрутизатор поддерживает до 30 человек в каждой возрастной группе.')
    # Внутренние дни 09:00–21:00; первый/последний ограничены заданными пользователем окнами.
    settings = {}
    for offset in range(days):
        day = first.date() + timedelta(days=offset)
        start = first.strftime('%H:%M') if offset == 0 else '09:00'
        finish = last.strftime('%H:%M') if offset == days - 1 else '21:00'
        override = (form.day_settings or {}).get(day, {})
        start, finish = override.get('day_start', start), override.get('day_end', finish)
        if datetime.combine(day, time.fromisoformat(finish)) - datetime.combine(day, time.fromisoformat(start)) < timedelta(hours=1):
            raise HTTPException(422, 'Для каждого дня нужно минимум 60 минут.')
        settings[day.isoformat()] = {'day_start': start, 'day_end': finish}
    modes = [{'walking': 'walk', 'public_transit': 'transit', 'car': 'taxi'}[m] for m in form.transport_modes]
    allowed = {p['id'] for p in catalog(city)}
    if not set(form.required_poi_ids) <= allowed:
        raise HTTPException(422, 'Выберите обязательные места из актуального каталога выбранного города.')
    payload = {'city': city, 'timezone': form.timezone, 'start_date': first.date().isoformat(), 'days': days,
               'day_start': '09:00', 'day_end': '21:00', 'day_settings': settings,
               'pace': form.pace, 'transport': modes[0] if len(modes) == 1 else 'mixed', 'transport_modes': modes,
               'group': {'adult': form.traveler_counts.adults, 'child': form.traveler_counts.children,
                         'senior': form.traveler_counts.seniors, 'pet': 0},
               'primary_interests': form.primary_interests, 'secondary_interests': form.secondary_interests,
               'required_ids': form.required_poi_ids, 'budget_amount': float(form.budget_amount),
               'budget_currency': form.budget_currency, 'budget_basis': 'per_group' if form.budget_basis == 'group' else 'per_person'}
    try:
        preferences = (MoscowPreferences if city == 'moscow' else ShanghaiPreferences).model_validate(payload)
    except ValidationError:
        raise HTTPException(422, 'Параметры превышают ограничения маршрутизатора. Проверьте бюджет, время и компанию.') from None
    return form.model_dump(mode='json'), preferences


@router.post('/api/route-previews', dependencies=[Depends(same_origin)], status_code=201)
def generate_preview(data: PreviewBuild, request: Request, user: dict = Depends(current_user),
                     owner: str | None = Header(default=None, alias='X-TripWise-Draft-Owner')):
    from backend.shanghai import build_plan
    require_owner_context(owner, user)
    source = None
    if data.source_preview_id:
        with _lock:
            original = _entry(data.source_preview_id, user['id'], request)
            source = ({'route_id': original['saved_route_id'], 'version': original['saved_version']}
                      if original['saved_route_id'] else deepcopy(original['source']))
    form, preferences = engine_preferences(data.form_preferences)
    plan, options, snapshot = build_plan(preferences)
    options['_fullstack_preferences'] = form
    now = int(timestamp())
    route = {'city': preferences.city, 'version': 1, 'poi_snapshot_id': snapshot,
             'preferences': preferences.model_dump(mode='json'), 'options': options, 'plan': plan,
             'created_at': now, 'updated_at': now}
    return _put_preview(route, form, user, request, source)


@router.get('/api/route-previews/{preview_id}')
def read_preview(preview_id: str, request: Request, user: dict = Depends(current_user)):
    return public_route(preview_owned(preview_id, user['id'], request), user)


@router.post('/api/planned-trips/{route_id}/preview', dependencies=[Depends(same_origin)], status_code=201)
def fork_saved(route_id: str, data: RouteVersion, request: Request, user: dict = Depends(current_user),
               owner: str | None = Header(default=None, alias='X-TripWise-Draft-Owner')):
    from backend.shanghai import owned_route
    require_owner_context(owner, user)
    route = owned_route(route_id, user['id'])
    if route['version'] != data.version:
        raise HTTPException(409, 'Поездка уже изменена. Откройте её заново.')
    return _put_preview(route, form_from_engine(route), user, request,
                        {'route_id': route_id, 'version': route['version']})


@router.post('/api/route-previews/{preview_id}/save', dependencies=[Depends(same_origin)])
def save_preview(preview_id: str, data: RouteVersion, request: Request, user: dict = Depends(current_user),
                 owner: str | None = Header(default=None, alias='X-TripWise-Draft-Owner')):
    from backend.shanghai import owned_route
    require_owner_context(owner, user)
    with _lock:
        item = _entry(preview_id, user['id'], request)
        route = item['route']
        if route['version'] != data.version:
            raise HTTPException(409, 'Предпросмотр изменился. Обновите страницу маршрута.')
        if item['saved_route_id']:
            saved = owned_route(item['saved_route_id'], user['id'])
            if saved['version'] != item['saved_version']:
                raise HTTPException(409, 'Сохранённая поездка уже изменена. Откройте её заново.')
            return public_route({**saved, 'is_preview': False, 'form_preferences': form_from_engine(saved)}, user)
        source = item['source']
        now = int(timestamp())
        with database() as db:
            db.execute('BEGIN IMMEDIATE')
            if source:
                row = db.execute('SELECT version FROM planned_trips WHERE route_id=? AND owner_id=?',
                                 (source['route_id'], user['id'])).fetchone()
                if row is None:
                    raise HTTPException(404, 'Поездка удалена. Изменения не сохранены.')
                if row['version'] != source['version']:
                    raise HTTPException(409, 'Поездка изменена в другой вкладке. Откройте её заново.')
                route_id, version = source['route_id'], source['version'] + 1
                db.execute('''UPDATE planned_trips SET city=?,version=?,poi_snapshot_id=?,preferences=?,options=?,plan=?,updated_at=?
                              WHERE route_id=? AND owner_id=?''',
                           (route['city'], version, route['poi_snapshot_id'], json.dumps(route['preferences']),
                            json.dumps(route['options']), json.dumps(route['plan']), now, route_id, user['id']))
            else:
                route_id, version = uuid4().hex, 1
                db.execute('INSERT INTO planned_trips VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (route_id, user['id'], route['city'], version, route['poi_snapshot_id'],
                            json.dumps(route['preferences']), json.dumps(route['options']), json.dumps(route['plan']), now, now))
        item['saved_route_id'], item['saved_version'] = route_id, version
        saved = owned_route(route_id, user['id'])
        return public_route({**saved, 'is_preview': False, 'form_preferences': form_from_engine(saved)}, user)


@router.delete('/api/planned-trips/{route_id}', dependencies=[Depends(same_origin)], status_code=204)
def delete_saved(route_id: str, data: RouteVersion, user: dict = Depends(current_user),
                 owner: str | None = Header(default=None, alias='X-TripWise-Draft-Owner')):
    from fastapi import Response
    require_owner_context(owner, user)
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT version FROM planned_trips WHERE route_id=? AND owner_id=?', (route_id, user['id'])).fetchone()
        if row is None:
            raise HTTPException(404, 'Поездка не найдена.')
        if row['version'] != data.version:
            raise HTTPException(409, 'Поездка изменена. Обновите список.')
        db.execute('DELETE FROM planned_trips WHERE route_id=? AND owner_id=?', (route_id, user['id']))
    return Response(status_code=204)


def apply_preview_change(preview_id, user, request, version, plan, options):
    with _lock:
        item = _entry(preview_id, user['id'], request)
        route = item['route']
        if route['version'] != version or item['saved_route_id']:
            raise HTTPException(409, 'Предпросмотр уже изменён или сохранён. Откройте поездку заново.')
        # History belongs to preview memory too, never to SQL until explicit Save.
        history = route['options'].get('conversation', [])
        route['form_preferences'] = merge_form_windows(route['form_preferences'], options)
        route.update(plan=deepcopy(plan), options={**deepcopy(options), 'conversation': history,
                     '_fullstack_preferences': route['form_preferences']}, version=version + 1, updated_at=int(timestamp()))


def append_preview_conversation(preview_id, owner_id, request, version, body, message):
    with _lock:
        item = _entry(preview_id, owner_id, request)
        route = item['route']
        if route['version'] != version:
            raise HTTPException(409, 'Предпросмотр изменился. Откройте его заново.')
        history = route['options'].setdefault('conversation', [])
        if not any(row.get('request_id') == body['request_id'] for row in history):
            history.extend([{'role': 'user', 'content': body['text'].strip()},
                            {'role': 'assistant', 'content': message, 'request_id': body['request_id']}])
            route['options']['conversation'] = history[-24:]
