import logging
import os
import asyncio
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from functools import wraps
from datetime import datetime, timedelta, date
import pytz
from bot.utils.utils import get_current_time
import json
from bot.utils.day_utils import (
    calculate_last_report_date,
    calculate_sprint_end_date,
    get_next_monday,
)
from bot.utils.team_presets import get_team_preset_settings

# Параметры подключения к PostgreSQL
def get_database_dsn() -> str:
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        return dsn
    host = os.getenv("PGHOST", "127.0.0.1")
    port = os.getenv("PGPORT", "55432")
    dbname = os.getenv("PGDATABASE", "dailybot")
    user = os.getenv("PGUSER", "dailybot")
    password = os.getenv("PGPASSWORD", "")
    sslmode = os.getenv("PGSSLMODE", "disable")
    auth = f":{password}" if password else ""
    return f"postgresql://{user}{auth}@{host}:{port}/{dbname}?sslmode={sslmode}"

# Глобальный пул соединений
_pg_pool: AsyncConnectionPool | None = None

async def get_pool() -> AsyncConnectionPool:
    global _pg_pool
    if _pg_pool is None:
        dsn = get_database_dsn()
        _pg_pool = AsyncConnectionPool(
            dsn,
            min_size=int(os.getenv("PGPOOL_MIN", "3")),
            max_size=int(os.getenv("PGPOOL_MAX", "10")),
            open=False,
        )
        await _pg_pool.open()
    return _pg_pool

async def close_pool() -> None:
    """Закрыть пул соединений PostgreSQL (если инициализирован)."""
    global _pg_pool
    if _pg_pool is not None:
        try:
            await _pg_pool.close()
        finally:
            _pg_pool = None

def db_retry(max_attempts=3, base_delay=0.1, max_delay=2.0):
    """
    Декоратор для повторных попыток операций с базой данных PostgreSQL.
    
    Args:
        max_attempts: Максимальное количество попыток (по умолчанию 3)
        base_delay: Базовая задержка в секундах (по умолчанию 0.1)
        max_delay: Максимальная задержка в секундах (по умолчанию 2.0)
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except psycopg.Error as e:
                    last_exception = e
                    error_msg = str(e).lower()
                    
                    # Эвристика для временных/сетевых ошибок
                    transient_markers = [
                        'deadlock detected',
                        'could not serialize',
                        'terminating connection',
                        'connection not open',
                        'closed connection',
                        'timeout',
                    ]
                    if any(msg in error_msg for msg in transient_markers):
                        if attempt < max_attempts - 1:
                            delay = min(base_delay * (2 ** attempt), max_delay)
                            logging.warning(
                                f"Проблема с PostgreSQL (попытка {attempt + 1}/{max_attempts}). Повтор через {delay:.2f} сек. Ошибка: {e}"
                            )
                            await asyncio.sleep(delay)
                            continue
                    
                    # Для не временных ошибок не повторяем
                    logging.error(f"Критическая ошибка базы данных: {e}")
                    raise
                except Exception as e:
                    # Для других типов ошибок не повторяем
                    logging.error(f"Неожиданная ошибка при работе с базой данных: {e}")
                    raise
            
            # Если все попытки исчерпаны
            logging.error(f"Все {max_attempts} попытки записи в базу данных исчерпаны. Последняя ошибка: {last_exception}")
            raise last_exception
        
        return wrapper
    return decorator

# --- Функции для работы с БД (PostgreSQL) ---
@db_retry(max_attempts=3)
async def init_db():
    """Инициализация базы данных: создание таблиц, если они не существуют, и применение миграций."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # Создаём все таблицы, если они не существуют
            await cur.execute("""
                -- Таблица команд
                CREATE TABLE IF NOT EXISTS public.teams (
                  id SERIAL PRIMARY KEY,
                  name TEXT NOT NULL,
                  chat_id BIGINT,
                  chat_topic_id BIGINT,
                  board_link TEXT,
                  morning_time TIME NOT NULL DEFAULT '09:00',
                  evening_time TIME NOT NULL DEFAULT '22:00',
                  report_time TIME NOT NULL DEFAULT '10:00',
                  report_days TEXT NOT NULL DEFAULT 'tue,wed,thu,fri',
                  timezone TEXT NOT NULL DEFAULT 'Asia/Yekaterinburg',
                  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                  test_flag BOOLEAN NOT NULL DEFAULT FALSE,
                  questions_json jsonb DEFAULT '[]'::jsonb
                );

                -- Таблица сотрудников
                CREATE TABLE IF NOT EXISTS public.employees (
                  tg_id BIGINT PRIMARY KEY,
                  username TEXT,
                  full_name TEXT,
                  team_id INTEGER REFERENCES public.teams(id) ON DELETE SET NULL
                );

                -- Таблица членства в командах
                CREATE TABLE IF NOT EXISTS public.user_team_memberships (
                  id SERIAL PRIMARY KEY,
                  employee_tg_id BIGINT NOT NULL REFERENCES public.employees(tg_id) ON DELETE CASCADE,
                  team_id INTEGER NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
                  is_manager BOOLEAN NOT NULL DEFAULT FALSE,
                  role TEXT,
                  daily_time TEXT NOT NULL DEFAULT 'morning',
                  gitverse_nickname TEXT,
                  vacation_start TEXT,
                  vacation_end TEXT,
                  joined_at TIMESTAMP NOT NULL DEFAULT NOW(),
                  UNIQUE (employee_tg_id, team_id)
                );

                -- Таблица отчётов
                CREATE TABLE IF NOT EXISTS public.reports (
                  id SERIAL PRIMARY KEY,
                  employee_tg_id BIGINT NOT NULL REFERENCES public.employees(tg_id) ON DELETE CASCADE,
                  report_datetime TIMESTAMP NOT NULL,
                  llm_questions TEXT,
                  llm_answer TEXT,
                  answers_json jsonb DEFAULT '[]'::jsonb,
                  team_id INTEGER REFERENCES public.teams(id) ON DELETE SET NULL
                );

                -- Таблица приглашений
                CREATE TABLE IF NOT EXISTS public.team_invites (
                  id SERIAL PRIMARY KEY,
                  team_id INTEGER NOT NULL UNIQUE REFERENCES public.teams(id) ON DELETE CASCADE,
                  invite_code TEXT NOT NULL UNIQUE,
                  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                  is_active BOOLEAN NOT NULL DEFAULT TRUE
                );

                -- Таблица использования токенов LLM
                CREATE TABLE IF NOT EXISTS public.llm_token_usage (
                  id SERIAL PRIMARY KEY,
                  event TEXT NOT NULL,
                  team_id INTEGER REFERENCES public.teams(id) ON DELETE SET NULL,
                  employee_tg_id BIGINT REFERENCES public.employees(tg_id) ON DELETE SET NULL,
                  provider TEXT,
                  model TEXT,
                  model_version TEXT,
                  input_tokens INTEGER,
                  output_tokens INTEGER,
                  total_tokens INTEGER,
                  duration_ms INTEGER,
                  attempts INTEGER,
                  response_text TEXT,
                  created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );

                -- Добавляем недостающие колонки для метрик, если таблица уже существовала
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name = 'llm_token_usage' AND column_name = 'duration_ms'
                    ) THEN
                        ALTER TABLE public.llm_token_usage ADD COLUMN duration_ms INTEGER;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name = 'llm_token_usage' AND column_name = 'attempts'
                    ) THEN
                        ALTER TABLE public.llm_token_usage ADD COLUMN attempts INTEGER;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name = 'llm_token_usage' AND column_name = 'response_text'
                    ) THEN
                        ALTER TABLE public.llm_token_usage ADD COLUMN response_text TEXT;
                    END IF;
                END $$;

                -- Таблица спринтов (если понадобится)
                CREATE TABLE IF NOT EXISTS public.team_sprints (
                  id SERIAL PRIMARY KEY,
                  team_id INTEGER NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
                  started_at TIMESTAMP NOT NULL DEFAULT NOW(),
                  finished_at TIMESTAMP,
                  is_active BOOLEAN NOT NULL DEFAULT TRUE
                );

                -- Добавляем колонки спринтов, если они отсутствуют
                ALTER TABLE team_sprints
                    ADD COLUMN IF NOT EXISTS start_date DATE;
                ALTER TABLE team_sprints
                    ADD COLUMN IF NOT EXISTS end_date DATE;
                ALTER TABLE team_sprints
                    ADD COLUMN IF NOT EXISTS last_report_date DATE;
                ALTER TABLE team_sprints
                    ADD COLUMN IF NOT EXISTS duration_weeks INTEGER NOT NULL DEFAULT 2;
                ALTER TABLE team_sprints
                    ADD COLUMN IF NOT EXISTS plans_requested BOOLEAN NOT NULL DEFAULT FALSE;

                -- Настройки спринтов на уровне команды
                ALTER TABLE teams
                    ADD COLUMN IF NOT EXISTS sprint_enabled BOOLEAN NOT NULL DEFAULT TRUE;
                ALTER TABLE teams
                    ADD COLUMN IF NOT EXISTS sprint_duration_weeks INTEGER NOT NULL DEFAULT 1;

                -- Обновляем существующие команды: включаем спринты и устанавливаем длительность 1 неделя
                UPDATE teams SET sprint_enabled = TRUE WHERE sprint_enabled = FALSE;
                UPDATE teams SET sprint_duration_weeks = 1 WHERE sprint_duration_weeks != 1;

                -- Инициализация новых колонок team_sprints
                UPDATE team_sprints
                    SET start_date = COALESCE(start_date, started_at::date)
                WHERE start_date IS NULL;
                UPDATE team_sprints
                    SET end_date = COALESCE(end_date, start_date)
                WHERE end_date IS NULL;
                UPDATE team_sprints
                    SET last_report_date = COALESCE(last_report_date, end_date)
                WHERE last_report_date IS NULL;

                -- Таблица планов спринта
                CREATE TABLE IF NOT EXISTS public.sprint_plans (
                  id SERIAL PRIMARY KEY,
                  sprint_id INTEGER NOT NULL REFERENCES public.team_sprints(id) ON DELETE CASCADE,
                  employee_tg_id BIGINT NOT NULL REFERENCES public.employees(tg_id) ON DELETE CASCADE,
                  plan_text TEXT NOT NULL,
                  created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_sprint_plans_sprint_employee
                    ON public.sprint_plans (sprint_id, employee_tg_id);

                -- Удаляем колонку team_id из sprint_plans, если она существует
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = 'sprint_plans'
                          AND column_name = 'team_id'
                          AND table_schema = 'public'
                    ) THEN
                        ALTER TABLE sprint_plans DROP COLUMN team_id;
                        RAISE NOTICE 'Колонка team_id удалена из таблицы sprint_plans';
                    END IF;
                END $$;

                -- Добавляем поле is_participant в таблицу user_team_memberships
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 
                        FROM information_schema.columns 
                        WHERE table_name='user_team_memberships' 
                        AND column_name='is_participant'
                    ) THEN
                        ALTER TABLE user_team_memberships 
                        ADD COLUMN is_participant BOOLEAN NOT NULL DEFAULT FALSE;
                        
                        -- Для всех НЕ-менеджеров устанавливаем is_participant = TRUE
                        UPDATE user_team_memberships
                        SET is_participant = TRUE
                        WHERE is_manager = FALSE;
                    END IF;
                END $$;

                -- Для всех НЕ-менеджеров устанавливаем is_participant = TRUE
                UPDATE user_team_memberships 
                SET is_participant = TRUE 
                WHERE is_manager = FALSE;

                -- Гарантируем default/NOT NULL для daily_time
                DO $$
                BEGIN
                    -- Заполняем пустые значения
                    UPDATE user_team_memberships
                    SET daily_time = 'morning'
                    WHERE daily_time IS NULL OR daily_time = '';

                    -- Устанавливаем дефолт и NOT NULL 
                    ALTER TABLE user_team_memberships 
                        ALTER COLUMN daily_time SET DEFAULT 'morning';
                    ALTER TABLE user_team_memberships 
                        ALTER COLUMN daily_time SET NOT NULL;
                EXCEPTION WHEN others THEN
                    -- игнорируем ошибки
                    NULL;
                END $$;

                --- Миграция: добавление полей для еженедельных задач ---
                DO $$
                BEGIN
                    -- Запрос планов
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'teams' AND column_name = 'weekly_plan_day') THEN
                        ALTER TABLE teams ADD COLUMN weekly_plan_day TEXT DEFAULT 'mon';
                        ALTER TABLE teams ADD COLUMN weekly_plan_time TIME DEFAULT '10:00';
                    END IF;    
                    -- Анализ
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'teams' AND column_name = 'weekly_analysis_day') THEN
                        ALTER TABLE teams ADD COLUMN weekly_analysis_day TEXT DEFAULT 'fri';
                        ALTER TABLE teams ADD COLUMN weekly_analysis_time TIME DEFAULT '10:00';
                    END IF;
                END $$;

                -- Таблица кураторов (пользователи, которые могут создавать команды)
                CREATE TABLE IF NOT EXISTS public.curators (
                  tg_id BIGINT PRIMARY KEY,
                  added_at TIMESTAMP NOT NULL DEFAULT NOW()
                );

                -- Таблица Product Owners
                CREATE TABLE IF NOT EXISTS public.product_owners (
                  id SERIAL PRIMARY KEY,
                  employee_tg_id BIGINT NOT NULL REFERENCES public.employees(tg_id) ON DELETE CASCADE,
                  team_id INTEGER NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
                  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                  UNIQUE (employee_tg_id, team_id)
                );

                -- Таблица причин отсутствия отчетов
                CREATE TABLE IF NOT EXISTS public.missing_report_reasons (
                  id SERIAL PRIMARY KEY,
                  employee_tg_id BIGINT NOT NULL REFERENCES public.employees(tg_id) ON DELETE CASCADE,
                  team_id INTEGER NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
                  reason_text TEXT NOT NULL,
                  report_date DATE NOT NULL,
                  created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_missing_report_reasons_employee_team_date
                    ON public.missing_report_reasons (employee_tg_id, team_id, report_date);
            """)
        await conn.commit()
    logging.info("База данных инициализирована: все таблицы созданы или уже существуют.")
    await migrate_add_questions_fields()
    await migrate_add_weekly_plans_table()
    await migrate_update_weekly_plans_constraint()
    await migrate_add_is_po_field()

@db_retry(max_attempts=3)
async def db_add_llm_token_usage(
    event: str,
    team_id: int | None = None,
    employee_tg_id: int | None = None,
    *,
    provider: str | None = None,
    model: str | None = None,
    model_version: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    duration_ms: int | None = None,
    attempts: int | None = None,
    response_text: str | None = None,
) -> None:
    """Сохранить событие использования токенов LLM."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO llm_token_usage (
                  event, team_id, employee_tg_id,
                  provider, model, model_version,
                  input_tokens, output_tokens, total_tokens,
                  duration_ms, attempts, response_text
                ) VALUES (
                  %s, %s, %s,
                  %s, %s, %s,
                  %s, %s, %s,
                  %s, %s, %s
                )
                """,
                (
                    event, team_id, employee_tg_id,
                    provider, model, model_version,
                    input_tokens, output_tokens, total_tokens,
                    duration_ms, attempts, response_text,
                ),
            )
        await conn.commit()

@db_retry(max_attempts=3)
async def db_ensure_employee(tg_id: int, username: str | None, full_name: str | None) -> None:
    """Гарантировать существование записи в employees. Вставляет минимальные поля, если нет."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO employees (tg_id, username, full_name)
                VALUES (%s, %s, %s)
                ON CONFLICT (tg_id) DO NOTHING
                """,
                (tg_id, username, full_name),
            )
        await conn.commit()

@db_retry(max_attempts=3)
async def db_add_employee(tg_id, username, full_name, role, daily_time, team_id=None):
    """Добавление нового сотрудника в базу данных (с поддержкой новой структуры таблиц)"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 1. Вставляем или обновляем запись в employees (на случай, если пользователь уже есть)
            await cur.execute(
                """
                INSERT INTO employees (tg_id, username, full_name, team_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (tg_id) DO UPDATE
                SET username = EXCLUDED.username,
                    full_name = EXCLUDED.full_name,
                    team_id = EXCLUDED.team_id
                """,
                (tg_id, username, full_name, team_id)
            )

            # 2. Если указан team_id — добавляем запись в user_team_memberships
            if team_id is not None:
                await cur.execute(
                    """
                    INSERT INTO user_team_memberships (employee_tg_id, team_id, role, daily_time)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (employee_tg_id, team_id) DO UPDATE
                    SET role = EXCLUDED.role,
                        daily_time = EXCLUDED.daily_time
                    """,
                    (tg_id, team_id, role, daily_time)
                )

        await conn.commit()

async def db_get_employee(tg_id):
    """Получение информации о сотруднике по telegram ID"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # Обогащаем базовые данные персонифицированными значениями из membership для текущей выбранной команды (employees.team_id)
            await cur.execute(
                """
                SELECT 
                  e.tg_id,
                  e.username,
                  e.full_name,
                  m.role AS role,
                  m.daily_time AS daily_time,
                  m.vacation_start AS vacation_start,
                  m.vacation_end AS vacation_end,
                  e.team_id AS team_id,
                  m.gitverse_nickname AS gitverse_nickname
                FROM employees e
                LEFT JOIN user_team_memberships m 
                  ON m.employee_tg_id = e.tg_id AND m.team_id = e.team_id
                WHERE e.tg_id = %s
                """,
                (tg_id,)
            )
            return await cur.fetchone()

async def db_get_all_employees():
    """Получение всех сотрудников из базы данных"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT tg_id, username, full_name, team_id FROM employees"
            )
            return await cur.fetchall()

@db_retry(max_attempts=3)
async def db_delete_employee(tg_id):
    """Удаление сотрудника и всех его отчётов в одной транзакции"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # Сначала удаляем зависимые записи отчётов, затем сотрудника
            await cur.execute("DELETE FROM reports WHERE employee_tg_id = %s", (tg_id,))
            await cur.execute("DELETE FROM employees WHERE tg_id = %s", (tg_id,))
        await conn.commit()


@db_retry(max_attempts=3)
async def db_delete_employee_reports_for_team(employee_tg_id: int, team_id: int) -> None:
    """Удалить отчёты конкретного сотрудника в рамках одной команды."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM reports WHERE employee_tg_id = %s AND team_id = %s",
                (employee_tg_id, team_id),
            )
        await conn.commit()

@db_retry(max_attempts=3)
async def db_add_report(tg_id, team_id, report_datetime, answers_json, llm_questions: str | None = None) -> str:
    """Добавление отчета в базу данных"""
    pool = await get_pool()
    report_id = f"{tg_id}_{report_datetime.strftime('%Y%m%d_%H%M%S')}"  # Генерируем report_id для логов
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO reports (employee_tg_id, team_id, report_datetime, answers_json, llm_questions)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (tg_id, team_id, report_datetime, answers_json, llm_questions)
            )
            row = await cur.fetchone()
        await conn.commit()
        logging.info(f"В БД добавлен отчет от пользователя {tg_id} в {report_datetime.strftime('%H:%M:%S')}")
        return row['id'] if row and row['id'] is not None else ""

@db_retry(max_attempts=3)
async def db_update_report_llm_answer(report_id: str, llm_answer: str) -> bool:
    """Обновить ответ на уточняющий вопрос LLM в БД"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE reports
                SET llm_answer = %s
                WHERE id = %s
                """,
                (llm_answer, report_id)
            )
        await conn.commit()
    logging.info(f"LLM ответ для отчета {report_id} обновлен")
    return True

@db_retry(max_attempts=3)
async def db_add_missing_report_reason(tg_id: int, team_id: int, reason_text: str, report_date: date) -> int:
    """Добавление причины отсутствия отчета в базу данных"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO missing_report_reasons (employee_tg_id, team_id, reason_text, report_date)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (tg_id, team_id, reason_text, report_date)
            )
            row = await cur.fetchone()
        await conn.commit()
        logging.info(f"В БД добавлена причина отсутствия отчета от пользователя {tg_id} за {report_date}")
        return row['id'] if row and row['id'] is not None else 0

async def db_get_daily_period_reports(team_id=None, team=None):
    """Получение отчетов за дейли-период с учетом времени дейли и времени отчета команды"""
    pool = await get_pool()
    async with pool.connection() as conn:
        from datetime import timedelta, datetime
        
        # Получаем настройки команды
        if team is None and team_id is not None:
            team = await db_get_team_by_id(team_id)
        
        if not team:
            # Если нет команды — используем UTC окно за последние 48 часов
            now_utc = datetime.utcnow()
            start_utc = (now_utc - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
            end_utc = now_utc.strftime('%Y-%m-%d %H:%M:%S')
            if team_id is not None:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        """
                        SELECT e.full_name, e.role, r.answers_json, e.tg_id, r.report_datetime, e.daily_time, r.llm_questions, r.llm_answer
                        FROM reports r
                        JOIN employees e ON r.employee_tg_id = e.tg_id
                        WHERE r.team_id = %s AND r.report_datetime BETWEEN %s AND %s
                        ORDER BY r.report_datetime DESC
                        """,
                        (team_id, start_utc, end_utc)
                    )
                    return await cur.fetchall()
            else:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        """
                        SELECT e.full_name, e.role, r.answers_json, e.tg_id, r.report_datetime, e.daily_time, r.llm_questions, r.llm_answer
                        FROM reports r
                        JOIN employees e ON r.employee_tg_id = e.tg_id
                        WHERE r.report_datetime BETWEEN %s AND %s
                        ORDER BY r.report_datetime DESC
                        """,
                        (start_utc, end_utc)
                    )
                    return await cur.fetchall()
        
        # Получаем время команды
        try:
            morning_time = team['morning_time']
            evening_time = team['evening_time']
            report_time = team['report_time']
        except Exception as e:
            logging.error(f"Отсутствуют обязательные поля времени в объекте команды: {e}")
            return []
        if not morning_time or not evening_time or not report_time:
            logging.error("Некорректные настройки времени команды (пустые значения). Пропускаем выборку отчетов.")
            return []
        
        # Парсим время
        try:
            morning_hour, morning_minute = map(int, morning_time.split(':'))
            evening_hour, evening_minute = map(int, evening_time.split(':'))
            report_hour, report_minute = map(int, report_time.split(':'))
        except Exception as e:
            logging.error(f"Некорректный формат времени в настройках команды: {e}")
            return []
        
        # Получаем текущее время в TZ команды
        try:
            team_tz = pytz.timezone(team['timezone'])
        except Exception:
            team_tz = pytz.timezone('Asia/Yekaterinburg')
        now_team = datetime.now(team_tz)
        today = now_team.date()
        yesterday = today - timedelta(days=1)
        # Граница начала окна (вчерашний evening_time) в TZ команды
        start_team = team_tz.localize(datetime.combine(yesterday, datetime.min.time().replace(
            hour=evening_hour, minute=evening_minute, second=0
        )))
        end_team = now_team
        # Переводим границы в UTC
        start_utc = start_team.astimezone(pytz.UTC)
        end_utc = end_team.astimezone(pytz.UTC)
        # Форматируем для SQL
        evening_start_yesterday_str = start_utc.strftime('%Y-%m-%d %H:%M:%S')
        evening_end_today_str = end_utc.strftime('%Y-%m-%d %H:%M:%S')
        
        # Выполняем запрос с единым окном: от evening_time вчера до сейчас
        try:
            logging.info(
                f"Окно выборки отчетов: [{evening_start_yesterday_str} .. {evening_end_today_str}], team_id={team_id}"
            )
        except Exception:
            pass
        if team_id is not None:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                SELECT e.full_name, m.role, r.answers_json, e.tg_id, r.report_datetime, m.daily_time, r.llm_questions, r.llm_answer
                FROM reports r
                JOIN employees e ON r.employee_tg_id = e.tg_id
                LEFT JOIN user_team_memberships m ON m.employee_tg_id = e.tg_id AND m.team_id = r.team_id
                WHERE r.team_id = %s AND r.report_datetime BETWEEN %s AND %s
                  AND (m.is_manager = FALSE OR (m.is_manager = TRUE AND m.is_participant = TRUE))
                ORDER BY r.report_datetime DESC
            """,
                    (
                        team_id,
                        evening_start_yesterday_str,
                        evening_end_today_str,
                    ),
                )
                rows = await cur.fetchall()
        else:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                SELECT e.full_name, m.role, r.answers_json, e.tg_id, r.report_datetime, m.daily_time, r.llm_questions, r.llm_answer
                FROM reports r
                JOIN employees e ON r.employee_tg_id = e.tg_id
                LEFT JOIN user_team_memberships m ON m.employee_tg_id = e.tg_id AND m.team_id = r.team_id
                WHERE r.report_datetime BETWEEN %s AND %s
                ORDER BY r.report_datetime DESC
            """,
                    (
                        evening_start_yesterday_str,
                        evening_end_today_str,
                    ),
                )
                rows = await cur.fetchall()
        try:
            logging.info(f"Найдено отчетов в окне: {len(rows)}")
            for i, row in enumerate(rows[:3]):
                logging.info(f"Отчет[{i}]: tg_id={row['tg_id']}, dt={row['report_datetime']}, daily_time={row['daily_time']}")
        except Exception:
            pass
        return rows

async def db_get_employees_for_daily(time_str, team_id=None):
    """Получение списка сотрудников для ежедневного опроса по времени и команде"""
    pool = await get_pool()
    async with pool.connection() as conn:
        if team_id is not None:
            # Используем membership для команды, исключая менеджеров
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT employee_tg_id FROM user_team_memberships WHERE daily_time = %s AND team_id = %s AND (is_manager = FALSE OR is_participant = TRUE)",
                    (time_str, team_id),
                )
                rows = await cur.fetchall()
                return [row[0] for row in rows]
        else:
            # Без team_id выбираем по membership среди всех команд
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT employee_tg_id FROM user_team_memberships WHERE daily_time = %s AND is_manager = FALSE",
                    (time_str,),
                )
                rows = await cur.fetchall()
                return [row[0] for row in rows]


@db_retry(max_attempts=3)
async def db_get_employees_with_vacation_info(tg_ids: list[int], team_id: int = None):
    """Получение информации о сотрудниках с данными об отпуске одним запросом.
    
    Args:
        tg_ids: Список telegram ID сотрудников
        team_id: ID команды (опционально, для фильтрации по membership)
    
    Returns:
        Список словарей с информацией о сотрудниках (tg_id, full_name, vacation_start, vacation_end)
    """
    if not tg_ids:
        return []
    
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            if team_id is not None:
                # Получаем сотрудников с информацией об отпуске для конкретной команды
                await cur.execute(
                    """
                    SELECT 
                        e.tg_id,
                        e.full_name,
                        m.vacation_start AS vacation_start,
                        m.vacation_end AS vacation_end
                    FROM employees e
                    INNER JOIN user_team_memberships m 
                        ON m.employee_tg_id = e.tg_id AND m.team_id = %s
                    WHERE e.tg_id = ANY(%s)
                    """,
                    (team_id, tg_ids)
                )
            else:
                # Получаем сотрудников без привязки к команде (берем первое membership)
                await cur.execute(
                    """
                    SELECT DISTINCT ON (e.tg_id)
                        e.tg_id,
                        e.full_name,
                        m.vacation_start AS vacation_start,
                        m.vacation_end AS vacation_end
                    FROM employees e
                    INNER JOIN user_team_memberships m 
                        ON m.employee_tg_id = e.tg_id
                    WHERE e.tg_id = ANY(%s)
                    ORDER BY e.tg_id, m.team_id
                    """,
                    (tg_ids,)
                )
            return await cur.fetchall()

async def db_get_last_report_date(tg_id):
    """Получение даты и времени последнего отчета сотрудника"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT MAX(report_datetime) FROM reports WHERE employee_tg_id = %s", (tg_id,))
            result = await cur.fetchone()
        
        if result and result[0]:
            datetime_value = result[0]
            if hasattr(datetime_value, 'strftime'):
                datetime_str = datetime_value.strftime('%Y-%m-%d %H:%M:%S')
            else:
                datetime_str = str(datetime_value)
            
            try:
                if ' ' in datetime_str:
                    date_part, time_part = datetime_str.split(' ', 1)
                    date_obj = datetime.strptime(date_part, '%Y-%m-%d')
                    formatted_date = date_obj.strftime('%d.%m.%Y')
                    time_parts = time_part.split(':')
                    formatted_time = f"{time_parts[0]}:{time_parts[1]}"
                    return f"{formatted_date} в {formatted_time}"
                else:
                    date_obj = datetime.strptime(datetime_str, '%Y-%m-%d')
                    return date_obj.strftime('%d.%m.%Y')
            except:
                return datetime_str
        return "Никогда"

@db_retry(max_attempts=3)
async def db_update_employee_field(tg_id, field, value):
    """Обновление поля сотрудника. Поля, зависящие от команды, обновляются в user_team_memberships.
    Поддерживаемые per-team поля: role, daily_time, gitverse_nickname.
    Глобальные поля (employees): username, full_name, team_id.
    """
    per_team_fields = {"role", "daily_time", "gitverse_nickname"}
    pool = await get_pool()
    async with pool.connection() as conn:
        if field in per_team_fields:
            # Определяем текущую выбранную команду пользователя
            async with conn.cursor() as cur_get:
                await cur_get.execute("SELECT team_id FROM employees WHERE tg_id = %s", (tg_id,))
                row = await cur_get.fetchone()
                current_team_id = row[0] if row else None
            if current_team_id is None:
                # Нет выбранной команды — нечего обновлять в разрезе команды
                return
            # Обновляем или создаём membership
            if field == "role":
                await db_add_membership(tg_id, current_team_id, role=value)
            elif field == "daily_time":
                await db_add_membership(tg_id, current_team_id, daily_time=value)
            elif field == "gitverse_nickname":
                await db_add_membership(tg_id, current_team_id, gitverse_nickname=value)
            await conn.commit()
        else:
            # Обновляем базовые поля в employees
            async with conn.cursor() as cur:
                await cur.execute(f"UPDATE employees SET {field} = %s WHERE tg_id = %s", (value, tg_id))
            await conn.commit()

@db_retry(max_attempts=3)
async def db_update_vacation(tg_id, start, end):
    """Обновление информации об отпуске сотрудника в рамках выбранной команды."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur_get:
            await cur_get.execute("SELECT team_id FROM employees WHERE tg_id = %s", (tg_id,))
            row = await cur_get.fetchone()
            current_team_id = row[0] if row else None
        if current_team_id is None:
            return
        async with conn.cursor() as cur_upd:
            await cur_upd.execute(
                "UPDATE user_team_memberships SET vacation_start = %s, vacation_end = %s WHERE employee_tg_id = %s AND team_id = %s",
                (start, end, tg_id, current_team_id),
            )
        await conn.commit()

# --- Функции для работы с командами ---
@db_retry(max_attempts=3)
async def db_create_team(
    name, chat_id=None, chat_topic_id=None, board_link=None,
    morning_time='09:00', evening_time='22:00', report_time='10:00', report_days='fri',
    timezone: str = 'Asia/Yekaterinburg', test_flag: bool = False, preset_choice: str = None
):
    """Создание новой команды с начальными вопросами в зависимости от выбранного пресета"""
    # Получаем настройки для выбранного пресета
    if preset_choice:
        preset_settings = get_team_preset_settings(preset_choice)
        morning_time = preset_settings['morning_time']
        evening_time = preset_settings['evening_time']
        report_time = preset_settings['report_time']
        report_days = preset_settings['report_days']
        questions = preset_settings['questions_json']
    else:
        # Используем настройки по умолчанию для обратной совместимости
        questions = [
        {
            "id": 1,
            "text": "Что вы сделали за эту неделю?",
            "field": "done_week",
            "time_variants": {},
            "board_related": False
        },
        {
            "id": 2,
            "text": "Какие трудности возникли во время работы?",
            "field": "problems_week",
            "time_variants": {},
            "board_related": False
        }
    ]
    pool = await get_pool()
    async with pool.connection() as conn:
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO teams (
                    name, chat_id, chat_topic_id, board_link,
                    morning_time, evening_time, report_time, report_days, timezone,
                    created_at, test_flag, questions_json, sprint_enabled, sprint_duration_weeks
                )
                VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                RETURNING id
                """,
                (
                    name, chat_id, chat_topic_id, board_link,
                    morning_time, evening_time, report_time, report_days, timezone,
                    created_at, test_flag, json.dumps(questions, ensure_ascii=False),
                    True, 1  # sprint_enabled = True, sprint_duration_weeks = 1
                ),
            )
            row = await cur.fetchone()
        await conn.commit()
        return int(row['id']) if row and row['id'] is not None else 0

async def db_get_team_by_manager(manager_tg_id):
    """Получение команды менеджера по membership (если выбрана текущая — проверяем её, иначе первую по имени)."""
    pool = await get_pool()
    async with pool.connection() as conn:
        # Сначала пробуем взять текущую команду пользователя
        async with conn.cursor() as cur_get:
            await cur_get.execute("SELECT team_id FROM employees WHERE tg_id = %s", (manager_tg_id,))
            row = await cur_get.fetchone()
            current_team_id = row[0] if row else None
        if current_team_id is not None:
            # Проверяем, что это действительно команда, где он менеджер
            async with conn.cursor() as cur_chk:
                await cur_chk.execute(
                    "SELECT 1 FROM user_team_memberships WHERE employee_tg_id = %s AND team_id = %s AND is_manager = TRUE",
                    (manager_tg_id, current_team_id),
                )
                ok = await cur_chk.fetchone()
            if ok:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        """
                        SELECT id, name, chat_id, chat_topic_id, board_link,
                               to_char(morning_time, 'HH24:MI') AS morning_time,
                               to_char(evening_time, 'HH24:MI') AS evening_time,
                               to_char(report_time,  'HH24:MI') AS report_time,
                               report_days, timezone, created_at, test_flag, questions_json,
                               weekly_plan_day, to_char(weekly_plan_time, 'HH24:MI') AS weekly_plan_time,
                              weekly_analysis_day, to_char(weekly_analysis_time, 'HH24:MI') AS weekly_analysis_time,
                              sprint_enabled, sprint_duration_weeks
                        FROM teams WHERE id = %s
                        """,
                        (current_team_id,),
                    )
                    return await cur.fetchone()
        # Нет текущей менеджерской команды — возвращаем None
        return None

async def db_get_team_by_id(team_id):
    """Получение команды по ID"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, name, chat_id, chat_topic_id, board_link,
                        to_char(morning_time, 'HH24:MI') AS morning_time,
                        to_char(evening_time, 'HH24:MI') AS evening_time,
                        to_char(report_time, 'HH24:MI') AS report_time,
                        report_days, timezone, created_at, test_flag, questions_json,
                        weekly_plan_day, to_char(weekly_plan_time, 'HH24:MI') AS weekly_plan_time,
                        weekly_analysis_day, to_char(weekly_analysis_time, 'HH24:MI') AS weekly_analysis_time,
                        sprint_enabled, sprint_duration_weeks
                FROM teams WHERE id = %s
                """,
                (team_id,),
            )
            return await cur.fetchone()

async def db_get_all_teams():
    """Получение всех команд"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, name, chat_id, chat_topic_id, board_link,
                       to_char(morning_time, 'HH24:MI') AS morning_time,
                       to_char(evening_time, 'HH24:MI') AS evening_time,
                       to_char(report_time, 'HH24:MI') AS report_time,
                       report_days, timezone, created_at, test_flag, questions_json,
                       sprint_enabled, sprint_duration_weeks
                FROM teams
                """
            )
            return await cur.fetchall()

async def db_get_team_employees(team_id):
    """Получение всех сотрудников команды (membership + базовые данные)"""
    return await db_get_team_members(team_id)

@db_retry(max_attempts=3)
async def db_update_employee_team(tg_id, team_id):
    """Обновление команды сотрудника"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE employees SET team_id = %s WHERE tg_id = %s", (team_id, tg_id))
        await conn.commit()

@db_retry(max_attempts=3)
async def db_update_team_field(team_id: int, field: str, value):
    """Обновление поля команды"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"UPDATE teams SET {field} = %s WHERE id = %s", (value, team_id))
        await conn.commit()


# --- Новые функции для работы с членствами (user_team_memberships) ---

@db_retry(max_attempts=3)
async def db_add_membership(
    employee_tg_id: int,
    team_id: int,
    *,
    is_manager: bool = False,
    role: str | None = None,
    daily_time: str | None = None,
    gitverse_nickname: str | None = None,
    vacation_start: str | None = None,
    vacation_end: str | None = None,
) -> int:
    """Добавить связь сотрудник-команда с персонифицированными полями."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO user_team_memberships (
                    employee_tg_id, team_id, is_manager, role, daily_time, gitverse_nickname, vacation_start, vacation_end
                ) VALUES (
                    %s, %s, %s, %s, COALESCE(%s, 'morning'), %s, %s, %s
                )
                ON CONFLICT (employee_tg_id, team_id) DO UPDATE
                SET is_manager = (user_team_memberships.is_manager OR EXCLUDED.is_manager),
                    role = COALESCE(EXCLUDED.role, user_team_memberships.role),
                    daily_time = COALESCE(EXCLUDED.daily_time, user_team_memberships.daily_time),
                    gitverse_nickname = COALESCE(EXCLUDED.gitverse_nickname, user_team_memberships.gitverse_nickname),
                    vacation_start = COALESCE(EXCLUDED.vacation_start, user_team_memberships.vacation_start),
                    vacation_end = COALESCE(EXCLUDED.vacation_end, user_team_memberships.vacation_end)
                RETURNING id
                """,
                (
                    employee_tg_id,
                    team_id,
                    is_manager,
                    role,
                    daily_time,
                    gitverse_nickname,
                    vacation_start,
                    vacation_end,
                ),
            )
            row = await cur.fetchone()
        await conn.commit()
        return int(row[0]) if row and row[0] is not None else 0


@db_retry(max_attempts=3)
async def db_remove_membership(employee_tg_id: int, team_id: int) -> None:
    """Удалить связь сотрудник-команда."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM user_team_memberships WHERE employee_tg_id = %s AND team_id = %s",
                (employee_tg_id, team_id),
            )
        await conn.commit()


@db_retry(max_attempts=3)
async def db_update_membership_field(employee_tg_id: int, team_id: int, field: str, value) -> None:
    """Обновить конкретное поле membership."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE user_team_memberships SET {field} = %s WHERE employee_tg_id = %s AND team_id = %s",
                (value, employee_tg_id, team_id),
            )
        await conn.commit()


async def db_get_user_memberships(employee_tg_id: int):
    """Список членств пользователя с данными команды и полями membership."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT m.employee_tg_id, m.team_id, m.is_manager, m.is_po, m.role, m.daily_time, m.gitverse_nickname,
                       m.vacation_start, m.vacation_end,
                       t.name AS team_name,
                       t.chat_id, t.chat_topic_id, t.board_link,
                       to_char(t.morning_time, 'HH24:MI') AS morning_time,
                       to_char(t.evening_time, 'HH24:MI') AS evening_time,
                       to_char(t.report_time,  'HH24:MI') AS report_time,
                       t.report_days, t.timezone, t.id AS id
                FROM user_team_memberships m
                JOIN teams t ON t.id = m.team_id
                WHERE m.employee_tg_id = %s
                ORDER BY t.name ASC
                """,
                (employee_tg_id,),
            )
            return await cur.fetchall()


async def db_get_membership(employee_tg_id: int, team_id: int):
    """Получить одно членство пользователя в команде."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT employee_tg_id, team_id, is_manager, is_participant, is_po, role, daily_time, gitverse_nickname,
                       vacation_start, vacation_end
                FROM user_team_memberships
                WHERE employee_tg_id = %s AND team_id = %s
                """,
                (employee_tg_id, team_id),
            )
            return await cur.fetchone()


async def db_get_user_manager_teams(employee_tg_id: int):
    """Список команд, где пользователь является менеджером."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT t.id, t.name, t.chat_id, t.chat_topic_id, t.board_link,
                       to_char(t.morning_time, 'HH24:MI') AS morning_time,
                       to_char(t.evening_time, 'HH24:MI') AS evening_time,
                       to_char(t.report_time,  'HH24:MI') AS report_time,
                       t.report_days, t.timezone, t.created_at, t.test_flag
                FROM user_team_memberships m
                JOIN teams t ON t.id = m.team_id
                WHERE m.employee_tg_id = %s AND m.is_manager = TRUE
                ORDER BY t.name ASC
                """,
                (employee_tg_id,),
            )
            return await cur.fetchall()


async def db_get_team_members(team_id: int):
    """Получить список членов команды с их per-team полями из membership и базовыми данными пользователя."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT e.tg_id, e.username, e.full_name,
                       m.role AS role,
                       m.daily_time AS daily_time,
                       m.vacation_start, m.vacation_end,
                       m.is_manager,  m.is_participant, m.is_po,
                       m.gitverse_nickname AS gitverse_nickname
                FROM employees e
                LEFT JOIN user_team_memberships m ON e.tg_id = m.employee_tg_id AND m.team_id = %s
                WHERE m.team_id = %s
                ORDER BY e.full_name ASC
                """,
                (team_id, team_id),
            )
            return await cur.fetchall()


async def db_get_employees_for_daily_by_membership(time_str: str, team_id: int):
    """Получить TGID сотрудников команды по группе времени из membership."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT employee_tg_id FROM user_team_memberships WHERE team_id = %s AND daily_time = %s",
                (team_id, time_str),
            )
            rows = await cur.fetchall()
            return [row[0] for row in rows]


@db_retry(max_attempts=3)
async def db_add_report_with_team(
    tg_id: int,
    team_id: int,
    yesterday: str,
    today: str,
    problems: str,
    llm_questions: str | None = None,
) -> int:
    """Добавить отчёт, жёстко связывая его с командой."""
    pool = await get_pool()
    async with pool.connection() as conn:
        now_local = get_current_time()
        report_datetime = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO reports (employee_tg_id, report_datetime, yesterday, today, problems, llm_questions, team_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (tg_id, report_datetime, yesterday, today, problems, llm_questions, team_id),
            )
            row = await cur.fetchone()
        await conn.commit()
        logging.info(f"В БД добавлен отчет от пользователя {tg_id} для команды {team_id} в {now_local.strftime('%H:%M:%S')}")
        return int(row[0]) if row and row[0] is not None else 0


# --- Функции для работы с приглашениями ---
@db_retry(max_attempts=3)
async def db_create_invite(team_id, invite_code):
    """Создание приглашения в команду (одна ссылка на команду)"""
    pool = await get_pool()
    async with pool.connection() as conn:
        from datetime import datetime
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO team_invites (team_id, invite_code, created_at, is_active)
                VALUES (%s, %s, %s, TRUE)
                ON CONFLICT (team_id) DO UPDATE
                SET invite_code = EXCLUDED.invite_code,
                    created_at = EXCLUDED.created_at,
                    is_active = TRUE
                """,
                (team_id, invite_code, created_at),
            )
        await conn.commit()

async def db_get_invite_by_code(invite_code):
    """Получение приглашения по коду"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, team_id, invite_code, created_at, is_active FROM team_invites WHERE invite_code = %s",
                (invite_code,),
            )
            return await cur.fetchone()

async def db_get_team_invite(team_id):
    """Получение приглашения команды (одна ссылка на команду)"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, invite_code, created_at, is_active FROM team_invites WHERE team_id = %s",
                (team_id,),
            )
            return await cur.fetchone()

@db_retry(max_attempts=3)
async def db_toggle_invite_status(team_id, is_active):
    """Активировать или деактивировать приглашение команды"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE team_invites SET is_active = %s WHERE team_id = %s", (is_active, team_id))
        await conn.commit()

# --- Функции для работы с настройками времени команды ---
async def db_get_team_time_settings(team_id: int):
    """Получение настроек времени команды"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT to_char(morning_time, 'HH24:MI') AS morning_time,
                       to_char(evening_time, 'HH24:MI') AS evening_time,
                       to_char(report_time, 'HH24:MI') AS report_time,
                       report_days
                FROM teams WHERE id = %s
                """,
                (team_id,),
            )
            return await cur.fetchone()

@db_retry(max_attempts=3)
async def db_update_team_time_settings(team_id: int, morning_time: str, evening_time: str, 
                                     report_time: str, report_days: str):
    """Обновление настроек времени команды"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE teams SET 
                    morning_time = %s, evening_time = %s, report_time = %s, report_days = %s
                WHERE id = %s
                """,
                (morning_time, evening_time, report_time, report_days, team_id),
            )
        await conn.commit()

async def db_get_teams_by_time(time_type: str, time_value: str):
    """Получение команд по времени (для планировщика)"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"""
                SELECT id, name, chat_id, chat_topic_id, report_days, questions_json
                FROM teams WHERE {time_type}_time = %s
                """,
                (time_value,),
            )
            return await cur.fetchall()

async def db_get_all_teams_with_times():
    """Получение всех команд с настройками времени"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, name, chat_id, chat_topic_id, board_link,
                        to_char(morning_time, 'HH24:MI') AS morning_time,
                        to_char(evening_time, 'HH24:MI') AS evening_time,
                        to_char(report_time, 'HH24:MI') AS report_time,
                        report_days, timezone, created_at, test_flag, questions_json,
                        weekly_plan_day, to_char(weekly_plan_time, 'HH24:MI') AS weekly_plan_time,
                        weekly_analysis_day, to_char(weekly_analysis_time, 'HH24:MI') AS weekly_analysis_time,
                        sprint_enabled, sprint_duration_weeks
                FROM teams
                """
            )
            return await cur.fetchall()

# --- Функции для работы с вопросами команды ---
@db_retry(max_attempts=3)
async def migrate_add_questions_fields():
    """Миграция для добавления полей questions_json и answers_json"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # Добавляем колонки, если их нет 
            # Для teams: questions_json как JSONB
            await cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'teams' AND column_name = 'questions_json') THEN
                        ALTER TABLE teams ADD COLUMN questions_json JSONB DEFAULT '[]'::JSONB;
                    END IF;
                END $$;
            """)
            # Для reports: answers_json
            await cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'reports' AND column_name = 'answers_json') THEN
                        ALTER TABLE reports ADD COLUMN answers_json JSONB DEFAULT '{}'::JSONB;
                    END IF;
                END $$;
            """)

            # Заполняем дефолтными вопросами команды с пустым questions_json
            default_questions = json.dumps([
                {
                    "id": 1,
                    "text": "Вопрос с вариантами",
                    "field": "yesterday",
                    "time_variants": {"morning": " Что вы сделали вчера?", "evening": " Что вы сделали сегодня?"},
                    "board_related": False
                },
                {
                    "id": 2,
                    "text": "Вопрос с вариантами",
                    "field": "today",
                    "time_variants": {"morning": "Что вы планируете сделать сегодня?", "evening": "Что вы планируете сделать завтра?"},
                    "board_related": True
                },
                {
                    "id": 3,
                    "text": "Какие есть трудности или проблемы? (Если нет, напишите 'нет')",
                    "field": "problems",
                    "time_variants": {},
                    "board_related": False
                }
            ], ensure_ascii=False)

            # Обновляем команды, у которых questions_json пуст или NULL
            await cur.execute(
                """
                UPDATE teams 
                SET questions_json = %s 
                WHERE questions_json IS NULL OR questions_json = '[]'::jsonb
                """,
                (default_questions,)
            )

        await conn.commit()
    logging.info("Миграция questions_json и answers_json завершена")

async def db_get_team_questions(team_id: int) -> list:
    """Получить вопросы команды из БД"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT questions_json FROM teams WHERE id = %s", (team_id,))
            row = await cur.fetchone()
            return row['questions_json'] if row and row['questions_json'] else []

@db_retry(max_attempts=3)
async def db_update_team_questions(team_id: int, questions: list) -> bool:
    """Обновить вопросы команды в БД с валидацией структуры"""
    # Валидация структуры вопросов
    for q in questions:
        if not isinstance(q, dict):
            logging.error(f"Некорректный формат вопроса: {q}")
            return False
        if not all(key in q for key in ['id', 'text', 'field']):
            logging.error(f"Отсутствуют обязательные поля в вопросе: {q}")
            return False
        if not isinstance(q['id'], int):
            logging.error(f"Поле 'id' должно быть целым числом: {q}")
            return False
        if not isinstance(q['text'], str):
            logging.error(f"Поле 'text' должно быть строкой: {q}")
            return False
        if not isinstance(q['field'], str):
            logging.error(f"Поле 'field' должно быть строкой: {q}")
            return False
        if 'time_variants' in q and not isinstance(q['time_variants'], dict):
            logging.error(f"Поле 'time_variants' должно быть словарем: {q}")
            return False
        if 'board_related' in q and not isinstance(q['board_related'], bool):
            logging.error(f"Поле 'board_related' должно быть булевым: {q}")
            return False

    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE teams SET questions_json = %s WHERE id = %s",
                (json.dumps(questions, ensure_ascii=False), team_id)
            )
        await conn.commit()
    logging.info(f"Вопросы для команды {team_id} обновлены: {questions}")
    return True 

@db_retry(max_attempts=3)
async def db_update_membership_participation(tg_id: int, team_id: int) -> bool:
    """Переключает статус участия менеджера в опросах."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                UPDATE user_team_memberships 
                SET is_participant = NOT is_participant 
                WHERE employee_tg_id = %s AND team_id = %s AND is_manager = TRUE
                RETURNING is_participant
            """, (tg_id, team_id))
            row = await cur.fetchone()
            await conn.commit()
            return row[0] if row else False
# --- Функции для работы с кураторами ---

@db_retry(max_attempts=3)
async def db_is_curator(tg_id: int) -> bool:
    """Проверить, является ли пользователь куратором."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM curators WHERE tg_id = %s", (tg_id,))
            row = await cur.fetchone()
            return bool(row)

@db_retry(max_attempts=3)
async def db_add_curator(tg_id: int) -> None:
    """Добавить пользователя в список кураторов."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO curators (tg_id)
                VALUES (%s)
                ON CONFLICT (tg_id) DO NOTHING
                """,
                (tg_id,),
            )
        await conn.commit()

@db_retry(max_attempts=3)
async def db_remove_curator(tg_id: int) -> None:
    """Удалить пользователя из списка кураторов."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM curators WHERE tg_id = %s", (tg_id,))
        await conn.commit()

@db_retry(max_attempts=3)
async def db_list_curators() -> list[int]:
    """Получить список TGID всех кураторов."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT tg_id FROM curators ORDER BY added_at ASC")
            rows = await cur.fetchall()
            return [row[0] for row in rows]

# --- Функции для работы со статистикой токенов LLM ---

@db_retry(max_attempts=3)
async def db_get_token_usage_by_event(start_date: str, end_date: str) -> list[dict]:
    """Получить использование токенов по событиям за период"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT 
                    event,
                    SUM(input_tokens) as total_input_tokens,
                    SUM(output_tokens) as total_output_tokens,
                    SUM(total_tokens) as total_tokens_sum,
                    COUNT(*) as request_count
                FROM llm_token_usage
                WHERE created_at BETWEEN %s AND %s
                GROUP BY event
                ORDER BY total_tokens_sum DESC
                """,
                (start_date, end_date)
            )
            return await cur.fetchall()

@db_retry(max_attempts=3)
async def db_get_token_usage_by_team(start_date: str, end_date: str) -> list[dict]:
    """Получить использование токенов по командам за период"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT 
                    t.id as team_id,
                    t.name as team_name,
                    COALESCE(SUM(ltu.total_tokens), 0) as total_tokens,
                    COUNT(*) as request_count
                FROM teams t
                LEFT JOIN llm_token_usage ltu ON t.id = ltu.team_id 
                    AND ltu.created_at BETWEEN %s AND %s
                GROUP BY t.id, t.name
                HAVING COALESCE(SUM(ltu.total_tokens), 0) > 0
                ORDER BY total_tokens DESC
                """,
                (start_date, end_date)
            )
            return await cur.fetchall()

@db_retry(max_attempts=3)
async def db_get_duration_by_hour(start_date: str, end_date: str) -> list[dict]:
    """Получить среднюю длительность ответов по часам"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT 
                    EXTRACT(HOUR FROM created_at) as hour,
                    AVG(duration_ms) as avg_duration,
                    COUNT(*) as request_count
                FROM llm_token_usage
                WHERE created_at BETWEEN %s AND %s
                    AND duration_ms IS NOT NULL
                GROUP BY EXTRACT(HOUR FROM created_at)
                ORDER BY hour
                """,
                (start_date, end_date)
            )
            return await cur.fetchall()

@db_retry(max_attempts=3)
async def db_get_duration_by_20min(start_date: str, end_date: str) -> list[dict]:
    """Получить среднюю длительность ответов по 20-минутным интервалам"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT 
                    EXTRACT(HOUR FROM created_at) as hour,
                    FLOOR(EXTRACT(MINUTE FROM created_at) / 20) * 20 as minute,
                    AVG(duration_ms) as avg_duration,
                    COUNT(*) as request_count
                FROM llm_token_usage
                WHERE created_at BETWEEN %s AND %s
                    AND duration_ms IS NOT NULL
                GROUP BY EXTRACT(HOUR FROM created_at), FLOOR(EXTRACT(MINUTE FROM created_at) / 20) * 20
                ORDER BY hour, minute
                """,
                (start_date, end_date)
            )
            return await cur.fetchall()

@db_retry(max_attempts=3)
async def db_get_requests_count_by_hour(start_date: str, end_date: str) -> list[dict]:
    """Получить сумму попыток обращения к API по часам"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT 
                    EXTRACT(HOUR FROM created_at) as hour,
                    COUNT(*) as requests_count,
                    SUM(attempts) as total_attempts,
                    SUM(CASE WHEN attempts >= 5 AND duration_ms IS NULL THEN attempts ELSE 0 END) as failed_attempts,
                    SUM(CASE WHEN attempts >= 2 THEN attempts - 1 ELSE 0 END) as retry_attempts
                FROM llm_token_usage
                WHERE created_at BETWEEN %s AND %s
                GROUP BY EXTRACT(HOUR FROM created_at)
                ORDER BY hour
                """,
                (start_date, end_date)
            )
            return await cur.fetchall()

@db_retry(max_attempts=3)
async def db_get_token_usage_by_day(start_date: str, end_date: str) -> list[dict]:
    """Получить траты токенов по дням за период"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT 
                    DATE(created_at) as date,
                    SUM(total_tokens) as total_tokens,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    COUNT(*) as request_count
                FROM llm_token_usage
                WHERE created_at BETWEEN %s AND %s
                GROUP BY DATE(created_at)
                ORDER BY date
                """,
                (start_date, end_date)
            )
            return await cur.fetchall()

@db_retry(max_attempts=3)
async def db_get_reports_by_team(start_date: str, end_date: str) -> list[dict]:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT 
                    t.id as team_id,
                    t.name as team_name,
                    COUNT(r.id) as report_count
                FROM teams t
                LEFT JOIN reports r ON t.id = r.team_id 
                    AND r.report_datetime BETWEEN %s AND %s
                GROUP BY t.id, t.name
                HAVING COUNT(r.id) > 0
                ORDER BY report_count DESC
                """,
                (start_date, end_date)
            )
            return await cur.fetchall()

@db_retry(max_attempts=3)
async def db_get_top_employees_by_tokens(start_date: str, end_date: str, limit: int = 10) -> list[dict]:
    """Получить топ участников по тратам токенов за период"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT 
                    e.tg_id,
                    e.full_name,
                    COALESCE(SUM(ltu.total_tokens), 0) as total_tokens,
                    COUNT(ltu.id) as request_count
                FROM employees e
                LEFT JOIN llm_token_usage ltu ON e.tg_id = ltu.employee_tg_id
                    AND ltu.created_at BETWEEN %s AND %s
                GROUP BY e.tg_id, e.full_name
                HAVING COALESCE(SUM(ltu.total_tokens), 0) > 0
                ORDER BY total_tokens DESC
                LIMIT %s
                """,
                (start_date, end_date, limit)
            )
            return await cur.fetchall()

@db_retry(max_attempts=3)
async def db_get_team_member_counts() -> dict[int, int]:
    pool = await get_pool()
    result = {}
    today = datetime.utcnow().date()
    
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # Форматируем сегодняшнюю дату в формат 'DD-MM-YYYY' для сравнения
            today_str = today.strftime('%d-%m-%Y')
            
            await cur.execute(
                """
                SELECT team_id, COUNT(*) as member_count
                FROM user_team_memberships
                WHERE ((is_manager = FALSE) OR (is_manager = TRUE AND is_participant = TRUE))
                  AND (
                      vacation_start IS NULL 
                      OR vacation_end IS NULL
                      OR vacation_start = ''
                      OR vacation_end = ''
                      OR NOT (
                          TO_DATE(vacation_start, 'DD-MM-YYYY') <= TO_DATE(%s, 'DD-MM-YYYY')
                          AND TO_DATE(vacation_end, 'DD-MM-YYYY') >= TO_DATE(%s, 'DD-MM-YYYY')
                      )
                  )
                GROUP BY team_id
                """,
                (today_str, today_str)
            )
            rows = await cur.fetchall()
            for row in rows:
                result[row[0]] = row[1]
    return result

@db_retry(max_attempts=3)
async def db_get_team_settings() -> dict:
    """Получить настройки всех команд (report_days и т.д.)"""
    pool = await get_pool()
    result = {}
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, report_days, morning_time, evening_time, timezone
                FROM teams
                """
            )
            rows = await cur.fetchall()
            for row in rows:
                result[row['id']] = {
                    'report_days': row.get('report_days', ''),
                    'morning_time': row.get('morning_time', ''),
                    'evening_time': row.get('evening_time', ''),
                    'timezone': row.get('timezone', 'Asia/Yekaterinburg')
                }
    return result

@db_retry(max_attempts=3)
async def db_get_failed_requests(start_date: str, end_date: str) -> list[dict]:
    """Получить список неудачных запросов к API (с NULL duration_ms и 5+ попыток)"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT 
                    event,
                    attempts,
                    created_at,
                    team_id,
                    employee_tg_id
                FROM llm_token_usage
                WHERE created_at BETWEEN %s AND %s
                    AND attempts >= 5
                    AND duration_ms IS NULL
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (start_date, end_date)
            )
            return await cur.fetchall()

@db_retry(max_attempts=3)
async def db_get_attempts_statistics(start_date: str, end_date: str) -> dict[int, int]:
    """Получить статистику количества запросов по количеству попыток (1, 2, 3, 4, 5+)"""
    pool = await get_pool()
    result = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT 
                    CASE 
                        WHEN attempts = 1 THEN 1
                        WHEN attempts = 2 THEN 2
                        WHEN attempts = 3 THEN 3
                        WHEN attempts = 4 THEN 4
                        WHEN attempts >= 5 THEN 5
                        ELSE 1
                    END as attempt_group,
                    COUNT(*) as count
                FROM llm_token_usage
                WHERE created_at BETWEEN %s AND %s
                GROUP BY attempt_group
                ORDER BY attempt_group
                """,
                (start_date, end_date)
            )
            rows = await cur.fetchall()
            for row in rows:
                attempt_group = int(row['attempt_group'])
                count = int(row['count'])
                result[attempt_group] = count
    return result

@db_retry(max_attempts=3)
async def db_get_total_teams_count() -> int:
    """Получить общее количество команд"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM teams")
            row = await cur.fetchone()
            return row[0] if row else 0

@db_retry(max_attempts=3)
async def db_get_total_members_count() -> int:
    """Получить общее количество участников (всех, кто должен отправлять отчёты, не в отпуске)"""
    pool = await get_pool()
    today = datetime.utcnow().date()
    today_str = today.strftime('%d-%m-%Y')
    
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT COUNT(*) as member_count
                FROM user_team_memberships
                WHERE ((is_manager = FALSE) OR (is_manager = TRUE AND is_participant = TRUE))
                  AND (
                      vacation_start IS NULL 
                      OR vacation_end IS NULL
                      OR vacation_start = ''
                      OR vacation_end = ''
                      OR NOT (
                          TO_DATE(vacation_start, 'DD-MM-YYYY') <= TO_DATE(%s, 'DD-MM-YYYY')
                          AND TO_DATE(vacation_end, 'DD-MM-YYYY') >= TO_DATE(%s, 'DD-MM-YYYY')
                      )
                  )
                """,
                (today_str, today_str)
            )
            row = await cur.fetchone()
            return row[0] if row else 0

@db_retry(max_attempts=3)
async def db_get_active_members_count(start_date: str, end_date: str) -> int:
    """Получить количество активных участников за период (тех, кто отправил хотя бы 1 отчёт)"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT COUNT(DISTINCT r.employee_tg_id) as active_members
                FROM reports r
                WHERE r.report_datetime BETWEEN %s AND %s
                """,
                (start_date, end_date)
            )
            row = await cur.fetchone()
            return row[0] if row else 0


# --- Миграция и функции для weekly_plans ---

@db_retry(max_attempts=3)
async def migrate_add_weekly_plans_table():
    """Миграция: создание таблицы weekly_plans без ограничений"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS public.weekly_plans (
                    id SERIAL PRIMARY KEY,
                    employee_tg_id BIGINT NOT NULL,
                    team_id INTEGER NOT NULL,
                    week_start_date DATE NOT NULL,
                    plan_text TEXT,
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """)
        await conn.commit()
    logging.info("Таблица weekly_plans создана или уже существует.")


@db_retry(max_attempts=3)
async def db_save_weekly_plan(employee_tg_id: int, team_id: int, week_start_date: str, plan_text: str):
    async with (await get_pool()).connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO weekly_plans (employee_tg_id, team_id, week_start_date, plan_text)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (employee_tg_id, team_id, week_start_date)
                DO UPDATE SET plan_text = EXCLUDED.plan_text, updated_at = NOW()
            """, (employee_tg_id, team_id, week_start_date, plan_text))
        await conn.commit()

async def db_get_weekly_plan(employee_tg_id: int, week_start_date: str, team_id: int) -> dict | None:
    async with (await get_pool()).connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:  # Добавляем row_factory=dict_row
            await cur.execute(
                "SELECT plan_text FROM weekly_plans WHERE employee_tg_id = %s AND week_start_date = %s AND team_id = %s",
                (employee_tg_id, week_start_date, team_id)
            )
            return await cur.fetchone()

@db_retry(max_attempts=3)
async def migrate_update_weekly_plans_constraint():
    """Миграция: обновление уникального ограничения в weekly_plans"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # Проверяем наличие старого ограничения unique_employee_week
            await cur.execute("""
                SELECT 1 FROM pg_constraint
                WHERE conname = 'unique_employee_week';
            """)
            old_constraint_exists = await cur.fetchone()

            # Проверяем наличие старого индекса idx_weekly_plans_employee_week
            await cur.execute("""
                SELECT 1 FROM pg_indexes
                WHERE indexname = 'idx_weekly_plans_employee_week';
            """)
            old_index_exists = await cur.fetchone()

            # Проверяем наличие нового ограничения unique_employee_team_week
            await cur.execute("""
                SELECT 1 FROM pg_constraint
                WHERE conname = 'unique_employee_team_week';
            """)
            new_constraint_exists = await cur.fetchone()

            # Удаляем старое ограничение, если оно существует
            if old_constraint_exists:
                await cur.execute("""
                    ALTER TABLE public.weekly_plans
                    DROP CONSTRAINT unique_employee_week;
                """)
                logging.info("Старое ограничение unique_employee_week удалено.")

            # Удаляем старый индекс, если он существует
            if old_index_exists:
                await cur.execute("""
                    DROP INDEX IF EXISTS idx_weekly_plans_employee_week;
                """)
                logging.info("Старый индекс idx_weekly_plans_employee_week удален.")

            # Добавляем новое ограничение, если его нет
            if not new_constraint_exists:
                await cur.execute("""
                    ALTER TABLE public.weekly_plans
                    ADD CONSTRAINT unique_employee_team_week
                    UNIQUE (employee_tg_id, team_id, week_start_date);
                """)
                logging.info("Уникальное ограничение unique_employee_team_week добавлено.")

            # Проверяем и добавляем новый индекс
            await cur.execute("""
                SELECT 1 FROM pg_indexes
                WHERE indexname = 'idx_weekly_plans_employee_team_week';
            """)
            new_index_exists = await cur.fetchone()
            if not new_index_exists:
                await cur.execute("""
                    CREATE UNIQUE INDEX idx_weekly_plans_employee_team_week
                    ON public.weekly_plans (employee_tg_id, team_id, week_start_date);
                """)
                logging.info("Уникальный индекс idx_weekly_plans_employee_team_week добавлен.")
            else:
                logging.info("Индекс idx_weekly_plans_employee_team_week уже существует.")
        await conn.commit()
    logging.info("Миграция unique_employee_team_week завершена.")

async def db_get_weekly_reports_for_employee(employee_tg_id: int, team_id: int, week_start_date: str) -> list[dict]:
    """
    Получить все отчёты сотрудника за неделю (с понедельника по воскресенье).
    week_start_date — в формате 'YYYY-MM-DD' (понедельник).
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # Определяем конец недели
            from datetime import datetime, timedelta
            start = datetime.strptime(week_start_date, '%Y-%m-%d')
            end = start + timedelta(days=6)
            end_date = end.strftime('%Y-%m-%d')

            await cur.execute("""
                SELECT answers_json, report_datetime
                FROM reports
                WHERE employee_tg_id = %s AND team_id = %s
                  AND report_datetime::date BETWEEN %s AND %s
                ORDER BY report_datetime ASC
            """, (employee_tg_id, team_id, week_start_date, end_date))
            return await cur.fetchall()


# --- Спринты ---

@db_retry(max_attempts=3)
async def db_update_sprint_settings(team_id: int, *, enabled: bool | None = None, duration_weeks: int | None = None):
    """Обновление настроек спринтов для команды"""
    if enabled is None and duration_weeks is None:
        return
    updates = []
    params = []
    if enabled is not None:
        updates.append("sprint_enabled = %s")
        params.append(enabled)
    if duration_weeks is not None:
        updates.append("sprint_duration_weeks = %s")
        params.append(max(1, duration_weeks))
    params.append(team_id)
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE teams SET {', '.join(updates)} WHERE id = %s",
                tuple(params)
            )
        await conn.commit()


@db_retry(max_attempts=3)
async def db_team_has_active_sprint(team_id: int) -> bool:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM team_sprints WHERE team_id = %s AND is_active = TRUE LIMIT 1",
                (team_id,)
            )
            row = await cur.fetchone()
            return bool(row)


@db_retry(max_attempts=3)
async def db_get_active_sprint(team_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, team_id, started_at, finished_at, is_active,
                       start_date, end_date, last_report_date,
                       duration_weeks, plans_requested
                FROM team_sprints
                WHERE team_id = %s AND is_active = TRUE
                ORDER BY start_date ASC
                LIMIT 1
                """,
                (team_id,)
            )
            return await cur.fetchone()


@db_retry(max_attempts=3)
async def db_get_sprint_by_id(sprint_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, team_id, started_at, finished_at, is_active,
                       start_date, end_date, last_report_date,
                       duration_weeks, plans_requested
                FROM team_sprints
                WHERE id = %s
                """,
                (sprint_id,)
            )
            return await cur.fetchone()


@db_retry(max_attempts=3)
async def db_create_sprint(team_id: int, start_date: date, duration_weeks: int, report_days: str | None, force_start_date: bool = False) -> dict:
    """Создать новый спринт и вернуть его данные.

    Args:
        force_start_date: Если True, использовать start_date как есть, без сдвига на понедельник
    """
    if not force_start_date:
        start_date = get_next_monday(start_date)
    duration_weeks = max(1, duration_weeks)
    last_report_day = calculate_last_report_date(start_date, duration_weeks, report_days)
    end_date = last_report_day
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur_cleanup:
            await cur_cleanup.execute(
                "UPDATE team_sprints SET is_active = FALSE WHERE team_id = %s AND is_active = TRUE",
                (team_id,)
            )
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO team_sprints (team_id, start_date, end_date, last_report_date, duration_weeks, is_active, plans_requested)
                VALUES (%s, %s, %s, %s, %s, TRUE, FALSE)
                RETURNING id, team_id, started_at, finished_at, is_active,
                          start_date, end_date, last_report_date, duration_weeks, plans_requested
                """,
                (team_id, start_date, end_date, last_report_day, duration_weeks)
            )
            row = await cur.fetchone()
        await conn.commit()
        return row


@db_retry(max_attempts=3)
async def db_finish_sprint(sprint_id: int):
    """Завершить спринт"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE team_sprints
                SET is_active = FALSE, finished_at = NOW()
                WHERE id = %s
                """,
                (sprint_id,)
            )
        await conn.commit()


@db_retry(max_attempts=3)
async def db_update_sprint_dates(sprint_id: int, *, end_date: date | None = None, last_report_date: date | None = None, duration_weeks: int | None = None):
    """Обновление дат активного спринта"""
    if not any([end_date, last_report_date, duration_weeks]):
        return

    updates = []
    params = []

    # Если передана last_report_date, автоматически устанавливаем end_date равной ей
    if last_report_date is not None:
        updates.append("end_date = %s")
        params.append(last_report_date)
        updates.append("last_report_date = %s")
        params.append(last_report_date)
    elif end_date is not None:
        # Если передана только end_date, устанавливаем last_report_date равной ей
        updates.append("end_date = %s")
        params.append(end_date)
        updates.append("last_report_date = %s")
        params.append(end_date)

    if duration_weeks is not None:
        updates.append("duration_weeks = %s")
        params.append(duration_weeks)

    params.append(sprint_id)
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE team_sprints SET {', '.join(updates)} WHERE id = %s",
                tuple(params)
            )
        await conn.commit()


@db_retry(max_attempts=3)
async def db_mark_sprint_plans_requested(sprint_id: int):
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE team_sprints SET plans_requested = TRUE WHERE id = %s",
                (sprint_id,)
            )
        await conn.commit()
    logging.info("Миграция unique_employee_team_week завершена.")


@db_retry(max_attempts=3)
async def migrate_add_is_po_field():
    """Миграция: добавление поля is_po в таблицу user_team_memberships"""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 
                        FROM information_schema.columns 
                        WHERE table_name='user_team_memberships' 
                        AND column_name='is_po'
                    ) THEN
                        ALTER TABLE user_team_memberships 
                        ADD COLUMN is_po BOOLEAN NOT NULL DEFAULT FALSE;
                        
                        -- Добавляем всех существующих PO из таблицы product_owners в user_team_memberships
                        UPDATE user_team_memberships utm
                        SET is_po = TRUE
                        FROM product_owners po
                        WHERE utm.employee_tg_id = po.employee_tg_id 
                          AND utm.team_id = po.team_id;
                    END IF;
                END $$;
            """)
        await conn.commit()
    logging.info("Миграция is_po завершена.")


# --- Функции для работы с Product Owners ---

@db_retry(max_attempts=3)
async def db_add_product_owner(employee_tg_id: int, team_id: int) -> int:
    """Добавить Product Owner в команду. Также обновляет is_po в user_team_memberships."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # Добавляем запись в product_owners
            await cur.execute(
                """
                INSERT INTO product_owners (employee_tg_id, team_id)
                VALUES (%s, %s)
                ON CONFLICT (employee_tg_id, team_id) DO NOTHING
                RETURNING id
                """,
                (employee_tg_id, team_id),
            )
            row = await cur.fetchone()

            # Обновляем is_po в user_team_memberships
            await cur.execute(
                """
                UPDATE user_team_memberships
                SET is_po = TRUE
                WHERE employee_tg_id = %s AND team_id = %s
                """,
                (employee_tg_id, team_id),
            )
        await conn.commit()
        return int(row[0]) if row and row[0] is not None else 0


@db_retry(max_attempts=3)
async def db_remove_product_owner(employee_tg_id: int, team_id: int) -> None:
    """Удалить Product Owner из команды. Также обновляет is_po в user_team_memberships."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # Удаляем из product_owners
            await cur.execute(
                "DELETE FROM product_owners WHERE employee_tg_id = %s AND team_id = %s",
                (employee_tg_id, team_id),
            )

            # Обновляем is_po в user_team_memberships
            await cur.execute(
                """
                UPDATE user_team_memberships
                SET is_po = FALSE
                WHERE employee_tg_id = %s AND team_id = %s
                """,
                (employee_tg_id, team_id),
            )
        await conn.commit()


async def db_get_product_owners_by_team(team_id: int):
    """Получить список Product Owners команды с их данными."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT 
                    e.tg_id, 
                    e.username, 
                    e.full_name,
                    po.created_at
                FROM product_owners po
                JOIN employees e ON po.employee_tg_id = e.tg_id
                WHERE po.team_id = %s
                ORDER BY e.full_name ASC
                """,
                (team_id,),
            )
            return await cur.fetchall()


async def db_is_product_owner(employee_tg_id: int, team_id: int) -> bool:
    """Проверить, является ли сотрудник Product Owner в команде."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM product_owners WHERE employee_tg_id = %s AND team_id = %s",
                (employee_tg_id, team_id),
            )
            row = await cur.fetchone()
            return bool(row)


async def db_get_user_po_teams(employee_tg_id: int):
    """Получить список команд, где пользователь является Product Owner."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT t.id, t.name, t.chat_id, t.chat_topic_id, t.board_link,
                       to_char(t.morning_time, 'HH24:MI') AS morning_time,
                       to_char(t.evening_time, 'HH24:MI') AS evening_time,
                       to_char(t.report_time,  'HH24:MI') AS report_time,
                       t.report_days, t.timezone, t.created_at, t.test_flag
                FROM product_owners po
                JOIN teams t ON t.id = po.team_id
                WHERE po.employee_tg_id = %s
                ORDER BY t.name ASC
                """,
                (employee_tg_id,),
            )
            return await cur.fetchall()


@db_retry(max_attempts=3)
async def db_add_sprint_plan_entry(sprint_id: int, employee_tg_id: int, plan_text: str):
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO sprint_plans (sprint_id, employee_tg_id, plan_text)
                VALUES (%s, %s, %s)
                """,
                (sprint_id, employee_tg_id, plan_text)
            )
        await conn.commit()


@db_retry(max_attempts=3)
async def db_get_sprint_plans_for_user(sprint_id: int, employee_tg_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, sprint_id, employee_tg_id, plan_text, created_at
                FROM sprint_plans
                WHERE sprint_id = %s AND employee_tg_id = %s
                ORDER BY created_at ASC
                """,
                (sprint_id, employee_tg_id)
            )
            return await cur.fetchall()


@db_retry(max_attempts=3)
async def db_get_sprint_plans_for_team(sprint_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT sp.id,
                       sp.employee_tg_id,
                       sp.plan_text,
                       sp.created_at,
                       e.full_name,
                       COALESCE(m.role, '') AS role
                FROM sprint_plans sp
                JOIN team_sprints ts ON ts.id = sp.sprint_id
                LEFT JOIN employees e ON e.tg_id = sp.employee_tg_id
                LEFT JOIN user_team_memberships m ON m.employee_tg_id = sp.employee_tg_id
                                                   AND m.team_id = ts.team_id
                WHERE sp.sprint_id = %s
                ORDER BY sp.created_at ASC
                """,
                (sprint_id,)
            )
            return await cur.fetchall()


@db_retry(max_attempts=3)
async def db_get_reports_for_sprint(team_id: int, start_date: date, end_date: date) -> list[dict]:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT r.employee_tg_id,
                       r.report_datetime,
                       r.answers_json,
                       r.llm_questions,
                       r.llm_answer,
                       e.full_name,
                       COALESCE(m.role, '') AS role
                FROM reports r
                LEFT JOIN employees e ON e.tg_id = r.employee_tg_id
                LEFT JOIN user_team_memberships m ON m.employee_tg_id = r.employee_tg_id AND m.team_id = r.team_id
                WHERE r.team_id = %s
                  AND r.report_datetime::date BETWEEN %s AND %s
                  AND (m.is_manager = FALSE OR (m.is_manager = TRUE AND m.is_participant = TRUE))
                ORDER BY r.report_datetime ASC
                """,
                (team_id, start_date, end_date)
            )
            return await cur.fetchall()