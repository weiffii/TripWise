"""Воспроизводимая offline-проверка рекомендаций; профили сценариев тестовые."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_ds.data import load_pois
from ai_ds.recommendation import MODEL_VERSION, RecommendationError, recommend

SNAPSHOTS = (
    ('moscow', 'ru/20261117T173836.768006Z'),
    ('saint-petersburg', 'ru/20261117T173907.144035Z'),
    ('tokyo', 'jp/20261117T173938.648464Z'),
)
NOW = datetime(2026, 11, 17, 21, tzinfo=timezone.utc)


def evaluate():
    datasets = {slug: load_pois(ROOT / 'data/geoapify' / path / 'pois.validated.json')
                for slug, path in SNAPSHOTS}
    combined = [row for rows in datasets.values() for row in rows]
    scenarios, checks = [], []

    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})

    def run(slug, name, profile, rows):
        before = deepcopy((profile, rows))
        result = recommend(profile, rows, evaluated_at=NOW)
        ids = [item['poi_id'] for item in result['items']]
        lookup = {p['id']: p for p in rows}
        prefix = slug + ':' + name
        check(prefix + ':input_unchanged', before == (profile, rows))
        check(prefix + ':unique_ids', len(ids) == len(set(ids)))
        check(prefix + ':same_destination', all(
            lookup[i]['country_code'] == profile['destination']['country_code'] and
            lookup[i]['city'].strip().casefold() == profile['destination']['city'].strip().casefold()
            for i in ids))
        reversed_result = recommend(profile, list(reversed(rows)), evaluated_at=NOW)
        check(prefix + ':permutation_stable', result == reversed_result)
        check(prefix + ':constraints_honest', all(
            value == 'not_checked' for value in result['constraint_checks'].values()))
        check(prefix + ':no_fabricated_score', all(item['score'] is None for item in result['items']))
        primary = set(profile['primary_interests'])
        secondary = set(profile['secondary_interests'])
        primary_hits = sum(bool(primary.intersection(lookup[i]['categories'])) for i in ids)
        secondary_hits = sum(bool(secondary.intersection(lookup[i]['categories'])) for i in ids)
        fallback = sum(any(r['code'] == 'FALLBACK_SELECTION' for r in item['reasons'])
                       for item in result['items'])
        scenarios.append({'city_profile': slug, 'scenario': name,
                          'profile_is_test_scenario': True, 'profile': profile,
                          'candidate_count': len(rows), 'result_count': len(ids),
                          'poi_ids': ids, 'primary_category_hits': primary_hits,
                          'secondary_category_hits': secondary_hits,
                          'fallback_count': fallback,
                          'excluded_count': len(result['excluded']),
                          'required_poi_ids': result['required_poi_ids'],
                          'constraint_checks': result['constraint_checks']})
        return result

    for slug, _ in SNAPSHOTS:
        rows = datasets[slug]
        outputs = {}
        for kind in ('museums', 'nature'):
            profile = load_pois(ROOT / 'examples/preferences/real' / f'{slug}.{kind}.json')
            outputs[kind] = run(slug, 'existing_' + kind, profile, rows)
        check(slug + ':interest_change_changes_order',
              outputs['museums']['items'] != outputs['nature']['items'])
        base = load_pois(ROOT / 'examples/preferences/real' / f'{slug}.museums.json')
        for name, interests in [('nature_unusual', ['nature', 'unusual']),
                                ('entertainment_shopping', ['entertainment', 'shopping']),
                                ('unusual_only', ['unusual'])]:
            profile = deepcopy(base)
            profile.update(primary_interests=interests, secondary_interests=[])
            run(slug, name, profile, rows)
        budget = deepcopy(base)
        budget['budget'] = {'amount': 0, 'currency': 'JPY' if slug == 'tokyo' else 'RUB',
                            'scope': 'trip_food_and_entertainment'}
        limited = run(slug, 'zero_budget_unknown_prices', budget, rows)
        check(slug + ':budget_not_silently_filtered', limited['items'] == outputs['museums']['items'])
        foreign = run(slug, 'mixed_cities', base, combined)
        check(slug + ':foreign_cities_excluded', foreign['items'] == outputs['museums']['items'] and
              len(foreign['excluded']) == len(combined) - len(rows))
        required = deepcopy(base)
        top_ids = {item['poi_id'] for item in outputs['museums']['items']}
        outside = next(p['id'] for p in rows if p['id'] not in top_ids)
        required['required_poi_ids'] = [outside]
        result = run(slug, 'required_outside_top_k', required, rows)
        check(slug + ':required_preserved_separately', result['required_poi_ids'] == [outside])
        run(slug, 'empty_dataset', base, [])
        wrong_city = deepcopy(base)
        wrong_city['destination']['city'] = '__TEST_UNKNOWN_CITY__'
        empty = run(slug, 'unknown_city', wrong_city, rows)
        check(slug + ':unknown_city_empty', not empty['items'])
        invalid = deepcopy(base)
        invalid['primary_interests'] = []
        try:
            recommend(invalid, rows, evaluated_at=NOW)
        except RecommendationError as error:
            check(slug + ':empty_primary_rejected', any(
                detail['code'] == 'EXPECTED_ONE_OR_TWO_CATEGORIES' for detail in error.details))
        else:
            check(slug + ':empty_primary_rejected', False)
        for name, required_id, expected in [
            ('unknown_required', '__TEST_MISSING_POI__', 'UNKNOWN_REQUIRED_POI'),
            ('foreign_required', next(p['id'] for p in combined if p['city'] != rows[0]['city']),
             'REQUIRED_POI_CONFLICT')]:
            profile = deepcopy(base)
            profile['required_poi_ids'] = [required_id]
            try:
                recommend(profile, combined, evaluated_at=NOW)
            except RecommendationError as error:
                check(slug + ':' + name, any(d['code'] == expected for d in error.details))
            else:
                check(slug + ':' + name, False)
    return {'evaluated_at': NOW.isoformat(), 'model_version': MODEL_VERSION,
            'network_requests': 0, 'user_relevance_evaluated': False,
            'metric_meaning': 'Техническое совпадение категорий, не Precision/NDCG и не пользовательская релевантность',
            'snapshots': [{'path': 'data/geoapify/' + path, 'count': len(datasets[slug])}
                          for slug, path in SNAPSHOTS],
            'scenario_count': len(scenarios), 'check_count': len(checks),
            'failed_checks': [c for c in checks if not c['passed']],
            'checks': checks, 'scenarios': scenarios}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'reports/recommendation/readiness-2026-11-17.json')
    args = parser.parse_args()
    report = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n',
                           encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('scenario_count', 'check_count', 'failed_checks')}))
    return 1 if report['failed_checks'] else 0


if __name__ == '__main__':
    sys.exit(main())
