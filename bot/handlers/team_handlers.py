import asyncio
import logging

from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import MAX_TEAM_NAME_LENGTH
from bot.core import router
from bot.core.database import (
    db_add_membership,
    db_create_team,
    db_get_employee,
    db_get_invite_by_code,
    db_get_membership,
    db_get_team_by_id,
    db_get_team_by_manager,
    db_update_employee_team,
    db_get_user_memberships
)
from bot.core.states import TeamRegistration
from bot.utils import (
    add_board_choice_keyboard,
    add_chat_choice_keyboard,
    cancel_keyboard,
    manager_keyboard_with_invite,
    send_or_edit_message,
    team_action_keyboard,
    team_preset_selection_keyboard,
    timezone_selection_keyboard,
)
from bot.utils.scheduler_manager import update_team_scheduler
from bot.utils.text_constants import (
    get_curator_required_message,
    CHAT_ID_FORMAT_ERROR,
    CHAT_ID_LENGTH_ERROR,
    TEAM_NOT_FOUND_ERROR,
    TOPIC_ID_FORMAT_ERROR,
    TOPIC_ID_LENGTH_ERROR,
    get_add_board_message,
    get_add_chat_message,
    get_already_registered_message,
    get_chat_id_confirmation_message,
    get_chat_skipped_message,
    get_create_team_message,
    get_error_message,
    get_join_team_message,
    get_length_validation_message,
    get_team_already_exists_error_message,
    get_team_created_message,
    get_team_invite_accepted_message,
    get_team_name_confirmation_message,
    get_team_preset_selection_message,
    get_topic_confirmation_message,
    get_welcome_message,
    get_create_team_limit_reached_message,
    get_team_limit_reached_message,
)


async def is_team_limit_reached(user_id: int, limit: int = 15) -> bool:
    """Проверяет, достиг ли пользователь лимита участия в командах."""
    try:
        memberships = await db_get_user_memberships(user_id)
        return bool(memberships) and len(memberships) >= int(limit)
    except Exception:
        return False


async def _delayed_registration_check(bot, user_id: int, team_name: str, state: FSMContext):
    """Через 15 минут проверить, завершил ли пользователь регистрацию."""
    try:
        await asyncio.sleep(15 * 60)
        employee = await db_get_employee(user_id)
        # Напоминаем, если пользователя нет в БД или он без команды
        if (not employee) or (not employee["team_id"]):
            message = (
                "⏰ <b>Напоминание о регистрации</b>\n\n"
                "Вы не завершили регистрацию. Ответьте на вопрос выше, чтобы продолжить"
            )
            await bot.send_message(chat_id=user_id, text=message, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка при отложенной проверке регистрации пользователя {user_id}: {e}")


async def _validate_invite_code(invite_code: str):
    """Проверить валидность пригласительного кода"""
    invite = await db_get_invite_by_code(invite_code)
    if not invite:
        return None, "❌ Недействительная пригласительная ссылка. Обратитесь к менеджеру за новой ссылкой."
    
    if not invite['is_active']:
        return None, "❌ Эта пригласительная ссылка неактивна. Обратитесь к менеджеру за активной ссылкой."
    
    team = await db_get_team_by_id(invite['team_id'])
    if not team:
        return None, TEAM_NOT_FOUND_ERROR
    
    return team, None


async def handle_invite_code(message: Message, state: FSMContext, invite_code: str):
    """Обработка пригласительного кода"""
    user_id = message.from_user.id
    # Лимит: не более 15 команд (создание/участие)
    if await is_team_limit_reached(user_id):
        await send_or_edit_message(message, get_team_limit_reached_message())
        return
    
    # Проверяем, не зарегистрирован ли уже пользователь
    employee = await db_get_employee(user_id)
    
    # Проверяем приглашение
    team, error_message = await _validate_invite_code(invite_code)
    if error_message:
        await send_or_edit_message(message, error_message)
        return
    # Менеджер этой команды считается уже участником — запрещаем повторное вступление
    try:
        if team.get('manager_tg_id') and int(team['manager_tg_id']) == int(user_id):
            await send_or_edit_message(message, f"Вы уже состоите в команде '{team['name']}'.")
            logging.info(f"Пользователь {user_id} является менеджером команды {team['id']} — повторное вступление запрещено")
            return
    except Exception:
        pass
    
    # Если пользователь уже есть в системе — добавляем в команду, если ещё не состоит
    if employee:
        existing = await db_get_membership(user_id, team['id'])
        if existing:
            await send_or_edit_message(message, f"Вы уже состоите в команде '{team['name']}'.")
            logging.info(f"Пользователь {user_id} уже состоит в команде {team['id']}")
            return
        # Создаем membership на основе текущих данных пользователя
        try:
            await db_add_membership(
                employee_tg_id=user_id,
                team_id=team['id'],
                role=employee.get('role'),
                daily_time=employee.get('daily_time'),
            )
            await db_update_employee_team(user_id, team['id'])
            from bot.utils.text_constants import get_employee_welcome_message
            await send_or_edit_message(message, get_employee_welcome_message(team['name']))
            # Показать меню сотрудника
            from bot.handlers.main_handlers import _show_employee_menu
            employee_updated = await db_get_employee(user_id)
            await _show_employee_menu(message, employee_updated)
            logging.info(f"Пользователь {user_id} добавлен в команду {team['id']} через инвайт")
        except Exception as e:
            logging.error(f"Не удалось добавить пользователя {user_id} в команду {team['id']}: {e}")
            from bot.utils.text_constants import get_error_message
            await send_or_edit_message(message, get_error_message("registration"))
        return

    # Сохраняем информацию о команде в состоянии (для нового пользователя)
    invite = await db_get_invite_by_code(invite_code)
    await state.update_data(team_id=invite['team_id'], invite_code=invite_code)
    
    # Создаем отложенную проверку регистрации (15 минут)
    try:
        from bot.core.bot_instance import bot as bot_instance
        asyncio.create_task(_delayed_registration_check(bot_instance, user_id, team['name'], state))
        logging.info(f'Запланирована отложенная проверка регистрации {user_id} в команде {team["name"]}')
    except Exception as e:
        logging.error(f"Не удалось запланировать отложенную проверку регистрации: {e}")
    
    # Переходим к регистрации с информацией о команде
    await send_or_edit_message(
        message,
        get_team_invite_accepted_message(team['name']),
        reply_markup=cancel_keyboard()
    )
    from bot.core.states import Registration
    await state.set_state(Registration.waiting_for_name)


# Обработчики для создания команды
@router.callback_query(F.data == "create_team")
async def create_team_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик создания новой команды"""
    user_id = callback.from_user.id
    # Только кураторы могут создавать команды
    try:
        from bot.core.database import db_is_curator
        if not await db_is_curator(user_id):
            await send_or_edit_message(callback, get_curator_required_message())
            await callback.answer()
            return
    except Exception as e:
        logging.error(f"Ошибка проверки прав куратора для {user_id}: {e}")
        await send_or_edit_message(callback, get_curator_required_message())
        await callback.answer()
        return
    # Проверка лимита участий/созданий команд (не более 5)
    if await is_team_limit_reached(user_id):
        await send_or_edit_message(callback, get_create_team_limit_reached_message())
        await callback.answer()
        return
    
    # Убираем ограничение «только одна команда-менеджер»
    
    await send_or_edit_message(
        callback.message,
        get_team_preset_selection_message(),
        reply_markup=team_preset_selection_keyboard()
    )
    await state.set_state(TeamRegistration.waiting_for_preset_choice)
    await callback.answer()


@router.message(Command("create_command"))
async def create_command_message(message: Message, state: FSMContext):
    """Команда /create_command: начать создание новой команды (с проверкой лимита 5)"""
    # Только кураторы могут создавать команды
    try:
        from bot.core.database import db_is_curator
        if not await db_is_curator(message.from_user.id):
            from bot.utils.text_constants import get_curator_required_message
            await send_or_edit_message(message, get_curator_required_message())
            return
    except Exception as e:
        logging.error(f"Ошибка проверки прав куратора для {message.from_user.id}: {e}")
        from bot.utils.text_constants import get_curator_required_message
        await send_or_edit_message(message, get_curator_required_message())
        return
    # Проверка лимита участий/созданий команд (не более 15)
    if await is_team_limit_reached(message.from_user.id):
        await send_or_edit_message(message, get_create_team_limit_reached_message())
        return
    await send_or_edit_message(
        message,
        get_team_preset_selection_message(),
        reply_markup=team_preset_selection_keyboard()
    )
    from bot.core.states import TeamRegistration
    await state.set_state(TeamRegistration.waiting_for_preset_choice)


@router.callback_query(F.data == "join_team")
async def join_team_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик присоединения к команде"""
    await send_or_edit_message(
        callback.message,
        get_join_team_message()
    )
    await callback.answer()


@router.callback_query(TeamRegistration.waiting_for_preset_choice, F.data.startswith("preset_"))
async def handle_preset_selection(callback: CallbackQuery, state: FSMContext):
    """Выбор пресета настроек команды"""
    preset_choice = callback.data.split("_", 1)[1]  # Убираем префикс "preset_"

    # Сохраняем выбранный пресет
    await state.update_data(preset_choice=preset_choice)

    # Переходим к вводу названия команды
    await send_or_edit_message(
        callback.message,
        get_create_team_message(),
        reply_markup=cancel_keyboard()
    )
    await state.set_state(TeamRegistration.waiting_for_team_name)
    await callback.answer()


@router.callback_query(TeamRegistration.waiting_for_timezone, F.data.startswith("set_timezone_"))
async def handle_timezone_selection(callback: CallbackQuery, state: FSMContext):
    """Выбор часового пояса команды (МСК/ЕКБ)"""
    choice = callback.data.split("_")[-1]
    if choice == 'msk':
        tz_value = 'Europe/Moscow'
        tz_label = 'МСК'
    else:
        tz_value = 'Asia/Yekaterinburg'
        tz_label = 'ЕКБ'

    await state.update_data(timezone=tz_value)

    data = await state.get_data()
    team_name = data.get('team_name')
    manager_tg_id = data.get('manager_tg_id')

    # Подтверждаем и переходим к выбору добавления чата
    await send_or_edit_message(
        callback,
        get_team_name_confirmation_message(team_name, manager_tg_id) + f"\n\n✅ Часовой пояс: <b>{tz_label}</b>",
        reply_markup=add_chat_choice_keyboard()
    )
    await state.set_state(TeamRegistration.waiting_for_chat_choice)
    await callback.answer()


@router.message(TeamRegistration.waiting_for_team_name)
async def handle_team_name(message: Message, state: FSMContext):
    """Обработка названия команды"""
    if not message.text:
        await send_or_edit_message(
            message,
            "❌ Пожалуйста, отправьте текстовое название команды. Файлы, фото, стикеры не принимаются."
        )
        return
    """Обработка названия команды"""
    team_name = message.text.strip()
    
    if len(team_name) < 2:
        await send_or_edit_message(
            message,
            get_length_validation_message("Название команды", min_length=2)
        )
        return
    elif len(team_name) > MAX_TEAM_NAME_LENGTH:
        await send_or_edit_message(
            message,
            get_length_validation_message("Название команды", max_length=MAX_TEAM_NAME_LENGTH)
        )
        return

    await state.update_data(team_name=team_name)

    # Создаётся новая команда - автоматически используем ID пользователя
    user_id = message.from_user.id
    await state.update_data(manager_tg_id=user_id)

    # После названия запрашиваем часовой пояс команды
    tz_prompt = (
        "⏰ Выберите часовой пояс для вашей команды:\n\n"
        "Это влияет на время отправки вопросов и отчётов."
    )
    await send_or_edit_message(
        message,
        tz_prompt,
        reply_markup=timezone_selection_keyboard()
    )
    await state.set_state(TeamRegistration.waiting_for_timezone)


@router.callback_query(F.data == "add_chat_yes")
async def add_chat_yes_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора добавления чата"""
    await send_or_edit_message(
        callback.message,
        get_add_chat_message(),
        reply_markup=cancel_keyboard()
    )
    await state.set_state(TeamRegistration.waiting_for_chat_id)
    await callback.answer()


@router.callback_query(F.data == "add_chat_skip")
async def add_chat_skip_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик пропуска добавления чата"""
    await state.update_data(chat_id=None, chat_topic_id=None)

    # Переходим к запросу ссылки на доску
    await send_or_edit_message(
        callback.message,
        get_chat_skipped_message(),
        reply_markup=add_board_choice_keyboard()
    )
    await state.set_state(TeamRegistration.waiting_for_board_link)
    await callback.answer()


@router.message(TeamRegistration.waiting_for_chat_id)
async def handle_chat_id(message: Message, state: FSMContext):
    """Обработка ID чата команды"""
    chat_id = message.text.strip()

    # Проверяем, что это похоже на ID чата
    if not chat_id.startswith('-') or not chat_id[1:].isdigit():
        await send_or_edit_message(
            message,
            CHAT_ID_FORMAT_ERROR
        )
        return

    # Ограничение на длину: максимум 18 цифр в числовой части
    if len(chat_id) > 18:
        await send_or_edit_message(
            message,
            CHAT_ID_LENGTH_ERROR
        )
        return

    await state.update_data(chat_id=chat_id)
    
    await send_or_edit_message(
        message,
        get_chat_id_confirmation_message(chat_id),
        reply_markup=cancel_keyboard()
    )
    await state.set_state(TeamRegistration.waiting_for_chat_topic)


@router.message(TeamRegistration.waiting_for_chat_topic)
async def handle_chat_topic(message: Message, state: FSMContext):
    """Обработка ID топика чата"""
    chat_topic = message.text.strip()

    # Проверяем, что это целое число
    if chat_topic != '0':
        try:
            int(chat_topic)
        except ValueError:
            await send_or_edit_message(
                message,
                TOPIC_ID_FORMAT_ERROR
            )
            return
        # Ограничение на длину: максимум 18 цифр
        digits = chat_topic.lstrip('-')
        if len(digits) > 18:
            await send_or_edit_message(
                message,
                TOPIC_ID_LENGTH_ERROR
            )
            return

    if chat_topic == '0':
        chat_topic = None

    await state.update_data(chat_topic_id=chat_topic)

    # Переходим к запросу ссылки на доску
    await send_or_edit_message(
        message,
        get_topic_confirmation_message(chat_topic),
        reply_markup=add_board_choice_keyboard()
    )
    await state.set_state(TeamRegistration.waiting_for_board_link)


@router.callback_query(F.data == "add_board_yes")
async def add_board_yes_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик добавления ссылки на доску"""
    await send_or_edit_message(
        callback.message,
        get_add_board_message(),
        reply_markup=cancel_keyboard()
    )
    await state.set_state(TeamRegistration.waiting_for_board_link)
    await callback.answer()


@router.callback_query(F.data == "add_board_skip")
async def add_board_skip_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик пропуска добавления ссылки на доску"""

    global send_message_with_retry
    try:
        data = await state.get_data()
        manager_tg_id = data['manager_tg_id']
        logging.info(f"Пользователь {manager_tg_id} пропустил добавление ссылки на доску")

        # Повторная проверка: только кураторы могут создавать команды
        try:
            from bot.core.database import db_is_curator
            if not await db_is_curator(manager_tg_id):
                from bot.utils.text_constants import get_curator_required_message
                await send_or_edit_message(callback, get_curator_required_message())
                await callback.answer()
                return
        except Exception as e:
            logging.error(f"Ошибка проверки прав куратора при создании команды (skip board) для {manager_tg_id}: {e}")
            from bot.utils.text_constants import get_curator_required_message
            await send_or_edit_message(callback, get_curator_required_message())
            await callback.answer()
            return

        await state.update_data(board_link=None)
        
        # Создаём команду без ссылки на доску
        logging.info(f"Создаём команду '{data['team_name']}' для пользователя {manager_tg_id}")

        team_timezone = data.get('timezone', 'Asia/Yekaterinburg')
        preset_choice = data.get('preset_choice')
        team_id = await db_create_team(
            data['team_name'],
            data['chat_id'],
            data['chat_topic_id'],
            None,  # board_link
            timezone=team_timezone,
            preset_choice=preset_choice
        )
        
        logging.info(f"Команда '{data['team_name']}' успешно создана с ID: {team_id}")

        # Инициализируем планировщик для новой команды
        try:
            await update_team_scheduler(team_id)
            logging.info(f"Планировщик инициализирован для новой команды {team_id}")
        except Exception as scheduler_error:
            logging.error(f"Ошибка при инициализации планировщика для команды {team_id}: {scheduler_error}")

        # Получаем команду для отображения меню
        team = await db_get_team_by_id(team_id)

        # Отправляем сообщение об успешном создании команды
        from bot.utils.utils import send_message_with_retry

        await send_message_with_retry(
            manager_tg_id,
            get_team_created_message(data['team_name'])
        )

        # Добавляем менеджера в employees (если не существует) и membership; делаем команду текущей
        try:
            from bot.core.database import db_ensure_employee
            await db_ensure_employee(manager_tg_id, callback.from_user.username, callback.from_user.full_name)
            await db_add_membership(employee_tg_id=manager_tg_id, team_id=team_id, is_manager=True)
            await db_update_employee_team(manager_tg_id, team_id)
        except Exception as e:
            logging.error(f"Не удалось создать membership менеджера {manager_tg_id} для команды {team_id}: {e}")

        # Отправляем стандартное меню менеджера
        from bot.handlers.main_handlers import _show_manager_menu
        await _show_manager_menu(callback, team)
        await state.clear()
        await callback.answer()

        logging.info(f"Регистрация команды '{data['team_name']}' завершена успешно")

    except Exception as e:
        logging.error(f"Ошибка при пропуске добавления доски: {e}")

        try:
            await send_message_with_retry(
                manager_tg_id,
                get_error_message("team_creation"),
                reply_markup=cancel_keyboard()
            )
        except:
            # Если не удалось отправить сообщение, просто отвечаем на callback
            pass
        await callback.answer()


@router.message(TeamRegistration.waiting_for_board_link)
async def handle_board_link(message: Message, state: FSMContext):
    """Обработка ссылки на доску команды"""
    board_link = message.text.strip()
    
    # Простая валидация ссылки
    if not board_link.startswith(('http://', 'https://')):
        await send_or_edit_message(
            message,
            get_error_message("invalid_link")
        )
        return
    
    # Нормализуем GitVerse ссылку, если это tasktracker
    try:
        from bot.utils.utils import normalize_gitverse_board_link
        if board_link:
            board_link = normalize_gitverse_board_link(board_link)
    except Exception:
        pass

    await state.update_data(board_link=board_link)

    # Создаём команду с ссылкой на доску
    data = await state.get_data()

    # Повторная проверка: только кураторы могут создавать команды
    try:
        from bot.core.database import db_is_curator
        if not await db_is_curator(message.from_user.id):
            from bot.utils.text_constants import get_curator_required_message
            await send_or_edit_message(message, get_curator_required_message())
            return
    except Exception as e:
        logging.error(f"Ошибка проверки прав куратора при создании команды (board link) для {message.from_user.id}: {e}")
        from bot.utils.text_constants import get_curator_required_message
        await send_or_edit_message(message, get_curator_required_message())
        return
    preset_choice = data.get('preset_choice')
    team_id = await db_create_team(
        data['team_name'],
        data['chat_id'],
        data['chat_topic_id'],
        data['board_link'],
        timezone=data.get('timezone', 'Asia/Yekaterinburg'),
        preset_choice=preset_choice
    )
    
    # Инициализируем планировщик для новой команды
    try:
        await update_team_scheduler(team_id)
        logging.info(f"Планировщик инициализирован для новой команды {team_id}")
    except Exception as scheduler_error:
        logging.error(f"Ошибка при инициализации планировщика для команды {team_id}: {scheduler_error}")
    
    # Получаем команду для отображения меню
    team = await db_get_team_by_id(team_id)

    # Отправляем сообщение об успешном создании команды
    from bot.utils.utils import send_message_with_retry

    await send_message_with_retry(
        message.from_user.id,
        get_team_created_message(data['team_name'])
    )

    # Добавляем менеджера в employees (если не существует) и membership; делаем команду текущей
    try:
        from bot.core.database import db_ensure_employee
        await db_ensure_employee(message.from_user.id, message.from_user.username, message.from_user.full_name)
        await db_add_membership(employee_tg_id=message.from_user.id, team_id=team_id, is_manager=True)
        await db_update_employee_team(message.from_user.id, team_id)
    except Exception as e:
        logging.error(f"Не удалось создать membership менеджера {message.from_user.id} для команды {team_id}: {e}")

    # Отправляем стандартное меню менеджера
    from bot.handlers.main_handlers import _show_manager_menu
    await _show_manager_menu(message, team)
    await state.clear()


# Обработчик отмены удален - теперь используется универсальный обработчик в cancel_handlers.py 