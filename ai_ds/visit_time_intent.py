"""Распознавание явной команды закрепления уже запланированного визита."""

import re

from ai_ds.day_windows import valid_clock
from ai_ds.moscow_named_poi import _day_from_text, normalize


PIN_FIELDS = {'op', 'day_date', 'poi_id', 'start_time'}


def valid_pin(op):
    return (set(op) == PIN_FIELDS and isinstance(op.get('poi_id'), str) and
            bool(op['poi_id']) and isinstance(op.get('day_date'), str) and
            bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', op['day_date'])) and
            valid_clock(op.get('start_time')))


def local_visit_pin(text, days):
    if not re.match(r'^\s*(?:пожалуйста,?\s+)?(?:закрепи(?:ть)?|зафиксируй|зафиксировать)\b', text, re.I):
        return None
    if re.search(r'\b(?:и|затем|а также)\s+(?:добавь|замени|убери|удали|перенеси|закрепи|включи)\b', text, re.I):
        return None

    def clarify(message):
        return {'action': 'needs_clarification', 'operations': [], 'clarification': message}

    times = re.findall(r'(?<!\d)(\d{1,2}:[0-5]\d)\b', text)
    value = times[0].zfill(5) if len(times) == 1 else ''
    if not valid_clock(value):
        return clarify('Укажите точное время. Например: «Закрепи Эрмитаж в 14:30».')
    dates = [day['date'] for day in days]
    selected = _day_from_text(text, dates)
    if selected is False:
        return clarify('Укажите день из этой поездки.')
    phrase = normalize(text)
    matches = []
    for day in days:
        if selected and day['date'] != selected:
            continue
        for visit in day['visits']:
            name = normalize(visit['name'])
            if visit.get('type', 'visit') == 'visit' and name and f' {name} ' in f' {phrase} ':
                matches.append((len(name), day['date'], visit['poi_id']))
    matches.sort(reverse=True)
    if not matches or len(matches) > 1 and matches[0][0] == matches[1][0]:
        return clarify('Укажите название посещения из маршрута и, если оно повторяется, день.')
    _, day, key = matches[0]
    return {'action': 'propose', 'clarification': None,
            'operations': [{'op': 'pin_visit_time', 'day_date': day, 'poi_id': key, 'start_time': value}]}
