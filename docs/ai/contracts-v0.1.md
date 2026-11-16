# TripWise — контракт рекомендательного прототипа 0.1

Согласован владельцем AI/DS в чате 16 ноября 2026 года. Согласование с интерфейсами Fullstack требуется при интеграции. Документ фиксирует интерфейс, а не наличие реализации. Математическая модель и коэффициенты согласуются отдельно с Recommendation Agent.

## 1. Область и формат

Первый этап: локальный JSON-вход → валидация → ранжирование → JSON-выход. HTTP, БД, LLM и реализация маршрутизатора в этот этап не входят. Полный Free MVP из FEATURES.md сохраняется.

- UTF-8, `schema_version: "0.1"` в запросе, профиле и ответе.
- Все перечисленные в примерах ключи обязательны; допустимость `null` описана ниже.
- Неизвестное значение — `null`; ноль означает известный ноль. NaN и Infinity запрещены.
- ID — непустые строки, единые между компонентами. ID POI уникальны в наборе.
- Даты — `YYYY-MM-DD`; временные метки — ISO 8601 с часовым смещением.
- Все примеры POI и их характеристик ниже синтетические.

## 2. Категории

| Код | Значение |
|---|---|
| museums | Музеи |
| history | История |
| nature | Природа |
| cafes | Кафе |
| entertainment | Развлечения |
| shopping | Шопинг |
| viewpoints | Смотровые площадки |
| unusual | Необычные места |

## 3. UserPreferences

```json
{
  "schema_version": "0.1",
  "destination": {"country_code": "RU", "city": "Тестовый город"},
  "date_start": "2026-11-17",
  "date_end": "2026-11-17",
  "timezone": "Europe/Moscow",
  "primary_interests": ["museums", "history"],
  "secondary_interests": ["nature"],
  "pace": "balanced",
  "transport": "walking",
  "budget": {
    "amount": 3000,
    "currency": "RUB",
    "scope": "trip_food_and_entertainment"
  },
  "required_poi_ids": ["synthetic:museum-01"],
  "top_k": 5
}
```

| Поле | Тип и ограничения |
|---|---|
| destination | Объект: country_code — двухбуквенный код страны; city — непустая строка |
| date_start, date_end | Корректные даты; date_end ≥ date_start |
| timezone | Название часового пояса для будущего расписания |
| primary_interests | 1–2 уникальные категории из словаря |
| secondary_interests | Уникальные категории; можно []; пересечение с primary_interests запрещено |
| pace | relaxed / balanced / intensive |
| transport | walking / car / public_transit |
| budget | Объект либо null, если бюджет не задан |
| budget.amount | Конечное неотрицательное число в основных денежных единицах |
| budget.currency | Трёхбуквенный код валюты |
| budget.scope | trip_food_and_entertainment: общий бюджет на питание и развлечения за всю поездку |
| required_poi_ids | Массив уникальных ID; можно [] |
| top_k | Положительное целое; при недостатке кандидатов выдаётся меньше |

Конвертация валют в прототип не входит. Обязательные места передаются Routing независимо от попадания в top-K; ранжирование не гарантирует посещение.

## 4. POI

```json
{
  "id": "synthetic:museum-01",
  "name": "Тестовый музей",
  "country_code": "RU",
  "city": "Тестовый город",
  "coordinates": {"latitude": 55.75, "longitude": 37.62},
  "categories": ["museums", "history"],
  "rating": {"value": 4.5, "scale_min": 0, "scale_max": 5},
  "popularity": {"value": 120, "metric": "review_count"},
  "visit_duration_minutes": 60,
  "visit_duration_basis": "test_fixture",
  "estimated_cost": null,
  "opening_hours": null,
  "availability": "unknown",
  "provenance": {
    "source_id": "synthetic_fixture",
    "source_record_id": "museum-01",
    "source_url": null,
    "retrieved_at": null,
    "verified_at": null,
    "is_synthetic": true
  }
}
```

| Поле | Тип и ограничения |
|---|---|
| id, name, country_code, city | Непустые строки; country_code — двухбуквенный код |
| coordinates | Объект либо null; latitude ∈ [-90,90], longitude ∈ [-180,180], градусы |
| categories | Непустой массив уникальных категорий из словаря |
| rating | Объект либо null; scale_min < scale_max; value внутри шкалы |
| popularity | Объект либо null; value — конечное неотрицательное число, metric — непустая строка |
| visit_duration_minutes | Положительное целое либо null; минуты |
| visit_duration_basis | source / expert_estimate / test_fixture; null при неизвестной длительности |
| estimated_cost | Объект с amount ≥ 0 и currency либо null; одно посещение одним человеком |
| opening_hours | В 0.1 только null: расписания не интерпретируются |
| availability | available / unavailable / unknown; не подтверждает открытость в конкретную дату |
| provenance | Объект происхождения данных |

В provenance обязательны все ключи примера. source_id и source_record_id — непустые строки; source_url — URL либо null; retrieved_at и verified_at — временные метки либо null; is_synthetic — boolean. Для реальных POI retrieved_at обязателен и не null, is_synthetic = false. verified_at = null означает отсутствие зафиксированной проверки.

Первая версия использует один источник на набор. Смешивание источников требует согласования provenance на уровне полей. Нормализацию рейтинга и популярности определяет отдельно утверждаемая модель. Синтетические fixtures хранятся отдельно от реальных данных и не закрывают F03.

## 5. Запрос и ответ Recommendation

Запрос содержит schema_version, preferences (полный UserPreferences) и pois (массив полных POI). Пустой массив pois допустим; ссылки required_poi_ids должны разрешаться в переданном наборе.

Пример результата для тестового запроса с top_k = 1:

```json
{
  "schema_version": "0.1",
  "model_version": "test-example-not-an-approved-model",
  "items": [
    {
      "poi_id": "synthetic:museum-01",
      "rank": 1,
      "score": null,
      "reasons": [
        {"code": "PRIMARY_INTEREST_MATCH", "categories": ["museums", "history"]}
      ]
    }
  ],
  "excluded": [],
  "required_poi_ids": ["synthetic:museum-01"],
  "warnings": [
    {"code": "SYNTHETIC_DATA", "poi_id": "synthetic:museum-01"},
    {"code": "BUDGET_NOT_VERIFIED", "poi_id": null},
    {"code": "OPENING_HOURS_NOT_VERIFIED", "poi_id": null}
  ],
  "constraint_checks": {
    "budget": "not_checked",
    "opening_hours": "not_checked",
    "route_feasibility": "not_checked"
  }
}
```

- model_version — непустая строка, версия утверждённой реализации. Значение примера не обозначает существующую модель.
- items — массив до top_k элементов, без повторов; rank — целые 1…N. poi_id разрешается в исходном наборе.
- score — конечное число либо null для метода без численного score; смысл и диапазон фиксируются вместе с моделью.
- reasons — непустой массив. Коды: PRIMARY_INTEREST_MATCH, SECONDARY_INTEREST_MATCH, RATING_SIGNAL, POPULARITY_SIGNAL, FRESHNESS_SIGNAL, FALLBACK_SELECTION. Для совпадения интересов categories содержит реально совпавшие категории. Объяснение отражает фактически использованный фактор.
- excluded — массив объектов с poi_id и code; для известного недоступного кандидата code = UNAVAILABLE. Объекты вне top-K не считаются автоматически исключёнными.
- required_poi_ids сохраняет обязательные места независимо от top-K.
- warnings — массив объектов с непустым code и poi_id (строка либо null для общего предупреждения).
- constraint_checks: passed / failed / unknown / not_checked. В первом ранжировщике проверки бюджета, часов и выполнимости маршрута имеют not_checked.

Ошибка структуры возвращается без частичной выдачи:

```json
{
  "schema_version": "0.1",
  "error": {
    "code": "VALIDATION_ERROR",
    "details": [
      {"path": "preferences.primary_interests", "code": "EXPECTED_ONE_OR_TWO_CATEGORIES"}
    ]
  }
}
```

Неизвестный обязательный ID — ошибка запроса. Известное недоступное обязательное место — явный конфликт, не молчаливое удаление; перед реализацией согласовать точный код конфликта. Некорректные исходные записи Data отделяет до ранжирования, сохраняя причины в отчёте качества. Если некорректная запись попала в запрос ранжировщика, запрос отклоняется как невалидный.

## 6. Граница TripRoute

Будущий интерфейс: `build_route(preferences, pois, recommendation, travel_times) → TripRoute`. Routing получает весь проверенный набор, включая обязательные места за пределами top-K.

Пример структуры черновика — не готовый маршрут:

```json
{
  "schema_version": "0.1",
  "status": "draft",
  "days": [
    {
      "date": "2026-11-17",
      "visits": [
        {"poi_id": "synthetic:museum-01", "start_at": null, "end_at": null}
      ],
      "legs": []
    }
  ],
  "unplaced_required_poi_ids": [],
  "warnings": ["SCHEDULE_NOT_CALCULATED"],
  "constraint_checks": {
    "time": "not_checked",
    "budget": "not_checked",
    "opening_hours": "not_checked",
    "required_places": "passed"
  }
}
```

- status: draft / feasible / infeasible / unknown.
- days — массив дней с date, visits и legs; visits содержит ссылки POI и время ISO 8601 со смещением либо null в черновике.
- Для каждой соседней пары в рассчитанном маршруте нужен переход с from_poi_id, to_poi_id, duration_seconds, distance_meters, basis, source_id и geometry/navigation_url.
- Время перехода — секунды, расстояние — метры; неизвестное — null, известные значения неотрицательны.
- basis: routing_provider / test_matrix / geometric_estimate. Тестовая матрица и геометрическая оценка не подтверждают реальную пешую навигацию.
- unplaced_required_poi_ids — обязательные места, не включённые в маршрут; их отсутствие в маршруте не скрывается.
- warnings здесь — массив кодов строками; constraint_checks использует те же четыре статуса, что Recommendation.

Это граница следующего этапа, не окончательная спецификация Routing. Перед его реализацией отдельно согласовать travel_times, дневные окна, отдых, часы работы и точные типы геометрии/ссылок. Статус feasible нельзя выводить только из наличия списка мест.

## 7. Владельцы и предлагаемые пути

| Владелец | Пути и ответственность |
|---|---|
| Architect + команда | docs/ai/contracts-v0.1.md — общий контракт |
| Data | ai_ds/data.py, tests/test_data.py — загрузка, нормализация, качество |
| Data | data/fixtures/pois.synthetic.json; data/processed/ — реальные наборы отдельно |
| Recommendation | ai_ds/recommendation.py, tests/test_recommendation.py — утверждённое ранжирование |
| Recommendation | ai_ds/demo.py, examples/preferences/ — демонстрация |
| Routing, позже | ai_ds/routing.py, tests/test_routing.py — расписание и ограничения |
| Fullstack | HTTP-адаптер, UI, БД, Routing API; пути определяются по коду напарника |
| QA | tests/test_pipeline.py — проверка локальной цепочки |

Кроме этого документа, перечисленные пути предлагаются для создания и не подтверждают наличие реализации. Data не меняет ранжирование; Recommendation не меняет исходные данные; Fullstack не дублирует модель. Общий контракт меняется совместно.

## 8. Среда и целевой запуск

Согласованный вариант: Python 3.11+, стандартные json, argparse и unittest без сторонних зависимостей. Доступность рабочего Python необходимо проверить. pytest используется только при подтверждённой доступности или согласованной установке.

Целевые команды после реализации, сейчас не подтверждены запуском:

```powershell
python -m ai_ds.demo --pois data/fixtures/pois.synthetic.json --preferences examples/preferences/history.json
python -m unittest discover -s tests -v
```

Демонстрация выводит JSON в stdout; ошибки — в stderr с ненулевым кодом завершения. HTTP-сервер, БД и LLM для этого этапа не требуются.

## 9. Приёмка и оставшиеся зависимости

- Восемь категорий, 1–2 основных интереса и дополнительные валидируются.
- Некорректный ввод даёт воспроизводимые ошибки.
- Два контрольных профиля дают объяснимые результаты по отдельно утверждённой модели.
- Обязательные ID сохраняются для Routing независимо от top-K.
- Неизвестные цены, часы и перемещения не объявляются проверенными ограничениями.
- Демонстрация и тесты запускаются документированными командами.

Остаются: проверка среды; согласование с фактическим API напарника; выбор и проверка реальных источников; утверждение модели Recommendation; детализация Routing. Готовность контракта не означает готовность этих компонентов.
