"""Локальный воспроизводимый аудит сохранённых Geoapify snapshots; без сети."""

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys

from ai_ds.data import CATEGORIES, process_pois
from ai_ds.sources.geoapify import normalize


SNAPSHOTS = (
    'ru/20261117T173836.768006Z',
    'ru/20261117T173907.144035Z',
    'jp/20261117T173938.648464Z',
)
NULLABLE = ('rating', 'popularity', 'visit_duration_minutes',
            'estimated_cost', 'opening_hours')


def inspect_snapshot(directory):
    """Проверить сохранённый snapshot, не изменяя исходные файлы."""
    def read(name):
        return json.loads((directory / name).read_text(encoding='utf-8'))

    metadata, raw, rows = (read(name) for name in
                           ('metadata.json', 'raw.json', 'pois.validated.json'))
    normalized, normalization = normalize(
        raw, city=metadata['city'], country_code=metadata['country_code'],
        retrieved_at=metadata['retrieved_at'])
    accepted, quality = process_pois(rows)
    target = metadata['target_limit']
    checks = {
        'raw_normalization_matches_saved': normalized[:target] == rows,
        'all_saved_records_accepted': accepted == rows,
        'unique_ids': len({row['id'] for row in rows}) == len(rows),
        'source_and_retrieval_match': all(
            row['provenance']['source_id'] == 'geoapify'
            and row['provenance']['source_record_id'] == row['id'].removeprefix('geoapify:')
            and row['provenance']['retrieved_at'] == metadata['retrieved_at']
            and row['provenance']['is_synthetic'] is False for row in rows),
        'budget_within_current_adapter_bounds': (
            metadata['http_attempt_count'] <= 13
            and metadata['reserved_credit_upper_estimate'] <= 61),
        'nonempty': bool(rows),
    }
    categories = Counter(category for row in rows for category in row['categories'])
    return {
        'snapshot': directory.as_posix(), 'city': metadata['city'],
        'checks': checks, 'checks_passed': all(checks.values()),
        'raw_feature_count': len(raw['features']), 'accepted_count': len(rows),
        'normalization_rejection_counts': dict(Counter(
            item['code'] for item in normalization['normalization_rejected'])),
        'quality': quality,
        'category_counts': {c: categories[c] for c in sorted(CATEGORIES)},
        'missing_category_codes': sorted(CATEGORIES - categories.keys()),
        'null_counts': {field: sum(row[field] is None for row in rows) for field in NULLABLE},
        'availability_unknown_count': sum(row['availability'] == 'unknown' for row in rows),
        'verified_at_unknown_count': sum(row['provenance']['verified_at'] is None for row in rows),
        'duplicate_conflicting_ids_reported_at_collection': read('quality.json').get('duplicate_conflicting_ids', []),
        'collection': {key: metadata[key] for key in (
            'target_limit', 'stop_reason', 'http_attempt_count',
            'reserved_credit_upper_estimate', 'coverage_exhaustive')},
        'readiness': {
            'recommendation_contract': 'ready' if all(checks.values()) else 'failed',
            'rating_and_popularity_ranking': 'unknown',
            'route_feasibility': 'not_confirmed',
            'frontend_integration': 'not_checked',
        },
    }


def self_test():
    """Проверить диагностику без изменения реальных snapshots."""
    from tempfile import TemporaryDirectory
    import unittest

    class AuditTests(unittest.TestCase):
        def test_valid_and_corrupted_saved_records(self):
            source = Path('data/geoapify') / SNAPSHOTS[0]
            with TemporaryDirectory() as temp:
                target = Path(temp)
                for name in ('metadata.json', 'raw.json', 'pois.validated.json', 'quality.json'):
                    (target / name).write_bytes((source / name).read_bytes())
                result = inspect_snapshot(target)
                self.assertTrue(result['checks_passed'])
                rows = json.loads((target / 'pois.validated.json').read_text(encoding='utf-8'))
                rows[0]['coordinates']['latitude'] = 100
                (target / 'pois.validated.json').write_text(json.dumps(rows), encoding='utf-8')
                bad = inspect_snapshot(target)
                self.assertFalse(bad['checks_passed'])
                self.assertGreater(bad['quality']['summary']['rejected_count'], 0)

        def test_duplicate_and_provenance_detection(self):
            source = Path('data/geoapify') / SNAPSHOTS[0]
            with TemporaryDirectory() as temp:
                target = Path(temp)
                for name in ('metadata.json', 'raw.json', 'pois.validated.json', 'quality.json'):
                    (target / name).write_bytes((source / name).read_bytes())
                rows = json.loads((target / 'pois.validated.json').read_text(encoding='utf-8'))
                rows.append(deepcopy(rows[0]))
                rows[0]['provenance']['is_synthetic'] = True
                (target / 'pois.validated.json').write_text(json.dumps(rows), encoding='utf-8')
                bad = inspect_snapshot(target)
                self.assertFalse(bad['checks']['unique_ids'])
                self.assertFalse(bad['checks']['source_and_retrieval_match'])

    return unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(AuditTests)).wasSuccessful()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args(argv)
    if args.self_test:
        return 0 if self_test() else 1
    if args.output is None:
        parser.error('--output required without --self-test')
    snapshots = [inspect_snapshot(Path('data/geoapify') / name) for name in SNAPSHOTS]
    report = {
        'audit_date': '2026-11-17', 'source': 'geoapify', 'network_requests': 0,
        'scope': 'Сохранённые snapshots; поля и условия API повторно в сети не проверялись',
        'total_accepted': sum(item['accepted_count'] for item in snapshots),
        'all_checks_passed': all(item['checks_passed'] for item in snapshots),
        'snapshots': snapshots,
        'limitations': [
            'География опирается на сохранённый boundary filter, а не независимую проверку границ',
            'Координаты не подтверждают вход; схожие названия не доказывают дубликат',
            'Рейтинг, популярность, длительность, цена и часы неизвестны',
            'Реальные пешие переходы не представлены; выполнимость маршрута не подтверждена',
            'Лимиты metadata отражают оценку загрузчика, не фактический баланс кредитов',
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'all_checks_passed': report['all_checks_passed'],
                      'total_accepted': report['total_accepted']}, ensure_ascii=False))
    return 0 if report['all_checks_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
