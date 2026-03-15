import os
import json

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
BOARD_URL = os.getenv('BOARD_URL')

ADMINS_TG_IDS: set[int] = set(int(x) for x in (json.loads(os.getenv('ADMINS_TG_IDS', '[]')) or []))
# LLM настройки
FOLDER_ID = os.getenv('FOLDER_ID')
AUTH_KEY = os.getenv('AUTH_KEY')

# Настройка часового пояса (по умолчанию Екатеринбург)
TIMEZONE = os.getenv('TIMEZONE', 'Asia/Yekaterinburg')

# Чат/топик для еженедельного отчёта по токенам
WEEKLY_TOKEN_REPORT_CHAT_ID = int(os.getenv('WEEKLY_TOKEN_REPORT_CHAT_ID', '0') or 0)
WEEKLY_TOKEN_REPORT_TOPIC_ID = int(os.getenv('WEEKLY_TOKEN_REPORT_TOPIC_ID', '0') or 0) if os.getenv('WEEKLY_TOKEN_REPORT_TOPIC_ID') else None

ERROR_LOG_CHAT_ID = int(os.getenv('ERROR_LOG_CHAT_ID', '0') or 0) if os.getenv('ERROR_LOG_CHAT_ID') else None
ERROR_LOG_TOPIC_ID = int(os.getenv('ERROR_LOG_TOPIC_ID')) if os.getenv('ERROR_LOG_TOPIC_ID') else None

# Константы времени для дейли
TIME_CONSTANTS = {
    'morning': {
        'db_value': 'morning',
        'display': '9:00',
        'icon': '☀️',
        'name': 'Утреннее дейли',
        'days': 'tue,wed,thu,fri'
    },
    'evening': {
        'db_value': 'evening',
        'display': '22:00',
        'icon': '🌙',
        'name': 'Вечернее дейли',
        'days': 'mon,tue,wed,thu'
    }
}

# Настройки времени по умолчанию для команд
DEFAULT_TEAM_TIMES = {
    'morning_time': '09:00',
    'evening_time': '22:00',
    'report_time': '10:00',
    'morning_days': 'tue,wed,thu,fri',
    'evening_days': 'mon,tue,wed,thu',
    'report_days': 'tue,wed,thu,fri'
}

# Предустановленные варианты времени
PRESET_TIMES = [
    '08:00', '08:30', '09:00', '09:30', '10:00', '10:30',
    '11:00', '11:30', '12:00', '12:30', '13:00', '13:30',
    '14:00', '14:30', '15:00', '15:30', '16:00', '16:30',
    '17:00', '17:30', '18:00', '18:30', '19:00', '19:30',
    '20:00', '20:30', '21:00', '21:30', '22:00', '22:30',
    '23:00', '23:30'
]


# Функция для получения часа и минуты из display
def get_time_from_display(display: str) -> tuple:
    """Извлекает час и минуту из строки времени в формате HH:MM"""
    hour, minute = map(int, display.split(':'))
    return hour, minute


# Добавляем hour и minute к каждой записи TIME_CONSTANTS
for time_key, time_data in TIME_CONSTANTS.items():
    hour, minute = get_time_from_display(time_data['display'])
    time_data['hour'] = hour
    time_data['minute'] = minute

# Время отправки отчетов
REPORT_DISPLAY = '10:00'
REPORT_SEND_TIME = {
    'display': REPORT_DISPLAY,
    'days': 'tue,wed,thu,fri'  # Отчеты отправляются во вторник,пятницу
}

# Добавляем hour и minute к REPORT_SEND_TIME
hour, minute = get_time_from_display(REPORT_SEND_TIME['display'])
REPORT_SEND_TIME['hour'] = hour
REPORT_SEND_TIME['minute'] = minute

# Для обратной совместимости
MORNING_DB_VALUE = TIME_CONSTANTS['morning']['db_value']
EVENING_DB_VALUE = TIME_CONSTANTS['evening']['db_value']
MORNING_DISPLAY = TIME_CONSTANTS['morning']['display']
EVENING_DISPLAY = TIME_CONSTANTS['evening']['display']

# Константы для валидации
MAX_NAME_LENGTH = 50
MAX_ROLE_LENGTH = 30
MAX_TEAM_NAME_LENGTH = 50
MAX_MESSAGE_LENGTH = 3950

# Максимальная длина числовой части ID чата/топика Telegram (цифр)
CHAT_ID_MAX_DIGITS = 18

# Общие сообщения об ошибках
ERROR_MESSAGES = {
    'name_too_long': f"Слишком длинное имя. Максимум {MAX_NAME_LENGTH} символов. Введите имя снова:",
    'role_too_long': f"Слишком длинная роль. Максимум {MAX_ROLE_LENGTH} символов. Введите роль снова:",
    'invalid_name_format': "Неверный формат имени. Имя должно содержать имя и инициал фамилии (например: Иван И. или Иван Ив.). Введите имя снова:",
}
