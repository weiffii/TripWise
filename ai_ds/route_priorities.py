"""Приоритет и одинаковые метрики для планирования и сравнения маршрутов."""
from typing import Literal

RoutePriority = Literal['balanced', 'walking', 'transfers', 'cost']


def leg_metrics(pair, mode, minutes):
    if mode == 'walk':
        return minutes, 0
    if mode == 'taxi':
        return 0, 0
    steps = pair.get('transport_steps') or []
    if not steps:
        return None, None
    walk = sum(step['duration_seconds'] for step in steps if step['kind'] in
               ('walk_access', 'walk_egress', 'walk_transfer'))
    boardings = sum(step['kind'] == 'wait_estimate' for step in steps)
    return walk / 60, max(0, boardings - 1)


def comparison_metrics(route):
    plan = route['plan']
    metrics = [leg_metrics(leg, leg.get('mode') or ('transit' if leg.get('transport_steps') else 'walk'),
                           leg['duration_seconds'] / 60)
               for day in plan['days'] for leg in day['legs']]
    budget = plan['budget_summary']
    return {'route_priority': route['preferences'].get('route_priority', 'balanced'),
            'walk_minutes': round(sum(minutes for minutes, _ in metrics if minutes is not None), 1),
            'walking_complete': all(minutes is not None for minutes, _ in metrics),
            'transfers': sum(count for _, count in metrics) if all(count is not None for _, count in metrics) else None,
            'cost_rub': budget.get('estimated_total', budget.get('known_total')),
            'budget_limit_rub': budget.get('budget_amount'),
            'visit_count': sum(len(day['visits']) for day in plan['days']),
            'required_missing': len(plan.get('unplaced_required_poi_ids', []))}
