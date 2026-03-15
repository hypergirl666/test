from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import EVENING_DB_VALUE, MORNING_DB_VALUE

import logging

def daily_time_keyboard(team_settings=None, include_back: bool = False):
    """Клавиатура для выбора времени дейли с настройками команды
    
    Args:
        team_settings: словарь настроек команды для отображения времени
        include_back: добавлять ли кнопку «Назад» (для флоу изменения настроек сотрудника)
    """
    from bot.config import EVENING_DISPLAY, MORNING_DISPLAY

    # Используем время команды, если доступно, иначе используем значения по умолчанию
    if team_settings:
        morning_time = team_settings['morning_time']
        evening_time = team_settings['evening_time']
        tz_label = 'ЕКБ'
        try:
            tz_value = team_settings['timezone']
            if tz_value == 'Europe/Moscow':
                tz_label = 'МСК'
        except Exception:
            pass
    else:
        morning_time = MORNING_DISPLAY
        evening_time = EVENING_DISPLAY
        tz_label = 'ЕКБ'
    
    buttons = [
        [InlineKeyboardButton(text=f"🌅 Утреннее дейли ({morning_time} {tz_label})",
                                      callback_data=f"set_time_{MORNING_DB_VALUE}")],
        [InlineKeyboardButton(text=f"🌆 Вечернее дейли ({evening_time} {tz_label})",
                                      callback_data=f"set_time_{EVENING_DB_VALUE}")]
    ]
    if include_back:
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel_action")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def timezone_selection_keyboard():
    """Клавиатура выбора часового пояса команды"""
    buttons = [
        [InlineKeyboardButton(text="МСК (Europe/Moscow)", callback_data="set_timezone_msk")],
        [InlineKeyboardButton(text="ЕКБ (Asia/Yekaterinburg)", callback_data="set_timezone_ekb")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def team_preset_selection_keyboard():
    """Клавиатура выбора пресета настроек команды"""
    buttons = [
        [InlineKeyboardButton(text="📊 Ежедневные отчёты", callback_data="preset_daily_reports")],
        [InlineKeyboardButton(text="📅 Недельные планы и отчёты", callback_data="preset_weekly_plans")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def role_selection_keyboard(include_back: bool = False):
    """Клавиатура для выбора роли в 2 столбца
    
    Args:
        include_back: добавлять ли кнопку «Назад» (для флоу изменения настроек сотрудника)
    """
    buttons = [
        [
            InlineKeyboardButton(text="👨‍💻 Разработчик", callback_data="set_role_Разработчик"),
            InlineKeyboardButton(text="🎨 Дизайнер", callback_data="set_role_Дизайнер")
        ],
        [
            InlineKeyboardButton(text="📊 Аналитик", callback_data="set_role_Аналитик"),
            InlineKeyboardButton(text="🧪 Тестировщик", callback_data="set_role_Тестировщик")
        ],
        [
            InlineKeyboardButton(text="📈 Product Manager", callback_data="set_role_Product Manager"),
            InlineKeyboardButton(text="🔧 DevOps", callback_data="set_role_DevOps")
        ],
        [
            InlineKeyboardButton(text="📊 Data Scientist", callback_data="set_role_Data Scientist"),
            InlineKeyboardButton(text="🔒 Специалист по безопасности", callback_data="set_role_Специалист по безопасности")
        ]
    ]
    if include_back:
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel_action")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def team_time_settings_keyboard():
    """Клавиатура настроек времени команды - в несколько столбцов"""
    buttons = [
        [
            InlineKeyboardButton(text="🌅 Утренний опрос", callback_data="team_time_morning"),
            InlineKeyboardButton(text="🌆 Вечерний опрос", callback_data="team_time_evening")
        ],
        [
            InlineKeyboardButton(text="📊 Время отчетов", callback_data="team_time_report"),
            InlineKeyboardButton(text="📅 Дни недели", callback_data="team_time_days")
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel_team_edit_action")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def time_selection_keyboard(time_type: str, current_settings: dict = None):
    """Умная клавиатура выбора времени - показывает только логичные варианты"""
    from bot.config import PRESET_TIMES, get_time_from_display

    # Создаем более компактную клавиатуру с 3 кнопками в ряду
    buttons = []
    
    # Фильтруем время в зависимости от типа и текущих настроек
    filtered_times = []
    
    for time in PRESET_TIMES:
        # Конвертируем время в минуты для сравнения
        hour, minute = get_time_from_display(time)
        time_minutes = hour * 60 + minute
        
        # Определяем, подходит ли это время
        is_valid = True
        
        if current_settings:
            if time_type == 'morning':
                # Утреннее время должно быть меньше времени отчета
                if 'report_time' in current_settings:
                    report_hour, report_minute = get_time_from_display(current_settings['report_time'])
                    report_minutes = report_hour * 60 + report_minute
                    if time_minutes >= report_minutes:
                        is_valid = False
                        
            elif time_type == 'report':
                # Время отчета должно быть больше утреннего, но меньше вечернего
                if 'morning_time' in current_settings:
                    morning_hour, morning_minute = get_time_from_display(current_settings['morning_time'])
                    morning_minutes = morning_hour * 60 + morning_minute
                    if time_minutes <= morning_minutes:
                        is_valid = False
                        
                if 'evening_time' in current_settings:
                    evening_hour, evening_minute = get_time_from_display(current_settings['evening_time'])
                    evening_minutes = evening_hour * 60 + evening_minute
                    if time_minutes >= evening_minutes:
                        is_valid = False
                        
            elif time_type == 'evening':
                # Вечернее время должно быть больше времени отчета
                if 'report_time' in current_settings:
                    report_hour, report_minute = get_time_from_display(current_settings['report_time'])
                    report_minutes = report_hour * 60 + report_minute
                    if time_minutes <= report_minutes:
                        is_valid = False
        
        if is_valid:
            filtered_times.append(time)
    
    # Если нет подходящих вариантов, показываем все
    if not filtered_times:
        filtered_times = PRESET_TIMES
    
    # Группируем время по 3 кнопки в ряду для компактности
    for i in range(0, len(filtered_times), 3):
        row = []
        for j in range(3):
            if i + j < len(filtered_times):
                time = filtered_times[i + j]
                
                row.append(InlineKeyboardButton(
                    text=f"{time}", 
                    callback_data=f"team_time_{time_type}_{time}"
                ))
        buttons.append(row)
    
    # Добавляем кнопки навигации
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="team_time_settings")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)



def interactive_days_keyboard(time_type: str, current_days: str = None):
    """
    Интерактивная клавиатура для выбора дней недели с переключателями
    
    Args:
        time_type: тип времени (morning, evening, report)
        current_days: текущие дни в формате 'mon-fri' или список дней
    """
    # Определяем активные дни
    active_days = set()
    
    if current_days:
        if isinstance(current_days, str):
            # Просто разбиваем по дефисам (без специальных случаев)
            active_days = string_to_days(current_days)
        elif isinstance(current_days, (list, set)):
            # Если передан список или множество дней
            active_days = set(current_days)
    
    # Определяем дни недели и их отображение
    days_config = {
        'mon': 'ПН',
        'tue': 'ВТ', 
        'wed': 'СР',
        'thu': 'ЧТ',
        'fri': 'ПТ',
        'sat': 'СБ',
        'sun': 'ВС'
    }
    
    buttons = []
    row = []
    
    for day_code, day_display in days_config.items():
        # Определяем статус дня
        is_active = day_code in active_days
        status = "✅" if is_active else "❌"
        
        # Создаем кнопку
        button_text = f"{status}{day_display}"
        callback_data = f"toggle_day_{time_type}_{day_code}"
        
        row.append(InlineKeyboardButton(
            text=button_text,
            callback_data=callback_data
        ))
        
        # Добавляем кнопки по 3 в ряд
        if len(row) == 3:
            buttons.append(row)
            row = []
    
    # Добавляем оставшиеся кнопки
    if row:
        buttons.append(row)
    
    # Добавляем кнопки управления
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="team_time_settings")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def days_to_string(active_days: set) -> str:
    """
    Преобразует множество активных дней в строку для сохранения в БД
    
    Args:
        active_days: множество активных дней (например, {'mon', 'tue', 'wed'})
    
    Returns:
        str: строка в формате для БД (например, 'mon,tue,wed')
    """
    if not active_days:
        return ""
    
    # Сортируем дни в правильном порядке
    day_order = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    sorted_days = sorted(active_days, key=lambda x: day_order.index(x))
    
    return ','.join(sorted_days)


def string_to_days(days_string: str) -> set:
    """
    Преобразует строку дней в множество
    
    Args:
        days_string: строка дней (например, 'mon,tue,wed')
    
    Returns:
        set: множество дней
    """
    if not days_string:
        return set()
    
    return set(days_string.split(','))


def days_to_cron_days(days_string: str) -> str | None:
    """
    Преобразует строку дней в формат cron для APScheduler
    
    Args:
        days_string: строка дней в формате 'mon,fri' или 'mon,tue,wed'
    
    Returns:
        str | None: строка в формате cron для APScheduler или None, если дни не выбраны
    """
    if not days_string:
        return None
    
    # Просто сортируем дни по числовым значениям для APScheduler
    day_mapping = {
        'mon': 1, 'tue': 2, 'wed': 3, 
        'thu': 4, 'fri': 5, 'sat': 6, 'sun': 0
    }
    
    days = days_string.split(',')
    sorted_days = sorted(days, key=lambda x: day_mapping.get(x, 0))
    
    return ','.join(sorted_days)


def time_type_selection_keyboard():
    """Клавиатура выбора типа времени для настройки"""
    buttons = [
        [InlineKeyboardButton(text="🌅 Утренний опрос", callback_data="time_type_morning")],
        [InlineKeyboardButton(text="🌆 Вечерний опрос", callback_data="time_type_evening")],
        [InlineKeyboardButton(text="📊 Время отчетов", callback_data="time_type_report")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel_action")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_daily_keyboard(team_id: int = None, manual_poll: bool = False):
    """Клавиатура для подтверждения начала дейли опроса"""
    if team_id is not None:
        if manual_poll:
            callback_data = f"start_manual_poll_{team_id}"
            nothing_callback = f"nothing_done_{team_id}"
        else:
            callback_data = f"start_daily_poll_{team_id}"
            nothing_callback = f"nothing_done_{team_id}"
    else:
        if manual_poll:
            callback_data = "start_manual_poll"
            nothing_callback = "nothing_done"
        else:
            callback_data = "start_daily_poll"
            nothing_callback = "nothing_done"

    buttons = [
        [InlineKeyboardButton(text="✅ Да, готов(а)!", callback_data=callback_data)],
        [InlineKeyboardButton(text="Ничего не делал", callback_data=nothing_callback)]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def voice_confirmation_keyboard(question_type: str):
    """
    Клавиатура для подтверждения голосового ответа
    question_type: 'yesterday', 'today', 'problems'
    """
    buttons = [
        [
            InlineKeyboardButton(text="✅ Да, всё верно", callback_data=f"voice_confirm_{question_type}"),
            InlineKeyboardButton(text="❌ Нет, ответить заново", callback_data=f"voice_retry_{question_type}")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def missing_report_reply_keyboard(team_id: int):
    """Клавиатура для ответа на вопрос о пропущенном отчете"""
    buttons = [
        [InlineKeyboardButton(text="✍️ Ответить", callback_data=f"reply_missing_report_{team_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def change_data_keyboard():
    """Клавиатура для изменения данных"""
    buttons = [
        [InlineKeyboardButton(text="✏️ Изменить данные", callback_data="change_data_start")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def gitverse_nickname_keyboard():
    """Клавиатура шага ввода ника GitVerse с кнопкой пропуска"""
    buttons = [
        [InlineKeyboardButton(text="Пропустить", callback_data="gitverse_skip")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def what_to_change_keyboard(include_gitverse: bool = False, include_leave: bool = False):
    """Клавиатура выбора поля для изменения
    
    Args:
        include_gitverse: добавлять ли пункт изменения ника GitVerse
        include_leave: добавлять ли пункт выхода из команды
    """
    buttons = [
        [
            InlineKeyboardButton(text="Изменить Имя", callback_data="change_field_full_name"),
            InlineKeyboardButton(text="Изменить Роль", callback_data="change_field_role")
        ],
        [
            InlineKeyboardButton(text="Изменить Время", callback_data="change_field_daily_time"),
            InlineKeyboardButton(text="Изменить даты отпуска", callback_data="change_field_vacation")
        ]
    ]
    if include_gitverse:
        buttons.append([
            InlineKeyboardButton(text="Изменить GitVerse ник", callback_data="change_field_gitverse_nickname")
        ])
    if include_leave:
        buttons.append([
            InlineKeyboardButton(text="Выйти из команды", callback_data="leave_team")
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel_action")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_poll_keyboard():
    """Клавиатура подтверждения отправки опроса"""
    buttons = [
        [InlineKeyboardButton(text="✅ Да, отправить", callback_data="confirm_poll_yes")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="confirm_poll_no")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_delete_employee_keyboard(tg_id_to_delete: int):
    """Клавиатура подтверждения удаления сотрудника"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_employee_yes_{tg_id_to_delete}")],
        [InlineKeyboardButton(text="↩️ Отмена", callback_data="cancel_action")]
    ])


def menu_inline_keyboard():
    """Клавиатура с кнопкой возврата в меню"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Меню", callback_data="go_to_menu")]
    ])


# Алиас для обратной совместимости
change_data_inline_keyboard = change_data_keyboard


def team_action_keyboard():
    """Клавиатура для выбора действия с командой"""
    buttons = [
        [InlineKeyboardButton(text="📘 Руководство по использованию", url="https://telegra.ph/Rukovodstvo-po-Daily-Bot-08-20")],
        [
            InlineKeyboardButton(text="🏗️ Создать команду", callback_data="create_team"),
            InlineKeyboardButton(text="🔗 Присоединиться к команде", callback_data="join_team")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def team_action_keyboard_for_manager():
    """Клавиатура для выбора действия с командой (только присоединение для менеджеров)"""
    buttons = [
        [InlineKeyboardButton(text="🔗 Присоединиться к команде", callback_data="join_team")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def choose_team_keyboard(memberships: list[dict]):
    """Клавиатура выбора команды при множестве членств.
    Ожидает словари с ключами: team_id, team_name, is_manager, role
    """
    rows = []
    total_count=0
    for m in memberships:
        total_count+=1
        name = m.get('team_name') or m.get('name') or 'Команда'
        role = 'менеджер' if m.get('is_manager') else (m.get('role') or 'сотрудник')
        btn_text = f"{name} — {role}"
        rows.append([InlineKeyboardButton(text=btn_text, callback_data=f"choose_team_{m.get('team_id')}")])
    # Добавляем кнопку создания команды только если общее количество членств меньше 15
    if total_count < 15:
        rows.append([InlineKeyboardButton(text="🆕 Создать команду", callback_data="create_command_inline")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manager_keyboard_with_invite(sprint_enabled=False, has_active_sprint=False):
    """Клавиатура менеджера с кнопкой добавления сотрудников

    Args:
        sprint_enabled: Включены ли спринты в команде
        has_active_sprint: Есть ли активный спринт в команде
    """

    buttons = [
        [
            InlineKeyboardButton(text="👥 Сотрудники", callback_data="view_employees"),
            InlineKeyboardButton(text="👤 Добавить", callback_data="add_employees")
            
        ],
        [

            InlineKeyboardButton(text="📅 Мои планы", callback_data="view_weekly_plan"),
            InlineKeyboardButton(text="📅 Планы команды", callback_data="view_team_weekly_plans")

        ],
        [
            InlineKeyboardButton(text="🚀 Запустить опрос", callback_data="launch_survey"),
            InlineKeyboardButton(text="📈 Отчёт", callback_data="view_report")

        ]
    ]

    # Добавляем кнопку спринтов
    buttons.append([InlineKeyboardButton(text="🏁 Спринт", callback_data="open_sprint_menu")])

    buttons.extend([
        [
            InlineKeyboardButton(text="🔧 Настройки", callback_data="team_settings")
        ],
        [
            InlineKeyboardButton(text="📘 Руководство по использованию", url="https://telegra.ph/Rukovodstvo-po-Daily-Bot-08-20")
        ]
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def employee_main_keyboard(memberships_count: int, has_active_sprint: bool = False, is_po: bool = False):
    """Главное меню сотрудника с динамичной кнопкой смены/добавления команды."""
    buttons = [
        [InlineKeyboardButton(text="✏️ Изменить данные", callback_data="change_data_start")]
    ]
    if has_active_sprint:
        buttons.append([InlineKeyboardButton(text="🏁 Мои планы", callback_data="view_sprint_my_plans")])
    if is_po:
        buttons.append([InlineKeyboardButton(text="👤 Product Owner", callback_data="po_menu")])
    if memberships_count and memberships_count > 1:
        buttons.append([InlineKeyboardButton(text="🔀 Выбрать команду", callback_data="open_choose_team")])
    else:
        buttons.append([InlineKeyboardButton(text="➕ Добавить команду", callback_data="open_add_team")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def manager_main_keyboard(memberships_count: int, sprint_enabled: bool = False, has_active_sprint: bool = False,is_po: bool = False):
    """Главное меню менеджера c динамичной кнопкой смены/добавления команды."""
    base = manager_keyboard_with_invite(sprint_enabled, has_active_sprint).inline_keyboard[:]
    # Добавляем кнопку PO, если менеджер является PO
    if is_po:
        base.append([InlineKeyboardButton(text="👤 Product Owner", callback_data="po_menu")])
    if memberships_count and memberships_count > 1:
        base.append([InlineKeyboardButton(text="🔀 Выбрать команду", callback_data="open_choose_team")])
    else:
        base.append([InlineKeyboardButton(text="➕ Добавить команду", callback_data="open_add_team")])
    return InlineKeyboardMarkup(inline_keyboard=base)


def add_team_info_keyboard():
    """Клавиатура инфо-экрана добавления команды."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="go_to_menu")],
        [InlineKeyboardButton(text="🆕 Создать", callback_data="create_command_inline")]
    ])


def cancel_keyboard():
    """Клавиатура с кнопкой отмены"""
    buttons = [
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def cancel_team_edit_keyboard():
    """Клавиатура с кнопкой назад для настроек команды"""
    buttons = [
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel_team_edit_action")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_to_employee_settings_keyboard():
    """Клавиатура с кнопкой назад к настройкам сотрудника"""
    buttons = [
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel_action")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def board_link_edit_keyboard():
    """Клавиатура редактирования ссылки на доску: удалить/назад"""
    buttons = [
        [InlineKeyboardButton(text="🗑 Удалить", callback_data="delete_board_link")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel_team_edit_action")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def add_chat_choice_keyboard():
    """Клавиатура для выбора добавления чата команды"""
    buttons = [
        [InlineKeyboardButton(text="✅ Да, добавить чат", callback_data="add_chat_yes")],
        [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="add_chat_skip")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def add_board_choice_keyboard():
    """Клавиатура для выбора добавления ссылки на доску"""
    buttons = [
        [InlineKeyboardButton(text="✅ Да, добавить доску", callback_data="add_board_yes")],
        [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="add_board_skip")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def team_edit_keyboard():
    """Клавиатура для выбора поля команды для редактирования"""
    buttons = [
        [InlineKeyboardButton(text="✏️ Название команды", callback_data="edit_team_name")],
        [InlineKeyboardButton(text="✏️ Изменить свои данные", callback_data="change_data_start")],
        [
            InlineKeyboardButton(text="📱 ID чата", callback_data="edit_chat_id"),
            InlineKeyboardButton(text="📋 ID топика", callback_data="edit_chat_topic")
        ],
        [
            InlineKeyboardButton(text="🔗 Доска", callback_data="edit_board_link"),
            InlineKeyboardButton(text="⏰ Время", callback_data="team_time_settings")
        ],
        [InlineKeyboardButton(text="🌍 Часовой пояс", callback_data="edit_timezone")],
        [InlineKeyboardButton(text="👑 Режим менеджера", callback_data="manager_participation_settings")],
        [InlineKeyboardButton(text="👤 Добавить PO", callback_data="add_po")],
        [InlineKeyboardButton(text="🗑 Удалить команду", callback_data="delete_team")],
        [InlineKeyboardButton(text="❓ Вопросы опросов", callback_data="edit_questions")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel_team_edit")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons) 

def question_type_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора типа вопроса: общий или с вариациями"""
    buttons = [
        [InlineKeyboardButton(text="Общий", callback_data="question_type_common")],
        [InlineKeyboardButton(text="С вариациями (утро/вечер)", callback_data="question_type_variants")],
        [InlineKeyboardButton(text="⬅ Назад", callback_data="cancel_team_edit_action")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def team_questions_keyboard(questions: list = None) -> InlineKeyboardMarkup:
    """Клавиатура для редактирования вопросов"""
    buttons = []
    if questions:
        for q in questions:
            buttons.append([
                InlineKeyboardButton(
                    text=f"Удалить #{q['id']}: {q['text'][:20]}...",
                    callback_data=f"delete_question_{q['id']}"
                ),
                InlineKeyboardButton(
                    text=f"Редактировать #{q['id']}",
                    callback_data=f"edit_question_{q['id']}" 
                )
            ])
    buttons.append([InlineKeyboardButton(text="➕ Добавить вопрос", callback_data="add_question")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel_team_edit_action")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def yes_no_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура Да/Нет"""
    buttons = [
        [InlineKeyboardButton(text="Да", callback_data="yes")],
        [InlineKeyboardButton(text="Нет", callback_data="no")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def manager_participation_keyboard(current_state: bool):
    """Клавиатура выбора режима участия менеджера"""
    status = "✅ Участвую в опросах" if current_state else "❌ Не участвую в опросах"
    buttons = [
        [InlineKeyboardButton(text=status, callback_data="toggle_manager_participation")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="team_settings")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def select_team_keyboard(teams: list, action: str = "write") -> InlineKeyboardMarkup:
    prefix = "start_write_weekly_plan" if action == "write" else "view_weekly_plan"
    buttons = [
        [InlineKeyboardButton(text=team['name'], callback_data=f"{prefix}_{team['id']}")]
        for team in teams
    ]
    logging.info(f"Created select_team_keyboard with action={action}, buttons={[b[0].callback_data for b in buttons]}")
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def write_weekly_plan_keyboard(team_id: int) -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой 'Написать планы' для конкретной команды"""
    buttons = [
        [InlineKeyboardButton(text="✍️ Написать планы", callback_data=f"start_write_weekly_plan_{team_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def sprint_menu_keyboard(is_enabled: bool, has_active_sprint: bool = False, sprint_id: int | None = None, manager_participates: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура настроек спринта для менеджера"""
    toggle_text = "⏸️ Отключить спринты" if is_enabled else "✅ Включить спринты"
    toggle_callback = "sprint_disable" if is_enabled else "sprint_enable"
    buttons = []

    if is_enabled and has_active_sprint:
        # Есть активный спринт - показываем функции управления
        buttons.append([InlineKeyboardButton(text="📊 Промежуточный отчёт", callback_data="sprint_interim_report")])
        buttons.append([InlineKeyboardButton(text="🏁 Завершить досрочно", callback_data=f"manual_finish_sprint_{sprint_id}")])
        if manager_participates:
            buttons.append([InlineKeyboardButton(text="📝 Мои планы", callback_data="view_sprint_my_plans")])
    elif is_enabled and not has_active_sprint:
        # Спринты включены, но нет активного спринта - показываем кнопку запуска
        buttons.append([InlineKeyboardButton(text="🚀 Начать спринт сейчас", callback_data="start_sprint_now")])

    buttons.extend([
        [InlineKeyboardButton(text="⏱️ Изменить длительность", callback_data="sprint_change_duration")],
        [InlineKeyboardButton(text=toggle_text, callback_data=toggle_callback)],
    ])
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="go_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def sprint_duration_keyboard(current_duration: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора длительности спринта"""
    options = [1, 2, 3, 4, 5, 6]
    rows = []
    for weeks in options:
        label = f"{'✅ ' if weeks == current_duration else ''}{weeks} нед."
        rows.append([InlineKeyboardButton(text=label, callback_data=f"sprint_set_duration_{weeks}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="open_sprint_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sprint_my_plans_keyboard(team_id: int, sprint_id: int, has_plans: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура для просмотра личных спринтовых планов"""
    rows = []
    if has_plans:
        # Если есть планы - показываем кнопку "Дополнить планы"
        rows.append([InlineKeyboardButton(text="➕ Дополнить планы", callback_data=f"start_sprint_plan_{team_id}_{sprint_id}")])
    else:
        # Если планов нет - показываем кнопку "Написать планы"
        rows.append([InlineKeyboardButton(text="✍️ Написать планы", callback_data=f"start_sprint_plan_{team_id}_{sprint_id}")])
    # Кнопка "Назад" всегда присутствует
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="go_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def write_sprint_plan_keyboard(team_id: int, sprint_id: int) -> InlineKeyboardMarkup:
    """Кнопка запуска ввода планов на спринт"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Написать планы", callback_data=f"start_sprint_plan_{team_id}_{sprint_id}")]
    ])