"""Narrow deterministic interpretation for gap-filling requests.

This is a small validated command grammar, not an LLM imitation. The actual
selection, routing and validation remain in the authoritative scheduler.
"""
import re


_NUMBER_WORDS = {
    'первый': 1, 'первом': 1, 'первого': 1,
    'второй': 2, 'втором': 2, 'второго': 2,
    'третий': 3, 'третьем': 3, 'третьего': 3,
    'четвертый': 4, 'четвёртый': 4, 'четвертом': 4, 'четвёртом': 4,
    'пятый': 5, 'пятом': 5,
}


def gap_fill_decision(text, dates):
    """Return a safe, explicit operation or None for regular LLM interpretation.

    Intercept only an unmistakable request to fill idle time. Never swallow
    requests with other edits (cost limits, deletions, transport changes).
    """
    if not isinstance(text, str) or not isinstance(dates, list) or not dates:
        return None
    lower = text.casefold().replace('ё', 'е')
    if not re.search(r'перерыв|просто[йяе]|ожидани|пуст[а-я]*\s+(?:врем|дн|окн)|свободн[а-я]*\s+(?:окн|врем)|заполн[а-я]*\s+(?:дн|расписани)', lower):
        return None
    if not re.search(r'длинн|больш|много|слишком|сократ|заполн|устран|убер|избав|добав|мало', lower):
        return None
    # Named category constraints belong to the regular interpreter unless the
    # user expressly allows all categories.
    if re.search(r'музе[йяи]|театр|парки?|кафе|ресторан|магазин|природ|истори|развлеч|шопинг|смотров|необычн', lower) and not re.search(r'люб[а-я]*\s+(?:мест|категор)', lower):
        return None
    # Complex requests must be interpreted as a whole, never partly dropped.
    if re.search(r'замен[а-я]*\s+\S+|удал[а-я]*\s+\S+|перенес[а-я]*|измени\s+транспорт|только\s+такси|только\s+метро|[0-9]+\s*(?:руб|₽)', lower):
        return None
    # e.g. "в 2, 3, 4 день", "во 2 и 3 днях", "дни 2-4".
    numeric = re.findall(r'\b((?:\d{1,2}\s*[,и–-]\s*)*\d{1,2})\s*(?:-?й|-?м|-?х)?\s*(?:день|дня|дней|дни|днях|дне)\b', lower)
    numeric += re.findall(r'\bдн(?:и|ях|ей|я)\s+(\d{1,2}(?:\s*[-–,и]\s*\d{1,2})*)', lower)
    requested = set()
    for block in numeric:
        span = re.search(r'(\d{1,2})\s*[-–]\s*(\d{1,2})', block)
        if span:
            a, b = map(int, span.groups())
            if b < a:
                return None
            requested.update(range(a, b + 1))
        requested.update(map(int, re.findall(r'\d{1,2}', block)))
    # Plural ordinal names: "во второй, третий и четвертый дни".
    words = re.findall(r'\b[а-я]+\b', lower)
    for word in words:
        number = _NUMBER_WORDS.get(word)
        if number:
            requested.add(number)
    if not requested and re.search(r'\b(?:все|всех|каждом|каждый|повсюду)\b', lower):
        requested = set(range(1, len(dates)+1))
    if not requested:
        # Missing days: do not ask a redundant question: the request is global.
        requested = set(range(1, len(dates)+1))
    if min(requested) < 1 or max(requested) > len(dates):
        return {'action': 'needs_clarification', 'operations': [],
                'clarification': f'В поездке {len(dates)} дн. Укажите существующие дни для заполнения пауз.'}
    return {'action': 'propose', 'operations': [{'op': 'fill_gaps',
            'days': [dates[i-1] for i in sorted(requested)]}], 'clarification': None}


def interpret_with_retry(interpreter, payload, conversation):
    """One bounded recovery for a truncated OpenRouter answer, no paid model fallback.

    Retried context includes the full request and route dates, but fewer nearby
    catalog suggestions and old chat turns. Provider errors other than truncation
    are returned unchanged; no hallucinated operation is ever accepted.
    """
    from ai_ds.openrouter import OpenRouterError

    original = {**payload, 'conversation': conversation[-8:]}
    try:
        return interpreter(original)
    except OpenRouterError as error:
        if error.code != 'LLM_INCOMPLETE_RESPONSE':
            raise
    compact = {**payload, 'catalog': payload.get('catalog', [])[:20],
               'conversation': conversation[-3:]}
    return interpreter(compact)
