"""Опциональные плановые оценки; исходные POI и факты не изменяются."""
from datetime import datetime, timezone
from ai_ds.data import nonempty, process_pois, timestamp

METHOD_VERSION = 'minimum-hour-planning-assumption-v0.1'
CATEGORY_MINUTES = {
    'museums': 60,
    'history': 60,
    'nature': 60,
    'cafes': 60,
    'entertainment': 90,
    'shopping': 60,
    'viewpoints': 60,
    'unusual': 60,
}


def estimate_unknown_durations(pois, *, recorded_at=None,
                               author='TripWise user-approved planning assumption',
                               excluded_poi_ids=()):
    """Вернуть (duration_overrides, report), только по явному вызову.

    Минимум час утверждён владельцем 18 ноября; значения не являются
    измерениями. Для нескольких категорий выбирается максимум. Известные
    длительности, unavailable и явно исключённые записи не заменяются.
    """
    if not nonempty(author):
        raise ValueError('Expected nonempty assumption author')
    recorded_at = (datetime.now(timezone.utc).isoformat()
                   if recorded_at is None else recorded_at)
    if not timestamp(recorded_at):
        raise ValueError('recorded_at must be an aware ISO timestamp')
    if not isinstance(excluded_poi_ids, (list, tuple, set, frozenset)):
        raise ValueError('Expected explicit excluded POI IDs')
    if not all(nonempty(i) for i in excluded_poi_ids):
        raise ValueError('Invalid excluded POI ID')
    accepted, quality = process_pois(pois)
    if quality['summary']['rejected_count']:
        raise ValueError('Invalid input POIs')
    excluded = set(excluded_poi_ids)
    if excluded - {p['id'] for p in accepted}:
        raise ValueError('Unknown excluded POI ID')
    overrides, skipped = [], []
    for poi in sorted(accepted, key=lambda p: p['id']):
        code = ('EXPLICITLY_EXCLUDED' if poi['id'] in excluded else
                'UNAVAILABLE' if poi['availability'] == 'unavailable' else
                'KNOWN_DURATION_PRESERVED'
                if poi['visit_duration_minutes'] is not None else None)
        if code is not None:
            skipped.append({'poi_id': poi['id'], 'code': code})
            continue
        minutes = max(CATEGORY_MINUTES[c] for c in poi['categories'])
        overrides.append({
            'poi_id': poi['id'], 'minutes': minutes, 'basis': 'expert_estimate',
            'method_version': METHOD_VERSION, 'author': author,
            'recorded_at': recorded_at, 'evidence_url': None,
        })
    return overrides, {
        'method_version': METHOD_VERSION, 'kind': 'planning_assumptions',
        'input_data_kind': quality['data_kind'],
        'minimum_estimated_minutes': 60,
        'category_minutes': dict(CATEGORY_MINUTES),
        'estimated_count': len(overrides), 'skipped': skipped,
        'are_verified_visit_durations': False,
        'limitations': [
            'User-approved starting assumptions, not observed visit durations',
            'No confirmation of opening hours, cost, location or visitor access',
            'Explicit opt-in only; original POIs and source verification stay unchanged',
            'Estimated routes must remain marked unknown/draft',
        ],
    }
