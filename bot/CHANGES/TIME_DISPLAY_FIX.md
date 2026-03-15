# 🔧 Исправление отображения времени команды — актуальная реализация

## Что было не так
В сообщениях использовались константы `MORNING_DISPLAY`/`EVENING_DISPLAY` вместо фактического времени из настроек команды.

## Как сделано сейчас
- `bot/handlers/employee_handlers.py::process_new_time_value` — после изменения `daily_time` берём `display_time` из команды (`morning_time`/`evening_time`).
- `bot/handlers/registration_handlers.py::process_time_selection_registration` — при показе итоговой инфы берём время из команды, если регистрация по инвайту.
- Константы `MORNING_DISPLAY`/`EVENING_DISPLAY` в сообщениях не используются.

## Подтверждено в коде
- `employee_handlers.py`: получение `team` и выбор `display_time` из `team['morning_time'|'evening_time']`.
- `registration_handlers.py`: выбор `display_time` из `team` при наличии `team_id`.

## Пользовательский эффект
- Сообщения всегда показывают актуальное командное время.

## Статус
✅ В продакшене, соответствует текущему коду


