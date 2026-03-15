import logging
from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import ERROR_MESSAGES, EVENING_DB_VALUE, MAX_NAME_LENGTH, MAX_ROLE_LENGTH, MORNING_DB_VALUE
from bot.core import ChangeData, Vacation, router
from bot.core.database import (
    db_get_employee,
    db_get_last_report_date,
    db_get_team_by_id,
    db_update_employee_field,
    db_update_vacation,
)
from bot.utils import (
    back_to_employee_settings_keyboard,
    daily_time_keyboard,
    get_error_message_for_expected_text,
    menu_inline_keyboard,
    parse_date_flexible,
    role_selection_keyboard,
    send_or_edit_message,
    validate_and_format_name,
    validate_max_length,
    validate_text_message,
    what_to_change_keyboard,
)

# Константы для сообщений
VACATION_DATE_FORMAT_HELP = "Введите дату в любом из форматов:\n• ДД-ММ-ГГГГ (например: 15-01-2025)\n• ДД.ММ.ГГГГ (например: 15.01.2025)\n• ДД ММ ГГГГ (например: 15 01 2025)"
VACATION_END_PROMPT = "Введите дату окончания отпуска в формате:\n• ДД-ММ-ГГГГ (например: 25-01-2025)"
VACATION_DATE_ERROR = "Некорректный формат даты. Используйте формат:\n• ДД-ММ-ГГГГ (например: 15-01-2025)"
VACATION_END_ERROR = "Дата окончания отпуска не может быть раньше даты начала. Введите корректную дату окончания:"
from bot.utils.text_constants import get_error_message

VACATION_DATE_PARSE_ERROR = get_error_message("date")


# --- Флоу изменения данных ---
@router.callback_query(F.data == "change_data_start")
async def start_changing_data(callback: CallbackQuery, state: FSMContext):
    """Начало процесса изменения данных сотрудника"""
    user_id = callback.from_user.id
    logging.info(f"Пользователь {user_id} начал изменение данных")

    # Добавляем цитату с данными пользователя
    employee = await db_get_employee(user_id)
    quote = ""
    if employee:
        last_report_date = await db_get_last_report_date(employee["tg_id"])
        from bot.utils.text_constants import get_user_info_quote
        quote = await get_user_info_quote(employee, last_report_date)

    settings_header = "Что вы хотите изменить?"
    text = f"{quote}\n{settings_header}" if quote else settings_header

    # Если у команды настроена GitVerse доска — показываем пункт изменения ника
    include_gitverse = False
    if employee and employee['team_id']:
        try:
            team = await db_get_team_by_id(employee['team_id'])
            if team and team['board_link']:
                from bot.utils.utils import is_gitverse_board_link
                include_gitverse = is_gitverse_board_link(team['board_link'])
        except Exception:
            include_gitverse = False

    await send_or_edit_message(callback, text, reply_markup=what_to_change_keyboard(include_gitverse, include_leave=True))
    await state.set_state(ChangeData.choosing_field)
    await callback.answer()


@router.callback_query(F.data.startswith("change_field_"))
async def choose_field_to_change(callback: CallbackQuery, state: FSMContext):
    """Выбор поля для изменения"""
    field = callback.data[len("change_field_"):]
    user_id = callback.from_user.id
    logging.info(f"Пользователь {user_id} выбрал для изменения поле: {field}")
    
    await state.update_data(field_to_change=field)

    if field == "daily_time":
        # Получаем настройки команды для отображения времени
        team_settings = None
        employee = await db_get_employee(callback.from_user.id)
        if employee and employee['team_id']:
            team = await db_get_team_by_id(employee['team_id'])
            if team:
                team_settings = team
        
        await send_or_edit_message(callback, "Выберите новое время дейли:", reply_markup=daily_time_keyboard(team_settings, include_back=True))
        await state.set_state(ChangeData.entering_new_value)
    elif field == "vacation":
        await send_or_edit_message(callback, VACATION_DATE_FORMAT_HELP, reply_markup=back_to_employee_settings_keyboard())
        await state.set_state(Vacation.waiting_for_start)
    elif field == "role":
        await send_or_edit_message(callback, "Выберите новую роль или отправьте в сообщении кастомную роль:", reply_markup=role_selection_keyboard(include_back=True))
        await state.set_state(ChangeData.entering_new_value)
    else:
        field_map = {"full_name": "Имя", "gitverse_nickname": "Ник гитвёрс"}
        from bot.utils.text_constants import get_field_input_message
        await send_or_edit_message(callback, get_field_input_message(field_map.get(field, field)), reply_markup=back_to_employee_settings_keyboard())
        await state.set_state(ChangeData.entering_new_value)
    await callback.answer()


@router.callback_query(F.data == "leave_team")
async def leave_team_callback(callback: CallbackQuery, state: FSMContext):
    """Подтверждение выхода из текущей команды"""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, выйти", callback_data="confirm_leave_team")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="go_to_menu")]
    ])
    await send_or_edit_message(callback, "Вы действительно хотите выйти из текущей команды?", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "confirm_leave_team")
async def confirm_leave_team_callback(callback: CallbackQuery, state: FSMContext):
    """Удалить membership пользователя из текущей команды"""
    user_id = callback.from_user.id
    employee = await db_get_employee(user_id)
    team_id = employee['team_id'] if employee else None
    if not team_id:
        await callback.answer("Команда не выбрана")
        return
    try:
        from bot.core.database import db_remove_membership, db_update_employee_team
        await db_remove_membership(user_id, team_id)
        # Сбрасываем текущую команду, чтобы не остаться в контексте удалённой
        await db_update_employee_team(user_id, None)
        await send_or_edit_message(callback, "✅ Вы вышли из команды.", reply_markup=menu_inline_keyboard())
        await state.clear()
    except Exception as e:
        logging.error(f"Не удалось выйти из команды {team_id} пользователю {user_id}: {e}")
        await callback.answer("Ошибка при выходе из команды")


 


 


@router.message(ChangeData.entering_new_value)
async def process_new_value(message: Message, state: FSMContext):
    """Обработка нового значения для поля сотрудника"""
    user_data = await state.get_data()
    field = user_data.get('field_to_change')

    if not field or field == 'vacation':
        from bot.utils.text_constants import get_error_start_again_message
        await send_or_edit_message(message, get_error_start_again_message())
        await state.clear()
        return

    # Проверка на текстовое сообщение
    if not validate_text_message(message):
        context_map = {'full_name': 'full_name', 'role': 'role', 'gitverse_nickname': 'role'}
        context = context_map.get(field, field)
        await send_or_edit_message(message, get_error_message_for_expected_text(context))
        return

    new_value = message.text
    # Специальная обработка для имени
    if field == 'full_name':
        if not validate_max_length(new_value, MAX_NAME_LENGTH):
            await send_or_edit_message(message, ERROR_MESSAGES['name_too_long'])
            return
        
        # Валидация и форматирование имени
        is_valid, formatted_name = validate_and_format_name(new_value)
        if not is_valid:
            await send_or_edit_message(message, ERROR_MESSAGES['invalid_name_format'])
            return
        
        new_value = formatted_name
        user_id = message.from_user.id
        logging.info(f"Пользователь {user_id} изменил {field} на: {message.text} -> {new_value}")
    else:
        if field == 'role' and not validate_max_length(new_value, MAX_ROLE_LENGTH):
            await send_or_edit_message(message, ERROR_MESSAGES['role_too_long'])
            return
        # Специальная обработка ника GitVerse: пустые/отрицательные значения стирают, иначе просто сохраняем
        if field == 'gitverse_nickname':
            nv = (new_value or '').strip()
            if nv in ['-', '—', 'нет', 'Нет', 'no', 'No', '']:
                new_value = None

        user_id = message.from_user.id
        logging.info(f"Пользователь {user_id} изменил {field} на: {new_value}")
    
    await db_update_employee_field(message.from_user.id, field, new_value)
    from bot.utils.text_constants import get_data_updated_message
    await send_or_edit_message(
        message,
        get_data_updated_message(),
        reply_markup=menu_inline_keyboard()
    )
    await state.clear()


@router.callback_query(ChangeData.entering_new_value, F.data.startswith("set_time_"))
async def process_new_time_value(callback: CallbackQuery, state: FSMContext):
    """Обработка изменения времени дейли"""
    user_data = await state.get_data()
    field = user_data.get('field_to_change')

    if field != 'daily_time':
        from bot.utils.text_constants import get_error_start_again_message
        await send_or_edit_message(callback, get_error_start_again_message())
        await state.clear()
        await callback.answer()
        return

    # Получаем время из callback_data и проверяем, что это правильное значение
    time_from_callback = callback.data.split('_')[-1]
    
    # Проверяем, что время соответствует нашим константам
    if time_from_callback == MORNING_DB_VALUE:
        new_time = MORNING_DB_VALUE
    elif time_from_callback == EVENING_DB_VALUE:
        new_time = EVENING_DB_VALUE
    else:
        # Если получили старое значение, используем утреннее время по умолчанию
        new_time = MORNING_DB_VALUE
        logging.warning(f"Получено неожиданное время из callback: {time_from_callback}, используем {MORNING_DB_VALUE}")
    
    user_id = callback.from_user.id
    logging.info(f"Пользователь {user_id} изменил время дейли на: {new_time}")
    
    await db_update_employee_field(callback.from_user.id, 'daily_time', new_time)
    
    # Получаем время команды для отображения
    display_time = ""
    employee = await db_get_employee(callback.from_user.id)
    if employee and employee['team_id']:
        from bot.core.database import db_get_team_by_id
        team = await db_get_team_by_id(employee['team_id'])
        if team:
            if new_time == MORNING_DB_VALUE:
                display_time = team['morning_time']
            else:
                display_time = team['evening_time']
    
    from bot.utils.text_constants import get_field_updated_message
    await send_or_edit_message(
        callback.message,
        get_field_updated_message("Время дейли", display_time, "Вы всегда можете вернуться в главное меню."),
        reply_markup=menu_inline_keyboard()
    )
    await state.clear()
    await callback.answer()


@router.callback_query(ChangeData.entering_new_value, F.data.startswith("set_role_"))
async def process_new_role_value(callback: CallbackQuery, state: FSMContext):
    """Обработка изменения роли"""
    user_data = await state.get_data()
    field = user_data.get('field_to_change')

    if field != 'role':
        from bot.utils.text_constants import get_error_start_again_message
        await send_or_edit_message(callback, get_error_start_again_message())
        await state.clear()
        await callback.answer()
        return

    role_data = callback.data.split('_', 2)[2]  # Получаем роль из callback_data
    user_id = callback.from_user.id
    logging.info(f"Пользователь {user_id} изменил роль на: {role_data}")
    
    await db_update_employee_field(callback.from_user.id, 'role', role_data)
    from bot.utils.text_constants import get_data_updated_message
    await send_or_edit_message(
        callback.message,
        get_data_updated_message(),
        reply_markup=menu_inline_keyboard()
    )
    await state.clear()
    await callback.answer()


@router.message(ChangeData.entering_new_value)
async def process_new_value(message: Message, state: FSMContext):
    """Обработка нового значения для поля сотрудника"""
    user_data = await state.get_data()
    field = user_data.get('field_to_change')

    if not field or field == 'vacation':
        from bot.utils.text_constants import get_error_start_again_message
        await send_or_edit_message(message, get_error_start_again_message())
        await state.clear()
        return

    # Проверка на текстовое сообщение
    if not validate_text_message(message):
        context_map = {'full_name': 'full_name', 'role': 'role'}
        context = context_map.get(field, field)
        await send_or_edit_message(message, get_error_message_for_expected_text(context))
        return

    new_value = message.text
    # Специальная обработка для имени
    if field == 'full_name':
        if not validate_max_length(new_value, MAX_NAME_LENGTH):
            await send_or_edit_message(message, ERROR_MESSAGES['name_too_long'])
            return
        
        # Валидация и форматирование имени
        is_valid, formatted_name = validate_and_format_name(new_value)
        if not is_valid:
            await send_or_edit_message(message, ERROR_MESSAGES['invalid_name_format'])
            return
        
        new_value = formatted_name
        user_id = message.from_user.id
        logging.info(f"Пользователь {user_id} изменил {field} на: {message.text} -> {new_value}")
    else:
        # Обработка роли
        if field == 'role':
            if not validate_max_length(new_value, MAX_ROLE_LENGTH):
                await send_or_edit_message(message, ERROR_MESSAGES['role_too_long'])
                return
            
            user_id = message.from_user.id
            logging.info(f"Пользователь {user_id} изменил роль на: {new_value}")
            
            await db_update_employee_field(message.from_user.id, 'role', new_value)
            from bot.utils.text_constants import get_data_updated_message
            await send_or_edit_message(
                message,
                get_data_updated_message(),
                reply_markup=menu_inline_keyboard()
            )
            await state.clear()
            return
        else:
            # Роль теперь обрабатывается через кнопки, поэтому здесь только имя, а также GitVerse ник
            if field == 'gitverse_nickname':
                nv = (new_value or '').strip()
                if nv in ['-', '—', 'нет', 'Нет', 'no', 'No', '']:
                    new_value = None
            user_id = message.from_user.id
            logging.info(f"Пользователь {user_id} изменил {field} на: {new_value}")
    
    await db_update_employee_field(message.from_user.id, field, new_value)
    from bot.utils.text_constants import get_data_updated_message
    await send_or_edit_message(
        message,
        get_data_updated_message(),
        reply_markup=menu_inline_keyboard()
    )
    await state.clear()


# --- Vacation flow ---
@router.message(Vacation.waiting_for_start)
async def vacation_set_start(message: Message, state: FSMContext):
    """Обработка даты начала отпуска"""
    if not validate_text_message(message):
        await send_or_edit_message(message, get_error_message_for_expected_text('vacation_start'))
        return
    
    parsed_date = parse_date_flexible(message.text)
    if parsed_date is None:
        await send_or_edit_message(message, VACATION_DATE_ERROR)
        return
    
    await state.update_data(vacation_start=parsed_date)
    await send_or_edit_message(message, VACATION_END_PROMPT, reply_markup=back_to_employee_settings_keyboard())
    await state.set_state(Vacation.waiting_for_end)


@router.message(Vacation.waiting_for_end)
async def vacation_set_end(message: Message, state: FSMContext):
    """Обработка даты окончания отпуска"""
    if not validate_text_message(message):
        await send_or_edit_message(message, get_error_message_for_expected_text('vacation_end'))
        return
    
    parsed_date = parse_date_flexible(message.text)
    if parsed_date is None:
        await send_or_edit_message(message, VACATION_DATE_ERROR)
        return
    
    user_data = await state.get_data()
    start = user_data['vacation_start']
    end = parsed_date
    
    # Проверяем, что дата окончания не раньше даты начала
    try:
        start_date = datetime.strptime(start, '%d-%m-%Y').date()
        end_date = datetime.strptime(end, '%d-%m-%Y').date()
        
        if end_date < start_date:
            await send_or_edit_message(message, VACATION_END_ERROR)
            return
    except Exception:
        await send_or_edit_message(message, VACATION_DATE_PARSE_ERROR)
        return
    
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} установил отпуск с {start} по {end}")
    
    await db_update_vacation(message.from_user.id, start, end)
    await send_or_edit_message(
        message,
        f"<b>✅ Отпуск установлен с {start} по {end}!</b>\n\nВы всегда можете вернуться в главное меню.",
        reply_markup=menu_inline_keyboard()
    )
    await state.clear() 