"""Read-only Wikimedia enrichment for a place from the TripWise catalogue."""

import html
import json
import math
import re
import time
import unicodedata
from datetime import timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from http.client import HTTPException
from threading import BoundedSemaphore, Lock
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


WIKIDATA_URL = 'https://www.wikidata.org/w/api.php'
WIKIPEDIA_URL = 'https://ru.wikipedia.org/w/api.php'
COMMONS_URL = 'https://commons.wikimedia.org/w/api.php'
USER_AGENT = 'TripWiseGuide/1.0 (https://github.com/weiffii/TripWise)'
MAX_BYTES = 3 * 1024 * 1024
REQUEST_TIMEOUT = 4
MAX_RETRY_DELAY = 2
OUTAGE_PAUSE = 12
_REQUESTS = BoundedSemaphore(2)
_RATE_LOCK = Lock()
_NEXT_REQUEST = {}
_QID = re.compile(r'Q[1-9][0-9]{0,14}\Z')
_LANGUAGES = 'ru|en|zh|mul'
_CATEGORY_TERMS = {
    'museums': ('музе', 'галере', 'museum', 'gallery', '博物馆', '美术馆'),
    'history': ('историч', 'памятник', 'монумент', 'усадьб', 'дворец', 'дворц', 'храм',
                'собор', 'церк', 'улица', 'площадь', 'крепост', 'монастыр', 'мост', 'здание',
                'набережн', 'heritage', 'monument', 'historic', 'palace', 'temple', 'church',
                'building', 'embankment', '外滩'),
    'nature': ('парк', 'сад', 'сквер', 'заповед', 'лес', 'park', 'garden', '公园', '园林'),
    'entertainment': ('театр', 'цирк', 'кино', 'зоопарк', 'аттракцион', 'выставоч',
                      'theat', 'amusement', 'aquarium', 'zoo', 'exhibition', '剧院'),
    'shopping': ('торгов', 'рынок', 'универмаг', 'shopping', 'market', 'department store', '商场'),
    'cafes': ('ресторан', 'кафе', 'кофейн', 'restaurant', 'cafe', 'café', '餐厅'),
    'viewpoints': ('смотров', 'башн', 'небоскр', 'viewpoint', 'observat', 'tower', 'skyscraper', '观景'),
    'unusual': ('архитект', 'мост', 'павильон', 'здание', 'арт', 'скульптур', 'сооружен',
                'architect', 'bridge', 'pavilion', 'building', 'sculpture', '公馆'),
}
_CATEGORY_TYPES = {
    'museums': {'Q33506', 'Q207694', 'Q2087181', 'Q16735822'},
    'history': {'Q4989906', 'Q16970', 'Q16560', 'Q839954', 'Q23413',
                'Q57821', 'Q174782', 'Q2977', 'Q12280'},
    'nature': {'Q22698', 'Q22746', 'Q1107656', 'Q167346', 'Q46169'},
    'entertainment': {'Q24354', 'Q153562', 'Q41253', 'Q18674739', 'Q194195'},
    'shopping': {'Q11315', 'Q330284'},
    'cafes': {'Q11707', 'Q30022'},
    'viewpoints': {'Q12518', 'Q11303'},
    'unusual': {'Q41176', 'Q4989906', 'Q12280', 'Q276173', 'Q12518'},
}
_LARGE_CATEGORIES = {'nature', 'entertainment'}
_NON_PLACE_TYPES = {'Q4167410', 'Q13406463', 'Q5', 'Q34770', 'Q11424', 'Q16521'}
_COMMERCIAL_OR_TRANSPORT_TYPES = {
    'Q11707', 'Q30022', 'Q11315', 'Q330284', 'Q133215', 'Q194195', 'Q27686',
    'Q41253', 'Q928830', 'Q55488',
}
_COMMERCIAL_OR_TRANSPORT_DESCRIPTIONS = (
    'станция метро', 'станция московского метрополитена', 'станция метрополитена',
    'железнодорожная станция', 'железнодорожный вокзал', 'metro station', 'railway station',
    'историческая железнодорожная станция', 'historic railway station',
    'train station', 'торговый центр', 'торгово развлекательный центр', 'shopping mall',
    'shopping center', 'shopping centre', 'парк аттракционов', 'развлекательный центр',
    'amusement park', 'ресторан', 'кофейня', 'гостиница', 'restaurant', 'coffee shop',
)
_CULTURAL_CATEGORIES = {'museums', 'history', 'nature', 'unusual'}


class WikimediaUnavailable(RuntimeError):
    """Wikimedia could not be consulted reliably for this request."""


class _ArticleMismatch(RuntimeError):
    """The Russian sitelink resolves to a different subject."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


urlopen = build_opener(_NoRedirect()).open


def _retry_delay(value, default):
    if value:
        try:
            return max(0, int(value))
        except ValueError:
            try:
                stamp = parsedate_to_datetime(value)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                return max(0, stamp.timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                pass
    return default


def _defer_requests(endpoint, delay):
    with _RATE_LOCK:
        _NEXT_REQUEST[endpoint] = max(_NEXT_REQUEST.get(endpoint, 0), time.monotonic() + delay)


def _await_rate_limit(endpoint):
    with _RATE_LOCK:
        delay = _NEXT_REQUEST.get(endpoint, 0) - time.monotonic()
    if delay > MAX_RETRY_DELAY:
        raise WikimediaUnavailable('Wikimedia попросила отложить запросы.')
    if delay > 0:
        time.sleep(delay)


def _request(endpoint, params):
    if endpoint not in {WIKIDATA_URL, WIKIPEDIA_URL, COMMONS_URL}:
        raise ValueError('Unknown Wikimedia endpoint')
    query = {'format': 'json', 'formatversion': '2', 'maxlag': '5', **params}
    request = Request(endpoint + '?' + urlencode(query), headers={
        'User-Agent': USER_AGENT, 'Accept': 'application/json',
    })
    for attempt in range(2):
        unthrottled_failure = False
        if not _REQUESTS.acquire(timeout=REQUEST_TIMEOUT):
            raise WikimediaUnavailable('Все соединения Wikimedia заняты.')
        try:
            _await_rate_limit(endpoint)
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                body = response.read(MAX_BYTES + 1)
                retry_after = getattr(response, 'headers', {}).get('Retry-After')
            if len(body) > MAX_BYTES:
                raise WikimediaUnavailable('Ответ Wikimedia превышает допустимый размер.')
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise WikimediaUnavailable('Неожиданный формат ответа Wikimedia.')
            error = payload.get('error')
            if not error:
                return payload
            if isinstance(error, dict) and error.get('code') in {'maxlag', 'ratelimited', 'readonly'}:
                delay = _retry_delay(retry_after, 1 if error['code'] == 'ratelimited' else 5)
                _defer_requests(endpoint, delay)
            else:
                raise WikimediaUnavailable('Wikimedia вернула ошибку API.')
        except HTTPError as error:
            retry_after = error.headers.get('Retry-After') if error.headers else None
            error.close()
            if error.code not in {429, 500, 502, 503, 504}:
                raise WikimediaUnavailable('Wikimedia вернула ошибку HTTP.') from error
            delay = _retry_delay(retry_after, 1 if error.code == 429 else .25)
            unthrottled_failure = not retry_after and error.code != 429
            if retry_after or error.code in {429, 503}:
                _defer_requests(endpoint, delay)
        except (URLError, TimeoutError, OSError, HTTPException):
            delay = .25
            unthrottled_failure = True
        except (ValueError, RecursionError) as error:
            raise WikimediaUnavailable('Wikimedia вернула некорректный JSON.') from error
        finally:
            _REQUESTS.release()
        if attempt and unthrottled_failure:
            _defer_requests(endpoint, OUTAGE_PAUSE)
        if attempt or delay > MAX_RETRY_DELAY:
            break
        time.sleep(delay)
    raise WikimediaUnavailable('Wikimedia временно недоступна.')


def _norm(value):
    if not isinstance(value, str):
        return ''
    value = unicodedata.normalize('NFKC', value).casefold().replace('ё', 'е')
    return ' '.join(re.findall(r'[^\W_]+', value))


def _title_base(value):
    return _norm(re.sub(r'\s*\([^()]+\)\s*$', '', value)) if isinstance(value, str) else ''


def _pair(lat, lon):
    return (lat, lon) if (type(lat) in {int, float} and type(lon) in {int, float}
                          and -90 <= lat <= 90 and -180 <= lon <= 180) else None


def _distance(left, right):
    lat1, lon1 = map(math.radians, left)
    lat2, lon2 = map(math.radians, right)
    angle = math.sin((lat2 - lat1) / 2) ** 2 + (
        math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 12742000 * math.asin(math.sqrt(min(1, angle)))


def _map(value):
    return value if isinstance(value, dict) else {}


def _claim_values(entity, prop, *, unqualified=False):
    preferred, normal = [], []
    for statement in _map(entity.get('claims')).get(prop, []):
        snak = statement.get('mainsnak', {})
        if (snak.get('snaktype') != 'value' or statement.get('rank') == 'deprecated'
                or unqualified and statement.get('qualifiers')):
            continue
        value = snak.get('datavalue', {}).get('value')
        if value is not None:
            (preferred if statement.get('rank') == 'preferred' else normal).append(value)
    return preferred or normal


def _entity_map(ids, *, russian_labels_only=False):
    props = ({'props': 'labels', 'languages': 'ru'} if russian_labels_only else {
        'props': 'labels|aliases|descriptions|claims|sitelinks',
        'languages': _LANGUAGES, 'sitefilter': 'ruwiki',
    })
    data = _request(WIKIDATA_URL, {
        'action': 'wbgetentities', 'ids': '|'.join(ids),
        'redirects': 'no', **props,
    })
    entities = data.get('entities')
    if not isinstance(entities, dict):
        raise WikimediaUnavailable('В ответе Wikidata отсутствуют сведения об объектах.')
    rows = [entities[qid] for qid in ids if qid in entities]
    if not all(isinstance(row, dict) for row in rows):
        raise WikimediaUnavailable('Неожиданные данные объекта Wikidata.')
    return [row for row in rows if 'missing' not in row]


def _site_title(entity):
    title = _map(_map(entity.get('sitelinks')).get('ruwiki')).get('title')
    if not isinstance(title, str) or not title.strip() or len(title) > 250:
        return None
    # Article namespace only. Avoid disambiguation and lists even when the term matches.
    normalized = _norm(title)
    if not normalized or normalized.startswith(('список ', 'списки ')) or normalized.endswith(' значения'):
        return None
    return title


def _match(entity, names, coords, category, *, source=False):
    title, qid = _site_title(entity), entity.get('id')
    if not isinstance(qid, str) or not _QID.fullmatch(qid):
        return None
    if not title and 'ruwiki' in _map(entity.get('sitelinks')):
        return None
    descriptions = [_map(v).get('value') for v in _map(entity.get('descriptions')).values()]
    label_values = [_map(v).get('value') for v in _map(entity.get('labels')).values()]
    alias_values = [_map(v).get('value') for group in _map(entity.get('aliases')).values() for v in group]
    texts = [title, *label_values, *alias_values] if title else [*label_values, *alias_values]
    exact = any(_norm(value) in names for value in texts)
    base = any(_title_base(value) in names for value in texts)
    if not exact and (not title or not base):
        return None
    description = ' '.join(map(_norm, descriptions))
    if any(mark in description for mark in ('значений викимедиа', 'список викимедиа', 'disambiguation', 'wikimedia list')):
        return None
    types = {v.get('id') for v in _claim_values(entity, 'P31') if isinstance(v, dict)}
    if types & _NON_PLACE_TYPES:
        return None
    if category in _CULTURAL_CATEGORIES and (types & _COMMERCIAL_OR_TRANSPORT_TYPES or
            any(_norm(value).startswith(_COMMERCIAL_OR_TRANSPORT_DESCRIPTIONS) for value in descriptions)):
        return None
    terms = _CATEGORY_TERMS.get(category, ())
    typed = bool(types & _CATEGORY_TYPES.get(category, set()))
    typed = typed or any(term in (description or _norm(title)) for term in terms)
    if not typed and (terms or not (source and exact)):
        return None
    locations = [
        _pair(v.get('latitude'), v.get('longitude'))
        for v in _claim_values(entity, 'P625')
        if isinstance(v, dict) and v.get('globe') in {
            'http://www.wikidata.org/entity/Q2', 'https://www.wikidata.org/entity/Q2'
        }
    ]
    if not coords or not any(locations):
        return None
    distance = min(_distance(coords, location) for location in locations if location)
    if distance > (1200 if category in _LARGE_CATEGORIES else 650):
        return None
    return (exact, distance)


def _search_names(values):
    for value in values:
        lang = 'zh' if re.search(r'[\u3400-\u9fff]', value) else (
            'ru' if re.search(r'[А-Яа-яЁё]', value) else 'en'
        )
        result = _request(WIKIDATA_URL, {
            'action': 'wbsearchentities', 'search': value, 'language': lang,
            'uselang': 'ru', 'type': 'item', 'limit': 6,
        })
        rows = result.get('search')
        if not isinstance(rows, list):
            raise WikimediaUnavailable('Некорректный результат поиска Wikidata.')
        for row in rows:
            if not isinstance(row, dict):
                raise WikimediaUnavailable('Неожиданная запись поиска Wikidata.')
            qid = row.get('id')
            if isinstance(qid, str) and _QID.fullmatch(qid):
                yield qid


def _identify(poi):
    vals = [poi.get('name'), *(poi.get('aliases') or [])]
    names = {}
    for value in vals:
        if isinstance(value, str) and len(value) < 250:
            key = _norm(value)
            # A building number alone cannot identify a museum, exhibition or collection.
            if key and not re.fullmatch(r'(?:павильон|pavilion)(?: номер| no)? \d+', key):
                names.setdefault(key, value)
    if not names:
        return None
    coord = poi.get('coordinates') or {}
    pos = _pair(coord.get('latitude'), coord.get('longitude'))
    if not pos:
        return None
    category = poi.get('category')
    source_id = poi.get('wikidata_id')
    if isinstance(source_id, str) and _QID.fullmatch(source_id):
        for entity in _entity_map([source_id]):
            if entity.get('id') == source_id and _match(entity, names, pos, category, source=True):
                return entity, 'catalog_wikidata'
    candidates = list(dict.fromkeys(_search_names(list(names.values())[:3])))
    if not candidates:
        return None
    matches = []
    for entity in _entity_map(candidates):
        result = _match(entity, names, pos, category)
        if result:
            exact, distance = result
            matches.append((not exact, distance, entity['id'], entity))
    matches.sort()
    if not matches:
        return None
    if len(matches) > 1 and (matches[0][0] == matches[1][0] and matches[1][1] - matches[0][1] < 300):
        return None
    return matches[0][3], 'name_category_coordinates'


class _TextOnly(HTMLParser):
    """Flatten source metadata and discard hidden or non-text elements."""

    _excluded = {'script', 'style', 'table', 'ul', 'ol', 'dl', 'figure', 'audio', 'video',
                 'iframe', 'object', 'svg', 'template', 'noscript'}

    def __init__(self):
        super().__init__()
        self.skip = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self._excluded:
            self.skip.append(tag)
        elif tag in {'p', 'br', 'div'} and not self.skip:
            self.parts.append('\n')

    def handle_startendtag(self, tag, attrs):
        if tag == 'br' and not self.skip:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.skip and self.skip[-1] == tag:
            self.skip.pop()
        elif tag in {'p', 'div'} and not self.skip:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _clean_markup(value):
    if not isinstance(value, str):
        return ''
    parser = _TextOnly()
    parser.feed(value)
    parser.close()
    return html.unescape(''.join(parser.parts)).replace('\u00a0', ' ')


_SKIP_SECTIONS = ('см также', 'примечан', 'литератур', 'источник', 'ссылк', 'галере',
                  'фотогалере', 'видео', 'медиа', 'список', 'таблиц', 'библиограф', 'комментар')
_INTERESTING_SECTIONS = ('истори', 'строитель', 'проект', 'архитект', 'экспозиц',
                         'коллекц', 'реконструкц', 'особенност', 'описан', 'восстановлен')
_SECTION = re.compile(r'^(={2,6})\s*(.*?)\s*\1\s*$')


def _sections(value):
    lead = {'title': '', 'paragraphs': []}
    result = [lead]
    current = lead
    suppressed = None
    for line in _clean_markup(value).splitlines():
        line = re.sub(r'\s+', ' ', line).strip()
        if heading := _SECTION.fullmatch(line):
            level, title = len(heading[1]), heading[2]
            if suppressed and level > suppressed:
                continue
            if _norm(title).startswith(_SKIP_SECTIONS):
                suppressed = level
            else:
                suppressed = None
                current = {'title': title[:100], 'paragraphs': []}
                result.append(current)
            continue
        if suppressed or line.startswith(('*', '#', '|', '{', '}', ';', '•', '<')):
            continue
        if re.match(r'^\d{1,2}[.)]\s', line):
            continue
        line = re.sub(r'\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]', '', line)
        if len(line) >= 42 and re.search(r'[А-Яа-яЁё]', line) and re.search(r'[.!?…][»”"]?$', line):
            current['paragraphs'].append(line)
    return result


def _sentences(paragraph):
    pieces = re.split(r'(?<=[.!?…])\s+(?=[А-ЯЁA-Z«„“])', paragraph)
    result = []
    for piece in pieces:
        if result and re.search(r'(?:\s[А-ЯA-Z]| \d{2,4} г|\b(?:ул|им|см|др|т|н))\.$', result[-1]):
            result[-1] += ' ' + piece
        else:
            result.append(piece)
    return result


def _excerpt(paragraphs, limit, sentence_limit=None):
    selected = []
    length = 0
    for paragraph in paragraphs:
        for sentence in _sentences(paragraph):
            separator = bool(selected)
            if length + len(sentence) + separator > limit:
                if not selected:
                    return sentence[:limit - 1].rsplit(' ', 1)[0].rstrip(',;: ') + '…'
                return ' '.join(selected)
            selected.append(sentence)
            length += len(sentence) + separator
            if sentence_limit is not None and len(selected) == sentence_limit:
                return ' '.join(selected)
    return ' '.join(selected)


def _page(endpoint, params):
    response = _request(endpoint, params)
    query = response.get('query')
    pages = query.get('pages') if isinstance(query, dict) else None
    if not isinstance(pages, list) or len(pages) > 1 or not all(isinstance(p, dict) for p in pages):
        raise WikimediaUnavailable('В ответе Wikimedia отсутствуют сведения о странице.')
    if not pages or pages[0].get('missing') or pages[0].get('invalid'):
        return None
    return pages[0]


def _article(entity):
    title = _site_title(entity)
    if not title:
        return None
    page = _page(WIKIPEDIA_URL, {
        'action': 'query', 'prop': 'extracts|pageprops|info', 'titles': title,
        'redirects': '1', 'explaintext': '1', 'exsectionformat': 'wiki',
        'ppprop': 'disambiguation|wikibase_item',
    })
    if not page:
        return None
    props = page.get('pageprops') or {}
    actual_id = props.get('wikibase_item')
    if (page.get('ns') != 0 or 'disambiguation' in props or actual_id != entity['id']
            or _site_title({'sitelinks': {'ruwiki': {'title': page.get('title')}}}) is None):
        raise _ArticleMismatch
    return page


def _date_facts(entity, *, unqualified=False):
    result, used_years = [], set()
    for prop, label, wording in (
        ('P571', 'Год основания', 'Датой основания этого места считается {} год.'),
        ('P1619', 'Год официального открытия', 'Место официально открылось в {} году.'),
    ):
        years = set()
        for stamp in _claim_values(entity, prop, unqualified=unqualified):
            if not isinstance(stamp, dict) or not stamp.get('calendarmodel', '').endswith('/Q1985727'):
                continue
            precision = stamp.get('precision')
            if not isinstance(precision, int) or precision < 9:
                continue
            match = re.match(r'^\+0*(\d{1,4})-', stamp.get('time', ''))
            if match and int(match[1]) > 0:
                years.add(str(int(match[1])))
        if len(years) == 1:
            year = next(iter(years))
            if not unqualified or year not in used_years:
                result.append({'title': label, 'text': wording.format(year)})
                used_years.add(year)
    return result


def _facts(entity, sections, lead_sentences):
    result, quoted = _date_facts(entity), set()
    for item in sections:
        if not _norm(item['title']).startswith(_INTERESTING_SECTIONS):
            continue
        paragraphs = item['paragraphs']
        if not paragraphs:
            continue
        sentences = [sentence for paragraph in paragraphs for sentence in _sentences(paragraph)
                     if sentence not in quoted and sentence not in lead_sentences]
        if not sentences:
            continue
        text = sentences[0]
        if not 45 <= len(text) <= 260 or text.endswith('…'):
            continue
        quoted.add(text)
        fact = {'title': item['title'], 'text': text}
        if len(sentences) >= 3 and 32 <= len(sentences[1]) <= 240:
            fact['detail'] = sentences[1]
            quoted.add(sentences[1])
        result.append(fact)
        if len(result) >= 4:
            break
    return result, quoted


_RELATED_CLAIMS = {
    'P84': ('Архитектор', 'Среди авторов проекта указан архитектор «{}».'),
    'P149': ('Архитектура', 'Архитектурный стиль места обозначен как «{}».'),
    'P1435': ('Охранный статус', 'Для места указан охранный статус «{}».'),
}


def _related_claims(entity):
    single_ids = {}
    for prop in _RELATED_CLAIMS:
        ids = {
            value['id'] for value in _claim_values(entity, prop, unqualified=True)
            if isinstance(value, dict) and isinstance(value.get('id'), str)
            and _QID.fullmatch(value['id']) and value['id'] != entity['id']
        }
        if len(ids) == 1:
            single_ids[prop] = next(iter(ids))
    return single_ids


def _russian_labels(ids):
    labels = {}
    for row in _entity_map(list(dict.fromkeys(ids)), russian_labels_only=True):
        target = _map(_map(row.get('labels')).get('ru')).get('value')
        label = ' '.join(_clean_markup(target).split())
        if 2 <= len(label) <= 130:
            labels[row.get('id')] = label
    return labels


def _wikidata_story(entity, poi_name):
    facts = _date_facts(entity, unqualified=True)
    description = _map(_map(entity.get('descriptions')).get('ru')).get('value')
    description = ' '.join(_clean_markup(description).split())
    if not (65 <= len(description) <= 240 and len(_norm(description).split()) >= 9
            and re.search(r'[А-Яа-яЁё]', description)):
        description = ''
    related = _related_claims(entity)
    if related:
        try:
            labels = _russian_labels(related.values())
        except WikimediaUnavailable:
            if len(facts) < (1 if description else 2):
                raise
            labels = {}
        for prop, qid in related.items():
            if qid in labels:
                title, wording = _RELATED_CLAIMS[prop]
                facts.append({'title': title, 'text': wording.format(labels[qid])})
    if len(facts) < (1 if description else 2):
        return None
    facts = facts[:4]
    intro = (f'«{poi_name}». В Wikidata это место описано так: {description.rstrip(".!?")}.' if description
             else f'«{poi_name}».')
    return {
        'short_text': ' '.join([intro, *(fact['text'] for fact in facts[:2])]),
        'full_text': ' '.join([intro, *(fact['text'] for fact in facts)]),
        'facts': facts,
        'sources': [],
    }


def _verified_url(value, host, prefix):
    if not isinstance(value, str) or len(value) > 2000 or re.search(r'[\s\\\x00-\x1f]', value):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme == 'https' and parts.netloc == host and parts.path.startswith(prefix):
            return value
    except ValueError:
        pass
    return None


def _image(entity):
    images = _claim_values(entity, 'P18')
    if not images:
        return None
    filename = images[0]
    if (not isinstance(filename, str) or len(filename) > 240 or re.search(r'[|\x00-\x1f]', filename) or not re.search(
            r'\.(?:jpe?g|png|webp)$', filename, re.IGNORECASE)):
        return None
    page = _page(COMMONS_URL, {
        'action': 'query', 'prop': 'imageinfo', 'titles': 'File:' + filename,
        'iiprop': 'url|mime|thumbmime|extmetadata', 'iiurlwidth': '900',
        'iiextmetadatafilter': 'Artist|LicenseShortName|LicenseUrl|Restrictions',
    })
    if not page or page.get('ns') != 6 or not page.get('imageinfo'):
        return None
    info = page['imageinfo'][0]
    mime = info.get('thumbmime') if info.get('thumburl') else info.get('mime')
    if mime not in {'image/jpeg', 'image/png', 'image/webp'}:
        return None
    image_url = _verified_url(info.get('thumburl') or info.get('url'), 'upload.wikimedia.org', '/wikipedia/commons/')
    source_url = _verified_url(info.get('descriptionurl'), 'commons.wikimedia.org', '/wiki/File:')
    metadata = info.get('extmetadata', {})
    def read(key):
        return ' '.join(_clean_markup(metadata.get(key, {}).get('value')).split())
    author, license_name = read('Artist'), read('LicenseShortName')
    license_url = _verified_url(read('LicenseUrl'), 'creativecommons.org', '/')
    if (not image_url or not source_url or not license_url or not author or len(author) > 140
            or _norm(author) in {'unknown', 'unknown author', 'own work', 'неизвестен', 'неизвестный автор'}
            or read('Restrictions')):
        return None
    by_license = re.fullmatch(r'CC BY(-SA)? (1\.0|2\.[05]|3\.0|4\.0)', license_name, re.IGNORECASE)
    path = urlsplit(license_url).path.rstrip('/')
    if by_license:
        slug = 'by-sa' if by_license[1] else 'by'
        if path != f'/licenses/{slug}/{by_license[2]}':
            return None
    elif _norm(license_name) == 'cc0' or _norm(license_name) == 'cc0 1 0':
        if path != '/publicdomain/zero/1.0':
            return None
    elif _norm(license_name) == 'public domain':
        if path != '/publicdomain/mark/1.0':
            return None
    else:
        return None
    return {'url': image_url, 'source_url': source_url, 'author': author,
            'license': license_name, 'license_url': license_url}


def _wikipedia_story(entity, page):
    if not page:
        return None
    lead, *sections = _sections(page.get('extract'))
    if not lead['paragraphs']:
        return None
    thematic = [section for section in sections if _norm(section['title']).startswith(_INTERESTING_SECTIONS)]
    lead_sentences = {sentence for paragraph in lead['paragraphs'] for sentence in _sentences(paragraph)}
    facts, quoted = _facts(entity, thematic, lead_sentences)

    def prose(section):
        paragraphs = (' '.join(sentence for sentence in _sentences(paragraph) if sentence not in quoted)
                      for paragraph in section['paragraphs'])
        return [paragraph for paragraph in paragraphs if paragraph]

    short = _excerpt(prose(lead) + [paragraph for section in thematic for paragraph in prose(section)], 880)
    narrative = [_excerpt(prose(lead), 1100)]
    for section in thematic:
        if len(narrative) >= 4:
            break
        paragraph = _excerpt(prose(section), 600)
        if paragraph:
            narrative.append(paragraph)
    wiki_url = 'https://ru.wikipedia.org/wiki/' + quote(page['title'].replace(' ', '_'), safe='')
    if type(page.get('lastrevid')) is int and page['lastrevid'] > 0:
        wiki_url += '?' + urlencode({'oldid': page['lastrevid']})
    return {
        'short_text': short,
        'full_text': '\n\n'.join(narrative),
        'facts': facts,
        'sources': [
            {'title': page['title'], 'url': wiki_url, 'license': 'CC BY-SA 4.0',
             'license_url': 'https://creativecommons.org/licenses/by-sa/4.0/',
             'attribution': 'Участники русской Википедии. Сокращённые выдержки из статьи.'},
        ],
    }


def build_story(poi):
    """Return a sourced text guide, None for an unverified match, or raise on source failure."""
    matched = _identify(poi)
    if not matched:
        return None
    entity, method = matched
    try:
        page = _article(entity)
    except _ArticleMismatch:
        return None
    story = _wikipedia_story(entity, page) or _wikidata_story(entity, poi['name'])
    if story is None:
        return None
    qid = entity['id']
    story['sources'].append(
        {'title': 'Wikidata · ' + qid, 'url': 'https://www.wikidata.org/wiki/' + qid,
         'license': 'CC0 1.0', 'license_url': 'https://creativecommons.org/publicdomain/zero/1.0/',
         'attribution': 'Участники Wikidata. Идентификация и структурированные данные.'},
    )
    story['match'] = {'wikidata_id': qid, 'confidence': 'high', 'method': method}
    try:
        story['image'] = _image(entity)
    except WikimediaUnavailable:
        story['image'] = None
    return story
