import logging

from aiogram import Bot
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message


def _get_chat_type(event: Message | CallbackQuery | ChatMemberUpdated) -> str:
    """Получить тип чата из события"""
    if hasattr(event, 'message') and event.message:
        return event.message.chat.type
    elif hasattr(event, 'chat'):
        return event.chat.type
    return "unknown"


class NotChannelFilter(BaseFilter):
    """Фильтр для игнорирования сообщений из каналов"""

    async def __call__(self, event: Message | CallbackQuery | ChatMemberUpdated) -> bool:
        return _get_chat_type(event) != "channel"


class PrivateChatFilter(BaseFilter):
    """Фильтр для обработки команд только в приватных чатах"""

    async def __call__(self, event: Message | CallbackQuery | ChatMemberUpdated) -> bool:
        return _get_chat_type(event) == "private"


class GroupChatFilter(BaseFilter):
    """Фильтр для обработки команд только в групповых чатах"""

    async def __call__(self, event: Message | CallbackQuery | ChatMemberUpdated) -> bool:
        chat_type = _get_chat_type(event)
        return chat_type in ["group", "supergroup"]


def apply_filters_to_router(router, bot: Bot):
    """Применяет фильтры к роутеру"""
    private_chat_filter = PrivateChatFilter()
    
    # Применяем фильтры: только приватные чаты
    router.message.filter(private_chat_filter)
    router.callback_query.filter(private_chat_filter) 