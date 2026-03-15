import json
import logging
from datetime import datetime, date, timedelta

import pytz
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import MAX_MESSAGE_LENGTH
from bot.core import SprintPlan
from bot.utils.day_utils import calculate_last_report_date, calculate_sprint_end_date, calculate_expected_reports_count
from bot.utils.llm_utils import llm_processor
from bot.core.database import (
    db_add_sprint_plan_entry,
    db_create_sprint,
    db_finish_sprint,
    db_get_active_sprint,
    db_get_employee,
    db_get_membership,
    db_get_reports_for_sprint,
    db_get_sprint_by_id,
    db_get_sprint_plans_for_user,
    db_get_sprint_plans_for_team,
    db_get_team_by_id,
    db_get_team_by_manager,
    db_get_team_members,
    db_mark_sprint_plans_requested,
    db_team_has_active_sprint,
    db_update_sprint_dates,
    db_update_sprint_settings,
)
from bot.utils import send_or_edit_message
from bot.utils.keyboards import (
    cancel_keyboard,
    sprint_duration_keyboard,
    sprint_menu_keyboard,
    sprint_my_plans_keyboard,
)
from bot.utils.scheduler_jobs import handle_sprint_cycle, _send_sprint_plan_requests
from bot.utils.scheduler_manager import scheduler_manager
from bot.utils.text_constants import get_access_error_message

router = Router()


def split_long_message(text, max_length=MAX_MESSAGE_LENGTH):
    """Разбивает длинное сообщение на части по символам"""
    if len(text) <= max_length:
        return [text]

    parts = []
    while text:
        if len(text) <= max_length:
            parts.append(text)
            break

        # Находим ближайший перенос строки или обрезаем по длине
        cut_pos = text.rfind('\n', 0, max_length)
        if cut_pos == -1:
            cut_pos = max_length

        part = text[:cut_pos]
        parts.append(part)
        text = text[cut_pos:].lstrip('\n')

    return parts


def _ensure_sprint_date(value) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _ensure_date(value) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _format_period(start: date | str | None, end: date | str | None) -> str:
    if not start or not end:
        return "—"
    start_dt = _ensure_date(start)
    end_dt = _ensure_date(end)
    return f"{start_dt.strftime('%d.%m')} — {end_dt.strftime('%d.%m')}"


async def _build_sprint_menu_text(team: dict, sprint: dict | None, manager_participates: bool = False) -> str:
    status = "✅ Активны" if team.get("sprint_enabled") else "⏸️ Выключены"
    duration = team.get("sprint_duration_weeks") or 2

    lines = [
        "<b>🏁 Управление спринтами</b>",
        "",
        "<b>📋 Как работают спринты:</b>",
        "• <b>Планирование:</b> Каждый понедельник бот автоматически запрашивает планы на спринт",
        "• <b>Отчёты:</b> В дни отчетов участники получают вопросы о прогрессе",
        "• <b>Анализ:</b> В последний день спринта формируется итоговый отчёт с LLM",
        "• <b>Завершение:</b> Спринт завершается автоматически в последний день отчетов",
        "",
        f"<b>👤 Ваш статус в опросах:</b> {'✅ Участвую' if manager_participates else '❌ Не участвую'}",
        "",
        f"<b>⚙️ Настройки:</b>",
        f"Статус: {status}",
        f"Длительность: {duration} нед.",
    ]
    if sprint:
        last_report_date = sprint.get("last_report_date")
        end_date = sprint.get("end_date")
        period = _format_period(sprint.get("start_date"), last_report_date)
        deadline_text = _ensure_date(last_report_date).strftime("%d.%m.%Y") if last_report_date else "—"

        # Определяем статус спринта и показываем соответствующую дату
        tz = pytz.timezone(team.get("timezone") or "Asia/Yekaterinburg")
        today = datetime.now(tz).date()
        start_date = _ensure_date(sprint.get("start_date"))
        sprint_end_date = _ensure_date(end_date) if end_date else start_date

        if today < start_date:
            sprint_status = "⏳ Запланирован"
            sprint_started = "Не начался"
            next_date_text = f"Начало: {start_date.strftime('%d.%m.%Y')}"
        elif today <= sprint_end_date:
            sprint_status = "🔄 Активен сейчас"
            sprint_started = "Начался"
            next_date_text = f"Завершение: {sprint_end_date.strftime('%d.%m.%Y')}"
        else:
            sprint_status = "⌛ Завершён (ожидает финальный отчёт)"
            sprint_started = "Завершён"
            next_date_text = f"Завершение: {sprint_end_date.strftime('%d.%m.%Y')}"

        lines.append(f"Спринт: {sprint_status}")
        lines.append(f"Статус: {sprint_started}")
        lines.append(f"Период: {period}")
        lines.append(next_date_text)
        if today > end_date:  # Показываем дату финального отчета только для завершенных спринтов
            lines.append(f"Финальный отчёт: {deadline_text}")

        # Добавляем статистику спринта
        lines.append("")  # Пустая строка для разделения
        lines.append("<b>📊 Статистика спринта:</b>")

        # Получаем участников команды
        team_members = await db_get_team_members(team["id"])
        total_members = len(team_members)

        # Получаем планы спринта
        sprint_plans = await db_get_sprint_plans_for_team(sprint["id"])
        plans_count = len(sprint_plans)

        lines.append(f"👥 Участников: {total_members}")
        lines.append(f"📝 Предоставлено планов: {plans_count}/{total_members}")

        if sprint.get("plans_requested"):
            lines.append("✅ Запрос планов отправлен")
        else:
            lines.append("⏳ Планы еще не запрашивались")
    elif team.get("sprint_enabled"):
        # Рассчитываем дату следующего понедельника
        tz = pytz.timezone(team.get("timezone") or "Asia/Yekaterinburg")
        today = datetime.now(tz).date()
        days_until_monday = (7 - today.weekday()) % 7
        if days_until_monday == 0:  # Сегодня понедельник
            days_until_monday = 7  # Следующий понедельник
        next_monday = today + timedelta(days=days_until_monday)
        lines.append(f"📅 Следующий спринт: {next_monday.strftime('%d.%m.%Y')}")
        lines.append("💡 Каждый понедельник бот автоматически создаёт новый спринт и запрашивает планы")
    else:
        lines.append("<b>🔄 Как включить спринты:</b>")
        lines.append("• Нажмите кнопку '✅ Включить спринты'")
        lines.append("• Установите длительность спринта (1-6 недель)")
        lines.append("• Бот начнёт автоматически управлять спринтами")
    return "\n".join(lines)


def _build_my_plans_text(team: dict, sprint: dict, plans: list[dict]) -> str:
    header = f"<b>🏁 Мои планы — {team['name']}</b>"
    period = _format_period(sprint.get("start_date"), sprint.get("last_report_date"))
    lines = [header, f"Период: {period}", ""]
    if not plans:
        lines.append("Вы ещё не добавили планы на текущий спринт.")
    else:
        for idx, item in enumerate(plans, start=1):
            created = item["created_at"]
            if isinstance(created, datetime):
                created_str = created.strftime("%d.%m %H:%M")
            else:
                created_str = str(created)
            lines.append(f"{idx}. ({created_str}) {item['plan_text']}")
    lines.append("\nДобавляйте новые пункты, чтобы дополнять список.")
    return "\n".join(lines)


async def _get_current_team_for_user(user_id: int) -> dict | None:
    team = await db_get_team_by_manager(user_id)
    if team:
        return team
    employee = await db_get_employee(user_id)
    if not employee or not employee.get("team_id"):
        return None
    return await db_get_team_by_id(employee["team_id"])


@router.callback_query(F.data == "open_sprint_menu")
async def open_sprint_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("настройки спринтов"), show_alert=True)
        return
    sprint = await db_get_active_sprint(team["id"])
    # Check if manager participates in surveys
    membership = await db_get_membership(user_id, team["id"])
    manager_participates = membership and membership.get("is_participant", False)
    text = await _build_sprint_menu_text(team, sprint, manager_participates)
    await send_or_edit_message(callback, text, reply_markup=sprint_menu_keyboard(bool(team.get("sprint_enabled")), bool(sprint), sprint["id"] if sprint else None, manager_participates))
    await callback.answer()


@router.callback_query(F.data == "sprint_change_duration")
async def change_sprint_duration(callback: CallbackQuery):
    user_id = callback.from_user.id
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("изменения длительности спринта"), show_alert=True)
        return
    duration = team.get("sprint_duration_weeks") or 2
    await send_or_edit_message(
        callback,
        "<b>⏱️ Длительность спринта</b>\n\n"
        "Выберите, сколько недель будет длиться один спринт:\n\n"
        "• <b>1 неделя:</b> Быстрые итерации, частые отчёты\n"
        "• <b>2 недели:</b> Стандартная длительность (рекомендуется)\n"
        "• <b>3-4 недели:</b> Для крупных проектов\n"
        "• <b>5-6 недель:</b> Для очень масштабных задач\n\n"
        "<i>💡 Изменение вступит в силу со следующего спринта</i>",
        reply_markup=sprint_duration_keyboard(duration),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sprint_set_duration_"))
async def set_sprint_duration(callback: CallbackQuery):
    user_id = callback.from_user.id
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("изменения длительности спринта"), show_alert=True)
        return
    try:
        weeks = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Некорректное значение.")
        return
    weeks = max(1, min(weeks, 12))

    # Check if there's an active sprint that needs date recalculation
    active_sprint = await db_get_active_sprint(team["id"])
    if active_sprint:
        # Recalculate end date and last report date for the active sprint
        start_date = _ensure_sprint_date(active_sprint.get("start_date"))
        new_last_report_date = calculate_last_report_date(start_date, weeks, team.get("report_days"))
        # Дата окончания спринта = дата последнего отчета
        new_end_date = new_last_report_date

        # Check if new end date is in the past - prompt to finish sprint
        tz = pytz.timezone(team.get("timezone") or "Asia/Yekaterinburg")
        today = datetime.now(tz).date()
        if new_last_report_date < today:
            # Show confirmation dialog to finish sprint
            from bot.utils.keyboards import InlineKeyboardMarkup, InlineKeyboardButton
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Завершить спринт сейчас", callback_data=f"confirm_finish_sprint_{active_sprint['id']}_{weeks}")],
                [InlineKeyboardButton(text="❌ Продолжить спринт", callback_data=f"continue_sprint_{weeks}")]
            ])
            await send_or_edit_message(
                callback,
                f"⚠️ Новая дата окончания спринта ({new_last_report_date.strftime('%d.%m.%Y')}) находится в прошлом.\n\n"
                f"Хотите завершить текущий спринт сейчас и отправить отчёт?",
                reply_markup=keyboard
            )
            await callback.answer()
            return

        await db_update_sprint_dates(
            active_sprint["id"],
            end_date=new_end_date,
            last_report_date=new_last_report_date,
            duration_weeks=weeks
        )
        logging.info("Менеджер %s обновил длительность спринта до %s недель и пересчитал даты активного спринта %s", user_id, weeks, active_sprint["id"])

    await db_update_sprint_settings(team["id"], duration_weeks=weeks)
    logging.info("Менеджер %s обновил длительность спринта до %s недель", user_id, weeks)
    updated_team = await db_get_team_by_manager(user_id)
    sprint = await db_get_active_sprint(updated_team["id"])
    # Check if manager participates in surveys
    membership = await db_get_membership(user_id, updated_team["id"])
    manager_participates = membership and membership.get("is_participant", False)
    text = await _build_sprint_menu_text(updated_team, sprint, manager_participates)
    await send_or_edit_message(callback, text, reply_markup=sprint_menu_keyboard(bool(updated_team.get("sprint_enabled")), bool(sprint), sprint["id"] if sprint else None, manager_participates))
    await callback.answer("Длительность обновлена")


@router.callback_query(F.data.startswith("confirm_finish_sprint_"))
async def confirm_finish_sprint(callback: CallbackQuery):
    user_id = callback.from_user.id
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("завершения спринта"), show_alert=True)
        return

    try:
        _, _, _, sprint_id_str, weeks_str = callback.data.split("_")
        sprint_id = int(sprint_id_str)
        weeks = int(weeks_str)
    except (ValueError, IndexError):
        await callback.answer("Некорректные параметры.")
        return

    # Verify sprint belongs to team
    sprint = await db_get_sprint_by_id(sprint_id)
    if not sprint or sprint["team_id"] != team["id"]:
        await callback.answer("Спринт не найден.", show_alert=True)
        return

    # Отвечаем на callback сразу, чтобы избежать timeout
    await callback.answer()

    # Finish sprint and generate report
    await send_or_edit_message(callback, "🏁 Завершаю спринт и генерирую отчёт...")

    try:
        # Generate final report
        reports = await db_get_reports_for_sprint(
            team["id"],
            _ensure_sprint_date(sprint.get("start_date")),
            _ensure_sprint_date(sprint.get("end_date"))
        )
        plans = await db_get_sprint_plans_for_team(sprint["id"])

        period_text = _format_sprint_period(sprint.get("start_date"), sprint.get("end_date"))
        plans_text = _format_sprint_plans_for_llm(plans)
        reports_text = _format_sprint_reports_for_llm(reports)

        expected_reports_count = calculate_expected_reports_count(
            _ensure_sprint_date(sprint.get("start_date")),
            _ensure_sprint_date(sprint.get("end_date")),
            team.get("report_days")
        )

        summary = await llm_processor.sprint_summarizator_async(
            team_name=team["name"],
            period_text=period_text,
            plans_text=plans_text,
            reports_text=reports_text,
            expected_reports_count=expected_reports_count,
            team_id=team["id"]
        )
        # Если LLM вернул пустой ответ, используем безопасное сообщение по умолчанию
        if not summary or not str(summary).strip():
            logging.warning("LLM вернул пустой финальный спринтовый отчёт для команды %s", team["id"])
            summary = "❌ Не удалось сформировать аналитический отчёт по спринту."

        # Finish sprint
        await db_finish_sprint(sprint_id)

        # Update team settings with new duration
        await db_update_sprint_settings(team["id"], duration_weeks=weeks)

        # Send summary to manager and team chat
        header = f"🏁 <b>Финальный отчёт спринта «{team['name']}»</b>\n{period_text}\n\n"
        plans_block = _format_sprint_plans_for_humans(plans)
        # Сначала планы участников, затем аналитический отчёт
        if plans_block:
            full_report = f"{header}{plans_block}\n\n{summary}"
        else:
            full_report = header + summary

        parts = split_long_message(full_report, MAX_MESSAGE_LENGTH)
        for i, part in enumerate(parts):
            if len(parts) > 1:
                part = f"{part}\n\n<i>(Часть {i + 1} из {len(parts)})</i>"

            # Send to manager
            await callback.message.answer(part, parse_mode="HTML")

            # Send to team chat if configured
            if team.get("chat_id"):
                try:
                    await callback.bot.send_message(
                        chat_id=team["chat_id"],
                        text=part,
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logging.error(f"Не удалось отправить отчёт в чат команды: {e}")

        # Return to sprint menu
        updated_team = await db_get_team_by_manager(user_id)
        membership = await db_get_membership(user_id, updated_team["id"])
        manager_participates = membership and membership.get("is_participant", False)
        text = await _build_sprint_menu_text(updated_team, None, manager_participates)
        await send_or_edit_message(callback, text, reply_markup=sprint_menu_keyboard(bool(updated_team.get("sprint_enabled")), False, None, manager_participates))
        await callback.answer("Спринт завершён")

    except Exception as exc:
        logging.error("Ошибка при завершении спринта: %s", exc)
        await send_or_edit_message(callback, "❌ Произошла ошибка при завершении спринта.")


@router.callback_query(F.data.startswith("manual_finish_sprint_"))
async def manual_finish_sprint(callback: CallbackQuery):
    user_id = callback.from_user.id
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("завершения спринта"), show_alert=True)
        return

    try:
        sprint_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Некорректный ID спринта.")
        return

    # Verify sprint belongs to team and is active
    sprint = await db_get_sprint_by_id(sprint_id)
    if not sprint or sprint["team_id"] != team["id"] or not sprint.get("is_active"):
        await callback.answer("Спринт не найден или уже завершён.", show_alert=True)
        return

    # Confirm finishing sprint
    from bot.utils.keyboards import InlineKeyboardMarkup, InlineKeyboardButton
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить завершение", callback_data=f"confirm_manual_finish_{sprint_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="open_sprint_menu")]
    ])
    await send_or_edit_message(
        callback,
        f"🏁 Вы уверены, что хотите завершить спринт?\n\n"
        f"Будет сгенерирован финальный отчёт и отправлен вам и в чат команды (если указан).",
        reply_markup=keyboard
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_manual_finish_"))
async def confirm_manual_finish_sprint(callback: CallbackQuery):
    user_id = callback.from_user.id
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("завершения спринта"), show_alert=True)
        return

    try:
        sprint_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Некорректный ID спринта.")
        return

    # Verify sprint belongs to team and is active
    sprint = await db_get_sprint_by_id(sprint_id)
    if not sprint or sprint["team_id"] != team["id"] or not sprint.get("is_active"):
        await callback.answer("Спринт не найден или уже завершён.", show_alert=True)
        return

    # Отвечаем на callback сразу, чтобы избежать timeout
    await callback.answer()

    # Finish sprint and generate report
    await send_or_edit_message(callback, "🏁 Завершаю спринт и генерирую отчёт...")

    try:
        # Generate final report
        reports = await db_get_reports_for_sprint(
            team["id"],
            _ensure_sprint_date(sprint.get("start_date")),
            _ensure_sprint_date(sprint.get("end_date"))
        )
        plans = await db_get_sprint_plans_for_team(sprint["id"])

        period_text = _format_sprint_period(sprint.get("start_date"), sprint.get("end_date"))
        plans_text = _format_sprint_plans_for_llm(plans)
        reports_text = _format_sprint_reports_for_llm(reports)

        expected_reports_count = calculate_expected_reports_count(
            _ensure_sprint_date(sprint.get("start_date")),
            _ensure_sprint_date(sprint.get("end_date")),
            team.get("report_days")
        )

        summary = await llm_processor.sprint_summarizator_async(
            team_name=team["name"],
            period_text=period_text,
            plans_text=plans_text,
            reports_text=reports_text,
            expected_reports_count=expected_reports_count,
            team_id=team["id"]
        )
        # Если LLM вернул пустой ответ, используем безопасное сообщение по умолчанию
        if not summary or not str(summary).strip():
            logging.warning("LLM вернул пустой финальный спринтовый отчёт (manual) для команды %s", team["id"])
            summary = "❌ Не удалось сформировать аналитический отчёт по спринту."

        # Finish sprint
        await db_finish_sprint(sprint_id)

        # Send summary to manager and team chat
        header = f"🏁 <b>Финальный отчёт спринта «{team['name']}»</b>\n{period_text}\n\n"
        plans_block = _format_sprint_plans_for_humans(plans)
        # Сначала планы участников, затем аналитический отчёт
        if plans_block:
            full_report = f"{header}{plans_block}\n\n{summary}"
        else:
            full_report = header + summary

        parts = split_long_message(full_report, MAX_MESSAGE_LENGTH)
        for i, part in enumerate(parts):
            if len(parts) > 1:
                part = f"{part}\n\n<i>(Часть {i + 1} из {len(parts)})</i>"

            # Send to manager
            await callback.message.answer(part, parse_mode="HTML")

            # Send to team chat if configured
            if team.get("chat_id"):
                try:
                    await callback.bot.send_message(
                        chat_id=team["chat_id"],
                        text=part,
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logging.error(f"Не удалось отправить отчёт в чат команды: {e}")

        # Return to sprint menu
        updated_team = await db_get_team_by_manager(user_id)
        membership = await db_get_membership(user_id, updated_team["id"])
        manager_participates = membership and membership.get("is_participant", False)
        text = await _build_sprint_menu_text(updated_team, None, manager_participates)
        await send_or_edit_message(callback, text, reply_markup=sprint_menu_keyboard(bool(updated_team.get("sprint_enabled")), False, None, manager_participates))

    except Exception as exc:
        logging.error("Ошибка при завершении спринта: %s", exc)
        await send_or_edit_message(callback, "❌ Произошла ошибка при завершении спринта.")


@router.callback_query(F.data.startswith("continue_sprint_"))
async def continue_sprint(callback: CallbackQuery):
    user_id = callback.from_user.id
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("продолжения спринта"), show_alert=True)
        return

    try:
        weeks = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Некорректное значение.")
        return

    # Continue with normal duration update
    active_sprint = await db_get_active_sprint(team["id"])
    if active_sprint:
        start_date = _ensure_sprint_date(active_sprint.get("start_date"))
        new_end_date = calculate_sprint_end_date(start_date, weeks)
        new_last_report_date = calculate_last_report_date(start_date, weeks, team.get("report_days"))

        await db_update_sprint_dates(
            active_sprint["id"],
            end_date=new_end_date,
            last_report_date=new_last_report_date,
            duration_weeks=weeks
        )

    await db_update_sprint_settings(team["id"], duration_weeks=weeks)
    updated_team = await db_get_team_by_manager(user_id)
    sprint = await db_get_active_sprint(updated_team["id"])
    membership = await db_get_membership(user_id, updated_team["id"])
    manager_participates = membership and membership.get("is_participant", False)
    text = await _build_sprint_menu_text(updated_team, sprint, manager_participates)
    await send_or_edit_message(callback, text, reply_markup=sprint_menu_keyboard(bool(updated_team.get("sprint_enabled")), bool(sprint), sprint["id"] if sprint else None, manager_participates))
    await callback.answer("Длительность обновлена")


@router.callback_query(F.data == "sprint_enable")
async def enable_sprint(callback: CallbackQuery):
    user_id = callback.from_user.id
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("включения спринтов"), show_alert=True)
        return
    await db_update_sprint_settings(team["id"], enabled=True)
    await scheduler_manager.update_team_jobs(team["id"])
    try:
        await handle_sprint_cycle(team["id"])
    except Exception as exc:
        logging.error("Не удалось запустить цикл спринта сразу после включения: %s", exc)
    updated_team = await db_get_team_by_manager(user_id)
    sprint = await db_get_active_sprint(updated_team["id"])
    membership = await db_get_membership(user_id, updated_team["id"])
    manager_participates = membership and membership.get("is_participant", False)
    text = await _build_sprint_menu_text(updated_team, sprint, manager_participates)
    await send_or_edit_message(callback, text, reply_markup=sprint_menu_keyboard(True, bool(sprint), sprint["id"] if sprint else None, manager_participates))
    await callback.answer("Спринты включены")


@router.callback_query(F.data == "sprint_disable")
async def disable_sprint(callback: CallbackQuery):
    user_id = callback.from_user.id
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("отключения спринтов"), show_alert=True)
        return
    sprint = await db_get_active_sprint(team["id"])
    if sprint:
        await db_finish_sprint(sprint["id"])
    await db_update_sprint_settings(team["id"], enabled=False)
    await scheduler_manager.update_team_jobs(team["id"])
    updated_team = await db_get_team_by_manager(user_id)
    membership = await db_get_membership(user_id, updated_team["id"])
    manager_participates = membership and membership.get("is_participant", False)
    membership = await db_get_membership(user_id, updated_team["id"])
    manager_participates = membership and membership.get("is_participant", False)
    text = await _build_sprint_menu_text(updated_team, None, manager_participates)
    await send_or_edit_message(callback, text, reply_markup=sprint_menu_keyboard(False, False, None, manager_participates))
    await callback.answer("Спринты отключены")


@router.callback_query(F.data == "view_sprint_my_plans")
async def view_sprint_my_plans(callback: CallbackQuery):
    user_id = callback.from_user.id
    team = await _get_current_team_for_user(user_id)
    if not team:
        await callback.answer("Сначала выберите команду.", show_alert=True)
        return
    sprint = await db_get_active_sprint(team["id"])
    if not sprint:
        if team.get("sprint_enabled"):
            await callback.answer("Спринт ещё не начался. Ожидайте понедельник.", show_alert=True)
        else:
            await callback.answer("Спринты отключены в текущей команде.", show_alert=True)
        return
    plans = await db_get_sprint_plans_for_user(sprint["id"], user_id)
    text = _build_my_plans_text(team, sprint, plans)
    has_plans = len(plans) > 0
    await send_or_edit_message(callback, text, reply_markup=sprint_my_plans_keyboard(team["id"], sprint["id"], has_plans))
    await callback.answer()


async def _start_plan_input(user_id: int, chat, state: FSMContext, sprint_id: int, team_id: int):
    sprint = await db_get_sprint_by_id(sprint_id)
    if not sprint or not sprint.get("is_active"):
        await send_or_edit_message(chat, "❌ Спринт уже завершён. Планы недоступны.")
        return
    await state.update_data(sprint_id=sprint_id, team_id=team_id)
    await state.set_state(SprintPlan.waiting_for_entry)
    from bot.utils.text_constants import get_sprint_plan_instructions
    await send_or_edit_message(
        chat,
        get_sprint_plan_instructions(),
        reply_markup=cancel_keyboard()
    )


@router.callback_query(F.data.startswith("sprint_plan_add_"))
async def sprint_plan_add(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    team = await _get_current_team_for_user(user_id)
    if not team:
        await callback.answer("Сначала выберите команду.", show_alert=True)
        return
    try:
        sprint_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Некорректный спринт.")
        return
    sprint = await db_get_sprint_by_id(sprint_id)
    if not sprint or sprint.get("team_id") != team["id"]:
        await callback.answer("Этот спринт вам недоступен.", show_alert=True)
        return
    await _start_plan_input(user_id, callback, state, sprint_id, team["id"])
    await callback.answer()


@router.callback_query(F.data.startswith("start_sprint_plan_"))
async def start_sprint_plan(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    if len(parts) != 5:
        await callback.answer("Некорректный запрос.")
        return
    _, _, _, team_id_str, sprint_id_str = parts
    try:
        team_id = int(team_id_str)
        sprint_id = int(sprint_id_str)
    except ValueError:
        await callback.answer("Некорректные параметры.")
        return
    user_id = callback.from_user.id
    membership = await db_get_membership(user_id, team_id)
    if not membership:
        await callback.answer("Вы не состоите в этой команде.", show_alert=True)
        return
    await _start_plan_input(user_id, callback, state, sprint_id, team_id)
    await callback.answer()


@router.message(SprintPlan.waiting_for_entry)
async def handle_sprint_plan_entry(message: Message, state: FSMContext):
    user_id = message.from_user.id
    text = (message.text or "").strip()
    if not text:
        await message.answer("План не должен быть пустым. Попробуйте ещё раз.")
        return
    if len(text) > 2000:
        await message.answer("План слишком длинный. Сократите текст (до 2000 символов).")
        return

    data = await state.get_data()
    sprint_id = data.get("sprint_id")
    team_id = data.get("team_id")
    if not sprint_id or not team_id:
        await message.answer("Не удалось определить спринт. Начните заново.")
        await state.clear()
        return

    sprint = await db_get_sprint_by_id(sprint_id)
    if not sprint or not sprint.get("is_active"):
        await message.answer("Спринт уже завершён. План не сохранён.")
        await state.clear()
        return

    # Сохраняем план
    await db_add_sprint_plan_entry(sprint_id, user_id, text)
    await state.clear()

    # Показываем благодарность и возвращаем в меню
    await message.answer(
        "✅ Спасибо! Ваш план на спринт добавлен.\n\n"
        "Вы всегда можете дополнить планы позже через меню «🏁 Мои планы».",
        reply_markup=None  # Убираем клавиатуру, возвращаем в обычное состояние
    )


@router.callback_query(F.data == "sprint_interim_report")
async def sprint_interim_report(callback: CallbackQuery):
    """Генерация промежуточного отчета по спринту (не завершает спринт)"""
    user_id = callback.from_user.id

    # Проверяем что пользователь - менеджер
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("промежуточного отчета"), show_alert=True)
        return

    # Проверяем что спринты включены
    if not team.get("sprint_enabled"):
        await callback.answer("Спринты отключены для этой команды", show_alert=True)
        return

    # Проверяем есть ли активный спринт
    sprint = await db_get_active_sprint(team["id"])
    if not sprint:
        await callback.answer("Нет активного спринта", show_alert=True)
        return

    # Отвечаем на callback сразу, чтобы избежать timeout
    await callback.answer()

    # Показываем сообщение о генерации отчета
    await send_or_edit_message(
        callback,
        "📊 Генерирую промежуточный отчет по спринту...\n\n"
        "Это может занять некоторое время."
    )

    try:
        # Получаем все отчеты за период спринта
        reports = await db_get_reports_for_sprint(
            team["id"],
            _ensure_sprint_date(sprint.get("start_date")),
            _ensure_sprint_date(sprint.get("end_date"))
        )

        # Получаем планы участников
        plans = await db_get_sprint_plans_for_team(sprint["id"])

        # Форматируем сводку планов и отчетов
        period_text = _format_sprint_period(sprint.get("start_date"), sprint.get("end_date"))
        summary = await _format_interim_summary(team["name"], period_text, plans, reports)

        if not summary:
            await send_or_edit_message(
                callback,
                "❌ Не удалось сформировать промежуточный отчет.\n\n"
                "Попробуйте позже."
            )
            return

        # Разбиваем на части если нужно
        parts = split_long_message(summary, MAX_MESSAGE_LENGTH)

        for i, part in enumerate(parts):
            if len(parts) > 1:
                part = f"{part}\n\n<i>(Часть {i + 1} из {len(parts)})</i>"

            await callback.message.answer(
                part,
                parse_mode="HTML"
            )

        # Возвращаемся к меню спринтов
        membership = await db_get_membership(user_id, team["id"])
        manager_participates = membership and membership.get("is_participant", False)
        menu_text = await _build_sprint_menu_text(team, sprint, manager_participates)
        await send_or_edit_message(
            callback,
            menu_text,
            reply_markup=sprint_menu_keyboard(True, True, sprint["id"], manager_participates)
        )

    except Exception as exc:
        logging.error("Ошибка при генерации промежуточного отчета: %s", exc)
        await send_or_edit_message(
            callback,
            "❌ Произошла ошибка при генерации отчета.\n\n"
            "Попробуйте позже."
        )


async def _start_sprint_manually(team_id: int):
    """Создание спринта вручную (без проверки дня недели)."""
    team = await db_get_team_by_id(team_id)
    if not team or not team.get('sprint_enabled'):
        raise ValueError("Спринты отключены для команды")

    tz = pytz.timezone(team['timezone'])
    today = datetime.now(tz).date()

    sprint = await db_get_active_sprint(team_id)
    if sprint:
        raise ValueError("Спринт уже активен")

    # Создаем спринт начиная с сегодняшнего дня (force_start_date=True для ручного запуска)
    sprint = await db_create_sprint(
        team_id,
        today,
        team.get('sprint_duration_weeks') or 2,
        team.get('report_days'),
        force_start_date=True
    )
    logging.info("Создан новый спринт %s для команды %s (ручной запуск)", sprint['id'], team_id)

    # Немедленно запрашиваем планы
    await _send_sprint_plan_requests(team, sprint)
    await db_mark_sprint_plans_requested(sprint['id'])


@router.callback_query(F.data == "start_sprint_now")
async def start_sprint_now(callback: CallbackQuery):
    """Ручной запуск спринта менеджером"""
    user_id = callback.from_user.id

    # Проверяем что пользователь - менеджер
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("запуска спринта"), show_alert=True)
        return

    # Проверяем что спринты включены
    if not team.get("sprint_enabled"):
        await send_or_edit_message(
            callback,
            "🏁 Спринты отключены для этой команды.\n\n"
            "Сначала включите спринты в настройках."
        )
        await callback.answer()
        return

    # Проверяем есть ли уже активный спринт
    active_sprint = await db_get_active_sprint(team["id"])
    if active_sprint:
        await send_or_edit_message(
            callback,
            f"🏁 Спринт уже активен!\n\n"
            f"Период: {_format_period(active_sprint.get('start_date'), active_sprint.get('last_report_date'))}\n\n"
            "Дождитесь окончания текущего спринта или завершите его вручную."
        )
        await callback.answer()
        return

    # Создаем новый спринт и отправляем запросы планов
    try:
        # Создаем спринт вручную (не дожидаясь понедельника)
        await _start_sprint_manually(team["id"])

        # Обновляем планировщик для новой задачи
        await scheduler_manager.update_team_jobs(team["id"])

        # Показываем обновленное меню
        updated_team = await db_get_team_by_manager(user_id)
        sprint = await db_get_active_sprint(updated_team["id"])
        membership = await db_get_membership(user_id, updated_team["id"])
        manager_participates = membership and membership.get("is_participant", False)
        text = await _build_sprint_menu_text(updated_team, sprint, manager_participates)
        await send_or_edit_message(callback, text, reply_markup=sprint_menu_keyboard(True, bool(sprint), sprint["id"] if sprint else None, manager_participates))
        await callback.answer("Спринт запущен!")

    except Exception as exc:
        logging.error("Не удалось запустить спринт вручную: %s", exc)
        await send_or_edit_message(
            callback,
            "❌ Не удалось запустить спринт. Попробуйте позже."
        )
        await callback.answer()


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
    """Форматирует планы спринта для отображения в финальном/ручном спринтовом отчёте."""
    if not plans:
        return "🏁 Планы участников:\n\n📝 Пока нет планов на спринт"

    grouped: dict = {}
    for plan in plans:
        key = plan.get('employee_tg_id')
        grouped.setdefault(key, {
            'name': plan.get('full_name') or f"ID {plan.get('employee_tg_id')}",
            'role': plan.get('role') or '',
            'plans': []
        })
        grouped[key]['plans'].append(plan.get('plan_text'))

    lines: list[str] = ["🏁 Планы участников:", ""]
    for user_info in grouped.values():
        role_suffix = f" ({user_info['role']})" if user_info['role'] else ""
        lines.append(f"👤 <b>{user_info['name']}{role_suffix}:</b>")
        for plan_text in user_info['plans']:
            lines.append(f"  • {plan_text}")
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


async def _format_interim_summary(team_name: str, period_text: str, plans: list[dict], reports: list[dict]) -> str:
    """Форматирование промежуточной сводки планов и отчетов без LLM анализа"""
    lines = [
        f"📊 <b>Промежуточная сводка команды «{team_name}»</b>",
        f"{period_text}",
        ""
    ]

    # Сводка планов
    if plans:
        lines.append("<b>🏁 Планы участников:</b>")
        grouped_plans = {}
        for plan in plans:
            key = plan.get('employee_tg_id')
            if key not in grouped_plans:
                grouped_plans[key] = {
                    'name': plan.get('full_name') or f"ID {plan.get('employee_tg_id')}",
                    'role': plan.get('role') or '',
                    'plans': []
                }
            grouped_plans[key]['plans'].append(plan.get('plan_text'))

        for user_info in grouped_plans.values():
            role_suffix = f" ({user_info['role']})" if user_info['role'] else ""
            lines.append(f"👤 <b>{user_info['name']}{role_suffix}:</b>")
            for plan_text in user_info['plans']:
                lines.append(f"  • {plan_text}")
            lines.append("")
    else:
        lines.append("<b>🏁 Планы участников:</b>")
        lines.append("📝 Пока нет планов на спринт")
        lines.append("")

    # Сводка отчетов
    if reports:
        lines.append("<b>📋 Отчеты участников:</b>")
        # Группируем отчеты по пользователям
        user_reports = {}
        for report in reports:
            key = report.get('employee_tg_id')
            if key not in user_reports:
                user_reports[key] = {
                    'name': report.get('full_name') or f"ID {report.get('employee_tg_id')}",
                    'role': report.get('role') or '',
                    'reports': []
                }
            user_reports[key]['reports'].append(report)

        for user_info in user_reports.values():
            role_suffix = f" ({user_info['role']})" if user_info['role'] else ""
            lines.append(f"👤 <b>{user_info['name']}{role_suffix}:</b>")

            # Сортируем отчеты по дате
            sorted_reports = sorted(user_info['reports'], key=lambda x: x.get('report_datetime'))

            for report in sorted_reports:
                dt = report.get('report_datetime')
                if isinstance(dt, datetime):
                    dt_text = dt.strftime('%d.%m %H:%M')
                else:
                    dt_text = str(dt)

                answers = _normalize_answers(report.get('answers_json'))
                yesterday = answers.get('yesterday') or answers.get('Yesterday') or '—'
                today = answers.get('today') or answers.get('Today') or '—'
                problems = answers.get('problems') or answers.get('Problems') or '—'

                lines.append(f"  📅 <i>{dt_text}:</i>")
                lines.append(f"    <b>Вчера:</b> {yesterday}")
                lines.append(f"    <b>Сегодня:</b> {today}")
                lines.append(f"    <b>Проблемы:</b> {problems}")
            lines.append("")
    else:
        lines.append("<b>📋 Отчеты участников:</b>")
        lines.append("📝 Пока нет отчетов")
        lines.append("")

    return "\n".join(lines)

