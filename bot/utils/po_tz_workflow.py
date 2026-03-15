"""
Модуль для создания технического задания (ТЗ) Product Owner
Переписан с TypeScript версии TS-bot
"""
import logging
import re
import json
import csv
import io
from typing import Dict, List, Optional, Literal
from datetime import datetime
import hashlib

from bot.utils.llm_utils import llm_processor

# Слоты вопросов
SlotId = Literal['goal', 'deliverable', 'acceptance', 'description']
ORDER: List[SlotId] = ['goal', 'deliverable', 'acceptance', 'description']

SLOT_PROMPTS: Dict[SlotId, Dict[str, str]] = {
    'goal': {
        'title': '🎯 Цель',
        'ask': '1) Опишите Цель проекта — что именно должно быть достигнуто и какую ценность это даст пользователю (1–2 абзаца).',
        'hint': 'Избегайте общих слов, укажите проблему пользователя и желаемый результат.'
    },
    'deliverable': {
        'title': '📦 Результат',
        'ask': '2) Что должна создать команда проекта? Перечислите конкретные артефакты/продукты/выпуски.',
        'hint': 'Например: мобильное приложение iOS/Android, админ-панель, API, landing, отчёты.'
    },
    'acceptance': {
        'title': '✅ Критерии приёмки результата',
        'ask': '3) Перечислите критерии приёмки (что должно быть выполнено, чтобы проект считался успешным). Минимум 3 пункта.',
        'hint': 'Формулируйте проверяемо: «пользователь может…», «время отклика ≤…», «конверсия ≥…».'
    },
    'description': {
        'title': '⚙️ Описание',
        'ask': '4) Опишите особенности проекта: ключевые технологии, архитектурные решения, интеграции и способы реализации.',
        'hint': 'Можно указать стек, ограничения, внешние сервисы и т. п.'
    }
}

# Валидация
MIN_GOAL_LENGTH = 40
MIN_DELIVERABLE_COUNT = 1
MIN_ACCEPTANCE_COUNT = 3
MIN_DESCRIPTION_LENGTH = 40

# Модели для AI
JUDGE_MODEL = 'gpt-4o-mini'  # Судья для проверки ответов
TZ_MODEL = 'gpt-4o'  # Генерация финального ТЗ
TOKEN_LIMIT = 3500


class TZWorkflowState:
    """Состояние процесса создания ТЗ"""
    def __init__(self):
        self.idx = 0  # Текущий индекс вопроса
        self.answers: Dict[str, any] = {}  # Ответы пользователя
        self.awaiting_clarify = False  # Ожидаем уточнение
        self.clarify_question: Optional[str] = None  # Вопрос для уточнения
        self.completed = False  # Все вопросы заполнены
        self.clarify_asked: Dict[SlotId, bool] = {}  # Уже спрашивали уточнение по слоту
        self.original_answers: Dict[SlotId, str] = {}  # Исходные ответы перед уточнением
        self.pending_notes: List[str] = []  # Заметки для финального ТЗ
        self.current_question_answer: Optional[str] = None  # Ответ на вопрос пользователя
        self.cached_tz: Optional[Dict[str, str]] = None  # Кэш сгенерированного ТЗ
        self.tz_counter = 1  # Счётчик ТЗ
        self.panel_message_id: Optional[int] = None  # ID сообщения панели
        self.busy = False  # Флаг обработки
        self.busy_note: Optional[str] = None  # Сообщение о процессе обработки


def normalize_list(text: str) -> List[str]:
    """Нормализация списка из текста"""
    text = text.strip()
    if text.startswith('['):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    
    # Разделяем по переносам строк, запятым, точкам с запятой, маркерам
    items = re.split(r'\n|[,;•\-–]', text)
    return [item.strip() for item in items if item.strip()]


def escape_html(s: str) -> str:
    """Экранирование HTML"""
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def progress_bar(current: int, total: int) -> str:
    """Создание прогресс-бара"""
    filled = '■' * current
    empty = '□' * (total - current)
    return filled + empty


def coerce_telegram_html(input_text: str) -> str:
    """Преобразование HTML в формат Telegram"""
    s = input_text.strip()
    
    # Убираем бэктики-кодблоки
    s = re.sub(r'```+', '', s)
    
    # Убираем доктайп и каркас
    s = re.sub(r'<!DOCTYPE[\s\S]*?>', '', s, flags=re.IGNORECASE)
    s = re.sub(r'</?(html|head|body)[^>]*>', '', s, flags=re.IGNORECASE)
    
    # Заголовки -> <b>…</b>
    s = re.sub(r'<h[1-6][^>]*>([\s\S]*?)</h[1-6]>', r'\n<b>\1</b>\n', s, flags=re.IGNORECASE)
    
    # Параграфы -> переносы
    s = re.sub(r'<\s*p[^>]*>', '', s, flags=re.IGNORECASE)
    s = re.sub(r'<\s*/p\s*>', '\n\n', s, flags=re.IGNORECASE)
    
    # Списки -> маркеры
    s = re.sub(r'<\s*li[^>]*>\s*', '• ', s, flags=re.IGNORECASE)
    s = re.sub(r'<\s*/li\s*>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'</?(ul|ol)[^>]*>', '', s, flags=re.IGNORECASE)
    
    # Таблицы -> просто строчки
    s = re.sub(r'<\s*tr[^>]*>', '', s, flags=re.IGNORECASE)
    s = re.sub(r'<\s*/tr\s*>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'<\s*t[dh][^>]*>', '', s, flags=re.IGNORECASE)
    s = re.sub(r'<\s*/t[dh]\s*>', ' | ', s, flags=re.IGNORECASE)
    s = re.sub(r'</?table[^>]*>', '', s, flags=re.IGNORECASE)
    
    # Оставляем только разрешённые теги
    allowed_tags = ['b', 'strong', 'i', 'em', 'u', 'ins', 's', 'strike', 'del', 'code', 'pre', 'a', 'blockquote', 'span']
    pattern = rf'</?(?!{"|".join(allowed_tags)})([a-z0-9]+)(\s[^>]*)?>'
    s = re.sub(pattern, '', s, flags=re.IGNORECASE)
    
    # Нормализуем разрешённые теги
    s = re.sub(r'<(b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote)(\s[^>]*)?>', r'<\1>', s, flags=re.IGNORECASE)
    s = re.sub(r'<span(?![^>]*class=["\']tg-spoiler["\'])[^>]*>', '<span>', s, flags=re.IGNORECASE)
    s = re.sub(r'<a[^>]*href=["\']?([^"\'>\s]+)["\']?[^>]*>', r'<a href="\1">', s, flags=re.IGNORECASE)
    
    # Подчищаем пустые теги и множественные переносы
    s = re.sub(r'<([biu]|strong|em|ins|s|strike|del|code|pre|blockquote|span)>\s*</\1>', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+\n', '\n', s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    
    return s.strip()


class TZWorkflowProcessor:
    """Процессор для создания ТЗ"""
    
    def __init__(self):
        # Используем глобальный экземпляр llm_processor из llm_utils
        self.llm = llm_processor
    
    async def judge_answer(self, slot: SlotId, user_answer: str, team_id: Optional[int] = None, employee_tg_id: Optional[int] = None) -> Dict:
        """
        AI судья для проверки ответа пользователя
        Возвращает: {"status": "sufficient"|"clarify", "missing_fields": [], "followup_question": ""}
        """
        criteria = {
            'goal': 'Связный текст 1–2 абзаца о целях и ценности. Без воды/общих фраз.',
            'deliverable': 'Конкретные артефакты/продукты/выпуски, минимум 1.',
            'acceptance': '≥3 измеримых критерия приёмки (списком).',
            'description': 'Особенности, технологии, интеграции, ограничения; 1–2 абзаца.',
        }
        
        sys_prompt = """Ты ассистент-проверяющий полноту ответа на слот ТЗ.
Верни ТОЛЬКО JSON: {"status":"sufficient"|"clarify","missing_fields":[],"followup_question":""}
Правила:
- Оценивай мягко, но не слишком строго. Если ответ в целом подходит по смыслу и связан с проектной деятельностью — ставь "sufficient".
- Если чего-то явно не хватает, верни "clarify" и СФОРМУЛИРУЙ ОДИН короткий followup_question.
- "missing_fields" укажи только при явном отсутствии ключевых аспектов."""
        
        user_prompt = f"""Слот: {slot}
Критерии: {criteria[slot]}
Ответ: \"\"\"{user_answer}\"\"\""""
        
        try:
            # Используем метод с системным промптом для лучшей работы с JSON ответами
            response = await self.llm.call_llm_with_system_prompt(
                system_prompt=sys_prompt,
                user_prompt=user_prompt,
                usage_event="tz_judge",
                team_id=team_id,
                employee_tg_id=employee_tg_id,
                temperature=0  # Низкая температура для более детерминированных ответов
            )
            
            # Парсим JSON из ответа
            # Модель может вернуть JSON в разных форматах, пытаемся извлечь
            json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
            else:
                # Fallback: пытаемся распарсить весь ответ
                parsed = json.loads(response)
            
            return {
                'status': parsed.get('status', 'sufficient'),
                'missing_fields': parsed.get('missing_fields', []),
                'followup_question': parsed.get('followup_question', '')
            }
        except Exception as e:
            logging.error(f"Ошибка при проверке ответа: {e}")
            # Fallback: принимаем ответ
            return {
                'status': 'sufficient',
                'missing_fields': [],
                'followup_question': ''
            }
    
    async def detect_and_answer_question(self, user_input: str, current_slot: SlotId, team_id: Optional[int] = None, employee_tg_id: Optional[int] = None) -> Dict:
        """
        Детекция вопроса пользователя и ответ на него
        Возвращает: {"isQuestion": bool, "answer": "", "explanation": ""}
        """
        sys_prompt = f"""Ты ассистент, который определяет, является ли ввод пользователя вопросом, и если да - отвечает на него.
Верни ТОЛЬКО JSON: {{"isQuestion":true|false,"answer":"","explanation":""}}

Правила:
- Если пользователь задаёт вопрос (начинается с вопросительных слов, содержит "?", "как", "что", "где" и т.п.) - верни isQuestion:true
- Если это вопрос - дай краткий, но полезный ответ на него
- В explanation объясни, как это связано с текущим этапом ТЗ
- Если это НЕ вопрос - верни isQuestion:false, остальные поля пустые

Текущий этап: {SLOT_PROMPTS[current_slot]['title']}
Описание этапа: {SLOT_PROMPTS[current_slot]['ask']}"""
        
        user_prompt = f'Ввод пользователя: """{user_input}"""'
        
        try:
            response = await self.llm.call_llm_with_system_prompt(
                system_prompt=sys_prompt,
                user_prompt=user_prompt,
                usage_event="tz_question_detection",
                team_id=team_id,
                employee_tg_id=employee_tg_id,
                temperature=0  # Низкая температура для более детерминированных ответов
            )
            
            json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
            else:
                parsed = json.loads(response)
            
            return {
                'isQuestion': parsed.get('isQuestion', False),
                'answer': parsed.get('answer', ''),
                'explanation': parsed.get('explanation', '')
            }
        except Exception as e:
            logging.error(f"Ошибка детекции вопроса: {e}")
            # Fallback: простая эвристика
            is_question = bool(re.search(r'\?|^(как|что|где|когда|почему|зачем|кто|какой|какая|какие|сколько)', user_input, re.IGNORECASE))
            return {'isQuestion': is_question, 'answer': '', 'explanation': ''}
    
    async def generate_final_tz(self, answers: Dict, notes: List[str] = [], team_id: Optional[int] = None, employee_tg_id: Optional[int] = None) -> str:
        """Генерация финального ТЗ"""
        sys_prompt = """Ты тимлид/системный аналитик. Сформируй ТОЛЬКО текст для Telegram с parse_mode=HTML.
Требования к формату:
- Никаких обёрток: БЕЗ <!DOCTYPE>, <html>, <head>, <body>, <h1>-<h6>, <ul>/<ol>/<li>, <table>, <img>, <div> и т.п.
- Разрешённые теги: <b>, <strong>, <i>, <em>, <u>, <ins>, <s>, <strike>, <del>, <code>, <pre>, <a>.
- Заголовки разделов — как жирный текст (<b>…</b>), списки — строками с маркером "• ".
- Разделяй блоки пустыми строками. Никаких Markdown бэктиков, блоков кода-ограждений и пролога/эпилога."""
        
        deliverable_text = ''
        if isinstance(answers.get('deliverable'), list):
            deliverable_text = '\n'.join(f"- {x}" for x in answers['deliverable'])
        else:
            deliverable_text = answers.get('deliverable', '-')
        
        acceptance_text = ''
        if isinstance(answers.get('acceptance'), list):
            acceptance_text = '\n'.join(f"- {x}" for x in answers['acceptance'])
        else:
            acceptance_text = answers.get('acceptance', '-')
        
        notes_text = '\n'.join(f"• {n}" for n in notes) if notes else '—'
        
        user_prompt = f"""Сформируй ТЗ в следующем формате:

<b>📋 ТЕХНИЧЕСКОЕ ЗАДАНИЕ</b>

<b>1. 🎯 Цель проекта</b>
{{анализ и структурирование цели из данных заказчика}}

<b>2. 📦 Результаты работ (Deliverables)</b>
{{структурированный список артефактов}}

<b>3. 🏗️ Архитектура и технологии</b>
{{предложения по архитектуре на основе описания}}

<b>4. ✅ Критерии приёмки</b>
{{структурированные критерии}}

<b>5. ⚠️ Ограничения и риски</b>
{{выявленные ограничения и потенциальные риски}}

<b>6. 📅 Планы релизов</b>
{{предложения по этапам разработки}}

<b>7. ❓ Открытые вопросы</b>
{notes_text}

<b>8. 💡 РЕКОМЕНДАЦИИ ПО РЕАЛИЗАЦИИ</b>
{{конкретные предложения по технологическому стеку, архитектуре, этапам}}

<b>9. 🚀 ИДЕАЛЬНОЕ ТЗ (если бы данные были полными)</b>
{{как должно было бы выглядеть ТЗ при идеальном получении данных}}

Данные от заказчика:
Цель: {answers.get('goal', '-')}
Результат: {deliverable_text}
Критерии: {acceptance_text}
Описание: {answers.get('description', '-')}

Важно: 
- Анализируй данные критически
- Предлагай конкретные решения
- Показывай, как должно было бы выглядеть идеальное ТЗ
- Давай практические рекомендации"""
        
        try:
            response = await self.llm.call_llm_with_system_prompt(
                system_prompt=sys_prompt,
                user_prompt=user_prompt,
                usage_event="tz_generation",
                team_id=team_id,
                employee_tg_id=employee_tg_id,
                temperature=0.3  # Немного выше для более творческой генерации ТЗ
            )
            
            return coerce_telegram_html(response.strip() or 'Не удалось сформировать ТЗ.')
        except Exception as e:
            logging.error(f"Ошибка при генерации ТЗ: {e}")
            return '<b>❌ Ошибка генерации ТЗ</b>\n\nНе удалось сформировать техническое задание. Попробуйте сбросить и начать заново.'
    
    def generate_txt_file(self, html_content: str) -> str:
        """Генерация TXT файла из HTML"""
        try:
            txt_content = html_content
            # Убираем HTML теги, но сохраняем структуру
            txt_content = re.sub(r'<b>(.*?)</b>', r'\1', txt_content)
            txt_content = re.sub(r'<strong>(.*?)</strong>', r'\1', txt_content)
            txt_content = re.sub(r'<i>(.*?)</i>', r'\1', txt_content)
            txt_content = re.sub(r'<em>(.*?)</em>', r'\1', txt_content)
            txt_content = re.sub(r'<u>(.*?)</u>', r'\1', txt_content)
            txt_content = re.sub(r'<ins>(.*?)</ins>', r'\1', txt_content)
            txt_content = re.sub(r'<s>(.*?)</s>', r'\1', txt_content)
            txt_content = re.sub(r'<strike>(.*?)</strike>', r'\1', txt_content)
            txt_content = re.sub(r'<del>(.*?)</del>', r'\1', txt_content)
            txt_content = re.sub(r'<code>(.*?)</code>', r'\1', txt_content)
            txt_content = re.sub(r'<pre>(.*?)</pre>', r'\1', txt_content)
            txt_content = re.sub(r'<a[^>]*href=["\']?([^"\'>\s]+)["\']?[^>]*>(.*?)</a>', r'\2 (\1)', txt_content)
            txt_content = re.sub(r'<blockquote>(.*?)</blockquote>', r'\1', txt_content)
            txt_content = re.sub(r'<span[^>]*>(.*?)</span>', r'\1', txt_content)
            # Убираем эмодзи
            txt_content = re.sub(r'[🎯📦✅⚙️⚠️📅❓💡🚀📋]', '', txt_content)
            # Нормализуем переносы строк
            txt_content = re.sub(r'\n{3,}', '\n\n', txt_content)
            
            return txt_content.strip()
        except Exception as e:
            logging.error(f"Ошибка при генерации TXT файла: {e}")
            return 'ОШИБКА ГЕНЕРАЦИИ ТЗ\n\nНе удалось сформировать техническое задание. Попробуйте сбросить и начать заново.'
    
    def generate_csv_file(self, html_content: str, tz_counter: int = 1) -> str:
        """Генерация CSV файла из HTML"""
        try:
            headers = [
                'id_',
                'Цель проекта',
                'Результаты работ',
                'Критерии приёмки',
                'Описание проекта',
                'Архитектура и технологии',
                'Ограничения и риски',
                'Планы релизов',
                'Открытые вопросы',
                'Рекомендации по реализации',
                'Общая оценка'
            ]
            
            def extract_section_content(html: str, section_name: str) -> str:
                """Извлечение содержимого раздела из HTML"""
                pattern = rf'<b>\d+\.\s*[^<]*{re.escape(section_name)}[^<]*</b>\s*([\s\S]*?)(?=<b>\d+\.\s*|$)'
                match = re.search(pattern, html, re.IGNORECASE)
                if match and match.group(1):
                    content = match.group(1)
                    # Очищаем HTML теги и эмодзи
                    content = re.sub(r'<[^>]*>', '', content)
                    content = re.sub(r'[🎯📦✅⚙️⚠️📅❓💡🚀📋]', '', content)
                    content = re.sub(r'\n+', ' ', content)
                    return content.strip()
                return 'Не указано'
            
            data = [
                str(tz_counter),
                extract_section_content(html_content, 'Цель проекта'),
                extract_section_content(html_content, 'Результаты работ'),
                extract_section_content(html_content, 'Критерии приёмки'),
                extract_section_content(html_content, 'Описание проекта'),
                extract_section_content(html_content, 'Архитектура и технологии'),
                extract_section_content(html_content, 'Ограничения и риски'),
                extract_section_content(html_content, 'Планы релизов'),
                extract_section_content(html_content, 'Открытые вопросы'),
                extract_section_content(html_content, 'Рекомендации по реализации'),
                extract_section_content(html_content, 'Общая оценка')
            ]
            
            # Формируем CSV
            output = io.StringIO()
            writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(headers)
            writer.writerow(data)
            
            return output.getvalue()
        except Exception as e:
            logging.error(f"Ошибка при генерации CSV файла: {e}")
            return f'id_,Цель проекта,Результаты работ,Критерии приёмки,Описание проекта,Архитектура и технологии,Ограничения и риски,Планы релизов,Открытые вопросы,Рекомендации по реализации,Общая оценка\n{tz_counter},Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации'
    
    async def generate_and_cache_tz(self, answers: Dict, notes: List[str] = [], tz_counter: int = 1, team_id: Optional[int] = None, employee_tg_id: Optional[int] = None) -> Dict[str, str]:
        """Автоматическое формирование и кэширование ТЗ во всех форматах"""
        logging.info('[generateAndCacheTZ] Начинаю генерацию всех форматов ТЗ...')
        
        try:
            # Сначала генерируем HTML версию (основную)
            html = await self.generate_final_tz(answers, notes, team_id, employee_tg_id)
            logging.info('[generateAndCacheTZ] HTML версия сгенерирована')
            
            # На основе HTML генерируем TXT и CSV
            txt = self.generate_txt_file(html)
            csv_content = self.generate_csv_file(html, tz_counter)
            
            logging.info('[generateAndCacheTZ] Все форматы сгенерированы успешно')
            
            return {
                'html': html,
                'txt': txt,
                'csv': csv_content,
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            logging.error(f'[generateAndCacheTZ] Ошибка при генерации: {e}')
            
            # Fallback: простые заглушки
            return {
                'html': '<b>❌ Ошибка генерации ТЗ</b>\n\nНе удалось сформировать техническое задание. Попробуйте сбросить и начать заново.',
                'txt': 'ОШИБКА ГЕНЕРАЦИИ ТЗ\n\nНе удалось сформировать техническое задание. Попробуйте сбросить и начать заново.',
                'csv': f'id_,Цель проекта,Результаты работ,Критерии приёмки,Описание проекта,Архитектура и технологии,Ограничения и риски,Планы релизов,Открытые вопросы,Рекомендации по реализации,Общая оценка\n{tz_counter},Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации,Ошибка генерации',
                'timestamp': datetime.now().isoformat()
            }

