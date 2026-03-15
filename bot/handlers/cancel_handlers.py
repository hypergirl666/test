import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.core import router
from bot.core.database import db_get_employee, db_get_last_report_date, db_get_team_by_manager
from bot.utils import send_or_edit_message
from bot.utils.keyboards import (
    change_data_keyboard,
    manager_keyboard_with_invite,
    team_action_keyboard,
    team_action_keyboard_for_manager,
    what_to_change_keyboard,
)
from bot.utils.text_constants import get_welcome_message


async def _handle_manager_cancel(callback: CallbackQuery, team):
    """Обработка отмены для менеджера - возврат в меню менеджера"""
    from bot.handlers.main_handlers import _show_manager_menu
    await _show_manager_menu(callback, team)


async def _handle_employee_cancel(callback: CallbackQuery, employee):
    """Обработка отмены для сотрудника - возврат в меню сотрудника"""
    from bot.handlers.main_handlers import _show_employee_menu
    await _show_employee_menu(callback, employee)


async def _handle_new_user_cancel(callback: CallbackQuery, state):
    """Обработка отмены для нового пользователя - возврат в главное меню"""
    await send_or_edit_message(
        callback.message,
        get_welcome_message(),
        reply_markup=team_action_keyboard()
    )
    from bot.core.states import TeamRegistration
    await state.set_state(TeamRegistration.choosing_action)


async def _handle_team_settings_cancel(callback: CallbackQuery, team):
    """Обработка отмены в настройках команды - возврат к настройкам команды"""
    from bot.utils.text_constants import get_team_settings_template
    settings_info = get_team_settings_template(team)

    from bot.utils.keyboards import team_edit_keyboard

    # Единый шаблон и одна пустая строка — текст из шаблона + хвост без лишних переносов
    await send_or_edit_message(
        callback.message,
        f"{settings_info}Выберите, что хотите изменить:",
        reply_markup=team_edit_keyboard(),
        disable_web_page_preview=True
    )


async def _handle_employee_settings_cancel(callback: CallbackQuery, state: FSMContext, employee):
    """Возврат к списку настроек сотрудника с цитатой"""
    from bot.core.states import ChangeData

    # Формируем цитату с данными пользователя
    try:
        last_report_date = await db_get_last_report_date(employee["tg_id"])
        from bot.utils.text_constants import get_user_info_quote
        quote = await get_user_info_quote(employee, last_report_date)
    except Exception:
        quote = ""

    settings_header = "Что вы хотите изменить?"
    text = f"{quote}\n{settings_header}" if quote else settings_header

    # Выводим кнопку изменения GitVerse ника, если у команды есть GitVerse-доска
    include_gitverse = False
    try:
        if employee and employee['team_id']:
            from bot.core.database import db_get_team_by_id
            team = await db_get_team_by_id(employee['team_id'])
            if team and team['board_link']:
                from bot.utils.utils import is_gitverse_board_link
                include_gitverse = is_gitverse_board_link(team['board_link'])
    except Exception:
        include_gitverse = False

    await send_or_edit_message(
        callback.message,
        text,
        reply_markup=what_to_change_keyboard(include_gitverse)
    )
    await state.set_state(ChangeData.choosing_field)


async def _determine_cancel_action(user_id: int, current_state: str, state_data: dict | None):
    """
    Определяет какое действие отмены нужно выполнить на основе текущего состояния UI и выбранной роли
    
    Returns:
        tuple: (action_type, additional_data)
        action_type: 'manager_menu', 'employee_menu', 'employee_settings', 'new_user', 'team_settings'
        additional_data: team или employee объект, или None
    """
    # 1) Приоритет: если мы в экранах настроек сотрудника — ведём себя как сотрудник, даже если пользователь менеджер в другой команде
    if current_state:
        # Корневой экран настроек сотрудника
        if ("ChangeData" in current_state) and ("choosing_field" in current_state):
            employee = await db_get_employee(user_id)
            logging.info(f"Отмена на корневом экране настроек для сотрудника {user_id} — возврат в меню сотрудника")
            return 'employee_menu', employee
        # Вложенные шаги изменения данных/отпуска
        if ("ChangeData" in current_state) or ("Vacation" in current_state):
            employee = await db_get_employee(user_id)
            logging.info(
                f"Отмена во вложенном шаге настроек для сотрудника {user_id} — возврат к списку настроек сотрудника")
            return 'employee_settings', employee
        # Экран настроек времени команды — остаёмся в настройках команды
        if 'TeamTimeSettings' in current_state:
            team = await db_get_team_by_manager(user_id)
            if team:
                logging.info(f"Отмена в настройках времени команды {team['name']} — возврат к настройкам команды")
                return 'team_settings', team
        # Ввод плана спринта — возврат в меню сотрудника
        if 'SprintPlan' in current_state:
            employee = await db_get_employee(user_id)
            logging.info(f"Отмена ввода плана спринта для сотрудника {user_id} — возврат в меню сотрудника")
            return 'employee_menu', employee

    # 2) Если в состоянии хранится выбранный режим — используем его
    is_manager_ctx = None
    try:
        if state_data and isinstance(state_data, dict):
            is_manager_ctx = state_data.get('current_is_manager')
    except Exception:
        is_manager_ctx = None
    if is_manager_ctx is True:
        team = await db_get_team_by_manager(user_id)
        if team:
            logging.info(f"Отмена: выбран режим менеджера — возврат в меню менеджера")
            return 'manager_menu', team
    if is_manager_ctx is False:
        employee = await db_get_employee(user_id)
        if employee:
            logging.info(f"Отмена: выбран режим сотрудника — возврат в меню сотрудника")
            return 'employee_menu', employee

    # 3) Фоллбек: определяем по текущим данным в БД
    team = await db_get_team_by_manager(user_id)
    if team:
        logging.info(f"Отмена (фоллбек) — пользователь менеджер, возврат в меню менеджера")
        return 'manager_menu', team

    # Проверяем, является ли пользователь сотрудником
    employee = await db_get_employee(user_id)
    if employee:
        logging.info(f"Отмена (фоллбек) — пользователь сотрудник, возврат в меню сотрудника")
        return 'employee_menu', employee

    # Если пользователь новый
    logging.info(f"Отмена для нового пользователя {user_id} - возврат в главное меню")
    return 'new_user', None


async def _execute_cancel_action(callback: CallbackQuery, state: FSMContext, action_type: str, additional_data):
    """Выполняет соответствующее действие отмены"""
    if action_type == 'manager_menu':
        await _handle_manager_cancel(callback, additional_data)
    elif action_type == 'employee_menu':
        await _handle_employee_cancel(callback, additional_data)
    elif action_type == 'employee_settings':
        await _handle_employee_settings_cancel(callback, state, additional_data)
    elif action_type == 'new_user':
        await _handle_new_user_cancel(callback, state)
    elif action_type == 'team_settings':
        await _handle_team_settings_cancel(callback, additional_data)


@router.callback_query(F.data == "cancel_action")
async def universal_cancel_handler(callback: CallbackQuery, state: FSMContext):
    """Универсальный обработчик отмены, который анализирует контекст и возвращает в соответствующее меню"""
    user_id = callback.from_user.id
    current_state = await state.get_state()
    state_data = await state.get_data()

    logging.info(f"Нажата кнопка 'Отмена' пользователем {user_id} в состоянии: {current_state}")

    # Определяем какое действие нужно выполнить
    action_type, additional_data = await _determine_cancel_action(user_id, current_state, state_data)

    # Выполняем соответствующее действие
    await _execute_cancel_action(callback, state, action_type, additional_data)

    # Чистим состояние, только если не остаёмся внутри экрана настроек сотрудника
    if action_type in ('manager_menu', 'employee_menu', 'new_user', 'team_settings'):
        await state.clear()

    logging.info(f"Отмена выполнена для пользователя {user_id} - действие: {action_type}")
    await callback.answer()


@router.callback_query(F.data == "cancel_team_edit")
async def cancel_team_edit_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик отмены редактирования команды - возврат в главное меню менеджера"""
    user_id = callback.from_user.id

    logging.info(f"Нажата кнопка 'Отмена редактирования команды' пользователем {user_id}")

    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        logging.warning(f"Пользователь {user_id} попытался отменить редактирование команды, но не является менеджером")
        await send_or_edit_message(
            callback.message,
            "❌ У вас нет команды для настройки."
        )
        await callback.answer()
        return

    # Очищаем состояние
    await state.clear()

    # Возвращаемся в главное меню менеджера
    logging.info(
        f"Отмена редактирования команды для менеджера {user_id} (команда: {team['name']}) - возврат в главное меню")
    await _handle_manager_cancel(callback, team)
    await callback.answer()


@router.callback_query(F.data == "cancel_team_edit_action")
async def cancel_team_edit_action_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Назад' в настройках команды - возврат к меню настроек команды"""
    user_id = callback.from_user.id

    logging.info(f"Нажата кнопка 'Назад' в настройках команды пользователем {user_id}")

    # Проверяем, является ли пользователь менеджером
    team = await db_get_team_by_manager(user_id)
    if not team:
        logging.warning(f"Пользователь {user_id} попытался вернуться в настройки команды, но не является менеджером")
        await send_or_edit_message(
            callback.message,
            "❌ У вас нет команды для настройки."
        )
        await callback.answer()
        return

    # Очищаем состояние
    await state.clear()

    # Возвращаемся к настройкам команды
    logging.info(f"Возврат к настройкам команды для менеджера {user_id} (команда: {team['name']})")
    await _handle_team_settings_cancel(callback, team)
    await callback.answer()
