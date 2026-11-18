"""Ограниченная read-only загрузка OSM POI с сохранением исходного ответа."""

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = 'https://overpass-api.de/api/interpreter'
USER_AGENT = 'TripWise-coursework/0.1 (small read-only POI snapshot; https://github.com/weiffii/TripWise)'
LICENSE_URL = 'https://www.openstreetmap.org/copyright'
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
CITIES = {
    'moscow': {'name': 'Москва', 'iso': 'RU-MOW'},
    'saint-petersburg': {'name': 'Санкт-Петербург', 'iso': 'RU-SPE'},
}


def query_path(city):
    if city not in CITIES:
        raise ValueError('Неизвестный город')
    return ROOT / 'data' / 'queries' / 'overpass' / (city + '.overpassql')


def retry_seconds(header, attempt, now=None):
    """Не повторять раньше Retry-After; слишком долгое ожидание прерывает загрузку."""
    fallback = min(2 ** attempt, 8)
    if header:
        try:
            seconds = float(header)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(header) -
                           (now or datetime.now(timezone.utc))).total_seconds()
            except (ValueError, TypeError, OverflowError):
                seconds = fallback
        if seconds != seconds or seconds == float('inf'):
            raise ValueError('Некорректный Retry-After')
        if seconds > 60:
            raise ValueError('Retry-After превышает 60 секунд; повторите позднее')
        return max(fallback, seconds)
    return fallback


def validate_document(document):
    if not isinstance(document, dict) or not isinstance(document.get('elements'), list):
        raise ValueError('Ответ не является Overpass JSON')
    if 'remark' in document and document['remark']:
        raise ValueError('Overpass вернул remark (возможен частичный ответ): ' + str(document['remark']))
    for element in document['elements']:
        if not isinstance(element, dict):
            raise ValueError('Overpass elements должен содержать объекты')
        tags = element.get('tags', {})
        if not isinstance(tags, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                for k, v in tags.items()):
            raise ValueError('Overpass tags должен содержать строковые пары')
    return document


def fetch(query, *, endpoint=ENDPOINT, opener=urlopen, sleep=time.sleep, attempts=2, timeout=30):
    """По умолчанию две попытки, максимум три; 400/403 не повторяются."""
    parsed = urlparse(endpoint)
    if parsed.scheme != 'https' or not parsed.netloc:
        raise ValueError('Требуется HTTPS endpoint')
    if type(attempts) is not int or not 1 <= attempts <= 3:
        raise ValueError('Допустимо от одной до трёх попыток')
    if type(timeout) is not int or not 1 <= timeout <= 45:
        raise ValueError('timeout должен быть целым от 1 до 45 секунд')
    request = Request(endpoint, data=urlencode({'data': query}).encode('utf-8'),
                      headers={'User-Agent': USER_AGENT, 'Accept': 'application/json',
                               'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
    for attempt in range(attempts):
        try:
            with opener(request, timeout=timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError('Ответ превышает ограничение 8 MiB')
            validate_document(json.loads(body.decode('utf-8')))
            return body
        except HTTPError as error:
            try:
                if error.code not in (429, 500, 502, 503, 504) or attempt + 1 == attempts:
                    raise
                delay = retry_seconds((error.headers or {}).get('Retry-After'), attempt)
            finally:
                error.close()
            sleep(delay)
        except (URLError, TimeoutError):
            if attempt + 1 == attempts:
                raise
            sleep(retry_seconds(None, attempt))
    raise RuntimeError('Недостижимое состояние загрузчика')


def admin_relation(document, city):
    validate_document(document)
    if city not in CITIES:
        raise ValueError('Неизвестный город')
    expected = CITIES[city]
    relations = []
    for element in document['elements']:
        tags = element.get('tags', {})
        if element.get('type') == 'relation' and tags.get('ISO3166-2') == expected['iso']:
            if (tags.get('boundary') != 'administrative' or tags.get('admin_level') != '4' or
                    tags.get('type') != 'boundary' or
                    tags.get('name:ru', tags.get('name')) != expected['name']):
                raise ValueError('Теги административной relation не соответствуют городу')
            relations.append(element)
    if (len(relations) != 1 or type(relations[0].get('id')) is not int or
            relations[0]['id'] <= 0):
        raise ValueError('Ожидалась одна административная relation города')
    return relations[0]


def download(city, raw_dir, *, endpoint=ENDPOINT, opener=urlopen, sleep=time.sleep,
             attempts=2, timeout=30):
    query = query_path(city).read_text(encoding='utf-8')
    body = fetch(query, endpoint=endpoint, opener=opener, sleep=sleep,
                 attempts=attempts, timeout=timeout)
    document = json.loads(body.decode('utf-8'))
    relation = admin_relation(document, city)
    now = datetime.now(timezone.utc)
    directory = Path(raw_dir) / city / now.strftime('%Y%m%dT%H%M%S.%fZ')
    directory.mkdir(parents=True, exist_ok=False)
    # Исходные байты сохраняются без пересериализации.
    (directory / 'response.json').write_bytes(body)
    (directory / 'query.overpassql').write_text(query, encoding='utf-8')
    metadata = {
        'source_id': 'openstreetmap', 'endpoint': endpoint, 'method': 'POST',
        'retrieved_at': now.isoformat(), 'city_key': city,
        'city': CITIES[city]['name'], 'country_code': 'RU',
        'admin_relation_id': relation['id'], 'admin_tags': relation['tags'],
        'selection_basis': 'overpass_map_to_area_from_verified_admin_relation',
        'query_sha256': hashlib.sha256(query.encode('utf-8')).hexdigest(),
        'response_sha256': hashlib.sha256(body).hexdigest(),
        'license': 'ODbL-1.0', 'license_url': LICENSE_URL,
        'attribution': '© OpenStreetMap contributors', 'is_synthetic': False,
        'user_agent': USER_AGENT,
        'limitations': ['OSM area selection is not a verification of the entrance location',
                        'Source timestamp is not a verification date of every fact'],
    }
    (directory / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n',
                                           encoding='utf-8')
    return directory


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--city', choices=tuple(CITIES), required=True)
    parser.add_argument('--raw-dir', type=Path, default=ROOT / 'data/raw/openstreetmap')
    parser.add_argument('--endpoint', default=ENDPOINT)
    parser.add_argument('--attempts', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=30)
    args = parser.parse_args(argv)
    try:
        directory = download(args.city, args.raw_dir, endpoint=args.endpoint,
                             attempts=args.attempts, timeout=args.timeout)
        print(directory)
        return 0
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
