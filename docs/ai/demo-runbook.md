# Демонстрация AI/DS TripWise

Проверено 17 ноября 2026 года из корня репозитория. Это локальный прототип, не веб-приложение. В основном показе ниже места синтетические, переходы заданы тестовой матрицей. Реальные рекомендации запускаются отдельными командами в конце документа.

## Запуск

Нужен Python 3.11+ с доступной базой часовых поясов. Новых зависимостей для этих команд не требуется. В текущем окружении проверен установленный `C:/msys64/ucrt64/bin/python.exe` версии 3.14.5. Если `python` указывает на нерабочий системный псевдоним Windows, используйте полный путь, как ниже.

```powershell
& 'C:/msys64/ucrt64/bin/python.exe' -B -m unittest discover -s tests -v
& 'C:/msys64/ucrt64/bin/python.exe' -B -m ai_ds.demo --pois data/processed/pois.synthetic.validated.json --preferences examples/preferences/history.json
& 'C:/msys64/ucrt64/bin/python.exe' -B -m ai_ds.demo --pois data/processed/pois.synthetic.validated.json --preferences examples/preferences/nature.json
& 'C:/msys64/ucrt64/bin/python.exe' -B -m ai_ds.routing --pois data/processed/pois.synthetic.validated.json --preferences examples/preferences/history.json --scenario examples/routing/day.synthetic.json
```

На другой машине замените путь интерпретатора на свой рабочий Python. CLI пишет UTF-8 JSON. Для сохранения JSON из PowerShell используйте явно заданную UTF-8 кодировку, а не системную кодировку перенаправления.

## Что показать за 3–5 минут

1. Открыть два файла `examples/preferences/*.json`: показать различие основных и дополнительных интересов на одном POI-наборе.
2. Запустить два рекомендательных CLI и сравнить ID и reasons.
3. Объяснить модель: число основных совпадений → дополнительных → наличие и значение нормализованного рейтинга → популярность → дата проверки → ID для равенств. Численных весов нет, `score: null`.
4. Запустить Routing CLI: он выбирает порядок посещений отдельно от ранжирования, учитывает направленные переходы, обязательные места, отдых и время.
5. Показать ограничения: тестовая матрица, неизвестные часы, бюджет не проверен, общий маршрут `unknown`.

## Подтверждённая выдача

| Профиль | Top-5, префикс всех ID — synthetic: |
|---|---|
| history | museums-01; museums-09; history-02; history-10; nature-03 |
| nature | unusual-08; nature-03; nature-11; museums-09; museums-01 |

Оба CLI завершились кодом 0. Различие выдачи подтверждает выполнение контрольного сценария, не пользовательскую релевантность.

Маршрут: museums-01 09:00–09:30; museums-09 09:55–10:25; history-02 10:50–11:35. Между посещениями — отдых и переходы, описанные в `examples/routing/day.synthetic.json`. Активное время 7500 секунд, с отдыхом 9300 секунд. Код завершения 0.

Проверки результата: `time: passed`, `required_places: passed`, `opening_hours: unknown`, `budget: not_checked`, общий `status: unknown`. Это не реальная пешая навигация и не подтверждение открытости музеев.

## Проверки и нерешённые вопросы

На исходном состоянии коммита `7aa6746` выполнено 74 unittest-теста, все прошли. В том числе OSM-тесты используют искусственные mock-ответы и не доказывают успешную загрузку реальных данных. Последующие изменения требуют отдельного актуального результата тестов.

Не показаны и не считаются готовыми: многодневный маршрут, автоматическая модель темпа, внешние рейтинги, реальный Routing API, LLM Route Assistant, frontend/backend/БД и сохранение поездок. Отсутствие этих компонентов в данной копии не доказывает их отсутствие у напарника.

На встрече согласовать фактический backend-стек и способ вызова AI/DS, состояние приложения у напарника, получение реальных данных и переходов, следующие API-контракты и модели. Интервью и пользовательские тесты подтверждаются только реальными записями исследований.

## Рекомендации на актуальных реальных POI

Проверены 2058 POI: Москва 580, Санкт-Петербург 627, Токио 851. Полная регрессия текущего рабочего дерева: 86/86 тестов успешно с `-W error::ResourceWarning`. Следующие шесть команд проверены: код 0, по пять результатов; запросов к API нет.

```powershell
& 'C:/msys64/ucrt64/bin/python.exe' -B -m ai_ds.demo --pois data/geoapify/ru/20261117T173836.768006Z/pois.validated.json --preferences examples/preferences/real/moscow.museums.json
& 'C:/msys64/ucrt64/bin/python.exe' -B -m ai_ds.demo --pois data/geoapify/ru/20261117T173836.768006Z/pois.validated.json --preferences examples/preferences/real/moscow.nature.json
& 'C:/msys64/ucrt64/bin/python.exe' -B -m ai_ds.demo --pois data/geoapify/ru/20261117T173907.144035Z/pois.validated.json --preferences examples/preferences/real/saint-petersburg.museums.json
& 'C:/msys64/ucrt64/bin/python.exe' -B -m ai_ds.demo --pois data/geoapify/ru/20261117T173907.144035Z/pois.validated.json --preferences examples/preferences/real/saint-petersburg.nature.json
& 'C:/msys64/ucrt64/bin/python.exe' -B -m ai_ds.demo --pois data/geoapify/jp/20261117T173938.648464Z/pois.validated.json --preferences examples/preferences/real/tokyo.museums.json
& 'C:/msys64/ucrt64/bin/python.exe' -B -m ai_ds.demo --pois data/geoapify/jp/20261117T173938.648464Z/pois.validated.json --preferences examples/preferences/real/tokyo.nature.json
```

Контрольные ID и результаты нормализации сохранены в [отчёте проверки](../../reports/data/geoapify-verification.2026-11-17.json). Выборки частичные: загрузки остановлены по бюджету запросов. Рейтинг и популярность неизвестны, поэтому выдача не доказывает качество или популярность мест. Длительности и реальные переходы также неизвестны: этот показ подтверждает рекомендации, но не выполнимость туристического маршрута.

## Актуальный показ версии 0.2 — 18 ноября

Предыдущие ограничения и числа тестов выше относятся к показу 17 ноября. Сейчас реализованы многодневность, темп, интервалы пауз, бюджетные проверки и Assistant. Итоговая регрессия изолированной копии: 188/188. Новые команды запуска без сети:

```powershell
python -B -m ai_ds.multiday --pois examples/routing/v02/pois.synthetic.json --preferences examples/routing/v02/preferences.synthetic.json --scenario examples/routing/v02/scenario.synthetic.json
python -B -m ai_ds.multiday --pois examples/routing/v02/moscow.real.pois.json --preferences examples/routing/v02/moscow.real.preferences.json --scenario examples/routing/v02/moscow.real.scenario.json
python -B scripts/demo_route_assistant.py
python -B scripts/demo_route_assistant.py --decision reject
python -B scripts/demo_route_assistant.py --decision confirm --allow-draft
```

На этой машине вместо python можно использовать C:/msys64/ucrt64/bin/python.exe. Первый пример синтетический. Второй использует два реальных POI, кэшированный Geoapify-переход и отдельно помеченные оценки длительности. Его статус unknown: цены и часы не подтверждены. Assistant по умолчанию использует synthetic_fixture интерпретатора, а не делает запрос LLM. Preview не меняет маршрут; reject сохраняет версию, confirm требует явного согласия на draft.

На защите показать inputs, посещения/legs/breaks, предупреждения, preview и подтверждение версии. Объяснить, что сервер повторно проверяет ограничения, а LLM предлагает только операции. Не называть memory store постоянным сохранением поездки. Флаг --live запускает реальный OpenRouter и для обычной репетиции не нужен. Geoapify повторно не вызывать: разрешённые шесть запросов уже потрачены.

Два AI/DS-слайда сохранены как редактируемая презентация artifact_puwVgRUe_R3l_i513Lwl3THNXCD6U-kQ. Для общего показа требуется включение в командную презентацию и репетиция. Полный текущий остаток: remaining-work-2026-11-18.md.

## Последний вариант для репетиции

Использовать reviewed-three-demo-2026-11-18.md: три реальных POI, каждый выбранный путь имеет геометрию/время/расстояние. Вызов Assistant: python -B scripts/demo_route_assistant.py --scenario-prefix moscow.reviewed-three. Все данные уже локальны, сеть не требуется. Это unknown/draft, а не подтверждённый сеанс посещения музеев. Два разрешённых Geoapify-пилота по шесть запросов исчерпаны.
