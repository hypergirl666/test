from bot.utils import keyboards
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def test_daily_time_keyboard_default():
    kb = keyboards.daily_time_keyboard()
    assert isinstance(kb, InlineKeyboardMarkup)
    assert any(isinstance(btn, InlineKeyboardButton) for row in kb.inline_keyboard for btn in row)

def test_timezone_selection_keyboard():
    kb = keyboards.timezone_selection_keyboard()
    assert isinstance(kb, InlineKeyboardMarkup)
    assert any('set_timezone' in btn.callback_data for row in kb.inline_keyboard for btn in row)

def test_role_selection_keyboard():
    kb = keyboards.role_selection_keyboard()
    assert isinstance(kb, InlineKeyboardMarkup)
    assert any('set_role' in btn.callback_data for row in kb.inline_keyboard for btn in row)

def test_team_time_settings_keyboard():
    kb = keyboards.team_time_settings_keyboard()
    assert isinstance(kb, InlineKeyboardMarkup)
    assert any('team_time' in btn.callback_data or 'cancel_team_edit_action' in btn.callback_data for row in kb.inline_keyboard for btn in row)

