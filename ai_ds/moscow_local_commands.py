"""Offline, verified Moscow chat commands.

Only recognize small unambiguous instructions. Unrecognized or compound requests
are forwarded intact to the language model, never silently simplified. All
resulting operations go through the same route recomputation and confirmation.
"""
from __future__ import annotations

import re
from ai_ds.day_windows import local_day_window

from ai_ds.moscow_assistant_intents import gap_fill_decision
from ai_ds.moscow_named_poi import _day_from_text, named_poi_decision, normalize, resolve_name

_PACES = (
    ('relaxed', re.compile(r'\b(?:спокойн\w*|нетороплив\w*|медленн\w*|не\s+спеша)\b')),
    ('balanced', re.compile(r'\b(?:сбалансированн\w*|умеренн\w*)\b')),
    ('intensive', re.compile(r'\b(?:насыщенн\w*|интенсивн\w*|быстр\w*)\b')),
)
_DAY_SUFFIX = re.compile(
    r'\s+(?:на|в|во)\s+(?:\d{4}-\d{2}-\d{2}|'
    r'\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|'
    r'сентября|октября|ноября|декабря)|'
    r'\d{1,2}\s*(?:-?й|-?м)?\s*(?:день|дня|дне)|'
    r'(?:первый|второй|третий|четвертый|пятый)\s+день)\s*$', re.I)
_SPLIT = re.compile(r'\s+(?:и|а\s+также)\s+(?=(?:добавь|вставь|убери|удали|исключи|'
                    r'перенеси|перемести|передвинь|сделай|измени|поменяй|поставь|заполни|сократи)\b)', re.I)


def _clarify(message):
    return {'action': 'needs_clarification', 'operations': [], 'clarification': message}


def _propose(operations):
    return {'action': 'propose', 'operations': operations, 'clarification': None}


def _existing_name(phrase, catalog, current):
    subset = {key: catalog[key] for key in current if key in catalog}
    return resolve_name(phrase, subset)

_CATEGORY_WORDS = (
    ('museums', re.compile(r'\bмузе\w*\b')),
    ('nature', re.compile(r'\b(?:парк\w*|сад\w*|природ\w*)\b')),
    ('history', re.compile(r'\bистор\w*\b')),
    ('shopping', re.compile(r'\b(?:шопинг\w*|магазин\w*|торгов\w*\s+центр\w*)\b')),
    ('entertainment', re.compile(r'\b(?:театр\w*|развлеч\w*)\b')),
    ('viewpoints', re.compile(r'\bсмотров\w*\b')),
    ('unusual', re.compile(r'\bнеобычн\w*\b')),
)

def _category_request(text, dates):
    """Only a generic attraction request; names must use the named matcher."""
    lower = normalize(text)
    if not re.match(r'^(?:добавь|добави|дабавь|включи|подбери|найди)\b', lower):
        return None
    # Reject non-category modifiers that would require semantic interpretation
    # rather than silently dropping constraints (e.g., accessibility/price).
    if re.search(r'\b(?:дешев\w*|бесплатн\w*|дорог\w*|рядом\w*|неподалеку|'
                r'коляск\w*|питомц\w*|инвалид\w*|только\w*)\b', lower):
        return None
    matching = [category for category, pattern in _CATEGORY_WORDS if pattern.search(lower)]
    if len(matching) != 1:
        return None
    category = matching[0]
    # "Музей Пушкина" is NOT a request for an arbitrary museum. After
    # removing command/qualifiers/date/category, a proper name must be handled
    # by named POI matching, not this category shortcut.
    remaining = lower
    remaining = re.sub(r'^(?:добавь|добави|дабавь|включи|подбери|найди)\b', '', remaining)
    remaining = re.sub(r'\b(?:в|во|на|мой|маршрут|маршруте|одно|один|одну|еще|ещё|'
                       r'какой-нибудь|несколько|новый|новое|интересный|место|мест|день|дня|дни|'
                       r'первый|второй|третий|четвертый|пятый)\b', ' ', remaining)
    remaining = re.sub(r'\b(?:\d+|января|февраля|марта|апреля|мая|июня|июля|августа|'
                       r'сентября|октября|ноября|декабря)\b', ' ', remaining)
    remaining = _DAY_SUFFIX.sub('', remaining)
    for _, pattern in _CATEGORY_WORDS:
        remaining = pattern.sub(' ', remaining)
    if remaining.strip():
        return None
    day = _day_from_text(text, dates)
    if day is False:
        return _clarify('Этого дня нет в поездке.')
    return _propose([{'op': 'add_category_poi', 'category': category, 'day': day}])


def _single(text, dates, catalog, current, history, selected_modes):
    # Explicit existing-stop moves/removals are never confused with adding a
    # similarly named place elsewhere in the city.
    lower = normalize(text)
    window = local_day_window(text, dates)
    if window is not None:
        return window
    # Handle transport/idle-time requests before the generic "remove place"
    # grammar ("убери такси" and "убери длинные перерывы" are not POI names).
    direct_gap = gap_fill_decision(text, dates)
    if direct_gap is not None:
        return direct_gap
    if re.search(r'\b(?:без|убери|исключи|не\s+используй)\s+такси\b', lower):
        modes = [mode for mode in (selected_modes or []) if mode != 'taxi']
        return (_propose([{'op': 'set_transport_modes', 'modes': modes}]) if modes
                else _clarify('Такси — единственный разрешённый режим. Разрешить пешие переходы или общественный транспорт?'))
    move = re.match(r'^(?:перенеси|перемести|передвинь)\s+(.+)$', lower)
    remove = re.match(r'^(?:убери|удали|исключи|убрать|удалить)\s+(.+)$', lower)
    if move or remove:
        phrase = (move or remove)[1]
        phrase = re.sub(r'^\s*(?:посещение|место|локацию|из\s+маршрута)\s+', '', phrase)
        target_day = _day_from_text(text, dates) if move else None
        if move and not target_day:
            return _clarify('На какой день перенести место? Укажите дату или номер дня поездки.')
        if target_day is False:
            return _clarify('Этого дня нет в поездке.')
        phrase = _DAY_SUFFIX.sub('', phrase).strip(' ,.!?')
        key, name = _existing_name(phrase, catalog, current)
        if key == 'ambiguous':
            return _clarify('В маршруте несколько похожих мест: ' + '; '.join(name))
        if key is None:
            return _clarify(f'Не нашёл в текущем расписании «{phrase}». Проверьте название посещения.')
        return _propose([{'op': 'move_poi', 'poi_id': key, 'day': target_day}] if move else
                        [{'op': 'remove_poi', 'poi_id': key}])

    # Do not reinterpret an unfinished compound edit as one bizarre POI
    # name; pass the complete request to the LLM for proper interpretation.
    if re.search(r'\b(?:и|а\s+также)\s+(?:переделай|перепланируй|переорганизуй)\b', lower):
        return None

    # Tempos: explicit desire for a different pace is preserved per day; a
    # global instruction alters each day, not the travel party or budget.
    pace_words = any(pattern.search(lower) for _, pattern in _PACES)
    if (re.search(r'\b(?:темп|ритм|спокойнее|насыщеннее|помедленнее|поинтенсивнее)\b', lower)
            or (pace_words and re.match(r'^(?:сделай|поставь|измени|поменяй|хочу)\b', lower))):
        paces = [pace for pace, pattern in _PACES if pattern.search(lower)]
        if len(paces) == 1:
            target_day = _day_from_text(text, dates)
            if target_day is False:
                return _clarify('Указанный день отсутствует в поездке.')
            days = [target_day] if target_day else dates
            return _propose([{'op': 'set_day_pace', 'day': day, 'pace': paces[0]} for day in days])

    # Grounded mode switches. Do not interpret "только метро" as generic
    # transit: that would silently authorize buses, which is not equivalent.
    only = re.search(r'\bтолько\s+(пешком|на\s+такси|такси|общественн\w*\s+транспорт\w*)\b', lower)
    if only:
        chosen = only[1]
        mode = 'walk' if 'пешком' in chosen else 'taxi' if 'такси' in chosen else 'transit'
        return _propose([{'op': 'set_transport_modes', 'modes': [mode]}])
    without = re.search(r'\b(?:без|убери|исключи|не\s+используй)\s+(такси)\b', lower)
    if without:
        modes = [mode for mode in (selected_modes or []) if mode != 'taxi']
        if not modes:
            return _clarify('Такси — единственный разрешённый режим. Разрешить пешие переходы или общественный транспорт?')
        return _propose([{'op': 'set_transport_modes', 'modes': modes}])

    category = _category_request(text, dates)
    if category is not None:
        return category
    return named_poi_decision(text, dates, catalog, current, history)


def local_command_decision(text, dates, catalog, current=(), history=(), selected_modes=None):
    """Best-effort exact operation parsing; None means delegate full request."""
    if not isinstance(text, str) or not text.strip():
        return None
    chunks = _SPLIT.split(text.strip())
    # If a compound edit includes a nonlocal clause, don't throw that clause
    # away just because one segment was locally recognized.
    parsed = [_single(part, dates, catalog, current, history, selected_modes) for part in chunks]
    if any(part is None for part in parsed):
        return None
    if len(chunks) == 1:
        return parsed[0]
    if any(part['action'] != 'propose' for part in parsed):
        return next(part for part in parsed if part['action'] != 'propose')
    operations = [op for part in parsed for op in part['operations']]
    if not operations or len(operations) > 12:
        return None
    return _propose(operations)
