"""Geoapify Places adapter: free small samples, POI contract 0.1, no key persistence."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ai_ds.data import process_pois, timestamp

BASE = 'https://api.geoapify.com'
ATTRIBUTION = ['Powered by Geoapify', 'OpenStreetMap contributors']
MAPPING = {
    'entertainment.museum': 'museums',
    'tourism.sights.castle': 'history',
    'tourism.sights.ruines': 'history',
    'tourism.sights.archaeological_site': 'history',
    'leisure.park': 'nature',
    'natural': 'nature',
    'catering.cafe': 'cafes',
    'entertainment.theme_park': 'entertainment',
    'commercial.shopping_mall': 'shopping',
    'tourism.attraction.viewpoint': 'viewpoints',
}


class GeoapifyAccessError(ValueError):
    def __init__(self, status, snapshot=None):
        self.status = status
        self.snapshot = snapshot
        super().__init__(f'Geoapify HTTP {status}; access/quota blocked; no further requests')


def fetch_json(path, params, key, *, opener=urlopen):
    if not isinstance(key, str) or not key.strip():
        raise ValueError('Set GEOAPIFY_API_KEY locally; never send it in chat')
    request = Request(BASE + path + '?' + urlencode(dict(params, apiKey=key.strip())),
                      headers={'User-Agent': 'TripWise-coursework/0.1', 'Accept': 'application/json'})
    try:
        with opener(request, timeout=30) as response:
            body = response.read(8 * 1024 * 1024 + 1)
    except HTTPError as error:
        status = error.code
        error.close()
        if status in (402, 403, 429):
            raise GeoapifyAccessError(status) from None
        raise ValueError(f'Geoapify HTTP {status}; URL omitted to protect key') from None
    except (OSError, URLError, TimeoutError):
        raise ValueError('Geoapify network error; URL omitted to protect key') from None
    if len(body) > 8 * 1024 * 1024:
        raise ValueError('Response exceeds 8 MiB')
    if key.strip().encode('utf-8') in body:
        raise ValueError('Unexpected credential echo; refusing response persistence')
    try:
        document = json.loads(body.decode('utf-8'), parse_constant=reject_constant)
    except (UnicodeError, ValueError):
        raise ValueError('Invalid Geoapify JSON') from None
    if not isinstance(document, dict):
        raise ValueError('Expected JSON object')
    return document


def reject_constant(value):
    raise ValueError('Non-finite JSON constant')


def resolve_city(city, country_code, key, *, opener=urlopen):
    if not isinstance(city, str) or not city.strip() or not isinstance(country_code, str) or not re.fullmatch('[A-Z]{2}', country_code):
        raise ValueError('Expected nonempty city and uppercase ISO country code')
    result = fetch_json('/v1/geocode/search', {'text': city, 'type': 'city', 'lang': 'ru',
                        'filter': 'countrycode:' + country_code.lower(), 'format': 'json', 'limit': 2}, key, opener=opener)
    results = result.get('results')
    if not isinstance(results, list):
        raise ValueError('Expected geocoder results')
    matches = [r for r in results if isinstance(r, dict) and
               str(r.get('country_code', '')).upper() == country_code and
               str(r.get('city', r.get('name', ''))).strip().casefold() == city.strip().casefold()]
    if len(matches) != 1 or not isinstance(matches[0].get('place_id'), str):
        raise ValueError('City boundary ambiguous or unconfirmed; inspect geocoder manually')
    return matches[0]


def normalize(document, *, city, country_code, retrieved_at):
    if not timestamp(retrieved_at):
        raise ValueError('retrieved_at must include timezone')
    if not isinstance(city, str) or not city.strip() or not isinstance(country_code, str) or not re.fullmatch('[A-Z]{2}', country_code):
        raise ValueError('Expected city and ISO country code')
    if not isinstance(document, dict) or document.get('type') != 'FeatureCollection' or not isinstance(document.get('features'), list):
        raise ValueError('Expected GeoJSON FeatureCollection')
    rows, rejected = [], []
    for index, feature in enumerate(document['features']):
        p = feature.get('properties') if isinstance(feature, dict) else None
        geometry = feature.get('geometry') if isinstance(feature, dict) else None
        if not isinstance(p, dict) or not isinstance(geometry, dict):
            rejected.append({'index': index, 'code': 'INVALID_FEATURE'}); continue
        cats = p.get('categories')
        if not isinstance(cats, list) or not all(isinstance(c, str) for c in cats):
            rejected.append({'index': index, 'code': 'INVALID_CATEGORIES'}); continue
        mapped = sorted({target for prefix, target in MAPPING.items()
                         if any(c == prefix or c.startswith(prefix + '.') for c in cats)})
        if not mapped or not isinstance(p.get('name'), str) or not p['name'].strip():
            rejected.append({'index': index, 'code': 'UNMAPPED_OR_UNNAMED'}); continue
        if str(p.get('country_code', '')).upper() != country_code:
            rejected.append({'index': index, 'code': 'COUNTRY_NOT_CONFIRMED'}); continue
        place_id, point = p.get('place_id'), geometry.get('coordinates')
        if not isinstance(place_id, str) or not re.fullmatch('[A-Za-z0-9_-]+', place_id):
            rejected.append({'index': index, 'code': 'INVALID_PLACE_ID'}); continue
        if geometry.get('type') != 'Point' or not isinstance(point, list) or len(point) != 2:
            rejected.append({'index': index, 'code': 'MISSING_POINT'}); continue
        rows.append({'id': 'geoapify:' + place_id, 'name': p['name'].strip(),
                     'country_code': country_code, 'city': city.strip(),
                     'coordinates': {'latitude': point[1], 'longitude': point[0]},
                     'categories': mapped, 'rating': None, 'popularity': None,
                     'visit_duration_minutes': None, 'visit_duration_basis': None,
                     'estimated_cost': None, 'opening_hours': None, 'availability': 'unknown',
                     'provenance': {'source_id': 'geoapify', 'source_record_id': place_id,
                                    'source_url': 'https://www.geoapify.com/',
                                    'retrieved_at': retrieved_at, 'verified_at': None, 'is_synthetic': False}})
    accepted, quality = process_pois(rows)
    return accepted, {'schema_version': '0.1', 'input_count': len(document['features']),
                      'normalization_rejected': rejected, 'quality': quality, 'attribution': ATTRIBUTION,
                      'limitations': ['City membership relies on verified place boundary filter',
                                      'Coordinates are not verified entrances',
                                      'User ratings, popularity, cost, hours and duration are unknown']}


def collect(city, country_code, *, limit=30, root=Path('data/geoapify'), key=None,
            opener=urlopen, max_credits=61, max_requests=13):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('limit must be integer 1..1000')
    if type(max_credits) is not int or not 2 <= max_credits <= 61:
        raise ValueError('max_credits must be integer 2..61')
    if type(max_requests) is not int or not 2 <= max_requests <= 13:
        raise ValueError('max_requests must be integer 2..13')
    key = os.environ.get('GEOAPIFY_API_KEY') if key is None else key
    boundary = resolve_city(city, country_code, key, opener=opener)
    groups = [','.join(MAPPING)] if limit <= 100 else [
        ','.join(prefix for prefix, mapped in MAPPING.items() if mapped == target)
        for target in dict.fromkeys(MAPPING.values())]
    offsets = {g: 0 for g in groups}
    active = list(groups)
    seen, pages, queries, conflicts = {}, [], [], []
    credit_estimate, request_count = 1, 1
    stop_reason, blocked = 'categories_exhausted', None
    now = datetime.now(timezone.utc)
    while active:
        for group in list(active):
            page_size = min(100, limit)
            reserved = (page_size + 19) // 20
            if request_count >= max_requests or credit_estimate + reserved > max_credits:
                stop_reason = 'budget_limit'
                active.clear()
                break
            params = {'categories': group, 'filter': 'place:' + boundary['place_id'],
                      'limit': page_size, 'offset': offsets[group], 'lang': 'ru'}
            credit_estimate += reserved
            request_count += 1
            try:
                document = fetch_json('/v2/places', params, key, opener=opener)
                if document.get('type') != 'FeatureCollection' or not isinstance(document.get('features'), list):
                    raise ValueError('Expected GeoJSON FeatureCollection')
            except ValueError as error:
                blocked = error if isinstance(error, GeoapifyAccessError) else None
                stop_reason = 'http_access_blocked' if blocked else 'request_failed'
                active.clear()
                break
            pages.append(document)
            queries.append(params)
            added = 0
            for feature in document['features']:
                props = feature.get('properties') if isinstance(feature, dict) else None
                pid = props.get('place_id') if isinstance(props, dict) else None
                if not isinstance(pid, str) or not pid:
                    # Preserve malformed features in raw pages; normalize diagnoses them.
                    continue
                if pid in seen:
                    if seen[pid] != feature:
                        conflicts.append(pid)
                    continue
                seen[pid] = feature
                added += 1
            offsets[group] += len(document['features'])
            if not added or len(document['features']) < page_size:
                active.remove(group)
            combined = {'type': 'FeatureCollection', 'features': list(seen.values())}
            rows, _ = normalize(combined, city=city, country_code=country_code, retrieved_at=now.isoformat())
            if len(rows) >= limit:
                stop_reason = 'target_reached'
                active.clear()
                break
    if not pages:
        if blocked:
            raise blocked
        raise ValueError('No successful Places page; dataset not created')
    combined = {'type': 'FeatureCollection', 'features': list(seen.values())}
    rows, report = normalize(combined, city=city, country_code=country_code, retrieved_at=now.isoformat())
    if not rows:
        raise ValueError('No accepted POIs; dataset not created')
    report['valid_before_target_limit'] = len(rows)
    rows = rows[:limit]
    _, final_quality = process_pois(rows)
    report.update(quality=final_quality, selected_count=len(rows),
                  category_counts={c: sum(c in r['categories'] for r in rows)
                                   for c in sorted(set(MAPPING.values()))},
                  duplicate_conflicting_ids=sorted(set(conflicts)), stop_reason=stop_reason)
    directory = Path(root) / country_code.lower() / now.strftime('%Y%m%dT%H%M%S.%fZ')
    directory.mkdir(parents=True, exist_ok=False)
    metadata = {'source_id': 'geoapify', 'retrieved_at': now.isoformat(), 'endpoint': BASE + '/v2/places',
                'parameters_without_key': queries, 'city': city, 'country_code': country_code,
                'boundary': boundary, 'attribution': ATTRIBUTION, 'is_synthetic': False,
                'http_attempt_count': request_count, 'successful_places_pages': len(pages),
                'reserved_credit_upper_estimate': credit_estimate, 'credit_balance_known': False,
                'stop_reason': stop_reason, 'target_limit': limit,
                'coverage_exhaustive': False, 'blocked_http_status': blocked.status if blocked else None}
    for filename, content in [('raw.json', combined), ('raw-pages.json', pages), ('metadata.json', metadata),
                              ('pois.validated.json', rows), ('quality.json', report)]:
        with (directory / filename).open('x', encoding='utf-8') as stream:
            json.dump(content, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
    if blocked:
        raise GeoapifyAccessError(blocked.status, snapshot=str(directory))
    return directory, len(rows)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--city', required=True)
    parser.add_argument('--country-code', required=True)
    parser.add_argument('--limit', type=int, default=30)
    parser.add_argument('--max-credits', type=int, default=61)
    parser.add_argument('--max-requests', type=int, default=13)
    parser.add_argument('--root', type=Path, default=Path('data/geoapify'))
    args = parser.parse_args(argv)
    try:
        directory, count = collect(args.city, args.country_code, limit=args.limit, root=args.root,
                                   max_credits=args.max_credits, max_requests=args.max_requests)
        print(json.dumps({'directory': str(directory), 'accepted_count': count}, ensure_ascii=False))
        return 0
    except GeoapifyAccessError as error:
        print(json.dumps({'error': str(error), 'snapshot': error.snapshot}), file=sys.stderr)
        return 3
    except (OSError, ValueError, TypeError, OverflowError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
