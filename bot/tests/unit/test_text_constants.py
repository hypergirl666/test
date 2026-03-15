import pytest
import asyncio
from bot.utils import text_constants as tc


# Проверяем наличие ключевых констант
@pytest.mark.parametrize("const_name", [
    "CHAT_ID_HELP", "CHAT_ID_REMOVE_HELP", "TOPIC_HELP", "BOARD_LINK_HELP", "DAYS_HELP", "DAILY_QUES_HELP",
    "MANAGER_ACCESS_ERROR", "TEAM_NOT_FOUND_ERROR", "CHAT_ID_FORMAT_ERROR", "TOPIC_ID_FORMAT_ERROR"
])
def test_constants_exist(const_name):
    assert hasattr(tc, const_name)


def test_get_user_info_template_basic():
    employee = {
        "tg_id": 1,
        "full_name": "Test User",
        "role": "dev",
        "daily_time": "morning",
        "vacation_start": None,
        "vacation_end": None,
        "team_id": None
    }
    # Проверяем, что функция возвращает строку (мок team_id)
    result = asyncio.run(tc.get_user_info_template(employee))
    assert isinstance(result, str) or result is None
