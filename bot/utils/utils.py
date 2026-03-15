import logging
from datetime import datetime
import pytz
import asyncio
import random
import traceback
import requests
import time
from aiogram.exceptions import (
    TelegramAPIError, 
    TelegramNetworkError, 
    TelegramServerError,
    TelegramNotFound,
    TelegramForbiddenError
)
from aiogram.types import Message
from aiogram.types import BufferedInputFile
from bot.config import TIMEZONE, ERROR_LOG_CHAT_ID, ERROR_LOG_TOPIC_ID, BOT_TOKEN
from aiogram.types import ReplyKeyboardRemove
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
import json


class TimezoneFormatter(logging.Formatter):
    """Кастомный форматтер для логов с учетом часового пояса"""
    
    def __init__(self, fmt=None, datefmt=None):
        super().__init__(fmt, datefmt)
        self.timezone = pytz.timezone(TIMEZONE)
    
    def formatTime(self, record, datefmt=None):
        """Переопределяем форматирование времени для использования нашего часового пояса"""
        ct = datetime.fromtimestamp(record.created, tz=self.timezone)
        if datefmt:
            return ct.strftime(datefmt)
        else:
            return ct.strftime('%Y-%m-%d %H:%M:%S')


class TelegramLogHandler(logging.Handler):
    """Handler для отправки ошибок в Telegram чат"""
    def __init__(self, chat_id: int, topic_id: int | None = None):
        super().__init__()
        self.chat_id = chat_id
        self.topic_id = topic_id
        self.setLevel(logging.ERROR)
        self._last_send_time = 0
        self._lock = None  # Будет создан при первом использовании
        
    def _get_lock(self):
        """Получает или создает asyncio.Lock"""
        if self._lock is None:
            try:
                loop = asyncio.get_running_loop()
                self._lock = asyncio.Lock()
            except RuntimeError:
                return None
        return self._lock
        
    async def _send_message_async(self, message: str):
        """Асинхронная отправка сообщения в Telegram"""
        if not self.chat_id or not BOT_TOKEN:
            return
        
        lock = self._get_lock()
        if lock:
            async with lock:
                current_time = time.time()
                time_since_last = current_time - self._last_send_time
                if time_since_last < 5.0:
                    await asyncio.sleep(5.0 - time_since_last)
                
                try:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    payload = {
                        'chat_id': self.chat_id,
                        'text': f'❌ <b>Ошибка</b>\n\n<blockquote>{message}</blockquote>',
                        'parse_mode': 'HTML'
                    }
                    if self.topic_id and self.topic_id != 0:
                        payload['message_thread_id'] = self.topic_id
                    
                    response = await asyncio.to_thread(requests.post, url, json=payload, timeout=5)
                    response.raise_for_status()
                    self._last_send_time = time.time()
                except Exception:
                    pass
    
    def emit(self, record: logging.LogRecord):
        """Отправляет ошибку в Telegram с задержкой"""
        if not self.chat_id or not BOT_TOKEN:
            return
        
        try:
            message = self.format(record)
            if record.exc_info:
                message += '\n\n' + ''.join(traceback.format_exception(*record.exc_info))
            
            if len(message) > 400:
                message = message[:380] + "... (обрезано)"
            
            # Используем asyncio для отправки
            try:
                asyncio.get_running_loop()
                asyncio.create_task(self._send_message_async(message))
            except RuntimeError:
                pass
        except Exception:
            pass


def setup_logging():
    """Настройка логгирования с правильным часовым поясом"""
    logger = logging.getLogger()
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    console_handler = logging.StreamHandler()
    formatter = TimezoneFormatter(
        fmt='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    
    logger.setLevel(logging.INFO)
    logger.addHandler(console_handler)
    
    telegram_handler = None
    if ERROR_LOG_CHAT_ID:
        telegram_handler = TelegramLogHandler(ERROR_LOG_CHAT_ID, ERROR_LOG_TOPIC_ID)
        telegram_handler.setFormatter(formatter)
        logger.addHandler(telegram_handler)
        logging.info(f"Telegram handler для ошибок настроен")
    
    logging.info(f"Логгирование настроено для часового пояса: {TIMEZONE}")
    return telegram_handler


def get_current_time(timezone: str | None = None):
    """Получает текущее время с учетом указанного или настроенного часового пояса"""
    tz = pytz.timezone(timezone if timezone else TIMEZONE)
    return datetime.now(tz)


def parse_date_flexible(date_string: str) -> str:
    """
    Парсит дату в различных форматах и возвращает в стандартном формате ДД-ММ-ГГГГ
    Поддерживаемые форматы:
    - ДД-ММ-ГГГГ (15-01-2025)
    - ДД.ММ.ГГГГ (15.01.2025)
    - ДД ММ ГГГГ (15 01 2025)
    
    Args:
        date_string: Строка с датой
        
    Returns:
        str: Дата в формате ДД-ММ-ГГГГ или None если не удалось распарсить
    """
    if not date_string or not isinstance(date_string, str):
        return None
    
    # Убираем лишние пробелы
    date_string = date_string.strip()
    
    # Список возможных форматов
    formats = [
        '%d-%m-%Y',  # 15-01-2025
        '%d.%m.%Y',  # 15.01.2025
        '%d %m %Y',  # 15 01 2025
    ]
    
    for fmt in formats:
        try:
            # Пытаемся распарсить дату
            parsed_date = datetime.strptime(date_string, fmt)
            # Возвращаем в стандартном формате ДД-ММ-ГГГГ
            return parsed_date.strftime('%d-%m-%Y')
        except ValueError:
            continue
    
    # Если ни один формат не подошёл, возвращаем None
    return None


def validate_text_message(message: Message) -> bool:
    """Проверка, является ли сообщение текстовым"""
    return message.text is not None and isinstance(message.text, str)


def validate_text_or_voice_message(message: Message) -> bool:
    """Проверка, является ли сообщение текстовым или голосовым"""
    return validate_text_message(message) or (message.voice is not None)


async def extract_text_from_message(message: Message) -> str:
    """Извлекает текст из сообщения или транскрибирует голосовое сообщение"""
    if message.text:
        return message.text
    elif message.voice:
        from bot.utils.voice_utils import transcribe_voice_message
        return await transcribe_voice_message(message.voice.file_id)
    else:
        raise ValueError("Сообщение не содержит текста или голоса")


def validate_max_length(value: str, max_length: int) -> bool:
    """Проверка максимальной длины строки"""
    return value is not None and isinstance(value, str) and len(value.strip()) <= max_length


def get_error_message_for_expected_text(context: str) -> str:
    """Возвращает сообщение об ошибке для ожидаемого текста"""
    context_messages = {
        'full_name': 'Неверный формат ввода, ожидаю текстовое сообщение с вашим именем и инициалом фамилии (например: Иван И. или Иван Ив.)',
        'role': 'Неверный формат ввода, ожидаю текстовое сообщение с вашей ролью (например: Программист, Дизайнер)',
        'vacation_start': 'Неверный формат ввода, ожидаю текстовое сообщение с датой начала отпуска в формате ДД-ММ-ГГГГ, ДД.ММ.ГГГГ или ДД ММ ГГГГ (например: 15-01-2025, 15.01.2025 или 15 01 2025)',
        'vacation_end': 'Неверный формат ввода, ожидаю текстовое сообщение с датой окончания отпуска в формате ДД-ММ-ГГГГ, ДД.ММ.ГГГГ или ДД ММ ГГГГ (например: 25-01-2025, 25.01.2025 или 25 01 2025)',
        'yesterday': 'Неверный формат ввода, ожидаю текстовое сообщение с описанием того, что вы сделали вчера',
        'today': 'Неверный формат ввода, ожидаю текстовое сообщение с описанием того, что планируете сделать сегодня',
        'problems': 'Неверный формат ввода, ожидаю текстовое сообщение с описанием проблем или слово "нет"'
    }
    return context_messages.get(context, 'Неверный формат ввода, ожидаю текстовое сообщение')


def get_error_message_for_expected_text_or_voice(context: str) -> str:
    """Возвращает сообщение об ошибке для ожидаемого текста или голосового сообщения"""
    context_messages = {
        'full_name': 'Неверный формат ввода, ожидаю текстовое сообщение с вашим именем и инициалом фамилии (например: Иван И. или Иван Ив.)',
        'role': 'Неверный формат ввода, ожидаю текстовое сообщение с вашей ролью (например: Программист, Дизайнер)',
        'vacation_start': 'Неверный формат ввода, ожидаю текстовое сообщение с датой начала отпуска в формате ДД-ММ-ГГГГ, ДД.ММ.ГГГГ или ДД ММ ГГГГ (например: 15-01-2025, 15.01.2025 или 15 01 2025)',
        'vacation_end': 'Неверный формат ввода, ожидаю текстовое сообщение с датой окончания отпуска в формате ДД-ММ-ГГГГ, ДД.ММ.ГГГГ или ДД ММ ГГГГ (например: 25-01-2025, 25.01.2025 или 25 01 2025)',
        'yesterday': 'Неверный формат ввода, ожидаю текстовое или голосовое сообщение с описанием того, что вы сделали вчера',
        'today': 'Неверный формат ввода, ожидаю текстовое или голосовое сообщение с описанием того, что планируете сделать сегодня',
        'problems': 'Неверный формат ввода, ожидаю текстовое или голосовое сообщение с описанием проблем или слово "нет"'
    }
    return context_messages.get(context, 'Неверный формат ввода, ожидаю текстовое или голосовое сообщение')


def should_skip_retry(error: Exception) -> bool:
    """
    Возвращает True, если ошибка указывает на то, что ретрай бесполезен.
    Проверяет специальные исключения aiogram и тексты ошибок Telegram API.
    """
    # Проверяем специальные исключения aiogram
    if isinstance(error, TelegramNotFound):
        return True
    
    if isinstance(error, TelegramForbiddenError):
        return True
    
    if isinstance(error, TelegramAPIError):
        error_str = str(error).lower()
        skip_phrases = [
            'chat not found',
            'bot was blocked',
            'user is deactivated',
        ]
        return any(phrase in error_str for phrase in skip_phrases)
    
    return False


async def _send_message_with_retry(chat_id, text, max_retries=3, base_delay=2, max_delay=300, is_report=False, **kwargs):
    from bot.core.bot_instance import bot
    """Отправка сообщения с retry логикой
        is_report: Если True, используются длинные задержки для отчётов (до 5 минут)
                   Если False, короткие задержки для обычных сообщений (до 10 секунд)
    """
    # Для отчётов используем более длинные задержки
    if is_report:
        max_delay = 300  # 5 минут
    else:
        max_delay = min(max_delay, 10)  # Для обычных сообщений максимум 10 секунд
    
    for attempt in range(max_retries + 1):
        try:
            # Диагностика: логируем ключевые параметры отправки
            if attempt == 0:
                mtid = kwargs.get('message_thread_id', None)
                if mtid is not None:
                    logging.info(f"Отправка сообщения в чат {chat_id} с message_thread_id={mtid}")
            message = await bot.send_message(chat_id, text, **kwargs)
            if attempt > 0:
                logging.info(f"Сообщение успешно отправлено пользователю {chat_id} с попытки {attempt + 1}")
            return message
        except (TelegramNetworkError, TelegramServerError, ConnectionError, TimeoutError, TelegramAPIError) as e:
            if should_skip_retry(e):
                logging.warning(f"Пропуск ретраев для пользователя {chat_id}: {e}")
                return None
            
            if attempt < max_retries:
                if is_report:
                    # Для отчётов: Попытка 1: ~2 секунды, попытка 2: ~10 секунд, попытка 3: ~30 секунд, до 5 минут
                    delay_multipliers = [1, 5, 15, 30, 60]
                else:
                    # Для обычных сообщений: Попытка 1: ~2 секунды, попытка 2: ~3 секунды, попытка 3: ~5 секунд, до 10 секунд
                    delay_multipliers = [1, 1.5, 2.5, 4, 5]
                
                multiplier = delay_multipliers[attempt] if attempt < len(delay_multipliers) else (delay_multipliers[-1] if is_report else 150)
                delay = min(base_delay * multiplier + random.uniform(0, 1), max_delay)
                logging.warning(f"Попытка {attempt + 1} не удалась для пользователя {chat_id}: {e}. Повторная попытка через {delay:.2f} секунд.")
                await asyncio.sleep(delay)
            else:
                logging.error(f"Все {max_retries + 1} попыток отправки сообщения пользователю {chat_id} исчерпаны: {e}")
        except Exception as e:
            logging.error(f"Неожиданная ошибка при отправке сообщения пользователю {chat_id}: {e}")
            return None
    return None


async def send_message_with_retry(chat_id, text, max_retries=3, base_delay=2, max_delay=300, remove_keyboard=True, is_report=False, **kwargs):
    """Отправка сообщения с retry логикой и экспоненциальным backoff"""
    # Проверяем, есть ли инлайн клавиатура в kwargs
    has_inline_keyboard = 'reply_markup' in kwargs and hasattr(kwargs['reply_markup'], 'inline_keyboard')
    
    if remove_keyboard and not has_inline_keyboard:
        # Удаляем только обычную клавиатуру, если нет инлайн клавиатуры
        kwargs['reply_markup'] = ReplyKeyboardRemove()
    if chat_id and str(chat_id).startswith('-'):
        logging.warning(f"Попытка отправить сообщение в групповой чат {chat_id}")
    return await _send_message_with_retry(chat_id, text, max_retries, base_delay, max_delay, is_report, **kwargs)


async def send_photo_with_retry(chat_id, photo_bytes: bytes, filename: str = "image.png", caption: str | None = None,
                                parse_mode: str | None = 'HTML', message_thread_id: int | None = None,
                                max_retries: int = 5, base_delay: float = 2.0, max_delay: float = 300.0,
                                is_report: bool = True):
    """Отправка фото с retry логикой (используется для отчётов).
    - is_report=True включает длинные бэкоффы до 5 минут.
    - message_thread_id указывает топик супергруппы.
    """
    from bot.core.bot_instance import bot
    import random
    if not photo_bytes:
        logging.error("send_photo_with_retry: пустой буфер фото")
        return None
    try:
        file = BufferedInputFile(photo_bytes, filename=filename)
    except Exception as e:
        logging.error(f"send_photo_with_retry: ошибка создания BufferedInputFile: {e}")
        return None

    # Параметры задержек
    if is_report:
        max_delay = 300
        delay_multipliers = [1, 5, 15, 30, 60]
    else:
        max_delay = min(max_delay, 10)
        delay_multipliers = [1, 1.5, 2.5, 4, 5]

    for attempt in range(max_retries + 1):
        try:
            kwargs = {}
            if caption is not None:
                kwargs['caption'] = caption
            if parse_mode:
                kwargs['parse_mode'] = parse_mode
            if message_thread_id is not None:
                kwargs['message_thread_id'] = message_thread_id
            msg = await bot.send_photo(chat_id, file, **kwargs)
            if attempt > 0:
                logging.info(f"Фото успешно отправлено в {chat_id} с попытки {attempt + 1}")
            return msg
        except (TelegramNetworkError, TelegramServerError, ConnectionError, TimeoutError, TelegramAPIError) as e:
            if should_skip_retry(e):
                logging.warning(f"Пропуск ретраев при отправке фото пользователю {chat_id}: {e}")
                return None
            
            if attempt < max_retries:
                multiplier = delay_multipliers[attempt] if attempt < len(delay_multipliers) else delay_multipliers[-1]
                delay = min(base_delay * multiplier + random.uniform(0, 1), max_delay)
                logging.warning(f"Не удалось отправить фото (попытка {attempt + 1}) в {chat_id}: {e}. Повтор через {delay:.2f}с")
                await asyncio.sleep(delay)
            else:
                logging.error(f"Все {max_retries + 1} попыток отправки фото в {chat_id} исчерпаны: {e}")
        except Exception as e:
            logging.error(f"send_photo_with_retry: неожиданная ошибка: {e}")
            return None
    return None


async def send_or_edit_message(message_or_callback, text, remove_keyboard=True, **kwargs):
    from bot.core.bot_instance import bot
    """Универсальная функция: пытается редактировать сообщение, иначе отправляет новое.

    Логика:
    - Если пришёл CallbackQuery и есть исходное сообщение бота — пробуем edit_message_text.
    - Если пришёл Message — пробуем edit_message_text.
    - В остальных случаях — отправляем новое сообщение.
    - При ошибке редактирования — fallback к отправке нового сообщения.
    """
    # Проверяем, есть ли инлайн клавиатура в kwargs
    has_inline_keyboard = 'reply_markup' in kwargs and hasattr(kwargs['reply_markup'], 'inline_keyboard')

    if remove_keyboard and not has_inline_keyboard:
        # Удаляем только обычную клавиатуру, если нет инлайн клавиатуры
        kwargs['reply_markup'] = ReplyKeyboardRemove()

    try:
        # 1) Попытка редактирования при CallbackQuery
        if hasattr(message_or_callback, 'message') and hasattr(message_or_callback.message, 'chat'):
            chat_id = message_or_callback.message.chat.id
            msg = message_or_callback.message
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg.message_id,
                    text=text,
                    **kwargs
                )
                return
            except Exception as e:
                pass

        # 2) Попытка редактирования при Message, отправленном ботом
        if hasattr(message_or_callback, 'chat') and hasattr(message_or_callback, 'message_id'):
            chat_id = message_or_callback.chat.id
            msg_id = getattr(message_or_callback, 'message_id', None)
            if msg_id:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=text,
                        **kwargs
                    )
                    return
                except Exception as e:
                    pass

        # 3) Отправка нового сообщения (fallback или если редактировать нечего)
        chat_id = None
        topic_id = None
        # Для CallbackQuery берём chat_id из message
        if hasattr(message_or_callback, 'message') and hasattr(message_or_callback.message, 'chat'):
            chat_id = message_or_callback.message.chat.id
            if hasattr(message_or_callback.message, 'message_thread_id') and message_or_callback.message.message_thread_id:
                topic_id = message_or_callback.message.message_thread_id
        elif hasattr(message_or_callback, 'chat'):
            chat_id = message_or_callback.chat.id
            if hasattr(message_or_callback, 'message_thread_id') and message_or_callback.message_thread_id:
                topic_id = message_or_callback.message_thread_id
        elif hasattr(message_or_callback, 'from_user'):
            chat_id = message_or_callback.from_user.id

        if chat_id:
            # Прокидываем thread_id если есть
            if topic_id and 'message_thread_id' not in kwargs:
                try:
                    kwargs['message_thread_id'] = int(topic_id)
                except Exception:
                    pass
            await send_message_with_retry(chat_id, text, **kwargs)
        else:
            logging.error("Не удалось определить chat_id для отправки сообщения")
    except Exception as e:
        logging.error(f"Ошибка при отправке/редактировании сообщения: {e}")


def is_on_vacation(vacation_start, vacation_end):
    """Проверка, находится ли сотрудник в отпуске"""
    if not vacation_start or not vacation_end:
        return False
    today = get_current_time().date()
    try:
        start = datetime.strptime(vacation_start, '%d-%m-%Y').date()
        end = datetime.strptime(vacation_end, '%d-%m-%Y').date()
        return start <= today <= end
    except Exception:
        return False


def validate_and_format_name(name: str) -> tuple[bool, str]:
    """
    Валидирует и форматирует имя согласно шаблону "Иван И." или "Иван И" или "Иван Ив" или "Иван Ив."
    
    Args:
        name: Строка с именем
        
    Returns:
        tuple[bool, str]: (валидно ли имя, отформатированное имя или сообщение об ошибке)
    """
    if not name or not isinstance(name, str):
        return False, "Имя не может быть пустым"
    
    # Убираем лишние пробелы
    name = name.strip()
    
    # Разбиваем на части
    parts = name.split()
    
    # Проверяем, что есть минимум 2 части (имя и инициал фамилии)
    if len(parts) !=2:
        return False, "Имя должно содержать имя и инициал фамилии (например: Иван И.)"
    
    # Берем имя (первая часть)
    first_name = parts[0]
    
    # Берем инициал фамилии (вторая часть)
    surname_initial = parts[1]
    
    # Проверяем, что имя состоит только из букв
    for char in first_name:
        if not char.isalpha():
            return False, "Имя должно содержать только буквы (например: Иван И.)"
    
    # Проверяем длину инициала фамилии (максимум 2 буквы + точка)
    letters_only = ''.join(char for char in surname_initial if char.isalpha())
    if len(letters_only) > 2:
        return False, "Инициал фамилии должен содержать максимум 2 буквы (например: Л. или Ла.)"
    
    # Проверяем, что инициал состоит только из букв (и возможно точки)
    for char in surname_initial:
        if char != '.' and not char.isalpha():
            return False, "Инициал фамилии должен содержать только буквы (например: Л. или Ла.)"
    
    # Форматируем: первая буква имени заглавная, остальные строчные
    formatted_first_name = first_name[0].upper() + first_name[1:].lower()
    
    # Форматируем инициал фамилии: первая буква заглавная, остальные строчные
    formatted_surname_initial = ""
    letter_count = 0
    for char in surname_initial:
        if char.isalpha():
            if letter_count == 0:
                formatted_surname_initial += char.upper()  # Первая буква заглавная
            else:
                formatted_surname_initial += char.lower()  # Остальные буквы строчные
            letter_count += 1
    
    # Добавляем точку, если её нет
    if not surname_initial.endswith("."):
        formatted_surname_initial += "."
    
    # Собираем результат
    formatted_name = f"{formatted_first_name} {formatted_surname_initial}"
    
    return True, formatted_name


def is_gitverse_board_link(link: str) -> bool:
    """Проверяет, что ссылка указывает на GitVerse tasktracker."""
    try:
        parsed = urlparse(link)
        if 'gitverse.ru' not in parsed.netloc:
            return False
        return '/tasktracker' in parsed.path
    except Exception:
        return False


def normalize_gitverse_board_link(link: str) -> str:
    """Приводит GitVerse ссылку на tasktracker к виду https://gitverse.ru/.../tasktracker?view=board"""
    try:
        parsed = urlparse(link)
        if 'gitverse.ru' not in parsed.netloc or '/tasktracker' not in parsed.path:
            return link
        # Оставляем только view=board
        query = {'view': 'board'}
        normalized = parsed._replace(query=urlencode(query))
        return urlunparse(normalized)
    except Exception:
        return link


def build_gitverse_personal_board_link(base_board_link: str, nickname: str) -> str:
    """Формирует персональную ссылку GitVerse с фильтром по assignedTo=["nickname"]."""
    try:
        # Нормализуем базовую ссылку
        base = normalize_gitverse_board_link(base_board_link)
        parsed = urlparse(base)
        # Разбираем существующие параметры (оставляем view=board)
        params = dict(parse_qsl(parsed.query))
        params['view'] = params.get('view', 'board')
        params['page'] = '1'
        # assignedTo как JSON-строка
        params['assignedTo'] = json.dumps([nickname])
        new_query = urlencode(params)
        new_parsed = parsed._replace(query=new_query)
        return urlunparse(new_parsed)
    except Exception:
        # Фоллбек — вернуть исходную ссылку
        return base_board_link

def validate_team_time_settings(morning_time: str, evening_time: str, report_time: str) -> tuple[bool, str]:
    """
    Валидация настроек времени команды
    
    Args:
        morning_time: Время утреннего опроса в формате HH:MM
        evening_time: Время вечернего опроса в формате HH:MM
        report_time: Время отправки отчетов в формате HH:MM
        
    Returns:
        tuple[bool, str]: (is_valid, error_message)
    """
    try:
        # Извлекаем час и минуту из строк времени
        morning_hour, morning_minute = map(int, morning_time.split(':'))
        evening_hour, evening_minute = map(int, evening_time.split(':'))
        report_hour, report_minute = map(int, report_time.split(':'))
        
        # Конвертируем в минуты для сравнения
        morning_minutes = morning_hour * 60 + morning_minute
        evening_minutes = evening_hour * 60 + evening_minute
        report_minutes = report_hour * 60 + report_minute
        
        # Проверяем ограничения
        if report_minutes <= morning_minutes:
            return False, f"Время отправки отчетов ({report_time}) должно быть позже времени утреннего опроса ({morning_time})"
        
        if evening_minutes <= report_minutes:
            return False, f"Время вечернего опроса ({evening_time}) должно быть позже времени отправки отчетов ({report_time})"
        
        return True, ""
        
    except ValueError:
        return False, "Неверный формат времени. Используйте формат HH:MM" 
    
def get_access_error_message(action: str) -> str:
    """Возвращает сообщение об ошибке доступа для указанного действия"""
    return f"❌ У вас нет прав для {action}"