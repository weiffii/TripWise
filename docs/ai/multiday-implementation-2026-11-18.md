# Многодневная маршрутизация 0.2 — реализация

18 ноября 2026. Реализован утверждённый R1–R5, применён route-validation. Старый ai_ds/routing.py/0.1 и исходные POI не меняются.

Модель multiday-insertion-v0.2 сначала размещает наиболее ограниченный required (число допустимых вставок, затем ID), затем optional по Recommendation.rank. Вставка выбирается по delta travel → окончание посещений дня → дата → позиция. Каждая вставка полностью пересчитывается по directed matrix, окну, темпу, паузам, открытиям и глобальному бюджету. Пул items+required явный; нет скрытого cap 3 дня/8 POI, но нет и гарантии глобального оптимума. Консервативная сложность required-фазы O(D*N^4), optional O(D*N^3), матрица O(N^2).

build_route создаёт draft; schedule_sequences сохраняет явные дни/порядок при правке; validate_route независимо пересчитывает выбранные последовательности. Точная схема: [контракт0.2](routing-contract-v0.2.md). Дневной темп влияет на активное окно и отдых, не фиксированное число мест. Все паузы явные, отдых после последнего POI не добавляется. Нельзя разделять активность обедом или пересекать закрытый интервал.

Strict unknown сохраняет неизвестную duration/leg без нулевых подстановок. Неудача insertion → unknown/SEARCH_NOT_PROVEN; infeasible только по доказанному необходимому условию (required не помещается ни в один день или неизбежные известные затраты превышают лимит). Частичная проверенная выдача не объявляется полным исполнением required. Начало/конец — первый/последний POI, гостиницы/ночёвки не включены.

## Воспроизведение

```powershell
& 'C:/msys64/ucrt64/bin/python.exe' -B -m ai_ds.multiday --pois examples/routing/v02/pois.synthetic.json --preferences examples/routing/v02/preferences.synthetic.json --scenario examples/routing/v02/scenario.synthetic.json
& 'C:/msys64/ucrt64/bin/python.exe' -B -m unittest discover -s tests -p test_multiday.py -v
& 'C:/msys64/ucrt64/bin/python.exe' -B scripts/compare_multiday_oracle.py --output reports/routing/oracle-comparison.2026-11-18.json
```

Проверенный пример — явно синтетический: 2 дня по 2 посещения, оба required сохранены, time/required passed; global unknown, opening unknown, budget not_checked. 25/25 тестов маршрутизатора успешно. Oracle:24 случая ≤5 кандидатов,22 совпадения rank-vector,20 найденных exact-расписаний,0 проверочных ошибок. Расхождение2случаев — измеренное ограничение эвристики, не подтверждение пользовательского качества. Общая регрессия175/175 успешна.

Исправлены реальные дефекты: known failure на микросекундной границе затирался unknown; validator принимал ложный budget passed/неполные статусы. Сохранены воспроизведения, hashes, регрессии и независимый ретест в reports/qa. Оценочные/тестовые исходные данные маркируются, не превращаются в проверенные факты. Реальные 2058 POI всё ещё не имеют подтверждённых duration/cost/hours; код не заполняет их ради демо. Веб-интеграция и настоящие user tests впереди.
