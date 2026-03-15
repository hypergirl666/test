import asyncio
import logging
from typing import Optional

import pytz
from datetime import datetime, date
from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.config import REPORT_SEND_TIME, TIME_CONSTANTS, TIMEZONE, WEEKLY_TOKEN_REPORT_CHAT_ID, WEEKLY_TOKEN_REPORT_TOPIC_ID
from bot.utils.utils import get_current_time, send_photo_with_retry, send_message_with_retry

# Ссылка на главный цикл событий бота, чтобы отправлять корутины из потоков
_MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None


def run_async_function(func, *args, **kwargs):
    """Синхронная обертка для выполнения асинхронных функций в планировщике.

    Работает корректно как из потока с активным asyncio-циклом, так и из
    потоков исполнителя (executor), где текущего цикла нет.
    """
    try:
        try:
            # Проверяем, есть ли запущенный цикл в текущем потоке
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Мы находимся в потоке с активным циклом событий — планируем задачу здесь
            asyncio.create_task(func(*args, **kwargs))
        else:
            # Текущего цикла нет (мы в потоке executor'а). Если известен главный цикл —
            # планируем в нём и дожидаемся выполнения синхронно.
            global _MAIN_LOOP
            if _MAIN_LOOP and _MAIN_LOOP.is_running():
                future = asyncio.run_coroutine_threadsafe(func(*args, **kwargs), _MAIN_LOOP)
                future.result()
            else:
                # Фоллбек: запускаем корутину в новом цикле (может быть невалидно для некоторых lib)
                asyncio.run(func(*args, **kwargs))
    except Exception as e:
        logging.error(f"Ошибка при выполнении асинхронной функции {func.__name__}: {e}", exc_info=True)



async def _send_report_for_team_impl(team_id: int):
    """Внутренняя реализация отправки отчета для команды (выполняется через очередь)."""
    from bot.handlers.manager_handlers import format_and_send_report
    from bot.handlers.daily_handlers import check_and_send_missing_report_question
    
    logging.info(f"Запуск отправки отчета для команды ID={team_id}")
    await format_and_send_report(send_to_chat=True, team_id=team_id)
    logging.info(f"Отчет отправлен для команды ID={team_id}")
    
    # Проверяем неотправленные отчеты и отправляем вопрос участникам
    await check_and_send_missing_report_question(team_id)


async def _send_daily_questions_to_team_impl(team_id: int, time_type: str):
    """Внутренняя реализация отправки daily-опросов для команды (выполняется через очередь)."""
    from bot.handlers.daily_handlers import send_daily_questions_to_team
    
    await send_daily_questions_to_team(team_id, time_type)


async def _send_daily_questions_to_team_queued(team_id: int, time_type: str):
    """Отправка daily-опросов для команды через очередь рассылок."""
    try:
        from bot.utils.notification_queue import get_notification_queue
        
        # Добавляем задачу в очередь рассылок вместо прямого выполнения
        queue = get_notification_queue()
        queue.add(func=_send_daily_questions_to_team_impl, task_args=(team_id, time_type))
        logging.info(f"Задача отправки {time_type} опроса для команды ID={team_id} добавлена в очередь рассылок")
    except Exception as e:
        logging.error(f"Ошибка при добавлении задачи отправки {time_type} опроса для команды ID={team_id}: {e}", exc_info=True)


async def _handle_sprint_cycle_queued(team_id: int):
    """Запуск цикла спринта через очередь рассылок (чтобы не забивать пул БД при массовом запуске)."""
    try:
        from bot.utils.notification_queue import get_notification_queue

        queue = get_notification_queue()
        queue.add(func=handle_sprint_cycle, task_args=(team_id,))
        logging.info(f"Задача цикла спринта для команды ID={team_id} добавлена в очередь рассылок")
    except Exception as e:
        logging.error(f"Ошибка при добавлении задачи цикла спринта для команды ID={team_id}: {e}", exc_info=True)


async def send_report_for_team(team_id: int):
    """Отправка отчета для одной конкретной команды (запуск из планировщика команды).
    
    Задача добавляется в очередь рассылок для последовательной обработки, чтобы избежать
    перегрузки пула соединений БД при одновременном запуске множества команд.
    """
    try:
        from bot.utils.notification_queue import get_notification_queue
        
        # Добавляем задачу в очередь рассылок вместо прямого выполнения
        queue = get_notification_queue()
        # Передаем аргументы через kwargs, чтобы избежать конфликта с позиционными параметрами метода add
        queue.add(func=_send_report_for_team_impl, task_args=(team_id,))
        logging.info(f"Задача отправки отчета для команды ID={team_id} добавлена в очередь рассылок")
    except Exception as e:
        logging.error(f"Ошибка при добавлении задачи отправки отчета для команды ID={team_id}: {e}", exc_info=True)


async def send_invitations_to_teams_by_time(time_type: str, time_value: str):
    """Отправка приглашений командам с определенным временем"""
    try:
        from bot.core.database import db_get_teams_by_time
        from bot.handlers.daily_handlers import send_daily_questions_to_team
        from bot.utils.day_utils import get_computed_team_days
        
        logging.info(f"Поиск команд с временем {time_type}: {time_value}")
        teams = await db_get_teams_by_time(time_type, time_value)
        logging.info(f"Найдено команд: {len(teams)}")
        
        for team in teams:
            logging.info(f"Обработка команды '{team['name']}' (ID: {team['id']})")
            
            # Вычисляем правильные дни для данного типа времени
            computed_days = get_computed_team_days(team['report_days'])
            current_day = get_current_time().strftime('%a').lower()[:3]  # mon, tue, etc.
            
            logging.info(f"Текущий день: {current_day}")
            logging.info(f"Дни отчетов: {team['report_days']}")
            logging.info(f"Утренние дни: {computed_days['morning_days']}")
            logging.info(f"Вечерние дни: {computed_days['evening_days']}")
            
            if time_type == 'morning':
                # Для утренних дейли используем computed_days['morning_days']
                morning_days = computed_days['morning_days'].split(',')
                logging.info(f"Проверка утренних дней: {morning_days}")
                if current_day in morning_days:
                    logging.info(f"Отправка утренних приглашений для команды '{team['name']}'")
                    await send_daily_questions_to_team(team['id'], time_type)
                    logging.info(f"Приглашения отправлены для команды '{team['name']}' (ID: {team['id']}) - {time_type} {time_value}")
                else:
                    logging.info(f"Текущий день {current_day} не входит в утренние дни {morning_days}")
            elif time_type == 'evening':
                # Для вечерних дейли используем computed_days['evening_days']
                evening_days = computed_days['evening_days'].split(',')
                logging.info(f"Проверка вечерних дней: {evening_days}")
                if current_day in evening_days:
                    logging.info(f"Отправка вечерних приглашений для команды '{team['name']}'")
                    await send_daily_questions_to_team(team['id'], time_type)
                    logging.info(f"Приглашения отправлены для команды '{team['name']}' (ID: {team['id']}) - {time_type} {time_value}")
                else:
                    logging.info(f"Текущий день {current_day} не входит в вечерние дни {evening_days}")
            
    except Exception as e:
        logging.error(f"Ошибка при отправке приглашений командам с временем {time_type} {time_value}: {e}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")


def _get_job_by_id(scheduler: AsyncIOScheduler, job_id: str) -> Job:
    """Получить задачу по ID"""
    try:
        return scheduler.get_job(job_id)
    except Exception:
        return None


def _add_or_update_job(scheduler: AsyncIOScheduler, job_id: str, func, trigger: CronTrigger, args=None) -> bool:
    """Добавить новую задачу или обновить существующую"""
    try:
        job = _get_job_by_id(scheduler, job_id)
        if job:
            # Получаем информацию о старой задаче
            old_trigger = job.trigger
            old_args = job.args
            
            # Проверяем, изменились ли параметры
            trigger_changed = str(old_trigger) != str(trigger)
            # Дополнительно учитываем смену часового пояса триггера
            try:
                old_tz = getattr(old_trigger, 'timezone', None)
                new_tz = getattr(trigger, 'timezone', None)
                if old_tz != new_tz:
                    trigger_changed = True
            except Exception:
                pass
            
            # Нормализуем аргументы для сравнения (преобразуем в кортежи)
            old_args_tuple = tuple(old_args) if old_args else ()
            new_args_tuple = tuple(args) if args else ()
            args_changed = old_args_tuple != new_args_tuple
            
            # Отладочная информация
            logging.debug(f"Сравнение аргументов для {job_id}: {old_args_tuple} vs {new_args_tuple} (изменены: {args_changed})")
            
            if trigger_changed or args_changed:
                # Обновляем существующую задачу
                scheduler.reschedule_job(job_id, trigger=trigger, args=args, misfire_grace_time=1200)
                
                # Логируем изменения
                changes = []
                if trigger_changed:
                    changes.append(f"триггер: {old_trigger} → {trigger}")
                if args_changed:
                    changes.append(f"аргументы: {old_args_tuple} → {new_args_tuple}")
                
                logging.info(f"Задача {job_id} обновлена: {', '.join(changes)}")
                
                # Показываем время следующего запуска (в TZ триггера)
                try:
                    tz = getattr(trigger, 'timezone', None)
                except Exception:
                    tz = None
                base_now = get_current_time()
                now_for_trigger = base_now.astimezone(tz) if tz else base_now
                next_run = trigger.get_next_fire_time(None, now_for_trigger)
                if next_run:
                    time_until = int((next_run - now_for_trigger).total_seconds())
                    hours, remainder = divmod(time_until, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                    logging.info(f"Следующий запуск {job_id}: {next_run.strftime('%Y-%m-%d %H:%M:%S')} (через {time_str})")
                else:
                    logging.warning(f"Не удалось определить время следующего запуска для {job_id}")
            else:
                logging.info(f"Задача {job_id} не изменилась, обновление не требуется")
        else:
            # Добавляем новую задачу
            scheduler.add_job(func, trigger=trigger, args=args, id=job_id, misfire_grace_time=1200)
            logging.info(f"Задача {job_id} добавлена с триггером {trigger}")
            
            # Показываем время первого запуска
            try:
                tz = getattr(trigger, 'timezone', None)
            except Exception:
                tz = None
            base_now = get_current_time()
            now_for_trigger = base_now.astimezone(tz) if tz else base_now
            next_run = trigger.get_next_fire_time(None, now_for_trigger)
            if next_run:
                time_until = int((next_run - now_for_trigger).total_seconds())
                hours, remainder = divmod(time_until, 3600)
                minutes, seconds = divmod(remainder, 60)
                time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                logging.info(f"Первый запуск {job_id}: {next_run.strftime('%Y-%m-%d %H:%M:%S')} (через {time_str})")
            else:
                logging.warning(f"Не удалось определить время первого запуска для {job_id}")
        return True
    except Exception as e:
        logging.error(f"Ошибка при добавлении/обновлении задачи {job_id}: {e}")
        return False


async def setup_team_scheduler_jobs(scheduler: AsyncIOScheduler, team_id: int):
    """Настройка задач планировщика для конкретной команды"""
    try:
        from bot.core.database import db_get_team_by_id
        from bot.handlers.daily_handlers import send_daily_questions_to_team
        from bot.utils.day_utils import get_computed_team_days
        
        team = await db_get_team_by_id(team_id)
        if not team:
            logging.error(f"Команда с ID {team_id} не найдена")
            return
        
        # Вычисляем morning_days и evening_days из report_days
        computed_days = get_computed_team_days(team['report_days'])
        morning_days = computed_days['morning_days']
        evening_days = computed_days['evening_days']
        
        logging.info(f"Настройка планировщика для команды '{team['name']}' (ID: {team_id})")
        logging.info(f"Время утреннего опроса: {team['morning_time']} ({morning_days})")
        logging.info(f"Время вечернего опроса: {team['evening_time']} ({evening_days})")
        logging.info(f"Время отчетов: {team['report_time']} ({team['report_days']})")
        
        # Функция для преобразования дней в формат APScheduler
        def days_to_cron_days(days_string: str) -> str | None:
            """Преобразует строку дней в формат cron для APScheduler"""
            if not days_string:
                return None
            
            # Просто сортируем дни по числовым значениям для APScheduler
            day_mapping = {
                'mon': 1, 'tue': 2, 'wed': 3, 
                'thu': 4, 'fri': 5, 'sat': 6, 'sun': 0
            }
            
            days = days_string.split(',')
            sorted_days = sorted(days, key=lambda x: day_mapping.get(x, 0))
            
            return ','.join(sorted_days)
        
        # Создаем обертки для корутин
        def morning_daily_wrapper():
            """Обертка для утреннего опроса команды"""
            run_async_function(_send_daily_questions_to_team_queued, team_id, 'morning')
        
        def evening_daily_wrapper():
            """Обертка для вечернего опроса команды"""
            run_async_function(_send_daily_questions_to_team_queued, team_id, 'evening')
        
        def report_wrapper():
            """Обертка для отправки отчета конкретной команды"""
            run_async_function(send_report_for_team, team_id)
        
        # Часовой пояс для этой команды
        try:
            team_timezone = team['timezone'] or TIMEZONE
        except Exception:
            team_timezone = TIMEZONE

        # Добавляем или обновляем задачи для утреннего опроса
        morning_hour, morning_minute = map(int, team['morning_time'].split(':'))
        morning_cron_days = days_to_cron_days(morning_days)
        morning_job_id = f'morning_daily_team_{team_id}'

        if morning_cron_days:
            morning_trigger = CronTrigger(
                hour=morning_hour,
                minute=morning_minute,
                day_of_week=morning_cron_days,
                timezone=team_timezone
            )
            _add_or_update_job(
                scheduler,
                morning_job_id,
                morning_daily_wrapper,
                morning_trigger
            )
        else:
            job = _get_job_by_id(scheduler, morning_job_id)
            if job:
                scheduler.remove_job(morning_job_id)
                logging.info(f"Задача {morning_job_id} удалена, так как дни не выбраны")
        
        # Добавляем или обновляем задачи для вечернего опроса
        evening_hour, evening_minute = map(int, team['evening_time'].split(':'))
        evening_cron_days = days_to_cron_days(evening_days)
        evening_job_id = f'evening_daily_team_{team_id}'

        if evening_cron_days:
            evening_trigger = CronTrigger(
                hour=evening_hour,
                minute=evening_minute,
                day_of_week=evening_cron_days,
                timezone=team_timezone
            )
            _add_or_update_job(
                scheduler,
                evening_job_id,
                evening_daily_wrapper,
                evening_trigger
            )
        else:
            job = _get_job_by_id(scheduler, evening_job_id)
            if job:
                scheduler.remove_job(evening_job_id)
                logging.info(f"Задача {evening_job_id} удалена, так как дни не выбраны")
        
        # Добавляем или обновляем задачи для отчетов
        report_hour, report_minute = map(int, team['report_time'].split(':'))
        report_cron_days = days_to_cron_days(team['report_days'])
        report_job_id = f'report_team_{team_id}'

        if report_cron_days:
            report_trigger = CronTrigger(
                hour=report_hour,
                minute=report_minute,
                day_of_week=report_cron_days,
                timezone=team_timezone
            )
            _add_or_update_job(
                scheduler,
                report_job_id,
                report_wrapper,
                report_trigger
            )
        else:
            job = _get_job_by_id(scheduler, report_job_id)
            if job:
                scheduler.remove_job(report_job_id)
                logging.info(f"Задача {report_job_id} удалена, так как дни не выбраны")

        # Настройка запроса планов спринта
        sprint_job_id = f'sprint_plan_team_{team_id}'
        def sprint_plan_wrapper():
            run_async_function(_handle_sprint_cycle_queued, team_id)

        # Спринт требует наличия хотя бы одного рабочего дня для отчетов
        if team.get('sprint_enabled') and team.get('report_days'):
            sprint_trigger = CronTrigger(
                day_of_week='mon',
                hour=morning_hour,
                minute=morning_minute,
                timezone=team_timezone
            )
            _add_or_update_job(
                scheduler,
                sprint_job_id,
                sprint_plan_wrapper,
                sprint_trigger
            )
        else:
            job = _get_job_by_id(scheduler, sprint_job_id)
            if job:
                scheduler.remove_job(sprint_job_id)
                if not team.get('sprint_enabled'):
                    logging.info(f"Задача {sprint_job_id} удалена, так как спринты отключены")
                else:
                    logging.info(f"Задача {sprint_job_id} удалена, так как дни не выбраны")
        
        logging.info(f"Планировщик настроен для команды '{team['name']}' (ID: {team_id})")
        
    except Exception as e:
        logging.error(f"Ошибка при настройке задач планировщика для команды {team_id}: {e}")


async def update_team_scheduler_jobs(scheduler: AsyncIOScheduler, team_id: int):
    """Обновление задач планировщика для команды"""
    await setup_team_scheduler_jobs(scheduler, team_id)


async def remove_team_scheduler_jobs(scheduler: AsyncIOScheduler, team_id: int):
    """Удаление задач планировщика для команды"""
    job_ids = [
        f'morning_daily_team_{team_id}',
        f'evening_daily_team_{team_id}',
        f'report_team_{team_id}'
    ]
    
    for job_id in job_ids:
        try:
            scheduler.remove_job(job_id)
            logging.info(f"Задача планировщика {job_id} удалена")
        except Exception as e:
            logging.warning(f"Не удалось удалить задачу планировщика {job_id}: {e}")


async def setup_all_teams_scheduler_jobs(scheduler: AsyncIOScheduler):
    """Настройка задач планировщика для всех команд"""
    try:
        from bot.core.database import db_get_all_teams_with_times
        
        teams = await db_get_all_teams_with_times()
        for team in teams:
            await setup_team_scheduler_jobs(scheduler, team['id'])

        logging.info(f"Задачи планировщика настроены для {len(teams)} команд")
        
    except Exception as e:
        logging.error(f"Ошибка при настройке задач планировщика для всех команд: {e}")


def _format_time_until(seconds: int) -> str:
    """Форматировать время до следующего запуска"""
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _get_job_name(job_id: str) -> str:
    """Получить человекочитаемое имя задачи"""
    job_names = {
        'morning_daily': "Утренний опрос",
        'evening_daily': "Вечерний опрос", 
        'report_team': "Отчет команды"
    }
    return job_names.get(job_id, job_id)


def log_next_run_times(scheduler: AsyncIOScheduler):
    """Логирует информацию о следующем выполнении задач"""
    try:
        base_now = get_current_time()
        for job in scheduler.get_jobs():
            try:
                tz = getattr(job.trigger, 'timezone', None)
                now_for_trigger = base_now.astimezone(tz) if tz else base_now
                next_run = job.trigger.get_next_fire_time(None, now_for_trigger)
                if next_run:
                    time_until = int((next_run - now_for_trigger).total_seconds())
                    job_name = _get_job_name(job.id)
                    formatted_time = _format_time_until(time_until)
                    logging.info(f"{job_name}: следующий запуск - {next_run.strftime('%Y-%m-%d %H:%M:%S')} "
                                f"(через {formatted_time})")
                else:
                    logging.warning(f"Не удалось определить время следующего выполнения для задачи {job.id}")
            except Exception as e:
                logging.error(f"Ошибка при получении времени выполнения для задачи {job.id}: {e}")
    except Exception as e:
        logging.error(f"Ошибка при логировании времени выполнения задач: {e}")


def _date_from_value(value) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _is_member_on_vacation(member: dict, reference_date: date) -> bool:
    start = member.get('vacation_start')
    end = member.get('vacation_end')
    if not start or not end:
        return False
    try:
        start_dt = datetime.strptime(start, '%d-%m-%Y').date()
        end_dt = datetime.strptime(end, '%d-%m-%Y').date()
    except Exception:
        return False
    return start_dt <= reference_date <= end_dt


def _format_sprint_plan_prompt(team: dict, sprint: dict) -> str:
    from bot.utils.text_constants import get_sprint_plan_instructions
    start = _date_from_value(sprint.get('start_date'))
    end = _date_from_value(sprint.get('end_date'))
    period = f"{start.strftime('%d.%m')} — {end.strftime('%d.%m')}"
    return get_sprint_plan_instructions(team_name=team['name'], period=period)


async def _send_sprint_plan_requests(team: dict, sprint: dict):
    from bot.core.database import db_get_team_members
    from bot.core.bot_instance import bot
    from bot.utils.keyboards import write_sprint_plan_keyboard

    members = await db_get_team_members(team['id'])
    if not members:
        logging.info("Команда %s не имеет участников для спринта", team['id'])
        return

    tz = pytz.timezone(team['timezone'])
    reference_date = datetime.now(tz).date()
    message_text = _format_sprint_plan_prompt(team, sprint)

    for member in members:
        tg_id = member['tg_id']
        if member.get('is_manager') and not member.get('is_participant'):
            continue
        if _is_member_on_vacation(member, reference_date):
            continue
        try:
            await bot.send_message(
                chat_id=tg_id,
                text=message_text,
                reply_markup=write_sprint_plan_keyboard(team['id'], sprint['id'])
            )
            logging.info("Запрос спринтовых планов отправлен пользователю %s", tg_id)
        except Exception as exc:
            logging.error("Не удалось отправить запрос планов пользователю %s: %s", tg_id, exc)


async def handle_sprint_cycle(team_id: int):
    """Создание спринта и запрос планов (вызывается по понедельникам)."""
    from bot.core.database import (
        db_create_sprint,
        db_finish_sprint,
        db_get_active_sprint,
        db_get_team_by_id,
        db_mark_sprint_plans_requested,
    )

    team = await db_get_team_by_id(team_id)
    if not team or not team.get('sprint_enabled'):
        return

    tz = pytz.timezone(team['timezone'])
    today = datetime.now(tz).date()

    sprint = await db_get_active_sprint(team_id)
    # Убрана автоматическая проверка окончания спринта по дате
    # Спринт завершается только при отправке финальной сводки

    if not sprint:
        if today.weekday() != 0:
            logging.info("Спринт команды %s начнётся в следующий понедельник", team_id)
            return
        sprint = await db_create_sprint(
            team_id,
            today,
            team.get('sprint_duration_weeks') or 2,
            team.get('report_days')
        )
        logging.info("Создан новый спринт %s для команды %s", sprint['id'], team_id)

    start_date = _date_from_value(sprint.get('start_date'))
    if not sprint.get('plans_requested') and today == start_date:
        await _send_sprint_plan_requests(team, sprint)
        await db_mark_sprint_plans_requested(sprint['id'])


async def create_and_start_scheduler(send_daily_questions_func, format_and_send_report_func):
    """Создание и запуск планировщика с настроенными задачами"""
    scheduler = AsyncIOScheduler(timezone=TIMEZONE, misfire_grace_time=1200)
    # Запоминаем главный цикл событий
    global _MAIN_LOOP
    try:
        _MAIN_LOOP = asyncio.get_running_loop()
    except RuntimeError:
        _MAIN_LOOP = None
    
    # Настраиваем индивидуальные задачи для каждой команды
    await setup_all_teams_scheduler_jobs(scheduler)

    # Еженедельный отчёт по токенам по пятницам, 20:00 (Екатеринбург)
    async def _weekly_token_report_job():
        try:
            if not WEEKLY_TOKEN_REPORT_CHAT_ID:
                logging.warning("WEEKLY_TOKEN_REPORT_CHAT_ID не задан, пропускаю отправку еженедельного отчёта")
                return
            from bot.utils.token_report import generate_token_report
            text_report, unified_chart = await generate_token_report(days=7)

            # Если есть изображение — отправляем фото с подписью, иначе просто текст
            if unified_chart:
                try:
                    unified_chart.seek(0, 2)
                    size = unified_chart.tell()
                    unified_chart.seek(0)
                    if size > 0:
                        chart_bytes = unified_chart.read()
                        await send_photo_with_retry(
                            chat_id=WEEKLY_TOKEN_REPORT_CHAT_ID,
                            photo_bytes=chart_bytes,
                            filename="weekly_token_report.png",
                            message_thread_id=WEEKLY_TOKEN_REPORT_TOPIC_ID,
                            is_report=True,
                        )
                finally:
                    try:
                        unified_chart.close()
                    except Exception:
                        pass
            
            # Затем отправляем текст отдельным сообщением
            await send_message_with_retry(
                WEEKLY_TOKEN_REPORT_CHAT_ID, text_report, parse_mode='HTML',
                is_report=True, message_thread_id=WEEKLY_TOKEN_REPORT_TOPIC_ID
            )
        except Exception as e:
            logging.error(f"weekly_token_report_job: ошибка отправки отчёта: {e}", exc_info=True)

    weekly_trigger = CronTrigger(
        day_of_week='fri', hour=20, minute=0, timezone=pytz.timezone(TIMEZONE),
    )
    _add_or_update_job(
        scheduler,
        'weekly_token_report',
        lambda: run_async_function(_weekly_token_report_job),
        weekly_trigger
    )

    scheduler.start()
    logging.info(f"Планировщик запущен с часовым поясом: {TIMEZONE}")
    log_next_run_times(scheduler)
    return scheduler

async def setup_weekly_plan_job(scheduler, team_id: int):
    from bot.core.database import db_get_team_by_id
    """Настроить задачу отправки запроса на планы каждую неделю в понедельник 9:00"""
    team = await db_get_team_by_id(team_id)
    if not team:
        logging.error(f"Команда с ID {team_id} не найдена в setup_weekly_plan_job")
        return

    tz = pytz.timezone(team['timezone'])
    job_id = f"weekly_plan_{team_id}"

    # Удаляем старую задачу, если есть
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        logging.info(f"Старая задача {job_id} удалена")

    # Если дни отчетов не выбраны, не планируем еженедельные планы
    if not team.get('report_days'):
        logging.info(f"Задача weekly_plan для команды {team_id} не создана, так как дни не выбраны")
        return

    scheduler.add_job(
        send_weekly_plan_request_to_team,
        'cron',
        day_of_week='mon',
        hour=9,
        minute=0,
        timezone=tz,
        args=[team_id],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=300  # 5 минут
    )
    logging.info(f"Задача weekly_plan настроена для команды {team_id} в {team['timezone']}")


async def send_weekly_plan_request_to_team(team_id: int):
    from bot.core.database import db_get_team_members, db_get_team_by_id
    from bot.core.bot_instance import bot, dp
    from bot.utils.keyboards import write_weekly_plan_keyboard, select_team_keyboard
    from bot.core.database import get_pool
    from datetime import datetime, timedelta
    from bot.core import WeeklyPlan

    logging.info(f"Начало выполнения send_weekly_plan_request_to_team для team_id={team_id}")
    
    team = await db_get_team_by_id(team_id)
    if not team:
        logging.error(f"Команда с ID {team_id} не найдена")
        return

    members = await db_get_team_members(team_id)
    logging.info(f"Члены команды {team['name']}: {members}")
    if not members:
        logging.warning(f"Нет членов команды для team_id={team_id}")
        return

    tz = pytz.timezone(team['timezone'])
    now = datetime.now(tz)
    monday = (now - timedelta(days=now.weekday())).date()
    logging.info(f"Текущая дата: {now}, понедельник: {monday}")

    for member in members:
        tg_id = member['tg_id']
        
        # Исключаем менеджеров, которые не участвуют в опросах
        is_manager = member.get('is_manager', False)
        is_participant = member.get('is_participant', False)
        if is_manager and not is_participant:
            logging.info(f"Пользователь {tg_id} - менеджер, исключен из запроса на планы")
            continue
        
        logging.info(f"Обработка пользователя {tg_id}")
        vacation_start = member.get('vacation_start')
        vacation_end = member.get('vacation_end')
        in_vacation = False
        if vacation_start and vacation_end:
            try:
                v_start = datetime.strptime(vacation_start, '%d-%m-%Y').date()
                v_end = datetime.strptime(vacation_end, '%d-%m-%Y').date()
                if v_start <= monday <= v_end:
                    in_vacation = True
                    logging.info(f"Пользователь {tg_id} в отпуске (с {v_start} по {v_end})")
            except Exception as e:
                logging.error(f"Ошибка проверки отпуска для {tg_id}: {e}", exc_info=True)
        if not in_vacation:
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text=f"📅 Что у Вас в планах на этой неделе для команды {team['name']}?",
                    reply_markup=write_weekly_plan_keyboard(team_id)
                )
                logging.info(f"Запрос на план для команды {team['name']} отправлен пользователю {tg_id}")
            except Exception as e:
                logging.error(f"Не удалось отправить запрос на план пользователю {tg_id} для команды {team['name']}: {e}", exc_info=True)

async def create_and_start_weekly_plan_scheduler():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from bot.core.database import db_get_all_teams_with_times
    scheduler = AsyncIOScheduler(timezone="Asia/Yekaterinburg")  # или общий TIMEZONE

    teams = await db_get_all_teams_with_times()
    for team in teams:
        await setup_weekly_plan_job(scheduler, team['id'])

    scheduler.start()
    logging.info("✅ Weekly plan scheduler запущен")
    return scheduler