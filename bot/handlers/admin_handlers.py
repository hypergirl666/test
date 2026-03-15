import logging

from aiogram.filters import Command
from aiogram.types import Message
from aiogram.types import BufferedInputFile

from bot.config import ADMINS_TG_IDS
from bot.core import router
from bot.core.database import db_add_curator
from bot.utils.utils import send_or_edit_message
from bot.utils.token_report import generate_token_report


@router.message(Command("add_curator"))
async def add_curator_command(message: Message):
    """
    Команда /add_curator <tg_id>: доступна только администраторам.
    """
    user_id = message.from_user.id
    if user_id not in ADMINS_TG_IDS:
        return

    # Извлекаем аргумент tg_id
    parts = (message.text or "").strip().split()
    if len(parts) < 2:
        await send_or_edit_message(message, "ℹ️ Использование: /add_curator tg_id")
        return
    try:
        target_tg_id = int(parts[1])
    except ValueError:
        await send_or_edit_message(message, "❌ tg_id должен быть числом. Пример: /add_curator 123456789")
        return

    try:
        await db_add_curator(target_tg_id)
        await send_or_edit_message(message, f"✅ Пользователь {target_tg_id} добавлен в кураторы (или уже был).")
    except Exception as e:
        logging.error(f"Ошибка добавления куратора {target_tg_id}: {e}")
        await send_or_edit_message(message, "❌ Ошибка при добавлении куратора. Попробуйте позже.")


@router.message(Command("token_report"))
async def token_report_command(message: Message):
    """
    Команда /token_report [days]: доступна только администраторам.
    Генерирует отчет по использованию токенов LLM.
    """
    user_id = message.from_user.id
    if user_id not in ADMINS_TG_IDS:
        return

    # Извлекаем аргумент days
    parts = (message.text or "").strip().split()
    days = 7  # По умолчанию 7 дней
    
    if len(parts) >= 2:
        try:
            days = int(parts[1])
            if days < 1 or days > 365:
                await send_or_edit_message(message, "⚠️ Количество дней должно быть от 1 до 365")
                return
        except ValueError:
            await send_or_edit_message(message, "❌ Количество дней должно быть числом")
            return

    try:
        # Генерируем отчет
        text_report, unified_chart = await generate_token_report(days)
        
        # Сначала отправляем график отдельным сообщением (если есть)
        if unified_chart:
            try:
                # Проверяем, что график не пустой
                unified_chart.seek(0, 2)  # Переходим в конец
                chart_size = unified_chart.tell()
                unified_chart.seek(0)  # Возвращаемся в начало
                
                if chart_size > 0:
                    chart_data = unified_chart.read()
                    unified_chart.seek(0)  # Возвращаемся в начало для возможного повторного чтения
                    
                    chart_file = BufferedInputFile(
                        chart_data,
                        filename=f"token_report_{days}d.png"
                    )

                    await message.answer_photo(chart_file)
                    
                unified_chart.close()
            except Exception as e:
                logging.error(f"Ошибка отправки графика: {e}", exc_info=True)
                try:
                    unified_chart.close()
                except:
                    pass
        
        # Затем отправляем текст отдельным сообщением
        try:
            await message.answer(text_report, parse_mode='HTML')
        except Exception as e:
            logging.error(f"Ошибка отправки текстового отчёта: {e}", exc_info=True)
            await send_or_edit_message(message, f"❌ Ошибка при отправке текстового отчёта: {e}")
            
    except Exception as e:
        logging.error(f"Ошибка генерации отчета по токенам: {e}", exc_info=True)
        await send_or_edit_message(message, f"❌ Ошибка при генерации отчета: {e}")