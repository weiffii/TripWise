# Geoapify Places: бесплатный локальный pipeline

Документация проверена 17 ноября 2026. Источник выбран для малого реального POI-набора; это не источник пользовательских рейтингов.

## Условия

- https://apidocs.geoapify.com/docs/places/ прямо разрешает хранение/кэширование результатов.
- https://www.geoapify.com/pricing/ : Free, 3000 кредитов/день, без банковской карты. Это кредиты, не число любых запросов.
- https://www.geoapify.com/terms-and-conditions/ : обязательны OSM attribution и Geoapify attribution для Free.
- Регистрация: https://myprojects.geoapify.com/ . Выбрать Free, не подключать платный тариф. Ключ хранить вне проекта и не отправлять в чат.

OpenTripMap также допускает хранение результатов, но его значимость не является пользовательским рейтингом: https://dev.opentripmap.org/product . Foursquare OS Places — отдельный глобальный bulk-продукт, не коммерческий Places API; в этот адаптер не включён: https://opensource.foursquare.com/os-places/ .

## Запуск

Ключ читается только из GEOAPIFY_API_KEY. Если он сохранён через Windows User Environment Variables, новый терминал увидит его автоматически. В уже запущенном PowerShell можно присвоить без вывода значения:

```powershell
$env:GEOAPIFY_API_KEY = [Environment]::GetEnvironmentVariable('GEOAPIFY_API_KEY', 'User')
& 'C:/msys64/ucrt64/bin/python.exe' -B -m ai_ds.sources.geoapify --city Москва --country-code RU --limit 30
& 'C:/msys64/ucrt64/bin/python.exe' -B -m ai_ds.sources.geoapify --city Санкт-Петербург --country-code RU --limit 30
& 'C:/msys64/ucrt64/bin/python.exe' -B -m unittest discover -s tests -p test_geoapify.py -v
```

Запуск начинает с разрешения административной границы города, затем запрашивает Places с filter=place:ID. При limit > 100 категории обходятся группами с пагинацией offset; ID дедуплицируются. Параметры --max-requests (по умолчанию 13, включая геокодирование) и --max-credits (по умолчанию верхняя оценка 61) ограничивают запросы. Оценка не подтверждает фактический баланс. Повторных попыток одного HTTP-запроса нет, socket timeout 30 секунд. При 402/403/429 дальнейшие запросы прекращаются; уже полученные данные сохраняются, CLI возвращает код 3. При другой ошибке после успешной страницы сохраняется частичный набор с request_failed; его наличие не означает достижения цели. Запуск без ключа не обращается к сети. Ключ не выводится, не сохраняется в metadata, сетевые ошибки не содержат URL.

Результаты в новом каталоге data/geoapify/<country>/<timestamp>: raw.json, raw-pages.json, metadata.json, pois.validated.json, quality.json. Существующие snapshots не перезаписываются. Географическая принадлежность опирается на boundary filter, а не только bbox; неоднозначный геокодер отклоняется. Отсутствие country_code, имени, категории или координат не выдумывается. В POI provenance используется Geoapify, upstream OSM атрибуция сохраняется отдельно. source_url ведёт на провайдера, place_id — ID исходной записи.

Нет новых зависимостей; контракт, Data, Recommendation и Routing не менялись. Рейтинги, популярность, цена, длительность и часы неизвестны и остаются null. Координаты не подтверждают вход. Поэтому получение POI не гарантирует рассчитанный маршрут.

Тесты используют явно искусственные ответы в памяти/временных каталогах. Они не считаются реальными данными. Число настоящих POI фиксируется только после успешного авторизованного сетевого запуска.

## Фактически выполнено 17 ноября 2026

- Пять mock-тестов прошли на Python 3.14.5; тест с временными файлами запущен с отдельным разрешением вне read-only sandbox.
- Москва: data/geoapify/ru/20261117T172229.397444Z — 21 принятый реальный POI из запроса limit=30. Категории: museums5, history9, nature3, cafes3, viewpoints1.
- Санкт-Петербург: data/geoapify/ru/20261117T172251.253217Z — 29 принятых реальных POI. Категории: museums13, cafes15, history1.
- Повторная проверка обоих массивов process_pois: 0 отклонений. Суммарно 50 настоящих POI, в каждом есть source ID и время получения, is_synthetic=false.
- Выполнены четыре бесплатных HTTP GET суммарно (geocoding+Places на город), ключ не печатался и не записывался в raw/metadata. Результаты не являются вымышленными fixtures.
- Не все восемь категорий представлены; выборка мала и не репрезентативна. null рейтинги/популярность/цены/длительности/часы не заменены оценками. Маршрут на этих POI ещё нельзя подтвердить без длительностей и перемещений.

## Расширенный реальный набор

В рамках одного дополнительного запроса limit=100 на город (и одного геокодирования на город) получены:

| Город | Каталог snapshot | Принято |
|---|---|---|
| Москва | data/geoapify/ru/20261117T172416.600600Z | 76 |
| Санкт-Петербург | data/geoapify/ru/20261117T172420.515824Z | 92 |
| Токио | data/geoapify/jp/20261117T172424.669958Z | 74 |

Итого 242 POI в трёх последних городских наборах. Все три повторно проверены process_pois: 0 отклонений. Старые малые snapshots сохранены для воспроизводимости и не суммируются как дополнительные уникальные места. Фактическое покрытие меньше limit=100: безымянные/неподдержанные/некорректные записи не подменяются. Япония подтверждает, что адаптер не привязан к RU. Общее количество HTTP GET за весь этап — 10 (два малых города и три расширенных города, по два запроса каждый).

## Два профиля на реальных данных каждого города

Команды этого раздела относятся к историческим 242 POI. Актуальные шесть команд для 2058 POI приведены в [runbook](demo-runbook.md#рекомендации-на-актуальных-реальных-poi).

Новые профили находятся в examples/preferences/real/. Основные интересы — museums/history либо nature/viewpoints, дополнительный — cafes. В каждом городе один и тот же проверенный набор. Рейтинг и популярность остаются неизвестными; различие выдачи отражает интересы, а не подтверждённую пользовательскую релевантность.

```powershell
python -B -m ai_ds.demo --pois data/geoapify/ru/20261117T172416.600600Z/pois.validated.json --preferences examples/preferences/real/moscow.museums.json
python -B -m ai_ds.demo --pois data/geoapify/ru/20261117T172416.600600Z/pois.validated.json --preferences examples/preferences/real/moscow.nature.json
python -B -m ai_ds.demo --pois data/geoapify/ru/20261117T172420.515824Z/pois.validated.json --preferences examples/preferences/real/saint-petersburg.museums.json
python -B -m ai_ds.demo --pois data/geoapify/ru/20261117T172420.515824Z/pois.validated.json --preferences examples/preferences/real/saint-petersburg.nature.json
python -B -m ai_ds.demo --pois data/geoapify/jp/20261117T172424.669958Z/pois.validated.json --preferences examples/preferences/real/tokyo.museums.json
python -B -m ai_ds.demo --pois data/geoapify/jp/20261117T172424.669958Z/pois.validated.json --preferences examples/preferences/real/tokyo.nature.json
```

При нерабочем системном python замените начало команды на & 'C:/msys64/ucrt64/bin/python.exe'. Эти команды не обращаются к API и не расходуют кредиты.

## Проверка последних расширенных snapshots

17 ноября повторно проверены Москва `ru/20261117T173836.768006Z` — 580 POI, Санкт-Петербург `ru/20261117T173907.144035Z` — 627, Токио `jp/20261117T173938.648464Z` — 851. Итого 2058, без суммирования исторических выборок. Во всех трёх metadata: target_limit=1000, stop_reason=budget_limit, 13 HTTP-попыток, верхняя оценка 61 кредит, coverage_exhaustive=false. Это не полные городские каталоги.

Повторная нормализация совпадает с сохранёнными POI, process_pois отклонений не выявил. Шесть рекомендательных CLI завершились кодом 0 с пятью результатами каждый. Девять Geoapify mock-тестов входят в успешную общую регрессию 86/86. В этой проверке сетевые вызовы не выполнялись. [Подробный результат](../../reports/data/geoapify-verification.2026-11-17.json).
