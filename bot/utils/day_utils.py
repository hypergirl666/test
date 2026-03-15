"""
Утилиты для работы с днями недели
"""

from datetime import date, timedelta

DAY_CODES = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
DAY_INDEX = {code: idx for idx, code in enumerate(DAY_CODES)}


def days_to_russian(days_string: str) -> str:
    """
    Преобразует строку с английскими днями недели в русские сокращения.
    Сначала сортирует дни в порядке планировщика (mon,tue,wed,thu,fri,sat,sun),
    затем переводит в русские сокращения.
    
    Args:
        days_string: строка с днями (например, 'tue,wed,thu,fri')
        
    Returns:
        str: строка с русскими сокращениями (например, 'ВТ,СР,ЧТ,ПТ')
    """
    if not days_string:
        return ""
    
    # Маппинг английских дней на русские сокращения
    day_mapping = {
        'mon': 'ПН',
        'tue': 'ВТ', 
        'wed': 'СР',
        'thu': 'ЧТ',
        'fri': 'ПТ',
        'sat': 'СБ',
        'sun': 'ВС'
    }
    
    # Порядок сортировки для планировщика (mon=1, tue=2, ..., sun=0)
    sort_order = {
        'mon': 1, 'tue': 2, 'wed': 3, 'thu': 4, 
        'fri': 5, 'sat': 6, 'sun': 0
    }
    
    # Разбиваем строку на отдельные дни
    days = days_string.split(',')
    
    # Сортируем дни в порядке планировщика
    sorted_days = sorted(days, key=lambda x: sort_order.get(x.strip(), 0))
    
    # Преобразуем каждый день в русские сокращения
    russian_days = []
    for day in sorted_days:
        day = day.strip()
        if day in day_mapping:
            russian_days.append(day_mapping[day])
        else:
            # Если день не распознан, оставляем как есть
            russian_days.append(day)
    
    return ','.join(russian_days)


def calculate_morning_days_from_report_days(report_days: str) -> str:
    """
    Вычисляет дни утренних дейли из дней отчетов.
    Дни утренних дейли совпадают с днями отчетов.
    
    Args:
        report_days: строка с днями отчетов (например, 'tue,wed,thu,fri')
        
    Returns:
        str: дни утренних дейли в том же формате
    """
    return report_days


def calculate_evening_days_from_report_days(report_days: str) -> str:
    """
    Вычисляет дни вечерних дейли из дней отчетов.
    Дни вечерних дейли = сдвиг на один день раньше, чем дни отчетов.
    
    Args:
        report_days: строка с днями отчетов (например, 'tue,wed,thu,fri')
        
    Returns:
        str: дни вечерних дейли в том же формате
    """
    if not report_days:
        return ""
    
    # Маппинг для сдвига на один день раньше
    day_mapping = {
        'mon': 'sun',
        'tue': 'mon', 
        'wed': 'tue',
        'thu': 'wed',
        'fri': 'thu',
        'sat': 'fri',
        'sun': 'sat'
    }
    
    # Разбиваем строку на отдельные дни
    days = report_days.split(',')
    
    # Применяем маппинг к каждому дню
    evening_days = []
    for day in days:
        day = day.strip()
        if day in day_mapping:
            evening_days.append(day_mapping[day])
        else:
            # Если день не распознан, оставляем как есть
            evening_days.append(day)
    
    return ','.join(evening_days)


def get_computed_team_days(report_days: str) -> dict:
    """
    Вычисляет все дни команды из дней отчетов.
    
    Args:
        report_days: строка с днями отчетов
        
    Returns:
        dict: словарь с computed_days, morning_days, evening_days
    """
    morning_days = calculate_morning_days_from_report_days(report_days)
    evening_days = calculate_evening_days_from_report_days(report_days)
    
    return {
        'report_days': report_days,
        'morning_days': morning_days,
        'evening_days': evening_days
    } 


def normalize_report_day_indices(report_days: str | None) -> list[int]:
    """
    Преобразует строку с днями отчётов в список индексов дней недели (0=понедельник).
    Если строка пустая — возвращает пустой список.
    """
    if not report_days:
        return []

    indices = []
    for day in report_days.split(','):
        code = day.strip().lower()
        if code in DAY_INDEX:
            indices.append(DAY_INDEX[code])
    return indices


def get_monday(date_obj: date) -> date:
    """Возвращает понедельник недели для переданной даты."""
    return date_obj - timedelta(days=date_obj.weekday())


def get_next_monday(date_obj: date) -> date:
    """
    Возвращает ближайший понедельник (если дата уже понедельник — её же).
    """
    if date_obj.weekday() == 0:
        return date_obj
    return date_obj + timedelta(days=(7 - date_obj.weekday()))


def calculate_sprint_end_date(start_date: date, duration_weeks: int) -> date:
    """
    Возвращает дату окончания спринта (включительно).
    """
    duration_weeks = max(duration_weeks, 1)
    total_days = duration_weeks * 7
    return start_date + timedelta(days=total_days - 1)


def calculate_last_report_date(start_date: date, duration_weeks: int, report_days: str | None) -> date:
    """
    Вычисляет дату последнего отчёта внутри спринта, отталкиваясь от настроенных дней отчётов.
    Если подходящая дата не найдена, возвращается конец спринта.
    """
    allowed_days = normalize_report_day_indices(report_days)
    end_date = calculate_sprint_end_date(start_date, duration_weeks)

    current = end_date
    while current >= start_date:
        if current.weekday() in allowed_days:
            return current
        current -= timedelta(days=1)
    return end_date


def calculate_expected_reports_count(start_date: date, end_date: date, report_days: str | None) -> int:
    """
    Считает количество дней, когда должны были быть отчеты в заданном диапазоне (включительно).
    """
    allowed_days = normalize_report_day_indices(report_days)
    count = 0
    current = start_date
    while current <= end_date:
        if current.weekday() in allowed_days:
            count += 1
        current += timedelta(days=1)
    return count
