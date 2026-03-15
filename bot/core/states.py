from aiogram.fsm.state import State, StatesGroup


# --- Классы для состояний FSM (Finite State Machine) ---
class Registration(StatesGroup):
    waiting_for_name = State()
    waiting_for_role = State()
    waiting_for_time = State()
    waiting_for_gitverse_nickname = State()


class DailyPoll(StatesGroup):
    waiting_for_answer = State()  # Ожидание ответа на текущий вопрос (новое состояние для динамических вопросов)
    waiting_for_llm_answer = State()  # Ожидание ответа на уточняющий вопрос от LLM (оставляем для совместимости)
    waiting_for_missing_report_reason = State()  # Ожидание ответа на вопрос "Почему вы не заполнили отчет?"
    confirming_answer = State()
    
class ChangeData(StatesGroup):
    choosing_field = State()
    entering_new_value = State()


class Vacation(StatesGroup):
    waiting_for_start = State()
    waiting_for_end = State()


class ManualPoll(StatesGroup):
    waiting_for_confirmation = State()


class TeamRegistration(StatesGroup):
    choosing_action = State()
    waiting_for_preset_choice = State()
    waiting_for_team_name = State()
    waiting_for_timezone = State()
    waiting_for_chat_choice = State()
    waiting_for_chat_id = State()
    waiting_for_chat_topic = State()
    waiting_for_board_link = State()
    waiting_for_role = State()
    waiting_for_time = State()


class TeamEdit(StatesGroup):
    choosing_field = State()
    waiting_for_new_team_name = State()
    waiting_for_new_chat_id = State()
    waiting_for_new_chat_topic = State()
    waiting_for_new_board_link = State()
    waiting_for_timezone = State()


class TeamTimeSettings(StatesGroup):
    choosing_time_type = State()  # дейли или отчеты
    choosing_morning_time = State()  # выбор времени утреннего опроса
    choosing_evening_time = State()  # выбор времени вечернего опроса
    choosing_report_time = State()  # выбор времени отчетов
    choosing_report_days = State()  # выбор дней для отчетов
    confirming_settings = State()  # подтверждение настроек

# Тест для вопросов команды
class TeamQuestionsEdit(StatesGroup):
    choosing_action = State()
    waiting_for_question_type = State()  # Новое: выбор типа вопроса (общий или с вариациями)
    waiting_for_text = State()
    waiting_for_field = State()
    waiting_for_variants = State()
    waiting_for_board_related = State()
    waiting_for_morning_variant = State()
    waiting_for_evening_variant = State()
    waiting_for_edit_choice = State()
    waiting_for_edit_text = State()
    waiting_for_edit_field = State()
    waiting_for_edit_variants = State()
    waiting_for_edit_board_related = State()
    waiting_for_answer = State()  # Ожидание ответа на текущий вопрос
    waiting_for_llm_answer = State()  # Ожидание ответа на уточняющий вопрос от LLM

class WeeklyPlan(StatesGroup):
    waiting_for_plan = State()
    confirming_plan = State()


class SprintPlan(StatesGroup):
    waiting_for_entry = State()


class AddPO(StatesGroup):
    selecting_employee = State()


class POTZCreation(StatesGroup):
    """Состояния для создания ТЗ Product Owner"""
    waiting_for_answer = State()  # Ожидание ответа на текущий вопрос
    awaiting_clarify = State()  # Ожидание уточнения
    completed = State()  # Все вопросы заполнены