# Пакет подключения AI/DS и решений для полного сценария

17 ноября 2026. Architect Agent по заданию Analyst. Это дополнение к [существующей спецификации backend](backend-integration.md), а не новая реализованная архитектура. Производственный код, утверждённые контракты 0.1 и файлы Fullstack не менялись. Предложения ниже требуют решения команды до реализации.

## Что можно подключить сейчас

В локальной копии доступны Python-функции и CLI; HTTP-сервер, БД и UI в этой проверке не найдены и не тестировались. Точные сигнатуры сверены с кодом:

```python
load_pois(path)
process_pois(records)  # -> (accepted, report)
recommend(preferences, pois, *, evaluated_at=None)
build_route(preferences, pois, recommendation, travel_times, routing_context)
```

Работают на Python 3.11+ со стандартной библиотекой и доступной базой zoneinfo. Текущий компьютер использует `C:/msys64/ucrt64/bin/python.exe`, deployment должен выбрать свой путь. `load_pois` читает любой JSON, не гарантирует массив и не заменяет доменную валидацию. `recommend` повторно валидирует профиль/POI; Routing получает полный каталог и отдельные required ID, включая отсутствующие в top-K.

Готовые реальные каталоги: Москва `data/geoapify/ru/20261117T173836.768006Z/pois.validated.json`, СПб `data/geoapify/ru/20261117T173907.144035Z/pois.validated.json`, Токио `data/geoapify/jp/20261117T173938.648464Z/pois.validated.json`. Всего 2058 POI. [Data-аудит](data-readiness-2026-11-17.md) подтвердил структуру, но все rating/popularity/duration/cost/hours неизвестны. [Оценка рекомендаций](recommendation-readiness-2026-11-17.md) — 30 сценариев/204 проверки, не пользовательская релевантность.

Для выбора набора backend хранит серверный allowlist «страна + нормализованный город → одобренный snapshot». Пользователь не задаёт путь, имя модуля, команду или файл scenario. Версия snapshot должна сохраняться с поездкой/предложением правки, чтобы повторное открытие не подменяло данные новой загрузкой. Это правило интеграции предлагается утвердить; в текущем backend оно не реализовано.

Минимальный импорт для локальной интеграционной проверки с существующими синтетическими входами:

```python
from ai_ds.data import load_pois, process_pois
from ai_ds.recommendation import recommend, RecommendationError
from ai_ds.routing import build_route, RoutingError

pois, quality = process_pois(load_pois('data/processed/pois.synthetic.validated.json'))
preferences = load_pois('examples/preferences/history.json')
scenario = load_pois('examples/routing/day.synthetic.json')
if quality['summary']['rejected_count']:
    raise ValueError('POI snapshot contains rejected records')
try:
    recommendation = recommend(preferences, pois)
    route = build_route(preferences, pois, recommendation,
                        scenario['travel_times'], scenario['routing_context'])
except (RecommendationError, RoutingError) as error:
    domain_error = error.response()  # backend отображает/журналирует без секретов
    raise
```

Это синтетический smoke-test, не расписание реального города. Для реальных рекомендаций заменить путь POI и профиль на `examples/preferences/real/moscow.museums.json` и остановиться после `recommend`: прикладывать синтетическую матрицу к Geoapify ID нельзя.

CLI для backend другого языка, без введения отдельного сервиса:

```powershell
& 'C:/msys64/ucrt64/bin/python.exe' -B -m ai_ds.demo --pois data/geoapify/ru/20261117T173836.768006Z/pois.validated.json --preferences examples/preferences/real/moscow.museums.json
& 'C:/msys64/ucrt64/bin/python.exe' -B -m ai_ds.routing --pois data/processed/pois.synthetic.validated.json --preferences examples/preferences/history.json --scenario examples/routing/day.synthetic.json
```

Backend запускает subprocess с массивом аргументов, `shell=False`, серверным интерпретатором/working directory, timeout и лимитом входов/выходов, читает UTF-8 stdout/stderr. Конкретные timeout/размеры устанавливаются после измерения на deployment; значение не объявляется согласованным. Профиль создаётся в серверном временном каталоге, удаляется владельцем процесса; произвольные клиентские пути не передаются.

| Случай | Фактический локальный результат | Что должен различать адаптер |
|---|---|---|
| Recommendation success | exit 0, JSON stdout: schema_version 0.1, model_version lexicographic-v0.1, items/reasons/required/warnings/checks | rank не маршрут; `score=null` допустим |
| Routing result | exit 0, TripRoute stdout, в том числе unknown/infeasible | process success не означает feasible; проверять constraint_checks |
| Невалидный профиль/POI | exit 1, JSON stderr VALIDATION_ERROR | Ошибка пользователя/доменных входов, без частичной выдачи |
| Невалидный routing context/matrix | exit 1, JSON stderr ROUTING_VALIDATION_ERROR | path/code указывает конкретный конфликт |
| Ошибка чтения/JSON | exit 2, JSON stderr INPUT_ERROR | Серверный snapshot/file failure отдельно от пользовательского профиля |
| Неверные CLI-аргументы | exit 2, текст argparse | Нельзя всегда json.loads(stderr) без fallback |

HTTP-статусы и endpoint names не существуют в этой AI/DS-копии. Предлагается mapper: доменная валидация → 422, устаревшая версия подтверждения → 409, timeout внешнего сервиса → 504, внутренняя ошибка выбранного сервером snapshot → 500; корректный TripRoute unknown/infeasible → нормальный структурированный ответ с этим статусом. Это проект HTTP-политики для Fullstack, не изменение текущего CLI.

## Рекомендуемый контракт следующего этапа — проект 0.2

Не менять семантику 0.1 на месте: сохранить его однодневный код/CLI и ввести явную opt-in версию 0.2 после утверждения. Recommendation `lexicographic-v0.1` можно вызывать через внутренний адаптер, преобразующий вход к 0.1; при этом нельзя переписать `schema_version` неизвестного payload без валидации. Перед реализацией точную многодневную модель сверить с [предложением Routing](routing-completion-proposal-2026-11-17.md).

Предложенный Routing способ — сначала вставлять обязательные места с проверкой позиций по всем дням, затем необязательные в порядке Recommendation.rank; повторные посещения между днями запрещены. Темп задаёт долю доступного дневного окна для активного времени и отдых, а не фиксированное число POI. Точные коэффициенты и tie-break фиксируются в proposal Routing до утверждения. Однодневный точный перебор до восьми кандидатов сохраняется как oracle малых тестов. Ограничение baseline «8 кандидатов/один день» не переносится автоматически на новую модель; примеры 1–3 дней не являются ограничением полного требования. Неуспех эвристики даёт unknown, а infeasible допускается только при доказанном конфликте. Новую модель нельзя объявлять точным глобальным оптимумом.

| Объект | Минимальный проект изменения | Правило / причина |
|---|---|---|
| Trip request | schema_version 0.2, preferences с текущими полями, poi_snapshot_id | Нужна явная версия входа и воспроизводимость |
| Routing context | `days: [{date, day_start, day_end}]`, endpoint_policy first_last_poi, pace_policy_version | Один день на каждую включённую дату; окно/таймзона проверяются; поддерживаемый диапазон и лимиты согласуются с Routing |
| Pace policy | Версия политики возвращается вместе с вычисленными max_active_seconds/rest_after_visit_seconds каждого дня | Все коэффициенты и параметры из утверждённой таблицы Routing; отличие темпа не только в числе POI |
| POI duration | Сохранить visit_duration_minutes и visit_duration_basis; при экспертном значении добавить проверяемую ссылку на отдельную версию policy/запись происхождения | Никаких молчаливых defaults по категориям; текущие null не изменяются |
| Field provenance | `field_provenance` для обогащённых rating/popularity/duration/cost/hours с source_id, source_record_id/source_url, retrieved_at, verified_at, basis/policy_version при оценке | Текущий один-source provenance сохраняет первичный источник объекта; новый источник поля не маскируется под Geoapify |
| Opening hours | До отдельного утверждения схемы оставить null и unknown; будущий отдельный объект должен задавать локальные интервалы и исключения дат с timezone/provenance | Строка OSM не объявляется проверенным расписанием. Необходимо отдельное согласование алгоритма интерпретации |
| Budget context | scope trip_food_and_entertainment, известные cost посещений плюс явно заданные расходы питания; одна валюта, отдельные known_total/unknown_items/check_status | Нельзя объявить общий бюджет соблюдённым по одним билетам; курсы валют не добавлять. Different currency → unknown/ошибка согласованной политики, не сложение |
| Travel matrix | Существующие mode/basis/source_id; добавить retrieved_at и по pair nullable geometry/navigation_url; known/unknown/unreachable не менять | Направленность сохраняется; geometry — GeoJSON LineString либо null, navigation_url — проверенный абсолютный HTTPS URL либо null; точный формат утвердить |
| TripRoute legs | Сохранять время/метры/basis/source и передавать geometry/navigation_url/retrieved_at из исходной pair | Устранить текущую потерю геометрии: сейчас Routing всегда отдаёт null |
| TripRoute | model_version новой модели, policy versions, poi_snapshot_id, checks и unplaced_required_poi_ids; дни/посещения/breaks/legs | Unknown-поля и непоставленные required видны UI; duplicates между днями запрещены по умолчанию |

`feasible` для полного туристического сценария допустим только если все обязательные проверки пройдены по достаточным данным. Расписание с экспертными длительностями должно явно маркироваться как приблизительное; реальные непроверенные часы сохраняют общий unknown. В 0.1 budget всегда not_checked. Новая версия должна различать известный конфликт (failed) и неизвестность; при известных расходах уже выше лимита можно доказать failed даже при оставшихся null, но нельзя доказать passed по нижней границе.

Матрица должна совпадать с transport. Walking — основной доказуемый режим. Car/public_transit поддерживаются только с данными соответствующего режима, без смены walking на car по формальному полю и без вымышленных расписаний ОТ. Геометрическое расстояние не выдаётся за дорожное. Для F08 нужны ≥3 POI и по каждой соседней паре известные секунды, метры, реальный source и geometry либо navigation_url, плюс фактическое отображение Fullstack.

## Route Assistant — проект протокола без нового HTTP endpoint

LLM только интерпретирует текст в разрешённую операцию. Выбор/пересчёт/валидация выполняются кодом; доступ к БД и произвольному коду LLM не даётся. Согласованный между двумя предложениями минимальный проект: replace_poi, remove_poi, add_poi, set_day_pace, set_meal_break. Точные поля операций — в [Routing proposal](routing-completion-proposal-2026-11-17.md). Для remove/replace обязательного места — явный конфликт, не скрытая отмена required. Неясные запросы возвращают needs_clarification. Новый ID обязан разрешаться в серверном каталоге. Согласованность документов не означает утверждение этого проекта владельцем.

Вход AI/DS propose: `route_id`, `base_version`, `poi_snapshot_id`, `text`; исходный маршрут/профиль читает backend из серверного хранилища и передаёт внутренними аргументами. LLM получает только нужные данные и доступные ID, а не секреты или произвольный raw. Structured response проходит строгую схему и allowlist, затем функции строят candidate_route и checks. Выход: `proposal_id`, `route_id`, `base_version`, `operations`, `diff`, `candidate_route`, `checks`, `warnings`, `validation_status`, `explanation`, `expires_at`. `validation_status`: ready_for_confirmation / needs_clarification / rejected; это состояние предложения, отдельно от candidate_route.status. Поля protocol — предложение, не текущая реализация.

Подтверждение: backend получает proposal_id/base_version, сверяет владельца, версию исходной поездки и snapshot, повторно проверяет зависимые актуальные данные, атомарно сохраняет новую версию. Если поездка изменилась — stale_version, без перезаписи. Повторное подтверждение той же proposal должно быть идемпотентным. Reject/timeout/ошибка LLM/невалидный JSON/injection/unknown ID/невыполнимая правка оставляют исходную поездку неизменной. Для candidate unknown UI показывает неопределённость; разрешать его подтверждение или только draft — отдельное продуктовое решение, рекомендуется разрешать только явно маркированный draft, без feasible.

Время жизни proposal, хранение payload LLM и передача пользовательского текста провайдеру согласуются с доступом/политикой сервиса; неизвестные условия не подменяются defaults. Наличие Codex-агентов не является LLM-интеграцией продукта.

## Передача владельцам и критерии интеграции

| Владелец | Получает / делает | Передаёт следующему |
|---|---|---|
| Data | Версия каталога, quality, attribution; после утверждения — происхождение дополненных полей | Recommendation/Routing: валидный каталог и честные ограничения |
| Recommendation | Существующий профиль/каталог → items + required + warnings | Routing: снимок, полный каталог, model_version |
| Fullstack | Реальная матрица/геометрия и backend-адаптер, карта, хранение | Routing: directed known/unknown/unreachable; QA: работающий URL/окружение |
| Routing | Дни/темп/время/required/бюджет/unknown; propose Assistant | Fullstack: TripRoute и proposal/diff/checks для UI |
| Fullstack | Preview/confirm/version/сохранение, UI warns и атрибуция | QA: полный повторяемый сценарий |
| QA | Реальные 3+ POI, каждая пешая pair, 2 профиля, конфликт required, unknown, approve/reject/stale | Команда: протокол и реальные дефекты без объявления mock интеграцией |

Первый интеграционный срез — вызов реальных рекомендаций через подтверждённый транспорт, UTF-8, предупреждения и отображение известных полей. Второй — расписание с достоверными/явно оценочными длительностями и настоящими directed legs, карта и сохранение. Третий — настоящий разрешённый LLM propose/confirm. Полный F05/F08/F10 не закрывается первым срезом.

## Один пакет решений и отдельные внешние вопросы

Рекомендуется одним решением утвердить: совместимый opt-in контракт 0.2 с сохранением baseline 0.1; модель дней/темпа из proposal Routing; запрет молчаливых длительностей с явно версионированными экспертными оценками только после отдельного выбора; происхождение каждого дополненного поля; single-currency бюджет без вымышленных цен; unknown для непрочитанных часов; перенос real leg geometry/link; Assistant propose→validate→preview→version-checked confirm без прямого изменения БД. Это разрешит реализацию конкретных локальных модулей после проверки согласованности документов. Не считается утверждённым этим файлом.

Внешние факты, которые одно согласование алгоритма не заменяет:

1. Fullstack: где текущий код/ветка, стек/версия runtime, deployment, текущий payload анкеты/БД и владелец адаптера? Python → рекомендуется import; другой язык → существующий CLI; отдельный HTTP-сервис пока не вводить.
2. Кто предоставляет реальный Routing API, какой mode/лимит/доступ/права кэша/источник и формат geometry/navigation_url? Поставщик и ключ в этой задаче не выбраны.
3. Какой LLM API доступен, какой бюджет/квота, допустимая передача/хранение данных и серверное хранение ключа? Нужен настоящий проверенный разрешённый вызов, не чат и не mock.
4. Какие допустимые реальные источники рейтинга/популярности/часов/цен/длительностей доступны и каковы права хранения/показа? Подключение/обогащение этим документом не выполнены.

## Выполненная проверка

17 ноября Architect запустил локальный import-пример и три subprocess-команды на установленном Python 3.14.5, без сети и новых зависимостей. Импорт: 5 рекомендаций, синтетический маршрут 09:00–11:35, status unknown, time/required passed, budget not_checked, hours unknown. Реальный московский Recommendation CLI: exit 0, 5 items. Synthetic Routing CLI: exit 0, unknown. Recommendation CLI с отсутствующим POI-файлом: exit 2, UTF-8 JSON INPUT_ERROR. Невалидные primary_interests при прямом вызове: VALIDATION_ERROR, path preferences.primary_interests, code EXPECTED_ONE_OR_TWO_CATEGORIES.

Полная регрессия этим агентом не повторялась; независимый QA выполнит итоговую проверку новой волны. HTTP/API/карта/БД/настоящий маршрут/LLM не тестировались. Готовность: локальная передача baseline — с ограничениями; полный интеграционный сценарий — недостаточно данных и незавершённые зависимости. Статусы TASKS.md не изменены.
