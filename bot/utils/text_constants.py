# Текстовые константы для сообщений

# Сообщения помощи
CHAT_ID_HELP = "💡 <i>Добавьте бота в командный чат и введите в командном чате команду <code>/show_id</code></i>"
CHAT_ID_REMOVE_HELP = "💡 <i>Если хотите убрать чат, введите '0'</i>"
TOPIC_HELP = "💡 <i>Топики — это отдельные темы внутри супергрупп (если в группе несколько чатов по темам). Чтобы бот знал, куда отправлять отчёты, нужно указать ID нужного топика. Получить этот ID можно, отправив команду <code>/show_id</code> внутри нужной темы. Если вы не используете топики или хотите, чтобы отчёты приходили в основной чат (General), просто введите '0'.</i>"
BOARD_LINK_HELP = (
    '💡 <i>Ссылка на доску будет отображаться во втором вопросе дейли — это поможет участникам команды быстро '
    'находить актуальную информацию о задачах.</i>\n\n'
    '💡 <i>Если укажете <b>GitVerse</b> tasktracker (например: '
    '<code>https://gitverse.ru/org/repo/tasktracker?view=board</code>), бот будет спрашивать у участников ник GitVerse '
    'при регистрации и во втором вопросе присылать персональную ссылку на доску с фильтром по задачам конкретного участника.</i>'
)
DAYS_HELP = "💡<i> Даты утреннего и вечернего дейли формируются автоматически в зависимости от выбранных дат отчетов</i>"
DAILY_QUES_HELP = "💡<i>Вопросы направляются утренней группе в день отчёта, вечерней — накануне вечером. Отчёты передаются менеджеру в назначенные дни и время</i>"

# Сообщения об ошибках
MANAGER_ACCESS_ERROR = "❌ У вас нет прав для редактирования команды. Эта функция доступна только менеджерам команд."
TEAM_NOT_FOUND_ERROR = "❌ Команда не найдена."

# Сообщения валидации
CHAT_ID_FORMAT_ERROR = f"❌ Неверный формат ID чата. ID чата должен начинаться с '-' и содержать только цифры.\n\n{CHAT_ID_HELP}\n\nПопробуйте ещё раз:"
TOPIC_ID_FORMAT_ERROR = f"❌ ID топика должен быть целым числом. Попробуйте ещё раз:\n\n{TOPIC_HELP}"
CHAT_ID_LENGTH_ERROR = f"❌ ID чата должен содержать не более 18 цифр.\n\n{CHAT_ID_HELP}\n\nПопробуйте ещё раз:"
TOPIC_ID_LENGTH_ERROR = f"❌ ID топика должен содержать не более 18 цифр.\n\n{TOPIC_HELP}"

QUESTIONS_EDIT_HELP = (
    "💡 Введите текст вопроса. Поддерживаются вариации:\n"
    "• Основной текст (общий для утра/вечера)\n"
    "• Вариации: [morning: текст] [evening: текст]\n"
    "• Поле для хранения (например: yesterday)\n"
    "• Связь с доской (добавляет ссылку на board_link)"
)


async def get_user_info_template(employee, last_report_date=None):
    """
    Генерирует единый шаблон информации о пользователе
    
    Args:
        employee: словарь с данными сотрудника
        last_report_date: дата последнего отчета (опционально)
    
    Returns:
        str: отформатированный текст с информацией о пользователе
    """
    from bot.config import EVENING_DB_VALUE, MORNING_DB_VALUE

    tg_id = employee["tg_id"]
    full_name = employee["full_name"]
    role = employee["role"]
    daily_time = employee["daily_time"]
    vacation_start = employee["vacation_start"]
    vacation_end = employee["vacation_end"]

    # Получаем время из команды
    display_time = ""  # значение по умолчанию
    timezone_str = "ЕКБ"

    team_name = None
    if employee['team_id']:
        from bot.core.database import db_get_team_by_id
        team = await db_get_team_by_id(employee['team_id'])
        if team:
            try:
                tz = team['timezone']
            except Exception:
                tz = None
            if tz == 'Europe/Moscow':
                timezone_str = 'МСК'
            team_name = team.get('name')
            if daily_time == MORNING_DB_VALUE:
                display_time = team['morning_time']
            else:
                display_time = team['evening_time']

    # Формируем информацию об отпуске
    if vacation_start and vacation_end:
        vacation_info = f"• <b>Даты отпуска:</b> {vacation_start} — {vacation_end}"
    else:
        vacation_info = f"• <b>Даты отпуска:</b> N/A"

    # Формируем информацию о последнем отчете
    if last_report_date:
        last_report_info = f"• <b>Последний отчёт:</b> {last_report_date}"
    else:
        last_report_info = ""

    # Основные моменты о DailyBot
    bot_team_scope = f"в команде \"{team_name}\":" if team_name else "в вашей команде:"
    bot_info = (
        f"<b>🤖 Как работает DailyBot {bot_team_scope}</b>\n"
        f"<blockquote>• <b>Автоматические опросы</b> - бот присылает вопросы каждый будний день в {display_time} {timezone_str}\n"
        "• <b>Гибкие ответы</b> - можете отвечать текстом или голосовым сообщением\n"
        "• <b>Отчёты для команды</b> - ваши ответы автоматически отправляются менеджеру</blockquote>\n\n"
    )

    # оформление данных пользователя в виде телеграмовской цитаты
    # GitVerse ник, если у команды есть gitverse-доска
    gitverse_line = ""
    try:
        if employee['team_id']:
            from bot.core.database import db_get_team_by_id
            team = await db_get_team_by_id(employee['team_id'])
            if team and team['board_link']:
                from bot.utils.utils import is_gitverse_board_link
                if is_gitverse_board_link(team['board_link']):
                    gitverse_nick = (employee['gitverse_nickname'] if 'gitverse_nickname' in employee.keys() else None) or '—'
                    gitverse_line = f"<b>GitVerse ник:</b> {gitverse_nick}\n"
    except Exception:
        gitverse_line = ""

    user_data = (
        f"💼 <b>Ваши данные:</b>\n"
        f"<blockquote>"
        f"<b>ID:</b> <code>{tg_id}</code>\n"
        f"<b>Имя:</b> {full_name}\n"
        f"<b>Роль:</b> {role}\n"
        f"<b>Время дейли:</b> {display_time} по {timezone_str}\n"
        f"{gitverse_line}"
        f"{last_report_info}\n"
        f"{vacation_info}\n"
        f"</blockquote>\n"
        "💡 <i>Вы можете изменить свои данные в настройках</i>"
    )

    template = bot_info + user_data

    return template


async def get_user_info_quote(employee, last_report_date=None):
    """
    Генерирует короткую цитату с данными пользователя для отображения в настройках
    """
    from bot.config import MORNING_DB_VALUE

    tg_id = employee["tg_id"]
    full_name = employee["full_name"]
    role = employee["role"]
    daily_time = employee["daily_time"]
    vacation_start = employee["vacation_start"]
    vacation_end = employee["vacation_end"]

    # Получаем время из команды
    display_time = "09:00"  # значение по умолчанию
    timezone_str = "ЕКБ"

    if employee['team_id']:
        from bot.core.database import db_get_team_by_id
        team = await db_get_team_by_id(employee['team_id'])
        if team:
            try:
                tz = team['timezone']
            except Exception:
                tz = None
            if tz == 'Europe/Moscow':
                timezone_str = 'МСК'
            display_time = team['morning_time'] if daily_time == MORNING_DB_VALUE else team['evening_time']

    # Формируем информацию об отпуске
    if vacation_start and vacation_end:
        vacation_info = f"• <b>Даты отпуска:</b> {vacation_start} — {vacation_end}"
    else:
        vacation_info = f"• <b>Даты отпуска:</b> N/A"

    # Формируем информацию о последнем отчете
    last_report_info = f"• <b>Последний отчёт:</b> {last_report_date}" if last_report_date else ""

    # Возвращаем только блок с данными пользователя
    # GitVerse ник в короткой цитате при наличии gitverse-доски
    gitverse_line = ""
    try:
        if employee['team_id']:
            from bot.core.database import db_get_team_by_id
            team = await db_get_team_by_id(employee['team_id'])
            if team and team['board_link']:
                from bot.utils.utils import is_gitverse_board_link
                if is_gitverse_board_link(team['board_link']):
                    gitverse_nick = (employee['gitverse_nickname'] if 'gitverse_nickname' in employee.keys() else None) or '—'
                    gitverse_line = f"<b>GitVerse ник:</b> {gitverse_nick}\n"
    except Exception:
        gitverse_line = ""

    return (
        f"💼 <b>Ваши данные:</b>\n"
        f"<blockquote>"
        f"<b>ID:</b> <code>{tg_id}</code>\n"
        f"<b>Имя:</b> {full_name}\n"
        f"<b>Роль:</b> {role}\n"
        f"<b>Время дейли:</b> {display_time} по {timezone_str}\n"
        f"{gitverse_line}"
        f"{last_report_info}\n"
        f"{vacation_info}\n"
        f"</blockquote>"
    )


async def get_manager_info_template(team, report_time):
    """
    Генерирует единый шаблон информации о менеджере и команде с красивым оформлением

    Args:
        team: словарь с данными команды
        report_time: время отправки отчетов

    Returns:
        str: отформатированный текст с информацией о менеджере и команде
    """
    # Получаем количество участников команды
    from bot.core.database import db_get_team_employees, db_get_active_sprint
    employees = await db_get_team_employees(team['id'])
    employees_count = len(employees) if employees else 0

    recommendation = ""
    if employees_count == 0:
        recommendation = "\n\n⚠️ <b>Рекомендация:</b> Добавьте участников в команду для полноценной работы"
    if not team['chat_id']:
        recommendation += "\n⚠️ <b>Рекомендация:</b> Отчёты отправляюстя только Вам. Чтобы бот отправлял отчёты в командный чат добавьте его в настройках"

    # Используем функцию приветствия
    hello = f"👋 <b>Добро пожаловать, Менеджер команды '{team['name']}'!</b>\n\n"
    welcome_text = get_manager_functional(report_time)

    # Используем функцию настроек команды с количеством участников
    team_settings = get_team_settings_template(team, "Информация о команде", employees_count)

    template = hello + f"{team_settings}"+f"{DAILY_QUES_HELP}\n\n" + welcome_text + recommendation

    return template


def get_team_settings_template(team, title="Текущие настройки", employees_count=None):
    """
    Генерирует единый шаблон настроек команды
    
    Args:
        team: словарь с данными команды
        title: заголовок (по умолчанию "Текущие настройки")
        employees_count: количество участников команды (опционально)
    
    Returns:
        str: отформатированный текст с настройками команды
    """
    # Формируем информацию о количестве участников
    employees_info = f"\n<b>👥 Количество участников:</b> {employees_count}" if employees_count is not None else ""

    # Формируем информацию о чате
    chat_info = team['chat_id'] or 'Не настроен'

    # Формируем информацию о временных настройках
    from bot.utils.day_utils import days_to_russian

    morning_time = team['morning_time']
    evening_time = team['evening_time']
    report_time = team['report_time']
    report_days = team['report_days']

    # Вычисляем дни для утренних и вечерних дейли
    from bot.utils.day_utils import get_computed_team_days
    computed_days = get_computed_team_days(report_days)
    morning_days = days_to_russian(computed_days['morning_days'])
    evening_days = days_to_russian(computed_days['evening_days'])
    report_days_russian = days_to_russian(report_days)

    # Определяем ярлык часового пояса
    try:
        tz_label = 'МСК' if (team['timezone'] == 'Europe/Moscow') else 'ЕКБ'
    except Exception:
        tz_label = 'ЕКБ'

    # Используем общий шаблон для временных настроек
    time_settings = get_time_settings_template(morning_time, evening_time, report_time,
                                               morning_days, evening_days, report_days_russian,
                                               tz_label)

    template = (
        f"<b>{title}</b>\n"
        f"<blockquote><b>ID чата:</b> {chat_info}\n"
        f"<b>ID топика:</b> {team['chat_topic_id'] or 'Не используется'}\n"
        f"<b>Ссылка на доску:</b> {team['board_link'] if team['board_link'] else 'Не добавлена'}{employees_info}</blockquote>\n\n"
        f"<b>Временные настройки:</b>\n"
        f"{time_settings}\n\n"
    )

    return template


def get_length_validation_message(field_name, min_length=None, max_length=None):
    """
    Генерирует сообщение об ошибке валидации длины поля
    
    Args:
        field_name: название поля
        min_length: минимальная длина (опционально)
        max_length: максимальная длина (опционально)
    
    Returns:
        str: отформатированное сообщение об ошибке
    """
    def _pluralize_symbol(count: int) -> str:
        count = abs(int(count))
        last_two = count % 100
        last = count % 10
        if 11 <= last_two <= 14:
            return "символов"
        if last == 1:
            return "символ"
        if 2 <= last <= 4:
            return "символа"
        return "символов"

    if min_length and max_length:
        return f"❌ {field_name} должно содержать от {min_length} до {max_length} символов. Попробуйте ещё раз:"
    elif min_length:
        return f"❌ {field_name} должно содержать минимум {min_length} {_pluralize_symbol(min_length)}. Попробуйте ещё раз:"
    elif max_length:
        return f"❌ {field_name} должно содержать максимум {max_length} {_pluralize_symbol(max_length)}. Попробуйте ещё раз:"
    else:
        return f"❌ {field_name} имеет недопустимую длину. Попробуйте ещё раз:"


def get_manager_functional(report_time):
    """
    Генерирует приветственное сообщение для менеджера с красивым оформлением и подробными описаниями функций
    
    Args:
        report_time: время отправки отчетов
    
    Returns:
        str: отформатированное приветственное сообщение с HTML-разметкой
    """
    template = (
        f"<b>Функции</b>\n"
        f"<blockquote>👥 <b>Сотрудники</b> - просмотр списка всех участников команды\n"
        f"👤 <b>Добавить</b> - создание пригласительной ссылки для добавления новых сотрудников в команду\n"
        f"📈 <b>Отчёт</b> - получение текущего отчета по команде с информацией о выполненных задачах и проблемах\n"
        f"🚀 <b>Запустить опрос</b> - проведение дейли-опроса вручную для сбора актуальной информации от команды\n"
        f"🏁 <b>Спринт</b> - настройка длительности, запрос планов и получение итогового отчёта\n"
        f"🔧 <b>Настройки</b> - изменение названия команды, настройка чата для отчетов, топика и ссылки на доску задач</blockquote>"
    )

    return template


def get_access_error_message(action_description=""):
    """
    Генерирует универсальное сообщение об ошибке доступа
    
    Args:
        action_description: описание действия (опционально)
    
    Returns:
        str: отформатированное сообщение об ошибке доступа
    """
    if action_description:
        return f"❌ У вас нет прав для выполнения этого действия: {action_description}. Эта функция доступна только менеджерам команд."
    else:
        return "❌ У вас нет прав для выполнения этого действия. Эта функция доступна только менеджерам команд."


def get_invite_created_message(team_name, invite_link):
    """
    Генерирует сообщение о создании пригласительной ссылки
    
    Args:
        team_name: название команды
        invite_link: ссылка для приглашения
    
    Returns:
        str: отформатированное сообщение о создании ссылки
    """
    return (
        f"🔗 <b>Пригласительная ссылка создана!</b>\n\n"
        f"<b>Ссылка для команды '{team_name}':</b>\n"
        f"<code>{invite_link}</code>\n\n"
        f"<b>Статус:</b> ✅ Активна\n"
        f"<b>Срок действия:</b> Без ограничений\n\n"
        "Отправьте эту ссылку новым сотрудникам для регистрации в команде."
    )


def get_invite_menu_message(team_name, invite_link, status, created_date):
    """
    Генерирует сообщение меню управления приглашением
    
    Args:
        team_name: название команды
        invite_link: ссылка для приглашения
        status: статус ссылки (активна/неактивна)
        created_date: дата создания
    
    Returns:
        str: отформатированное сообщение меню приглашения
    """
    return (
        f"🔗 <b>Пригласительная ссылка команды '{team_name}'</b>\n\n"
        f"<b>Ссылка:</b>\n"
        f"<code>{invite_link}</code>\n\n"
        f"<b>Статус:</b> {status}\n"
        f"<b>Создана:</b> {created_date}\n"
        f"<b>Срок действия:</b> Без ограничений\n\n"
        "Отправьте эту ссылку новым сотрудникам для регистрации в команде."
    )


def get_survey_group_selection_message(team_name):
    """
    Генерирует сообщение для выбора группы для опроса
    
    Args:
        team_name: название команды
    
    Returns:
        str: отформатированное сообщение
    """
    return (
        f"Какую группу сотрудников команды '{team_name}' опросить?\n"
        "Всем сотрудникам из этой группы будут отправлены вопросы"
    )


def get_survey_sent_message(count, group_name):
    """
    Генерирует сообщение об успешной отправке опроса
    
    Args:
        count: количество сотрудников
        group_name: название группы
    
    Returns:
        str: отформатированное сообщение об успешной отправке опроса
    """
    return f"✅ Приглашения отправлены {count} сотрудникам из {group_name}."


def get_field_input_message(field_name):
    """
    Генерирует сообщение для ввода нового значения поля
    
    Args:
        field_name: название поля
    
    Returns:
        str: отформатированное сообщение
    """
    return f"Введите новое значение для поля '{field_name}':"


def get_data_updated_message():
    """
    Генерирует сообщение об успешном обновлении данных
    
    Returns:
        str: отформатированное сообщение
    """
    return "<b>✅ Данные успешно обновлены!</b>\n\nВы всегда можете вернуться в главное меню."


def get_field_updated_message(field_name, new_value, additional_info=""):
    """
    Генерирует сообщение об успешном изменении конкретного поля
    
    Args:
        field_name: название измененного поля
        new_value: новое значение
        additional_info: дополнительная информация (опционально)
    
    Returns:
        str: отформатированное сообщение
    """
    message = f"✅ <b>{field_name} успешно изменен на '{new_value}'!</b>"

    if additional_info:
        message += f"\n\n{additional_info}"

    return message


def get_item_removed_message(item_name, additional_info=""):
    """
    Генерирует сообщение об успешном удалении элемента
    
    Args:
        item_name: название удаленного элемента
        additional_info: дополнительная информация (опционально)
    
    Returns:
        str: отформатированное сообщение
    """
    message = f"✅ <b>{item_name} успешно удален!</b>"

    if additional_info:
        message += f"\n\n{additional_info}"

    return message


def get_change_cancelled_message():
    """
    Генерирует сообщение об отмене изменения данных
    
    Returns:
        str: отформатированное сообщение
    """
    return "Изменение отменено."


def get_error_start_again_message():
    """
    Генерирует сообщение об ошибке с предложением начать сначала
    
    Returns:
        str: отформатированное сообщение
    """
    return "Произошла ошибка. Пожалуйста, начните сначала /start"


def get_no_data_message(context, entity_name):
    """
    Универсальная функция для сообщений об отсутствии данных
    
    Args:
        context: контекст (например, "команде", "группе")
        entity_name: название сущности
    
    Returns:
        str: отформатированное сообщение
    """
    return f"В {context} '{entity_name}' пока нет данных."


def get_processing_message(action, entity_name):
    """
    Универсальная функция для сообщений о выполнении действий
    
    Args:
        action: действие (например, "Формирую отчет", "Отправляю опрос")
        entity_name: название сущности
    
    Returns:
        str: отформатированное сообщение
    """
    return f"{action} {entity_name}..."


def get_error_message(error_type="general", action_description="", additional_info=""):
    """
    Универсальная функция для генерации сообщений об ошибках
    
    Args:
        error_type: тип ошибки ("general", "voice", "date", "registration", "team_creation", "invalid_link")
        action_description: описание действия, при котором произошла ошибка (опционально)
        additional_info: дополнительная информация (опционально)
    
    Returns:
        str: отформатированное сообщение об ошибке
    """
    if error_type == "voice":
        return "Произошла ошибка при обработке голосового сообщения. Попробуйте еще раз."
    elif error_type == "date":
        return "Произошла ошибка при проверке дат. Попробуйте снова."
    elif error_type == "registration":
        return "❌ Произошла ошибка при регистрации. Попробуйте снова /start."
    elif error_type == "team_creation":
        return "❌ <b>Произошла ошибка при создании команды.</b>\n\nПожалуйста, попробуйте еще раз."
    elif error_type == "invalid_link":
        return (
            "❌ <b>Некорректная ссылка!</b>\n\n"
            "Ссылка должна начинаться с http:// или https://\n"
            "Пожалуйста, введите корректную ссылку на доску:"
        )
    else:  # general
        if action_description:
            return f"❌ <b>Произошла ошибка при {action_description}.</b>\n\nПожалуйста, попробуйте еще раз."
        else:
            return "❌ <b>Произошла ошибка.</b>\n\nПожалуйста, попробуйте еще раз."


def get_report_accepted_message():
    """
    Генерирует сообщение о принятии отчета
    
    Returns:
        str: отформатированное сообщение
    """
    return "<b>✅ Спасибо! Ваш отчёт принят и будет отправлен менеджеру.</b>\n\nВы всегда можете вернуться в главное меню."


def get_welcome_message():
    """
    Генерирует приветственное сообщение
    
    Returns:
        str: отформатированное приветственное сообщение
    """
    return (
        "<b>👋 Добро пожаловать в DailyBot!</b>\n\n"
        "Благодарим Вас, что воспользовались нашим ботом\n"
        "Его миссия - помогать командам проводить ежедневные опросы (дейли), чтобы отслеживать прогресс и вовремя выявлять проблемы.\n\n"

        "<b>🎯 Как это работает:</b>\n"
        "• <b>Менеджеры</b> создают команды и настраивают автоматические опросы\n"
        "• <b>Сотрудники</b> получают вопросы каждый день в назначенное время\n"
        "• <b>Отчёты</b> собираются и отправляются менеджерам\n\n"

        "<b>📋 Что спрашивает бот:</b>\n"
        "1. Что сделали вчера\n"
        "2. Что планируете сделать сегодня\n"
        "3. Какие есть трудности или проблемы\n\n"

        "<b>💡 Возможности:</b>\n"
        "• Поддержка голосовых сообщений\n"
        "• Автоматические напоминания\n"
        "• Отправка отчётов в чат команды\n"
        "• Summary отчётов\n"
        "• Интеграция с доской задач\n\n"
        "<b>Как начать?</b>\n"
        "Если Вы менеджер команды - создайте команду\n"
        "Если Вы сотрудник - перейдите по ссылке от менеджера команд\n\n"
        
        "Если у Вас возникают вопросы, затруднения, пожелания - сразу пишите @justmatthew989\n"
        "Будем благодарны за обратную связь."
    )


def get_team_created_message(team_name):
    """
    Генерирует сообщение о создании команды
    
    Args:
        team_name: название команды
    
    Returns:
        str: отформатированное сообщение
    """
    return (
        f"🎉 <b>Команда '{team_name}' успешно создана!</b>\n\n"
        "<b>📋 Что дальше?</b>\n"
        "• Добавьте сотрудников через пригласительную ссылку\n"
        "• Настройте чат для отправки отчётов\n"
        "• Укажите ссылку на доску задач\n"
        "Бот будет автоматически отправлять опросы каждый день в назначенное время!"
    )


def get_employee_welcome_message(team_name):
    """
    Генерирует приветственное сообщение для нового сотрудника команды
    
    Args:
        team_name: название команды
    
    Returns:
        str: отформатированное приветственное сообщение
    """
    return (
        f"🎉 <b>Добро пожаловать в команду '{team_name}'!</b>\n\n"

        "<b>📋 Стандартные вопросы дейли:</b>\n"
        "1️⃣ <b>Что сделали вчера?</b> - расскажите о выполненных задачах\n"
        "2️⃣ <b>Что планируете сделать сегодня?</b> - поделитесь планами на день\n"
        "3️⃣ <b>Какие есть трудности или проблемы?</b> - сообщите о блокерах\n\n"
        "Ваш первый опрос придет в назначенное время!"
    )


def get_team_invite_accepted_message(team_name):
    """
    Генерирует сообщение о принятии приглашения в команду
    
    Args:
        team_name: название команды
    
    Returns:
        str: отформатированное сообщение
    """
    return (
        f"✅ <b>Приглашение принято!</b>\n\n"
        f"🎉 <b>Вы вступаете в команду:</b> {team_name}\n\n"
        f"📋 <b>После регистрации вы будете получать приглашения на дейли опросы в назначенное время</b>\n\n"
        f"<b>Начнём регистрацию!</b>\n"
        f"Пожалуйста, <b>введите ваше имя и первую букву фамилии (Пример: Иван Ив или Иван И)</b>:"
    )


def get_already_registered_message():
    """
    Генерирует сообщение о том, что пользователь уже зарегистрирован
    
    Returns:
        str: отформатированное сообщение
    """
    return "❌ Вы уже зарегистрированы в системе. Пригласительные ссылки предназначены только для новых пользователей."


def get_create_team_message():
    """
    Генерирует сообщение для создания новой команды
    
    Returns:
        str: отформатированное сообщение
    """
    return (
        "🏗️ <b>Создание новой команды</b>\n\n"
        "Пожалуйста, <b>введите название команды</b>:\n\n"
        "ℹ️ <i>Один пользователь может участвовать не более, чем в <b>15</b> командах.</i>"
    )


def get_team_preset_selection_message():
    """
    Генерирует сообщение для выбора пресета настроек команды

    Returns:
        str: отформатированное сообщение
    """
    return (
        "🎯 <b>Выберите тип работы команды</b>\n\n"
        "📊 <b>Ежедневные отчёты</b>\n"
        "• Ежедневные опросы: что сделали/планируют/трудности\n"
        "• Недельные спринты с отчётами по пятницам\n"
        "• Подходит для команд с ежедневным прогрессом\n\n"
        "📅 <b>Недельные планы и отчёты</b>\n"
        "• Планы на неделю по понедельникам\n"
        "• Сводка прогресса по пятницам\n"
        "• Подходит для команд с недельным планированием\n\n"
        "💡 <i>Все настройки можно изменить позже в меню управления командой.</i>"
    )


def get_team_limit_reached_message() -> str:
    """Единый текст про лимит команд (для создания и присоединения)."""
    return (
        "❌ <b>Лимит команд исчерпан.</b>\n\n"
        "Вы уже состоите в 15 командах. Присоединение или создание новых недоступно."
    )


def get_curator_required_message() -> str:
    """Сообщение, когда создание команды доступно только кураторам."""
    return (
        "❌ <b>Недостаточно прав.</b>\n\n"
        "Создавать команды могут только кураторы. Обратитесь к администратору. @justmatthew989"
    )
# Обратная совместимость
get_create_team_limit_reached_message = get_team_limit_reached_message


def get_join_team_message():
    """
    Генерирует сообщение для присоединения к команде
    
    Returns:
        str: отформатированное сообщение
    """
    return (
        "🔗 <b>Присоединение к команде</b>\n\n"
        "Запросите пригласительную ссылку у своего менеджера.\n\n"
        "Менеджер может создать ссылку в разделе '👤 Добавить сотрудников'."
    )


def get_team_name_confirmation_message(team_name, user_id):
    """
    Генерирует сообщение подтверждения названия команды
    
    Args:
        team_name: название команды
        user_id: ID пользователя
    
    Returns:
        str: отформатированное сообщение
    """
    return (
        f"✅ Название команды: <b>{team_name}</b>\n"
        f"✅ Telegram ID менеджера: <code>{user_id}</code>\n\n"
        f"📋 <b>Хотите добавить групповой чат для отправки отчётов?</b>\n\n"
        f"💡 <i>Если добавите чат, ежедневные отчёты будут отправляться и в чат команды, и менеджеру.\n"
        f"Если пропустите, отчёты будут приходить только менеджеру в личные сообщения.</i>"
    )


def get_add_chat_message():
    """
    Генерирует сообщение для добавления группового чата
    
    Returns:
        str: отформатированное сообщение
    """
    return (
        "📋 <b>Добавление группового чата</b>\n\n"
        "Пожалуйста, <b>введите ID чата команды</b> (например: -1001234567890):\n\n"
        f"{CHAT_ID_HELP}"
    )


def get_chat_skipped_message():
    """
    Генерирует сообщение о пропуске добавления чата
    
    Returns:
        str: отформатированное сообщение
    """
    return (
        "⏭️ Чат команды пропущен.\n\n"
        "Вы можете добавить <b>ссылку на доску команды</b> (Например, Miro).\n\n"
        f"{BOARD_LINK_HELP}"
    )


def get_chat_id_confirmation_message(chat_id):
    """
    Генерирует сообщение подтверждения ID чата
    
    Args:
        chat_id: ID чата
    
    Returns:
        str: отформатированное сообщение
    """
    return (
        f"✅ ID чата команды: <code>{chat_id}</code>\n\n"
        "Теперь <b>введите ID топика чата</b>:\n\n"
        f"{TOPIC_HELP}"
    )


def get_topic_confirmation_message(chat_topic):
    """
    Генерирует сообщение подтверждения ID топика
    
    Args:
        chat_topic: ID топика или None
    
    Returns:
        str: отформатированное сообщение
    """
    return (
        f"✅ ID топика чата: <code>{chat_topic or 'Не используется'}</code>\n\n"
        "Вы можете добавить<b> ссылку на доску команды</b> (Например, Miro).\n\n"
        f"{BOARD_LINK_HELP}"
    )


def get_add_board_message():
    """
    Генерирует сообщение для добавления ссылки на доску
    
    Returns:
        str: отформатированное сообщение
    """
    return (
        "📋 <b>Добавление ссылки на доску команды</b>\n\n"
        "Пожалуйста, <b>введите ссылку на доску команды</b>:\n\n"
        f"{BOARD_LINK_HELP}"
    )


def get_team_already_exists_error_message(team_name):
    """
    Генерирует сообщение об ошибке - команда уже существует
    
    Args:
        team_name: название существующей команды
    
    Returns:
        str: отформатированное сообщение об ошибке
    """
    return (
        f"❌ <b>Ошибка!</b>\n\n"
        f"Вы уже являетесь менеджером команды '{team_name}'.\n"
        f"Один пользователь может создать только одну команду."
    )


# --- Тексты для настроек времени команды ---
def get_team_time_settings_message(team_name: str, current_settings: dict) -> str:
    """Сообщение с текущими настройками времени команды"""
    from bot.utils.day_utils import days_to_russian

    morning_days = days_to_russian(current_settings.get('morning_days', 'tue,wed,thu,fri'))
    evening_days = days_to_russian(current_settings.get('evening_days', 'mon,tue,wed,thu'))
    report_days = days_to_russian(current_settings.get('report_days', 'tue,wed,thu,fri'))

    # Используем общий шаблон для временных настроек
    # tz_label для экрана настройки времени берём из полной информации team нет — оставляем пустым
    time_settings = get_time_settings_template(
        current_settings.get('morning_time', '09:00'),
        current_settings.get('evening_time', '22:00'),
        current_settings.get('report_time', '10:00'),
        morning_days, evening_days, report_days,
        None
    )

    return (
        f'<b>⏰ Настройки времени команды "{team_name}"</b>,'
        f'{time_settings}\n\n'
        f'{DAYS_HELP}\n\n'
        f'Выберите, что хотите изменить:')


def get_time_updated_message() -> str:
    """Сообщение об успешном обновлении времени"""
    return "✅ <b>Настройки времени успешно обновлены!</b>"


def get_time_validation_error_message(error: str) -> str:
    """Сообщение об ошибке валидации времени"""
    return f"❌ <b>Ошибка валидации:</b> {error}\n\nПопробуйте выбрать другое время."


def get_time_selection_message(time_type: str) -> str:
    """Сообщение для выбора времени с информацией о ручном вводе"""
    time_info = {
        'morning': {
            'name': '🌅 утреннего опроса',
            'description': 'Время, когда команда будет получать утренние дейли',
            'constraint': 'Должно быть <b>раньше</b> времени отчетов',
            'example': '09:00, 08:30, 10:15'
        },
        'evening': {
            'name': '🌆 вечернего опроса',
            'description': 'Время, когда команда будет получать вечерние дейли',
            'constraint': 'Должно быть <b>позже</b> времени отчетов',
            'example': '18:00, 19:30, 22:15'
        },
        'report': {
            'name': '📊 отправки отчетов',
            'description': 'Время, когда будут отправляться отчеты команды',
            'constraint': 'Должно быть <b>между</b> утренним и вечерним временем',
            'example': '10:00, 14:30, 16:15'
        }
    }

    info = time_info.get(time_type, {
        'name': time_type,
        'description': 'Выберите подходящее время',
        'constraint': '',
        'example': 'HH:MM'
    })

    return (
        f"<b>⏰ Выбор времени {info['name']}</b>\n"
        f"{info['description']}\n"
        f"<b>📋 Ограничение:</b> {info['constraint']}\n"
        f"<b>💡 Варианты ввода:</b>\n"
        f"• Выберите время из предложенных вариантов\n"
        f"• Или напишите время в формате {info['example']}\n"
    )


def get_days_selection_message(time_type: str) -> str:
    """Сообщение для выбора дней недели с подробной информацией"""


    return (
        f"<b>📅 Выбор дней недели для получения отчётов</b>\n\n"
        f"<b>💡 Как использовать:</b>\n"
        f"<blockquote>✅ <b>Зеленая галочка</b> = В этот день будут приходить отчёты\n"
        f"❌ <b>Красный крестик</b> = отчёты не будут приходить в этот день</blockquote>\n"
        f"Чтобы изменить статус дня, нажмите на него\n"
    )


def get_time_settings_saved_message() -> str:
    """Сообщение о сохранении настроек времени"""
    return "✅ <b>Настройки времени команды сохранены!</b>\n\nПланировщик будет обновлен автоматически."


def get_time_settings_cancel_message() -> str:
    """Сообщение об отмене настроек времени"""
    return "❌ <b>Настройка времени отменена.</b>"


def get_time_constraints_info() -> str:
    """Информация об ограничениях времени"""
    return (
        """<b>ℹ️ Ограничения времени:</b>

    • <b>Утренний опрос</b> должен быть <b>раньше</b> времени отчетов
    • <b>Время отчетов</b> должно быть <b>между</b> утренним и вечерним временем
    • <b>Вечерний опрос</b> должен быть <b>позже</b> времени отчетов
    
    <b>📝 Формат времени:</b> <b>HH:MM</b> (например: 09:30, 14:15, 22:00)
    
    <b>💡 Пример правильной последовательности:</b>
    🌅 Утренний опрос: 09:00
    📊 Отчеты: 10:00  
    🌆 Вечерний опрос: 18:00"""
    )


def get_time_settings_template(morning_time: str, evening_time: str, report_time: str,
                               morning_days: str, evening_days: str, report_days: str,
                               tz_label: str | None = None) -> str:
    """Шаблон для отображения временных настроек в виде цитаты"""
    tz_line = f"🌏 <b>Часовой пояс команды:</b> {tz_label}\n" if tz_label else ""
    return (
        f"<blockquote>"
        f"{tz_line}"
        f"🌅 <b>Утренний опрос:</b> {morning_time} ({morning_days})\n"
        f"🌆 <b>Вечерний опрос:</b> {evening_time} ({evening_days})\n"
        f"📊 <b>Отчеты:</b> {report_time} ({report_days})"
        f"</blockquote>"
    )

def get_no_reports_message() -> str:
    """Сообщение об отсутствии отчётов"""
    return "❌ Нет отчётов за указанный период."


def get_sprint_plan_instructions(team_name: str = None, period: str = None) -> str:
    """
    Генерирует инструкции для ввода планов спринта

    Args:
        team_name: название команды (опционально)
        period: период спринта (опционально)

    Returns:
        str: отформатированный текст инструкций
    """
    header = ""
    if team_name and period:
        header = f"🏁 <b>Новый спринт команды «{team_name}»</b>\nПериод: {period}\n\n"

    return (
        f"{header}"
        "<b>📋 Задачи на спринт</b>\n\n"
        "Опишите, над чем будете работать в этом спринте:\n\n"
        "• <b>Цели и результаты:</b> Чего планируете достичь?\n"
        "• <b>Конкретные задачи:</b> Какие работы будете выполнять?\n"
        "• <b>Ожидаемые результаты:</b> Что получится в итоге?\n\n"
    )