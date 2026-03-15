import logging

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.core import router
from bot.core.database import db_get_user_po_teams, db_get_membership, db_get_employee
from bot.utils import send_or_edit_message
from bot.utils.keyboards import employee_main_keyboard
from bot.core.database import db_get_user_memberships


@router.callback_query(F.data == "po_menu")
async def po_menu_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик меню Product Owner (заглушка)"""
    user_id = callback.from_user.id
    
    # Проверяем, является ли пользователь PO
    employee = await db_get_employee(user_id)
    if not employee:
        await callback.answer("❌ Пользователь не найден")
        return
    
    # Получаем список команд, где пользователь является PO
    po_teams = await db_get_user_po_teams(user_id)
    
    if not po_teams:
        # Если нет команд где он PO, проверяем текущую команду
        if employee.get('team_id'):
            membership = await db_get_membership(user_id, employee['team_id'])
            if not membership or not membership.get('is_po', False):
                await callback.answer("❌ У вас нет доступа к меню Product Owner")
                return
    
    # Формируем список команд
    teams_text = ""
    if po_teams:
        teams_text = "\n\n<b>Ваши команды как PO:</b>\n"
        for team in po_teams:
            teams_text += f"• {team['name']}\n"
    else:
        teams_text = "\n\n<i>Вы являетесь PO в текущей команде.</i>"
    
    # Меню PO с функционалом создания ТЗ
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Создать ТЗ", callback_data="po_create_tz")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="go_to_menu")]
    ])
    
    await send_or_edit_message(
        callback,
        f"👤 <b>Product Owner</b>\n\n"
        f"Добро пожаловать в меню Product Owner!{teams_text}\n\n"
        f"<b>Доступные функции:</b>\n"
        f"• 📋 Создать ТЗ - пошаговое создание технического задания",
        reply_markup=keyboard
    )
    await callback.answer()

