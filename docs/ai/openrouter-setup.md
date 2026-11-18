# OpenRouter для Route Assistant — 17 ноября 2026

Пользователь согласовал бесплатный OpenRouter: только openrouter/free, без пополнения и платных запасных моделей. Программный адаптер: ai_ds/openrouter.py, стандартная библиотека Python, новых зависимостей нет.

## Политика запроса

Один POST https://openrouter.ai/api/v1/chat/completions. model жёстко задан openrouter/free; models/fallback на платные модели не передаются. provider.max_price ограничен нулём для prompt/completion/request. Установлены require_parameters=true и data_collection=deny. Это фильтр провайдера, а не абсолютная гарантия отсутствия обязательного хранения по закону. При отсутствии подходящего бесплатного endpoint возвращается ошибка, настройки автоматически не ослабляются.

Передаются текст правки, даты/окна/темп дня, ID посещений, минимальный каталог ID/названий/категорий, required ID и timezone. Вложенные raw/provenance/заметки и неизвестные поля не отправляются. Секрет находится только в Authorization; ошибки не возвращают текст провайдера или ключ. Редиректы запрещены, автоматических повторов нет. Default timeout 30 секунд, max_output_tokens 1024; конфигурация задаётся сервером, не LLM. Ответ ограничен 1 MiB, JSON проверяется на повторяющиеся поля и нечисловые константы, незавершённый ответ отклоняется. Доменную проверку каждой операции и пересчёт выполняет RouteAssistant.

## Проверка условий

Официальные материалы прочитаны 17 ноября 2026:

- [Бесплатные модели и лимиты](https://openrouter.ai/collections/free-models/): заявлены 20 запросов/мин и 50/день для бесплатных вариантов без покупки кредитов. Лимиты и доступность могут меняться, ошибки также расходуют квоту.
- [Free router](https://openrouter.ai/docs/guides/routing/routers/free-router): openrouter/free выбирает доступную бесплатную модель, поэтому точная модель может меняться.
- [Правило бесплатных fallback](https://openrouter.ai/docs/guides/routing/model-variants/free): отдельные платные fallback могут тарифицироваться; наш адаптер их не передаёт.
- [Provider routing](https://openrouter.ai/docs/guides/routing/provider-selection): require_parameters, data_collection и max_price.
- [Сбор данных](https://openrouter.ai/docs/guides/privacy/data-collection): политики OpenRouter и конечного провайдера различаются; не включать опциональное сохранение текстов без необходимости.

Бесплатный сервис не даёт гарантий доступности. Доступ конкретной учётной записи из текущего окружения ещё не проверен. Полезность русского распознавания и стабильность операций требуют настоящих сценариев после получения ключа; temperature=0 не доказывает воспроизводимость меняющихся моделей.

## Локальный ключ и вызов

Создать ключ на https://openrouter.ai/settings/keys и сохранить через настройки Windows в пользовательскую переменную OPENROUTER_API_KEY. Не помещать его в Git, файлы примеров или чат. Новый терминал получает переменную автоматически. Уже открытый обычный PowerShell может прочитать её без вывода значения:

```powershell
$env:OPENROUTER_API_KEY = [Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY', 'User')
& 'C:/msys64/ucrt64/bin/python.exe' -B -m ai_ds.openrouter --live-smoke
```

Флаг --live-smoke выполняет один настоящий бесплатный запрос на явно синтетическом примере. Без ключа команда завершается с LLM_KEY_NOT_CONFIGURED без сети. Возвращает только решение и безопасные metadata; модель в metadata — выбранная провайдером, не постоянная модель TripWise.

```python
from ai_ds.openrouter import OpenRouterInterpreter
# Передать экземпляр в RouteAssistant как interpreter, согласно его документации.
interpreter = OpenRouterInterpreter()
```

## Фактические проверки

Выполнено 14 mock-тестов, все успешно:

```powershell
& 'C:/msys64/ucrt64/bin/python.exe' -B -W error::ResourceWarning -m unittest discover -s tests -p test_openrouter.py -v
```

Это искусственные ответы; сеть не использовалась. Проверено отсутствие OPENROUTER_API_KEY в process/user environment без вывода значения. Настоящий вызов пока не выполнялся; A16/F10 не объявлены закрытыми. После сохранения локального ключа нужен отдельный smoke и интеграционная проверка propose/confirm.

## Актуализация 18 ноября

Локальный ключ настроен без сохранения в репозитории. Настоящий smoke завершился успешно: reports/llm/openrouter-live.2026-11-18.json. Отдельно прошла реальная интеграция с Assistant: reports/llm/assistant-live.2026-11-18.json, включая preview, confirm до версии 2 и идемпотентное повторное подтверждение. Всего два вызова, без платных fallback. Предыдущая запись об отсутствии ключа относится к состоянию 17 ноября. Два примера не доказывают качество распознавания на широком наборе запросов.
