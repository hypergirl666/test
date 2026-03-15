import asyncio
import logging
import sys

# Импорты из наших модулей
from bot.core.database import close_pool, init_db
from bot.handlers import format_and_send_report, send_daily_questions
from bot.utils import apply_filters_to_router, create_and_start_scheduler
from bot.utils.scheduler_manager import scheduler_manager
from bot.utils.utils import setup_logging
from bot.handlers.weekly_plan_handlers import router as weekly_plan_router
from bot.handlers.sprint_handlers import router as sprint_router
from bot.utils.scheduler_jobs import create_and_start_weekly_plan_scheduler

async def main():
    """Основная функция запуска бота"""
    
    # Импорты бота и роутеров
    import bot.core.bot_instance as bot_instance
    bot_instance_obj = bot_instance.bot
    dp = bot_instance.dp
    router = bot_instance.router
    group_router = bot_instance.group_router
    dp.include_router(weekly_plan_router)
    dp.include_router(sprint_router)

    # Инициализация логгирования с правильным часовым поясом
    setup_logging()

    # Инициализация базы данных
    await init_db()

    # Применение фильтров к роутеру
    apply_filters_to_router(router, bot_instance_obj)
    
    # Настройка и запуск планировщика
    scheduler = await create_and_start_scheduler(send_daily_questions, format_and_send_report)


    # Регистрируем планировщик в глобальном менеджере
    scheduler_manager.set_scheduler(scheduler)
    
    # Инициализация планировщика для всех команд с настройками времени
    try:
        await scheduler_manager.setup_all_teams_jobs()
        logging.info("Планировщик команд с настройками времени инициализирован")
    except Exception as e:
        logging.error(f"Ошибка при инициализации планировщика команд: {e}")
    
    # Подключение роутеров к диспетчеру
    dp.include_router(router)
    dp.include_router(group_router)
    
    # Запуск бота
    logging.info("Запуск бота...")
    try:
        await dp.start_polling(bot_instance_obj)
    finally:
        # Корректно закрываем пул соединений БД
        try:
            await close_pool()
        except Exception:
            pass


if __name__ == '__main__':
    # Workaround для Windows + psycopg async: использовать совместимую политику цикла
    if sys.platform.startswith('win'):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass
    asyncio.run(main())