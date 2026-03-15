import logging

from aiogram import F
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, Message

from bot.core import bot, group_router
from bot.utils.filters import GroupChatFilter

# Применяем фильтр групповых чатов к роутеру
group_router.message.filter(GroupChatFilter())
group_router.chat_member.filter(GroupChatFilter())


def is_group_chat(message: Message) -> bool:
    """Проверяет, является ли чат групповым"""
    return message.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]


@group_router.message(Command("show_id"))
async def handle_show_id(message: Message):
    """Обработчик команды /show_id - показывает ID чата и топика (только в групповых чатах)"""
    logging.info(f"Команда /show_id вызвана в чате {message.chat.type} (ID: {message.chat.id})")

    # Проверяем, что это групповой чат
    if not is_group_chat(message):
        user_id = message.from_user.id
        logging.info(f"Показываем ID пользователя: {user_id}")
        await message.reply(
            f"🏷️ Ваш ID: <code>{user_id}</code>\n\nЕсли вы хотите узнать информацию о чате, добавьте бота в групповой чат.")
        return

    chat_id = message.chat.id
    chat_type = message.chat.type
    chat_title = message.chat.title if hasattr(message.chat, 'title') else "Группа"

    # Получаем ID топика, если это форум
    topic_id = None
    if hasattr(message, 'message_thread_id') and message.message_thread_id:
        topic_id = message.message_thread_id

    # Формируем ответ
    response = f"📋 <b>Информация о чате:</b>\n\n"
    response += f"🆔 <b>ID чата:</b> <code>{chat_id}</code>\n"

    if topic_id:
        response += f"🏷️ <b>ID топика:</b> <code>{topic_id}</code>\n"
    else:
        if chat_type == ChatType.SUPERGROUP:
            response += "💡 <b>Для работы с топиками:</b>\n"
            response += "Перейдите в нужный топик и напишите <code>/show_id</code> чтобы узнать его ID.\n\n"
        else:
            response += f"🏷️ <b>ID топика:</b> <code>Эта группа не имеет топиков</code>\n"

    response += f"\n💡 <i>Эти данные можно использовать для настройки бота в групповых чатах.</i>"

    await message.reply(response)
