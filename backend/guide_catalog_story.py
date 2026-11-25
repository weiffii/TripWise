"""Короткий рассказ о месте на основе данных каталога TripWise."""

from math import asin, cos, radians, sin, sqrt
import re
from urllib.parse import urlsplit

from backend.place_catalog import city_places, place_summary


CATALOG_CATEGORIES = {'museums', 'history'}
CITY_NAMES = {'moscow': 'Москва', 'shanghai': 'Шанхай'}
LANDMARKS = {
    'moscow': ('osm-r-1360641', 'osm-r-2421978', 'moscow-cultural-609'),
    'shanghai': ('shanghai-demo:bund', 'shanghai-demo:yu-garden', 'shanghai-demo:shanghai-museum'),
}
OSM_TYPES = {'node', 'way', 'relation'}
CATALOG_URL = 'https://github.com/weiffii/TripWise'
HISTORIC_OBSERVATIONS = (
    (r'\b(?:памятник|монумент|мемориал)\b',
     'Рассмотрите памятник и основание отдельно. Если есть надпись, сравните, что она сообщает о посвящении '
     'и что о нём можно понять по самой композиции.'),
    (r'\b(?:храм|собор|церковь|монастырь)\b',
     'Если здание видно с улицы, сравните очертания входа и верхней части. Обратите внимание, как окна, '
     'силуэт и отделка влияют на то, что выделяется издалека.'),
    (r'\b(?:усадьба|дворец|особняк|палаты)\b',
     'Попробуйте найти главный вход со стороны улицы. Если часть территории доступна, посмотрите, '
     'как постройки расположены относительно входа и открытых пространств.'),
    (r'\b(?:площадь|набережная|улица|переулок)\b',
     'Присмотритесь к тому, где пространство открывается, а где становится теснее. Обратите внимание '
     'на линии фасадов и на то, какие здания видны с разных сторон.'),
)


def clean(value, limit):
    if not isinstance(value, str):
        return ''
    return ' '.join(value.split())[:limit].strip(' ,.;')


def safe_source(value):
    if not isinstance(value, str) or len(value) > 1000 or re.search(r'[\s\\]', value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme == 'https' and url.hostname and url.username is None and url.password is None:
            return value
    except ValueError:
        pass
    return None


def catalog_context(raw, city):
    source_ids = raw.get('source_ids') or {}
    element, osm_id = source_ids.get('osm_type'), str(source_ids.get('osm_id'))
    osm = f'https://www.openstreetmap.org/{element}/{osm_id}' if element in OSM_TYPES and osm_id.isascii() and osm_id.isdigit() else None
    url = osm or next((link for key in ('coordinate_source_url', 'source_url', 'source')
                      if (link := safe_source(raw.get(key)))), CATALOG_URL)
    is_osm = urlsplit(url).hostname in {'openstreetmap.org', 'www.openstreetmap.org'}
    location = clean(raw.get('district') or (raw.get('area') if city == 'shanghai' else None), 100)
    return {
        'area': location if location.casefold() != CITY_NAMES[city].casefold() else '',
        'address': clean(raw.get('address'), 240),
        'source': {
            'title': 'OpenStreetMap' if is_osm else 'Источник карточки TripWise',
            'url': url,
            'attribution': ('© OpenStreetMap contributors. Название и положение по каталогу TripWise.'
                            if is_osm else 'Название, категория и положение по каталогу TripWise.'),
            'license': 'ODbL 1.0' if is_osm else None,
            'license_url': 'https://www.openstreetmap.org/copyright' if is_osm else None,
        },
    }


def nearby_landmark(poi):
    coords = poi['coordinates']
    lat, lon = map(radians, (coords['latitude'], coords['longitude']))
    candidates = []
    catalog = city_places(poi['city'])
    for key in LANDMARKS[poi['city']]:
        if key == poi['poi_id'] or key not in catalog:
            continue
        place = place_summary(catalog[key])
        if place['name'].casefold() == poi['name'].casefold():
            continue
        other_lat, other_lon = map(radians, (place['lat'], place['lon']))
        hav = sin((lat - other_lat) / 2) ** 2 + cos(lat) * cos(other_lat) * sin((lon - other_lon) / 2) ** 2
        distance = 12_742_000 * asin(sqrt(min(1, hav)))
        if 150 <= distance <= 3000:
            candidates.append((distance, place['name']))
    if not candidates:
        return ''
    distance, name = min(candidates)
    value = round(distance / 100) * 100
    return f'По данным карты между этой точкой и местом «{name}» около {value} метров по прямой.'


def catalog_story(poi):
    if poi['category'] not in CATALOG_CATEGORIES:
        return None
    name = poi['name']
    city = CITY_NAMES[poi['city']]
    context = poi.get('catalog_context') or {}
    museum = poi['category'] == 'museums'
    kind = 'музейная остановка' if museum else 'историческая остановка'
    intro = f'«{name}» — {kind} в маршруте по городу {city}.'
    facts = []
    if context.get('address'):
        facts.append({'title': 'Адрес по каталогу', 'text': f'В каталоге указан адрес: {context["address"]}.'})
    elif context.get('area'):
        facts.append({'title': 'Расположение', 'text': f'Ориентир по каталогу: {context["area"]}.'})
    if landmark := nearby_landmark(poi):
        facts.append({'title': 'Ориентир на карте', 'text': landmark})
    tip = ('Если экспозиция открыта, выберите один предмет и прочитайте его подпись. Сведения о времени и месте '
           'создания помогают понять, что связывает этот предмет с темой музея.' if museum else
           'Осмотрите место с доступной стороны. Если есть памятная доска, найдите на ней, кому или чему посвящено '
           'это место. Если перед вами здание, сравните нижнюю часть фасада с верхней и присмотритесь к материалам.')
    if not museum:
        tip = next((observation for pattern, observation in HISTORIC_OBSERVATIONS
                    if re.search(pattern, name, re.IGNORECASE)), tip)
    facts.append({'title': 'На что обратить внимание', 'text': tip})
    locations = [fact['text'] for fact in facts[:-1]]
    short = ' '.join([intro, *locations[:1], tip])
    full = '\n\n'.join([intro, ' '.join(locations), tip]) if len(locations) > 1 else short
    return {
        'short_text': short, 'full_text': full, 'facts': facts,
        'sources': [context.get('source') or {'title': 'Каталог TripWise', 'url': CATALOG_URL,
                                             'attribution': 'Название и категория из каталога TripWise.'}],
        'match': {'wikidata_id': None, 'confidence': 'catalog', 'method': 'catalog_id'},
        'image': None, 'coverage': 'catalog',
    }
