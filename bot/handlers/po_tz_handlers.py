"""
Обработчики для создания технического задания (ТЗ) Product Owner
"""
import logging
from typing import Optional

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile

from bot.core import router
from bot.core.states import POTZCreation
from bot.core.database import db_get_employee, db_get_membership, db_get_team_by_id
from bot.utils import send_or_edit_message
from bot.utils.po_tz_workflow import (
    TZWorkflowProcessor, TZWorkflowState, ORDER, SLOT_PROMPTS,
    normalize_list, escape_html, progress_bar,
    MIN_GOAL_LENGTH, MIN_DELIVERABLE_COUNT, MIN_ACCEPTANCE_COUNT, MIN_DESCRIPTION_LENGTH
)

# Глобальное хранилище состояний (в продакшене лучше использовать Redis или БД)
_tz_states: dict[int, TZWorkflowState] = {}


def get_tz_state(user_id: int) -> TZWorkflowState:
    """Получить состояние создания ТЗ для пользователя"""
    if user_id not in _tz_states:
        _tz_states[user_id] = TZWorkflowState()
    return _tz_states[user_id]


def clear_tz_state(user_id: int):
    """Очистить состояние создания ТЗ для пользователя"""
    if user_id in _tz_states:
        del _tz_states[user_id]


def build_tz_keyboard(state: TZWorkflowState) -> InlineKeyboardMarkup:
    """Построение клавиатуры для панели ТЗ"""
    keyboard = []
    
    if state.busy:
        keyboard.append([InlineKeyboardButton(text="⏳ Обработка…", callback_data="tz_noop")])
    else:
        # Кнопки навигации
        nav_row = []
        if state.idx > 0 and not state.completed:
            nav_row.append(InlineKeyboardButton(text="◀ Назад", callback_data="tz_back"))
        if not state.completed:
            nav_row.append(InlineKeyboardButton(text="🔄 Сброс", callback_data="tz_reset"))
        
        if nav_row:
            keyboard.append(nav_row)
        
        # Кнопки для финального этапа
        if state.completed:
            keyboard.append([InlineKeyboardButton(text="📋 Сформировать ТЗ", callback_data="tz_finalize")])
            keyboard.append([InlineKeyboardButton(text="📄 Скачать TXT", callback_data="tz_download_txt")])
            keyboard.append([InlineKeyboardButton(text="📊 Скачать CSV", callback_data="tz_download_csv")])
            keyboard.append([InlineKeyboardButton(text="🔄 Сброс", callback_data="tz_reset")])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def render_panel_text(state: TZWorkflowState) -> str:
    """Рендеринг текста панели"""
    # Если ожидаем уточнение
    if state.awaiting_clarify and state.clarify_question:
        slot = ORDER[state.idx]
        return f"""<b>❓ Уточнение по пункту «{escape_html(SLOT_PROMPTS[slot]['title'])}»</b>

{escape_html(state.clarify_question)}"""
    
    total = len(ORDER)
    step = min(state.idx + 1, total)
    slot = None if state.completed else ORDER[state.idx]
    prompt = SLOT_PROMPTS[slot] if slot else None
    status = f"\n<i>⏳ {escape_html(state.busy_note or 'Выполняю…')}</i>\n" if state.busy else ""
    
    summary = f"""<b>📊 Прогресс:</b> {progress_bar(state.completed and total or state.idx, total)} <code>{state.completed and total or state.idx}/{total}</code>{status}"""
    
    if state.completed:
        if state.cached_tz:
            return f"""{summary}

<b>🎉 Все пункты заполнены!</b> 

<b>✅ Техническое задание готово!</b>
• <b>📋 Сформировать ТЗ</b> - показать в чате
• <b>📄 Скачать TXT</b> - скачать файл
• <b>📊 Скачать CSV</b> - скачать файл

<i>ТЗ автоматически сгенерировано и готово к использованию</i>"""
        else:
            return f"""{summary}

<b>🎉 Все пункты заполнены!</b> 

<b>⏳ Формирую техническое задание...</b>
<i>Пожалуйста, подождите, это может занять несколько секунд</i>"""
    
    hint_text = f"\n\n💡 {escape_html(prompt['hint'])}" if prompt.get('hint') else ''
    ask = f"""<b>📋 Шаг {step} из {total}: {escape_html(prompt['title'])}</b>
{escape_html(prompt['ask'])}{hint_text}"""
    
    result = f"""{summary}

{ask}"""
    
    # Добавляем ответ на вопрос, если он есть
    if state.current_question_answer:
        result += f"\n\n{state.current_question_answer}"
    
    return result


async def update_panel(user_id: int, state: TZWorkflowState, processor: TZWorkflowProcessor, team_id: Optional[int] = None):
    """Обновление панели ТЗ"""
    text = render_panel_text(state)
    keyboard = build_tz_keyboard(state)
    
    # Если есть panel_message_id, пытаемся отредактировать
    if state.panel_message_id:
        try:
            from bot.core.bot_instance import bot
            await bot.edit_message_text(
                chat_id=user_id,
                message_id=state.panel_message_id,
                text=text,
                reply_markup=keyboard,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
            return
        except Exception as e:
            logging.warning(f"Не удалось отредактировать сообщение панели: {e}")
            state.panel_message_id = None
    
    # Создаём новое сообщение
    from bot.core.bot_instance import bot
    try:
        msg = await bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=keyboard,
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        state.panel_message_id = msg.message_id
    except Exception as e:
        logging.error(f"Ошибка при отправке панели: {e}")


@router.callback_query(F.data == "po_create_tz")
async def start_tz_creation(callback: CallbackQuery, state: FSMContext):
    """Начало создания ТЗ"""
    user_id = callback.from_user.id
    
    # Проверяем, является ли пользователь PO
    employee = await db_get_employee(user_id)
    if not employee:
        await callback.answer("❌ Пользователь не найден")
        return
    
    # Получаем команду пользователя
    team_id = None
    if employee.get('team_id'):
        membership = await db_get_membership(user_id, employee['team_id'])
        if membership and membership.get('is_po', False):
            team_id = employee['team_id']
    
    if not team_id:
        await callback.answer("❌ У вас нет доступа к созданию ТЗ. Вы должны быть PO в команде.")
        return
    
    # Инициализируем состояние
    tz_state = get_tz_state(user_id)
    tz_state.idx = 0
    tz_state.answers = {}
    tz_state.awaiting_clarify = False
    tz_state.clarify_question = None
    tz_state.completed = False
    tz_state.clarify_asked = {}
    tz_state.original_answers = {}
    tz_state.pending_notes = []
    tz_state.current_question_answer = None
    tz_state.cached_tz = None
    tz_state.panel_message_id = None
    
    # Сохраняем team_id в FSM
    await state.update_data(team_id=team_id)
    await state.set_state(POTZCreation.waiting_for_answer)
    
    processor = TZWorkflowProcessor()
    await update_panel(user_id, tz_state, processor, team_id)
    await callback.answer()


@router.callback_query(F.data == "tz_back")
async def tz_back_callback(callback: CallbackQuery, state: FSMContext):
    """Возврат к предыдущему вопросу"""
    user_id = callback.from_user.id
    tz_state = get_tz_state(user_id)
    
    if tz_state.idx > 0 and not tz_state.completed:
        tz_state.idx -= 1
        tz_state.awaiting_clarify = False
        tz_state.clarify_question = None
        tz_state.current_question_answer = None
        
        state_data = await state.get_data()
        team_id = state_data.get('team_id')
        
        processor = TZWorkflowProcessor()
        await update_panel(user_id, tz_state, processor, team_id)
    
    await callback.answer()


@router.callback_query(F.data == "tz_reset")
async def tz_reset_callback(callback: CallbackQuery, state: FSMContext):
    """Сброс создания ТЗ"""
    user_id = callback.from_user.id
    
    # Удаляем сообщение с ТЗ, если есть
    tz_state = get_tz_state(user_id)
    if tz_state.panel_message_id:
        try:
            from bot.core.bot_instance import bot
            await bot.delete_message(user_id, tz_state.panel_message_id)
        except Exception:
            pass
    
    clear_tz_state(user_id)
    await state.clear()
    await callback.answer("✅ Создание ТЗ сброшено")


@router.callback_query(F.data == "tz_noop")
async def tz_noop_callback(callback: CallbackQuery):
    """Заглушка для кнопки во время обработки"""
    await callback.answer("⏳ Идёт обработка…")


@router.callback_query(F.data == "tz_finalize")
async def tz_finalize_callback(callback: CallbackQuery, state: FSMContext):
    """Показ финального ТЗ"""
    user_id = callback.from_user.id
    tz_state = get_tz_state(user_id)
    
    if not tz_state.completed or not tz_state.cached_tz:
        await callback.answer()
        return
    
    # Показываем HTML версию ТЗ
    doc = tz_state.cached_tz['html']
    MAX = 3900
    out = doc if len(doc) <= MAX else doc[:MAX - 30] + '\n\n<i>Обрезано по длине сообщения…</i>'
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀ Назад", callback_data="tz_back_to_panel")]
    ])
    
    try:
        from bot.core.bot_instance import bot
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=tz_state.panel_message_id,
            text=out,
            reply_markup=keyboard,
            parse_mode='HTML',
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.error(f"Ошибка при показе ТЗ: {e}")
    
    await callback.answer()


@router.callback_query(F.data == "tz_back_to_panel")
async def tz_back_to_panel_callback(callback: CallbackQuery, state: FSMContext):
    """Возврат к панели"""
    user_id = callback.from_user.id
    tz_state = get_tz_state(user_id)
    
    state_data = await state.get_data()
    team_id = state_data.get('team_id')
    
    processor = TZWorkflowProcessor()
    await update_panel(user_id, tz_state, processor, team_id)
    await callback.answer()


@router.callback_query(F.data == "tz_download_txt")
async def tz_download_txt_callback(callback: CallbackQuery, state: FSMContext):
    """Скачивание TXT файла"""
    user_id = callback.from_user.id
    tz_state = get_tz_state(user_id)
    
    if not tz_state.completed or not tz_state.cached_tz:
        await callback.answer()
        return
    
    try:
        import tempfile
        import os
        
        txt_content = tz_state.cached_tz['txt']
        
        # Создаём временный файл
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(txt_content)
            temp_path = f.name
        
        from bot.core.bot_instance import bot
        file = FSInputFile(temp_path, filename='Техническое_задание.txt')
        await bot.send_document(
            chat_id=user_id,
            document=file,
            caption='📄 Техническое задание TXT'
        )
        
        # Удаляем временный файл
        os.unlink(temp_path)
        
        await callback.answer("✅ TXT файл отправлен")
    except Exception as e:
        logging.error(f"Ошибка при создании TXT файла: {e}")
        await callback.answer("❌ Ошибка при создании файла", show_alert=True)


@router.callback_query(F.data == "tz_download_csv")
async def tz_download_csv_callback(callback: CallbackQuery, state: FSMContext):
    """Скачивание CSV файла"""
    user_id = callback.from_user.id
    tz_state = get_tz_state(user_id)
    
    if not tz_state.completed or not tz_state.cached_tz:
        await callback.answer()
        return
    
    try:
        import tempfile
        import os
        
        csv_content = tz_state.cached_tz['csv']
        
        # Создаём временный файл
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(csv_content)
            temp_path = f.name
        
        from bot.core.bot_instance import bot
        file = FSInputFile(temp_path, filename='Техническое_задание.csv')
        await bot.send_document(
            chat_id=user_id,
            document=file,
            caption='📊 Техническое задание CSV'
        )
        
        # Удаляем временный файл
        os.unlink(temp_path)
        
        await callback.answer("✅ CSV файл отправлен")
    except Exception as e:
        logging.error(f"Ошибка при создании CSV файла: {e}")
        await callback.answer("❌ Ошибка при создании файла", show_alert=True)


@router.message(POTZCreation.waiting_for_answer)
async def handle_tz_answer(message: Message, state: FSMContext):
    """Обработка ответа пользователя на вопрос ТЗ"""
    user_id = message.from_user.id
    tz_state = get_tz_state(user_id)
    
    if tz_state.busy or tz_state.completed:
        return
    
    # Удаляем сообщение пользователя
    try:
        await message.delete()
    except Exception:
        pass
    
    if not message.text:
        return
    
    slot = ORDER[tz_state.idx]
    text = message.text.strip()
    
    state_data = await state.get_data()
    team_id = state_data.get('team_id')
    
    processor = TZWorkflowProcessor()
    
    # Если ожидаем уточнение
    if tz_state.awaiting_clarify:
        tz_state.busy = True
        tz_state.busy_note = '💬 Обрабатываю уточнение…'
        await update_panel(user_id, tz_state, processor, team_id)
        
        # Объединяем исходный ответ с уточнением
        original_answer = tz_state.original_answers.get(slot, '')
        combined_answer = f"{original_answer}\n\n💬 <b>Уточнение:</b>\n{text}"
        
        # Сохраняем объединённый ответ
        tz_state.answers[slot] = combined_answer
        tz_state.awaiting_clarify = False
        tz_state.clarify_question = None
        tz_state.original_answers[slot] = None
        tz_state.current_question_answer = None
        tz_state.idx += 1
        
        if tz_state.idx >= len(ORDER):
            tz_state.completed = True
        
        # Если завершили все этапы, автоматически формируем и кэшируем ТЗ
        if tz_state.completed and not tz_state.cached_tz:
            tz_state.busy = True
            tz_state.busy_note = '📋 Автоматически формирую ТЗ...'
            await update_panel(user_id, tz_state, processor, team_id)
            
            try:
                cached_tz = await processor.generate_and_cache_tz(
                    tz_state.answers,
                    tz_state.pending_notes,
                    tz_state.tz_counter,
                    team_id,
                    user_id
                )
                tz_state.cached_tz = cached_tz
                logging.info('[AUTO_TZ] ТЗ автоматически сгенерировано и закэшировано')
            except Exception as e:
                logging.error(f'[AUTO_TZ] Ошибка при автоматической генерации ТЗ: {e}')
            
            tz_state.busy = False
            tz_state.busy_note = None
        
        tz_state.busy = False
        tz_state.busy_note = None
        await update_panel(user_id, tz_state, processor, team_id)
        return
    
    # Проверяем, не является ли это вопросом
    tz_state.busy = True
    tz_state.busy_note = '🔍 Анализирую ввод…'
    await update_panel(user_id, tz_state, processor, team_id)
    
    question_result = await processor.detect_and_answer_question(text, slot, team_id, user_id)
    
    if question_result.get('isQuestion') and question_result.get('answer'):
        # Это вопрос - сохраняем ответ в состоянии и показываем в панели
        answer_text = f"""{question_result['answer']}

💡 <b>Пояснение:</b> {question_result.get('explanation', 'Это поможет вам лучше понять, что нужно указать в данном разделе ТЗ.')}

<i>Теперь, пожалуйста, дайте ответ на вопрос выше 👆</i>"""
        
        tz_state.busy = False
        tz_state.busy_note = None
        tz_state.current_question_answer = answer_text
        await update_panel(user_id, tz_state, processor, team_id)
        return
    
    # Если это не вопрос, продолжаем обычную обработку
    tz_state.busy_note = '🔍 Проверяю ответ…'
    await update_panel(user_id, tz_state, processor, team_id)
    
    # Проверяем ответ через AI судью
    try:
        verdict = await processor.judge_answer(slot, text, team_id, user_id)
    except Exception as e:
        logging.error(f"Ошибка при проверке ответа: {e}")
        tz_state.awaiting_clarify = True
        tz_state.clarify_question = '❌ Не удалось проверить ответ. Переформулируйте, пожалуйста.'
        tz_state.busy = False
        tz_state.busy_note = None
        await update_panel(user_id, tz_state, processor, team_id)
        return
    
    # Упрощённая логика: либо принимаем, либо один раз спрашиваем уточнение
    can_accept = verdict.get('status') == 'sufficient'
    if not can_accept:
        already_asked = tz_state.clarify_asked.get(slot, False)
        if not already_asked:
            # Сохраняем исходный ответ и задаём уточнение
            tz_state.original_answers[slot] = text
            tz_state.awaiting_clarify = True
            tz_state.clarify_question = verdict.get('followup_question') or '💡 Можете уточнить ключевые детали?'
            tz_state.clarify_asked[slot] = True
            tz_state.busy = False
            tz_state.busy_note = None
            await update_panel(user_id, tz_state, processor, team_id)
            return
        else:
            # Уже спрашивали — принимаем с оговоркой
            tz_state.pending_notes.append(
                f"Слот «{SLOT_PROMPTS[slot]['title']}» принят с оговоркой: {', '.join(verdict.get('missing_fields', [])) or 'требуется уточнение деталей'}."
            )
            can_accept = True
    
    # Сохраняем ответ
    try:
        normalized = text
        if slot in ('deliverable', 'acceptance'):
            normalized = normalize_list(text)
        
        # Простая валидация
        if slot == 'goal' and len(text) < MIN_GOAL_LENGTH:
            tz_state.pending_notes.append(f"Слот «{SLOT_PROMPTS[slot]['title']}» принят по упрощённой проверке.")
        elif slot == 'deliverable' and isinstance(normalized, list) and len(normalized) < MIN_DELIVERABLE_COUNT:
            tz_state.pending_notes.append(f"Слот «{SLOT_PROMPTS[slot]['title']}» принят по упрощённой проверке.")
        elif slot == 'acceptance' and isinstance(normalized, list) and len(normalized) < MIN_ACCEPTANCE_COUNT:
            tz_state.pending_notes.append(f"Слот «{SLOT_PROMPTS[slot]['title']}» принят по упрощённой проверке.")
        elif slot == 'description' and len(text) < MIN_DESCRIPTION_LENGTH:
            tz_state.pending_notes.append(f"Слот «{SLOT_PROMPTS[slot]['title']}» принят по упрощённой проверке.")
        
        tz_state.answers[slot] = normalized
        tz_state.idx += 1
        tz_state.awaiting_clarify = False
        tz_state.clarify_question = None
        tz_state.current_question_answer = None
    except Exception as e:
        logging.error(f"Ошибка при сохранении ответа: {e}")
        # Fallback: сохраняем как есть
        fallback = normalize_list(text) if slot in ('deliverable', 'acceptance') else text
        tz_state.answers[slot] = fallback
        tz_state.pending_notes.append(f"Слот «{SLOT_PROMPTS[slot]['title']}» принят по упрощённой проверке.")
        tz_state.idx += 1
        tz_state.awaiting_clarify = False
        tz_state.clarify_question = None
        tz_state.current_question_answer = None
    
    if tz_state.idx >= len(ORDER):
        tz_state.completed = True
    
    # Если завершили все этапы, автоматически формируем и кэшируем ТЗ
    if tz_state.completed and not tz_state.cached_tz:
        tz_state.busy = True
        tz_state.busy_note = '📋 Автоматически формирую ТЗ...'
        await update_panel(user_id, tz_state, processor, team_id)
        
        try:
            cached_tz = await processor.generate_and_cache_tz(
                tz_state.answers,
                tz_state.pending_notes,
                tz_state.tz_counter,
                team_id,
                user_id
            )
            tz_state.cached_tz = cached_tz
            logging.info('[AUTO_TZ] ТЗ автоматически сгенерировано и закэшировано')
        except Exception as e:
            logging.error(f'[AUTO_TZ] Ошибка при автоматической генерации ТЗ: {e}')
        
        tz_state.busy = False
        tz_state.busy_note = None
    
    tz_state.busy = False
    tz_state.busy_note = None
    await update_panel(user_id, tz_state, processor, team_id)

