"""Geoapify Walking Routing: bounded directed pairs, no invented travel times."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from urllib.request import HTTPRedirectHandler, build_opener

from ai_ds.data import number, process_pois
from ai_ds.sources.geoapify import ATTRIBUTION, GeoapifyAccessError, fetch_json

SOURCE_ID = 'geoapify_routing'


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _point(value):
    return (isinstance(value, list) and len(value) == 2 and
            number(value[0]) and -180 <= value[0] <= 180 and
            number(value[1]) and -90 <= value[1] <= 90)


def normalize_pair(document, source_id, target_id):
    """One two-waypoint response. MultiLineString is not bridged across gaps."""
    if not isinstance(document, dict) or document.get('type') != 'FeatureCollection':
        raise ValueError('Expected GeoJSON FeatureCollection')
    features = document.get('features')
    if not isinstance(features, list) or len(features) != 1:
        raise ValueError('Expected exactly one route; absence is not confirmed unreachable')
    feature = features[0]
    if not isinstance(feature, dict) or not isinstance(feature.get('properties'), dict):
        raise ValueError('Invalid route feature')
    props, geometry = feature['properties'], feature.get('geometry')
    if props.get('mode') != 'walk':
        raise ValueError('Walking response mode not confirmed')
    if str(props.get('distance_units', '')).casefold() != 'meters':
        raise ValueError('Metric response units not confirmed')
    duration, distance = props.get('time'), props.get('distance')
    if not number(duration) or duration < 0 or not number(distance) or distance < 0:
        raise ValueError('Invalid route duration or distance')
    if not isinstance(geometry, dict):
        raise ValueError('Route geometry missing')
    coords = geometry.get('coordinates')
    if geometry.get('type') == 'LineString':
        line = coords
    elif geometry.get('type') == 'MultiLineString':
        if not isinstance(coords, list) or not coords:
            raise ValueError('Invalid multiline geometry')
        line = []
        for part in coords:
            if not isinstance(part, list) or len(part) < 2 or not all(_point(p) for p in part):
                raise ValueError('Invalid line coordinates')
            if line and line[-1] != part[0]:
                raise ValueError('Disconnected route geometry; no invented connector')
            line.extend(part if not line else part[1:])
    else:
        raise ValueError('Unsupported route geometry')
    if not isinstance(line, list) or len(line) < 2 or not all(_point(p) for p in line):
        raise ValueError('Invalid line coordinates')
    return {'from_poi_id': source_id, 'to_poi_id': target_id, 'status': 'known',
            'duration_seconds': duration, 'distance_meters': distance,
            'geometry': {'type': 'LineString', 'coordinates': deepcopy(line)},
            'navigation_url': None}


def collect_matrix(pois, *, root=Path('data/routing/geoapify'), key=None, max_requests=6,
                   opener=None, test_mode=False):
    """Fail before network if full directed matrix exceeds explicit request budget.

    Pilot authorization: at most six requests. No key is persisted. A network or
    provider error stops further requests; partial raw/matrix is retained.
    """
    if type(test_mode) is not bool:
        raise ValueError('test_mode must be boolean')
    if type(max_requests) is not int or not 1 <= max_requests <= 6:
        raise ValueError('Pilot max_requests must be integer 1..6')
    accepted, quality = process_pois(pois)
    if quality['summary']['rejected_count'] or not 2 <= len(accepted) <= 3:
        raise ValueError('Pilot requires 2..3 valid POIs; input is never silently truncated')
    if any(p['coordinates'] is None for p in accepted):
        raise ValueError('Coordinates required')
    destinations = {(p['country_code'], p['city'].strip().casefold()) for p in accepted}
    if len(destinations) != 1:
        raise ValueError('One destination required')
    if len(accepted) * (len(accepted) - 1) > max_requests:
        raise ValueError('Full directed matrix exceeds request budget')
    key = os.environ.get('GEOAPIFY_API_KEY') if key is None else key
    if not isinstance(key, str) or not key.strip():
        raise ValueError('Set GEOAPIFY_API_KEY locally; never print it')
    opener = build_opener(_NoRedirect()).open if opener is None else opener
    now = datetime.now(timezone.utc)
    raw, pairs, errors = [], [], []
    attempts, stop_reason, blocked = 0, 'target_reached', None
    ordered = sorted(accepted, key=lambda p: p['id'])
    for source in ordered:
        if stop_reason != 'target_reached':
            break
        for target in ordered:
            if source['id'] == target['id']:
                continue
            a, b = source['coordinates'], target['coordinates']
            parameters = {'waypoints': f"{a['latitude']},{a['longitude']}|{b['latitude']},{b['longitude']}",
                          'mode': 'walk', 'units': 'metric', 'format': 'geojson'}
            attempts += 1
            try:
                document = fetch_json('/v1/routing', parameters, key, opener=opener)
                raw.append({'from_poi_id': source['id'], 'to_poi_id': target['id'],
                            'parameters_without_key': parameters, 'response': document})
                pairs.append(normalize_pair(document, source['id'], target['id']))
            except ValueError as error:
                blocked = error if isinstance(error, GeoapifyAccessError) else None
                stop_reason = 'http_access_blocked' if blocked else 'request_failed'
                errors.append({'from_poi_id': source['id'], 'to_poi_id': target['id'],
                               'code': stop_reason, 'http_status': blocked.status if blocked else None})
                break
    if not pairs:
        if blocked is not None:
            raise blocked
        raise ValueError('No valid route pairs; matrix snapshot not created')
    matrix = {'schema_version': '0.2', 'mode': 'walking',
              'basis': 'test_matrix' if test_mode else 'routing_provider',
              'source_id': 'geoapify_routing_mock' if test_mode else SOURCE_ID,
              'retrieved_at': now.isoformat(), 'valid_for': None, 'pairs': pairs}
    metadata = {'source_id': matrix['source_id'], 'source_url': 'https://apidocs.geoapify.com/docs/routing/',
                'retrieved_at': now.isoformat(), 'http_attempt_count': attempts,
                'max_requests': max_requests, 'successful_pairs': len(pairs),
                'expected_pairs': len(ordered) * (len(ordered) - 1),
                'coverage_complete': len(pairs) == len(ordered) * (len(ordered) - 1),
                'stop_reason': stop_reason, 'errors': errors, 'attribution': ATTRIBUTION,
                'input_is_synthetic': any(p['provenance']['is_synthetic'] for p in ordered),
                'test_mode': test_mode, 'credit_balance_known': False,
                'limitations': ['Provider estimate, not observed walking time',
                                'POI coordinates are not independently verified entrances',
                                'No confirmation of access restrictions or opening hours',
                                'Walking only; no transit schedules or parking validation',
                                'No visit durations or prices are inferred']}
    directory = Path(root) / now.strftime('%Y%m%dT%H%M%S.%fZ')
    directory.mkdir(parents=True, exist_ok=False)
    for filename, value in [('raw.json', raw), ('travel-times.json', matrix),
                            ('selected-pois.json', ordered), ('metadata.json', metadata)]:
        with (directory / filename).open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
    if blocked is not None:
        raise GeoapifyAccessError(blocked.status, snapshot=str(directory))
    return directory, matrix


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pois', type=Path, required=True, help='Explicit selected 2..3 POIs, no automatic truncation')
    parser.add_argument('--root', type=Path, default=Path('data/routing/geoapify'))
    parser.add_argument('--max-requests', type=int, default=6)
    args = parser.parse_args(argv)
    try:
        pois = json.loads(args.pois.read_text(encoding='utf-8-sig'))
        directory, matrix = collect_matrix(pois, root=args.root, max_requests=args.max_requests)
        print(json.dumps({'snapshot': str(directory), 'known_pairs': len(matrix['pairs'])}))
        return 0
    except GeoapifyAccessError as error:
        print(json.dumps({'error': 'GEOAPIFY_ACCESS_BLOCKED', 'http_status': error.status,
                          'snapshot': error.snapshot}), file=sys.stderr)
        return 3
    except (OSError, ValueError, TypeError, OverflowError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
