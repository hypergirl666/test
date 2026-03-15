import asyncio
import logging
from collections import deque
from typing import Optional, Dict, Any, Callable


class NotificationQueue:
    """
    FIFO очередь для рассылок по времени команды: опросники, отчёты (спринтовые, дневные).
    Обрабатывает задачи рассылки с ограниченным параллелизмом (не более n одновременно)
    и задержкой между запуском задач, чтобы снизить нагрузку на БД.
    Также обрабатывает отдельные сообщения с задержкой 1 секунда между пользователями.
    """

    def __init__(self, delay_between_tasks: float = 0.5, max_concurrent_tasks: int = 3):
        """
        Args:
            delay_between_tasks: Задержка в секундах между запуском задач рассылки (по умолчанию 0.5 сек)
            max_concurrent_tasks: Максимальное количество одновременно выполняемых задач (по умолчанию 3)
        """
        self._queue: deque = deque()
        self._processing: bool = False
        self._task: Optional[asyncio.Task] = None
        self._delay_between_tasks = delay_between_tasks
        self._max_concurrent_tasks = max_concurrent_tasks
        self._task_semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent_tasks)

    def add(self, chat_id: int = None, text: str = None, func: Callable = None, *args, **kwargs):
        """Добавить задачу или сообщение в очередь.
        
        Для сообщений:
            chat_id: ID чата/пользователя для отправки
            text: Текст сообщения
            **kwargs: Дополнительные параметры для send_message_with_retry
        
        Для задач рассылки:
            func: Асинхронная функция для выполнения (задача рассылки)
            *args: Позиционные аргументы для функции
            **kwargs: Именованные аргументы для функции
        """
        if func is not None:
            # Это задача рассылки
            task_args = kwargs.pop('task_args', args)
            task_kwargs = kwargs.pop('task_kwargs', {})

            task_data = {
                'type': 'task',
                'func': func,
                'args': task_args if isinstance(task_args, tuple) else tuple(task_args),
                'kwargs': task_kwargs
            }
            self._queue.append(task_data)
            
            queue_size = len(self._queue)
            func_name = getattr(func, '__name__', str(func))
            logging.info(
                f"Добавлена задача рассылки в очередь: {func_name}, "
                f"args={task_args}, kwargs={task_kwargs}, размер очереди={queue_size}"
            )
        else:
            # Это сообщение
            notification = {
                'type': 'message',
                'chat_id': chat_id,
                'text': text,
                **kwargs
            }
            self._queue.append(notification)

            queue_size = len(self._queue)
            is_report = kwargs.get('is_report', False)
            msg_type = "отчёт" if is_report else "уведомление"
            logging.info(
                f"Добавлено в очередь: {msg_type} для chat_id={chat_id}, "
                f"размер очереди={queue_size}, длина текста={len(text)} символов"
            )

        # Запускаем обработку, если она ещё не запущена
        if not self._processing:
            self._start_processing()

    def _start_processing(self):
        """Запустить фоновую задачу обработки очереди."""
        if self._task is None or self._task.done():
            self._processing = True
            self._task = asyncio.create_task(self._process_queue())
            logging.debug(f"Запущена обработка очереди, размер очереди: {len(self._queue)}")

    async def _process_queue(self):
        """Обработка очереди: задачи рассылки и сообщения."""
        from bot.utils.utils import send_message_with_retry

        async def _send_notification(notification_data):
            """Отправка одного уведомления с логированием."""
            chat_id = notification_data['chat_id']
            text = notification_data['text']
            text_preview = text[:40] + ('...' if len(text) > 40 else '')

            try:
                await send_message_with_retry(
                    chat_id,
                    text,
                    **{k: v for k, v in notification_data.items() if k not in ['type', 'chat_id', 'text']}
                )
                logging.info(f"Отправлено: tg_id={chat_id}, текст='{text_preview}'")
            except Exception as e:
                logging.error(f"Ошибка при отправке уведомления пользователю {chat_id}: {e}")

        async def _execute_task(task_data):
            """Выполнение задачи рассылки с логированием и ограничением параллелизма."""
            func = task_data['func']
            args = task_data['args']
            kwargs = task_data['kwargs']
            func_name = getattr(func, '__name__', str(func))

            try:
                # Используем семафор для ограничения количества одновременно выполняемых задач
                async with self._task_semaphore:
                    logging.info(f"Начало выполнения задачи рассылки: {func_name}, args={args}, kwargs={kwargs}")
                    result = await func(*args, **kwargs)
                    logging.info(f"Задача рассылки выполнена успешно: {func_name}")
                    return result
            except Exception as e:
                logging.error(f"Ошибка при выполнении задачи рассылки {func_name}: {e}", exc_info=True)
                raise

        processed_count = 0
        while True:
            # Ждём появления элементов в очереди
            if not self._queue:
                await asyncio.sleep(0.1)
                continue

            item = self._queue.popleft()
            item_type = item.get('type', 'message')

            # Задержка перед обработкой (кроме первого элемента)
            if processed_count > 0:
                delay = self._delay_between_tasks if item_type == 'task' else 1.0
                await asyncio.sleep(delay)

            processed_count += 1

            if item_type == 'task':
                # Задача рассылки: запускаем в фоне с ограничением параллелизма через семафор
                # Семафор внутри _execute_task ограничивает до max_concurrent_tasks одновременно
                func_name = getattr(item['func'], '__name__', str(item['func']))
                logging.info(
                    f"Запуск задачи рассылки #{processed_count}: {func_name}, "
                    f"осталось в очереди: {len(self._queue)}"
                )
                asyncio.create_task(_execute_task(item))
            else:
                # Сообщение: запускаем отправку в фоне
                chat_id = item['chat_id']
                text_preview = item['text'][:40] + ('...' if len(item['text']) > 40 else '')
                logging.info(
                    f"Отправка сообщения #{processed_count}: tg_id={chat_id}, "
                    f"текст='{text_preview}', осталось в очереди: {len(self._queue)}"
                )
                asyncio.create_task(_send_notification(item))

        logging.info(f"Обработка очереди завершена, обработано элементов: {processed_count}")
        self._processing = False

    def clear(self):
        """Очистить очередь от неотправленных сообщений."""
        self._queue.clear()

    def size(self) -> int:
        """Получить текущий размер очереди."""
        return len(self._queue)


# Глобальный экземпляр очереди
_notification_queue: Optional[NotificationQueue] = None


def get_notification_queue() -> NotificationQueue:
    """Получить глобальный экземпляр очереди рассылок.
    
    Очередь обрабатывает:
    - Задачи рассылки по времени команды: опросники, отчёты (спринтовые, дневные)
    - Отдельные сообщения/уведомления
    
    Настройки через переменные окружения:
    - NOTIFICATION_QUEUE_DELAY_BETWEEN_TASKS: задержка между запуском задач (по умолчанию 0.5 сек)
    - NOTIFICATION_QUEUE_MAX_CONCURRENT_TASKS: максимум одновременно выполняемых задач (по умолчанию 3)
    """
    global _notification_queue
    if _notification_queue is None:
        import os
        delay_between_tasks = float(os.getenv("NOTIFICATION_QUEUE_DELAY_BETWEEN_TASKS", "0.5"))
        max_concurrent_tasks = int(os.getenv("NOTIFICATION_QUEUE_MAX_CONCURRENT_TASKS", "3"))
        _notification_queue = NotificationQueue(
            delay_between_tasks=delay_between_tasks,
            max_concurrent_tasks=max_concurrent_tasks
        )
    return _notification_queue

