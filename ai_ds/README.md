# AI/DS TripWise — запуск и передача backend

Актуально на 18 ноября 2026. Это локальная библиотека и CLI, без HTTP-сервера/БД/UI. Нужны Python3.11+ и доступная IANA-база часовых поясов. В проверенной среде используется Python3.14.5 из C:/msys64/ucrt64/bin/python.exe. Новых Python-зависимостей нет.

## Локальная демонстрация без API

Из корня репозитория или распакованного AI/DS-пакета:

```powershell
python -B -m ai_ds.multiday --pois examples/routing/v02/pois.synthetic.json --preferences examples/routing/v02/preferences.synthetic.json --scenario examples/routing/v02/scenario.synthetic.json
python -B -m ai_ds.multiday --pois examples/routing/v02/moscow.real.pois.json --preferences examples/routing/v02/moscow.real.preferences.json --scenario examples/routing/v02/moscow.real.scenario.json
python -B scripts/demo_route_assistant.py
python -B scripts/demo_route_assistant.py --decision reject
python -B scripts/demo_route_assistant.py --decision confirm --allow-draft
python -B -W error::ResourceWarning -m unittest discover -s tests -v
```

Если системный python — нерабочий WindowsApps alias, замените его на рабочий интерпретатор. Первый маршрут полностью синтетический. Второй использует реальные POI/Geoapify-путь, но длительности — отдельно утверждённые оценки минимум час; часы/стоимость неизвестны, общий статус unknown. Это ориентировочный draft, не проверенная фактическая поездка.

Assistant CLI по умолчанию использует **явно обозначенный synthetic fixture интерпретатора**, не LLM. Состояние только в памяти процесса, default preview ничего не применяет. Confirm для unknown требует --allow-draft; reject сохраняетверсию1. Флаг --live делает один настоящий запрос согласованного бесплатного OpenRouter, читая только OPENROUTER_API_KEY окружения. Не используйте --live для обычной регрессии; ключи не включены в пакет и не передаются в браузер.

## Python-интеграция

```python
from ai_ds.data import load_pois
from ai_ds.recommendation import recommend
from ai_ds.multiday import build_route, validate_route

pois = load_pois('server-approved-pois.json')
preferences = load_pois('server-owned-preferences.json')
recommendation = recommend(preferences, pois)
route = build_route(preferences, pois, recommendation, travel_times, routing_context)
assert validate_route(route, preferences, pois, recommendation,
                      travel_times, routing_context)['valid']
```

Имена файлов в примере — серверные настройки, не клиентские пути. Для backend на другом языке запускайте CLI массивом аргументов с shell=False, UTF-8, рабочейдиректорией пакета и явным timeout. Exit0 означает структурированный ответ, включая unknown/infeasible; exit1 — доменная валидация; exit2 — INPUT_ERROR/argparse. Проверяйте содержимое, не только код процесса.

preferences/POI/Recommendation сохраняют0.1. Новый routing_context требуетschema0.2 и все дни диапазона. Полная схема: docs/ai/routing-contract-v0.2.md. Операции и авторизация Assistant: docs/ai/route-assistant-implementation-2026-11-18.md. Server-owned snapshot иcontext/store poi_snapshot_id должны совпадать. InMemoryRouteStore — reference, production backend реализует собственное атомарное хранение/версии/авторизацию.

## Что должен отобразить frontend

- Посещения/дни/legs/breaks и basis оценок, все warnings/checks, unplaced required и unknown.
- rank — рекомендация, не порядок маршрута; score=null не ошибка.
- NO_PRIMARY_INTEREST_MATCH означает отсутствие основных интересов в доступной выборке; fallback не становится совпадением. NO_CANDIDATES_FOR_DESTINATION означает пустую доступную выборку.
- budget_summary.known_total — integer либо точная десятичная строка. Неизвестные затраты не равны нулю, другой валюты не конвертируем автоматически.
- preview/diff до confirm; unknown только с явным согласием на draft; VERSION_CONFLICT/INPUT_DATA_CHANGED требуют новогоpreview.
- Geoapify/OSM attribution со ссылками: [Powered by Geoapify](https://www.geoapify.com/) и [OpenStreetMap contributors](https://www.openstreetmap.org/copyright). Правила карты/показа и внешней навигации соблюдает Fullstack.

## Данные и ограничения

2058 POI имеют происхождение, но не все факты проверены. Минимум час — утверждённая плановая оценка, не измерение. Оценки хранятся отдельно в data/enrichment/duration-estimates, исходные snapshots не изменены. Передавайте sidecars только для ID переданного каталога. Одна запись с возможным конфликтом адреса исключена из real demo, исходная запись сохранена для аудита.

Кэшированные Geoapify-пути и настоящие OpenRouter smoke доказывают конкретные технические вызовы; не доказывают UI/БД, качество рекомендаций у туристов, часы и доступность места. Первый real demo сохранён на двух POI; новый reviewed-three содержит три POI и два выбранных перехода, каждый со временем, расстоянием и LineString. Полный веб-показ с картой и сохранением остаётся открытым. Подробности и команды: docs/ai/reviewed-three-demo-2026-11-18.md.

Не реализуются автоматически: рейтинги/популярность без допустимого источника, полноценные расписания ОТ, валютная конвертация, гостиницы/ночёвки, реальные платежи. Premium только после стабильного P0. Пользовательские тесты заполняются реальными участниками, не агентами.

## Проверяемый архив

scripts/build_ai_ds_bundle.py копирует только AI/DS-код, данные, тесты, примеры и документацию в собственный временный каталог, проверяет сохранностькопии и запускает тесты там. В архив включаются BUILD_MANIFEST.json, VERIFICATION.json и TEST_LOG.txt. Ключи/Git/конфигурации .codex/.aws и сам artifacts не включаются. Источник — незакоммиченное рабочее дерево, не опубликованный релиз. Проверка в чистом каталоге не заменяет запуск на другой ОС с собственной базой timezone.


## Новое проверенное демо трёх POI

```powershell
python -B -m ai_ds.multiday --pois examples/routing/v02/moscow.reviewed-three.pois.json --preferences examples/routing/v02/moscow.reviewed-three.preferences.json --scenario examples/routing/v02/moscow.reviewed-three.scenario.json
python -B scripts/demo_route_assistant.py --scenario-prefix moscow.reviewed-three
python -B scripts/demo_route_assistant.py --scenario-prefix moscow.reviewed-three --decision confirm --allow-draft
```

Три обязательных места и два перехода проверены без сети. Матрица содержит шесть настоящих направленных пар второго разрешённого пилота. Входные предположения, области применимости опубликованных тарифов и ограничения часов описаны в reviewed-three-demo-2026-11-18.md. Общий статус unknown: 1800 RUB известной суммы билетов не учитывают неизвестное питание. Предыдущий двухточечный пример сохранён, default CLI не изменён.

Для reviewed-three суммы относятся к явно заданному демонстрационному типу посетителя, не ко всем пользователям. Frontend должен показывать price_scope из reports/data/reviewed-three-pois.2026-11-18.json: взрослый РФ/ЕАЭС для ГИМ/1812, основная категория основной экспозиции геологического музея. Если реальная категория посетителя неизвестна или не подходит, эти суммы нельзя применять как его подтверждённые расходы.
