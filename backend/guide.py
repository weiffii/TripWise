"""Аудиогиды маршрута и общий кэш проверенных материалов."""

from datetime import datetime, timezone
from math import isfinite
import json
import re
from threading import Lock
from time import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from backend.accounts import current_user, database, same_origin
from backend.guide_stories import curated_story
from backend.guide_wikimedia import WikimediaUnavailable, build_story
from backend.place_catalog import city_places, legacy_places, place_summary


router = APIRouter(prefix='/api/guide', tags=['Аудиогид'], dependencies=[Depends(current_user)])
City = Literal['moscow', 'shanghai']
POI_ID_MAX = 512
LANGUAGE = 'ru'
VERSION = '1'
POSITIVE_TTL = 30 * 86400
NEGATIVE_TTL = 86400
STALE_TTL = 180 * 86400
LOOKUP_LOCKS = [Lock() for _ in range(32)]
GUIDE_CATEGORIES = {'museums', 'history', 'nature', 'unusual'}
EXCLUDED_KINDS = {
    'restaurant', 'cafe', 'bar', 'food_court', 'shopping_center', 'shop', 'market',
    'viewpoint', 'amusement_park', 'attraction_ride', 'entertainment', 'hotel', 'station', 'transport',
}
EXCLUDED_NAMES = re.compile(
    r'^(?:тц|трц|торговый (?:центр|комплекс)|смотровая площадка|колесо обозрения|'
    r'парк аттракционов|развлекательный центр|батутный центр|аквапарк|'
    r'ресторан|кофейня|кафе|бар|отель|гостиница)(?:\b|$)', re.IGNORECASE)
QID = re.compile(r'Q[1-9][0-9]*\Z')


class AvailabilityRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    poi_ids: list[Annotated[str, Field(min_length=1, max_length=POI_ID_MAX)]] = Field(max_length=256)


def init_guide():
    with database() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS guide_stories (
            city TEXT NOT NULL, poi_id TEXT NOT NULL, language TEXT NOT NULL, version TEXT NOT NULL,
            updated_at REAL NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (city, poi_id, language, version)
        )''')


def get_poi(city, poi_id):
    raw = city_places(city).get(poi_id)
    if raw is None and city == 'moscow':
        raw = legacy_places().get(poi_id)
    if raw is None:
        return None
    point = place_summary(raw)
    source_ids = raw.get('source_ids') or {}
    provenance = raw.get('provenance') or {}
    category = raw.get('primary_category') or next(iter(raw.get('categories') or ()), None)
    wikidata_id = source_ids.get('wikidata_id') or raw.get('wikidata_id') or raw.get('wikidata')
    aliases = dict.fromkeys([*(raw.get('alternative_names') or ()), *(raw.get('names') or {}).values(),
                            raw.get('name_original'), raw.get('name_ru')])
    return {
        'poi_id': poi_id, 'city': city, 'name': point['name'], 'category': category,
        'kind': point['kind'], 'coordinates': {'latitude': point['lat'], 'longitude': point['lon']},
        'aliases': [alias for alias in aliases if isinstance(alias, str) and alias and alias != point['name']],
        'wikidata_id': wikidata_id if isinstance(wikidata_id, str) and QID.fullmatch(wikidata_id) else None,
        'synthetic': bool(provenance.get('is_synthetic') or 'synthetic' in provenance.get('identity', '')
                          or raw.get('source_type') == 'synthetic'),
    }


def eligible(poi):
    if (poi['category'] not in GUIDE_CATEGORIES or poi.get('kind') in EXCLUDED_KINDS or poi.get('synthetic')
            or EXCLUDED_NAMES.match(poi['name'].strip())):
        return False
    coords = poi.get('coordinates') or {}
    lat, lon = coords.get('latitude'), coords.get('longitude')
    return (type(lat) in (int, float) and isfinite(lat) and -90 <= lat <= 90
            and type(lon) in (int, float) and isfinite(lon) and -180 <= lon <= 180)


def response_for(poi, status, story=None, updated=None, stale=False):
    return {
        **{key: poi[key] for key in ('poi_id', 'city', 'name', 'category')},
        'status': status, 'language': LANGUAGE, 'version': VERSION,
        'short_text': None, 'full_text': None, 'facts': [], 'sources': [],
        'match': None, 'image': None, **(story or {}),
        'updated_at': datetime.fromtimestamp(updated, timezone.utc).isoformat() if updated is not None else None,
        'stale': stale,
    }


def stored_stories(city, ids):
    if not ids:
        return {}
    slots = ','.join('?' for _ in ids)
    with database() as db:
        rows = db.execute(f'''SELECT poi_id, updated_at, payload FROM guide_stories
            WHERE city = ? AND language = ? AND version = ? AND poi_id IN ({slots})''',
                          (city, LANGUAGE, VERSION, *ids)).fetchall()
    return {row['poi_id']: (row['updated_at'], json.loads(row['payload'])) for row in rows}


def save_story(city, poi_id, story, now):
    with database() as db:
        db.execute('''INSERT INTO guide_stories VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (city, poi_id, language, version) DO UPDATE SET
            updated_at = excluded.updated_at, payload = excluded.payload''',
                   (city, poi_id, LANGUAGE, VERSION, now, json.dumps(story, ensure_ascii=False)))


@router.post('/availability', dependencies=[Depends(same_origin)])
def availability(city: City, body: AvailabilityRequest):
    ids = list(dict.fromkeys(body.poi_ids))
    cached = stored_stories(city, ids)
    now = time()
    items = []
    for poi_id in ids:
        poi = get_poi(city, poi_id)
        status = 'excluded'
        if poi and eligible(poi):
            status = 'unknown'
            if poi_id in cached:
                updated, story = cached[poi_id]
                if story and now - updated <= STALE_TTL:
                    status = 'available'
                elif not story and now - updated < NEGATIVE_TTL:
                    status = 'unavailable'
            if status != 'available' and curated_story(city, poi_id):
                status = 'available'
        items.append({'poi_id': poi_id, 'status': status, 'category': poi['category'] if poi else None})
    return {'items': items}


@router.get('/poi/{poi_id}')
def point_guide(city: City, poi_id: Annotated[str, Path(min_length=1, max_length=POI_ID_MAX)]):
    poi = get_poi(city, poi_id)
    if poi is None:
        raise HTTPException(404, 'Место отсутствует в каталоге.')
    if not eligible(poi):
        return response_for(poi, 'excluded')
    # A repeat click or concurrent request for the same object shares the result.
    with LOOKUP_LOCKS[hash((city, poi_id)) % len(LOOKUP_LOCKS)]:
        now = time()
        updated, story = stored_stories(city, [poi_id]).get(poi_id, (0, None))
        if updated and now - updated < (POSITIVE_TTL if story else NEGATIVE_TTL):
            backup = story or curated_story(city, poi_id)
            return response_for(poi, 'available' if backup else 'unavailable', backup, updated)
        try:
            refreshed = build_story(poi)
        except WikimediaUnavailable:
            if story and now - updated <= STALE_TTL:
                return response_for(poi, 'available', story, updated, stale=True)
            backup = curated_story(city, poi_id)
            if backup:
                return response_for(poi, 'available', backup)
            return response_for(poi, 'temporarily_unavailable')
        updated = time()
        save_story(city, poi_id, refreshed, updated)
        story = refreshed or curated_story(city, poi_id)
        return response_for(poi, 'available' if story else 'unavailable', story, updated)
