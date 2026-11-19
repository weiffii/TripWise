# TripWise Moscow — частичный реальный слой (19.11.2026)

**Статус:** доступен готовый реальный культурный слой, но **не** полноценная база всей Москвы и **не** полностью подтверждённые работающие заведения.

- **1 281** кандидатов из одной опубликованной OSM-производной карты;
- **1 268** уникальных после удаления 13 точных дублей;
- **1 224** записи уровня B, **44** уровня C, **0** уровня A;
- 495 музеи/галереи, 398 развлечения (театры/кино), 375 парки;
- покрытие 124 районов старой Москвы в источнике, из них 121 район представлен POI; ТиНАО не входит;
- кафе, магазины, памятники и смотровые в этом извлечении отсутствуют.

## Просмотр без запуска команд

Откройте `artifacts/tripwise-moscow-coverage.html` — полная интерактивная карта; `artifacts/tripwise-moscow-verified.html` — **525 записей B из центра**, не независимо подтверждённые A. Для быстрого просмотра есть `artifacts/moscow-poi-coverage.png`. HTML содержит офлайн-координатный режим, если Leaflet/CDN недоступны; онлайн-подложка и Leaflet-кластеры требуют сети.

## Данные

`data/demo/moscow/pois_real_master.json`, `pois.csv`, `pois.geojson` — основа. `pois_tripwise_demo.json` — предпросмотр B, **не совместимый с Shanghai Engine без адаптации**. Список проблем — `quality_review.csv`, дополнительные скрипты и исходные строки — в `scripts/` и `data/demo/moscow/`.

## Аудит

Прочитайте `reports/MOSCOW_DATA_AUDIT.md`, `DATA_PROVENANCE_AND_LICENSES.md`, `TRIPWISE_MOSCOW_INTEGRATION.md`, `MOSCOW_DATA_STATISTICS.json`.

Для повторной локальной сборки: `python scripts/build_moscow_catalog.py` и `python scripts/render_moscow_artifacts.py`. **Оба скрипта уже выполнены:** результаты в архиве готовые. Скрипты не обращаются к сети и воспроизводят снимок за 19.11.2026, но не собирают новую актуальную OSM-выгрузку.

Данные OSM © OpenStreetMap contributors, ODbL 1.0; дополнительно укажите исходный [проект визуализации](https://github.com/ArtemIlinn/masters-urban-analytics).
