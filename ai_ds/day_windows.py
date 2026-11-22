"""Validation and local interpretation of itinerary day-window edits."""
import re

from ai_ds.moscow_named_poi import _day_from_text

MAX_TRIP_DAYS = 14
CLOCK_PATTERN = r'(?:[01][0-9]|2[0-3]):[0-5][0-9]'
DAY_WINDOW_FIELDS = {'op', 'day', 'start_time', 'end_time'}


def valid_clock(value):
    return isinstance(value, str) and re.fullmatch(CLOCK_PATTERN, value) is not None


def minutes(value):
    hour, minute = map(int, value.split(':'))
    return hour * 60 + minute


def local_day_window(text, dates):
    lower = text.casefold()
    if (re.search(r'завтрак|обед|ужин|перерыв|прибытие|вылет|прил[её]т', lower)
            or re.search(r'\b(?:добавь|убери|замени|посети)\b', lower)
            or re.match(r'^\s*перенеси\s+(?!начало\b|окончание\b)', lower)):
        return None
    interval = re.search(rf'\bс\s*({CLOCK_PATTERN})\s*(?:до|[-–—])\s*({CLOCK_PATTERN})\b', lower)
    start = re.search(rf'\b(?:нач(?:ни|ать|ало)|старт\w*)\s+(?:(?:день|маршрут)\s+)?(?:в|с|на)\s*({CLOCK_PATTERN})\b', lower)
    end = re.search(rf'\b(?:закончи|закончить|заверши|окончание|завершение)\s+(?:(?:день|маршрут)\s+)?(?:в|до|на)\s*({CLOCK_PATTERN})\b', lower)
    if re.search(r'\b(?:день|дня|дне|числа)\b', lower) and not interval:
        start = start or re.search(rf'\bс\s*({CLOCK_PATTERN})\s*[.!?]?$', lower)
        end = end or re.search(rf'\bдо\s*({CLOCK_PATTERN})\s*[.!?]?$', lower)
    if not interval and not start and not end:
        return None
    day = _day_from_text(text, dates)
    if not day or day not in dates[1:-1]:
        return {'action': 'needs_clarification', 'operations': [],
                'clarification': 'Укажите номер или дату промежуточного дня. Часы первого и последнего дня задаются в анкете.'}
    first, last = interval.groups() if interval else (start[1] if start else None, end[1] if end else None)
    return {'action': 'propose', 'clarification': None, 'operations': [
        {'op': 'set_day_window', 'day': day, 'start_time': first, 'end_time': last},
    ]}
