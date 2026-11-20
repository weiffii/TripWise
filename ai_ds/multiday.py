"""Opt-in multiday-insertion-v0.2. Missing tourist facts remain unknown."""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sys
from ai_ds.data import load_pois
from ai_ds.recommendation import RecommendationError, recommend, utc_microseconds
from ai_ds.routing_v02_contract import RoutingV02Error, require, validate_context, validate_matrix, evaluate_budget
MODEL_VERSION = 'multiday-insertion-v0.2'

class _Issue(Exception):
    def __init__(self, code, check='time', unknown=False, poi_id=None):
        self.code, self.check, self.unknown, self.poi_id = code, check, unknown, poi_id

def _prepare(preferences, pois, recommendation, travel_times, routing_context):
    expected = recommend(preferences, pois)
    require(isinstance(recommendation, dict), 'recommendation', 'EXPECTED_OBJECT')
    for field in ('schema_version', 'model_version', 'items', 'required_poi_ids', 'excluded', 'constraint_checks'):
        require(recommendation.get(field) == expected[field], 'recommendation.' + field, 'RECOMMENDATION_MISMATCH')
    by_id = {p['id'].strip(): p for p in pois}
    context = validate_context(routing_context, preferences, by_id)
    matrix = validate_matrix(travel_times, preferences, by_id)
    context['_matrix_basis'] = travel_times['basis']
    ranked = [item['poi_id'] for item in recommendation['items']]
    required = set(preferences['required_poi_ids'])
    eligible = set(by_id) - {p['poi_id'] for p in expected['excluded']}
    return by_id, context, matrix, ranked, required, eligible

def _stamp(day, offset, zone):
    return (day['start'].astimezone(timezone.utc) + timedelta(microseconds=offset)).astimezone(zone).isoformat()

def _day_schedule(sequence, day, context, matrix):
    base = utc_microseconds(day['start'])
    blocked = sorted((utc_microseconds(b['start']) - base, utc_microseconds(b['end']) - base, b['kind']) for b in day['blocked'])
    stamp = lambda offset: _stamp(day, offset, context['zone'])
    breaks = [{'start_at': stamp(a), 'end_at': stamp(b), 'kind': kind} for a, b, kind in blocked]
    visits, legs, warnings = [], [], set()
    opening_status, active, travel_total, current = 'passed', 0, 0, 0
    def add_wait(a, b):
        for left, right, _ in blocked:
            if right <= a or left >= b:
                continue
            if a < left:
                breaks.append({'start_at': stamp(a), 'end_at': stamp(min(b, left)), 'kind': 'waiting'})
            a = max(a, right)
        if a < b:
            breaks.append({'start_at': stamp(a), 'end_at': stamp(b), 'kind': 'waiting'})
    def advance(at, duration):
        proposed = at
        for left, right, _ in blocked:
            if proposed < right and proposed + duration > left:
                proposed = right
        if proposed + duration > day['window_us']:
            raise _Issue('DAY_WINDOW_EXCEEDED')
        return proposed
    try:
        for index, poi_id in enumerate(sequence):
            duration = context['durations'][poi_id]
            if duration is None:
                raise _Issue('UNKNOWN_DURATION', unknown=True, poi_id=poi_id)
            openings = context['openings'].get((poi_id, day['date']))
            if openings is None:
                opening_status = 'unknown'
                warnings.add('OPENING_HOURS_NOT_VERIFIED')
                arrival = advance(current, duration)
            else:
                arrival = None
                for opens, closes in openings:
                    try:
                        candidate = advance(max(current, utc_microseconds(opens) - base), duration)
                    except _Issue:
                        continue
                    if candidate + duration <= utc_microseconds(closes) - base:
                        arrival = candidate
                        break
                if arrival is None:
                    raise _Issue('REQUIRED_CLOSED' if not openings else 'OPENING_WINDOW_EXCEEDED', check='opening_hours', poi_id=poi_id)
            add_wait(current, arrival)
            current = arrival + duration
            active += duration
            if active > day['active_limit_us']:
                raise _Issue('ACTIVE_LIMIT_EXCEEDED')
            visits.append({'poi_id': poi_id, 'start_at': stamp(arrival), 'end_at': stamp(current), 'duration_basis': ('expert_estimate' if poi_id in context['estimated_ids'] else context['by_id'][poi_id]['visit_duration_basis'])})
            if poi_id in context['estimated_ids']:
                warnings.add('ESTIMATED_VISIT_DURATION')
            if index == len(sequence) - 1:
                continue
            if day['rest_us']:
                rest_start = advance(current, day['rest_us'])
                add_wait(current, rest_start)
                breaks.append({'start_at': stamp(rest_start), 'end_at': stamp(rest_start + day['rest_us']), 'kind': 'rest'})
                current = rest_start + day['rest_us']
            target = sequence[index + 1]
            pair = matrix.get((poi_id, target))
            if pair is None or pair['status'] == 'unknown':
                raise _Issue('MISSING_TRAVEL_TIME', unknown=True, poi_id=target)
            if pair['status'] == 'unreachable':
                raise _Issue('UNREACHABLE_TRANSITION', poi_id=target)
            duration = pair['duration_us']
            departure = advance(current, duration)
            validity = pair.get('valid_for')
            if validity is not None:
                lower, upper = (utc_microseconds(t) - base for t in validity)
                if departure < lower or departure + duration > upper:
                    raise _Issue('MATRIX_NOT_VALID_FOR_TIME', unknown=True, poi_id=target)
            add_wait(current, departure)
            current = departure + duration
            active += duration
            travel_total += duration
            if active > day['active_limit_us']:
                raise _Issue('ACTIVE_LIMIT_EXCEEDED')
            leg = {key: deepcopy(pair.get(key)) for key in ('from_poi_id', 'to_poi_id', 'duration_seconds', 'distance_meters', 'geometry', 'navigation_url', 'retrieved_at', 'basis', 'source_id')}
            leg.update(start_at=stamp(departure), end_at=stamp(current))
            if pair.get('transport_steps'):
                leg['transport_steps'] = deepcopy(pair['transport_steps'])
            legs.append(leg)
            if pair['distance_meters'] is None:
                warnings.add('DISTANCE_UNKNOWN')
            if pair.get('geometry') is None and pair.get('navigation_url') is None:
                warnings.add('NAVIGATION_PATH_UNKNOWN')
    except _Issue as issue:
        return {'valid': False, 'code': issue.code, 'check': issue.check, 'unknown': issue.unknown, 'poi_id': issue.poi_id}
    breaks.sort(key=lambda b: (utc_microseconds(datetime.fromisoformat(b['start_at'])), utc_microseconds(datetime.fromisoformat(b['end_at'])), b['kind']))
    if not any(b['kind'] == 'meal' for b in day['blocked']):
        warnings.add('MEAL_BREAK_NOT_CONFIGURED')
    elapsed = max([current] + [b for _, b, _ in blocked])
    return {'valid': True, 'date': day['date'], 'visits': visits, 'legs': legs, 'breaks': breaks, 'pace': day['pace'], 'active_limit_seconds': day['active_limit_us'] / 1_000_000, 'rest_after_visit_seconds': day['rest_us'] / 1_000_000, 'active_seconds': active / 1_000_000, 'visit_span_seconds': current / 1_000_000, 'elapsed_seconds': elapsed / 1_000_000, 'constraint_checks': {'time': 'passed', 'opening_hours': opening_status}, 'warnings': sorted(warnings), '_travel_us': travel_total, '_end_us': current}

def _budget(sequences, preferences, context):
    return evaluate_budget({i for values in sequences.values() for i in values}, [d['date'] for d in context['days']], preferences, context)

def _proof(required, preferences, context, matrix):
    if _budget({context['days'][0]['date']: sorted(required)}, preferences, context)['status'] == 'failed':
        return {'code': 'REQUIRED_BUDGET_EXCEEDED', 'check': 'budget'}
    for poi_id in sorted(required):
        attempts = [_day_schedule([poi_id], d, context, matrix) for d in context['days']]
        if attempts and not any(a['valid'] or a.get('unknown') for a in attempts):
            return {'code': 'REQUIRED_HAS_NO_FEASIBLE_DAY', 'check': 'required_places', 'poi_id': poi_id}
    return None

def _result(preferences, by_id, context, matrix, ranked, required, sequences, *, unresolved=None, diagnostics=None, proof=None, pool_scope='recommendation_items_plus_required'):
    unresolved, diagnostics = set(unresolved or ()), list(diagnostics or ())
    days, warnings, issues = [], {'HOTEL_TRANSFERS_NOT_INCLUDED'}, []
    selected = set()
    for day in context['days']:
        sequence = sequences[day['date']]
        scheduled = _day_schedule(sequence, day, context, matrix)
        if scheduled['valid']:
            selected.update(sequence)
            warnings.update(scheduled['warnings'])
            days.append({k: v for k, v in scheduled.items() if not k.startswith('_') and k != 'valid'})
        else:
            issues.append(scheduled)
            unresolved.update(sequence)
            diagnostics.append({k: v for k, v in scheduled.items() if k != 'valid'} | {'date': day['date']})
            days.append({'date': day['date'], 'visits': [], 'legs': [], 'breaks': [], 'pace': day['pace'], 'constraint_checks': {'time': 'unknown' if scheduled.get('unknown') else 'failed', 'opening_hours': 'unknown'}, 'warnings': [scheduled['code']]})
    budget = _budget(sequences, preferences, context)
    warnings.update(budget.get('warnings', []))
    unplaced = required - selected
    checks = {'time': 'unknown' if unresolved else 'passed', 'required_places': 'unknown' if unplaced else 'passed', 'opening_hours': 'unknown' if any(d['constraint_checks']['opening_hours'] == 'unknown' for d in days) else 'passed', 'budget': budget['status']}
    for issue in issues:
        if not issue['unknown']:
            checks[issue['check']] = 'failed'
    if proof:
        checks[proof['check']] = 'failed'
        diagnostics.append(proof)
    if not selected and not required and not issues:
        checks['time'] = 'unknown'
        warnings.add('NO_COMPLETE_SCHEDULE')
    if unplaced:
        warnings.add('SEARCH_NOT_PROVEN' if proof is None else proof['code'])
    if unresolved:
        warnings.add('UNRESOLVED_CANDIDATE_DATA')
    if any(by_id[i]['provenance']['is_synthetic'] for i in set(ranked) | required | selected):
        warnings.add('SYNTHETIC_DATA')
    if any(by_id[i]['availability'] == 'unknown' for i in selected):
        warnings.add('AVAILABILITY_UNKNOWN')
    if any(by_id[i]['coordinates'] is None for i in selected):
        warnings.add('COORDINATES_UNKNOWN')
    if context['estimated_ids'] & selected:
        warnings.add('ESTIMATED_VISIT_DURATION')
    if any(by_id[i]['visit_duration_basis'] == 'test_fixture' for i in selected):
        warnings.add('TEST_VISIT_DURATION')
    if context['_matrix_basis'] == 'test_matrix':
        warnings.add('TEST_TRAVEL_TIMES')
    if context['_matrix_basis'] == 'geometric_estimate':
        warnings.add('GEOMETRIC_TRAVEL_ESTIMATE')
    if preferences['transport'] == 'car':
        warnings.add('PARKING_AND_ACCESS_NOT_VERIFIED')
    if preferences['transport'] == 'public_transit':
        warnings.add('PUBLIC_TRANSIT_PROVIDER_LIMITATIONS')
    uncertainty = {'SYNTHETIC_DATA', 'TEST_TRAVEL_TIMES', 'GEOMETRIC_TRAVEL_ESTIMATE', 'AVAILABILITY_UNKNOWN', 'COORDINATES_UNKNOWN', 'DISTANCE_UNKNOWN', 'NAVIGATION_PATH_UNKNOWN', 'ESTIMATED_VISIT_DURATION', 'ESTIMATED_COST', 'TEST_COST_DATA', 'TEST_VISIT_DURATION'}
    status = ('infeasible' if proof else 'unknown' if any(v in ('unknown', 'failed') for v in checks.values()) or warnings & uncertainty else 'feasible')
    return {'schema_version': '0.2', 'model_version': MODEL_VERSION, 'route_id': None, 'version': None, 'status': status, 'poi_snapshot_id': context.get('poi_snapshot_id'), 'days': days, 'unplaced_required_poi_ids': sorted(unplaced), 'unresolved_candidate_ids': sorted(unresolved), 'warnings': sorted(warnings), 'constraint_checks': checks, 'budget_summary': budget, 'diagnostics': diagnostics, 'planning_metadata': {'algorithm': MODEL_VERSION, 'search_complete': False, 'candidate_count': len(set(ranked) | required), 'selected_count': len(selected), 'pool_scope': pool_scope, 'pace_policy_version': 'pace-policy-v0.2', 'unknown_duration_policy': 'strict'}}

def build_route(preferences, pois, recommendation, travel_times, routing_context):
    """Construct a deterministic draft without modifying inputs or the old 0.1 API."""
    by_id, context, matrix, ranked, required, eligible = _prepare(preferences, pois, recommendation, travel_times, routing_context)
    sequences = {d['date']: [] for d in context['days']}
    schedules = {d['date']: _day_schedule([], d, context, matrix) for d in context['days']}
    unresolved, diagnostics = set(), []
    proof = _proof(required, preferences, context, matrix)
    if proof:
        return _result(preferences, by_id, context, matrix, ranked, required, sequences, unresolved=required, proof=proof)
    def options(poi_id):
        choices, failures = [], []
        for day in context['days']:
            previous = sequences[day['date']]
            for position in range(len(previous) + 1):
                new = previous[:position] + [poi_id] + previous[position:]
                scheduled = _day_schedule(new, day, context, matrix)
                if not scheduled['valid']:
                    failures.append(scheduled)
                    continue
                proposed = dict(sequences, **{day['date']: new})
                if _budget(proposed, preferences, context)['status'] == 'failed':
                    failures.append({'code': 'BUDGET_EXCEEDED', 'check': 'budget', 'unknown': False})
                    continue
                delta = scheduled['_travel_us'] - schedules[day['date']]['_travel_us']
                defer_occupied = bool(routing_context.get('optimize_local_order') and previous)
                choices.append(((defer_occupied, delta, scheduled['_end_us'], day['date'], position),
                                day['date'], new, scheduled))
        choices.sort(key=lambda option: option[0])
        return choices, failures
    pending = set(required)
    while pending:
        candidates = [(poi_id, *options(poi_id)) for poi_id in sorted(pending)]
        poi_id, choices, failures = min(candidates, key=lambda candidate: (len(candidate[1]), candidate[0]))
        if not choices:
            unresolved.update(pending)
            diagnostics.extend({'poi_id': poi_id, 'code': f['code'], 'unknown': f.get('unknown', False)} for f in failures)
            break
        _, day, sequence, scheduled = choices[0]
        sequences[day], schedules[day] = sequence, scheduled
        pending.remove(poi_id)
    if not pending:
        for poi_id in ranked:
            if poi_id in required:
                continue
            choices, failures = options(poi_id)
            if choices:
                _, day, sequence, scheduled = choices[0]
                sequences[day], schedules[day] = sequence, scheduled
            else:
                if any(f.get('unknown') for f in failures):
                    unresolved.add(poi_id)
                diagnostics.append({'poi_id': poi_id, 'code': 'CANDIDATE_NOT_PLACED', 'reasons': sorted({f['code'] for f in failures})})
    if routing_context.get('optimize_local_order'):
        for settings in context['days']:
            day = settings['date']
            for _ in range(2):
                sequence = sequences[day]
                best = schedules[day]
                improved = sequence
                for first in range(len(sequence) - 1):
                    for last in range(first + 2, len(sequence) + 1):
                        candidate = sequence[:first] + sequence[first:last][::-1] + sequence[last:]
                        scheduled = _day_schedule(candidate, settings, context, matrix)
                        if scheduled['valid'] and scheduled['_travel_us'] < best['_travel_us']:
                            improved, best = candidate, scheduled
                if improved == sequence:
                    break
                sequences[day], schedules[day] = improved, best
    result = _result(preferences, by_id, context, matrix, ranked, required, sequences, unresolved=unresolved, diagnostics=diagnostics)
    verified = validate_route(result, preferences, pois, recommendation, travel_times, routing_context)
    require(verified['valid'], 'route', 'INTERNAL_SCHEDULE_VALIDATION_FAILED')
    return result

def schedule_sequences(preferences, pois, recommendation, travel_times, routing_context, sequences):
    """Recompute explicit date->ID sequences, preserving user edit order and days."""
    by_id, context, matrix, ranked, required, eligible = _prepare(preferences, pois, recommendation, travel_times, routing_context)
    require(isinstance(sequences, dict), 'sequences', 'EXPECTED_OBJECT')
    require(set(sequences) == {d['date'] for d in context['days']}, 'sequences', 'DATE_COVERAGE_MISMATCH')
    seen = set()
    for day, sequence in sequences.items():
        require(isinstance(sequence, list), 'sequences.' + day, 'EXPECTED_ARRAY')
        for poi_id in sequence:
            require(isinstance(poi_id, str) and poi_id in eligible, 'sequences.' + day, 'UNKNOWN_OR_EXCLUDED_POI')
            require(poi_id not in seen, 'sequences.' + day, 'DUPLICATE_VISIT')
            seen.add(poi_id)
    return _result(preferences, by_id, context, matrix, ranked, required, deepcopy(sequences), proof=_proof(required, preferences, context, matrix), pool_scope='explicit_sequences')

def validate_route(route, preferences, pois, recommendation, travel_times, routing_context):
    """Reschedule independently and compare facts rather than trusting passed flags."""
    require(isinstance(route, dict), 'route', 'EXPECTED_OBJECT')
    require(route.get('schema_version') == '0.2', 'route.schema_version', 'VERSION_MISMATCH')
    require(route.get('model_version') == MODEL_VERSION, 'route.model_version', 'MODEL_MISMATCH')
    require(isinstance(route.get('days'), list), 'route.days', 'EXPECTED_ARRAY')
    sequences = {}
    for day in route['days']:
        require(isinstance(day, dict) and isinstance(day.get('date'), str) and isinstance(day.get('visits'), list), 'route.days', 'INVALID_DAY')
        require(day['date'] not in sequences, 'route.days', 'DUPLICATE_DAY')
        require(all(isinstance(v, dict) and isinstance(v.get('poi_id'), str) for v in day['visits']), 'route.days.visits', 'INVALID_VISIT')
        sequences[day['date']] = [v['poi_id'] for v in day['visits']]
    canonical = schedule_sequences(preferences, pois, recommendation, travel_times, routing_context, sequences)
    errors = []
    for field in ('days', 'unplaced_required_poi_ids', 'poi_snapshot_id', 'budget_summary'):
        if route.get(field) != canonical[field]:
            errors.append({'path': 'route.' + field, 'code': 'SCHEDULE_MISMATCH'})
    route_warnings = route.get('warnings')
    if not isinstance(route_warnings, list) or not all(isinstance(w, str) for w in route_warnings):
        errors.append({'path': 'route.warnings', 'code': 'INVALID_WARNINGS'})
    elif not set(canonical['warnings']) <= set(route_warnings):
        errors.append({'path': 'route.warnings', 'code': 'MISSING_WARNINGS'})
    checks = route.get('constraint_checks')
    if not isinstance(checks, dict):
        errors.append({'path': 'route.constraint_checks', 'code': 'EXPECTED_OBJECT'})
    else:
        if set(checks) != set(canonical['constraint_checks']):
            errors.append({'path': 'route.constraint_checks', 'code': 'INVALID_CHECK_FIELDS'})
        for name, check in canonical['constraint_checks'].items():
            actual = checks.get(name)
            allowed = ('passed', 'failed', 'unknown', 'not_checked') if name == 'budget' else ('passed', 'failed', 'unknown')
            if not isinstance(actual, str) or actual not in allowed:
                errors.append({'path': 'route.constraint_checks.' + name, 'code': 'INVALID_CHECK_STATUS'})
            elif actual == 'passed' and check != 'passed':
                errors.append({'path': 'route.constraint_checks.' + name, 'code': 'FALSE_PASSED_CHECK'})
    if route.get('status') not in ('feasible', 'unknown', 'infeasible'):
        errors.append({'path': 'route.status', 'code': 'INVALID_ROUTE_STATUS'})
    if route.get('status') == 'infeasible' and canonical['status'] != 'infeasible':
        errors.append({'path': 'route.status', 'code': 'FALSE_INFEASIBLE_STATUS'})
    if route.get('status') == 'feasible' and isinstance(checks, dict) and any(v in ('unknown', 'failed') for v in checks.values()):
        errors.append({'path': 'route.status', 'code': 'STATUS_CHECK_CONFLICT'})
    if route.get('status') == 'feasible' and canonical['status'] != 'feasible':
        errors.append({'path': 'route.status', 'code': 'FALSE_FEASIBLE_STATUS'})
    return {'valid': not errors, 'errors': errors, 'constraint_checks': canonical['constraint_checks'], 'route': canonical}

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='strict')
    parser = argparse.ArgumentParser(description=__doc__)
    catalog = parser.add_mutually_exclusive_group(required=True)
    catalog.add_argument('--pois')
    catalog.add_argument('--moscow-v7', action='store_true')
    parser.add_argument('--preferences', required=True)
    parser.add_argument('--scenario', required=True)
    args = parser.parse_args(argv)
    try:
        preferences, scenario = (load_pois(p) for p in (args.preferences, args.scenario))
        if args.moscow_v7:
            from ai_ds.moscow_catalog import load_moscow_v7
            pois, snapshot = load_moscow_v7()
        else:
            pois = load_pois(args.pois)
        require(isinstance(scenario, dict), 'scenario', 'EXPECTED_OBJECT')
        require('routing_context' in scenario, 'scenario.routing_context', 'MISSING_FIELD')
        if args.moscow_v7:
            pin = scenario['routing_context'].get('poi_snapshot_id')
            require(pin is None or pin == snapshot, 'routing_context.poi_snapshot_id', 'SNAPSHOT_MISMATCH')
            scenario['routing_context']['poi_snapshot_id'] = snapshot
        recommendation = recommend(preferences, pois)
        if args.moscow_v7 and 'moscow_rail' in scenario:
            from ai_ds.moscow_transit import build_graph, build_matrix
            require(preferences['transport'] == 'public_transit', 'preferences.transport', 'MODE_MISMATCH')
            rail = scenario['moscow_rail']
            document = load_pois(rail['osm_snapshot'])
            graph = build_graph(document, rail['estimates'], rail.get('transfers', []))
            ids = sorted({row['poi_id'] for row in recommendation['items']} | set(preferences['required_poi_ids']))
            require(len(ids) <= 40, 'preferences.top_k', 'RAIL_MATRIX_LIMIT')
            scenario['travel_times'] = build_matrix(graph, ids, rail['approaches'])
        require('travel_times' in scenario, 'scenario.travel_times', 'MISSING_FIELD')
        result = build_route(preferences, pois, recommendation, scenario['travel_times'], scenario['routing_context'])
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except (RoutingV02Error, RecommendationError) as error:
        print(json.dumps(error.response(), ensure_ascii=False), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, OverflowError, RecursionError) as error:
        print(json.dumps({'schema_version': '0.2', 'error': {'code': 'INPUT_ERROR', 'details': [{'message': str(error)}]}}, ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())
