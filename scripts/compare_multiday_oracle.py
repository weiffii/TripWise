"""Воспроизводимое сравнение insertion 0.2 с exact 0.1 на синтетических данных."""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_ds.data import load_pois
from ai_ds.recommendation import recommend
from ai_ds.routing import build_route as exact_route


def compare():
    from ai_ds.multiday import build_route, validate_route

    fixture = load_pois(ROOT / 'data/processed/pois.synthetic.validated.json')
    base_profile = load_pois(ROOT / 'examples/preferences/history.json')
    results, failures = [], []
    for seed in range(24):
        rng = random.Random(seed)
        count = 3 + seed % 3
        pois = deepcopy(fixture[:count])
        for poi in pois:
            poi['visit_duration_minutes'] = rng.choice([20, 30, 45, 60, 90])
            poi['visit_duration_basis'] = 'test_fixture'
        ids = sorted(p['id'] for p in pois)
        profile = deepcopy(base_profile)
        profile['top_k'] = count
        profile['required_poi_ids'] = rng.sample(ids, seed % 3)
        profile['pace'] = 'balanced'
        minutes = rng.choice([120, 180, 240, 300])
        start = datetime.fromisoformat('2026-11-17T09:00:00+03:00')
        end = start + timedelta(minutes=minutes)
        active_seconds = minutes * 60 * 70 // 100
        matrix = {'mode': 'walking', 'basis': 'test_matrix',
                  'source_id': 'synthetic_oracle_fixture', 'retrieved_at': None,
                  'valid_for': None, 'pairs': []}
        for source in ids:
            for target in ids:
                if source == target:
                    continue
                reachable = rng.random() >= 0.12
                matrix['pairs'].append({
                    'from_poi_id': source, 'to_poi_id': target,
                    'status': 'known' if reachable else 'unreachable',
                    'duration_seconds': rng.choice([0, 300, 600, 1200]) if reachable else None,
                    'distance_meters': 500 if reachable else None,
                    'geometry': None, 'navigation_url': None})
        old_context = {'day_start': start.isoformat(), 'day_end': end.isoformat(),
                       'endpoint_policy': 'first_last_poi', 'declared_pace': 'balanced',
                       'max_active_seconds': active_seconds, 'rest_after_visit_seconds': 900}
        new_context = {'schema_version': '0.2', 'endpoint_policy': 'first_last_poi',
                       'pace_policy_version': 'pace-policy-v0.2',
                       'unknown_duration_policy': 'strict',
                       'days': [{'date': profile['date_start'],
                                 'day_start': start.isoformat(), 'day_end': end.isoformat(),
                                 'meal_break': None, 'blocked_intervals': [],
                                 'max_active_seconds': None}],
                       'duration_overrides': [], 'opening_intervals': [], 'cost_context': None}
        recommendation = recommend(profile, pois)
        before = deepcopy((profile, pois, recommendation, matrix, new_context))
        old = exact_route(profile, pois, recommendation, matrix, old_context)
        new = build_route(profile, pois, recommendation, matrix, new_context)
        validation = validate_route(new, profile, pois, recommendation, matrix, new_context)
        old_selected = {v['poi_id'] for day in old['days'] for v in day['visits']}
        new_selected = [v['poi_id'] for day in new['days'] for v in day['visits']]
        ranked = [item['poi_id'] for item in recommendation['items']]
        old_vector = [int(i in old_selected) for i in ranked]
        new_vector = [int(i in new_selected) for i in ranked]
        exact_found = old['constraint_checks']['time'] == 'passed'
        errors = []
        if not validation['valid']:
            errors.append('INDEPENDENT_VALIDATION_FAILED')
        if before != (profile, pois, recommendation, matrix, new_context):
            errors.append('INPUT_MUTATION')
        if len(new_selected) != len(set(new_selected)):
            errors.append('DUPLICATE_VISIT')
        if exact_found and new['status'] == 'infeasible':
            errors.append('FALSE_INFEASIBLE')
        if new_selected and new['constraint_checks']['time'] == 'failed':
            errors.append('INVALID_SCHEDULE')
        if new_vector > old_vector and exact_found:
            errors.append('EXCEEDS_EXACT_ORACLE_UNDER_EQUAL_LIMITS')
        if errors:
            failures.append({'seed': seed, 'codes': errors})
        results.append({'seed': seed, 'poi_count': count,
                        'required_count': len(profile['required_poi_ids']),
                        'window_minutes': minutes,
                        'exact_time': old['constraint_checks']['time'],
                        'insertion_time': new['constraint_checks']['time'],
                        'insertion_status': new['status'],
                        'exact_rank_vector': old_vector, 'insertion_rank_vector': new_vector,
                        'rank_equal': old_vector == new_vector,
                        'required_unplaced': new['unplaced_required_poi_ids'],
                        'errors': errors})
    return {'date': '2026-11-18', 'fixture_date': '2026-11-17', 'data_kind': 'synthetic',
            'network_requests': 0, 'scope': 'one-day <=5 candidates; equal pace/time/rest limits',
            'not_measured': ['real tourist relevance', 'global multiday optimality'],
            'case_count': len(results), 'rank_equal_count': sum(r['rank_equal'] for r in results),
            'exact_found_count': sum(r['exact_time'] == 'passed' for r in results),
            'failures': failures, 'cases': results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    report = compare()
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, ensure_ascii=False))
    return 1 if report['failures'] else 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
