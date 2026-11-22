"""Локальный справочник интерфейса, не набор данных рекомендательного алгоритма."""

import json
from pathlib import Path

from fastapi import APIRouter

CATALOG_PATH = Path(__file__).resolve().parents[1] / 'frontend' / 'assets' / 'catalog.json'
CATALOG = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
CITIES = {(country['code'], city['name']): city for country in CATALOG['countries'] for city in country['cities']}
CURRENCY_CODES = frozenset(item['code'] for item in CATALOG['currencies'])
router = APIRouter(tags=['Справочник путешествия'])


@router.get('/api/catalog')
@router.get('/api/travel-catalog', include_in_schema=False)
def travel_catalog():
    return CATALOG
