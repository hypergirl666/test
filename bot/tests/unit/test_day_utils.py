from bot.utils import day_utils

def test_days_to_russian():
    assert day_utils.days_to_russian('mon,tue,wed') == 'ПН,ВТ,СР'
    assert day_utils.days_to_russian('fri,thu') == 'ЧТ,ПТ'
    assert day_utils.days_to_russian('') == ''
    assert day_utils.days_to_russian('abc') == 'abc'
    assert day_utils.days_to_russian('sun,mon') == 'ВС,ПН'

def test_calculate_morning_days_from_report_days():
    assert day_utils.calculate_morning_days_from_report_days('mon,tue') == 'mon,tue'
    assert day_utils.calculate_morning_days_from_report_days('') == ''

def test_calculate_evening_days_from_report_days():
    # Проверяем сдвиг дней
    assert day_utils.calculate_evening_days_from_report_days('mon,tue,wed') == 'sun,mon,tue'
    assert day_utils.calculate_evening_days_from_report_days('') == ''
    assert day_utils.calculate_evening_days_from_report_days('abc') == 'abc'

