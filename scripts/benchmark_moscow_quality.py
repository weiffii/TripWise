"""Двадцать одинаковых сценариев для сравнения версий рабочего планировщика."""
import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_ds.moscow_road import coordinates
from ai_ds.moscow_transit import _distance
from backend.moscow import MoscowAssistant, indexes, plan_moscow, source_hours
from backend.shanghai import MoscowPreferences


RED_SQUARE = 'moscow-osm-relation-1577673'
DISTANT = ['moscow-osm-node-5076035172', 'moscow-cultural-188']
EXAMPLES = {'balanced_1', 'balanced_3', 'metro_transfer', 'assistant_edit'}
CASES = {
    'balanced_1': {}, 'balanced_3': {'days': 3}, 'intensive_5': {'days': 5, 'pace': 'intensive'},
    'relaxed': {'pace': 'relaxed'}, 'gastronomy': {'primary_interests': ['cafes']},
    'low_budget': {'budget_amount': 1800}, 'required': {'required_ids': [RED_SQUARE, DISTANT[1]]},
    'distant_required': {'days': 3, 'required_ids': DISTANT},
    'walking': {'transport': 'walk', 'required_ids': [RED_SQUARE]},
    'metro_transfer': {'transport': 'transit', 'required_ids': DISTANT},
    'mixed': {'transport': 'mixed', 'days': 3, 'required_ids': DISTANT},
    'no_metro_bus_allowed': {'transport': 'transit', 'required_ids': DISTANT},
    'museum_history': {'primary_interests': ['museums', 'history'], 'secondary_interests': ['unusual']},
    'unusual_viewpoints': {'primary_interests': ['unusual', 'viewpoints']},
    'early_late': {'day_start': '08:00', 'day_end': '22:00'},
    'unknown_hours': {'required_ids': [RED_SQUARE]},
    'impossible_window': {'required_ids': DISTANT, 'day_end': '10:00', 'visit_minutes': 90},
    'assistant_edit': {'primary_interests': ['nature'], 'day_end': '21:00'},
    'no_repeats': {'days': 5}, 'avoidable_gaps': {'days': 3, 'day_start': '08:00', 'day_end': '21:00'},
}


def minute(value):
    return 60 * int(value[11:13]) + int(value[14:16])


def edit(plan, prefs, options, snapshot, operations):
    service = MoscowAssistant()
    service.configured = True
    service.interpreter = lambda _: {'action': 'propose', 'operations': operations, 'clarification': None}
    token, state = service.session()
    state['route_options'] = options
    csrf = state['csrf']
    common = {'route_id': 'quality-route'}
    service.request(token, csrf, 'register', {**common, 'request_id': 'register-quality', 'version': 1,
                    'plan': plan, 'preferences': prefs.model_dump(mode='json')})
    result = service.request(token, csrf, 'message', {**common, 'request_id': 'message-quality',
        'base_version': 1, 'poi_snapshot_id': snapshot, 'text': 'Добавь Красную площадь.' if operations[0]['op'] == 'add_poi'
        else 'Без метро, используй автобус при наличии маршрута.'})
    if result['type'] != 'proposal':
        return plan, result.get('code', result['type'])
    result = service.request(token, csrf, 'decide', {'request_id': 'confirm-quality',
        'base_version': 1, 'proposal_id': result['proposal_id'], 'decision': 'confirm'})
    return result.get('plan', plan), result.get('code', result['type'])


def measure(plan, prefs):
    catalog = indexes()[0]
    interests = set(prefs.primary_interests + prefs.secondary_interests)
    visits, useful = [], []
    waiting, legs, meals, overlaps, returns, hours, unknown_hours = [], [], [], 0, 0, 0, 0
    unexplained = 0
    modes, leg_modes = Counter(), Counter()
    transfers = 0
    diagnostics = {(gap['date'], minute(gap['start_at']), minute(gap['end_at']))
                   for gap in plan.get('gap_diagnostics', {}).get('remaining', []) if gap.get('reason_code')}
    for day in plan['days']:
        meal_ids = {slot['poi_id'] for slot in plan.get('moscow_meals', {}).get(day['date'], {}).values()}
        day_visits = day['visits']
        visits.extend(v['poi_id'] for v in day_visits)
        meals.extend(meal_ids)
        useful.extend((v['poi_id'], minute(v['end_at']) - minute(v['start_at'])) for v in day_visits if v['poi_id'] not in meal_ids)
        timeline = sorted(((minute(row['start_at']), minute(row['end_at']), row.get('kind'))
                           for row in day_visits + day['legs'] + day['breaks']), key=lambda row: row[:2])
        overlaps += sum(a[1] > b[0] for a, b in zip(timeline, timeline[1:]))
        if timeline:
            free = [(minute('0000-00-00T' + prefs.day_start), timeline[0][0]),
                    *((a[1], b[0]) for a, b in zip(timeline, timeline[1:])),
                    (timeline[-1][1], minute('0000-00-00T' + prefs.day_end))]
            waiting.extend(end - start for start, end in free if end > start)
            unexplained += sum(end - start > 30 and (day['date'], start, end) not in diagnostics
                               for start, end in free)
        waiting.extend(end - start for start, end, kind in timeline if kind == 'waiting')
        legs.extend(day['legs'])
        for row in day['legs']:
            steps = row.get('transport_steps') or []
            leg_modes[row.get('mode') or ('transit' if steps else prefs.transport)] += 1
            for step in steps:
                if step.get('kind') == 'rail_estimate' and ':' in (step.get('line') or ''):
                    modes[step['line'].split(':', 1)[0]] += 1
            transfers += sum(step.get('kind') in {'walk_transfer', 'transfer'} for step in steps)
        points = [coordinates(catalog[v['poi_id']]) for v in day_visits]
        returns += sum(_distance(a, c) < 450 and min(_distance(a, b), _distance(b, c)) > 1200
                       for a, b, c in zip(points, points[1:], points[2:]))
        weekday = datetime.fromisoformat(day['date']).weekday()
        for row in day_visits:
            field = catalog[row['poi_id']]['planning']['opening_hours']
            if field['source_type'] != 'source':
                unknown_hours += 1
                continue
            window = source_hours(field['value'], weekday)
            if window is None:
                unknown_hours += 1
            elif not window or row['start_at'][11:16] < window[0] or row['end_at'][11:16] > window[1]:
                hours += 1
    useful_minutes = sum(duration for _, duration in useful)
    travel_minutes = sum(leg['duration_seconds'] for leg in legs) / 60
    meal_minutes = sum(minute(slot['end_at']) - minute(slot['start_at'])
                       for slots in plan.get('moscow_meals', {}).values() for slot in slots.values())
    cost = plan['budget_summary']['known_total']
    return {'status': plan['status'], 'poi_count': len(visits), 'required_total': len(prefs.required_ids),
            'required_placed': len(set(prefs.required_ids) & set(visits)), 'active_minutes': useful_minutes + meal_minutes + travel_minutes,
            'useful_visit_minutes': useful_minutes, 'travel_minutes': travel_minutes, 'waiting_minutes': sum(waiting),
            'gaps_over_minutes': {threshold: sum(gap > threshold for gap in waiting) for threshold in (30, 45, 60)},
            'unexplained_gaps_over_30': unexplained, 'source_hour_violations': hours, 'source_hours_unknown': unknown_hours,
            'gap_repair': {key: plan['gap_diagnostics'][key] for key in ('before','after','attempts','inserted_poi_ids')}
                          if 'gap_diagnostics' in plan else None,
            'time_overlaps': overlaps, 'return_heuristic_count': returns, 'duplicates': len(visits) - len(set(visits)),
            'interest_matches': sum(bool(interests & set(catalog[key]['categories'])) for key, _ in useful),
            'uncovered_interests': plan.get('uncovered_interests'), 'known_cost_rub': cost,
            'within_estimated_budget': cost <= prefs.budget_amount if cost is not None else None,
            'meal_count': len(meals), 'meal_checks': {k: v for k, v in plan.get('native_checks', {}).items() if 'meal' in k},
            'estimated_transport_share': round(sum(leg.get('basis') != 'routing_provider' for leg in legs) / len(legs), 3) if legs else None,
            'transit_ride_steps_by_mode': dict(modes), 'transport_legs_by_mode': dict(leg_modes),
            'observed_transfer_steps': transfers}


def run(case, overrides):
    prefs = MoscowPreferences(**{
        'city': 'moscow', 'timezone': 'Europe/Moscow', 'start_date': '2026-11-21', 'days': 1,
        'day_start': '09:00', 'day_end': '20:00', 'pace': 'balanced', 'transport': 'walk',
        'group': dict(adult=1, child=0, senior=0, pet=0), 'primary_interests': ['museums', 'nature'],
        'secondary_interests': ['history'], 'required_ids': [], 'budget_amount': 25000,
        'budget_currency': 'RUB', 'budget_basis': 'per_group', **overrides,
    })
    plan, options, snapshot = plan_moscow(prefs)
    outcome = None
    if case == 'assistant_edit':
        plan, outcome = edit(plan, prefs, options, snapshot, [
            {'op': 'add_poi', 'poi_id': RED_SQUARE, 'day': prefs.start_date.isoformat()},
        ])
    elif case == 'no_metro_bus_allowed':
        plan, outcome = edit(plan, prefs, options, snapshot, [
            {'op': 'set_transit_preferences', 'avoid': ['metro'], 'require': ['bus']},
        ])
    result = measure(plan, prefs)
    if outcome:
        result['mock_assistant_outcome'] = outcome
    if case == 'assistant_edit':
        result['target_added'] = any(v['poi_id'] == RED_SQUARE for day in plan['days'] for v in day['visits'])
    if case in EXAMPLES:
        result['example'] = {d['date']: [[v['poi_id'], v['start_at'][11:16], v['end_at'][11:16]]
                                        for v in d['visits']] for d in plan['days']}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--label', required=True)
    parser.add_argument('--head', help='Commit being measured when run from an exported tree')
    args = parser.parse_args()
    report = {'label': args.label, 'head': args.head or subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'conditions': 'Moscow V7; offline, bundled transport fixtures; mock LLM; one adult; RUB',
              'metric_notes': 'Gaps include the beginning and end of nonempty days and explicit waiting. '
                              'Unexplained flags unassigned time over 30 min. Geographic returns use a distance heuristic. '
                              'Unknown source hours are separate from measured violations. '
                              'When an edit is refused the metrics describe the saved original plan.',
              'results': {}}
    with patch.dict(os.environ, {'MOSCOW_OSRM_FOOT': '', 'MOSCOW_OSRM_CAR': ''}):
        for case, overrides in CASES.items():
            try:
                report['results'][case] = run(case, overrides)
            except Exception as error:
                report['results'][case] = {'error': type(error).__name__, 'detail': str(error)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
