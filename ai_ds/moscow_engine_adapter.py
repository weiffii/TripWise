"""Адаптер московских данных к общему маршрутизатору и формату сохранённых поездок."""
from math import ceil

from ai_ds.shanghai_assistant import bridge_call


MODES = {'walking': 'walk', 'car': 'taxi', 'public_transit': 'transit'}
MATRIX_MODES = {label: mode for mode, label in MODES.items()}


def clock(stamp):
    return stamp[11:16]


def timestamp(date, minutes):
    return f'{date}T{minutes // 60:02d}:{minutes % 60:02d}:00+03:00'


def engine_input(profile, pois, matrix, context):
    durations = {row['poi_id']: row['minutes'] for row in context['duration_overrides']}
    group = context['moscow_group']
    people = group['adult'] + group['child'] + group['senior']
    costs = {row['poi_id']: row['amount'] for row in context['cost_context']['visit_costs']}
    openings = {}
    for row in context['opening_intervals']:
        windows = row['intervals']
        if windows is not None:
            openings.setdefault(row['poi_id'], {})[row['date']] = (
                {'open': clock(windows[0]['start_at']), 'close': clock(windows[0]['end_at'])}
                if windows else {'open': '00:00', 'close': '24:00', 'closed': True})
    catalog = []
    for poi in pois:
        key = poi['id']
        price = costs[key]
        if price is None:
            continue
        restaurant = 'average_check_rub' in poi['planning']
        catalog.append({
            'id': key, 'name': poi['name'], 'kind': 'restaurant' if restaurant else 'attraction',
            'categories': poi['categories'], 'lat': poi['coordinates']['latitude'],
            'lon': poi['coordinates']['longitude'], 'duration_minutes': durations[key],
            'opening': {'open': '00:00', 'close': '24:00', 'closed_weekdays': [], 'by_date': openings.get(key, {})},
            'price_cny': price / people, 'price_multipliers': {'adult': 1, 'child': 1, 'senior': 1},
            'catalog_signals': poi.get('catalog_signals', {}),
        })
    selected = context.get('moscow_transport_modes')
    mode = ('mixed' if len(selected) > 1 else selected[0]) if selected else MODES[profile['transport']]
    matrices = matrix.get('profiles') or {MODES[profile['transport']]: matrix}
    profiles = {}
    rows = {}
    for name, source in matrices.items():
        profile_rows = profiles.setdefault(name, {})
        for pair in source['pairs']:
            a, b = pair['from_poi_id'], pair['to_poi_id']
            actual_mode = 'walk' if name == 'transit' and not pair.get('transport_steps') else name
            value = None
            if pair['status'] == 'known' and (not selected or actual_mode in selected):
                group_factor = (1.2 if group['senior'] else 1.12 if group['child'] else 1) if actual_mode == 'walk' else 1
                minutes = max(1, ceil(pair['duration_seconds'] * group_factor / 60))
                cost_basis = 'no_fare' if actual_mode == 'walk' else 'unknown'
                value = {'base_minutes': minutes, 'planned_minutes': minutes, 'buffer_minutes': 0,
                         'mode': actual_mode, 'distance_m': pair['distance_meters'] or 0, 'transport_cost_cny': 0,
                         'source': pair['source_id'], 'basis': pair['basis'], 'transport_cost_basis': cost_basis}
                rows[a, b, name] = {**pair, 'mode': actual_mode,
                                    'provider_duration_seconds': pair['duration_seconds'],
                                    'group_time_factor': group_factor,
                                    'transport_cost_rub': 0 if actual_mode == 'walk' else None,
                                    'transport_cost_basis': cost_basis}
            profile_rows.setdefault(a, {})[b] = value
    first = context['days'][0]
    options = {'start_date': profile['date_start'], 'days': len(context['days']),
               'day_start': clock(first['day_start']), 'day_end': clock(first['day_end']),
               'pace': profile['pace'], 'transport': mode, 'group': group,
               'primary_interests': profile['primary_interests'], 'secondary_interests': profile['secondary_interests'],
               'required_ids': profile['required_poi_ids'], 'budget_cny': profile['budget']['amount']}
    if selected:
        options['transport_modes'] = selected
    dataset = {'pois': catalog, 'leg_profiles': profiles, 'currency': 'RUB', 'compact_waits': True}
    if context.get('day_anchors'):
        dataset['day_anchors'] = context['day_anchors']
    return dataset, options, rows


def saved_plan(native, context, rows, pace):
    days, meals = [], {}
    day_limits = {day['date']: day for day in context['days']}
    for day in native['days']:
        date = day['date']
        visits, legs, breaks = [], [], []
        warnings = list(day.get('warnings', []))
        for index, event in enumerate(day['events']):
            start, end = (timestamp(date, event[f'{kind}_min']) for kind in ('start', 'end'))
            if index and event['start_min'] > day['events'][index - 1]['end_min']:
                explanation = event.get('waiting_reason', '')
                reason = ('opening' if 'открыти' in explanation else 'meal_window' if 'приёма пищи' in explanation
                          or 'интервала' in explanation else 'search_limit')
                breaks.append({'kind': 'waiting', 'start_at': timestamp(date, day['events'][index - 1]['end_min']),
                               'end_at': start, 'reason_code': reason,
                               'reason': explanation or 'Поиск в пределах доступного набора остановок оставил свободное окно.'})
            if event['type'] == 'travel':
                a, b, mode = event['from_poi_id'], event['to_poi_id'], event['mode']
                pair = rows[a, b, event.get('leg_profile', mode)]
                legs.append({**pair, 'mode': mode, 'geometry': pair.get('geometry'), 'start_at': start, 'end_at': end,
                             'duration_seconds': (event['end_min'] - event['start_min']) * 60,
                             'from_poi_id': a, 'to_poi_id': b})
            elif event['type'] in ('visit', 'meal'):
                visits.append({'poi_id': event['poi_id'], 'start_at': start, 'end_at': end, 'price_rub': event.get('price_cny'),
                               'duration_basis': 'expert_estimate'})
                if event.get('meal_kind'):
                    meals.setdefault(date, {})[event['meal_kind']] = {'poi_id': event['poi_id'], 'start_at': start, 'end_at': end}
        if visits and 'lunch' not in meals.get(date, {}):
            limit = day_limits[date]
            window = {'start_at': max(limit['day_start'], timestamp(date, 12 * 60)),
                      'end_at': min(limit['day_end'], timestamp(date, 15 * 60))}
            if window['start_at'] < window['end_at']:
                if not add_lunch_break(breaks, day, date, window):
                    warnings.append('В обеденном окне не осталось 30 свободных минут. Проверьте время дня и обязательные места.')
        days.append({'date': date, 'visits': visits, 'legs': legs, 'breaks': breaks, 'pace': pace,
                     'start_at': min([timestamp(date, day['events'][0]['start_min']),
                                      *(item['start_at'] for item in breaks)]) if day['events'] else None,
                     'end_at': max([timestamp(date, day['events'][-1]['end_min']),
                                    *(item['end_at'] for item in breaks)]) if day['events'] else None,
                     'warnings': warnings})
    checks = {key: value for key, value in native.get('checks', {}).items()
              if key in {'required_places', 'time', 'transport', 'group', 'no_duplicates', 'meal_intervals'}}
    checks.update(opening_hours='unknown', budget='unknown')
    warnings = list(native.get('warnings', []))
    unknown_transport_costs = sum(leg['transport_cost_rub'] is None for day in days for leg in day['legs'])
    if unknown_transport_costs:
        warnings.append('Стоимость такси и общественного транспорта неизвестна и не включена в расчёт бюджета.')
    return {'schema_version': '0.2', 'model_version': native['model_version'], 'engine': 'shared-js',
            'status': 'infeasible' if native['status'] == 'infeasible' else 'unknown',
            'poi_snapshot_id': context['poi_snapshot_id'], 'days': days,
            'unplaced_required_poi_ids': native.get('unplaced_required_ids', []),
            'uncovered_interests': native.get('uncovered_interests', []),
            'budget_summary': {'currency': 'RUB', 'known_total': native['summary']['total_cost_cny'], 'status': 'unknown',
                               'unknown_transport_legs': unknown_transport_costs},
            'constraint_checks': checks, 'native_checks': native.get('checks', {}),
            'moscow_meals': meals, 'warnings': warnings,
            'ranking': native.get('ranking', []), 'engine_days': native['days'],
            **({'moscow_transport_modes': context['moscow_transport_modes']} if context.get('moscow_transport_modes') else {})}


def minutes_from_stamp(value):
    hour, minute = map(int, clock(value).split(':'))
    return hour * 60 + minute


def add_lunch_break(breaks, day, date, window):
    earliest, latest = map(minutes_from_stamp, (window['start_at'], window['end_at']))
    events = day['events']
    intervals = zip([earliest, *(item['end_min'] for item in events)],
                    [*(item['start_min'] for item in events), latest])
    available = [(max(left, earliest), min(right, latest)) for left, right in intervals]
    candidates = [(abs(max(left, min(13 * 60, right - 30)) - 13 * 60), max(left, min(13 * 60, right - 30)))
                  for left, right in available if right - left >= 30]
    if not candidates:
        return False
    _, chosen = min(candidates)
    start, end = timestamp(date, chosen), timestamp(date, chosen + 30)
    for item in breaks[:]:
        if item['start_at'] <= start and end <= item['end_at']:
            breaks.remove(item)
            if item['start_at'] < start:
                breaks.append({**item, 'end_at': start})
            if end < item['end_at']:
                breaks.append({**item, 'start_at': end})
            break
    breaks.append({'kind': 'meal', 'start_at': start, 'end_at': end, 'reason_code': 'meal',
                   'reason': 'Свободный обеденный перерыв. Подходящее заведение в расписание не вошло.'})
    breaks.sort(key=lambda item: item['start_at'])
    return True


def plan_city(profile, pois, matrix, context):
    dataset, options, rows = engine_input(profile, pois, matrix, context)
    native = bridge_call({'action': 'plan', 'dataset': dataset, 'options': options})
    return saved_plan(native, context, rows, profile['pace'])


def fixed_sequence(state, sequences, *, optimize=None):
    profile, context, pois = (state[key] for key in ('preferences', 'routing_context', 'pois'))
    dataset, options, rows = engine_input(profile, pois, state['travel_times'],
        {**context, 'opening_intervals': context.get('moscow_original_hours', context['opening_intervals'])})
    settings = {date: {'pace': pace} for date, pace in context.get('day_pace_overrides', {}).items()}
    meal_times, entries, base_days, changed_days = {}, {}, [], []
    originals = {day['date']: day for day in state['route'].get('engine_days', [])}
    for day in state['route']['days']:
        date = day['date']
        meals = context.get('moscow_meals', {}).get(date, {})
        by_poi = {slot['poi_id']: kind for kind, slot in meals.items()}
        meal_times[date] = {kind: value for kind, value in context.get('moscow_meal_pins', {}).get(date, {}).items()
                            if kind in meals}
        old_visits = {row['poi_id']: row for row in day['visits']}
        entries[date] = []
        for key in sequences[date]:
            entry = {'poi_id': key}
            if key in by_poi:
                entry['meal_kind'] = by_poi[key]
            hint = context.get('moscow_start_hints', {}).get(date, {}).get(key)
            if hint is not None:
                entry['start_min'] = hint
            elif date not in state.get('reflow_days', ()) and key in old_visits:
                hour, minute = map(int, clock(old_visits[key]['start_at']).split(':'))
                entry['start_min'] = hour * 60 + minute
            entries[date].append(entry)
        base_days.append(originals.get(date, {'date': date, 'events': []}))
        old_meals = state['route'].get('moscow_meals', {}).get(date, {})
        if (date not in originals or date in context.get('moscow_start_hints', {})
                or list(old_visits) != sequences[date] or meals != old_meals
                or settings.get(date, {}).get('pace', profile['pace']) != day.get('pace')
                or state.get('requested_transport')):
            changed_days.append(date)
    args = {'action': 'schedule', 'dataset': dataset, 'options': options,
            'base_plan': {'schema_version': 'shanghai-demo-v1', 'days': base_days},
            'sequences': entries, 'changed_days': changed_days,
            'day_settings': {day: settings[day] for day in changed_days if day in settings},
            'meal_times': {day: meal_times[day] for day in changed_days if meal_times[day]}}
    options['day_settings'] = settings
    options['meal_times'] = meal_times
    scheduled = bridge_call(args)
    if optimize:
        scheduled = bridge_call({'action': 'edit', 'dataset': dataset, 'options': scheduled['options'],
                                 'base_plan': scheduled['plan'], 'operations': [optimize]})
    result = saved_plan(scheduled['plan'], context, rows, profile['pace'])
    for day in result['days']:
        day['pace'] = context.get('day_pace_overrides', {}).get(day['date'], profile['pace'])
    return result
