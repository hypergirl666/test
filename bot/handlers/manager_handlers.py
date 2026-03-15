import asyncio
import json
import logging
from datetime import datetime, date

import pytz
from aiogram import F
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import (
    EVENING_DB_VALUE,
    EVENING_DISPLAY,
    MAX_MESSAGE_LENGTH,
    MORNING_DB_VALUE,
    MORNING_DISPLAY,
    TIME_CONSTANTS,
    TIMEZONE,
)
from bot.core import ManualPoll, router
from bot.core.database import (
    db_create_invite,
    db_delete_employee,
    db_get_all_employees,
    db_get_daily_period_reports,
    db_get_employee,
    db_get_membership,
    db_get_employees_for_daily,
    db_get_team_by_id,
    db_get_team_by_manager,
    db_get_team_employees,
    db_get_team_invite,
    db_get_team_members,
    db_remove_membership,
    db_delete_employee_reports_for_team,
    db_get_user_memberships,
    db_get_active_sprint,
    db_finish_sprint,
    db_get_reports_for_sprint,
    db_get_sprint_plans_for_team,
    db_update_employee_team,
    db_toggle_invite_status,
)
from bot.handlers.daily_handlers import _calculate_deadline_time
from bot.utils.notification_queue import get_notification_queue
from bot.utils.day_utils import calculate_expected_reports_count
from bot.utils import (
    confirm_daily_keyboard,
    confirm_poll_keyboard,
    is_on_vacation,
    manager_keyboard_with_invite,
    send_message_with_retry,
    send_or_edit_message,
)
from bot.utils.llm_utils import llm_processor
from bot.config import MAX_MESSAGE_LENGTH

from bot.core.database import db_get_membership
from bot.utils.keyboards import manager_participation_keyboard
from bot.core.database import db_update_membership_participation

import pytz
from bot.utils.text_constants import get_access_error_message
from bot.utils.utils import get_current_time


def _get_report_status(report, daily_time, today_date, team_timezone: str | None = None):
    """Получить статус отчета сотрудника"""
    if not report:
        return "❌ Нет отчёта" + (
            " (ожидался сегодня)" if daily_time == MORNING_DB_VALUE else " (ожидался вчера/сегодня)")

    try:
        # Разбираем сохранённое время (в БД хранится в UTC)
        raw_dt = report["report_datetime"]
        has_time = ' ' in raw_dt
        if has_time:
            date_part, time_part = raw_dt.split(' ', 1)
            base_naive = datetime.strptime(raw_dt, '%Y-%m-%d %H:%M:%S')
        else:
            date_part = raw_dt
            time_part = ''
            base_naive = datetime.strptime(raw_dt, '%Y-%m-%d')

        # Базовый TZ: UTC → TZ команды
        base_tz = pytz.UTC
        base_aware = base_tz.localize(base_naive)
        if team_timezone:
            team_tz = pytz.timezone(team_timezone)
            team_dt = base_aware.astimezone(team_tz)
        else:
            team_dt = base_aware

        # Время для отображения
        time_display = f" в {team_dt.strftime('%H:%M')}" if has_time else ""

        # Определяем статус даты относительно today_date (в TZ команды)
        today_dt = datetime.strptime(today_date, '%Y-%m-%d')
        if team_dt.date() == today_dt.date():
            date_status = "сегодня"
        elif (today_dt.date() - team_dt.date()).days == 1:
            date_status = "вчера"
        else:
            date_status = team_dt.strftime('%Y-%m-%d')

        return f"✅ Отправил отчёт ({date_status}{time_display})"
    except Exception:
        return "✅ Отправил отчёт"

def format_report(report, daily_time, today, timezone):
    """Форматирует отчет для отправки с поддержкой нового формата answers_json (список словарей)"""
    from datetime import datetime
    import pytz
    import json

    status = _get_report_status(report, daily_time, today, timezone)
    formatted = f"<b>{report['full_name']}</b> ({report['role']}):\n"

    # Получаем answers_json
    answers = report['answers_json']

    # Если это строка — парсим
    if isinstance(answers, str):
        try:
            answers = json.loads(answers)
        except json.JSONDecodeError:
            answers = []

    # Обрабатываем в зависимости от типа
    if isinstance(answers, list):
        # Новый формат: список словарей [{"field": "...", "answer": "..."}, ...]
        for item in answers:
            if isinstance(item, dict) and 'field' in item and 'answer' in item:
                field_name = item['field'].replace('_', ' ').title()  # yesterday -> Yesterday
                formatted += f"<b>{field_name}:</b> {item['answer']}\n"
    elif isinstance(answers, dict):
        # Старый формат (для обратной совместимости)
        for field, answer in answers.items():
            field_name = field.replace('_', ' ').title()
            formatted += f"<b>{field_name}:</b> {answer}\n"
    else:
        # Неизвестный формат
        formatted += "<i>Ответы не распознаны</i>\n"

    # Добавляем LLM-вопросы, если есть
    if report.get('llm_questions'):
        formatted += f"<b>Уточняющий вопрос:</b> {report['llm_questions']}\n"
    if report.get('llm_answer'):
        formatted += f"<b>Ответ на уточняющий вопрос:</b> {report['llm_answer']}\n"

    formatted += f"{status}\n"
    return status, formatted

def _format_employee_block(emp, report_dict, today_date, team_timezone: str | None, include_llm_fields: bool = False):
    """Форматировать блок информации о сотруднике"""
    tg_id = emp["tg_id"]
    username = emp["username"] or "(нет username)"
    full_name = emp["full_name"]
    role = emp["role"]
    daily_time = emp["daily_time"]

    # Получаем данные времени напрямую из TIME_CONSTANTS
    time_data = TIME_CONSTANTS.get(daily_time, TIME_CONSTANTS['morning'])
    daily_icon = time_data['icon']
    display_time = time_data['display']

    # Проверяем отпуск
    vacation_start = emp["vacation_start"]
    vacation_end = emp["vacation_end"]
    is_on_vacation = False
    if vacation_start and vacation_end:
        try:
            today = get_current_time().date()
            start = datetime.strptime(vacation_start, '%d-%m-%Y').date()
            end = datetime.strptime(vacation_end, '%d-%m-%Y').date()
            is_on_vacation = start <= today <= end
        except Exception:
            pass

    # Получаем данные отчета
    report = report_dict.get(tg_id) if not is_on_vacation else None

    if is_on_vacation:
        yesterday = today_text = problems = llm_question = llm_answer = "—"
        status = "🏖️ В отпуске"
    elif report:
        yesterday = report["yesterday"]
        today_text = report["today"]
        problems = report["problems"]
        # Безопасное получение LLM полей с обработкой возможного отсутствия
        try:
            llm_question = report["llm_questions"] if report["llm_questions"] else "—"
        except (KeyError, IndexError):
            llm_question = "—"
        try:
            llm_answer = report["llm_answer"] if report["llm_answer"] else "—"
        except (KeyError, IndexError):
            llm_answer = "—"
        status = _get_report_status(report, daily_time, today_date, team_timezone)
    else:
        yesterday = today_text = problems = llm_question = llm_answer = "—"
        status = _get_report_status(None, daily_time, today_date, team_timezone)

    # Базовые строки
    lines = [
        f"<b>{full_name}</b> • {role} {daily_icon}",
        f"<code>{username}</code>",
        f"{status}",
        f"<b>Вчера:</b> {yesterday}",
        f"<b>Сегодня:</b> {today_text}",
        f"<b>Трудности:</b> {problems}",
    ]
    # Дополнительные поля LLM — только если включено для команды
    if include_llm_fields:
        lines.append(f"<b>Доп. Вопрос:</b> {llm_question}")
        lines.append(f"<b>Ответ:</b> {llm_answer}")
    # Собираем блок
    return ("\n".join(lines) + "\n\n")


def split_long_message(text, max_length=MAX_MESSAGE_LENGTH):
    """Разбивает длинное сообщение на части по символам"""
    if len(text) <= max_length:
        return [text]

    parts = []
    current_part = ""

    # Разбиваем по строкам, чтобы не разрывать слова
    lines = text.split('\n')

    for line in lines:
        # Если текущая строка помещается в текущую часть
        if len(current_part + line + '\n') <= max_length:
            current_part += line + '\n'
        else:
            # Если текущая часть не пустая, сохраняем её
            if current_part:
                parts.append(current_part.strip())
                current_part = ""

            # Если одна строка слишком длинная, разбиваем её по словам
            if len(line) > max_length:
                words = line.split()
                for word in words:
                    if len(current_part + word + ' ') <= max_length:
                        current_part += word + ' '
                    else:
                        if current_part:
                            parts.append(current_part.strip())
                            current_part = ""
                        current_part = word + ' '
            else:
                current_part = line + '\n'

    # Добавляем последнюю часть
    if current_part:
        parts.append(current_part.strip())

    return parts


def split_report_by_employees(employees, report_dict, team=None, max_length=MAX_MESSAGE_LENGTH):
    """Разбивает отчет на части, сохраняя целостность записей сотрудников"""
    parts = []

    # Формируем заголовок с названием команды и временем отправки отчетов с учетом TZ команды
    date_str = get_current_time().strftime('%d.%m.%Y')
    if team:
        try:
            team_name = team['name']
        except Exception:
            team_name = None
        title = f"📊 <b>Ежедневный отчет команды «{team_name}»</b>" if team_name else "📊 <b>Ежедневный отчет команды</b>"
        header = f"{title}\n{date_str}\n\n"
    else:
        header = f"📊 <b>Ежедневный отчет команды</b>\n{date_str}\n\n"

    current_part = header
    today_date = get_current_time().strftime('%Y-%m-%d')

    # Вычисляем текущую дату в TZ команды для корректного статуса (сегодня/вчера)
    try:
        team_tz_name = team['timezone'] if team else None
    except Exception:
        team_tz_name = None
    if team_tz_name:
        try:
            now_team = datetime.now(pytz.timezone(team_tz_name))
            today_date = now_team.strftime('%Y-%m-%d')
        except Exception:
            today_date = get_current_time().strftime('%Y-%m-%d')
    else:
        today_date = get_current_time().strftime('%Y-%m-%d')

    # Флаг включения LLM-полей на уровне команды
    include_llm_fields = False
    try:
        if team and isinstance(team, dict):
            include_llm_fields = bool(team.get('test_flag'))
    except Exception:
        include_llm_fields = False

    for emp in employees:
        employee_block = _format_employee_block(emp, report_dict, today_date, team_tz_name, include_llm_fields)

        # Проверяем, поместится ли сотрудник в текущую часть
        if len(current_part + employee_block) <= max_length:
            current_part += employee_block
        else:
            # Сохраняем текущую часть и начинаем новую БЕЗ заголовка
            parts.append(current_part.strip())
            current_part = employee_block

    # Добавляем последнюю часть
    if current_part:
        parts.append(current_part.strip())

    return parts


def _ensure_sprint_date(value) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except Exception:
        return datetime.utcnow().date()


def _normalize_answers(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if isinstance(raw, list):
        result = {}
        for item in raw:
            if isinstance(item, dict) and item.get('field'):
                result[item['field']] = item.get('answer', '')
        return result
    if isinstance(raw, dict):
        return raw
    return {}


def _format_sprint_plans_for_llm(plans: list[dict]) -> str:
    grouped = {}
    for plan in plans:
        key = plan.get('employee_tg_id')
        grouped.setdefault(key, {
            'name': plan.get('full_name') or f"ID {plan.get('employee_tg_id')}",
            'role': plan.get('role') or '',
            'items': []
        })
        grouped[key]['items'].append(plan.get('plan_text'))
    if not grouped:
        return "нет планов"
    lines = []
    for info in grouped.values():
        role_suffix = f" ({info['role']})" if info['role'] else ""
        lines.append(f"- {info['name']}{role_suffix}:")
        for item in info['items']:
            lines.append(f"  • {item}")
    return "\n".join(lines)


def _format_sprint_plans_for_humans(plans: list[dict]) -> str:
    """Форматирует планы спринта для отображения в финальном спринтовом отчёте."""
    if not plans:
        return "🏁 Планы участников:\n\n📝 Пока нет планов на спринт"

    grouped: dict = {}
    for plan in plans:
        key = plan.get('employee_tg_id')
        grouped.setdefault(key, {
            'name': plan.get('full_name') or f"ID {plan.get('employee_tg_id')}",
            'role': plan.get('role') or '',
            'items': []
        })
        grouped[key]['items'].append(plan.get('plan_text'))

    lines: list[str] = ["🏁 Планы участников:", ""]
    for info in grouped.values():
        role_suffix = f" ({info['role']})" if info['role'] else ""
        lines.append(f"👤 <b>{info['name']}{role_suffix}:</b>")
        for item in info['items']:
            lines.append(f"  • {item}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _format_sprint_reports_for_llm(reports: list[dict]) -> str:
    if not reports:
        return "нет отчётов"
    lines = []
    for report in reports:
        dt = report.get('report_datetime')
        if isinstance(dt, datetime):
            dt_text = dt.strftime('%d.%m %H:%M')
        else:
            dt_text = str(dt)
        name = report.get('full_name') or f"ID {report.get('employee_tg_id')}"
        role = report.get('role') or ''
        answers = _normalize_answers(report.get('answers_json'))
        yesterday = answers.get('yesterday') or answers.get('Yesterday') or '—'
        today = answers.get('today') or answers.get('Today') or '—'
        problems = answers.get('problems') or answers.get('Problems') or '—'
        lines.append(
            f"[{dt_text}] {name} ({role})\n"
            f"- Yesterday: {yesterday}\n"
            f"- Today: {today}\n"
            f"- Problems: {problems}\n"
        )
    return "\n".join(lines)


def _format_sprint_period(start, end) -> str:
    start_dt = _ensure_sprint_date(start)
    end_dt = _ensure_sprint_date(end)
    return f"{start_dt.strftime('%d.%m.%Y')} — {end_dt.strftime('%d.%m.%Y')}"


async def _send_text_in_parts(text: str, recipient_id: int, *, message_kwargs: dict):
    parts = split_long_message(text, MAX_MESSAGE_LENGTH)
    success = True
    for idx, part in enumerate(parts):
        part_text = part
        if len(parts) > 1:
            part_text = f"{part}\n\n<i>(Часть {idx + 1} из {len(parts)})</i>"
        result = await send_message_with_retry(
            recipient_id,
            part_text,
            parse_mode=message_kwargs.get('parse_mode', ParseMode.HTML),
            is_report=True,
            **{k: v for k, v in message_kwargs.items() if k not in {'parse_mode'}}
        )
        if not result:
            success = False
            logging.error("Не удалось отправить часть %s спринтового отчёта получателю %s", idx + 1, recipient_id)
    return success


async def _send_sprint_summary_if_needed(team: dict, manager_id: int | None, send_to_chat: bool) -> bool:
    if not manager_id:
        return False
    sprint = await db_get_active_sprint(team['id'])
    if not sprint:
        return False
    tz = pytz.timezone(team['timezone'])
    today = datetime.now(tz).date()
    last_report_date = sprint.get('last_report_date')
    if not last_report_date or today != _ensure_sprint_date(last_report_date):
        return False
    reports = await db_get_reports_for_sprint(
        team['id'],
        _ensure_sprint_date(sprint.get('start_date')),
        _ensure_sprint_date(sprint.get('end_date'))
    )
    plans = await db_get_sprint_plans_for_team(sprint['id'])
    period_text = _format_sprint_period(sprint.get('start_date'), sprint.get('end_date'))
    plans_text = _format_sprint_plans_for_llm(plans)
    reports_text = _format_sprint_reports_for_llm(reports)
    plans_block = _format_sprint_plans_for_humans(plans)
    
    expected_reports_count = calculate_expected_reports_count(
        _ensure_sprint_date(sprint.get('start_date')),
        _ensure_sprint_date(sprint.get('end_date')),
        team.get('report_days')
    )

    summary = await llm_processor.sprint_summarizator_async(
        team_name=team['name'],
        period_text=period_text,
        plans_text=plans_text,
        reports_text=reports_text,
        expected_reports_count=expected_reports_count,
        team_id=team['id']
    )
    if not summary or not str(summary).strip():
        logging.warning("LLM не вернул валидный спринтовый отчёт для команды %s", team['id'])
        summary = "❌ Не удалось сформировать аналитический отчёт по спринту."

    header = f"🏁 <b>Спринтовый отчёт команды «{team['name']}»</b>\n{period_text}\n\n"
    # Сначала показываем планы участников, затем аналитическую часть
    if plans_block:
        summary_text = f"{header}{plans_block}\n\n{summary}"
    else:
        summary_text = header + summary
    manager_kwargs = {}
    manager_success = await _send_text_in_parts(summary_text, manager_id, message_kwargs=manager_kwargs)

    chat_success = True
    if send_to_chat and team.get('chat_id'):
        chat_kwargs = {}
        topic = team.get('chat_topic_id')
        if topic:
            try:
                chat_kwargs['message_thread_id'] = int(topic)
            except Exception:
                pass
        chat_success = await _send_text_in_parts(
            summary_text,
            team['chat_id'],
            message_kwargs=chat_kwargs
        )

    # Спринт завершается всегда, когда достигнут последний день отчетов
    await db_finish_sprint(sprint['id'])
    logging.info("Спринт завершен для команды %s", team['id'])

    if manager_success or chat_success:
        logging.info("Спринтовый отчёт отправлен для команды %s (менеджер: %s, чат: %s)",
                    team['id'], manager_success, chat_success)
    else:
        logging.warning("Не удалось отправить спринтовый отчёт для команды %s ни менеджеру, ни в чат",
                       team['id'])

    return manager_success or chat_success


# --- Функционал менеджера ---
async def format_and_send_report(send_to_chat: bool = True, team_id: int = None, manager_id: int = None):
    """Формирование и отправка отчета менеджеру и в канал команды

    Args:
        send_to_chat: Отправлять ли отчет в групповой чат (True по умолчанию для автоматических запусков)
        team_id: ID команды для фильтрации (если None, показываем всех)
        manager_id: ID менеджера для отправки отчета (если None, определяется по team_id)
    """
    from bot.utils.text_constants import TEAM_NOT_FOUND_ERROR, get_no_reports_message
    from bot.config import MAX_MESSAGE_LENGTH
    from datetime import datetime
    import pytz

    action_type = "по расписанию" if send_to_chat else "по запросу менеджера"
    logging.info(f"Формирование и отправка отчета {action_type}.")

    # Определяем менеджера и команду
    team = None
    if team_id is not None:
        team = await db_get_team_by_id(team_id)
        if not team:
            logging.error(f"Команда с ID {team_id} не найдена")
            if manager_id:
                await send_message_with_retry(manager_id, TEAM_NOT_FOUND_ERROR)
            return
        if manager_id is None:
            try:
                members = await db_get_team_members(team_id)
                managers = [m for m in members if m.get('is_manager')]
                manager_id = managers[0]['tg_id'] if managers else None
            except Exception as e:
                logging.error(f"Не удалось определить менеджера для команды {team_id}: {e}")
                manager_id = None
        # Диагностика настроек чата/топика
        try:
            logging.info(
                f"Настройки команды для отправки (team_id={team_id}): chat_id={repr(team['chat_id'])}, chat_topic_id(raw)={repr(team['chat_topic_id'])}"
            )
        except Exception:
            pass

    try:
        # Получаем отчёты за период
        reports = await db_get_daily_period_reports(team_id=team_id, team=team)
        logging.info(f"Найдено отчетов: {len(reports) if reports else 0}")
    except Exception as e:
        logging.error(f"Ошибка при получении отчетов: {e}")
        reports = []

    try:
        # Получаем сотрудников
        if team_id:
            # Список сотрудников команды без менеджеров для отчёта
            members = await db_get_team_members(team_id)
            employees = [m for m in members if not (m.get('is_manager') and not m.get('is_participant'))]
            # employees = members
        else:
            employees = await db_get_all_employees()
        logging.info(f"Найдено сотрудников: {len(employees) if employees else 0}")
        if employees:
            for emp in employees:
                logging.info(f"Сотрудник: {emp['full_name']} (ID: {emp['tg_id']})")
    except Exception as e:
        logging.error(f"Ошибка при получении сотрудников: {e}")
        employees = []

    if not employees:
        team_name = f"«{team['name']}»" if team and team.get('name') else ""
        report_text = f"Нет сотрудников для отправки отчёта команды {team_name}"
        if manager_id:
            manager_result = await send_message_with_retry(manager_id, report_text)
            if not manager_result:
                logging.error(f"Не удалось отправить сообщение об отсутствии сотрудников менеджеру {manager_id}")
        return

    # Формируем словарь сотрудников и отчётов
    emp_dict = {emp["tg_id"]: emp for emp in employees}
    report_dict = {}
    today = datetime.now(pytz.timezone(team['timezone'] if team else TIMEZONE)).date()

    # Обрабатываем отчёты
    if reports:
        for report in reports:
            tg_id = report["tg_id"]
            if tg_id in emp_dict:
                # Если у сотрудника ещё нет отчёта или этот отчёт новее, обновляем
                if tg_id not in report_dict or report["report_datetime"] > report_dict[tg_id]["report_datetime"]:
                    report_dict[tg_id] = report

    # Проверяем, есть ли отчеты за период
    if not report_dict:
        # Если сотрудники есть, но нет отчетов за период
        team_name = f"«{team['name']}»" if team and team.get('name') else ""
        report_text = f"Нет новых отчётов от сотрудников команды {team_name}"
        if manager_id:
            manager_result = await send_message_with_retry(manager_id, report_text)
            if not manager_result:
                logging.error(f"Не удалось отправить сообщение об отсутствии отчетов менеджеру {manager_id}")
        return

    # Формируем отчёты для каждого сотрудника
    report_parts = []
    current_part = ""
    part_count = 0
    today = datetime.now(pytz.timezone(team['timezone'] if team else TIMEZONE)).date()

    for emp in employees:
        tg_id = emp["tg_id"]
        report = report_dict.get(tg_id)
        # Помечаем отпускников
        try:
            on_vacation = is_on_vacation(emp.get('vacation_start'), emp.get('vacation_end'))
        except Exception:
            on_vacation = False
        if on_vacation:
            formatted = f"<b>{emp['full_name']}</b> ({emp['role']}):\n🏖️ В отпуске\n"
        elif report:
            status, formatted = format_report(report, emp['daily_time'], today, team['timezone'] if team else TIMEZONE)
        else:
            # Если отчёта нет, формируем заглушку
            status = _get_report_status(None, emp['daily_time'], today, team['timezone'] if team else TIMEZONE)
            formatted = f"<b>{emp['full_name']}</b> ({emp['role']}):\n{status}\n"

        # Проверяем, не превышает ли текущая часть лимит
        if len(current_part) + len(formatted) + 2 > MAX_MESSAGE_LENGTH:
            report_parts.append(current_part)
            current_part = formatted
            part_count += 1
        else:
            if current_part:
                current_part += "\n\n"
            current_part += formatted

    # Добавляем последнюю часть, если она есть
    if current_part:
        report_parts.append(current_part)

    # Если нет отчётов
    if not report_parts:
        if manager_id:
            queue = get_notification_queue()
            queue.add(manager_id, get_no_reports_message())
        return

    # Отправляем отчёт через очередь
    try:
        queue = get_notification_queue()
        
        # Отправка менеджеру - все части подряд
        for i, part in enumerate(report_parts):
            if i == 0:
                # Первая часть - полный отчёт
                queue.add(
                    manager_id,
                    part,
                    parse_mode=ParseMode.HTML,
                    is_report=True
                )
            else:
                # Последующие части - продолжение
                continuation_text = f"<i>(продолжение {i + 1}/{len(report_parts)})</i>\n\n{part}"
                queue.add(
                    manager_id,
                    continuation_text,
                    parse_mode=ParseMode.HTML,
                    is_report=True
                )

        # Проверяем, является ли сегодня последним днём спринта
        is_last_day_of_sprint = False
        if team and team.get('sprint_enabled'):
            sprint = await db_get_active_sprint(team['id'])
            if sprint:
                tz = pytz.timezone(team['timezone'])
                today = datetime.now(tz).date()
                last_report_date = sprint.get('last_report_date')
                if last_report_date and today == _ensure_sprint_date(last_report_date):
                    is_last_day_of_sprint = True

        sprint_summary_sent = False
        if team and team.get('sprint_enabled'):
            sprint_summary_sent = await _send_sprint_summary_if_needed(team, manager_id, send_to_chat)

        # Отправка в чат команды, если нужно
        if send_to_chat and team and team['chat_id']:
            topic_id = None
            raw_topic = team['chat_topic_id']
            logging.info(f"chat_topic_id из БД: {repr(raw_topic)}")
            if raw_topic is not None:
                try:
                    topic_id = int(raw_topic)
                except Exception as conv_err:
                    logging.error(f"Не удалось преобразовать chat_topic_id в int: raw={repr(raw_topic)}, error={conv_err}")

            for i, part in enumerate(report_parts):
                message_kwargs = {
                    'text': part if i == 0 else f"<i>(продолжение {i + 1}/{len(report_parts)})</i>\n\n{part}",
                    'parse_mode': ParseMode.HTML,
                    'is_report': True
                }
                if topic_id is not None:
                    message_kwargs['message_thread_id'] = topic_id

                queue.add(team['chat_id'], **message_kwargs)

            # Отправляем саммари отчёта (кроме последнего дня спринта)
            if not sprint_summary_sent and not is_last_day_of_sprint:
                await send_report_summary(manager_id, team, report_parts, send_to_chat)

            logging.info(
                f"Отчет {'разбит на ' + str(len(report_parts)) + ' частей и ' if len(report_parts) > 1 else ''}отправлен менеджеру {manager_id} и в чат команды {team['chat_id']}"
            )
        else:
            # Отправка только менеджеру
            chat_info = f" (команда без чата)" if team and not team['chat_id'] else ""
            if not sprint_summary_sent and not is_last_day_of_sprint:
                await send_report_summary(manager_id, team, report_parts, send_to_chat=False)
            logging.info(
                f"Отчет {'разбит на ' + str(len(report_parts)) + ' частей и ' if len(report_parts) > 1 else ''}отправлен менеджеру {manager_id}{chat_info}"
            )

    except Exception as e:
        logging.error(f"Не удалось отправить отчет: {e}")
        error_result = await send_message_with_retry(manager_id, "❌ Не удалось отправить отчет.")
        if not error_result:
            logging.error(f"Не удалось отправить сообщение об ошибке менеджеру {manager_id}")

        if send_to_chat and team and team['chat_id']:
            error_kwargs = {
                'chat_id': team['chat_id'],
                'text': "❌ Не удалось отправить отчет.",
                'parse_mode': ParseMode.HTML
            }
            if team['chat_topic_id']:
                try:
                    error_kwargs['message_thread_id'] = int(team['chat_topic_id'])
                except Exception:
                    pass
            error_chat_result = await send_message_with_retry(**error_kwargs)
            if not error_chat_result:
                logging.error(f"Не удалось отправить сообщение об ошибке в чат команды {team['chat_id']}")

async def send_report_summary(manager_id: int, team: dict, report_parts: list, send_to_chat: bool = True):
    """Создает и отправляет саммари отчёта на основе полного отчёта"""
    try:
        # Объединяем все части отчёта в один текст
        full_report = "\n\n".join(report_parts)

        # Получаем questions_json для контекста
        questions = team.get('questions_json', []) if team else []

        # Создаем саммари с помощью LLM (пробрасываем team_id и questions для учёта токенов и контекста)
        team_id_for_llm = team['id'] if team else None
        team_name_for_log = team.get('name', 'неизвестная') if team else 'неизвестная'
        logging.info(f"Создание саммари для команды {team_name_for_log} (ID: {team_id_for_llm}), длина отчёта: {len(full_report)} символов")
        summary = await llm_processor.daily_summarizator_async(full_report, team_id=team_id_for_llm, questions=questions)
        
        # Если саммари не создано (ошибка), отправляем простое сообщение без деталей
        if not summary:
            logging.error(f"Не удалось создать саммари для команды {team_name_for_log} (ID: {team_id_for_llm}). Длина отчёта: {len(full_report)} символов, количество частей: {len(report_parts)}")
            error_msg = "❌ Не удалось сформировать отчёт по статусу."
            await send_message_with_retry(manager_id, error_msg)
            return

        # Формируем заголовок для саммари
        team_name = team['name'] if team else "команды"
        summary_header = f"📋 <b>Отчёт по статусу команды ({team_name})</b>\n{get_current_time().strftime('%d.%m.%Y')}\n\n"

        # Добавляем заголовок к саммари
        summary_with_header = summary_header + summary

        # Разбиваем саммари на части, если оно слишком длинное
        summary_parts = split_long_message(summary_with_header, MAX_MESSAGE_LENGTH)

        # Отправляем саммари менеджеру по частям
        manager_success = True
        for i, part in enumerate(summary_parts):
            part_text = part
            if len(summary_parts) > 1:
                part_text = f"{part}\n\n<i>(Часть {i + 1} из {len(summary_parts)})</i>"

            manager_result = await send_message_with_retry(
                manager_id,
                part_text,
                parse_mode=ParseMode.HTML,
                is_report=True
            )

            if not manager_result:
                logging.error(f"Не удалось отправить часть {i + 1} саммари менеджеру {manager_id}")
                manager_success = False

        if not manager_success:
            logging.error(f"Не удалось отправить саммари менеджеру {manager_id}")

        # Отправляем саммари в чат команды, если это автоматический запуск и у команды есть чат
        if send_to_chat and team and team['chat_id']:
            topic_id = None
            raw_topic = team['chat_topic_id']
            logging.info(f"chat_topic_id из БД: {repr(raw_topic)}")
            if raw_topic is not None:
                try:
                    topic_id = int(raw_topic)
                except Exception as conv_err:
                    logging.error(f"Не удалось преобразовать chat_topic_id в int: raw={repr(raw_topic)}, error={conv_err}")
            chat_summary_header = f"📋 <b>Отчёт по статусу команды «{team_name}»</b>\n{get_current_time().strftime('%d.%m.%Y')}\n\n"
            chat_summary = chat_summary_header + summary

            # Разбиваем саммари для чата на части
            chat_summary_parts = split_long_message(chat_summary, MAX_MESSAGE_LENGTH)

            chat_success = True
            for i, part in enumerate(chat_summary_parts):
                part_text = part
                if len(chat_summary_parts) > 1:
                    part_text = f"{part}\n\n<i>(Часть {i + 1} из {len(chat_summary_parts)})</i>"

                message_kwargs = {
                    'chat_id': team['chat_id'],
                    'text': part_text,
                    'parse_mode': ParseMode.HTML,
                    'is_report': True
                }

                if topic_id is not None:
                    message_kwargs['message_thread_id'] = topic_id
                chat_result = await send_message_with_retry(**message_kwargs)
                if not chat_result:
                    place = f"топик {team['chat_id']}/{topic_id}" if topic_id is not None else f"чат {team['chat_id']}"
                    logging.error(f"Не удалось отправить часть {i + 1} саммари в {place}")
                    chat_success = False

            if manager_success and chat_success:
                logging.info(
                    f"Саммари отчёта отправлено менеджеру {manager_id} и в чат команды {team['chat_id']} ({len(summary_parts)} частей)")
            elif manager_success:
                logging.info(f"Саммари отчёта отправлено менеджеру {manager_id} ({len(summary_parts)} частей)")
            else:
                logging.error(f"Ошибки при отправке саммари")
        else:
            if manager_success:
                logging.info(f"Саммари отчёта отправлено менеджеру {manager_id} ({len(summary_parts)} частей)")
            else:
                logging.error(f"Ошибки при отправке саммари менеджеру")

    except Exception as e:
        logging.error(f"Ошибка при создании и отправке саммари: {e}")
        # Отправляем сообщение об ошибке менеджеру
        error_msg = "❌ Не удалось создать саммари отчёта."
        await send_message_with_retry(manager_id, error_msg)


@router.callback_query(F.data == "view_employees")
async def view_employees_callback(callback: CallbackQuery):
    """Просмотр списка всех сотрудников"""
    user_id = callback.from_user.id

    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("просмотра сотрудников"))
        return

    logging.info(f"Менеджер {user_id} команды '{team['name']}' просматривает список сотрудников")

    employees = await db_get_team_members(team['id'])
    # Исключаем только менеджеров, которые НЕ участвуют в опросах
    employees = [
    emp for emp in employees 
    if not (emp.get('is_manager') and not emp.get('is_participant', False))
    ]
    if not employees:
        from bot.utils.text_constants import get_no_data_message
        await send_or_edit_message(callback, get_no_data_message("команде", team['name']))
        await callback.answer()
        return

    for emp in employees:
        tg_id = emp["tg_id"]
        username = emp["username"]
        full_name = emp["full_name"]
        role = emp["role"]
        daily_time = emp["daily_time"]

        # Отображаем время, адаптированное под настройки команды
        if daily_time == MORNING_DB_VALUE:
            display_time = team['morning_time']
        else:
            display_time = team['evening_time']

        text = (
            f"<b>{full_name}</b> ({role})\n"
            f"Username: {f'@{username}' if username else 'N/A'}\n"
            f"ID: <code>{tg_id}</code>\n"
            f"Время дейли: {display_time}"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Удалить", callback_data=f"delete_employee_{tg_id}")]
        ])
        await callback.message.answer(text, reply_markup=keyboard)
        await asyncio.sleep(0.3)

    await callback.answer()


@router.callback_query(F.data.startswith("delete_employee_"))
async def delete_employee_callback(callback: CallbackQuery):
    """Запрос подтверждения на удаление сотрудника"""
    user_id = callback.from_user.id

    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        logging.warning(f"Попытка удаления сотрудника: пользователь {user_id} не является менеджером")
        await callback.answer(get_access_error_message("удаления сотрудников"))
        return

    try:
        tg_id_to_delete = int(callback.data.split("_")[-1])
    except (ValueError, IndexError) as e:
        logging.error(f"Ошибка парсинга tg_id из callback.data: {callback.data}, ошибка: {e}")
        await callback.answer("❌ Некорректный запрос")
        return

    logging.info(
        f"Менеджер {user_id} команды '{team['name']}' (ID: {team['id']}) пытается удалить сотрудника {tg_id_to_delete}")

    # Проверяем, имеет ли сотрудник членство в команде менеджера через user_team_memberships
    membership = await db_get_membership(tg_id_to_delete, team['id'])

    # Дополнительная проверка через employee для логирования
    employee = await db_get_employee(tg_id_to_delete)
    if employee:
        logging.info(f"Сотрудник {tg_id_to_delete} найден в employees, team_id: {employee.get('team_id')}")
    else:
        logging.warning(f"Сотрудник {tg_id_to_delete} не найден в таблице employees")

    if not membership:
        logging.warning(f"Сотрудник {tg_id_to_delete} не имеет членства в команде {team['id']} (менеджер: {user_id})")
        await callback.answer("❌ Сотрудник не найден в вашей команде")
        return

    logging.info(
        f"Членство сотрудника {tg_id_to_delete} в команде {team['id']} подтверждено. Показываем подтверждение удаления.")

    # Показываем подтверждение удаления
    confirm_text = (
        "⚠️ Вы уверены, что хотите удалить сотрудника из команды?\n\n"
        "Вместе с сотрудником удалятся все его отчёты по этой команде."
    )
    from bot.utils.keyboards import confirm_delete_employee_keyboard
    await send_or_edit_message(callback, confirm_text, reply_markup=confirm_delete_employee_keyboard(tg_id_to_delete))
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_delete_employee_yes_"))
async def confirm_delete_employee_yes(callback: CallbackQuery):
    """Подтверждение удаления сотрудника"""
    user_id = callback.from_user.id

    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        logging.warning(f"Подтверждение удаления: пользователь {user_id} не является менеджером")
        await callback.answer(get_access_error_message("удаления сотрудников"))
        return

    try:
        tg_id_to_delete = int(callback.data.split("_")[-1])
    except (ValueError, IndexError) as e:
        logging.error(f"Ошибка парсинга tg_id из callback.data: {callback.data}, ошибка: {e}")
        await callback.answer("❌ Некорректный запрос")
        return

    logging.info(
        f"Менеджер {user_id} подтвердил удаление сотрудника {tg_id_to_delete} из команды '{team['name']}' (ID: {team['id']})")

    # Проверяем, имеет ли сотрудник членство в команде менеджера через user_team_memberships
    membership = await db_get_membership(tg_id_to_delete, team['id'])

    if not membership:
        logging.warning(f"Попытка удаления: сотрудник {tg_id_to_delete} не имеет членства в команде {team['id']}")
        await callback.answer("❌ Сотрудник не найден в вашей команде")
        return

    logging.info(f"Начинаем удаление: сотрудник {tg_id_to_delete}, команда {team['id']}")

    # Удаляем только членство из этой команды и отчёты этой команды
    try:
        # Удаляем членство
        await db_remove_membership(tg_id_to_delete, team['id'])
        logging.info(f"Членство сотрудника {tg_id_to_delete} в команде {team['id']} удалено")

        # Удаляем отчёты только этой команды
        await db_delete_employee_reports_for_team(tg_id_to_delete, team['id'])
        logging.info(f"Отчёты сотрудника {tg_id_to_delete} для команды {team['id']} удалены")

        # Проверяем, нужно ли обновить employees.team_id
        employee = await db_get_employee(tg_id_to_delete)
        if employee and employee.get('team_id') == team['id']:
            # Если текущая команда у сотрудника совпадает с удаляемой - обнуляем её
            # Проверяем, есть ли другие членства
            other_memberships = await db_get_user_memberships(tg_id_to_delete)
            if other_memberships:
                # Если есть другие команды, устанавливаем первую из них
                new_team_id = other_memberships[0]['team_id']
                await db_update_employee_team(tg_id_to_delete, new_team_id)
                logging.info(f"Обновлён employees.team_id для {tg_id_to_delete} на {new_team_id}")
            else:
                # Если других команд нет - обнуляем
                await db_update_employee_team(tg_id_to_delete, None)
                logging.info(f"Обнулён employees.team_id для {tg_id_to_delete} (нет других команд)")

        from bot.utils.text_constants import get_data_updated_message
        await send_or_edit_message(callback, get_data_updated_message())
        logging.warning(
            f"✅ Менеджер {user_id} команды '{team['name']}' успешно удалил сотрудника {tg_id_to_delete} из команды и его отчёты по этой команде")
        await callback.answer("Сотрудник удалён из команды и его отчёты удалены")

    except Exception as e:
        logging.error(f"❌ Ошибка при удалении сотрудника {tg_id_to_delete} из команды {team['id']}: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка при удалении сотрудника")


@router.callback_query(F.data == "view_report")
async def manual_get_report_callback(callback: CallbackQuery):
    """Ручной запрос отчета"""
    user_id = callback.from_user.id

    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        from bot.utils.text_constants import get_access_error_message
        await callback.answer(get_access_error_message("просмотра отчетов"))
        return

    logging.info(f"Менеджер {user_id} команды '{team['name']}' запросил ручное формирование отчета")

    from bot.utils.text_constants import get_processing_message
    await send_or_edit_message(callback, get_processing_message("Формирую отчет команды", team['name']))

    # Проверяем, есть ли сотрудники в команде перед формированием отчета
    employees = await db_get_team_members(team['id'])
    if not employees:
        await send_or_edit_message(callback,
                                  f"❌ В команде '{team['name']}' нет сотрудников. Отчет не может быть сформирован.")
        await callback.answer()
        return

    await format_and_send_report(send_to_chat=False, team_id=team['id'], manager_id=user_id)

    # Уведомляем менеджера о том, что отчет не был отправлен в канал
    await send_or_edit_message(callback, "✅ Отчет сформирован и отправлен только вам (не отправлен в групповой чат)")


@router.callback_query(F.data == "launch_survey")
async def manual_start_poll_callback(callback: CallbackQuery):
    """Ручной запуск опроса"""
    user_id = callback.from_user.id

    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("запуска опроса"))
        return

    logging.info(f"Менеджер {user_id} команды '{team['name']}' запустил ручной опрос сотрудников")

    # Используем время команды для отображения
    morning_time = team['morning_time']
    evening_time = team['evening_time']

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Группу {morning_time}", callback_data=f"manual_poll_{MORNING_DB_VALUE}")],
        [InlineKeyboardButton(text=f"Группу {evening_time}", callback_data=f"manual_poll_{EVENING_DB_VALUE}")],
        [InlineKeyboardButton(text="Всех сотрудников команды", callback_data="manual_poll_all")],
    ])
    from bot.utils.text_constants import get_survey_group_selection_message
    await send_or_edit_message(callback, get_survey_group_selection_message(team['name']),
                               reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("manual_poll_"))
async def manual_poll_callback(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора группы для ручного опроса"""
    user_id = callback.from_user.id

    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        await send_or_edit_message(
            callback.message,
            get_access_error_message("запуска опроса")
        )
        await callback.answer()
        return

    group = callback.data.split('_')[-1]
    logging.info(f"Менеджер {user_id} команды '{team['name']}' выбрал для ручного опроса: {group}")

    if group == "all":
        employees = await db_get_team_members(team['id'])
        # Исключаем сотрудников, находящихся в отпуске
        employees = [emp for emp in employees if not is_on_vacation(emp.get('vacation_start'), emp.get('vacation_end'))]
        employee_ids = [emp["tg_id"] for emp in employees]
        group_name = f"всех сотрудников команды '{team['name']}'"
    else:
        # Получаем всех сотрудников команды и фильтруем по времени, исключая отпускников
        employees = await db_get_team_members(team['id'])
        employees = [
            emp for emp in employees
            if emp["daily_time"] == group and not is_on_vacation(emp.get('vacation_start'), emp.get('vacation_end'))
        ]
        employee_ids = [emp["tg_id"] for emp in employees]
        group_name = f"группы {group} команды '{team['name']}'"

    if not employee_ids:
        from bot.utils.text_constants import get_no_data_message
        await send_or_edit_message(callback, get_no_data_message("группе", group))
        await callback.answer()
        return

    # Сохраняем данные для подтверждения
    await state.update_data(group=group, employee_ids=employee_ids, group_name=group_name, team_id=team['id'])

    await send_or_edit_message(callback,
                               f"⚠️ Подтвердите отправку опроса\n\n"
                               f"Будет отправлено приглашение {len(employee_ids)} сотрудникам из {group_name}.\n\n"
                               f"Продолжить?",
                               reply_markup=confirm_poll_keyboard()
                               )
    await state.set_state(ManualPoll.waiting_for_confirmation)
    await callback.answer()


@router.callback_query(ManualPoll.waiting_for_confirmation, F.data == "confirm_poll_yes")
async def confirm_poll_yes(callback: CallbackQuery, state: FSMContext):
    """Подтверждение отправки ручного опроса"""
    user_id = callback.from_user.id

    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        await send_or_edit_message(
            callback.message,
            get_access_error_message("отправки опроса")
        )
        await callback.answer()
        return

    user_data = await state.get_data()
    group = user_data.get('group')
    employee_ids = user_data.get('employee_ids')
    group_name = user_data.get('group_name')
    team_id = user_data.get('team_id')

    # Дополнительная проверка, что команда соответствует
    if team_id != team['id']:
        await callback.answer("❌ Ошибка: несоответствие команды")
        return

    logging.info(f"Менеджер {user_id} команды '{team['name']}' подтвердил ручной опрос для {group_name}")

    from bot.utils.text_constants import get_processing_message
    await send_or_edit_message(callback, get_processing_message("🚀 Отправляю опрос для", group_name))

    count = 0
    # Получаем название команды один раз для текста приглашения
    team_name = team['name'] if team else "команда"
    for tg_id in employee_ids:
        # Получаем данные сотрудника для расчета времени
        employee = await db_get_employee(tg_id)
        daily_time = employee["daily_time"] if employee else TIME_CONSTANTS['morning']['db_value']

        # Получаем время отчетов из команды
        team_report_time = team['report_time'] if team else '10:00'

        # Рассчитываем время до дедлайна с учетом времени отчетов команды
        hours, minutes, deadline = _calculate_deadline_time(daily_time, team_report_time)
        deadline_str = deadline.strftime("%H:%M")

        message_text = (
            f"👋 Менеджер запустил внеплановый дейли-опрос!\n\n"
            f"Команда: <b>{team_name}</b>\n\n"
            f"Готовы ответить?"
        )

        # Добавляем в очередь
        queue = get_notification_queue()
        queue.add(
            tg_id,
            message_text,
            reply_markup=confirm_daily_keyboard(manual_poll=True, team_id=team_id)
        )
        count += 1

    logging.info(f"Ручной опрос отправлен {count} сотрудникам из {group_name}")
    from bot.utils.text_constants import get_survey_sent_message
    await send_or_edit_message(callback, get_survey_sent_message(count, group_name))
    await state.clear()
    await callback.answer()


@router.callback_query(ManualPoll.waiting_for_confirmation, F.data == "confirm_poll_no")
async def confirm_poll_no(callback: CallbackQuery, state: FSMContext):
    """Отмена ручного опроса"""
    user_id = callback.from_user.id

    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        await send_or_edit_message(
            callback.message,
            get_access_error_message("отмены опроса")
        )
        await callback.answer()
        return

    logging.info(f"Менеджер {user_id} команды '{team['name']}' отменил ручной опрос")

    await send_or_edit_message(callback, "❌ Отправка опроса отменена.")
    await state.clear()
    await callback.answer()


# Обработчик кнопки "Добавить сотрудников"
@router.callback_query(F.data == "add_employees")
async def handle_add_employees_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки добавления сотрудников"""
    user_id = callback.from_user.id

    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("добавления сотрудников"))
        return

    # Получаем приглашение команды
    invite = await db_get_team_invite(team['id'])

    if not invite:
        # Если приглашения нет, создаем новое
        await create_new_invite(callback.message, team)
    else:
        # Показываем меню управления приглашением
        await show_invite_menu(callback.message, team, invite)

    await callback.answer()


# --- Вспомогательные функции для работы с приглашениями ---
async def create_new_invite(message: Message, team):
    """Создание нового приглашения"""
    import secrets

    invite_code = secrets.token_urlsafe(16)
    await db_create_invite(team['id'], invite_code)

    bot_username = (await message.bot.me()).username
    invite_link = f"https://t.me/{bot_username}?start={invite_code}"

    from bot.utils.text_constants import get_invite_created_message
    await send_or_edit_message(
        message,
        get_invite_created_message(team['name'], invite_link),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Деактивировать ссылку", callback_data="deactivate_invite")]
        ])
    )


async def show_invite_menu(message: Message, team, invite):
    """Показ меню управления приглашением"""
    bot_username = (await message.bot.me()).username

    status = "✅ Активна" if invite['is_active'] else "❌ Неактивна"
    # Корректное форматирование даты создания приглашения для str и datetime
    raw_created = invite.get('created_at') if isinstance(invite, dict) else None
    if not raw_created:
        created_date = "Неизвестно"
    else:
        try:
            if isinstance(raw_created, datetime):
                created_date = raw_created.strftime('%d.%m.%Y')
            else:
                # Пытаемся распарсить ISO/строку, иначе показываем как есть
                created_date = datetime.fromisoformat(str(raw_created)).strftime('%d.%m.%Y')
        except Exception:
            # Фоллбек: отрезаем дату до пробела или отображаем исходную строку
            s = str(raw_created)
            created_date = s.split(' ')[0] if ' ' in s else s
    invite_link = f"https://t.me/{bot_username}?start={invite['invite_code']}"

    from bot.utils.text_constants import get_invite_menu_message
    text = get_invite_menu_message(team['name'], invite_link, status, created_date)

    buttons = []
    if invite['is_active']:
        buttons.append([InlineKeyboardButton(text="❌ Деактивировать ссылку", callback_data="deactivate_invite")])
    else:
        buttons.append([InlineKeyboardButton(text="✅ Активировать ссылку", callback_data="activate_invite")])
    buttons.append([InlineKeyboardButton(text="🔄 Создать новую ссылку", callback_data="create_new_invite")])

    await send_or_edit_message(
        message,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


# --- Обработчики для управления приглашениями ---


@router.callback_query(F.data == "create_new_invite")
async def create_new_invite_callback(callback: CallbackQuery):
    """Обработчик создания новой ссылки"""
    user_id = callback.from_user.id

    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("создания приглашений"))
        return

    await create_new_invite(callback.message, team)
    await callback.answer()


@router.callback_query(F.data == "activate_invite")
async def activate_invite_callback(callback: CallbackQuery):
    """Обработчик активации приглашения"""
    user_id = callback.from_user.id

    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("управления приглашениями"))
        return

    # Активируем приглашение
    await db_toggle_invite_status(team['id'], True)

    await callback.answer("✅ Приглашение активировано!")

    # Обновляем сообщение с новой ссылкой
    invite = await db_get_team_invite(team['id'])
    await show_invite_menu(callback.message, team, invite)


@router.callback_query(F.data == "deactivate_invite")
async def deactivate_invite_callback(callback: CallbackQuery):
    """Обработчик деактивации приглашения"""
    user_id = callback.from_user.id

    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("управления приглашениями"))
        return

    # Деактивируем приглашение
    await db_toggle_invite_status(team['id'], False)

    await callback.answer("❌ Приглашение деактивировано!")

    # Обновляем сообщение с новой ссылкой
    invite = await db_get_team_invite(team['id'])
    await show_invite_menu(callback.message, team, invite)



@router.callback_query(F.data == "manager_participation_settings")
async def manager_participation_settings(callback: CallbackQuery):
    """Показывает меню выбора режима участия менеджера."""
    user_id = callback.from_user.id
    
    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer("❌ У вас нет прав менеджера.")
        return
    
    # Получаем текущее состояние из таблицы user_team_memberships
    membership = await db_get_membership(user_id, team['id'])
    if not membership:
        await callback.answer("❌ Ошибка: вы не состоите в этой команде как менеджер.")
        return
        
    is_participant = bool(membership.get('is_participant')) if membership else False
    
    await send_or_edit_message(
        callback,
        "Ваш режим работы:",
        reply_markup=manager_participation_keyboard(is_participant)
    )
    await callback.answer()



@router.callback_query(F.data == "toggle_manager_participation")
async def toggle_manager_participation(callback: CallbackQuery):
    """Переключает статус участия менеджера в опросах."""
    user_id = callback.from_user.id
    
    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer("❌ У вас нет прав менеджера.")
        return

    # Переключаем статус в БД
    new_state = await db_update_membership_participation(user_id, team['id'])
    
    if new_state is not None:
        status_text = "теперь участвуете" if new_state else "больше не участвуете"
        await callback.answer(f"✅ Ваш статус обновлён: вы {status_text} в опросах.")
        # После переключения возвращаем пользователя в то же меню, чтобы он увидел обновлённый статус
        await manager_participation_settings(callback)
    else:
        await callback.answer("❌ Не удалось обновить статус. Попробуйте позже.")