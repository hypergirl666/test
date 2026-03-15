"""
Модуль для работы с пресетами настроек команд
"""


def get_team_preset_settings(preset_choice: str):
    """Получение настроек команды для выбранного пресета"""
    if preset_choice == 'daily_reports':
        # Ежедневные отчёты - ежедневные опросы + недельные отчеты по пятницам
        return {
            'morning_time': '09:00',
            'evening_time': '18:00',
            'report_time': '10:00',
            'report_days': 'mon,tue,wed,thu,fri',
            'questions_json': [
                {
                    "id": 1,
                    "text": "Что ты сделал вчера?",
                    "field": "yesterday",
                    "time_variants": {"morning": "Что ты сделал вчера?", "evening": "Что ты сделал сегодня?"},
                    "board_related": False
                },
                {
                    "id": 2,
                    "text": "Что планируешь сделать сегодня?",
                    "field": "today",
                    "time_variants": {"morning": "Что планируешь сделать сегодня?", "evening": "Что планируешь сделать завтра?"},
                    "board_related": False
                },
                {
                    "id": 3,
                    "text": "Какие есть трудности или проблемы? (Если нет, напишите 'нет')",
                    "field": "problems",
                    "time_variants": {},
                    "board_related": False
                }
            ]
        }
    else:
        # Недельные планы и отчёты - планы по понедельникам + сводка по пятницам (или по умолчанию)
        return {
            'morning_time': '09:00',
            'evening_time': '18:00',
            'report_time': '10:00',
            'report_days': 'fri',
            'questions_json': [
                {
                    "id": 1,
                    "text": "Что было выполнено на этой неделе?",
                    "field": "weekly_progress",
                    "time_variants": {},
                    "board_related": False
                },
                {
                    "id": 2,
                    "text": "Какие есть трудности или проблемы? (Если нет, напишите 'нет')",
                    "field": "problems",
                    "time_variants": {},
                    "board_related": False
                }
            ]
        }
