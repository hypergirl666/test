import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import MAX_TEAM_NAME_LENGTH, REPORT_SEND_TIME
from bot.core import router
from bot.core.database import (
    db_get_team_by_id, 
    db_get_team_by_manager, 
    db_update_team_field,
    db_get_team_members,
    db_get_membership,
    db_get_employee,
    db_add_product_owner,
    db_remove_product_owner
)
from bot.core.states import TeamEdit, AddPO
from bot.utils import (
    cancel_keyboard,
    cancel_team_edit_keyboard,
    manager_keyboard_with_invite,
    send_or_edit_message,
    team_edit_keyboard,
)
from bot.utils.text_constants import (
    BOARD_LINK_HELP,
    CHAT_ID_FORMAT_ERROR,
    CHAT_ID_HELP,
    CHAT_ID_LENGTH_ERROR,
    CHAT_ID_REMOVE_HELP,
    TEAM_NOT_FOUND_ERROR,
    TOPIC_HELP,
    TOPIC_ID_FORMAT_ERROR,
    TOPIC_ID_LENGTH_ERROR,
    get_access_error_message,
)


async def _check_manager_access(user_id: int):
    """Проверить права доступа менеджера"""
    team = await db_get_team_by_manager(user_id)
    return team, team is None


@router.callback_query(F.data == "team_settings")
async def team_settings_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки настроек команды"""
    user_id = callback.from_user.id
    team, is_not_manager = await _check_manager_access(user_id)

    if is_not_manager:
        await callback.answer(get_access_error_message())
        return

    logging.info(f"Менеджер {user_id} команды '{team['name']}' открыл настройки команды")

    from bot.utils.text_constants import get_team_settings_template
    settings_info = get_team_settings_template(team)

    # Единый шаблон: одна пустая строка между блоками — используем ровно как возвращает шаблон
    await send_or_edit_message(
        callback,
        f"{settings_info}Выберите, что хотите изменить:",
        reply_markup=team_edit_keyboard(),
        disable_web_page_preview=True
    )
    await callback.answer()


@router.callback_query(F.data == "delete_team")
async def delete_team_callback(callback: CallbackQuery, state: FSMContext):
    """Подтверждение удаления команды"""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    user_id = callback.from_user.id
    team, is_not_manager = await _check_manager_access(user_id)
    if is_not_manager:
        await callback.answer(get_access_error_message())
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_delete_team")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="team_settings")]
    ])
    await send_or_edit_message(callback, f"Вы уверены, что хотите удалить команду '{team['name']}'?", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "confirm_delete_team")
async def confirm_delete_team_callback(callback: CallbackQuery, state: FSMContext):
    """Удаление команды"""
    user_id = callback.from_user.id
    team, is_not_manager = await _check_manager_access(user_id)
    if is_not_manager or not team:
        await callback.answer(get_access_error_message())
        return
    try:
        from bot.core.database import get_pool
        pool = await get_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                # Каскадно: удаляем memberships, инвайт, отчёты этой команды (reports.team_id), затем команду
                await cur.execute("DELETE FROM user_team_memberships WHERE team_id = %s", (team['id'],))
                await cur.execute("DELETE FROM team_invites WHERE team_id = %s", (team['id'],))
                await cur.execute("DELETE FROM reports WHERE team_id = %s", (team['id'],))
                # Снимаем текущую команду у сотрудников, чтобы не нарушить FK на employees.team_id
                await cur.execute("UPDATE employees SET team_id = NULL WHERE team_id = %s", (team['id'],))
                await cur.execute("DELETE FROM teams WHERE id = %s", (team['id'],))
            await conn.commit()
        await send_or_edit_message(callback, "✅ Команда удалена.")
        await state.clear()
    except Exception as e:
        logging.error(f"Не удалось удалить команду {team['id']}: {e}")
        await callback.answer("Ошибка при удалении команды")


@router.callback_query(F.data == "edit_team_name")
async def edit_team_name_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик редактирования названия команды"""
    await send_or_edit_message(
        callback.message,
        "✏️ <b>Изменение названия команды</b>\n\n"
        "Пожалуйста, <b>введите новое название команды</b>:",
        reply_markup=cancel_team_edit_keyboard()
    )
    await state.set_state(TeamEdit.waiting_for_new_team_name)
    await callback.answer()


@router.callback_query(F.data == "edit_chat_id")
async def edit_chat_id_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик редактирования ID чата"""
    await send_or_edit_message(
        callback.message,
        f"📱 <b>Изменение ID чата команды</b>\n\n"
        f"Пожалуйста, <b>введите новый ID чата</b> (например: -1001234567890):\n\n"
        f"{CHAT_ID_HELP}\n\n"
        f"{CHAT_ID_REMOVE_HELP}",
        reply_markup=cancel_team_edit_keyboard()
    )
    await state.set_state(TeamEdit.waiting_for_new_chat_id)
    await callback.answer()


@router.callback_query(F.data == "edit_chat_topic")
async def edit_chat_topic_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик редактирования ID топика"""
    await send_or_edit_message(
        callback.message,
        f"📋 <b>Изменение ID топика чата</b>\n\n"
        f"Пожалуйста, <b>введите новый ID топика</b>:\n\n"
        f"{TOPIC_HELP}",
        reply_markup=cancel_team_edit_keyboard()
    )
    await state.set_state(TeamEdit.waiting_for_new_chat_topic)
    await callback.answer()


@router.message(TeamEdit.waiting_for_new_team_name)
async def handle_new_team_name(message: Message, state: FSMContext):
    """Обработка нового названия команды"""
    new_name = message.text.strip()
    from bot.utils.text_constants import get_length_validation_message

    if len(new_name) < 2:
        await send_or_edit_message(
            message,
            get_length_validation_message("Название команды", min_length=2)
        )
        return
    elif len(new_name) > MAX_TEAM_NAME_LENGTH:
        await send_or_edit_message(
            message,
            get_length_validation_message("Название команды", max_length=MAX_TEAM_NAME_LENGTH)
        )
        return

    user_id = message.from_user.id
    team, is_not_manager = await _check_manager_access(user_id)
    if is_not_manager:
        await send_or_edit_message(message, TEAM_NOT_FOUND_ERROR)
        await state.clear()
        return

    await db_update_team_field(team['id'], 'name', new_name)

    from bot.utils.text_constants import get_data_updated_message
    await send_or_edit_message(
        message,
        get_data_updated_message(),
        reply_markup=manager_keyboard_with_invite()
    )
    await state.clear()


@router.message(TeamEdit.waiting_for_new_chat_id)
async def handle_new_chat_id(message: Message, state: FSMContext):
    """Обработка нового ID чата"""
    new_chat_id = message.text.strip()
    user_id = message.from_user.id
    team, is_not_manager = await _check_manager_access(user_id)
    if is_not_manager:
        await send_or_edit_message(message, TEAM_NOT_FOUND_ERROR)
        await state.clear()
        return

    if new_chat_id == '0':
        # Убираем чат
        await db_update_team_field(team['id'], 'chat_id', None)
        await db_update_team_field(team['id'], 'chat_topic_id', None)

        from bot.utils.text_constants import get_item_removed_message
        await send_or_edit_message(
            message,
            get_item_removed_message("Чат команды",
                                     "💡 <i>Отчёты будут отправляться только менеджеру в личные сообщения.</i>"),
            reply_markup=manager_keyboard_with_invite()
        )
        await state.clear()
        return

    # Проверяем, что это похоже на ID чата
    if not new_chat_id.startswith('-') or not new_chat_id[1:].isdigit():
        await send_or_edit_message(
            message,
            CHAT_ID_FORMAT_ERROR
        )
        return
    # Ограничение на длину: максимум 18 цифр в числовой части
    if len(new_chat_id[1:]) > 18:
        await send_or_edit_message(
            message,
            CHAT_ID_LENGTH_ERROR
        )
        return

    # Обновляем ID чата
    await db_update_team_field(team['id'], 'chat_id', new_chat_id)

    from bot.utils.text_constants import get_field_updated_message
    await send_or_edit_message(
        message,
        get_field_updated_message("ID чата команды", new_chat_id,
                                  f"💡 <i>Теперь вы можете настроить ID топика в настройках команды.</i>"),
        reply_markup=manager_keyboard_with_invite()
    )
    await state.clear()


@router.message(TeamEdit.waiting_for_new_chat_topic)
async def handle_new_chat_topic(message: Message, state: FSMContext):
    """Обработка нового ID топика"""
    new_topic_id = message.text.strip()
    user_id = message.from_user.id
    team, is_not_manager = await _check_manager_access(user_id)
    if is_not_manager:
        await send_or_edit_message(message, TEAM_NOT_FOUND_ERROR)
        await state.clear()
        return

    # Проверяем, что это целое число
    if new_topic_id != '0':
        try:
            int(new_topic_id)
        except ValueError:
            await send_or_edit_message(message, TOPIC_ID_FORMAT_ERROR)
            return
        # Ограничение на длину: максимум 18 цифр
        digits = new_topic_id.lstrip('-')
        if len(digits) > 18:
            await send_or_edit_message(message, TOPIC_ID_LENGTH_ERROR)
            return

    if new_topic_id == '0':
        new_topic_id = None

    # Обновляем ID топика
    await db_update_team_field(team['id'], 'chat_topic_id', new_topic_id)

    from bot.utils.text_constants import get_field_updated_message
    await send_or_edit_message(
        message,
        get_field_updated_message("ID топика чата", new_topic_id or 'Не используется'),
        reply_markup=manager_keyboard_with_invite()
    )
    await state.clear()


@router.callback_query(F.data == "edit_board_link")
async def edit_board_link_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик редактирования ссылки на доску"""
    from bot.utils.keyboards import board_link_edit_keyboard
    await send_or_edit_message(
        callback.message,
        "📋 <b>Изменение ссылки на доску команды</b>\n\n"
        "Пожалуйста, <b>введите новую ссылку на доску</b>:\n\n" +
        BOARD_LINK_HELP,
        reply_markup=board_link_edit_keyboard()
    )
    await state.set_state(TeamEdit.waiting_for_new_board_link)
    await callback.answer()


@router.message(TeamEdit.waiting_for_new_board_link)
async def handle_new_board_link(message: Message, state: FSMContext):
    """Обработка новой ссылки на доску команды"""
    user_id = message.from_user.id
    team, is_not_manager = await _check_manager_access(user_id)

    if is_not_manager:
        await send_or_edit_message(message, get_access_error_message())
        return

    new_board_link = message.text.strip()

    # Ввод пустой строки не принимаем: просим ввести корректную ссылку или нажать «Назад»/«Удалить»
    if new_board_link.strip() == "":
        from bot.utils.keyboards import board_link_edit_keyboard
        from bot.utils.text_constants import get_error_message
        await send_or_edit_message(
            message,
            get_error_message("invalid_link"),
            reply_markup=board_link_edit_keyboard()
        )
        return

    from bot.utils.text_constants import get_error_message

    # Простая валидация ссылки
    if not new_board_link.startswith(('http://', 'https://')):
        await send_or_edit_message(
            message,
            get_error_message("invalid_link")
        )
        return

    # Нормализуем GitVerse ссылку, если это tasktracker
    try:
        from bot.utils.utils import normalize_gitverse_board_link
        new_board_link = normalize_gitverse_board_link(new_board_link)
    except Exception:
        pass

    await db_update_team_field(team['id'], 'board_link', new_board_link)

    from bot.utils.text_constants import get_team_settings_template

    # Получим актуальные данные команды
    fresh_team = await db_get_team_by_id(team['id'])
    settings_info = get_team_settings_template(fresh_team, "Обновленные настройки команды")

    await send_or_edit_message(
        message,
        f"✅ <b>Ссылка на доску обновлена!</b>\n\n{settings_info}\n\nВыберите, что хотите изменить:",
        reply_markup=team_edit_keyboard(),
        disable_web_page_preview=True
    )
    await state.clear()


@router.callback_query(F.data == "delete_board_link")
async def delete_board_link_callback(callback: CallbackQuery, state: FSMContext):
    """Удаление ссылки на доску по кнопке"""
    user_id = callback.from_user.id
    team, is_not_manager = await _check_manager_access(user_id)
    if is_not_manager:
        await callback.answer(get_access_error_message())
        return

    await db_update_team_field(team['id'], 'board_link', None)

    from bot.utils.text_constants import get_team_settings_template
    fresh_team = await db_get_team_by_id(team['id'])
    settings_info = get_team_settings_template(fresh_team, "Обновленные настройки команды")

    await send_or_edit_message(
        callback.message,
        f"✅ <b>Ссылка на доску удалена!</b>\n\n{settings_info}\n\nВыберите, что хотите изменить:",
        reply_markup=team_edit_keyboard(),
        disable_web_page_preview=True
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "edit_timezone")
async def edit_timezone_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик смены часового пояса команды"""
    user_id = callback.from_user.id
    team, is_not_manager = await _check_manager_access(user_id)
    if is_not_manager:
        await callback.answer(get_access_error_message("смены часового пояса"))
        return

    # Предложим выбор МСК/ЕКБ
    from bot.utils.keyboards import timezone_selection_keyboard
    try:
        current_label = 'МСК' if (team['timezone'] == 'Europe/Moscow') else 'ЕКБ'
    except Exception:
        current_label = 'ЕКБ'
    await send_or_edit_message(
        callback.message,
        f"🌍 <b>Часовой пояс команды</b>\n\nТекущий: <b>{current_label}</b>\n\nВыберите новый часовой пояс:",
        reply_markup=timezone_selection_keyboard()
    )
    await state.set_state(TeamEdit.waiting_for_timezone)
    await callback.answer()


@router.callback_query(TeamEdit.waiting_for_timezone, F.data.startswith("set_timezone_"))
async def handle_new_timezone(callback: CallbackQuery, state: FSMContext):
    """Сохранение нового часового пояса команды"""
    user_id = callback.from_user.id
    team, is_not_manager = await _check_manager_access(user_id)
    if is_not_manager:
        await callback.answer(get_access_error_message("смены часового пояса"))
        return

    choice = callback.data.split("_")[-1]
    if choice == 'msk':
        tz_value = 'Europe/Moscow'
        tz_label = 'МСК'
    else:
        tz_value = 'Asia/Yekaterinburg'
        tz_label = 'ЕКБ'

    await db_update_team_field(team['id'], 'timezone', tz_value)

    # Обновляем задачи планировщика для команды
    try:
        from bot.utils.scheduler_manager import update_team_scheduler
        await update_team_scheduler(team['id'])
    except Exception as e:
        logging.error(f"Не удалось обновить планировщик после смены TZ для команды {team['id']}: {e}")

    from bot.utils.text_constants import get_team_settings_template

    # Получим свежую команду для отображения актуальных данных
    fresh_team = await db_get_team_by_id(team['id'])
    settings_info = get_team_settings_template(fresh_team, "Обновленные настройки команды")

    await send_or_edit_message(
        callback.message,
        f"✅ <b>Часовой пояс обновлён на {tz_label}</b>\n\n{settings_info}\n\nВыберите, что хотите изменить:",
        reply_markup=team_edit_keyboard(),
        disable_web_page_preview=True
    )
    await state.clear()
    await callback.answer()

# Обработчик отмены удален - теперь используется универсальный обработчик в cancel_handlers.py


async def _show_po_management_menu(callback: CallbackQuery, state: FSMContext, team, success_message: str = None):
    """Вспомогательная функция для отображения меню управления PO"""
    # Получаем список всех сотрудников команды (исключаем только менеджеров, которые не участвуют в опросах)
    employees = await db_get_team_members(team['id'])
    employees = [
        emp for emp in employees 
        if not (emp.get('is_manager') and not emp.get('is_participant', False))
    ]

    if not employees:
        await send_or_edit_message(
            callback,
            "❌ В команде нет сотрудников для управления PO."
        )
        return

    # Создаем клавиатуру со списком сотрудников
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    buttons = []
    po_count = 0
    for emp in employees:
        full_name = emp.get('full_name', 'Неизвестно')
        role = emp.get('role', '')
        is_po = emp.get('is_po', False)
        
        if is_po:
            po_count += 1
            display_text = f"✅ {full_name}" + (f" ({role})" if role else "") + " - PO"
            callback_data = f"remove_po_{emp['tg_id']}"
        else:
            display_text = f"{full_name}" + (f" ({role})" if role else "")
            callback_data = f"select_po_{emp['tg_id']}"
        
        buttons.append([
            InlineKeyboardButton(
                text=display_text,
                callback_data=callback_data
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="team_settings")])

    po_text = f"\n\n<b>Текущие PO:</b> {po_count}" if po_count > 0 else ""
    
    message_text = f"👤 <b>Управление Product Owner</b>\n\n"
    if success_message:
        message_text += f"{success_message}\n\n"
    message_text += f"Выберите сотрудника из команды '{team['name']}':{po_text}\n\n"
    message_text += f"• Сотрудники с ✅ уже являются PO (можно убрать права)\n"
    message_text += f"• Остальных сотрудников можно назначить PO"
    
    await send_or_edit_message(
        callback,
        message_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await state.set_state(AddPO.selecting_employee)
    await state.update_data(team_id=team['id'])


@router.callback_query(F.data == "add_po")
async def add_po_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки добавления/удаления PO"""
    user_id = callback.from_user.id
    team, is_not_manager = await _check_manager_access(user_id)

    if is_not_manager:
        await callback.answer(get_access_error_message("управления PO"))
        return

    logging.info(f"Менеджер {user_id} команды '{team['name']}' открыл меню управления PO")

    await _show_po_management_menu(callback, state, team)
    await callback.answer()


@router.callback_query(F.data.startswith("select_po_"), AddPO.selecting_employee)
async def select_po_employee_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора сотрудника для назначения PO"""
    user_id = callback.from_user.id
    team, is_not_manager = await _check_manager_access(user_id)

    if is_not_manager:
        await callback.answer(get_access_error_message("добавления PO"))
        await state.clear()
        return

    try:
        employee_tg_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("❌ Некорректный запрос")
        await state.clear()
        return

    state_data = await state.get_data()
    team_id = state_data.get('team_id')

    if not team_id or team_id != team['id']:
        await callback.answer("❌ Ошибка: неверная команда")
        await state.clear()
        return

    # Проверяем, что сотрудник действительно в команде и не является уже PO
    membership = await db_get_membership(employee_tg_id, team_id)
    if not membership:
        await callback.answer("❌ Сотрудник не найден в команде")
        await state.clear()
        return

    if membership.get('is_po', False):
        await callback.answer("❌ Этот сотрудник уже является PO")
        await state.clear()
        return

    # Получаем информацию о сотруднике
    employee = await db_get_employee(employee_tg_id)
    employee_name = employee.get('full_name', 'Неизвестно') if employee else 'Неизвестно'

    # Добавляем PO
    try:
        await db_add_product_owner(employee_tg_id, team_id)
        logging.info(f"Менеджер {user_id} назначил сотрудника {employee_tg_id} PO в команде {team_id}")

        success_message = f"✅ <b>Product Owner добавлен!</b>\n\nСотрудник <b>{employee_name}</b> теперь является Product Owner команды '{team['name']}'.\n\nУ него появилась кнопка 'Product Owner' в меню."
        
        # Обновляем меню управления PO вместо возврата в настройки
        await _show_po_management_menu(callback, state, team, success_message)
    except Exception as e:
        logging.error(f"Ошибка при добавлении PO: {e}")
        await callback.answer("❌ Ошибка при добавлении PO")
    
    await callback.answer()


@router.callback_query(F.data.startswith("remove_po_"), AddPO.selecting_employee)
async def remove_po_employee_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик удаления PO у сотрудника"""
    user_id = callback.from_user.id
    team, is_not_manager = await _check_manager_access(user_id)

    if is_not_manager:
        await callback.answer(get_access_error_message("удаления PO"))
        await state.clear()
        return

    try:
        employee_tg_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("❌ Некорректный запрос")
        await state.clear()
        return

    state_data = await state.get_data()
    team_id = state_data.get('team_id')

    if not team_id or team_id != team['id']:
        await callback.answer("❌ Ошибка: неверная команда")
        await state.clear()
        return

    # Проверяем, что сотрудник действительно в команде и является PO
    membership = await db_get_membership(employee_tg_id, team_id)
    if not membership:
        await callback.answer("❌ Сотрудник не найден в команде")
        await state.clear()
        return

    if not membership.get('is_po', False):
        await callback.answer("❌ Этот сотрудник не является PO")
        await state.clear()
        return

    # Получаем информацию о сотруднике
    employee = await db_get_employee(employee_tg_id)
    employee_name = employee.get('full_name', 'Неизвестно') if employee else 'Неизвестно'

    # Удаляем PO
    try:
        await db_remove_product_owner(employee_tg_id, team_id)
        logging.info(f"Менеджер {user_id} убрал права PO у сотрудника {employee_tg_id} в команде {team_id}")

        success_message = f"✅ <b>Права Product Owner убраны!</b>\n\nСотрудник <b>{employee_name}</b> больше не является Product Owner команды '{team['name']}'.\n\nКнопка 'Product Owner' исчезнет из его меню."
        
        # Обновляем меню управления PO вместо возврата в настройки
        await _show_po_management_menu(callback, state, team, success_message)
    except Exception as e:
        logging.error(f"Ошибка при удалении PO: {e}")
        await callback.answer("❌ Ошибка при удалении PO")
    
    await callback.answer() 