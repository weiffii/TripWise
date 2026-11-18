# Routing 0.2 — контракт AI/DS

18 ноября 2026. Реализация утверждённых R1–R5: ai_ds/routing_v02_contract.py и ai_ds/multiday.py. Старые контракты, Recommendation lexicographic-v0.1 и ai_ds/routing.py сохраняются. Новых зависимостей нет.

## Публичный вызов

```python
from ai_ds.recommendation import recommend
from ai_ds.multiday import build_route, schedule_sequences, validate_route
recommendation = recommend(preferences, pois)
route = build_route(preferences, pois, recommendation, travel_times, routing_context)
# Явные последовательности (редактирование):
route = schedule_sequences(preferences, pois, recommendation, travel_times, routing_context,
                           {'2026-11-18': ['source:poi-id']})
validation = validate_route(route, preferences, pois, recommendation, travel_times, routing_context)
```

preferences/POI/Recommendation остаются объектами 0.1; Recommendation валидирует профиль и весь каталог. Матрица и контекст передаются отдельно. Новый модуль выбирается явно; простая замена schema_version чужого JSON не является адаптером.

## routing_context

```json
{
  "schema_version": "0.2",
  "endpoint_policy": "first_last_poi",
  "pace_policy_version": "pace-policy-v0.2",
  "unknown_duration_policy": "strict",
  "poi_snapshot_id": "server-approved-snapshot",
  "days": [{
    "date": "2026-11-18",
    "day_start": "2026-11-18T09:00:00+03:00",
    "day_end": "2026-11-18T18:00:00+03:00",
    "meal_break": null,
    "blocked_intervals": [],
    "max_active_seconds": null
  }],
  "day_pace_overrides": {},
  "duration_overrides": [],
  "opening_intervals": [],
  "cost_context": null
}
```

Обязательны четыре policy/version поля и days. На каждую дату включительного диапазона preferences.date_start–date_end — ровно одно окно, дни сортируются по дате. Нет бизнес-лимита тремя днями/восемью POI. Timestamps aware; локальные дата, время и UTC offset соответствуют IANA timezone профиля. Несуществующее DST-время отклоняется, осенние повторения различаются явным offset. Продолжительность считается по абсолютным микросекундам.

meal_break — null либо `{kind:meal,start_at,end_at}`. blocked_intervals — массив `{start_at,end_at}`. Они положительны, целиком в окне, не пересекаются друг с другом; соседние границы разрешены. Отсутствующий обед не выдумывается. day_pace_overrides — словарь дата→relaxed/balanced/intensive; отдельное day.pace запрещено. Default — preferences.pace.

| Темп | Доля окна после вычета fixed-пауз | Отдых между посещениями |
|---|---:|---:|
| relaxed | 55/100 | 1200 секунд |
| balanced | 70/100 | 900 секунд |
| intensive | 85/100 | 600 секунд |

Активный лимит = floor_microseconds((окно − meal − blocked) × доля), затем min с положительным max_active_seconds, если задан. Отдых повторно из окна не вычитается: его учитывает расписание. Доли — согласованные стартовые экспериментальные параметры, не эмпирические нормы. Bool/NaN/Infinity/отрицательные значения и точность меньше микросекунды отклоняются.

## Sidecars

- duration_overrides: массив `{poi_id,minutes,basis:expert_estimate,method_version,author,recorded_at,evidence_url}`. Minutes положительно, ID известен, recorded_at aware, evidence_url nullable HTTP(S). Повтор ID запрещён. Методика реальных оценок утверждается отдельно; интерфейс не разрешает автоматически заполнять 2058 null. Оценка маркируется и не изменяет raw/POI.
- opening_intervals: массив `{poi_id,date,timezone,intervals,source_id,source_url,retrieved_at,verified_at}`. intervals=null/отсутствующая запись — неизвестно, [] — переданный источником закрытый день; иначе массив непересекающихся aware `{start_at,end_at}` внутри локальной даты. Источник непустой, retrieved_at aware; source_url/verified_at nullable. Timezone совпадает с профилем, повтор POI/дата запрещён. Структурная проверка не подтверждает туристический факт; POI.opening_hours 0.1 остаётся null.
- cost_context: `{currency,visit_costs,meal_costs,coverage_complete}`. Visit row: `{poi_id,amount,basis,source_url,recorded_at}`, meal row вместо poi_id содержит date. Currency — 3 заглавные латинские буквы, optional row.currency сохраняет несовпадение без конвертации. Amount nonnegative int/finite float либо null; basis source/expert_estimate/test_fixture; recorded_at aware, source_url nullable HTTP(S). Повторы ID/дат запрещены.

Бюджет охватывает выбранные посещения и питание всех дат поездки в scope trip_food_and_entertainment. При отсутствии visit sidecar используется POI.estimated_cost. Неизвестное не равно нулю. Известная сумма — нижняя граница: выше лимита → failed; missing/unknown/неполный scope/другая валюта → unknown; passed только при полном известном покрытии одной валютой. coverage_complete=true не закрывает отсутствующие строки. Budget=null → not_checked. Питание дня не выводится автоматически из цены одного кафе.

Суммы сравниваются через точные Fraction от десятичных входов. budget_summary.known_total — JSON integer для целого или точная десятичная строка для дробного; остальные поля status/currency/unknown_items/warnings. ESTIMATED_COST/TEST_COST_DATA не дают объявить общую реальную выполнимость.

## travel_times

Обязательны mode/basis/source_id/pairs. Mode совпадает с transport. Basis test_matrix/routing_provider/geometric_estimate; routing_provider требует aware retrieved_at, остальные допускают null. valid_for — null либо aware `{start_at,end_at}`; scheduler проверяет весь переход внутри применимости.

Pair: `{from_poi_id,to_poi_id,status,duration_seconds,distance_meters,geometry?,navigation_url?}`. Оба ID известны, self-pair и дубликат направления запрещены. Known duration ≥0 точно до микросекунды; distance nullable или конечное ≥0. Unknown/unreachable требуют null duration/distance и различаются; отсутствующий A→B не наследует B→A.

Geometry nullable либо GeoJSON LineString минимум с двумя `[longitude,latitude]` WGS84. Navigation URL nullable либо абсолютный HTTPS без credentials/пробелов/управляющих символов. Структура не доказывает доступность и лицензию URL. Геометрия/ссылка/время/метры/source/retrieved_at передаются в legs.

## TripRoute и валидация

Выход: schema_version=0.2, model_version=multiday-insertion-v0.2, route_id/version (у локального draft null), poi_snapshot_id, status, days, unplaced_required_poi_ids, unresolved_candidate_ids, warnings, constraint_checks, budget_summary, diagnostics, planning_metadata.

День содержит date/visits/legs/breaks/pace, активный лимит, отдых, active_seconds, visit_span_seconds (до конца последнего посещения), elapsed_seconds (включая последний явный fixed-перерыв), дневные checks/warnings. Visits: poi_id/start_at/end_at/duration_basis. Break kind: rest/meal/blocked/waiting. Отдыха после последнего посещения нет, целая активность не пересекает fixed-паузу. Внутреннее время — int микросекунды; выходные seconds — отображаемые числа.

Checks имеют ровно time/required_places/opening_hours/budget. Допустимы passed/failed/unknown; not_checked допустим только для budget. Нельзя заменить фактический not_checked на passed. Status feasible/unknown/infeasible; infeasible — только доказанный конфликт, не тупик эвристики. Синтетика, тестовые/геометрические переходы, оценочные/тестовые длительности/затраты и недостающие условия сохраняют общий unknown.

validate_route возвращает `{valid,errors,constraint_checks,route}` и независимо пересчитывает выбранные последовательности, сравнивает timestamps/дни/геометрию/бюджет/snapshot/required, модель, обязательные предупреждения и статусы. Это не доказательство оптимальности или независимая повторная генерация первоначального пула optional-кандидатов; diagnostics/planning_metadata не заменяют проверку данных. Backend использует собственный серверный snapshot, а не присланный клиентом маршрут.

RoutingV02Error.response(): `{schema_version:0.2,error:{code:ROUTING_VALIDATION_ERROR,details:[{path,code}]}}`. RecommendationError сохраняет свой контракт. CLI exit0 — структурированный результат (в том числе unknown/infeasible), exit1 — доменная валидация, exit2 — INPUT_ERROR. CLI stderr argparse может быть текстом. HTTP-эндпоинты этим Python-модулем не создаются.

## Проверка

18 contract +25 multiday +24 Assistant целевых тестов успешно; полный прогон 18 ноября —175/175 успешно. Полные логи reports/qa/unittest.2026-11-18.*. Сравнение exact: reports/routing/oracle-comparison.2026-11-18.json. Локальная корректность не означает готовности UI/БД или фактического расписания реальных музеев.
