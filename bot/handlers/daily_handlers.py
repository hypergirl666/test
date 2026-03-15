import asyncio
import logging
import pytz
import json
from datetime import datetime, timedelta
from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import TIME_CONSTANTS, MORNING_DB_VALUE, EVENING_DB_VALUE, REPORT_SEND_TIME, TIMEZONE
from bot.core import router, DailyPoll
from bot.core.database import db_get_employees_for_daily, db_get_employee, db_add_report, db_update_report_llm_answer, db_get_team_by_id, db_get_team_questions, db_add_report_with_team, db_add_missing_report_reason
from bot.utils.llm_utils import llm_processor
from bot.utils import is_on_vacation, confirm_daily_keyboard, send_or_edit_message, menu_inline_keyboard, validate_text_or_voice_message, get_error_message_for_expected_text_or_voice, send_message_with_retry, extract_text_from_message, voice_confirmation_keyboard
from bot.utils.keyboards import missing_report_reply_keyboard
from bot.utils.notification_queue import get_notification_queue
from bot.utils.utils import is_gitverse_board_link, build_gitverse_personal_board_link, get_current_time
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from bot.core.database import db_get_membership
from bot.core.database import db_get_daily_period_reports
from bot.core.database import db_get_team_members
from bot.utils.scheduler_manager import scheduler_manager
from bot.utils.scheduler_jobs import run_async_function

async def get_questions(daily_time, is_manual_poll=False, user_id=None, team_id=None):
    """Получение текстов вопросов из базы данных с правильной логикой вариантов."""
    
    # Получаем информацию о команде и сотруднике
    if team_id is None:
        employee = await db_get_employee(user_id)
        if not employee or not employee.get('team_id'):
            logging.error(f"Не найден сотрудник или team_id для пользователя {user_id}")
            return {'questions': [], 'log_names': []}
        team_id = employee['team_id']

    team_info = await db_get_team_by_id(team_id) # Использует переданную команду
    if not team_info:
        logging.error(f"Не найдена команда с ID {team_id}")
        return {'questions': [], 'log_names': []}

    # Получаем все вопросы команды
    questions = await db_get_team_questions(team_info['id'])
    if not questions:
        logging.warning(f"Вопросы для команды {team_info['id']} не найдены.")
        return {'questions': [], 'log_names': []}
    
    logging.info(f"Для пользователя {user_id} используется daily_time: '{daily_time}'")

    # Формируем вопросы
    formatted_questions = []
    for q in questions[:5]:  # Ограничиваем до 5 вопросов
        # Выбираем текст с учётом time_variants
        time_variants = q.get('time_variants', {})
        final_text = time_variants.get(daily_time, q.get('text', 'Текст вопроса не найден'))
        logging.info(f"Вопрос field={q.get('field')}, daily_time={daily_time}, final_text={final_text}")

        # Добавляем ссылку на доску
        if q.get('board_related') and team_info.get('board_link'):
            board_link = team_info['board_link']
            board_link_text = ""
            try:
                employee = await db_get_employee(user_id) # Получаем employee для board_link
                if employee.get('gitverse_nickname') and is_gitverse_board_link(board_link):
                    personal = build_gitverse_personal_board_link(board_link, employee['gitverse_nickname'])
                    board_link_text = f"\n\n📋 <b>Доска (ваши задачи):</b> {personal}"
                else:
                    board_link_text = f"\n\n📋 <b>Доска команды:</b> {board_link}"
                
                final_text += board_link_text
            except Exception as e:
                logging.error(f"Ошибка при добавлении board_link: {e}")

        # Добавляем готовый вопрос в список
        formatted_questions.append({
            'text': final_text,
            'field': q['field'],
            'id': q['id'],
            'board_related': q.get('board_related', False)
        })

    # Возвращаем результат
    log_names = [q['field'] for q in formatted_questions]
    return {
        'first': formatted_questions[0]['text'] if len(formatted_questions) > 0 else None,
        'second': formatted_questions[1]['text'] if len(formatted_questions) > 1 else None,
        'third': formatted_questions[2]['text'] if len(formatted_questions) > 2 else None,
        'log_names': log_names,
        'questions': formatted_questions
    }

def _get_greeting(time_str):
    """Получить приветствие в зависимости от времени"""
    # Если передана строка времени в формате HH:MM
    if ':' in time_str:
        hour = int(time_str.split(':')[0])
    else:
        # Если передана константа времени (morning/evening), используем display значение
        if time_str == MORNING_DB_VALUE:
            hour = TIME_CONSTANTS['morning']['hour']
        elif time_str == EVENING_DB_VALUE:
            hour = TIME_CONSTANTS['evening']['hour']
        else:
            # По умолчанию используем утреннее время
            hour = TIME_CONSTANTS['morning']['hour']
    if hour < 12:
        return "☀️<b>Доброе утро</b>☀️"
    elif hour < 18:
        return "🌞<b>Добрый день</b>🌞"
    else:
        return "🌙<b>Добрый вечер</b>🌙"

def _calculate_deadline_time(daily_time: str, report_time_override: str | None = None) -> tuple:
    """Рассчитать время до дедлайна отправки отчета
    
    Args:
        daily_time: 'morning' или 'evening'
        report_time_override: строка 'HH:MM' для переопределения времени отчёта (опционально)
    Returns:
        (hours, minutes, deadline_datetime)
    """


    from datetime import datetime, timedelta
    from bot.utils.utils import get_current_time

    now = get_current_time()
    # Определяем целевое время отчёта
    if report_time_override:
        try:
            override_hour, override_minute = map(int, report_time_override.split(":"))
        except Exception:
            override_hour = REPORT_SEND_TIME['hour']
            override_minute = REPORT_SEND_TIME['minute']
    else:
        override_hour = REPORT_SEND_TIME['hour']
        override_minute = REPORT_SEND_TIME['minute']
    if daily_time == EVENING_DB_VALUE:
        # Для вечерней группы: считаем время до полуночи + время от полуночи до отчёта
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        report_time = now.replace(
            hour=override_hour,
            minute=override_minute,
            second=0,
            microsecond=0
        ) + timedelta(days=1)
        # Время до полуночи
        time_to_midnight = midnight - now
        # Время от полуночи до отчета (10:00)
        time_from_midnight = report_time - midnight

        # Общее время
        total_time = time_to_midnight + time_from_midnight
        deadline = now + total_time

    else:
        # Для утренней группы: дедлайн до 10:00 того же дня
        deadline = now.replace(
            hour=override_hour,
            minute=override_minute,
            second=0,
            microsecond=0
        )

        # Если дедлайн уже прошел сегодня, то до завтрашнего
        if now >= deadline:
            deadline = deadline + timedelta(days=1)
    # Рассчитываем разность
    time_diff = deadline - now
    total_minutes = int(time_diff.total_seconds() / 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60

    return hours, minutes, deadline


def _compute_team_next_deadline(team: dict) -> datetime | None:
    """Вычислить ближайший дедлайн по настройкам команды (в TZ команды)."""
    try:
        report_time_str = team.get('report_time')
        report_days = team.get('report_days')
        if not report_time_str or not report_days:
            return None
        report_hour, report_minute = map(int, report_time_str.split(':'))
        tz_name = team.get('timezone') or TIMEZONE
        tz = pytz.timezone(tz_name)
        trigger = CronTrigger(hour=report_hour, minute=report_minute, day_of_week=report_days, timezone=tz)
        now = datetime.now(tz)
        today_key = now.strftime('%a').lower()[:3]
        days_list = [d.strip() for d in report_days.split(',') if d.strip()]
        today_deadline = now.replace(hour=report_hour, minute=report_minute, second=0, microsecond=0)
        if today_key in days_list and now <= today_deadline:
            return today_deadline
        return trigger.get_next_fire_time(None, now)
    except Exception:
        return None


async def _send_incomplete_report_reminder(user_id: int, team_id: int, kind: str) -> None:
    """Отправить напоминание, если отчёт ещё не отправлен и дедлайн не прошёл."""
    logging.info(f"Попытка отправить напоминание ({kind}) для user_id={user_id}, team_id={team_id}")
    try:
        team = await db_get_team_by_id(team_id)
        if not team:
            return
        
        # Проверяем наличие отчёта
        reports = await db_get_daily_period_reports(team_id=team_id, team=team)
        if any(r.get('tg_id') == user_id for r in reports if isinstance(r, dict)):
            logging.info(f"Напоминание пропущено: отчёт уже отправлен для user_id={user_id}, team_id={team_id}")
            return
        
        # Проверяем, что дедлайн не прошёл
        tz = pytz.timezone(team.get('timezone') or TIMEZONE)
        now_local = datetime.now(tz)
        deadline = _compute_team_next_deadline(team)
        
        if not deadline or now_local >= deadline:
            logging.info(f"Напоминание пропущено: дедлайн прошёл для user_id={user_id}, team_id={team_id}, deadline={deadline}")
            return
        
        # Формируем текст напоминания
        left_minutes = int((deadline - now_local).total_seconds() // 60)
        hours = left_minutes // 60
        minutes = left_minutes % 60
        
        if hours > 0:
            time_left = f"{hours} ч. {minutes} мин."
        else:
            time_left = f"{minutes} мин."
        
        if kind == 'after_start':
            text = (
                f"⏰ <b>Напоминание</b>\n\n"
                f"Вы начали заполнять отчёт по команде <b>{team.get('name', '')}</b>, но ещё не отправили его.\n\n"
                f"⏳ До дедлайна осталось: <b>{time_left}</b>"
            )
        else:  # before_deadline
            text = (
                f"⏰ <b>Напоминание</b>\n\n"
                f"Вы ещё не отправили отчёт по команде <b>{team.get('name', '')}</b>.\n\n"
                f"⏳ До дедлайна осталось: <b>{time_left}</b>"
            )
        
        await send_message_with_retry(user_id, text.strip(), remove_keyboard=False)
        logging.info(f"Напоминание ({kind}) отправлено для user_id={user_id}, team_id={team_id}")
    except Exception as e:
        logging.error(f"Ошибка при отправке напоминания пользователю {user_id} по команде {team_id}: {e}")


async def _cancel_reminders(job_ids: list[str]) -> None:
    """Отменить напоминания по списку job_id"""
    if not job_ids:
        return
    try:
        scheduler = scheduler_manager.get_scheduler()
        if scheduler:
            for job_id in job_ids:
                try:
                    scheduler.remove_job(job_id)
                    logging.info(f"Напоминание отменено: {job_id}")
                except Exception:
                    pass
    except Exception:
        pass

async def _schedule_after_start_reminder(user_id: int, team_id: int, team_timezone: str) -> str | None:
    """Поставить напоминание через 20 минут после старта опроса.
    Возвращает job_id для последующей отмены или None."""
    scheduler = scheduler_manager.get_scheduler()
    if not scheduler:
        return None

    try:
        tz = pytz.timezone(team_timezone or TIMEZONE)
    except Exception:
        tz = pytz.timezone(TIMEZONE)

    now_local = datetime.now(tz)
    team = await db_get_team_by_id(team_id)
    deadline = _compute_team_next_deadline(team) if team else None
    
    # Если дедлайн прошёл или не определён, не ставим напоминание
    if not deadline or now_local >= deadline:
        return None

    # Напоминание через 20 минут после старта (только если дедлайн не пройдёт к этому времени)
    run_at = now_local + timedelta(minutes=20)
    if run_at >= deadline:
        return None

    try:
        # Используем фиксированный job_id без timestamp, чтобы при повторном нажатии "Готов" задача обновлялась
        job_id = f"remind_after_start_{user_id}_{team_id}"
        u_id, t_id = user_id, team_id
        def _after_start_wrapper():
            run_async_function(_send_incomplete_report_reminder, u_id, t_id, 'after_start')
        scheduler.add_job(
            _after_start_wrapper,
            trigger=DateTrigger(run_date=run_at, timezone=tz),
            id=job_id,
            replace_existing=True,
        )
        logging.info(f"Напоминание 'через 20 минут' поставлено для user_id={user_id}, team_id={team_id}, запуск: {run_at.strftime('%Y-%m-%d %H:%M:%S')}")
        return job_id
    except Exception as e:
        logging.error(f"Не удалось поставить напоминание после старта для {user_id}: {e}")
        return None

async def update_team_reminders(team_id: int) -> None:
    """Обновить напоминания 'за 20 минут до дедлайна' для всех пользователей команды при изменении времени команды"""
    try:
        from bot.core.database import db_get_team_members
        scheduler = scheduler_manager.get_scheduler()
        if not scheduler:
            return
            
        team = await db_get_team_by_id(team_id)
        if not team:
            return
        
        team_tz = team.get('timezone') or TIMEZONE
        members = await db_get_team_members(team_id)
        
        for member in members:
            user_id = member.get('tg_id')
            if user_id:
                # Отменяем старое напоминание перед постановкой нового
                old_job_id = f"remind_before_deadline_{user_id}_{team_id}"
                try:
                    scheduler.remove_job(old_job_id)
                except Exception:
                    pass  # Напоминание может не существовать
                
                try:
                    await _schedule_before_deadline_reminder(user_id, team_id, team_tz)
                except Exception as e:
                    logging.error(f"Не удалось обновить напоминание перед дедлайном для {user_id}: {e}")
    except Exception as e:
        logging.error(f"Ошибка при обновлении напоминаний для команды {team_id}: {e}")

async def _schedule_before_deadline_reminder(user_id: int, team_id: int, team_timezone: str) -> str | None:
    """Поставить напоминание за 20 минут до дедлайна для пользователя.
    Возвращает job_id или None."""
    scheduler = scheduler_manager.get_scheduler()
    if not scheduler:
        return None

    try:
        tz = pytz.timezone(team_timezone or TIMEZONE)
    except Exception:
        tz = pytz.timezone(TIMEZONE)

    now_local = datetime.now(tz)
    team = await db_get_team_by_id(team_id)
    deadline = _compute_team_next_deadline(team) if team else None
    
    if not deadline:
        return None

    run_at = deadline - timedelta(minutes=20)
    if run_at <= now_local:
        return None

    try:
        # Используем фиксированный job_id без timestamp, чтобы при изменении времени команды напоминание можно было обновить
        job_id = f"remind_before_deadline_{user_id}_{team_id}"
        u_id, t_id = user_id, team_id
        def _before_deadline_wrapper():
            run_async_function(_send_incomplete_report_reminder, u_id, t_id, 'before_deadline')
        scheduler.add_job(
            _before_deadline_wrapper,
            trigger=DateTrigger(run_date=run_at, timezone=tz),
            id=job_id,
            replace_existing=True,
        )
        logging.info(f"Напоминание 'за 20 мин до дедлайна' поставлено для user_id={user_id}, team_id={team_id}, запуск: {run_at.strftime('%Y-%m-%d %H:%M:%S')}, дедлайн: {deadline.strftime('%Y-%m-%d %H:%M:%S')}")
        return job_id
    except Exception as e:
        logging.error(f"Не удалось поставить напоминание перед дедлайном для {user_id}: {e}")
        return None

async def _prepare_daily_invitation(tg_id, time_str, greeting, timezone_str, team_id: int | None = None):
    """Подготовить параметры приглашения на дейли для добавления в очередь"""
    # Получаем данные сотрудника для определения группы
    employee = await db_get_employee(tg_id)
    daily_time = employee["daily_time"] if employee else MORNING_DB_VALUE

    # Дедлайн = время следующей отправки отчёта команды (по расписанию)
    from bot.utils.utils import get_current_time
    deadline = None
    # Определяем команду для расчёта дедлайна: приоритезируем явный team_id
    team = None
    if team_id:
        from bot.core.database import db_get_team_by_id
        team = await db_get_team_by_id(team_id)
    elif employee and employee['team_id']:
        from bot.core.database import db_get_team_by_id
        team = await db_get_team_by_id(employee['team_id'])

    if team and team.get('report_time') and team.get('report_days'):
        try:
            from apscheduler.triggers.cron import CronTrigger
            from bot.config import TIMEZONE
            report_time_str = team['report_time']
            report_hour, report_minute = map(int, report_time_str.split(':'))
            report_days = team['report_days']
            team_timezone = (team.get('timezone') or TIMEZONE) if isinstance(team, dict) else TIMEZONE
            trigger = CronTrigger(hour=report_hour, minute=report_minute, day_of_week=report_days, timezone=team_timezone)
            tz = pytz.timezone(team_timezone)
            now = datetime.now(tz)
            today_key = now.strftime('%a').lower()[:3]
            days_list = [d.strip() for d in report_days.split(',') if d.strip()]
            today_deadline = now.replace(hour=report_hour, minute=report_minute, second=0, microsecond=0)
            if today_key in days_list and now <= today_deadline:
                deadline = today_deadline
            else:
                deadline = trigger.get_next_fire_time(None, now)
        except Exception:
            deadline = None
    # Фоллбек: если не удалось вычислить по расписанию — считаем как раньше
    if deadline is None:
        _, _, deadline = _calculate_deadline_time(daily_time)
    # Считаем сколько времени осталось
    # Используем локальный для команды now для расчёта дельты
    try:
        team_timezone = team['timezone'] if team else None
    except Exception:
        team_timezone = None
    if team_timezone:
        now_local = datetime.now(pytz.timezone(team_timezone))
    else:
        now_local = get_current_time()
    total_minutes = max(0, int((deadline - now_local).total_seconds() // 60))
    hours = total_minutes // 60
    minutes = total_minutes % 60

    # Форматируем время дедлайна с меткой дня
    deadline_str = deadline.strftime("%H:%M")
    delta_days = (deadline.date() - now_local.date()).days
    if delta_days == 0:
        day_label = "сегодня"
    elif delta_days == 1:
        day_label = "завтра"
    else:
        day_label = deadline.strftime("%d.%m")

    # Добавляем название команды, если известно
    team_name_line = ""
    try:
        if team:
            team_name_line = f"Команда: <b>{team['name']}</b>\n\n"
    except Exception:
        team_name_line = ""

    message_text = (
        f"{greeting}\n"
        f"Пришло время дейли!\n\n"
        f"{team_name_line}"
        f"У вас есть <b>{hours} ч. {minutes} мин.</b> на отправку отчёта\n"
        f"Дедлайн: <b>{day_label}, {deadline_str} ({timezone_str})</b>\n\n"
        f"Можно отвечать текстовыми и голосовыми сообщениями🗣\n\n"
        f"<b>Готовы ответить на несколько вопросов?</b>"
    )


    return {
        'chat_id': tg_id,
        'text': message_text,
        'reply_markup': confirm_daily_keyboard(team_id=team_id),
        'is_report': True  # Используем увеличенные задержки
    }


# --- Флоу ежедневного опроса ---
async def send_daily_questions(time_str: str, team_id: int = None):
    """Отправка приглашений на ежедневный опрос сотрудникам команды"""
    if team_id:
        logging.info(f"Запуск рассылки дейли-опроса для команды {team_id} в группе {time_str}")
    else:
        logging.info(f"Запуск рассылки дейли-опроса для всех команд в группе {time_str}")

    greeting = _get_greeting(time_str)
    # Получаем команду один раз для всех пользователей (используем для timezone и напоминаний)
    team = None
    timezone_str = 'ЕКБ'
    if team_id:
        try:
            team = await db_get_team_by_id(team_id)
            if team:
                if team['timezone'] == 'Europe/Moscow':
                    timezone_str = 'МСК'
        except Exception as e:
            logging.error(f"Ошибка при получении команды {team_id}: {e}")

    # Получаем отображаемое время для сообщения
    if time_str == MORNING_DB_VALUE:
        display_time = TIME_CONSTANTS['morning']['display']
    elif time_str == EVENING_DB_VALUE:
        display_time = TIME_CONSTANTS['evening']['display']
    else:
        display_time = time_str # Если уже в формате HH:MM
    
    # Получаем список ID сотрудников для опроса
    employees_to_ask = await db_get_employees_for_daily(time_str, team_id)
    
    if not employees_to_ask:
        logging.info(f"Нет сотрудников для опроса (time_str={time_str}, team_id={team_id})")
        return

    # Получаем информацию о всех сотрудниках одним запросом (включая данные об отпуске)
    from bot.core.database import db_get_employees_with_vacation_info
    employees_info = await db_get_employees_with_vacation_info(employees_to_ask, team_id)
    
    # Создаем словарь для быстрого доступа к информации о сотрудниках
    employees_dict = {emp['tg_id']: emp for emp in employees_info}

    # Добавляем все приглашения в очередь и ставим напоминания за 20 минут до дедлайна
    queue = get_notification_queue()
    
    for tg_id in employees_to_ask:
        emp = employees_dict.get(tg_id)
        if not emp:
            # Сотрудник не найден в результатах запроса (возможно, нет membership для команды)
            logging.warning(f"Сотрудник {tg_id} не найден в базе данных или не имеет membership для команды {team_id}")
            continue
        if is_on_vacation(emp.get("vacation_start"), emp.get("vacation_end")):
            continue

        notification_params = await _prepare_daily_invitation(tg_id, display_time, greeting, timezone_str, team_id=team_id)
        queue.add(**notification_params)
        
        # Ставим напоминание за 20 минут до дедлайна для каждого пользователя
        if team:
            try:
                team_tz = team.get('timezone') or TIMEZONE
                await _schedule_before_deadline_reminder(tg_id, team_id, team_tz)
            except Exception as e:
                logging.error(f"Не удалось поставить напоминание перед дедлайном для {tg_id}: {e}")


async def send_daily_questions_to_all_teams(time_str: str):
    """Отправка приглашений на ежедневный опрос во все команды"""
    logging.info(f"Запуск рассылки дейли-опроса во все команды для группы {time_str}")

    try:
        from bot.core.database import db_get_all_teams
        teams = await db_get_all_teams()

        for team in teams:
            await send_daily_questions(time_str, team['id'])
            logging.info(f"Приглашения отправлены для команды '{team['name']}' (ID: {team['id']})")

    except Exception as e:
        logging.error(f"Ошибка при отправке приглашений по командам: {e}")


async def send_daily_questions_to_team(team_id: int, time_type: str):
    """Отправка приглашений на ежедневный опрос конкретной команде"""
    try:
        # Определяем ключ времени на основе типа (команда будет получена внутри send_daily_questions)
        time_key = MORNING_DB_VALUE if time_type == 'morning' else EVENING_DB_VALUE
        
        # Отправляем вопросы команде (команда будет получена один раз внутри send_daily_questions)
        await send_daily_questions(time_key, team_id)

    except Exception as e:
        logging.error(f"Ошибка при отправке вопросов команде {team_id}: {e}")


@router.callback_query(F.data.startswith("start_daily_poll"))
async def start_poll(callback: CallbackQuery, state: FSMContext):
    """Начало ежедневного опроса"""
    user_id = callback.from_user.id
    logging.info(f"Пользователь {user_id} начал дейли опрос")

    # извлекаем team_id из callback_data
    parts = callback.data.split('_')
    if len(parts) == 4: # "start", "daily", "poll", "<team_id>"
        try:
            invitation_team_id = int(parts[3])
        except ValueError:
            invitation_team_id = None
    else:
        invitation_team_id = None

    if not invitation_team_id:
        # Fallback: берем из employee
        employee = await db_get_employee(user_id)
        if not employee or not employee['team_id']:
            await callback.answer("❌ Вы не состоите в команде")
            return
        team_id = employee['team_id']
    else:
        team_id = invitation_team_id

    # получаем daily_time из user_team_memberships для этой команды
    
    membership = await db_get_membership(user_id, team_id)
    if not membership:
        await callback.answer("❌ Вы не состоите в этой команде")
        return
    daily_time = membership["daily_time"] if membership else MORNING_DB_VALUE
    logging.info(f"Дейли опрос: daily_time из membership = {daily_time}, team_id = {team_id}")

    # >>> ПЕРЕДАЕМ team_id в get_questions <<<
    questions = await get_questions(daily_time, is_manual_poll=False, user_id=user_id, team_id=team_id)
    if not questions['questions']:
        await callback.answer("❌ Нет вопросов для опроса. Обратитесь к менеджеру для настройки вопросов.")
        return

    # >>> сохраняем team_id в состоянии <<<
    await state.update_data(
        daily_time=daily_time,
        is_manual_poll=False,
        team_id=team_id, # <--- ВОТ ОНО, СВЯТОЕ!
        team_timezone=(await db_get_team_by_id(team_id))['timezone'],
        questions=questions['questions'],
        current_question_index=0,
        answers={},
        user_id=user_id
    )
    # Планируем напоминание через 20 минут после старта
    try:
        team_tz = (await db_get_team_by_id(team_id))['timezone']
        job_id = await _schedule_after_start_reminder(user_id, team_id, team_tz)
        if job_id:
            await state.update_data(reminder_job_ids=[job_id])
    except Exception as e:
        logging.error(f"Не удалось запланировать напоминание: {e}")
    await send_or_edit_message(callback, questions['first'])
    await state.set_state(DailyPoll.waiting_for_answer)
    await callback.answer()

@router.callback_query(F.data.startswith("start_manual_poll"))
async def start_manual_poll(callback: CallbackQuery, state: FSMContext):
    """Начало ручного опроса менеджером"""
    user_id = callback.from_user.id
    logging.info(f"Пользователь {user_id} начал ручной дейли опрос")

    # извлекаем team_id из callback_data 
    parts = callback.data.split('_')
    if len(parts) == 4: # "start", "manual", "poll", "<team_id>"
        try:
            invitation_team_id = int(parts[3])
        except ValueError:
            invitation_team_id = None
    else:
        invitation_team_id = None

    if not invitation_team_id:
        # Fallback: берем из employee
        employee = await db_get_employee(user_id)
        if not employee or not employee['team_id']:
            await callback.answer("❌ Вы не состоите в команде")
            return
        team_id = employee['team_id']
    else:
        team_id = invitation_team_id

    # Получаем daily_time из user_team_memberships для конкретной команды
    membership = await db_get_membership(user_id, team_id)
    if not membership:
        await callback.answer("❌ Вы не состоите в этой команде")
        return
    daily_time = membership["daily_time"] if membership else MORNING_DB_VALUE
    logging.info(f"Ручной опрос: daily_time из membership = {daily_time}, team_id = {team_id}")

    # передаем team_id в get_questions 
    questions = await get_questions(daily_time, is_manual_poll=True, user_id=user_id, team_id=team_id)
    if not questions['questions']:
        await callback.answer("❌ Нет вопросов для опроса. Обратитесь к менеджеру для настройки вопросов.")
        return

    # сохраняем team_id в состоянии
    await state.update_data(
        daily_time=daily_time,
        is_manual_poll=True,
        team_id=team_id, # <--- ВОТ ОНО, СВЯТОЕ!
        team_timezone=(await db_get_team_by_id(team_id))['timezone'],
        questions=questions['questions'],
        current_question_index=0,
        answers={},
        user_id=user_id # Используем ID из callback, он надежнее
    )
    # Планируем напоминание через 20 минут после старта
    try:
        team_tz = (await db_get_team_by_id(team_id))['timezone']
        job_id = await _schedule_after_start_reminder(user_id, team_id, team_tz)
        if job_id:
            await state.update_data(reminder_job_ids=[job_id])
    except Exception as e:
        logging.error(f"Не удалось запланировать напоминание: {e}")
    await send_or_edit_message(callback, questions['first'])
    await state.set_state(DailyPoll.waiting_for_answer)
    await callback.answer()

async def _send_next_question(message: Message, state: FSMContext):
    """Отправка следующего вопроса или завершение опроса"""
    user_data = await state.get_data()
    questions = user_data['questions']
    index = user_data.get('current_question_index', 0) + 1
    if index < len(questions):
        await state.update_data(current_question_index=index)
        await send_or_edit_message(message, questions[index]['text'], disable_web_page_preview=True)
        await state.set_state(DailyPoll.waiting_for_answer)
    else:
        await _complete_poll(message, state)

async def _complete_poll(message: Message, state: FSMContext):
    """Завершение опроса и сохранение отчёта"""
    user_data = await state.get_data()
    user_id = user_data.get('user_id')  # Берем из состояния
    if user_id is None:
        user_id = message.from_user.id  # fallback

    answers = user_data.get('answers', {})
    is_manual_poll = user_data.get('is_manual_poll', False)
    team_id = user_data.get('team_id')

    # Проверка наличия пользователя в БД
    employee = await db_get_employee(user_id)
    if employee is None:
        logging.error(f"Пользователь {user_id} не найден в БД")
        await send_or_edit_message(message, "Ошибка: вы не зарегистрированы.")
        return

    test_enabled = False
    if team_id:
        team = await db_get_team_by_id(team_id)
        try:
            test_enabled = bool(team.get('test_flag'))
        except Exception:
            test_enabled = False
    llm_questions_text = None
    if test_enabled:
        try:
            employee = await db_get_employee(user_id)
            emp_name = employee.get('full_name', '')
            emp_role = employee.get('role', '')
            llm_response = await llm_processor.generate_clarifying_questions_async(
                name=emp_name,
                role=emp_role,
                answers=answers,  # Передаём весь словарь answers_json
                questions=user_data['questions'],  # Вопросы из состояния
                daily_time=employee.get('daily_time', 'morning'),  # Время отчёта сотрудника
                team_id=team_id,
                employee_tg_id=user_id
            )
            if isinstance(llm_response, str) and not llm_response.strip().startswith("❌"):
                llm_questions_text = llm_response.strip()
        except Exception as e:
            logging.error(f"Ошибка LLM при генерации уточняющих вопросов: {e}")
            llm_questions_text = None
    llm_text_clean = llm_questions_text.strip() if llm_questions_text else ""
    if llm_text_clean and llm_text_clean.lower() != "none":
        try:
            await send_or_edit_message(message, f"<b>Уточняющий вопрос по вашему отчёту:</b>\n{llm_text_clean}")
            report_id = await db_add_report(
                tg_id=user_id,
                team_id=user_data['team_id'],
                report_datetime=get_current_time(user_data.get('team_timezone', 'UTC')),
                answers_json=json.dumps(answers, ensure_ascii=False),
                llm_questions=llm_text_clean
            )
            await state.update_data(pending_report_id=report_id)
            await state.set_state(DailyPoll.waiting_for_llm_answer)
            # Отменяем напоминания, т.к. отчёт уже зафиксирован
            user_data = await state.get_data()
            await _cancel_reminders(user_data.get('reminder_job_ids', []))
        except Exception as e:
            logging.error(f"Не удалось отправить уточняющий вопрос: {e}")
    else:
        await db_add_report(
            tg_id=user_id,
            team_id=user_data['team_id'],
            report_datetime=get_current_time(user_data.get('team_timezone', 'UTC')),
            answers_json=json.dumps(answers, ensure_ascii=False),
            llm_questions=None
        )
        poll_type_full = "ручной дейли опрос (запущенный менеджером)" if is_manual_poll else "дейли опрос"
        logging.info(f"Пользователь {user_id} успешно завершил {poll_type_full} и отправил отчет")
        from bot.utils.text_constants import get_report_accepted_message
        await send_or_edit_message(message, get_report_accepted_message(), reply_markup=menu_inline_keyboard())
        # Отменяем поставленные напоминания
        await _cancel_reminders(user_data.get('reminder_job_ids', []))
        await state.clear()

@router.message(DailyPoll.waiting_for_answer)
async def process_answer(message: Message, state: FSMContext):
    """Обработка ответа на текущий вопрос"""
    if not validate_text_or_voice_message(message):
        user_data = await state.get_data()
        questions = user_data['questions']
        index = user_data.get('current_question_index', 0)
        field = questions[index]['field'] if index < len(questions) else 'unknown'
        await send_or_edit_message(message, get_error_message_for_expected_text_or_voice(field))
        return
    user_id = message.from_user.id
    user_data = await state.get_data()
    questions = user_data['questions']
    index = user_data.get('current_question_index', 0)
    field = questions[index]['field'] if index < len(questions) else 'unknown'
    if message.text:
        response_text = message.text
        poll_type = "(ручной опрос)" if user_data.get('is_manual_poll', False) else ""
        logging.info(f"Пользователь {user_id} ответил на вопрос '{field}' {poll_type} (текстовое): {response_text[:50]}...")
        
        current_answers = user_data.get('answers', [])

        # Проверяем, не был ли уже дан ответ на этот field (на случай повтора)
        updated_answers = [ans for ans in current_answers if ans['field'] != field]
        updated_answers.append({"field": field, "answer": response_text})
        await state.update_data(answers=updated_answers)
        
        await _send_next_question(message, state)
    elif message.voice:
        try:
            response_text = await extract_text_from_message(message)
        except Exception as e:
            logging.error(f"Ошибка при извлечении текста из голосового сообщения: {e}")
            from bot.utils.text_constants import get_error_message
            await send_or_edit_message(message, get_error_message("voice"))
            return
        poll_type = "(ручной опрос)" if user_data.get('is_manual_poll', False) else ""
        logging.info(f"Пользователь {user_id} ответил на вопрос '{field}' {poll_type} (голосовое): {response_text[:50]}...")
        await state.update_data(temp_answer=response_text, temp_field=field)
        confirmation_text = f"<b>Распознанный текст:</b>\n<i>{response_text}</i>\n\n<b>Всё верно?</b>"
        await send_or_edit_message(message, confirmation_text, reply_markup=voice_confirmation_keyboard(field))
        await state.set_state(DailyPoll.confirming_answer)

@router.callback_query(F.data.startswith("voice_confirm_"))
async def confirm_answer_voice(callback: CallbackQuery, state: FSMContext):
    """Подтверждение голосового ответа на текущий вопрос"""
    user_data = await state.get_data()
    temp_answer = user_data.get('temp_answer')
    temp_field = user_data.get('temp_field')
    
    if not temp_answer or not temp_field:
        await callback.answer("Ошибка: не найден временный ответ")
        return
    
    user_id = callback.from_user.id
    logging.info(f"Callback user_id: {user_id}, Message user_id: {callback.message.from_user.id}")
    poll_type = "(ручной опрос)" if user_data.get('is_manual_poll', False) else ""
    logging.info(f"Пользователь {user_id} подтвердил голосовой ответ на вопрос '{temp_field}' {poll_type}")
    
    await state.update_data(answers={**user_data.get('answers', {}), temp_field: temp_answer})
    await _send_next_question(callback.message, state)
    await callback.answer()

@router.callback_query(F.data.startswith("voice_retry_"))
async def retry_answer_voice(callback: CallbackQuery, state: FSMContext):
    """Повторный ввод ответа на текущий вопрос"""
    user_data = await state.get_data()
    questions = user_data['questions']
    index = user_data.get('current_question_index', 0)
    field = questions[index]['field'] if index < len(questions) else 'unknown'
    poll_type = "(ручной опрос)" if user_data.get('is_manual_poll', False) else ""
    logging.info(f"Пользователь {callback.from_user.id} решил переответить на вопрос '{field}' {poll_type}")
    await send_or_edit_message(callback, questions[index]['text'], disable_web_page_preview=True)
    await state.set_state(DailyPoll.waiting_for_answer)
    await callback.answer()

@router.callback_query(F.data.startswith("nothing_done"))
async def handle_nothing_done(callback: CallbackQuery, state: FSMContext):
    """Обработка нажатия на кнопку 'Ничего не делал'"""
    user_id = callback.from_user.id
    logging.info(f"Пользователь {user_id} нажал 'Ничего не делал'")
    
    # Извлекаем team_id из callback_data
    parts = callback.data.split('_')
    if len(parts) == 3:  # "nothing", "done", "<team_id>"
        try:
            team_id = int(parts[2])
        except ValueError:
            team_id = None
    else:
        team_id = None
    
    if not team_id:
        # Fallback: берем из employee
        employee = await db_get_employee(user_id)
        if not employee or not employee['team_id']:
            await callback.answer("❌ Вы не состоите в команде")
            return
        team_id = employee['team_id']
    
    # Получаем daily_time из membership
    membership = await db_get_membership(user_id, team_id)
    if not membership:
        await callback.answer("❌ Вы не состоите в этой команде")
        return
    daily_time = membership.get('daily_time', MORNING_DB_VALUE)
    
    # Получаем вопросы команды
    questions = await get_questions(
        daily_time,
        is_manual_poll=False,
        user_id=user_id,
        team_id=team_id
    )
    
    if not questions['questions']:
        await callback.answer("❌ Нет вопросов для опроса. Обратитесь к менеджеру для настройки вопросов.")
        return
    
    # Формируем ответы: первый вопрос - "Ничего не делал", остальные - "-"
    answers = []
    for i, q in enumerate(questions['questions']):
        if i == 0:
            answers.append({"field": q['field'], "answer": "Ничего не делал"})
        else:
            answers.append({"field": q['field'], "answer": "-"})
    
    # Сохраняем отчет в БД
    try:
        team = await db_get_team_by_id(team_id)
        team_timezone = team.get('timezone') if team else TIMEZONE
        
        await db_add_report(
            tg_id=user_id,
            team_id=team_id,
            report_datetime=get_current_time(team_timezone),
            answers_json=json.dumps(answers, ensure_ascii=False),
            llm_questions=None
        )
        
        # Отменяем напоминания, если они были запланированы
        user_data = await state.get_data()
        await _cancel_reminders(user_data.get('reminder_job_ids', []))
        
        # Показываем сообщение о принятии отчета
        from bot.utils.text_constants import get_report_accepted_message
        await send_or_edit_message(callback, get_report_accepted_message(), reply_markup=menu_inline_keyboard())
        await callback.answer("Отчет сохранен")
        
        logging.info(f"Пользователь {user_id} отправил отчет 'Ничего не делал' для команды {team_id}")
    except Exception as e:
        logging.error(f"Ошибка при сохранении отчета 'Ничего не делал' для пользователя {user_id}: {e}")
        await callback.answer("❌ Произошла ошибка при сохранении отчета")

@router.message(DailyPoll.waiting_for_llm_answer)
async def process_llm_answer(message: Message, state: FSMContext):
    """Обработка ответа пользователя на уточняющий вопрос LLM и завершение отчёта"""
    if not validate_text_or_voice_message(message):
        await send_or_edit_message(message, get_error_message_for_expected_text_or_voice('llm'))
        return
    user_data = await state.get_data()
    report_id = user_data.get('pending_report_id')
    if not report_id:
        logging.warning("Не найден pending_report_id для ответа на LLM вопрос")
        from bot.utils.text_constants import get_report_accepted_message
        await send_or_edit_message(message, get_report_accepted_message(), reply_markup=menu_inline_keyboard())
        await state.clear()
        return

    # Получаем текст ответа
    if message.text:
        llm_answer_text = message.text
    else:
        try:
            llm_answer_text = await extract_text_from_message(message)
        except Exception as e:
            logging.error(f"Ошибка при извлечении текста ответа на LLM вопрос: {e}")
            from bot.utils.text_constants import get_error_message
            await send_or_edit_message(message, get_error_message("voice"))
            return
    try:
        await db_update_report_llm_answer(report_id, llm_answer_text)
    except Exception as e:
        logging.error(f"Не удалось сохранить llm_answer для отчёта {report_id}: {e}")
    from bot.utils.text_constants import get_report_accepted_message
    await send_or_edit_message(message, get_report_accepted_message(), reply_markup=menu_inline_keyboard())
    # Отменяем поставленные напоминания
    await _cancel_reminders(user_data.get('reminder_job_ids', []))
    await state.clear()


@router.message(DailyPoll.waiting_for_missing_report_reason)
async def process_missing_report_reason(message: Message, state: FSMContext):
    """Обработка ответа на вопрос 'Почему вы не заполнили отчет?'"""
    if not validate_text_or_voice_message(message):
        await send_or_edit_message(message, get_error_message_for_expected_text_or_voice('missing_report_reason'))
        return
    
    user_id = message.from_user.id
    team_id = (await state.get_data()).get('team_id') or (await db_get_employee(user_id) or {}).get('team_id')
    
    if not team_id:
        await send_or_edit_message(message, "❌ Не удалось определить команду. Попробуйте позже.")
        await state.clear()
        return
    
    try:
        reason_text = message.text or await extract_text_from_message(message)
    except Exception as e:
        logging.error(f"Ошибка при извлечении текста ответа: {e}")
        await send_or_edit_message(message, get_error_message_for_expected_text_or_voice('voice'))
        return
    
    try:
        team = await db_get_team_by_id(team_id)
        tz = pytz.timezone(team.get('timezone') if team else TIMEZONE)
        deadline = _compute_team_next_deadline(team) if team else None
        report_date = deadline.date() if deadline else (datetime.now(tz) - timedelta(days=1)).date()
        
        await db_add_missing_report_reason(user_id, team_id, reason_text, report_date)
        logging.info(f"Причина отсутствия отчета сохранена: user_id={user_id}, team_id={team_id}, date={report_date}")
        
        await send_or_edit_message(
            message,
            "✅ Спасибо за ответ! Ваша причина отсутствия отчета сохранена.",
            reply_markup=menu_inline_keyboard()
        )
    except Exception as e:
        logging.error(f"Ошибка при сохранении причины отсутствия отчета для {user_id}: {e}")
        await send_or_edit_message(message, "❌ Произошла ошибка при сохранении ответа. Попробуйте позже.")
    finally:
        await state.clear()


@router.callback_query(F.data.startswith("reply_missing_report_"))
async def reply_missing_report(callback: CallbackQuery, state: FSMContext):
    """Обработка нажатия на кнопку 'Ответить' для пропущенного отчета"""
    try:
        team_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        team_id = (await db_get_employee(callback.from_user.id) or {}).get('team_id')
        if not team_id:
            await callback.answer("❌ Не удалось определить команду", show_alert=True)
            return
    
    await state.update_data(team_id=team_id)
    await state.set_state(DailyPoll.waiting_for_missing_report_reason)
    
    await send_or_edit_message(
        callback,
        "✍️ <b>Напишите причину отсутствия отчета:</b>\n\n"
        "Вы можете ответить текстом или голосовым сообщением.",
        reply_markup=None
    )
    await callback.answer()


async def check_and_send_missing_report_question(team_id: int):
    """Проверяет неотправленные отчеты после дедлайна и отправляет вопрос 'Почему вы не заполнили отчет?'"""
    try:
        team = await db_get_team_by_id(team_id)
        if not team:
            logging.error(f"Команда с ID {team_id} не найдена для проверки неотправленных отчетов")
            return
        
        # Получаем всех участников команды
        members = await db_get_team_members(team_id)
        if not members:
            logging.info(f"Нет участников в команде {team_id}")
            return
        
        # Получаем множество ID пользователей, которые отправили отчеты
        reports = await db_get_daily_period_reports(team_id=team_id, team=team)
        reported_user_ids = {r.get('tg_id') for r in reports if isinstance(r, dict) and r.get('tg_id')}
        
        question_text = (
            f"❓ <b>Почему вы не заполнили отчет?</b>\n\n"
            f"Команда: <b>{team.get('name', '')}</b>\n\n"
            f"Нажмите кнопку «Ответить», чтобы указать причину отсутствия отчета."
        )
        reply_keyboard = missing_report_reply_keyboard(team_id)
        
        for member in members:
            user_id = member.get('tg_id')
            if (not user_id or user_id in reported_user_ids or 
                (member.get('is_manager') and not member.get('is_participant')) or
                is_on_vacation(member.get('vacation_start'), member.get('vacation_end'))):
                continue
            
            try:
                await send_message_with_retry(
                    user_id, question_text, parse_mode='HTML', 
                    is_report=False, reply_markup=reply_keyboard
                )
                logging.info(f"Вопрос о неотправленном отчете отправлен пользователю {user_id} из команды {team_id}")
            except Exception as e:
                logging.error(f"Не удалось отправить вопрос пользователю {user_id}: {e}")
        
    except Exception as e:
        logging.error(f"Ошибка при проверке неотправленных отчетов для команды {team_id}: {e}", exc_info=True)