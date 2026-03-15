import logging
from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from bot.core import router
from bot.core.states import TeamQuestionsEdit
from bot.core.database import db_get_team_by_manager, db_get_team_questions, db_update_team_questions
from bot.utils import send_or_edit_message
from bot.utils.keyboards import team_questions_keyboard, yes_no_keyboard, question_type_keyboard
from bot.utils.utils import get_access_error_message

# --- ОСНОВНАЯ ФУНКЦИЯ ОТОБРАЖЕНИЯ МЕНЮ ---

async def show_questions_menu(message: Message | CallbackQuery, team_id: int, state: FSMContext):
    """Отображает меню управления вопросами и устанавливает правильное состояние."""
    questions = await db_get_team_questions(team_id)
    text = "❓ <b>Вопросы команды:</b>\n\n"
    if not questions:
        text += "Пока нет ни одного вопроса. Добавьте первый!\n"
    else:
        for i, q in enumerate(questions, 1):
            text += f"<b>{i}. ID {q['id']}:</b> {q['text']}\n"
            text += f"   (Поле: `{q['field']}`, Доска: {'да' if q.get('board_related') else 'нет'})\n"
            if 'time_variants' in q and q['time_variants']:
                text += f"   <i>Варианты: утро - '{q['time_variants']['morning']}', вечер - '{q['time_variants']['evening']}'</i>\n"
    
    text += "\nВыберите действие:"
    
    await send_or_edit_message(message, text, reply_markup=team_questions_keyboard(questions))
    await state.set_state(TeamQuestionsEdit.choosing_action)

# --- ТОЧКА ВХОДА В РЕДАКТОР ВОПРОСОВ ---

@router.callback_query(F.data == "edit_questions")
async def edit_questions_callback(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Вопросы опросов' из главного меню настроек."""
    user_id = callback.from_user.id
    team = await db_get_team_by_manager(user_id)
    if not team:
        await callback.answer(get_access_error_message("редактирования вопросов"))
        return
        
    await state.update_data(team_id=team['id'])
    await show_questions_menu(callback.message, team['id'], state)
    await callback.answer()


# --- ОБРАБОТЧИКИ ДЕЙСТВИЙ ИЗ МЕНЮ: ДОБАВИТЬ, РЕДАКТИРОВАТЬ, УДАЛИТЬ ---

@router.callback_query(TeamQuestionsEdit.choosing_action, F.data == "add_question")
async def add_question_start(callback: CallbackQuery, state: FSMContext):
    """Начинает процесс ДОБАВЛЕНИЯ нового вопроса."""
    # Очищаем ID редактируемого вопроса на всякий случай, если он остался от прошлых действий
    await state.update_data(edit_question_id=None)
    await send_or_edit_message(callback.message, "Выберите тип вопроса:", reply_markup=question_type_keyboard())
    await state.set_state(TeamQuestionsEdit.waiting_for_question_type)
    await callback.answer()

@router.callback_query(TeamQuestionsEdit.choosing_action, F.data.startswith("edit_question_"))
async def edit_question_start(callback: CallbackQuery, state: FSMContext):
    """Начинает процесс РЕДАКТИРОВАНИЯ существующего вопроса."""
    try:
        q_id = int(callback.data.split("_")[-1])
        await state.update_data(edit_question_id=q_id)
        
        await send_or_edit_message(callback.message, "Выберите новый тип вопроса (или оставьте прежним):", reply_markup=question_type_keyboard())
        await state.set_state(TeamQuestionsEdit.waiting_for_question_type)
        await callback.answer()
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка: некорректный ID вопроса.")
        logging.error(f"Некорректный ID вопроса в callback: {callback.data}")

@router.callback_query(TeamQuestionsEdit.choosing_action, F.data.startswith("delete_question_"))
async def delete_question(callback: CallbackQuery, state: FSMContext):
    """УДАЛЯЕТ вопрос по ID."""
    try:
        q_id = int(callback.data.split("_")[-1])
        data = await state.get_data()
        team_id = data['team_id']
        
        questions = await db_get_team_questions(team_id)
        questions_after_delete = [q for q in questions if q['id'] != q_id]
        
        if len(questions) == len(questions_after_delete):
            await callback.answer(f"❌ Вопрос с ID {q_id} не найден.")
            return

        await db_update_team_questions(team_id, questions_after_delete)
        await callback.answer("✅ Вопрос удалён!")
        await show_questions_menu(callback.message, team_id, state)
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка: некорректный ID вопроса.")
        logging.error(f"Некорректный ID вопроса в callback: {callback.data}")
    except Exception as e:
        logging.error(f"Ошибка в delete_question: {e}")
        await callback.answer("❌ Ошибка при удалении.")

# --- ОБЩАЯ ЛОГИКА ДЛЯ ДОБАВЛЕНИЯ И РЕДАКТИРОВАНИЯ ---

@router.callback_query(TeamQuestionsEdit.waiting_for_question_type, F.data.in_({"question_type_common", "question_type_variants"}))
async def process_question_type(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор типа вопроса и направляет на нужный шаг."""
    question_type = callback.data
    await state.update_data(question_type=question_type)
    
    if question_type == "question_type_common":
        await send_or_edit_message(callback.message, "Введите текст вопроса:")
        await state.set_state(TeamQuestionsEdit.waiting_for_text)
    else:  # question_type_variants
        await send_or_edit_message(callback.message, "Введите текст для утра (например, 'Что делал вчера?'):")
        await state.set_state(TeamQuestionsEdit.waiting_for_morning_variant)
    await callback.answer()

@router.message(TeamQuestionsEdit.waiting_for_text)
async def process_text(message: Message, state: FSMContext):
    """Получает основной текст для общего вопроса."""
    await state.update_data(new_text=message.text.strip())
    # Обнуляем варианты, если пользователь переключился с вариантного типа на общий
    await state.update_data(time_variants={})
    await send_or_edit_message(message, "Введите имя поля для сохранения ответа (одно слово, латиницей, например: `yesterday`):")
    await state.set_state(TeamQuestionsEdit.waiting_for_field)

@router.message(TeamQuestionsEdit.waiting_for_morning_variant)
async def process_morning_variant(message: Message, state: FSMContext):
    """Получает утренний вариант текста."""
    await state.update_data(time_variants={'morning': message.text.strip()})
    await send_or_edit_message(message, "Введите текст для вечера (например, 'Что будешь делать завтра?'):")
    await state.set_state(TeamQuestionsEdit.waiting_for_evening_variant)

@router.message(TeamQuestionsEdit.waiting_for_evening_variant)
async def process_evening_variant(message: Message, state: FSMContext):
    """Получает вечерний вариант текста."""
    data = await state.get_data()
    time_variants = data.get('time_variants', {})
    time_variants['evening'] = message.text.strip()
    await state.update_data(time_variants=time_variants)
    # Для вариантного вопроса основной текст — это заглушка
    await state.update_data(new_text="Вопрос с вариантами")
    await send_or_edit_message(message, "Введите имя поля для сохранения ответа (одно слово, латиницей, например: `today`):")
    await state.set_state(TeamQuestionsEdit.waiting_for_field)

@router.message(TeamQuestionsEdit.waiting_for_field)
async def process_field(message: Message, state: FSMContext):
    """Получает имя поля и запрашивает связь с доской (финальный шаг перед сохранением)."""
    field = message.text.strip().lower()
    if not field.isalnum():
        await message.answer("❌ Имя поля должно содержать только латинские буквы и цифры.")
        return
    await state.update_data(new_field=field)
    await send_or_edit_message(message, "Этот вопрос связан с задачами на доске?", reply_markup=yes_no_keyboard())
    await state.set_state(TeamQuestionsEdit.waiting_for_board_related)

# --- ФИНАЛЬНЫЙ ОБРАБОТЧИК: СОХРАНЕНИЕ РЕЗУЛЬТАТА ---

@router.callback_query(TeamQuestionsEdit.waiting_for_board_related, F.data.in_({"yes", "no"}))
async def process_final_save(callback: CallbackQuery, state: FSMContext):
    """
    Финальный шаг. Собирает все данные и либо создает новый вопрос, 
    либо обновляет старый, в зависимости от наличия 'edit_question_id' в состоянии.
    """
    try:
        data = await state.get_data()
        team_id = data['team_id']
        q_id_to_edit = data.get('edit_question_id')
        
        board_related = callback.data == "yes"
        
        new_question_data = {
            "text": data['new_text'],
            "field": data['new_field'],
            "time_variants": data.get('time_variants', {}),
            "board_related": board_related
        }
        
        questions = await db_get_team_questions(team_id)
        
        if q_id_to_edit:
            # --- Логика РЕДАКТИРОВАНИЯ ---
            found = False
            for i, q in enumerate(questions):
                if q['id'] == q_id_to_edit:
                    new_question_data["id"] = q_id_to_edit # Сохраняем старый ID
                    questions[i] = new_question_data
                    found = True
                    break
            if not found:
                await callback.answer("❌ Ошибка: вопрос для редактирования не найден.")
                return
            
            await db_update_team_questions(team_id, questions)
            await callback.answer("✅ Вопрос обновлён!")

        else:
            # --- Логика ДОБАВЛЕНИЯ ---
            new_id = max([q['id'] for q in questions] or [0]) + 1
            new_question_data["id"] = new_id
            questions.append(new_question_data)
            
            await db_update_team_questions(team_id, questions)
            await callback.answer("✅ Вопрос добавлен!")

        await show_questions_menu(callback.message, team_id, state)

    except Exception as e:
        logging.error(f"Критическая ошибка в process_final_save: {e}")
        await callback.answer("❌ Произошла критическая ошибка при сохранении.")
        await state.set_state(TeamQuestionsEdit.choosing_action)