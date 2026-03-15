import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import (
    ERROR_MESSAGES,
    EVENING_DB_VALUE,
    MAX_NAME_LENGTH,
    MAX_ROLE_LENGTH,
    MORNING_DB_VALUE,
    TIME_CONSTANTS,
)
from bot.core import Registration, router
from bot.core.database import (
    db_add_employee,
    db_add_membership,
    db_get_employee,
    db_get_last_report_date,
    db_update_employee_team,
)
from bot.utils import (
    change_data_inline_keyboard,
    daily_time_keyboard,
    get_error_message_for_expected_text,
    role_selection_keyboard,
    send_or_edit_message,
    validate_and_format_name,
    validate_max_length,
    validate_text_message,
)


# --- Флоу регистрации для сотрудников ---
@router.message(Registration.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    """Обработка ввода имени при регистрации"""
    # Проверка на текстовое сообщение
    if not validate_text_message(message):
        await send_or_edit_message(message, get_error_message_for_expected_text('full_name'))
        return

    if not validate_max_length(message.text, MAX_NAME_LENGTH):
        await send_or_edit_message(message, ERROR_MESSAGES['name_too_long'])
        return

    # Валидация и форматирование имени
    is_valid, result = validate_and_format_name(message.text)
    if not is_valid:
        await send_or_edit_message(message, ERROR_MESSAGES['invalid_name_format'])
        return

    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} ввел имя при регистрации: {message.text} -> {result}")

    await state.update_data(full_name=result)
    await send_or_edit_message(message,
                               f"<b>✅ Отлично, {result}!</b>\n\n"
                               f"<b>Шаг 2: Выберите вашу роль</b>\n"
                               f"Роль поможет менеджеру и команде лучше понимать вашу специализацию.\n\n"
                               f"<b>💡 Варианты:</b>\n"
                               f"• Выберите из предложенных вариантов\n"
                               f"• Или напишите свою роль в сообщении\n\n",
                               reply_markup=role_selection_keyboard())
    await state.set_state(Registration.waiting_for_role)


@router.callback_query(Registration.waiting_for_role, F.data.startswith("set_role_"))
async def process_role_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора роли при регистрации"""
    role_data = callback.data.split("_", 2)[2]  # Получаем роль из callback_data

    user_id = callback.from_user.id
    logging.info(f"Пользователь {user_id} выбрал роль при регистрации: {role_data}")

    await state.update_data(role=role_data)

    # Получаем настройки команды, если есть team_id
    team_settings = None
    user_data = await state.get_data()
    team_id = user_data.get('team_id')
    if team_id:
        from bot.core.database import db_get_team_by_id
        team = await db_get_team_by_id(team_id)
        if team:
            team_settings = team

    await send_or_edit_message(callback,
                               f"<b>✅ Отлично! Роль '{role_data}' выбрана.</b>\n\n"
                               f"<b>Шаг 3: Выберите время для ежедневных опросов</b>\n"
                               f"Это время, когда бот будет присылать вам вопросы для дейли.\n\n"
                               f"<b>Варианты времени:</b>\n"
                               f"🌅 <b>Утреннее дейли</b> - вопросы приходят утром в день дейли\n"
                               f"🌆 <b>Вечернее дейли</b> - вопросы приходят с вечера на дейлик следующего дня\n\n",
                               reply_markup=daily_time_keyboard(team_settings))
    await state.set_state(Registration.waiting_for_time)
    await callback.answer()


@router.message(Registration.waiting_for_role)
async def process_role_input(message: Message, state: FSMContext):
    """Обработка ввода роли при регистрации (текстовый ввод)"""
    # Проверка на текстовое сообщение
    if not validate_text_message(message):
        await send_or_edit_message(message, get_error_message_for_expected_text('role'))
        return

    if not validate_max_length(message.text, MAX_ROLE_LENGTH):
        await send_or_edit_message(message, ERROR_MESSAGES['role_too_long'])
        return

    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} ввел роль при регистрации: {message.text}")

    await state.update_data(role=message.text)

    # Получаем настройки команды, если есть team_id
    team_settings = None
    user_data = await state.get_data()
    team_id = user_data.get('team_id')
    if team_id:
        from bot.core.database import db_get_team_by_id
        team = await db_get_team_by_id(team_id)
        if team:
            team_settings = team

    await send_or_edit_message(message,
                               f"<b>✅ Отлично! Роль '{message.text}' сохранена.</b>\n\n"
                               f"<b>Шаг 3: Выберите время для ежедневных опросов</b>\n"
                               f"Это время, когда бот будет присылать вам вопросы для дейли.\n\n"
                               f"<b>Варианты времени:</b>\n"
                               f"🌅 <b>Утреннее дейли</b> - вопросы приходят утром в день дейли\n"
                               f"🌆 <b>Вечернее дейли</b> - вопросы приходят с вечера на дейлик следующего дня\n\n",
                               reply_markup=daily_time_keyboard(team_settings))
    await state.set_state(Registration.waiting_for_time)


@router.callback_query(Registration.waiting_for_time, F.data.startswith("set_time_"))
async def process_time_selection_registration(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора времени при регистрации"""
    # Получаем время из callback_data и проверяем, что это правильное значение
    time_from_callback = callback.data.split("_")[-1]

    # Проверяем, что время соответствует нашим константам
    if time_from_callback == MORNING_DB_VALUE:
        time = MORNING_DB_VALUE
    elif time_from_callback == EVENING_DB_VALUE:
        time = EVENING_DB_VALUE
    else:
        # Если получили старое значение, используем утреннее время по умолчанию
        time = MORNING_DB_VALUE
        logging.warning(f"Получено неожиданное время из callback: {time_from_callback}, используем {MORNING_DB_VALUE}")

    user_id = callback.from_user.id
    logging.info(f"Пользователь {user_id} выбрал время дейли при регистрации: {time}")

    user_data = await state.get_data()

    # Проверяем, есть ли информация о команде (приглашение)
    team_id = user_data.get('team_id')

    try:
        # Сохраняем выбранное время во временном состоянии
        await state.update_data(daily_time=time)

        # Если у команды есть GitVerse доска — запросим ник
        ask_gitverse = False
        if team_id:
            from bot.core.database import db_get_team_by_id
            team = await db_get_team_by_id(team_id)
            if team and team['board_link']:
                from bot.utils.utils import is_gitverse_board_link
                if is_gitverse_board_link(team['board_link']):
                    ask_gitverse = True

        if ask_gitverse:
            from bot.utils.keyboards import gitverse_nickname_keyboard
            await send_or_edit_message(
                callback,
                "<b>Шаг 4: Ник в GitVerse</b>\n\nЕсли у вас есть ник на GitVerse, укажите его (пример: <code>username</code>).\n\n"
                "Это позволит отправлять персональную ссылку на доску задач с фильтром по вашим задачам.",
                reply_markup=gitverse_nickname_keyboard()
            )
            await state.set_state(Registration.waiting_for_gitverse_nickname)
            await callback.answer()
            return

        # Иначе — сразу создаём сотрудника
        await db_add_employee(
            tg_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=user_data['full_name'],
            role=user_data['role'],
            daily_time=time,
            team_id=team_id
        )
        if team_id:
            await db_add_membership(
                employee_tg_id=callback.from_user.id,
                team_id=team_id,
                role=user_data['role'],
                daily_time=time,
            )
            await db_update_employee_team(callback.from_user.id, team_id)

        # Если есть команда, обновляем информацию о сотруднике в команде
        employee = await db_get_employee(callback.from_user.id)
        tg_id = employee["tg_id"]
        last_report_date = await db_get_last_report_date(tg_id)

        # Отправляем первое сообщение - подтверждение регистрации
        if team_id:
            # Если есть команда, получаем её название и отправляем приветствие
            from bot.core.database import db_get_team_by_id
            team = await db_get_team_by_id(team_id)
            if team:
                from bot.utils.text_constants import get_employee_welcome_message
                welcome_message = get_employee_welcome_message(team['name'])
                await send_or_edit_message(callback.message, welcome_message)
            else:
                # Fallback если команда не найдена
                await send_or_edit_message(
                    callback.message,
                    "<b>🎉 Регистрация успешно завершена!</b>\n\n"
                    "<b>✅ Что дальше?</b>\n"
                    "• В назначенное время вы получите первый опрос с 3 вопросами\n"
                    "• Можете отвечать текстом или голосовыми сообщениями\n"
                    "• Ваши ответы будут отправлены менеджеру команды\n\n"
                    "<b>💡 Полезные команды:</b>\n"
                    "• Используйте меню для навигации по функциям бота\n"
                    "• В настройках профиля можете изменить свои данные\n"
                    "• При необходимости можете указать даты отпуска\n\n"
                    "<b>🚀 Добро пожаловать в команду!</b>"
                )

        # Отправляем второе сообщение - шаблон с информацией о пользователе
        from bot.utils.text_constants import get_user_info_template
        user_info = await get_user_info_template(employee, last_report_date)

        await callback.message.answer(
            user_info,
            reply_markup=change_data_inline_keyboard()
        )
        logging.info(f"Новый сотрудник {user_data['full_name']} ({callback.from_user.id}) зарегистрирован.")
    except Exception as e:
        logging.error(f"Ошибка при добавлении сотрудника в БД: {e}")
        from bot.utils.text_constants import get_error_message
        await send_or_edit_message(callback, get_error_message("registration"))

    await state.clear()
    await callback.answer()


@router.message(Registration.waiting_for_gitverse_nickname)
async def process_gitverse_nickname(message: Message, state: FSMContext):
    """Опциональный шаг: ник GitVerse при регистрации, если у команды GitVerse-доска."""
    # Допускаем только текст; если пользователь нажал кнопку — будет отдельный callback
    if not validate_text_message(message):
        await send_or_edit_message(message, get_error_message_for_expected_text('role'))
        return

    nickname_raw = (message.text or '').strip()
    nickname = nickname_raw if nickname_raw else None

    user_data = await state.get_data()
    team_id = user_data.get('team_id')
    daily_time = user_data.get('daily_time', MORNING_DB_VALUE)

    try:
        await db_add_employee(
            tg_id=message.from_user.id,
            username=message.from_user.username,
            full_name=user_data['full_name'],
            role=user_data['role'],
            daily_time=daily_time,
            team_id=team_id
        )
        if team_id:
            await db_add_membership(
                employee_tg_id=message.from_user.id,
                team_id=team_id,
                role=user_data['role'],
                daily_time=daily_time,
            )
            await db_update_employee_team(message.from_user.id, team_id)

        if nickname:
            from bot.core.database import db_update_employee_field
            await db_update_employee_field(message.from_user.id, 'gitverse_nickname', nickname)

        employee = await db_get_employee(message.from_user.id)
        tg_id = employee["tg_id"]
        last_report_date = await db_get_last_report_date(tg_id)

        if team_id:
            from bot.core.database import db_get_team_by_id
            team = await db_get_team_by_id(team_id)
            if team:
                from bot.utils.text_constants import get_employee_welcome_message
                welcome_message = get_employee_welcome_message(team['name'])
                await send_or_edit_message(message, welcome_message)

        from bot.utils.text_constants import get_user_info_template
        user_info = await get_user_info_template(employee, last_report_date)
        await message.answer(user_info, reply_markup=change_data_inline_keyboard())
        logging.info(
            f"Новый сотрудник {user_data['full_name']} ({message.from_user.id}) зарегистрирован. GitVerse: {nickname or '-'}")
    except Exception as e:
        logging.error(f"Ошибка при добавлении сотрудника/сохранении GitVerse: {e}")
        from bot.utils.text_constants import get_error_message
        await send_or_edit_message(message, get_error_message("registration"))

    await state.clear()


@router.callback_query(Registration.waiting_for_gitverse_nickname, F.data == "gitverse_skip")
async def process_gitverse_skip(callback: CallbackQuery, state: FSMContext):
    """Пропуск ввода ника GitVerse при регистрации."""
    user_data = await state.get_data()
    team_id = user_data.get('team_id')
    daily_time = user_data.get('daily_time', MORNING_DB_VALUE)

    try:
        await db_add_employee(
            tg_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=user_data['full_name'],
            role=user_data['role'],
            daily_time=daily_time,
            team_id=team_id
        )
        if team_id:
            await db_add_membership(
                employee_tg_id=callback.from_user.id,
                team_id=team_id,
                role=user_data['role'],
                daily_time=daily_time,
            )
            await db_update_employee_team(callback.from_user.id, team_id)

        if team_id:
            from bot.core.database import db_get_team_by_id
            team = await db_get_team_by_id(team_id)
            if team:
                from bot.utils.text_constants import get_employee_welcome_message
                welcome_message = get_employee_welcome_message(team['name'])
                await send_or_edit_message(callback.message, welcome_message)

        employee = await db_get_employee(callback.from_user.id)
        tg_id = employee["tg_id"]
        last_report_date = await db_get_last_report_date(tg_id)
        from bot.utils.text_constants import get_user_info_template
        user_info = await get_user_info_template(employee, last_report_date)
        await callback.message.answer(user_info, reply_markup=change_data_inline_keyboard())
        logging.info(
            f"Новый сотрудник {user_data['full_name']} ({callback.from_user.id}) зарегистрирован. GitVerse: пропуск")
    except Exception as e:
        logging.error(f"Ошибка при регистрации с пропуском GitVerse: {e}")
        from bot.utils.text_constants import get_error_message
        await send_or_edit_message(callback, get_error_message("registration"))

    await state.clear()
    await callback.answer()
