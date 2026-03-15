import os
import logging
from typing import Dict, List, Optional
from langchain.prompts import PromptTemplate
from dotenv import load_dotenv
import asyncio
import aiohttp
import time
import random
from bot.utils.openrouter_key_manager import OpenRouterKeyManager, KeyType

# Загружаем переменные окружения
load_dotenv()

# event loop для вызовов из фонового потока
_MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None


def register_main_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _MAIN_LOOP
    _MAIN_LOOP = loop


def get_main_event_loop() -> Optional[asyncio.AbstractEventLoop]:
    return _MAIN_LOOP


class LLMProcessor:
    """Класс для обработки LLM запросов через OpenRouter API"""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._timeout = aiohttp.ClientTimeout(total=180)
        self._key_manager = OpenRouterKeyManager()
        self._temperature = 0.7
        self._max_tokens = 3500
        if self._key_manager.has_available_keys():
            logging.info("LLM через OpenRouter готов к работе")
        else:
            logging.warning("Не найдено ни одного ключа OpenRouter. LLM функциональность недоступна.")

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout, trust_env=True)
        return self._session

    async def _call_llm_api(self, prompt: str, *, usage_event: str, team_id: int | None,
                            employee_tg_id: int | None) -> str:
        session = await self._ensure_session()
        url = "https://openrouter.ai/api/v1/chat/completions"

        start_ts = time.perf_counter()
        data = None
        
        # === Phase 1: Try Free Keys ===
        attempts = 0
        while True:
            # Получаем доступный бесплатный ключ
            key_model_pair = self._key_manager.get_free_key_and_model()
            
            # Если нет бесплатных ключей, выходим из цикла
            if not key_model_pair:
                logging.info("Доступные бесплатные ключи закончились")
                break
                
            attempts += 1
            api_key, model = key_model_pair
            
            if attempts == 1:
                logging.info(f"Запуск LLM (Free): event={usage_event}, model={model}")

            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self._temperature,
                "max_tokens": self._max_tokens,
            }

            try:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        # Проверяем, что ответ не пустой
                        temp_data = await resp.json()
                        message = temp_data.get("choices", [{}])[0].get("message", {})
                        answer_text = message.get("content", "")

                        # Если content пустой, проверяем reasoning
                        if not answer_text or not answer_text.strip():
                            answer_text = message.get("reasoning", "")

                        # Если ответ пустой, считаем это ошибкой и продолжаем retry
                        if not answer_text or not answer_text.strip():
                            logging.warning(
                                f"LLM API вернул пустой ответ на ключе {api_key[:10]}... (попытка {attempts})")
                            self._key_manager.mark_result(api_key, success=False)
                            continue

                        # Ответ не пустой - успех
                        data = temp_data
                        self._key_manager.mark_result(api_key, success=True)
                        break

                    elif resp.status == 403:
                        logging.warning(f"Ошибка 403 на ключе {api_key[:10]}...")
                        self._key_manager.mark_result(api_key, success=False, is_403=True)
                        
                    else:
                        logging.warning(f"Ошибка {resp.status} на ключе {api_key[:10]}...")
                        self._key_manager.mark_result(api_key, success=False)
                        
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logging.warning(f"Сетевая ошибка на ключе {api_key[:10]}...: {e}")
                self._key_manager.mark_result(api_key, success=False)
                continue

        # === Phase 2: Paid Key Fallback ===
        if data is None:
            paid_key_pair = self._key_manager.get_paid_key_and_model()
            
            if paid_key_pair:
                api_key, model = paid_key_pair
                logging.info(f"FALLBACK: Используем платный ключ {api_key[:10]}... модель {model}")
                
                # 4 попытки для платного ключа
                for i in range(4):
                    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                    payload = {
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": self._temperature,
                        "max_tokens": self._max_tokens,
                    }
                    
                    try:
                        async with session.post(url, json=payload, headers=headers) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                logging.info("Успешный запрос через платный ключ")
                                break
                            else:
                                logging.warning(f"Ошибка {resp.status} на платном ключе (попытка {i+1}/4)")
                                # Первые две попытки: задержка как раньше (1 + i секунд)
                                # Следующие две: больший таймаут (60 и 120 секунд)
                                if i < 2:
                                    delay = 1 + i
                                else:
                                    delay = 60 * (i - 1)  # 60 для i=2, 120 для i=3
                                await asyncio.sleep(delay)
                    except Exception as e:
                        logging.warning(f"Ошибка сети на платном ключе (попытка {i+1}/4): {e}")
                        # Первые две попытки: задержка как раньше (1 + i секунд)
                        # Следующие две: больший таймаут (60 и 120 секунд)
                        if i < 2:
                            delay = 1 + i
                        else:
                            delay = 60 * (i - 1)  # 60 для i=2, 120 для i=3
                        await asyncio.sleep(delay)
            else:
                logging.error("Платный ключ не найден!")

        if data is None:
            raise RuntimeError("Не удалось получить ответ от LLM")

        duration_ms = int((time.perf_counter() - start_ts) * 1000)
        usage = data.get("usage", {}) or {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))

        # Извлекаем ответ из message
        message = data.get("choices", [{}])[0].get("message", {})
        answer_text = message.get("content", "")

        # Если content пустой, проверяем reasoning
        if not answer_text or not answer_text.strip():
            reasoning_text = message.get("reasoning", "")
            if reasoning_text and reasoning_text.strip():
                answer_text = reasoning_text
                logging.info("LLM API вернул ответ в поле 'reasoning' вместо 'content'")

        # Если ответ всё ещё пустой, выбрасываем ошибку
        if not answer_text or not answer_text.strip():
            logging.error(f"LLM API вернул пустой content. Message: {message}")
            raise RuntimeError("LLM API вернул пустой текст ответа")
        try:
            from bot.core.database import db_add_llm_token_usage
            await db_add_llm_token_usage(
                event=usage_event, team_id=team_id, employee_tg_id=employee_tg_id,
                provider="openrouter", model=model, model_version=None,
                input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens,
                duration_ms=duration_ms, attempts=attempts, response_text=answer_text,
            )
        except Exception as e:
            logging.error(f"Ошибка записи метрик LLM: {e}")

        return answer_text

    async def call_llm_with_system_prompt(
            self,
            system_prompt: str,
            user_prompt: str,
            *,
            usage_event: str,
            team_id: int | None = None,
            employee_tg_id: int | None = None,
            temperature: float | None = None,
            max_tokens: int | None = None
    ) -> str:
        # Для простоты используем ту же логику _call_llm_api, просто добавив системный промпт в начало
        full_prompt = f"System: {system_prompt}\n\nUser: {user_prompt}"
        return await self._call_llm_api(full_prompt, usage_event=usage_event, team_id=team_id, employee_tg_id=employee_tg_id)

    def is_available(self) -> bool:
        return self._key_manager.has_available_keys()

    def daily_summarizator(self, report: str, team_id: int | None = None, questions: List[Dict] = None) -> str:
        """Функция делает саммари для PO, на основе daily отчета разработчиков"""
        if not self.is_available():
            return "❌ LLM функциональность недоступна. Проверьте настройки API ключей."

        try:
            # Формируем контекст вопросов, если они предоставлены
            context = ""
            if questions:
                context = "Контекст вопросов:\n"
                for q in questions:
                    field = q.get('field', f"Question {q.get('id', '')}")
                    text = q.get('text', 'Неизвестный вопрос')
                    context += f"- Поле: {field}, Вопрос: {text}\n"
                context += "\n"

            prompt_template = PromptTemplate(
                input_variables=["context", "report"],
                template='''
### Role
Ты - Project Manager в крупной IT компании. Твой опыт работы на данной позиции более 10 лет, ты обладаешь наиболее авторитетной экспертизой в оценке отчетов разработчиков.

### Task
Твоя задача - проанализировать отчёт, который содержит ответы разработчиков на заданные вопросы, и сделать краткое саммари. Ответы не обязательно связаны с задачами, а отражают состояние, прогресс или трудности. Учитывай контекст вопросов для правильной интерпретации ответов.

### Input 
{context}
Отчет о работе: {report}

### Output 
Проанализируй отчёт и создай саммари в следующем формате:
Не используй Markdown, при написании саммари
Саммари должны быть написаны на русском языке
Подробное саммари с ключевыми моментами, трудностями и прогрессом.
Если отчет пустой или не содержит информации, сообщи "Нет отчетов за указанный период."
Если отчет содержит негативные моменты, предложи рекомендации по их решению.
Если отчет положительный, подчеркни успехи и прогресс.
Если отчет нейтральный, отметь это и предложи пути для улучшения.
Если отчет содержит противоречивые или неполные данные, укажи на это и предложи уточняющие вопросы.
Используй простой и понятный язык, избегай технического жаргона.
Если отчет содержит много информации, постарайся сделать саммари емким и информативным, не теряя важные детали.
Если отчет содержит мало информации, постарайся извлечь максимум пользы из доступных данных
Если отчет содержит личные данные, избегай их упоминания в саммари
Если отчет содержит вопросы, постарайся ответить на них в саммари
Если отчет содержит просьбы о помощи, отметь это и предложи пути решения
Если отчет содержит предложения, отметь это и предложи пути их реализации
Учитывай колличестов вопросов в {context} при создании саммари и ответы на них {report}.
И самое главное - будь объективен и беспристрастен в своем анализе.
В саммари для каждого сотруднка выдели не более 5-7 строк, с важными моментами в его работе и на что обратить внимание
'''
            )

            prompt = prompt_template.format(context=context, report=report)
            # Используем async версию через event loop
            loop = get_main_event_loop()
            if loop and loop.is_running():
                return asyncio.run_coroutine_threadsafe(
                    self._call_llm_api(prompt, usage_event="daily_summary", team_id=team_id, employee_tg_id=None),
                    loop
                ).result()
            else:
                # Если нет event loop, создаем новый
                return asyncio.run(
                    self._call_llm_api(prompt, usage_event="daily_summary", team_id=team_id, employee_tg_id=None))

        except Exception as e:
            logging.error(f"Ошибка при создании саммари: {e}", exc_info=True)
            return None

    async def daily_summarizator_async(self, report: str, team_id: int | None = None,
                                       questions: List[Dict] = None) -> str | None:
        if not self.is_available():
            return None  # Возвращаем None вместо строки с ошибкой
        try:
            context = ""
            if questions:
                context = "Контекст вопросов:\n"
                for q in questions:
                    field = q.get('field', f"Question {q.get('id', '')}")
                    text = q.get('text', 'Неизвестный вопрос')
                    context += f"- Поле: {field}, Вопрос: {text}\n"
                context += "\n"
            prompt_template = PromptTemplate(
                input_variables=["context", "report"],
                template='''
Ты — аналитик статусов. На вход придёт список индивидуальных отчётов в формате:
Имя Ф. (Роль):
Yesterday: ...
Today: ...
Problems: ...
Уточняющий вопрос: "..."
Ответ на уточняющий вопрос: ...
[✅ Отправил отчёт | ❌ Нет отчёта]

Сформируй краткую, управленчески полезную сводку. Строго соблюдай формат и правила ниже.
Формат вывода:
✅ Успехи
- Чего добилисьИмя Ф. (Роль): 1 строка по ключевому достижению.
...

⛔ Блокеры
- Имя Ф. (Роль): что именно блокирует работу и что нужно для разблокировки.
- [Приоритет: High|Med|Low] Имя Ф. (Роль): короткое описание проблемы (факт)
Если схожие проблемы у нескольких участников - не дублируй, пиши имена через запятую.

🟡 Риски
- Имя Ф. (Роль): потенциальная проблема/ограничение, не заявленное как проблема (например, частые 429 при вызовах API). Коротко по сути.

📌 Рекомендации
- конкретное действие и краткий ожидаемый результат.
- Краткие рекомендации по блокерам для руководителя.
- Группируй одинаковые рекомендации (не дублируй по людям).
- Рекомаендации для руководителя


Правила:
- Краткость.
- Не переписывай весь отчёт; 1 строка на человека в каждом релевантном разделе.
- Сохраняй имена и роли как в исходнике.
- Классификация:
  - Блокеры: всё, что явно мешает продолжать работу без внешнего действия.
  - Риски: скрытые/подразумеваемые ограничения, зависимые факторы, отсутствие согласований.
- Объединяй повторы: похожие темы сводить в один пункт с перечислением имён.
- Тон нейтральный, без оценочных суждений. Только проверяемые факты и чёткие действия.
- Язык: русский. Эмодзи как в шаблоне.
### Input 
{context}
Отчет о работе: {report}

'''
            )
            prompt = prompt_template.format(context=context, report=report)
            result = await self._call_llm_api(prompt, usage_event="daily_summary", team_id=team_id, employee_tg_id=None)
            if not result or not result.strip():
                logging.error(f"LLM вернул пустой результат для саммари. Длина отчёта: {len(report)} символов")
                return None
            return result
        except RuntimeError as e:
            logging.error(f"Ошибка при создании саммари (async, RuntimeError): {e}")
            return None
        except Exception as e:
            logging.error(f"Ошибка при создании саммари (async): {e}", exc_info=True)
            return None  # Возвращаем None вместо строки с ошибкой

    async def sprint_summarizator_async(
            self,
            team_name: str,
            period_text: str,
            plans_text: str,
            reports_text: str,
            expected_reports_count: int,
            team_id: int | None = None
    ) -> str | None:
        """Создаёт итоговый отчёт по спринту с сравнением планов и факта."""
        if not self.is_available():
            return None
        try:
            prompt_template = PromptTemplate(
                input_variables=["team_name", "period", "plans", "reports", "expected_reports_count"],
                template="""
Ты — agile-коуч. Тебе предоставлены планы на спринт и отчёты за несколько рабочих дней.

Команда: {team_name}
Период: {period}
Ожидаемое количество отчётов от каждого сотрудника за этот период: {expected_reports_count}

### Планы на спринт
{plans}

### Отчёты за спринт
{reports}

Оцени кто сколько задач выполнил (берём по каждому дню - если в следующем дне уже новые задачи, значит задача была выполнена успешно).
Чего добилась команда за этот срок, с какими трудностями столкнулась и советы.
Отчёт должен содержать:
1. 📈 Оценка выполненных задач
   Разработчик - количество выполненных задач, Статус. Возможные статусы: "✅", "❌ (низкая активность)", "⚠️ (n дней без отчёта)".
   Учитывай, что всего ожидалось {expected_reports_count} отчётов. Если отчётов меньше, чем ожидалось, укажи это.

2. 🏆 Достижения команды
   Основные успехи и результаты спринта.

3. ⚠️ Основные трудности
   Проблемы, с которыми столкнулась команда.

4. 💡 Рекомендации для менеджера
   Что делать для улучшения процесса и результатов.

Пиши кратко и структурированно, без таблиц, только текст. Используй эмодзи как в шаблоне.
"""
            )
            prompt = prompt_template.format(
                team_name=team_name,
                period=period_text,
                plans=plans_text or "нет планов",
                reports=reports_text or "нет отчётов",
                expected_reports_count=expected_reports_count
            )
            return await self._call_llm_api(
                prompt,
                usage_event="sprint_summary",
                team_id=team_id,
                employee_tg_id=None
            )
        except Exception as e:
            logging.error(f"Ошибка при создании спринтового отчёта: {e}")
            return None

    def task_assessment(self, task: str, result: str, team_id: int | None = None) -> str:
        """Функция анализирует, насколько отчет разработчика соответствует поставленной задаче"""
        if not self.is_available():
            return "❌ LLM функциональность недоступна. Проверьте настройки API ключей."

        try:
            prompt_template = PromptTemplate(
                input_variables=["task", "result"],
                template="""
### Role
Ты - TeamLead в крупной IT компании. Твой опыт работы на данной позиции более 10 лет, ты обладаешь наиболее авторитетной экспертизой в оценке задач, выполняемых разработчиками.

### Task
Твоя задача - оценивать, насколько отчет разработчика о проделанной работе над определенной задачей соответствует тому, что на самом деле нужно было сделать. Оценить следует по шкале от 1 до 5.

### Input
Тебе на вход будут подаваться две сущности:
1. **task** - задача, которую разработчик должен был выполнить. Задача может быть описана как подробно, так и кратко, может иметь формальное название или нет. Исходя из этого текста, ты должен понять, что разработчик должен был сделать, его ТЗ.
2. **result** - отчет о проделанной работе, написанный разработчиком. В нем содержится описание проделанной работы над определенной задачей, которое ты должен сравнить с поставленной задачей.

### Instructions
Оцени отчет на основе того, насколько описанная работа выполняет требования задачи, по следующей шкале:

- **5**: Отчет четко указывает, что задача полностью выполнена.  
  *Пример:*  
  - Задача: Разработать daily бота для отчетов.  
  - Отчет: Я спроектировал бота, протестировал его локально, настроил расписания ping'ов разработчиков, запушил в гит, запустил на сервере, и бот сейчас активен и работает.  
  - Оценка: 5  

- **4**: Отчет указывает, что большая часть задачи выполнена, но не вся.  
  *Пример:*  
  - Задача: Поднять базу данных и запустить её на сервере.  
  - Отчет: Я спроектировал БД, построил схему, поднял её локально, жду согласования коллег, чтобы запушить на сервер.  
  - Оценка: 4  

- **3**: Отчет указывает на некоторый прогресс по задаче, но значительные части еще не завершены.  
  *Пример:*  
  - Задача: Реализовать новую функцию с frontend и backend компонентами.  
  - Отчет: Я начал работать над frontend, но еще не приступил к backend.  
  - Оценка: 3  

- **2**: Отчет указывает на минимальный прогресс по задаче.  
  *Пример:*  
  - Задача: Исправить 10 ошибок в системе.  
  - Отчет: Я посмотрел на одну ошибку, но не смог её исправить.  
  - Оценка: 2  

- **1**: Отчет указывает на отсутствие прогресса по задаче или является полностью нерелевантным.  
  *Пример:*  
  - Задача: Обновить документацию для API.  
  - Отчет: Я был занят другими задачами и не добрался до этого.  
  - Оценка: 1  

### Output
Укажи только числовую оценку от 1 до 5.

###Задача: {task}
###Отчет: {result}
###Твоя оценка: 
"""
            )
            prompt = prompt_template.format(task=task, result=result)
            # Используем async версию через event loop
            loop = get_main_event_loop()
            if loop and loop.is_running():
                return asyncio.run_coroutine_threadsafe(
                    self._call_llm_api(prompt, usage_event="task_assessment", team_id=team_id, employee_tg_id=None),
                    loop
                ).result()
            else:
                # Если нет event loop, создаем новый
                return asyncio.run(
                    self._call_llm_api(prompt, usage_event="task_assessment", team_id=team_id, employee_tg_id=None))

        except Exception as e:
            logging.error(f"Ошибка при оценке задачи: {e}")
            return f"❌ Ошибка при оценке задачи: {str(e)}"

    async def task_assessment_async(self, task: str, result: str, team_id: int | None = None) -> str:
        if not self.is_available():
            return "❌ LLM функциональность недоступна. Проверьте настройки API ключей."
        try:
            prompt_template = PromptTemplate(
                input_variables=["task", "result"],
                template="""
                ### Role
                Ты - TeamLead в крупной IT компании. Твой опыт работы на данной позиции более 10 лет, ты обладаешь наиболее авторитетной экспертизой в оценке задач, выполняемых разработчиками.

                ### Task
                Твоя задача - оценивать, насколько отчет разработчика о проделанной работе над определенной задачей соответствует тому, что на самом деле нужно было сделать. Оценить следует по шкале от 1 до 5.

                ### Input
                Тебе на вход будут подаваться две сущности:
                1. **task** - задача, которую разработчик должен был выполнить. Задача может быть описана как подробно, так и кратко, может иметь формальное название или нет. Исходя из этого текста, ты должен понять, что разработчик должен был сделать, его ТЗ.
                2. **result** - отчет о проделанной работе, написанный разработчиком. В нем содержится описание проделанной работы над определенной задачей, которое ты должен сравнить с поставленной задачей.

                ### Instructions
                Оцени отчет на основе того, насколько описанная работа выполняет требования задачи, по следующей шкале:

                - **5**: Отчет четко указывает, что задача полностью выполнена.  
                  *Пример:*  
                  - Задача: Разработать daily бота для отчетов.  
                  - Отчет: Я спроектировал бота, протестировал его локально, настроил расписания ping'ов разработчиков, запушил в гит, запустил на сервере, и бот сейчас активен и работает.  
                  - Оценка: 5  

                - **4**: Отчет указывает, что большая часть задачи выполнена, но не вся.  
                  *Пример:*  
                  - Задача: Поднять базу данных и запустить её на сервере.  
                  - Отчет: Я спроектировал БД, построил схему, поднял её локально, жду согласования коллег, чтобы запушить на сервер.  
                  - Оценка: 4  

                - **3**: Отчет указывает на некоторый прогресс по задаче, но значительные части еще не завершены.  
                  *Пример:*  
                  - Задача: Реализовать новую функцию с frontend и backend компонентами.  
                  - Отчет: Я начал работать над frontend, но еще не приступил к backend.  
                  - Оценка: 3  

                - **2**: Отчет указывает на минимальный прогресс по задаче.  
                  *Пример:*  
                  - Задача: Исправить 10 ошибок в системе.  
                  - Отчет: Я посмотрел на одну ошибку, но не смог её исправить.  
                  - Оценка: 2  

                - **1**: Отчет указывает на отсутствие прогресса по задаче или является полностью нерелевантным.  
                  *Пример:*  
                  - Задача: Обновить документацию для API.  
                  - Отчет: Я был занят другими задачами и не добрался до этого.  
                  - Оценка: 1  

                ### Output
                Укажи только числовую оценку от 1 до 5.

                ###Задача: {task}
                ###Отчет: {result}
                ###Твоя оценка: 
                """
            )
            prompt = prompt_template.format(task=task, result=result)
            return await self._call_llm_api(prompt, usage_event="task_assessment", team_id=team_id, employee_tg_id=None)
        except Exception as e:
            logging.error(f"Ошибка при оценке задачи (async): {e}")
            return f"❌ Ошибка при оценке задачи: {str(e)}"

    def generate_clarifying_questions(
            self,
            name: str,
            role: str,
            answers: Dict,
            questions: List[Dict],
            daily_time: str,
            team_id: Optional[int] = None,
            employee_tg_id: Optional[int] = None,
    ) -> str:
        """Генерация одного уточняющего вопроса по ежедневному отчёту участника."""

        if not self.is_available():
            return "❌ LLM функциональность недоступна. Проверьте настройки API ключей."

        try:
            # Формируем контекст для LLM на основе вопросов и ответов
            context = []

            answers_dict = {item['field']: item['answer'] for item in answers} if isinstance(answers, list) else answers
            for question in questions:
                field = question.get("field")
                if field in answers_dict:
                    # Выбираем текст вопроса с учётом daily_time
                    time_variants = question.get("time_variants", {})
                    question_text = time_variants.get(daily_time, question.get("text", ""))
                    answer = answers_dict.get(field, "")
                    context.append(f"Вопрос ({daily_time}): {question_text}\nОтвет: {answer}")

            # Если нет ответов или вопросов, возвращаем None
            if not context:
                logging.info("LLM модель (Mistral) НЕ БУДЕТ ЗАДАВАТЬ ВОПРОСА")
                return "None"

            # Формируем полный контекст
            context_str = "\n\n".join(context)
            prompt_template = PromptTemplate(
                input_variables=["name", "role", "context"],
                template=(
                    """Вы — аналитик, который помогает выявить потенциальные проблемы в ежедневных отчётах участников команды. На основе предоставленных ответов на вопросы сформулируйте ровно один уточняющий вопрос, который:

                    - Краткий и понятный.
                    - Помогает выявить скрытые или потенциальные блокеры (технические, организационные, коммуникационные).
                    - Если указаны трудности, уточняет их детали (что именно мешает, на каком этапе, какие зависимости).
                    - Если трудностей нет, но есть выполненные задачи, уточняет завершение работы (например, обновлён ли README, отмечено ли на доске, добавлены ли тесты).
                    - Если трудностей нет и задачи планируются, предполагает типичные проблемы и задаёт прикладной вопрос (например, про доступы, окружение, согласования).
                    - Игнорирует упоминания других участников.
                    - Если всё понятно и уточнять нечего, возвращает "None".

                    Данные сотрудника:
                    Имя: {name}
                    Роль: {role}
                    Ответы на вопросы:
                    {context}

                    Вывод: только один вопрос, без пояснений."""
                ),
            )

            prompt = prompt_template.format(
                name=name or "",
                role=role or "",
                context=context_str,
            )

            # Используем async версию через event loop
            loop = get_main_event_loop()
            if loop and loop.is_running():
                result = asyncio.run_coroutine_threadsafe(
                    self._call_llm_api(
                        prompt,
                        usage_event="clarifying_question",
                        team_id=team_id,
                        employee_tg_id=employee_tg_id,
                    ),
                    loop
                ).result()
            else:
                # Если нет event loop, создаем новый
                result = asyncio.run(
                    self._call_llm_api(
                        prompt,
                        usage_event="clarifying_question",
                        team_id=team_id,
                        employee_tg_id=employee_tg_id,
                    )
                )
            return result.strip() if result else "None"

        except Exception as e:
            logging.error(f"Ошибка при генерации уточняющего вопроса: {e}")
            return f"❌ Ошибка при генерации уточняющего вопроса: {str(e)}"

    async def generate_clarifying_questions_async(
            self,
            name: str,
            role: str,
            answers: Dict,
            questions: List[Dict],
            daily_time: str,
            team_id: Optional[int] = None,
            employee_tg_id: Optional[int] = None,
    ) -> str:
        if not self.is_available():
            return "❌ LLM функциональность недоступна. Проверьте настройки API ключей."
        try:
            context = []
            answers_dict = {item['field']: item['answer'] for item in answers} if isinstance(answers, list) else answers
            for question in questions:
                field = question.get("field")
                if field in answers_dict:
                    time_variants = question.get("time_variants", {})
                    question_text = time_variants.get(daily_time, question.get("text", ""))
                    answer = answers_dict.get(field, "")
                    context.append(f"Вопрос ({daily_time}): {question_text}\nОтвет: {answer}")
            if not context:
                logging.info("LLM модель (Mistral) НЕ БУДЕТ ЗАДАВАТЬ ВОПРОСА")
                return "None"
            context_str = "\n\n".join(context)
            prompt_template = PromptTemplate(
                input_variables=["name", "role", "context"],
                template=(
                    """Вы — аналитик, который помогает выявить потенциальные проблемы в ежедневных отчётах участников команды. На основе предоставленных ответов на вопросы сформулируйте ровно один уточняющий вопрос, который:

                    - Краткий и понятный.
                    - Помогает выявить скрытые или потенциальные блокеры (технические, организационные, коммуникационные).
                    - Если указаны трудности, уточняет их детали (что именно мешает, на каком этапе, какие зависимости).
                    - Если трудностей нет, но есть выполненные задачи, уточняет завершение работы (например, обновлён ли README, отмечено ли на доске, добавлены ли тесты).
                    - Если трудностей нет и задачи планируются, предполагает типичные проблемы и задаёт прикладной вопрос (например, про доступы, окружение, согласования).
                    - Игнорирует упоминания других участников.
                    - Если всё понятно и уточнять нечего, возвращает "None".

                    Данные сотрудника:
                    Имя: {name}
                    Роль: {role}
                    Ответы на вопросы:
                    {context}

                    Вывод: только один вопрос, без пояснений."""
                ),
            )
            prompt = prompt_template.format(name=name or "", role=role or "", context=context_str)
            return await self._call_llm_api(prompt, usage_event="clarifying_question", team_id=team_id, employee_tg_id=employee_tg_id)
        except Exception as e:
            logging.error(f"Ошибка при генерации уточняющего вопроса (async): {e}")
            return f"❌ Ошибка при генерации уточняющего вопроса: {str(e)}"


# Глобальный экземпляр LLM процессора
llm_processor = LLMProcessor()
