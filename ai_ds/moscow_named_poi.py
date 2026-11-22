"""Grounded, offline handling of explicit Moscow POI-name requests.

No language-model calls, no new fabricated points, no heuristic substitution of
an unrelated place. This module only resolves a real ID already in Moscow V7.
"""
from __future__ import annotations

from datetime import date
from difflib import SequenceMatcher
import re

_MONTHS = {
    'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5,
    'июня': 6, 'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10,
    'ноября': 11, 'декабря': 12,
}
_MONTHS_RE = '|'.join(_MONTHS)
_NUMBER_WORDS = {
    'первый': 1, 'первом': 1, 'первого': 1,
    'второй': 2, 'втором': 2, 'второго': 2,
    'третий': 3, 'третьем': 3, 'третьего': 3,
    'четвертый': 4, 'четвертом': 4, 'четвертого': 4,
    'пятый': 5, 'пятом': 5, 'пятого': 5,
    'шестой': 6, 'шестом': 6, 'шестого': 6,
    'седьмой': 7, 'седьмом': 7, 'седьмого': 7,
    'восьмой': 8, 'восьмом': 8, 'восьмого': 8,
    'девятый': 9, 'девятом': 9, 'девятого': 9,
    'десятый': 10, 'десятом': 10, 'десятого': 10,
    'одиннадцатый': 11, 'одиннадцатом': 11, 'одиннадцатого': 11,
    'двенадцатый': 12, 'двенадцатом': 12, 'двенадцатого': 12,
    'тринадцатый': 13, 'тринадцатом': 13, 'тринадцатого': 13,
    'четырнадцатый': 14, 'четырнадцатом': 14, 'четырнадцатого': 14,
}
# Search variants are ONLY queries into existing V7 names, not fabricated POI IDs.
_COMMON_ALIASES = {
    'уголок дедушки дурова': ('уголок дурова', 'театр зверей дурова', 'театр дурова'),
    'уголок дурова': ('уголок дедушки дурова', 'театр зверей дурова'),
}
# Confirmed in snapshot/tripwise-current/data/demo/moscow/v7/pois_real_expanded.csv:
# moscow-cultural-0,Уголок дедушки Дурова,55.77933,37.62121,entertainment.
# This is a lookup hint, not a synthetic POI. Only use if the ID exists.
_KNOWN_V7_IDS = {'уголок дедушки дурова': 'moscow-cultural-0',
                 'уголок дурова': 'moscow-cultural-0'}
_ADD = re.compile(
    r'\b(?:добавь|добавить|включи|включить|вставь|вставить|запланируй|'
    r'запиши|внеси|поставь|добави|дабавь|'
    r'(?<!не )хочу\s+(?:в|во|на|к|попасть\s+(?:в|на)|пойти\s+(?:в|на)|'
    r'сходить\s+(?:в|на)|посетить|увидеть|добавить)|'
    r'давай\s+(?:добавим|посетим|сходим\s+(?:в|на)|в|во)|'
    r'поехали\s+(?:в|на)|пойдем\s+(?:в|на))\b', re.I)
_QUESTION = re.compile(r'\b(?:почему|отчего|зачем|как\s+так|не\s+появил|не\s+добавил|куда\s+дел)\b', re.I)
_AUTO_REPLACE = re.compile(r'\bзамени(?:ть)?\s+автоматически\b|\bподбери\s+замену\b', re.I)
_STOP = {'мне', 'мой', 'моя', 'мое', 'мою', 'пожалуйста', 'маршрут', 'маршрута',
         'поездку', 'в', 'во', 'на', 'к', 'за', 'вместо', 'и', 'посещение', 'место',
         'это', 'туда', 'имени', 'им', 'года', 'день', 'дня', 'дней', 'дни',
         'первый', 'второй', 'третий', 'четвертый', 'пятый', 'автоматически', 'замени'}
_GENERIC = {'музей', 'музея', 'музеев', 'парк', 'парков', 'кафе', 'ресторан',
            'ресторанов', 'театр', 'театров', 'место', 'мест', 'места', 'сквер',
            'скверов', 'достопримечательность', 'достопримечательностей',
            'магазин', 'магазинов', 'еда', 'развлечение', 'развлечений',
            'больше', 'побольше', 'много', 'несколько', 'другие', 'еще', 'заведений'}
_GENERIC_QUALIFIERS = {'недорогой', 'недорогое', 'бесплатный', 'бесплатное',
                       'дешевый', 'дешевое', 'любой', 'любое', 'рядом', 'поближе',
                       'интересный', 'интересное', 'новый', 'новое', 'необычный',
                       'хороший', 'хорошее', 'близкий', 'поблизости', 'вкусный'}


def normalize(value):
    return ' '.join(re.findall(r'[a-zа-я0-9]+', str(value).casefold().replace('ё', 'е')))


def _name_words(value):
    return {w[:5] for w in normalize(value).split() if len(w) >= 4 and w not in _STOP}


def _day_from_text(text, dates):
    low = normalize(text)
    m = re.search(r'\b(\d{4}) (\d{1,2}) (\d{1,2})\b', low)
    if m:
        day = f'{int(m[1]):04}-{int(m[2]):02}-{int(m[3]):02}'
        return day if day in dates else False
    m = re.search(rf'\b([12]?\d|3[01])\s+({_MONTHS_RE})\b', low)
    if m:
        matches = [d for d in dates if date.fromisoformat(d).day == int(m[1])
                   and date.fromisoformat(d).month == _MONTHS[m[2]]]
        return matches[0] if len(matches) == 1 else False
    m = re.search(r'\b([12]?\d|3[01])[./]([01]?\d)\b', text)
    if m:
        matches = [d for d in dates if date.fromisoformat(d).day == int(m[1])
                   and date.fromisoformat(d).month == int(m[2])]
        return matches[0] if len(matches) == 1 else False
    m = re.search(r'\b(\d{1,2})\s*(?:-?(?:й|м)|)\s*(?:день|дня|дне|дни)\b', low)
    if not m:
        m = re.search(r'\b(?:день|дня|дне)\s+(\d{1,2})\b', low)
    if m:
        return dates[int(m[1])-1] if 1 <= int(m[1]) <= len(dates) else False
    words = low.split()
    for word in words:
        number = _NUMBER_WORDS.get(word)
        if number and re.search(r'\b(?:день|дня|дне|дни)\b', low):
            return dates[number-1] if number <= len(dates) else False
    # Follow-up answer: "22, замени автоматически" (a day of the month).
    if _AUTO_REPLACE.search(low) or re.search(r'\b\d{1,2}\s+числа\b', low):
        m = re.search(r'\b([12]?\d|3[01])(?:\s+числа\b|\b)', low)
        if m:
            matches = [d for d in dates if date.fromisoformat(d).day == int(m[1])]
            return matches[0] if len(matches) == 1 else False
    return None


def _target_phrase(text):
    match = _ADD.search(text)
    if not match:
        return None
    value = text[match.end():]
    value = re.sub(r'\b\d{4}-\d\d-\d\d\b|\b\d{1,2}[./]\d{1,2}\b|\b\d{1,2}:\d{2}\b', ' ', value)
    value = re.sub(rf'\b(?:[12]?\d|3[01])\s*(?:{_MONTHS_RE}|числа)\b', ' ', value, flags=re.I)
    value = normalize(value)
    ordinals = '|'.join(_NUMBER_WORDS)
    value = re.sub(rf'\b(?:{ordinals}|\d{{1,2}}(?:\s+[йм])?)\s+(?:день|дня|дне|дни)\b'
                   r'|\b(?:день|дня|дне)\s+\d{1,2}\b', ' ', value)
    value = re.split(r'\b(?:но\s+не|если|чтобы|без\s+(?:замен\w*|удалени\w*|изменени\w*))\b',
                     value, maxsplit=1)[0]
    tokens = [w for w in value.split() if w not in _STOP and w not in _MONTHS]
    if not tokens or all(token.isdigit() or token in _GENERIC | _GENERIC_QUALIFIERS for token in tokens):
        return None
    return ' '.join(tokens)



def _typo_score(phrase, name):
    """Conservative typo matching for a real Moscow catalog entry.

    Does not treat a matching surname or one common word as a place identity.
    Use no external libraries and never invent a POI.
    """
    q = [word for word in normalize(phrase).split() if len(word) >= 4 and word not in _STOP]
    words = [word for word in normalize(name).split() if len(word) >= 4 and word not in _STOP]
    if not q or not words:
        return 0
    if len(q) == 1 and len(q[0]) < 7:
        return 0
    # For multi-word names a typo in almost every token is acceptable, but
    # at least two distinct words must still agree closely.
    strengths = [max(SequenceMatcher(None, token, item).ratio() for item in words) for token in q]
    strong = sum(v >= .76 for v in strengths)
    if strong < min(2, len(q)) or sum(strengths) / len(q) < .82:
        return 0
    raw_similarity = SequenceMatcher(None, normalize(phrase), normalize(name)).ratio()
    if raw_similarity < .50 and len(q) >= 2:
        return 0
    # Penalize names that add many unrelated words; a rough substring can
    # otherwise incorrectly match a large museum/entertainment complex.
    extras = max(0, len(words) - len(q))
    return round(520 + 290 * sum(strengths) / len(q) + 100 * raw_similarity - 35 * extras)


def resolve_name(phrase, catalog):
    """Return (poi_id, name), ('ambiguous', names), or (None, None)."""
    variants = [normalize(phrase)]
    query_numbers = set(re.findall(r'\b\d+\b', variants[0]))
    words = variants[0].split()
    # Match an accusative adjective against the exact catalog title:
    # «Красную площадь» -> «Красная площадь». The head noun stays unchanged.
    if len(words) > 1:
        endings = {'ую': 'ая', 'юю': 'яя'}
        nominative = ' '.join([*(word[:-2] + endings[word[-2:]]
                                 if len(word) > 4 and word[-2:] in endings else word
                                 for word in words[:-1]), words[-1]])
        if nominative != variants[0]:
            variants.append(nominative)
    # Prefer the actual V7 ID for an unambiguous, verified alias. Never invent
    # a record or substitute an unrelated place based on a similar surname.
    known_id = _KNOWN_V7_IDS.get(variants[0])
    if known_id in catalog:
        point = catalog[known_id]
        candidate_name = normalize(point.get('name', ''))
        if 'дуров' in candidate_name and (
                'уголок' in candidate_name or
                ('театр' in candidate_name and 'терез' not in candidate_name)):
            return known_id, point['name']
    for known, aliases in _COMMON_ALIASES.items():
        if normalize(known) in variants[0]:
            variants.extend(normalize(alias) for alias in aliases)
    best = []
    for key, point in catalog.items():
        names = [point.get('name', ''), *(point.get('search_names') or ())]
        score = 0
        for name_index, name in enumerate(names):
            if not isinstance(name, str):
                continue
            clean = normalize(name)
            if query_numbers and query_numbers != set(re.findall(r'\b\d+\b', clean)):
                continue
            name_without_title = ' '.join(word for word in clean.split() if word not in ('имени', 'им'))
            # The Durov surname occurs in other Moscow venues and monuments.
            # Do not substitute an unrelated Durov-family attraction when a
            # request specifically names Grandfather Durov's animal theatre.
            if variants[0] in _KNOWN_V7_IDS and not (
                'дуров' in clean and ('уголок' in clean or
                ('театр' in clean and 'терез' not in clean))):
                continue
            words = _name_words(name)
            for phrase_variant in variants:
                query_words = _name_words(phrase_variant)
                if not query_words:
                    continue
                exact = clean == phrase_variant
                substring = (len(phrase_variant) >= 8 and phrase_variant in clean)
                overlap = len(query_words & words)
                # Require either an exact/contained phrase or two distinctive
                # matching stems; one unique full word only if it spans 7+ chars.
                if exact:
                    # Prefer an exact primary name to a different record which
                    # merely lists this phrase among its alternate names.
                    candidate = 1000 if name_index == 0 else 850
                elif clean != name_without_title and name_without_title == phrase_variant:
                    candidate = 975 if name_index == 0 else 830
                elif substring:
                    candidate = 900 if name_index == 0 else 780
                elif overlap >= 2 and overlap * 3 >= len(query_words) * 2:
                    candidate = 550 + int(80 * overlap / max(len(words), 1))
                elif len(query_words) == 1 and overlap and len(phrase_variant) >= 7:
                    candidate = 400
                else:
                    candidate = 0
                score = max(score, candidate)
                if not exact and not substring:
                    score = max(score, _typo_score(phrase_variant, name))
        if score:
            best.append((score, str(point.get('name', key)), key))
    if not best:
        return None, None
    best.sort(key=lambda row: (-row[0], row[1], row[2]))
    # Equal-high scores can be two distinct OSM objects. Never guess which ID.
    if len(best) > 1 and best[0][0] - best[1][0] < 90:
        return 'ambiguous', [name for _, name, _ in best[:3]]
    return best[0][2], best[0][1]


def named_poi_decision(text, dates, catalog, current=(), history=()):
    """Deterministic named addition / authorized replacement / diagnostic.

    Returns None if this is not a narrow named POI command. The language model
    can then handle broad or compound requests (unless its own quota is reached).
    """
    if not isinstance(text, str) or not isinstance(dates, list) or not dates:
        return None
    normalized = normalize(text)
    around = re.search(r'(?<!не )\b(?:переделай|перестрой|перепланируй)\b.*?\b(?:ближе\s+к|вокруг|рядом\s+с)\s+(.+)', normalized)
    replacement = re.match(r'^замени\s+(?:любой|любое|любую|один|одно)\s+.+?\s+на\s+(.+)', normalized)
    if normalized in {'любая', 'любое', 'любой', 'любую', 'любое место'}:
        # Bind a short reply only to the immediately preceding unresolved request.
        if len(history) >= 2 and history[-1].get('role') == 'assistant':
            previous = history[-2].get('content', '') if history[-2].get('role') == 'user' else ''
            target = _target_phrase(previous)
            question_text = history[-1].get('content', '')
            if not target:
                prior_replacement = re.match(r'^замени\s+.+?\s+на\s+(.+)', normalize(previous))
                target = prior_replacement[1] if prior_replacement else None
            if re.search(r'замен|какое место', question_text, re.I) and target:
                text = 'замени любое место на ' + target
                earlier_day = _day_from_text(previous, dates) or _day_from_text(question_text, dates)
                if earlier_day:
                    text += ' ' + earlier_day
                normalized = normalize(text)
                replacement = re.match(r'^замени\s+любое\s+место\s+на\s+(.+)', normalized)
            elif re.search(r'замен|какое место', question_text, re.I):
                return {'action': 'needs_clarification', 'operations': [],
                        'clarification': 'Можно заменить любую необязательную остановку. Укажите, какое место нужно добавить вместо неё.'}
    if around or replacement:
        phrase = (around or replacement)[1]
        phrase = re.split(r'\b(?:чтобы|можешь|можно|если)\b', phrase)[0]
        phrase = re.sub(r'\b(?:\d{4}\s+\d{1,2}\s+\d{1,2}|\d{1,2}\s+числа)\b', '', phrase).strip()
        key, _ = resolve_name(phrase, catalog)
        if key and key != 'ambiguous':
            day = _day_from_text(text, dates)
            if day is False or around and not day and len(dates) != 1:
                return {'action': 'needs_clarification', 'operations': [],
                        'clarification': 'Укажите дату или номер дня из вашей поездки.'}
            return {'action': 'propose', 'clarification': None, 'operations': [{
                'op': 'recenter_day' if around else 'replace_any_named', 'poi_id': key,
                'day': day or (dates[0] if len(dates) == 1 else None),
            }]}
    query = _target_phrase(text)
    question = bool(_QUESTION.search(text)) and not _ADD.search(text)
    replace_followup = bool(_AUTO_REPLACE.search(text)) and not _ADD.search(text)
    if query is None and not (question or replace_followup):
        # A single POI name typed into chat is a normal request to visit it.
        # Only accept a bare name if the catalog resolves it clearly; don't
        # hijack normal conversation or broad category requests.
        plain = re.sub(r'\b(?:\d{4}\s+\d{1,2}\s+\d{1,2}|\d{1,2}\s+числа)\b', '',
                       normalize(text)).strip()
        words = plain.split()
        if (1 <= len(words) <= 8 and not all(w in _GENERIC for w in words)
                and not any(w in {'не', 'нет', 'почему', 'зачем', 'убери', 'удали',
                                  'перенеси', 'передвинь', 'замени'} for w in words)):
            key, _ = resolve_name(plain, catalog)
            if key and key != 'ambiguous':
                query = plain
        if query is None:
            return None
    if query and re.search(r'\b(?:и|также|а затем)\s+(?:убери|измени|заполни|сократи|перенеси|удали)\b', text, re.I):
        # This is a compound edit: an isolated named-place command must not
        # silently discard the other half of the request.
        return None
    if query is None:
        if question:
            # A new named question takes precedence over earlier chat context.
            m = re.search(r'\b(?:почему\s+(?:не\s+)?(?:добавил(?:ся|ось|и)?|появил(?:ся|ось))|'
                          r'куда\s+дел(?:ся|ось)?)\b\s*(.+)', text, re.I)
            if m:
                query = normalize(m[1]).strip(' ?.!')
        if query is None and replace_followup:
            # A short "replace automatically" is meaningful only following
            # a previous explicit named request. Never guess a new target.
            for item in reversed(history[-16:]):
                if item.get('role') == 'user':
                    query = _target_phrase(item.get('content', ''))
                    if query:
                        break
        if query is None:
            return None
    day = _day_from_text(text, dates)
    if day is False:
        return {'action': 'needs_clarification', 'operations': [],
                'clarification': 'Указанная дата или номер дня отсутствует в этой поездке.'}
    key, name = resolve_name(query, catalog)
    if key == 'ambiguous':
        return {'action': 'needs_clarification', 'operations': [],
                'clarification': 'Найдено несколько похожих мест: ' + '; '.join(name) + '. Укажите точное название.'}
    if key is None:
        return {'action': 'needs_clarification', 'operations': [],
                'clarification': f'Не нашёл «{query}» среди мест текущей Moscow V7. Не буду подставлять другое место. Проверьте точное название в каталоге.'}
    if key in current:
        return {'action': 'needs_clarification', 'operations': [],
                'clarification': f'«{name}» уже есть в маршруте. Проверьте день посещения.'}
    if question:
        return {'action': 'needs_clarification', 'operations': [],
                'clarification': (f'«{name}» есть в московском каталоге, но в сохранённом маршруте этого места нет. '
                    'Предыдущее предложение могло не быть подтверждено или содержать замену другого POI; '
                    'по одному маршруту точную причину не определить. '
                    f'Напишите «добавь {name}», затем проверьте предварительный просмотр и подтвердите его.')}
    return {'action': 'propose', 'operations': [{
        'op': 'add_named_poi', 'poi_id': key, 'day': day,
        'replace_if_needed': bool(replace_followup or re.search(r'\b(?:замени|вместо|заменить)\b', text, re.I)),
    }], 'clarification': None}
