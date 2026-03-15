# bot/handlers/weekly_plan_handlers.py

import logging
from datetime import datetime, timedelta
import pytz
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from bot.core import WeeklyPlan
from bot.core.database import db_get_team_by_id, db_save_weekly_plan, db_get_weekly_plan, db_get_team_by_id, db_get_employee, get_pool, db_get_team_by_manager, db_get_team_members
from bot.utils import send_or_edit_message, menu_inline_keyboard
from bot.utils.utils import extract_text_from_message, validate_text_or_voice_message
from bot.utils.keyboards import cancel_keyboard, voice_confirmation_keyboard, write_weekly_plan_keyboard, select_team_keyboard
from psycopg.rows import dict_row

router = Router()

async def send_weekly_plan_request_to_user(tg_id: int, team_id: int):
    """Отправить пользователю запрос на планы"""
    from bot.core.bot_instance import bot
    try:
        await bot.send_message(
            chat_id=tg_id,
            text="📅 Что у Вас в планах на этой неделе?",
            reply_markup=menu_inline_keyboard()
        )
        # Устанавливаем FSM
        from aiogram.fsm.storage.memory import MemoryStorage
        from bot.core.bot_instance import dp
        state = dp.fsm.storage
        await state.set_state(tg_id, tg_id, WeeklyPlan.waiting_for_plan)
        logging.info(f"Запрос на план отправлен пользователю {tg_id}")
    except Exception as e:
        logging.error(f"Не удалось отправить запрос на план пользователю {tg_id}: {e}")


@router.message(WeeklyPlan.waiting_for_plan)
async def handle_weekly_plan_answer(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = await state.get_data()
    team_id = user_data.get('selected_team_id')
    if not team_id:
        await message.answer("Ошибка: команда не выбрана.")
        await state.clear()
        return
    team = await db_get_team_by_id(team_id)
    if not team:
        await message.answer("Команда не найдена.")
        await state.clear()
        return
    tz = pytz.timezone(team['timezone'])
    now = datetime.now(tz)
    monday = (now - timedelta(days=now.weekday())).date()
    week_start_str = monday.strftime('%Y-%m-%d')
    if not validate_text_or_voice_message(message):
        await message.answer("Пожалуйста, отправьте текстовый ответ или голосовое сообщение.")
        return
    if message.text:
        plan_text = message.text
        logging.info(f"Пользователь {user_id} ответил текстом на план недели для команды {team_id}: {plan_text[:50]}...")
    elif message.voice:
        try:
            plan_text = await extract_text_from_message(message)
            await state.update_data(temp_plan=plan_text)
            confirmation_text = f"<b>Распознанный текст:</b>\n<i>{plan_text}</i>\n\n<b>Всё верно?</b>"
            await message.answer(confirmation_text, reply_markup=voice_confirmation_keyboard(f"weekly_plan_{team_id}"))
            await state.set_state(WeeklyPlan.confirming_plan)
            return
        except Exception as e:
            logging.error(f"Ошибка транскрипции: {e}")
            await message.answer("Ошибка при распознавании голоса. Попробуйте текстом.")
            return
    if not plan_text:
        await message.answer("Пожалуйста, отправьте текстовый ответ или голосовое сообщение.")
        return
    await db_save_weekly_plan(user_id, team_id, week_start_str, plan_text)
    await message.answer(f"✅ Ваш план на неделю для команды {team['name']} сохранён!", reply_markup=menu_inline_keyboard())
    await state.clear()

@router.callback_query(F.data.startswith("voice_confirm_weekly_plan_"))
async def confirm_plan_voice(callback: CallbackQuery, state: FSMContext):
    team_id = int(callback.data.split("_")[-1])
    user_data = await state.get_data()
    plan_text = user_data.get('temp_plan')
    if not plan_text:
        await callback.message.answer("Ошибка: текст не найден.")
        await callback.answer()
        return
    user_id = callback.from_user.id
    team = await db_get_team_by_id(team_id)
    tz = pytz.timezone(team['timezone'])
    monday = (datetime.now(tz) - timedelta(days=datetime.now(tz).weekday())).date()
    week_start_str = monday.strftime('%Y-%m-%d')
    await db_save_weekly_plan(user_id, team_id, week_start_str, plan_text)
    await callback.message.answer(f"✅ План для команды {team['name']} сохранён!", reply_markup=menu_inline_keyboard())
    await state.clear()
    await callback.answer()

@router.callback_query(F.data.startswith("voice_retry_weekly_plan_"))
async def retry_plan_voice(callback: CallbackQuery, state: FSMContext):
    team_id = int(callback.data.split("_")[-1])
    team = await db_get_team_by_id(team_id)
    await callback.message.answer(
        f"✍️ Пожалуйста, напишите ваш план на эту неделю для команды {team['name']} (текстом или голосом):"
    )
    await state.set_state(WeeklyPlan.waiting_for_plan)
    await callback.answer()

@router.message(F.text == "/test_plan")
async def test_weekly_plan(message: Message, state: FSMContext):
    user_id = message.from_user.id
    async with (await get_pool()).connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT t.id, t.name
                FROM teams t
                JOIN user_team_memberships utm ON t.id = utm.team_id
                WHERE utm.employee_tg_id = %s
            """, (user_id,))
            rows = await cur.fetchall()
            teams = [{"id": row[0], "name": row[1]} for row in rows]
    if not teams:
        await message.answer("Вы не состоите в команде. Невозможно составить план.")
        return
    if len(teams) == 1:
        await message.answer(
            f"📅 Что у Вас в планах на этой неделе для команды {teams[0]['name']}?",
            reply_markup=write_weekly_plan_keyboard(teams[0]['id'])
        )
    else:
        await message.answer(
            "📅 Для какой команды вы хотите составить план на этой неделе?",
            reply_markup=select_team_keyboard(teams, action="write")  # Указываем action="write"
        )

@router.callback_query(F.data.startswith("start_write_weekly_plan_"))
async def start_write_weekly_plan(callback: CallbackQuery, state: FSMContext):
    team_id = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id
    async with (await get_pool()).connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT 1 FROM user_team_memberships
                WHERE employee_tg_id = %s AND team_id = %s
            """, (user_id, team_id))
            if not await cur.fetchone():
                await callback.message.answer("Вы не состоите в этой команде.")
                await callback.answer()
                return
    team = await db_get_team_by_id(team_id)
    if not team:
        await callback.message.answer("Команда не найдена.")
        await callback.answer()
        return
    await state.update_data(selected_team_id=team_id)
    await state.set_state(WeeklyPlan.waiting_for_plan)
    await callback.message.answer(
        f"✍️ Пожалуйста, напишите ваш план на эту неделю для команды {team['name']} (текстом или голосом):",
        reply_markup=cancel_keyboard()
    )
    await callback.answer()



@router.callback_query(F.data == "view_weekly_plan")
async def view_weekly_plan(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    logging.info(f"Handling view_weekly_plan for user_id={user_id}")
    async with (await get_pool()).connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT t.id, t.name AS name
                FROM teams t
                JOIN user_team_memberships utm ON t.id = utm.team_id
                WHERE utm.employee_tg_id = %s
            """, (user_id,))
            teams = await cur.fetchall()
            logging.info(f"Teams for user_id={user_id}: {teams}")
    if not teams:
        await callback.message.answer("Вы не состоите в команде.")
        await callback.answer()
        return
    if len(teams) == 1:
        team = await db_get_team_by_id(teams[0]['id'])
        tz = pytz.timezone(team['timezone'])
        monday = (datetime.now(tz) - timedelta(days=datetime.now(tz).weekday())).date()
        week_start_str = monday.strftime('%Y-%m-%d')
        plan = await db_get_weekly_plan(user_id, week_start_str, teams[0]['id'])
        logging.info(f"Plan for team_id={teams[0]['id']}, week_start={week_start_str}: {plan}")
        if plan and plan['plan_text']:
            await callback.message.answer(f"<b>Ваш план на неделю для команды {team['name']}:</b>\n{plan['plan_text']}")
        else:
            await callback.message.answer(f"Вы пока не указали план на неделю для команды {team['name']}.")
    else:
        await callback.message.answer(
            "📅 Для какой команды вы хотите посмотреть план?",
            reply_markup=select_team_keyboard(teams, action="view")
        )
    await callback.answer()



@router.callback_query(F.data.startswith("view_weekly_plan_"))
async def view_weekly_plan_for_team(callback: CallbackQuery):
    team_id = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id
    logging.info(f"Handling view_weekly_plan_for_team for user_id={user_id}, team_id={team_id}")
    team = await db_get_team_by_id(team_id)
    if not team:
        await callback.message.answer("Команда не найдена.")
        await callback.answer()
        return
    tz = pytz.timezone(team['timezone'])
    monday = (datetime.now(tz) - timedelta(days=datetime.now(tz).weekday())).date()
    week_start_str = monday.strftime('%Y-%m-%d')
    plan = await db_get_weekly_plan(user_id, week_start_str, team_id)
    logging.info(f"Plan for user_id={user_id}, team_id={team_id}, week_start={week_start_str}: {plan}")
    if plan and plan['plan_text']:
        await callback.message.answer(f"<b>Ваш план на неделю для команды {team['name']}:</b>\n{plan['plan_text']}")
    else:
        await callback.message.answer(f"Вы пока не указали план на неделю для команды {team['name']}.")
    await callback.answer()

@router.callback_query(F.data == "view_team_weekly_plans")
async def view_team_weekly_plans(callback: CallbackQuery, state: FSMContext):
    """Показать планы на неделю всех сотрудников текущей команды менеджера"""
    await state.clear()
    user_id = callback.from_user.id
    logging.info(f"✅ view_team_weekly_plans вызван менеджером {user_id}")

    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer("Вы не являетесь менеджером ни одной команды.", show_alert=True)
        return

    team_id = team['id']
    team_name = team['name']

    employees = await db_get_team_members(team_id)
    employees = [
        emp for emp in employees
        if not (emp.get('is_manager') and not emp.get('is_participant', False))
    ]

    if not employees:
        await callback.message.answer(f"В команде «{team_name}» пока нет сотрудников.")
        await callback.answer()
        return

    tz = pytz.timezone(team['timezone'])
    now = datetime.now(tz)
    monday = (now - timedelta(days=now.weekday())).date()
    week_start_str = monday.strftime('%Y-%m-%d')

    # Отправляем информацию по КАЖДОМУ сотруднику
    for emp in employees:
        tg_id = emp['tg_id']
        full_name = emp.get('full_name') or f"ID: {tg_id}"
        plan = await db_get_weekly_plan(tg_id, week_start_str, team_id)

        if plan and plan.get('plan_text'):
            text = f"<b>{full_name}</b>:\n{plan['plan_text']}"
        else:
            text = f"<b>{full_name}</b>:\n<i>План на неделю не заполнен.</i>"

        await callback.message.answer(text)

    await callback.answer()