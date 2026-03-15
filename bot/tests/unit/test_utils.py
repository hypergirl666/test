import pytest
from bot.utils import utils

@pytest.mark.parametrize("date_str,expected", [
    ("15-01-2025", "15-01-2025"),
    ("15.01.2025", "15-01-2025"),
    ("15 01 2025", "15-01-2025"),
    ("2025-01-15", None),
    ("", None),
    (None, None),
])
def test_parse_date_flexible(date_str, expected):
    assert utils.parse_date_flexible(date_str) == expected

