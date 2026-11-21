# TEST_RESULTS — TripWise v9

База: `c78bb2ea9dabad1614a80ac5463913c4cac0a2ca`.
Результаты относятся к исходникам из архивов v8/TripWise_code и локальным
синтетическим фикстурам, а НЕ к полноценной браузерной поездке пользователя.

| Проверка | Статус | Факт |
|---|---|---|
| 10 целевых наборов Python по московскому агенту, каталогу и планировщику | **PASS** | 84/84 (`python -m unittest`) |
| 8 Node.js скриптов `tests/test_moscow_*.cjs` | **PASS** | 42/42 проверок |
| Компиляция затронутых Python-модулей и JS-интерфейса | **PASS** | Синтаксических ошибок не обнаружено |
| Буквальный запрос «уголок дедушки дурова» при OpenRouter HTTP 429 | **PASS (fixture)** | Создано предложение без обращения к API; подтверждение работает |
| Опечатки «уголак дедущки дурово» | **PASS (fixture)** | Правильный ID, не Театриум Терезы Дуровой |
| Запрос «добавь парк», включая preview/confirm | **PASS (fixture)** | Вставлен POI из реального тестового каталога, API не вызывался |
| Перенос, удаление, смена темпа и режима | **PASS (unit)** | Выданы контрактные операции для общего планировщика |
| Безопасный перенос необязательного POI на другой день | **PASS (unit)** | Ни обязательные места, ни приёмы пищи не удаляются |
| Составной запрос с нераспознанной частью | **PASS (unit)** | Не выполняется только первая половина без остальных условий |
| Более широкий набор `tests.test_route_assistant` | **NOT RUN (missing fixture)** | 24 теста не запускаются из-за отсутствия `examples/preferences/history.json`; это не сбои утверждений об оптимизации |
| OpenRouter real API, квота, свободная модель и сеть | **NOT RUN** | Не производились сетевые обращения; ошибка 429 провайдера не может быть устранена кодом приложения |
| Полная реальная Moscow V7, транспортный граф, сохранённая поездка со скриншота | **NOT RUN** | Большая база и состояние поездки не входят в присланный исходный ZIP |
| Полный браузерный E2E / Shanghai Pilot 5000 | **NOT RUN** | Нет необходимых наборов данных и браузерной инфраструктуры |

**Ограничение точности:** опечатки поддерживаются на уверенно похожих названиях;
при нескольких почти одинаковых POI агент просит уточнение, чтобы не поехать
в другое место. Замена обязательных посещений запрещена. Полная оптимальность
маршрутов и обработка произвольных инструкций без OpenRouter не гарантируются.

## История v8 (без изменений)

# TEST_RESULTS — TripWise Moscow Routing v8

Base Git commit: `c78bb2ea9dabad1614a80ac5463913c4cac0a2ca`. All quantified comparisons below are on **small deterministic fixture datasets** and are not claimed to represent the 19,510 Moscow POIs.

## Actual execution

| Suite / check | Status | Observed |
|---|---|---|
| Targeted Moscow Python test files, including v8 | **PASS** | `78 passed, 7 subtests passed` (`pytest -q`) |
| All Moscow `*.cjs` tests (`tests/test_moscow_*.cjs`) | **PASS** | **42/42** (`node --test`) |
| Syntax: Python modules (`py_compile`) and shared JS (`node --check`) | **PASS** | No syntax errors |
| Rich budget: prefer restaurant over Burger King | **PASS** | 30,000 RUB → restaurant at 1,600 RUB in the small one-day fixture |
| Tight budget: inexpensive meal remains available | **PASS** | 1,600 RUB → inexpensive canteen at 250 RUB |
| Do not equate price with quality | **PASS** | Better-rated 1,600 RUB restaurant preferred over nearby worse-rated 2,600 RUB restaurant |
| Preserve full-service dinner over 3 days | **PASS** | All 3 days have sit-down dinner with 30,000 RUB group budget in test fixture |
| Group and individual budgets equivalent | **PASS** | 30,000 RUB/person × 2 = 60,000 RUB/group in mocked Moscow shortlist |
| Existing late waiting gap regression | **PASS** | Test for 65-minute dinner wait continues to pass |
| Older Shanghai test set | **FAIL (pre-existing)** | Three failures in two files: `test_shanghai_engine.cjs`, `test_shanghai_v4_meals.cjs`; same tests fail on v7. No additional failures in v8 found in that set. |
| Full Moscow V7 / real metro graph / real route from screenshot | **NOT RUN** | Missing original large `data/` datasets and saved trip state from upload |
| Full browser E2E and Shanghai 5000 POI | **NOT RUN** | Data and live environment unavailable |

## Before / after comparison (identical synthetic 3-day fixture)

One traveler, full 09:00–21:00 days, RUB group budget 30,000, four museums per day, dinner choices include nearby Burger King-style fast food or full-service restaurants with estimated costs.

| Metric | v7 | v8 |
|---|---:|---:|
| Total expected spending | 20,700 RUB | **29,400 RUB** |
| Budget used | 69.0% | **98.0%** |
| Useful attraction visits | 12 | **12** |
| Scheduled meals | 9 | **9** |
| Full-service restaurant dinners | 0/3 | **3/3** |
| Fast-food dinners | 3/3 | **0/3** |
| Above requested budget | No | No |
| Time and duplicate checks | PASS | PASS |
| `no_gaps` check | FAIL | FAIL — not addressed by v8 |

This result demonstrates **possible** higher spending on suitable venues, not a universal 98% budget-utilization guarantee. A one-day fixture with only two attractions uses much less budget because the algorithm cannot invent worthwhile purchases.

## Suggested local recheck in the complete repo

```bash
python -m pytest -q tests/test_moscow_dining_quality_v8.py tests/test_moscow_budget_candidate_pool.py
node --test tests/test_moscow_*.cjs
```

Restart the backend and build a **new** route after copying the files; already saved itineraries are not automatically regenerated.

---

## Previous v7 results

# TripWise Moscow Routing v7 — фактические проверки

Базовый коммит: `c78bb2ea9dabad1614a80ac5463913c4cac0a2ca`. Данные: локальные тестовые фикстуры; **реальный каталог Moscow V7 не загружен**.

## Что запускалось

| Сценарий | Статус | Результат |
|---|---|---|
| Новый тест `test_moscow_breakfast_geo_v7.cjs` | **PASS** | **7/7**: завтрак первым в день с завтраком; 1,3,5 дней; отсутствие «позднего завтрака»; нормализация редактирования; отклонение неверной последовательности; короткая ходьба; дальнее такси |
| Московский Node.js набор `node --test tests/test_moscow_*.cjs` | **PASS** | **37/37** (включая 7 новых) |
| Python регрессии `pytest` (9 целевых файлов тестов) | **PASS** | **74 passed, 7 subtests passed** |
| Шанхай `test_shanghai_assistant.cjs`, `test_shanghai_geo_meals.cjs` | **PASS** | **2/2** файлов |
| Дополнительный старый шанхайский набор | **FAIL (существовало в v6)** | `test_shanghai_engine.cjs`, `test_shanghai_transit.cjs`, `test_shanghai_v4_meals.cjs` не проходят одинаково в v6 и v7; последний: ожидалось 30, получено 5 |
| `test_shanghai_assistant_regression.cjs` | **NOT RUN (нет данных)** | Необходимый `data/demo/shanghai/pois.json` отсутствует в исходном ZIP; попытка запуска завершилась `ENOENT`, как и в v6 |
| Полный Moscow V7 / настоящий дорожный граф / браузерный E2E | **NOT RUN** | В присланных исходниках нет больших баз и дополнительных транспортных данных; подключение к внешнему GitHub из локального контейнера для скачивания файлов недоступно |
| Качество маршрута со скриншота пользователя | **NOT RUN** | Нет сохранённого маршрута с исходными предпочтениями, API-снапшотом и полной базой |

## Сравнение до / после на одинаковом корректном маленьком примере

1 день, 2 обязательных музея, в обоих вариантах соблюдены временные окна и бюджет, все обязательные посещения размещены, выбран mixed `[walk, taxi]`. Матрица: пешком 13 минут, такси 10 минут с модельной стоимостью 500 ₽, нет иных приёмов пищи в коротком дневном окне.

| Показатель | v6 | v7 |
|---|---:|---:|
| Выбранный переход | Такси | Пешком |
| Время перехода | 10 мин | 13 мин |
| Стоимость транспорта | 500 ₽ | 0 ₽ |
| Обязательных посещений | 2 / 2 | 2 / 2 |
| Бюджет | PASS | PASS |
| Временные окна | PASS | PASS |

Это **синтетическая фикстура**, цифры не являются реальными московскими тарифами.

## Запуск после распаковки

```bash
node --test tests/test_moscow_*.cjs
python -m pytest -q tests/test_moscow_pace_durations_v6.py tests/test_moscow_budget_windows_v2.py tests/test_moscow_planning_estimates_v2.py tests/test_moscow_named_poi_v5.py tests/test_moscow_named_poi_v4.py tests/test_moscow_gap_repair.py tests/test_moscow_budget_candidate_pool.py tests/test_moscow_agent_intents_v3.py tests/test_moscow_agent_fill_integration_v3.py
```

Версии Python и Node.js должны удовлетворять требованиям исходного репозитория. Для полной итоговой проверки необходима Moscow V7, транспортные данные, переменные среды и запущенное веб-приложение.

---

## История предыдущих протоколов

# TripWise v6 — протокол проверок длительностей и темпа

Исходный Git HEAD: `c78bb2ea9dabad1614a80ac5463913c4cac0a2ca`. Проверки выполнены локально по исходникам и синтетическим фикстурам **без полной Moscow V7**.

| Проверка | Статус | Результат |
|---|---|---|
| Новый Python-набор `tests/test_moscow_pace_durations_v6.py` | **PASS** | 8 из 8: ТЦ/ГУМ, тег OSM, небольшой магазин, музеи, реальные данные, модель, пользовательская длительность, адаптер |
| Новый Node.js-набор `tests/test_moscow_pace_durations_v6.cjs` | **PASS** | 5 из 5: 3 темпа, торговый центр 120–180 минут, пользовательская длительность, сохранение фактов, редактирование дня агентом, совместимость с Шанхаем |
| Совместный выборочный Python-регрессионный набор | **PASS** | 89 тестов прошли; 35 вложенных unittest subtests |
| Совместный Node.js-регрессионный набор | **PASS** | 32/32, включая шанхайский общий движок и агента |
| Python-компиляция изменённых файлов | **PASS** | `python -m py_compile` |
| JS-синтаксис изменённого движка | **PASS** | `node --check` |
| Реальный пользовательский маршрут со скриншота | **NOT RUN** | нет сохранённой поездки и полной локальной базы |
| Полный браузерный E2E, 19 510 POI и реальная транспортная геометрия | **NOT RUN** | отсутствует `data/` и инфраструктура маршрутизатора |
| Полный шанхайский пилот на 5000 POI | **NOT RUN** | отсутствует база Шанхая |
| Известный тест Шанхая `test_shanghai_v4_meals.cjs` | **NOT RUN in v6** | в предыдущем протоколе отмечена прежняя ошибка, v6 не заявляет её исправление |

### Запуск самостоятельно в корне проекта

```bash
python -m pytest -q tests/test_moscow_pace_durations_v6.py tests/test_moscow_planning_estimates_v2.py tests/test_moscow_budget_windows_v2.py tests/test_moscow_gap_repair.py tests/test_moscow_agent_intents_v3.py tests/test_moscow_agent_fill_integration_v3.py tests/test_moscow_budget_candidate_pool.py tests/test_moscow_named_poi_v4.py tests/test_moscow_named_poi_v5.py tests/test_openrouter.py
node --test tests/test_moscow_pace_durations_v6.cjs tests/test_moscow_low_budget_daily.cjs tests/test_moscow_meal_geography.cjs tests/test_moscow_routing_integrated_v2.cjs tests/test_moscow_map_palette.cjs tests/test_moscow_tail_budget_v3.cjs tests/test_shanghai_assistant.cjs tests/test_shanghai_5000_optimizer.cjs
```

### Что проверяют новые тесты

1. Подземный музей даже с категорией `history` классифицируется как музей, а не короткое историческое посещение.
2. Музеи, торговые центры и отдельные магазины получают разные синтетические оценки.
3. `relaxed` больше времени на музей, чем `balanced`; `balanced` больше, чем `intensive`.
4. Для торгового центра — 120–180 минут при каждом темпе, если оценка синтетическая.
5. Источник с реальной длительностью не переписывается оценкой категории.
6. Пользовательская длительность обязательного места сохраняется точно.
7. Редактирование темпа существующего дня пересчитывает время тем же JS-планировщиком.
8. Старые шанхайские POI без новых московских метаданных используют прежний расчёт.

---

# История предыдущих проверок (v5 и ранее)

# TripWise v5 — фактические результаты

Базовый коммит: `c78bb2ea9dabad1614a80ac5463913c4cac0a2ca`. Среда: исходники из `TripWise_code.zip` + накопительный v4 + описанные v5 изменения, без больших баз данных.

| Проверка | Результат |
|---|---|
| Прямое чтение реального CSV V7 через GitHub и поиск Дурова | **PASS**: запись `moscow-cultural-0`, имя «Уголок дедушки Дурова», координаты `55.77933,37.62121`, категория `entertainment`. Git blob `9542f7198bb8f41000bc0bd9e17c1bfe7dccbd35` |
| `pytest -q tests/test_moscow_named_poi_v5.py tests/test_moscow_named_poi_v4.py tests/test_moscow_agent_intents_v3.py tests/test_moscow_agent_fill_integration_v3.py tests/test_moscow_budget_candidate_pool.py tests/test_moscow_planning_estimates_v2.py tests/test_moscow_budget_windows_v2.py tests/test_moscow_gap_repair.py tests/test_openrouter.py` | **PASS: 81 passed, 35 subtests passed** |
| `node --test tests/test_moscow_low_budget_daily.cjs tests/test_moscow_meal_geography.cjs tests/test_moscow_routing_integrated_v2.cjs tests/test_moscow_map_palette.cjs tests/test_moscow_tail_budget_v3.cjs tests/test_shanghai_assistant.cjs tests/test_shanghai_5000_optimizer.cjs` | **PASS: 27/27** |
| Синтаксис изменённых Python-файлов и frontend JS | **PASS**: `python -m py_compile`, `node --check` |
| Точная фраза со скриншота: «дебил, я хочу в уголок дедушки дурова» | **PASS (unit)**: решение `add_named_poi`, ID `moscow-cultural-0`, без OpenRouter |
| Тот же запрос при `OpenRouterError('LLM_RATE_LIMITED')`: preview → confirm | **PASS (integration fixture)**: ID обнаружен в подтверждённом расписании, вызовов OpenRouter 0 |
| Защита от ложных совпадений с другими местами семьи Дуровых, запрет «не хочу», совместимость v4 | **PASS (unit)** |
| Маркер запущенной версии в API статуса | **PASS (unit)**: `agent_version=moscow-named-v5` |
| Реальная сохранённая поездка пользователя, 19 510 POI, цены и действительное время посещения | **NOT RUN**: нет полной V7 JSON, транспортного графа и конкретного ID поездки/БД |
| End-to-end в браузере на настоящих OSM-переходах | **NOT RUN**: нет полной среды и данных |
| Регрессия полного шанхайского пилота | **NOT RUN**: нет Shanghai Pilot v2 в архиве; отдельный существующий дефект прошлого шанхайского теста не устранялся |

**Ограничение:** PASS интеграционного теста подтверждает работу цепочки правки, но **не обещает**, что театру найдётся допустимое место в фактической поездке. Если физически невозможно вставить конкретное место, движок должен отклонить вставку с причиной, а не выдавать ложное «нет в каталоге».

---

## Предыдущие проверки v4 (для истории)

# TripWise v4 — результаты фактических проверок

**Базовый коммит:** `c78bb2ea9dabad1614a80ac5463913c4cac0a2ca`. Все проверки ниже проведены на архиве исходного кода без `data/demo/moscow/v7/` и без живого API.

| Контроль | Статус | Результат |
|---|---|---|
| Python: Moscow named POI + предыдущие целевые тесты + HTTP/OpenRouter mock | **PASS** | **70 passed**, 28 subtests; `pytest -q tests/test_moscow_named_poi_v4.py tests/test_moscow_agent_intents_v3.py tests/test_moscow_agent_fill_integration_v3.py tests/test_moscow_budget_candidate_pool.py tests/test_moscow_planning_estimates_v2.py tests/test_moscow_budget_windows_v2.py tests/test_moscow_gap_repair.py tests/test_openrouter.py` |
| Node.js: Moscow + Shanghai shared core targeted | **PASS** | **27/27 Node test files/tests passed**, включают 23 проверки внутри Shanghai Assistant; `node --test tests/test_moscow_low_budget_daily.cjs tests/test_moscow_meal_geography.cjs tests/test_moscow_routing_integrated_v2.cjs tests/test_moscow_map_palette.cjs tests/test_moscow_tail_budget_v3.cjs tests/test_shanghai_assistant.cjs tests/test_shanghai_5000_optimizer.cjs` |
| Синтаксис Python изменённых файлов и JS-движка | **PASS** | `python -m py_compile ...`; `node --check ai_ds/web_demo/shanghai-engine.js` |
| Добавление точного POI через `MoscowAssistant._request`, preview и confirm без вызова OpenRouter | **PASS (fixture)** | Новый тест специально настроил интерпретатор на `LLM_RATE_LIMITED`, и он **не вызван**; именованный POI присутствует в подтверждённом результате |
| «22, замени автоматически» + предыдущая просьба добавить названный объект | **PASS (unit)** | Генерируется операция на 22 ноября с разрешением замены одного необязательного места; сама замена проходит только после валидации |
| Запрет замены обязательных POI/приёмов пищи | **PASS (unit)** | Без явного разрешения вставка при отсутствии места отклоняется. При разрешённой замене protected остаются на месте |
| Вопрос «Почему не добавился Уголок дедушки Дурова?» | **PASS (unit)** | Локально проверяет каталог/сохранённые точки, не делает фальшивого утверждения об успешной правке |
| Локальные команды при отсутствующем API-ключе | **PASS (unit)** | Москва сообщает `available=true`, `mode=local`; сложные запросы по-прежнему требуют OpenRouter |
| Список POI Moscow V7 (реальные 19 510), цены/граф и конкретная поездка со скриншота | **NOT RUN** | Каталог, геометрия и пользовательские данные недоступны в `TripWise_code.zip`; реальное наличие театра по ID **не подтверждено** |
| Живой OpenRouter и настоящая причина 429 по аккаунту | **NOT RUN** | Нет доступа к API-ключу/квотам. Код интерпретирует HTTP 429 как лимит, но не знает, исчерпана квота дня или минуты |
| Общие `test_route_assistant.py` + CLI | **BLOCKED, 29 tests** | В присланном исходном ZIP отсутствуют `examples/preferences/history.json` и `scripts/demo_route_assistant.py`. Их исполнение даёт FileNotFoundError, не кодовую регрессию. |
| Полный браузерный E2E для Москвы и Шанхая | **NOT RUN** | Отсутствуют большие базы и настроенное браузерное окружение |
| Один прежний тест `test_shanghai_v4_meals.cjs` | **KNOWN FAIL** | Уже не проходил на v3 и предыдущем движке, описан ниже. В v4 не исправлялся |

## Проверка утверждений «до/после»

До v4 сервер целиком делегировал именованные просьбы OpenRouter, поэтому при HTTP 429 запрос без предварительного просмотра не мог быть обработан. Кроме того, `shortlist(...)` мог не включить конкретный ID, если совпадение имени имело низкий рейтинг. После v4 в фикстуре определённый ID выбирается независимо от OpenRouter, даже если его нет в исходных посещениях. Было: **0 подтверждённых named-POI добавлений в тесте при LLM_RATE_LIMITED**; стало: **1 добавление** с подтверждением. Данные теста синтетические и не подтверждают наличие этого ID в московском V7.

Выполнение списка проверок с тестами CLI/старых примеров отдельно показало **29 ошибок из-за отсутствующих файлов**, оставленные статусом BLOCKED, а не скрытые как PASS.

---

## Предыдущий отчёт v3

# TripWise v3 — реальные результаты тестов и ограничения

**Git HEAD исходной ветки:** `c78bb2ea9dabad1614a80ac5463913c4cac0a2ca`.
Пакет создан из присланного `TripWise_code.zip`, поверх предыдущего накопительного архива TripWise. Большие `data/` и сторонние зависимости не входят.

## Проверено на этом наборе

| Набор | Итог | Команда / условия |
|---|---|---|
| Москва, бюджет + окна + географический поиск + агент | **PASS — 36/36 Python** | `PYTHONPATH=. python -m pytest -q tests/test_moscow_budget_candidate_pool.py tests/test_moscow_planning_estimates_v2.py tests/test_moscow_budget_windows_v2.py tests/test_moscow_gap_repair.py tests/test_moscow_agent_intents_v3.py tests/test_moscow_agent_fill_integration_v3.py` |
| Москва, JS-движок, бюджет, питание, география, карта | **PASS — 25/25 Node.js** | `node --test tests/test_moscow_low_budget_daily.cjs tests/test_moscow_meal_geography.cjs tests/test_moscow_routing_integrated_v2.cjs tests/test_moscow_map_palette.cjs tests/test_moscow_tail_budget_v3.cjs` |
| Шанхай, общие планирование и агент, синтетические фикстуры | **PASS — 39/39 внутренних проверок** | `node --test tests/test_shanghai_assistant.cjs tests/test_shanghai_5000_optimizer.cjs` (оба Node.js файла успешны) |
| Изменённые Python-модули и JS-движок | **PASS** | `python -m py_compile ...`, `node --check ...` |
| Полный шанхайский тест `test_shanghai_engine.cjs` | **NOT RUN / BLOCKED** | Не хватает `data/demo/shanghai/pois.json` в исходном ZIP (`ENOENT`) |
| `test_shanghai_v4_meals.cjs` | **FAIL — 1/9** | Существующая ошибка ожидания 30 минут против фактически 5; воспроизведена и с двигателем из предыдущего ZIP. **Не исправлена здесь** |
| Python-сценарии `test_route_assistant.py`, `test_route_assistant_cli.py` | **NOT RUN / BLOCKED** | 29 тестов не могут корректно исполниться без `examples/preferences/history.json`, `scripts/demo_route_assistant.py`; отсутствуют в `TripWise_code.zip` |
| Полный локальный backend+browser на Moscow V7 (~19 510 POI), настоящие пути OSM и реальный OpenRouter | **NOT RUN** | Не было каталогов `data/`, внешнего браузерного окружения и живых подтверждённых тарифов |

## Сравнение ДО/ПОСЛЕ на идентичном синтетическом сценарии из 4 дней

Срок 21–24 ноября 2026, каждый день 09:00–20:00; бюджет 9400 RUB на компанию; несколько дорогих ужинов по 2700 RUB, достаточно бесплатных посещений и бюджетных кафе рядом. Код «ДО» — JS из непосредственно предыдущего архива; код «ПОСЛЕ» — текущий v3. Это **не** реальная поездка пользователя со скриншота.

| Показатель | ДО | ПОСЛЕ |
|---|---:|---:|
| Полезных посещений по дням | 2 / 2 / 2 / 2 | **7 / 7 / 7 / 7** |
| Всего полезных посещений | 8 | **28** |
| Время последнего события: 1 день | 18:50 | 18:25 |
| 2 день | 18:50 | **19:19** |
| 3 день | 10:27 | **19:24** |
| 4 день | 15:47 | **18:18** |
| Питание по дням | 3 / 3 / 0 / 0 | **2 / 2 / 2 / 2** |
| Учтённая стоимость за поездку | 9200 RUB | **7400 RUB** |
| Остаток от 9400 RUB | 200 RUB | **2000 RUB** |
| Соблюдение бюджета | PASS | PASS |
| Минимум одно посещение на день | 4/4 | 4/4 |
| Наличие всех требуемых приёмов пищи | FAIL | **FAIL (честно)** |
| Проверка полного устранения пустых окон | FAIL | **FAIL (остаётся неиспользованное время)** |

Обе версии проходят ограничения на бюджет, время и обязательные места **в этом тестовом наборе**. По количеству посещений и равномерности питания новая версия лучше, но **не все дни заняты до 20:00 и не все приёмы пищи выполнимы по бюджету**. Планировщик не выдумывает бесплатные ужины.

## Статусы 22 сценариев из исходного технического задания

`PASS (fixture)` означает прохождение воспроизводимого локального сценария, а **не** проверку на 19 510 московских местах. `NOT RUN` — ещё нельзя подтвердить. `FAIL` — обнаружен незакрытый недостаток.

| № | Сценарий | Статус |
|---:|---|---|
| 1 | 3 дня: прибытие 14:10, полный второй, отъезд 19:00 | PASS (fixture) |
| 2 | Малый бюджет, преимущественно бесплатные POI и дешёвая еда | PASS (fixture) |
| 3 | Замена дорогого питания более дешёвым | PASS (fixture) |
| 4 | Короткое расстояние пешком, не такси | PASS (fixture) |
| 5 | Режим ОТ, короткие пешие подходы | PASS (fixture) |
| 6 | Режим такси, короткие пешие подходы | PASS (fixture) |
| 7 | Режим только пешком | PASS (fixture) |
| 8 | Mixed, режим на каждом участке | PASS (fixture) |
| 9 | Ужин в подходящем заведении, не обычной кофейне | PASS (fixture) |
| 10 | Питание не создаёт дорогого географического отклонения | PASS (fixture) |
| 11 | Заполнение длительного окна существующими кандидатами | PASS (fixture) |
| 12 | Переходы между берегами Москвы-реки | PASS (fixture) |
| 13 | Отсутствие ненужных возвратов между районами для реального города | NOT RUN (real V7) |
| 14 | Учёт билетов, еды, такси и ОТ | PASS (fixture) |
| 15 | Неизвестная цена не считается нулевой | PASS (fixture) |
| 16 | Первый и последний дни ограничены | PASS (fixture) |
| 17 | Промежуточный день сохраняет собственное окно | PASS (fixture) |
| 18 | Жёлтые маркеры еды, транспортные цвета | PASS (unit) |
| 19 | Порядок остановок совпадает между расписанием и реальной картой | NOT RUN (browser) |
| 20 | 5 дней, без необоснованных окон при доступных реальных альтернативах | NOT RUN (real V7) |
| 21 | ИИ-агент пересчитывает изменения маршрута | PASS (synthetic integration; реальный OpenRouter NOT RUN) |
| 22 | Шанхайские сценарии продолжают работать | **PARTIAL**: 39 fixture PASS, один из прежних v4-тестов FAIL; полный E2E NOT RUN |

## Тесты, специфичные для v3

- **PASS:** запрос пользователя «в 2, 3, 4 день длинные перерывы, любые места и категории» создаёт одну атомарную операцию для 3 дат без OpenRouter.
- **PASS:** грамматика не перехватывает комплексный запрос или требование использовать только одну категорию.
- **PASS:** на `LLM_INCOMPLETE_RESPONSE` делается ровно один безопасный повтор.
- **PASS:** `MoscowGapIndex` реально расширяет категории по явному запросу «любые».
- **PASS:** агент на Node JS с ограниченным синтетическим каталогом строит preview и позволяет подтвердить, не меняя исходную версию до нажатия «Применить».
- **PASS:** 4 дня, выбор доступных посещений во всех днях, вечером по нескольку реальных по данным фикстуры остановок, без перерасхода.
- **NOT RUN:** скриншоты пользователя не были воспроизведены на реальной сохранённой поездке.

## Как запустить дополнительные проверки после распаковки в оригинальный репозиторий

```bash
python -m pytest -q tests/test_moscow_agent_intents_v3.py tests/test_moscow_agent_fill_integration_v3.py tests/test_moscow_gap_repair.py
node --test tests/test_moscow_tail_budget_v3.cjs tests/test_shanghai_assistant.cjs tests/test_shanghai_5000_optimizer.cjs
```

Базовые цены ресторанов/музеев и расписания реальных провайдеров должны проходить отдельную проверку источников. Проверка на тестовых ценах не подтверждает рыночные тарифы.


