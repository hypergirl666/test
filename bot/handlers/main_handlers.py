import logging

from aiogram import F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import REPORT_SEND_TIME, TIME_CONSTANTS
from bot.core import router
from bot.core.database import (
    db_get_employee,
    db_get_last_report_date,
    db_get_team_by_id,
    db_get_team_by_manager,
    db_get_user_memberships,
    db_team_has_active_sprint,
    db_update_employee_team,
)
from bot.utils import (
    change_data_keyboard,
    manager_keyboard_with_invite,
    send_or_edit_message,
    team_action_keyboard,
    team_action_keyboard_for_manager,
)
from bot.utils.keyboards import (
    add_team_info_keyboard,
    choose_team_keyboard,
    employee_main_keyboard,
    manager_main_keyboard,
)


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext):
    """Обработчик команды /start. Разделяет логику для менеджеров и сотрудников."""
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} ({message.from_user.full_name}) нажал /start")

    # Проверяем, есть ли параметры в команде (пригласительная ссылка)
    if message.text.startswith('/start '):
        invite_code = message.text.split(' ')[1]
        logging.info(f"Обработка пригласительной ссылки для пользователя {user_id} с кодом: {invite_code}")
        from bot.handlers.team_handlers import handle_invite_code
        await handle_invite_code(message, state, invite_code)
        return

    await _handle_menu_logic(message, state)


async def _handle_menu_logic(event: Message | CallbackQuery, state: FSMContext):
    """Общая логика для показа меню (работает с Message и CallbackQuery)"""
    user_id = event.from_user.id

    # Показываем стандартное меню по последнему выбранному контексту
    memberships = await db_get_user_memberships(user_id)
    memberships_count = len(memberships) if memberships else 0
    employee = await db_get_employee(user_id)
    if employee and employee.get('team_id'):
        from bot.core.database import db_get_membership
        m = await db_get_membership(user_id, employee['team_id'])
        if m and m.get('is_manager'):
            await _show_manager_menu(event, await db_get_team_by_id(employee['team_id']))
            return
        else:
            await _show_employee_menu(event, employee)
            return
    # Нет выбранной команды: если есть хотя бы одна — выбираем первую
    if memberships_count >= 1:
        chosen = memberships[0]
        await db_update_employee_team(user_id, chosen['id'])
        await state.update_data(current_team_id=chosen['id'], current_is_manager=bool(chosen.get('is_manager')))
        if chosen.get('is_manager'):
            await _show_manager_menu(event, await db_get_team_by_id(chosen['id']))
        else:
            employee = await db_get_employee(user_id)
        await _show_employee_menu(event, employee)
        return
    # Новый пользователь
    await _show_new_user_menu(event, state)


async def _show_manager_menu(event: Message | CallbackQuery, team):
    """Показать меню менеджера"""
    # Используем единый шаблон для отображения информации о менеджере
    from bot.utils.text_constants import get_manager_info_template

    # Используем время отчетов из БД команды
    team_report_time = team['report_time']
    manager_info = await get_manager_info_template(team, team_report_time)

    try:
        user_id = event.from_user.id
    except Exception:
        user_id = getattr(getattr(event, 'message', None), 'chat', None).id if hasattr(event, 'message') else None
    from bot.core.database import db_get_user_memberships, db_get_membership
    count = 0
    is_po = False
    has_active_sprint,sprint_enabled = False,False
    if user_id:
        mm = await db_get_user_memberships(user_id)
        count = len(mm) if mm else 0
        # Проверяем, является ли менеджер PO в текущей команде
        if team:
            membership = await db_get_membership(user_id, team['id'])
            if membership and membership.get('is_po', False):
                is_po = True
    if team:
        sprint_enabled = team.get('sprint_enabled', False)
        has_active_sprint = await db_team_has_active_sprint(team['id'])

    await send_or_edit_message(event, manager_info, reply_markup=manager_main_keyboard(count,sprint_enabled, has_active_sprint, is_po=is_po), disable_web_page_preview=True)


@router.callback_query(F.data.startswith("choose_team_"))
async def handle_choose_team(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    try:
        team_id = int(callback.data.split('_')[-1])
    except Exception:
        await callback.answer("Некорректный выбор команды")
        return

    # Установить текущую команду и роль
    from bot.core.database import db_get_membership
    membership = await db_get_membership(user_id, team_id)
    if not membership:
        await callback.answer("Нет доступа к выбранной команде")
        return

    await db_update_employee_team(user_id, team_id)
    await state.update_data(current_team_id=team_id, current_is_manager=bool(membership.get('is_manager')))

    team = await db_get_team_by_id(team_id)
    if membership.get('is_manager'):
        from bot.core.database import db_get_user_memberships
        mm = await db_get_user_memberships(user_id)
        from bot.utils.text_constants import get_manager_info_template
        team_report_time = team['report_time']
        manager_info = await get_manager_info_template(team, team_report_time)
        # Проверяем, является ли менеджер PO в выбранной команде
        is_po = membership.get('is_po', False) if membership else False
        sprint_enabled = team.get('sprint_enabled', False)
        has_active_sprint = await db_team_has_active_sprint(team_id)
        await send_or_edit_message(
            callback,
            manager_info,
            reply_markup=manager_main_keyboard(len(mm or []), sprint_enabled, has_active_sprint,is_po=is_po),
            disable_web_page_preview=True
        )
    else:
        employee = await db_get_employee(user_id)
        from bot.core.database import db_get_user_memberships
        mm = await db_get_user_memberships(user_id)
        last_report_date = await db_get_last_report_date(employee["tg_id"]) if employee else None
        from bot.utils.text_constants import get_user_info_template
        user_info = await get_user_info_template(employee, last_report_date)
        # Проверяем, является ли пользователь PO в выбранной команде
        is_po = membership.get('is_po', False) if membership else False
        has_active_sprint = False
        if employee and employee.get("team_id"):
            has_active_sprint = await db_team_has_active_sprint(employee["team_id"])
        await send_or_edit_message(
            callback,
            user_info,
            reply_markup=employee_main_keyboard(len(mm or []), has_active_sprint, is_po=is_po)
        )
    await callback.answer()


@router.callback_query(F.data == "open_choose_team")
async def open_choose_team_callback(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    memberships = await db_get_user_memberships(user_id)
    if memberships and len(memberships) > 1:
        await send_or_edit_message(callback, "Выберите команду:", reply_markup=choose_team_keyboard(memberships))
    else:
        await callback.answer("У вас пока одна команда")
    await callback.answer()


@router.callback_query(F.data == "open_add_team")
async def open_add_team_callback(callback: CallbackQuery, state: FSMContext):
    text = (
        "Вы можете создать новую команду или присоединиться к существующей, запросив ссылку у менеджера.\n"
        "После этого вы сможете в любое время переключаться между командами для настройки."
    )
    await send_or_edit_message(callback, text, reply_markup=add_team_info_keyboard())
    await callback.answer()


@router.callback_query(F.data == "create_command_inline")
async def create_command_inline_callback(callback: CallbackQuery, state: FSMContext):
    from bot.handlers.team_handlers import create_team_callback
    await create_team_callback(callback, state)
    await callback.answer()


async def _show_employee_menu(event: Message | CallbackQuery, employee):
    """Показать меню сотрудника"""
    tg_id = employee["tg_id"]
    last_report_date = await db_get_last_report_date(tg_id)
    
    # Используем единый шаблон для отображения информации о пользователе
    from bot.utils.text_constants import get_user_info_template
    user_info = await get_user_info_template(employee, last_report_date)

    from bot.core.database import db_get_user_memberships, db_get_membership
    mm = await db_get_user_memberships(tg_id)
    count = len(mm) if mm else 0
    has_active_sprint = False
    if employee and employee.get("team_id"):
        has_active_sprint = await db_team_has_active_sprint(employee["team_id"])

    # Проверяем, является ли пользователь PO в текущей команде
    is_po = False
    if employee.get('team_id'):
        membership = await db_get_membership(tg_id, employee['team_id'])
        if membership and membership.get('is_po', False):
            is_po = True
    else:
        # Если нет текущей команды, проверяем все членства
        for m in mm or []:
            if m.get('is_po', False):
                is_po = True
                break

    await send_or_edit_message(event, user_info, reply_markup=employee_main_keyboard(count,has_active_sprint, is_po=is_po))


async def _show_new_user_menu(event: Message | CallbackQuery, state):
    """Показать меню нового пользователя"""
    from bot.utils.text_constants import get_welcome_message
    await send_or_edit_message(
        event,
        get_welcome_message(),
        reply_markup=team_action_keyboard()
    )
    from bot.core.states import TeamRegistration
    await state.set_state(TeamRegistration.choosing_action)


@router.callback_query(F.data == "go_to_menu")
async def go_to_menu_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик возврата в главное меню"""
    await state.clear()
    await _handle_menu_logic(callback, state)
    await callback.answer()


@router.message(Command("show_id"))
async def handle_show_id_private(message: Message):
    """Обработчик команды /show_id в личных сообщениях"""
    user_id = message.from_user.id
    logging.info(f"Команда /show_id вызвана в личном чате пользователем {user_id}")

    response_text = (
        f"🏷️ Ваш TGID: <code>{user_id}</code>\n\n"
        f"💡 <i>Эту команду можно использовать в группах, чтобы узнать TGID чата</i>"
    )
    
    await send_or_edit_message(message, response_text) 