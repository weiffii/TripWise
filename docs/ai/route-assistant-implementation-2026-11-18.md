# Route Assistant 0.2 — реализация

18 ноября 2026. Реализован согласованный R5 в ai_ds/route_assistant.py, с применением route-validation. Никакого прямого доступа LLM к БД, коду или URL fetch нет. Контракт0.1 сохраняется.

## Python API

```python
from ai_ds.route_assistant import InMemoryRouteStore, RouteAssistant
from ai_ds.openrouter import OpenRouterInterpreter
store = InMemoryRouteStore()
# Backend передаёт доверенные валидированные данные и actor_id после авторизации:
store.register_route(actor_id, route_id, preferences=preferences, pois=pois,
    recommendation=recommendation, travel_times=matrix, routing_context=context,
    poi_snapshot_id=context['poi_snapshot_id'], route=route, version=1)
assistant = RouteAssistant(store, OpenRouterInterpreter(), proposal_ttl_seconds=server_ttl)
preview = assistant.propose(actor_id, {
    'schema_version':'0.2', 'route_id':route_id, 'base_version':1,
    'poi_snapshot_id':context['poi_snapshot_id'], 'text':'Сделай указанный день спокойнее'})
# Показываем diff/checks/warnings/candidate_route пользователю, затем:
result = assistant.decide(actor_id, {
    'proposal_id':preview['proposal_id'], 'base_version':1, 'decision':'confirm'},
    allow_draft=explicit_user_draft_consent)
```

TTL — обязательная серверная настройка, не универсальная константа. InMemoryRouteStore — reference adapter одного процесса; не заменяет production DB, HTTP-авторизацию, персистентность и распределённую CAS-транзакцию. get_route возвращает копию. refresh_data — доверенная backend-операция; клиенту не предоставляется. Backend должен синхронно передавать poi_snapshot_id в context и store.

## Операции и гарантии

Интерпретатор принимает минимальные text/days/catalog/required_poi_ids/timezone и возвращает только action/operations/clarification. Разрешены replace_poi(day,target_poi_id,desired_category), add_poi(day nullable,poi_id), remove_poi(poi_id), set_day_pace(day,pace), set_meal_break(day,start_at,end_at). Точные поля определены OPERATIONS; лишние поля, неизвестные enum/ID, повторные JSON-ключи и NaN отклоняются.

Replace-кандидат выбирается существующим Recommendation, не LLM. Add/replace могут использовать допустимые POI вне старого top-K. Обязательные места удалять/заменять нельзя; destination/dates/transport/budget/required не изменяются. Темп дня задаётся day_pace_overrides, глобальный профиль неизменён. Весь набор атомарен: копия → операции → пересчёт → независимая проверка → preview.

Propose принимает ровно schema_version/route_id/base_version/poi_snapshot_id/text. Actor устанавливает backend. Ответ: proposal_id/base_version/operations/diff/candidate_route/checks/warnings/validation_status/requires_draft_confirmation/expires_at. ready_for_confirmation не равно feasible; unknown требует отдельного явного allow_draft=true.

Decide принимает ровно proposal_id/base_version/decision(confirm|reject). Версия строго int (не bool), allow_draft строго bool. Проверяются владелец, TTL, версия, fingerprint исходного маршрута и входных данных. Confirm повторно считает маршрут, сравнивает с preview и атомарно увеличивает версию под lock. Stale/data change → новый preview; повтор того же решения идемпотентен для владельца, противоположное решение после завершения отклоняется. Reject не меняет исходник. Клиентская модификация копии preview не меняет хранимый proposal.

Стоимость не передаётся LLM. Текстовые ценовые фильтры вне allowlist и возвращают needs_clarification с UNKNOWN_COST/COST_FILTER_NOT_SUPPORTED. Неоднозначный день требует уточнения интерпретатора. Ошибка провайдера не раскрывает исходное исключение/ключ. AssistantError.response возвращает schema0.2/error{code,path}.

## Проверка и границы

24/24 теста Assistant успешно: память/авторизация/атомарность/версии/idempotency/TTL/draft, настоящие multiday-функции и mocked OpenRouter. Реальный отдельный OpenRouter smoke подтверждён root, но не равен веб-тесту Assistant. Ни одного дополнительного сетевого запроса эти тесты не делают. Полная регрессия 175/175 успешно. Непроверены UI/API/БД, production concurrency, реальные пользовательские тесты и фактическая выполнимость поездок при неизвестных длительностях/ценах/часах.
