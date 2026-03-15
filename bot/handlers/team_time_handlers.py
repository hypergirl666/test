import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import DEFAULT_TEAM_TIMES
from bot.core import router
from bot.core.database import (
    db_get_team_by_id,
    db_get_team_by_manager,
    db_get_team_time_settings,
    db_update_team_time_settings,
)
from bot.core.states import TeamTimeSettings
from bot.utils import cancel_keyboard, send_or_edit_message
from bot.utils.keyboards import interactive_days_keyboard, team_time_settings_keyboard, time_selection_keyboard
from bot.utils.scheduler_manager import update_team_scheduler
from bot.utils.text_constants import (
    get_days_selection_message,
    get_team_time_settings_message,
    get_time_selection_message,
    get_time_validation_error_message,
)
from bot.utils.utils import validate_team_time_settings


@router.callback_query(F.data == "team_time_settings")
async def show_team_time_settings(callback: CallbackQuery, state: FSMContext):
    """Показать меню настроек времени команды"""
    user_id = callback.from_user.id
    
    # Проверяем, является ли пользователь менеджером команды
    team = await db_get_team_by_manager(user_id)
    if not team:
        await send_or_edit_message(
            callback.message,
            "❌ У вас нет команды для настройки времени."
        )
        await callback.answer()
        return
    
    # Получаем текущие настройки времени
    time_settings = await db_get_team_time_settings(team['id'])
    report_days = time_settings['report_days'] if time_settings else DEFAULT_TEAM_TIMES['report_days']
    
    # Вычисляем morning_days и evening_days из report_days
    from bot.utils.day_utils import get_computed_team_days
    computed_days = get_computed_team_days(report_days)
    
    current_settings = {
        'morning_time': time_settings['morning_time'] if time_settings else DEFAULT_TEAM_TIMES['morning_time'],
        'evening_time': time_settings['evening_time'] if time_settings else DEFAULT_TEAM_TIMES['evening_time'],
        'report_time': time_settings['report_time'] if time_settings else DEFAULT_TEAM_TIMES['report_time'],
        'morning_days': computed_days['morning_days'],
        'evening_days': computed_days['evening_days'],
        'report_days': computed_days['report_days']
    }
    
    # Сохраняем настройки в состоянии
    await state.update_data(
        team_id=team['id'],
        current_settings=current_settings
    )
    
    await send_or_edit_message(
        callback.message,
        get_team_time_settings_message(team['name'], current_settings),
        reply_markup=team_time_settings_keyboard()
    )
    await state.set_state(TeamTimeSettings.choosing_time_type)
    await callback.answer()


@router.callback_query(TeamTimeSettings.choosing_time_type, F.data.startswith("team_time_"))
async def handle_time_type_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора типа времени для настройки"""
    action = callback.data.split("_")[2]  # morning, evening, report, days
    
    if action == "days":
        # Показываем интерактивную клавиатуру для выбора дней отчетов
        data = await state.get_data()
        current_settings = data['current_settings']
        
        await send_or_edit_message(
            callback.message,
            get_days_selection_message("report"),
            reply_markup=interactive_days_keyboard("report", current_settings['report_days'])
        )
        await state.set_state(TeamTimeSettings.choosing_report_days)
        await callback.answer()
        return
    
    # Получаем текущие настройки для умной клавиатуры
    data = await state.get_data()
    current_settings = data['current_settings']
    
    # Для времени показываем умную клавиатуру выбора времени
    await send_or_edit_message(
        callback.message,
        get_time_selection_message(action),
        reply_markup=time_selection_keyboard(action, current_settings)
    )
    
    if action == "morning":
        await state.set_state(TeamTimeSettings.choosing_morning_time)
    elif action == "evening":
        await state.set_state(TeamTimeSettings.choosing_evening_time)
    elif action == "report":
        await state.set_state(TeamTimeSettings.choosing_report_time)
    
    await callback.answer()


@router.callback_query(TeamTimeSettings.choosing_morning_time, F.data == "team_time_settings")
async def handle_back_to_time_settings_from_morning(callback: CallbackQuery, state: FSMContext):
    """Возврат к настройкам времени из выбора утреннего времени"""
    data = await state.get_data()
    team = await db_get_team_by_id(data['team_id'])
    current_settings = data['current_settings']
    
    await send_or_edit_message(
        callback.message,
        get_team_time_settings_message(team['name'], current_settings),
        reply_markup=team_time_settings_keyboard()
    )
    await state.set_state(TeamTimeSettings.choosing_time_type)
    await callback.answer()


@router.callback_query(TeamTimeSettings.choosing_evening_time, F.data == "team_time_settings")
async def handle_back_to_time_settings_from_evening(callback: CallbackQuery, state: FSMContext):
    """Возврат к настройкам времени из выбора вечернего времени"""
    data = await state.get_data()
    team = await db_get_team_by_id(data['team_id'])
    current_settings = data['current_settings']
    
    await send_or_edit_message(
        callback.message,
        get_team_time_settings_message(team['name'], current_settings),
        reply_markup=team_time_settings_keyboard()
    )
    await state.set_state(TeamTimeSettings.choosing_time_type)
    await callback.answer()


@router.callback_query(TeamTimeSettings.choosing_report_time, F.data == "team_time_settings")
async def handle_back_to_time_settings_from_report(callback: CallbackQuery, state: FSMContext):
    """Возврат к настройкам времени из выбора времени отчетов"""
    data = await state.get_data()
    team = await db_get_team_by_id(data['team_id'])
    current_settings = data['current_settings']
    
    await send_or_edit_message(
        callback.message,
        get_team_time_settings_message(team['name'], current_settings),
        reply_markup=team_time_settings_keyboard()
    )
    await state.set_state(TeamTimeSettings.choosing_time_type)
    await callback.answer()


@router.callback_query(TeamTimeSettings.choosing_morning_time, F.data.startswith("team_time_morning_"))
async def handle_morning_time_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора времени утреннего опроса"""
    time_value = callback.data.split("_")[3]  # HH:MM
    await update_time_setting(callback, state, "morning_time", time_value)


@router.callback_query(TeamTimeSettings.choosing_evening_time, F.data.startswith("team_time_evening_"))
async def handle_evening_time_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора времени вечернего опроса"""
    time_value = callback.data.split("_")[3]  # HH:MM
    await update_time_setting(callback, state, "evening_time", time_value)


@router.callback_query(TeamTimeSettings.choosing_report_time, F.data.startswith("team_time_report_"))
async def handle_report_time_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора времени отчетов"""
    time_value = callback.data.split("_")[3]  # HH:MM
    await update_time_setting(callback, state, "report_time", time_value)


async def save_settings_to_db(team_id: int, current_settings: dict) -> bool:
    """Сохранение настроек времени в базу данных"""
    try:
        # Сохраняем настройки в базу данных (только report_days)
        await db_update_team_time_settings(
            team_id=team_id,
            morning_time=current_settings['morning_time'],
            evening_time=current_settings['evening_time'],
            report_time=current_settings['report_time'],
            report_days=current_settings['report_days']
        )
        
        # Обновляем планировщик для этой команды
        scheduler_updated = await update_team_scheduler(team_id)
        if scheduler_updated:
            logging.info(f"Планировщик обновлен для команды {team_id}")
        else:
            logging.warning(f"Не удалось обновить планировщик для команды {team_id}")
        
        # Обновляем напоминания для всех пользователей команды
        try:
            from bot.handlers.daily_handlers import update_team_reminders
            await update_team_reminders(team_id)
        except Exception as e:
            logging.error(f"Не удалось обновить напоминания для команды {team_id}: {e}")
        
        return True
        
    except Exception as e:
        logging.error(f"Ошибка при сохранении настроек времени: {e}")
        return False


async def update_time_setting(callback: CallbackQuery, state: FSMContext, time_field: str, time_value: str):
    """Обновление настройки времени с валидацией и немедленным сохранением"""
    data = await state.get_data()
    current_settings = data['current_settings'].copy()
    team_id = data['team_id']
    
    # Обновляем выбранное время
    current_settings[time_field] = time_value
    
    # Валидируем настройки
    is_valid, error_message = validate_team_time_settings(
        current_settings['morning_time'],
        current_settings['evening_time'],
        current_settings['report_time']
    )
    
    if not is_valid:
        await send_or_edit_message(
            callback.message,
            get_time_validation_error_message(error_message),
            reply_markup=team_time_settings_keyboard()
        )
        await state.set_state(TeamTimeSettings.choosing_time_type)
        await callback.answer()
        return
    
    # Сохраняем обновленные настройки в состоянии
    await state.update_data(current_settings=current_settings)
    
    # Сразу сохраняем в базу данных
    save_success = await save_settings_to_db(team_id, current_settings)
    
    # Показываем обновленное меню
    team = await db_get_team_by_id(team_id)
    message = get_team_time_settings_message(team['name'], current_settings)
    
    if save_success:
        message = "✅ " + message + "\n\n💾 <b>Настройки автоматически сохранены!</b>"
    else:
        message = "⚠️ " + message + "\n\n❌ <b>Ошибка сохранения настроек</b>"
    
    await send_or_edit_message(
        callback.message,
        message,
        reply_markup=team_time_settings_keyboard()
    )
    await state.set_state(TeamTimeSettings.choosing_time_type)
    await callback.answer()


# Удалены устаревшие обработчики выбора дней (оставлены интерактивные переключатели)


@router.callback_query(TeamTimeSettings.choosing_report_days, F.data.startswith("toggle_day_report_"))
async def handle_toggle_report_day(callback: CallbackQuery, state: FSMContext):
    """Обработка переключения дня для отчетов"""
    day_code = callback.data.split("_")[3]  # mon, tue, wed, etc.
    await toggle_day_setting(callback, state, "report", day_code)


async def toggle_day_setting(callback: CallbackQuery, state: FSMContext, time_type: str, day_code: str):
    """Переключение дня в настройках с автоматическим применением логики и сохранением"""
    data = await state.get_data()
    current_settings = data['current_settings'].copy()
    team_id = data['team_id']
    
    # Получаем текущие дни отчетов
    current_days = current_settings['report_days']
    active_days = set(current_days.split(',')) if current_days else set()
    
    # Переключаем день
    if day_code in active_days:
        active_days.remove(day_code)
    else:
        active_days.add(day_code)
    
    # Обновляем дни отчетов
    current_settings['report_days'] = ','.join(sorted(active_days))
    
    # Вычисляем morning_days и evening_days из report_days
    from bot.utils.day_utils import get_computed_team_days
    computed_days = get_computed_team_days(current_settings['report_days'])
    current_settings['morning_days'] = computed_days['morning_days']
    current_settings['evening_days'] = computed_days['evening_days']
    
    # Сохраняем обновленные настройки в состоянии
    await state.update_data(current_settings=current_settings)
    
    # Сразу сохраняем в базу данных
    save_success = await save_settings_to_db(team_id, current_settings)
    
    message = get_days_selection_message("report")
    
    if save_success:
        message += "\n💾 <b>Настройки автоматически сохранены!</b>"
    else:
        message += "\n\n❌ <b>Ошибка сохранения настроек</b>"
    
    await send_or_edit_message(
        callback.message,
        message,
        reply_markup=interactive_days_keyboard(time_type, current_settings['report_days'])
    )
    await callback.answer()


# Обработчик отмены удален - теперь используется универсальный обработчик в cancel_handlers.py


# Обработчики для ручного ввода времени


@router.message(TeamTimeSettings.choosing_morning_time)
async def handle_morning_time_input(message: Message, state: FSMContext):
    """Обработка ручного ввода утреннего времени"""
    time_input = message.text.strip()
    
    # Валидация формата времени
    if not _validate_time_format(time_input):
        await send_or_edit_message(
            message,
            "❌ Неверный формат времени!\n\n"
            "Используйте формат ЧЧ:ММ\n"
            "Например: 09:00, 08:30, 10:15\n\n"
            "Попробуйте еще раз:",
            reply_markup=cancel_keyboard()
        )
        return
    
    # Обновляем настройку
    await update_time_setting_from_message(message, state, "morning_time", time_input)


@router.message(TeamTimeSettings.choosing_evening_time)
async def handle_evening_time_input(message: Message, state: FSMContext):
    """Обработка ручного ввода вечернего времени"""
    time_input = message.text.strip()
    
    # Валидация формата времени
    if not _validate_time_format(time_input):
        await send_or_edit_message(
            message,
            "❌ Неверный формат времени!\n\n"
            "Используйте формат ЧЧ:ММ\n"
            "Например: 18:00, 19:30, 22:15\n\n"
            "Попробуйте еще раз:",
            reply_markup=cancel_keyboard()
        )
        return
    
    # Обновляем настройку
    await update_time_setting_from_message(message, state, "evening_time", time_input)


@router.message(TeamTimeSettings.choosing_report_time)
async def handle_report_time_input(message: Message, state: FSMContext):
    """Обработка ручного ввода времени отчетов"""
    time_input = message.text.strip()
    
    # Валидация формата времени
    if not _validate_time_format(time_input):
        await send_or_edit_message(
            message,
            "❌ Неверный формат времени!\n\n"
            "Используйте формат ЧЧ:ММ\n"
            "Например: 10:00, 14:30, 16:15\n\n"
            "Попробуйте еще раз:",
            reply_markup=cancel_keyboard()
        )
        return
    
    # Обновляем настройку
    await update_time_setting_from_message(message, state, "report_time", time_input)


def _validate_time_format(time_str: str) -> bool:
    """Валидация формата времени ЧЧ:ММ"""
    try:
        if ':' not in time_str:
            return False
        
        hour, minute = time_str.split(':')
        hour = int(hour)
        minute = int(minute)
        
        return 0 <= hour <= 23 and 0 <= minute <= 59
    except (ValueError, TypeError):
        return False


async def update_time_setting_from_message(message: Message, state: FSMContext, time_field: str, time_value: str):
    """Обновление настройки времени из сообщения с валидацией и сохранением"""
    data = await state.get_data()
    current_settings = data['current_settings'].copy()
    team_id = data['team_id']
    
    # Обновляем выбранное время
    current_settings[time_field] = time_value
    
    # Валидируем настройки
    is_valid, error_message = validate_team_time_settings(
        current_settings['morning_time'],
        current_settings['evening_time'],
        current_settings['report_time']
    )
    
    if not is_valid:
        await send_or_edit_message(
            message,
            get_time_validation_error_message(error_message),
            reply_markup=team_time_settings_keyboard()
        )
        await state.set_state(TeamTimeSettings.choosing_time_type)
        return
    
    # Сохраняем обновленные настройки в состоянии
    await state.update_data(current_settings=current_settings)
    
    # Сразу сохраняем в базу данных
    save_success = await save_settings_to_db(team_id, current_settings)
    
    # Показываем обновленное меню
    team = await db_get_team_by_id(team_id)
    message_text = get_team_time_settings_message(team['name'], current_settings)
    
    if save_success:
        message_text = "✅ " + message_text + "\n\n💾 <b>Настройки автоматически сохранены!</b>"
    else:
        message_text = "⚠️ " + message_text + "\n\n❌ <b>Ошибка сохранения настроек</b>"
    
    await send_or_edit_message(
        message,
        message_text,
        reply_markup=team_time_settings_keyboard()
    )
    await state.set_state(TeamTimeSettings.choosing_time_type) 