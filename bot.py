# ──────────────────────────────────────────────
# ГЛАВНЫЙ ФАЙЛ БОТА
# ──────────────────────────────────────────────

import asyncio
import logging
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import database as db
import tts
import admin
from lessons import get_lesson, get_all_lesson_ids, seed_words_to_db
from config import BOT_TOKEN

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

main_router = Router()
dp.include_router(main_router)
dp.include_router(admin.router)


class LessonState(StatesGroup):
    choosing_mode = State()
    lesson_active = State()
    quiz_active = State()
    reviewing_srs = State()


@main_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username or "")
    await state.clear()

    streak = user[4] if len(user) > 4 else 0
    current_day = user[3] if len(user) > 3 else 1

    text = (
        f"🇻🇳 <b>Chào {message.from_user.first_name}!</b>\n\n"
        f"🔥 Твой стрик: <b>{streak}</b> дн.\n"
        f"📍 Сейчас ты на: <b>День {current_day}</b>\n\n"
        f"Что хочешь сделать?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📖 Урок День {current_day}", callback_data=f"lesson_day{current_day}")],
        [InlineKeyboardButton(text="🔁 Повторить слова (SRS)", callback_data="srs_start")],
        [InlineKeyboardButton(text="📊 Мой прогресс", callback_data="stats")],
        [InlineKeyboardButton(text="📚 Все уроки", callback_data="all_lessons")],
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@main_router.message(Command("stats"))
@main_router.callback_query(F.data == "stats")
async def cmd_stats(callback_or_message, state: FSMContext = None):
    msg = callback_or_message.message if isinstance(callback_or_message, CallbackQuery) else callback_or_message
    user_id = msg.from_user.id
    user = await db.get_or_create_user(user_id, msg.from_user.username or "")

    async with __import__("aiosqlite").connect(db.DB_PATH) as db_conn:
        async with db_conn.execute(
            "SELECT COUNT(*) FROM user_progress WHERE user_id=?", (user_id,)
        ) as cur:
            lessons_done = (await cur.fetchone())[0]
        async with db_conn.execute(
            "SELECT COUNT(*) FROM srs_cards WHERE user_id=?", (user_id,)
        ) as cur:
            cards_total = (await cur.fetchone())[0]
        async with db_conn.execute(
            "SELECT COUNT(*) FROM quiz_answers WHERE user_id=? AND correct=1", (user_id,)
        ) as cur:
            correct_answers = (await cur.fetchone())[0]
        async with db_conn.execute(
            "SELECT COUNT(*) FROM quiz_answers WHERE user_id=?", (user_id,)
        ) as cur:
            total_answers = (await cur.fetchone())[0]

    accuracy = int(correct_answers / total_answers * 100) if total_answers > 0 else 0

    text = (
        f"📊 <b>Твоя статистика</b>\n\n"
        f"🔥 Стрик: <b>{user[4]}</b> дн.\n"
        f"📖 Пройдено уроков: <b>{lessons_done}</b>\n"
        f"🎴 Карточек в SRS: <b>{cards_total}</b>\n"
        f"🎯 Точность ответов: <b>{accuracy}%</b> ({correct_answers}/{total_answers})\n"
        f"📍 Текущий день: <b>{user[3]}</b>"
    )
    if isinstance(callback_or_message, CallbackQuery):
        await callback_or_message.message.edit_text(text, parse_mode="HTML")
        await callback_or_message.answer()
    else:
        await msg.answer(text, parse_mode="HTML")


@main_router.callback_query(F.data == "all_lessons")
async def all_lessons(callback: CallbackQuery):
    ids = get_all_lesson_ids()
    buttons = []
    for lid in ids:
        lesson = get_lesson(lid)
        day_num = lid.replace("day", "")
        buttons.append([InlineKeyboardButton(
            text=f"День {day_num}: {lesson['title']}",
            callback_data=f"lesson_{lid}"
        )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("📚 <b>Выбери урок:</b>", parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@main_router.callback_query(F.data.startswith("lesson_day"))
@main_router.callback_query(F.data.startswith("lesson_"))
async def start_lesson(callback: CallbackQuery, state: FSMContext):
    lesson_id = callback.data.replace("lesson_", "")
    lesson = get_lesson(lesson_id)
    if not lesson:
        await callback.answer("Урок не найден", show_alert=True)
        return

    await state.update_data(lesson_id=lesson_id, quiz_index=0, score=0)
    await state.set_state(LessonState.lesson_active)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Экспресс (5 мин)", callback_data="mode_express")],
        [InlineKeyboardButton(text="⭐ Стандарт (15 мин)", callback_data="mode_standard")],
        [InlineKeyboardButton(text="🔥 Погружение (25 мин)", callback_data="mode_deep")],
    ])

    goals_text = "\n".join(f"  ✅ {g}" for g in lesson["goals"])
    text = (
        f"📖 <b>День {lesson_id.replace('day', '')}: {lesson['title']}</b>\n\n"
        f"🎯 <b>Цели урока:</b>\n{goals_text}\n\n"
        f"Сколько времени у тебя есть?"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@main_router.callback_query(F.data.startswith("mode_"), LessonState.lesson_active)
async def choose_mode(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.replace("mode_", "")
    data = await state.get_data()
    lesson_id = data["lesson_id"]
    lesson = get_lesson(lesson_id)

    await state.update_data(mode=mode)

    vocab = lesson["vocab"]
    if mode == "express":
        vocab = vocab[:6]
        phrases = lesson["phrases"][:2]
    elif mode == "standard":
        vocab = vocab[:10]
        phrases = lesson["phrases"]
    else:
        phrases = lesson["phrases"]

    vocab_lines = ""
    for vn, tr, ru, ex_vn, ex_tr, ex_ru in vocab:
        vocab_lines += f"  🇻🇳 <b>{vn}</b> (<i>{tr}</i>) — {ru}\n"

    phrase_lines = ""
    for vn, tr, ru in phrases:
        phrase_lines += f"  🇻🇳 <b>{vn}</b>\n  🗣 <i>{tr}</i>\n  🇷🇺 {ru}\n\n"

    grammar_lines = ""
    for title, content in lesson["grammar"]:
        grammar_lines += f"\n🧱 <b>{title}</b>\n{content}\n"

    deep_block = ""
    if mode == "deep":
        deep_block = f"\n🏮 <b>Культура</b>\n{lesson['culture']}\n"

    mode_label = {"express": "⚡ Экспресс", "standard": "⭐ Стандарт", "deep": "🔥 Погружение"}[mode]

    text = (
        f"📖 <b>День {lesson_id.replace('day', '')}</b> [{mode_label}]\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n📝 <b>НОВЫЕ СЛОВА</b>\n{vocab_lines}"
        f"\n🗣 <b>ФРАЗЫ</b>\n{phrase_lines}"
        f"{grammar_lines}{deep_block}"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Готов проверить себя?"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧠 Начать квиз", callback_data="start_quiz")]
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    # 🔊 ОЗВУЧКА
    try:
        audio_words = await tts.speak_words(vocab)
        await callback.message.answer_voice(
            voice=audio_words,
            caption="🔊 <b>Новые слова</b> — послушай и повтори вслух",
            parse_mode="HTML"
        )
        audio_phrases = await tts.speak_phrases(phrases)
        await callback.message.answer_voice(
            voice=audio_phrases,
            caption="🔊 <b>Фразы</b> — обрати внимание на интонацию",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"TTS error: {e}")

    await callback.answer()


@main_router.callback_query(F.data == "start_quiz", LessonState.lesson_active)
async def start_quiz(callback: CallbackQuery, state: FSMContext):
    await state.set_state(LessonState.quiz_active)
    await state.update_data(quiz_index=0, score=0)
    data = await state.get_data()
    lesson = get_lesson(data["lesson_id"])
    await show_question(callback, state, lesson, 0)


async def show_question(callback: CallbackQuery, state: FSMContext, lesson, q_idx):
    quiz = lesson["quiz"]
    q = quiz[q_idx]
    buttons = []
    for i, opt in enumerate(q["opts"]):
        buttons.append([InlineKeyboardButton(text=opt, callback_data=f"ans_{q_idx}_{i}")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(
        f"🧠 <b>Вопрос {q_idx + 1}/{len(quiz)}</b>\n\n{q['q']}",
        parse_mode="HTML", reply_markup=kb
    )


@main_router.callback_query(F.data.startswith("ans_"), LessonState.quiz_active)
async def process_answer(callback: CallbackQuery, state: FSMContext):
    _, q_idx_s, a_idx_s = callback.data.split("_")
    q_idx, a_idx = int(q_idx_s), int(a_idx_s)
    data = await state.get_data()
    lesson = get_lesson(data["lesson_id"])
    q = lesson["quiz"][q_idx]

    user_id = callback.from_user.id
    word_id_in_vocab = q["word_idx"]

    async with __import__("aiosqlite").connect(db.DB_PATH) as db_conn:
        async with db_conn.execute(
            "SELECT id FROM words WHERE day=? AND vn=?",
            (int(data['lesson_id'].replace('day', '')),
             lesson["vocab"][word_id_in_vocab][0])
        ) as cur:
            row = await cur.fetchone()
            word_id = row[0] if row else None

    correct = a_idx == q["correct"]
    if correct:
        data["score"] = data.get("score", 0) + 1
        feedback = "✅ Верно! +10 очков"
    else:
        feedback = f"❌ Правильно: <b>{q['opts'][q['correct']]}</b>"

    await state.update_data(score=data["score"])

    if word_id:
        await db.log_quiz_answer(user_id, word_id, correct)
        await db.add_word_to_srs(user_id, word_id)
        await db.process_srs_answer(user_id, word_id, correct)

    next_q = q_idx + 1
    if next_q < len(lesson["quiz"]):
        await state.update_data(quiz_index=next_q)
        await callback.answer()
        q2 = lesson["quiz"][next_q]
        buttons = []
        for i, opt in enumerate(q2["opts"]):
            buttons.append([InlineKeyboardButton(text=opt, callback_data=f"ans_{next_q}_{i}")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.edit_text(
            f"{feedback}\n\n➖➖➖➖➖\n\n"
            f"🧠 <b>Вопрос {next_q + 1}/{len(lesson['quiz'])}</b>\n\n{q2['q']}",
            parse_mode="HTML", reply_markup=kb
        )
    else:
        await db.save_progress(user_id, data["lesson_id"], data["score"], data["mode"])
        await db.update_streak(user_id)

        total = len(lesson["quiz"])
        score = data["score"]
        pct = int(score / total * 100)
        emoji = "🏆" if pct >= 75 else "💪" if pct >= 50 else "📚"

        goals_text = "\n".join(f"  ✅ {g}" for g in lesson["goals"])
        text = (
            f"🎉 <b>Урок завершён!</b>\n\n"
            f"{emoji} Результат: <b>{score}/{total}</b> ({pct}%)\n\n"
            f"📌 Что ты теперь умеешь:\n{goals_text}\n\n"
            f"🔥 Стрик обновлён! Возвращайся завтра."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="home")],
            [InlineKeyboardButton(text="🔁 Повторить урок", callback_data=f"lesson_{data['lesson_id']}")],
        ])
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        await state.clear()
        await callback.answer()


@main_router.callback_query(F.data == "home")
async def go_home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await cmd_start(callback.message, state)
    await callback.answer()


@main_router.callback_query(F.data == "srs_start")
async def srs_start(callback: CallbackQuery, state: FSMContext):
    cards = await db.get_due_cards(callback.from_user.id, limit=10)
    if not cards:
        await callback.message.edit_text(
            "🎉 <b>Нет слов на повторение!</b>\n\n"
            "Все карточки ещё впереди.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 В меню", callback_data="home")]
            ])
        )
        await callback.answer()
        return

    await state.set_state(LessonState.reviewing_srs)
    await state.update_data(srs_cards=cards, srs_index=0, srs_score=0)
    await show_srs_card(callback, state, cards[0], 0, len(cards))


async def show_srs_card(callback, state, card, idx, total):
    word_id, vn, tr, ru, ex_vn, ex_tr, ex_ru, card_id = card
    text = (
        f"🔁 <b>Повторение {idx + 1}/{total}</b>\n\n"
        f"🇻🇳 <b>{vn}</b> (<i>{tr}</i>)\n"
        f"🇷🇺 <i>{ru}</i>\n\n"
        f"💬 <i>{ex_vn}</i>\n"
        f"🗣 <i>{ex_tr}</i>\n"
        f"🇷🇺 {ex_ru}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="😓 Не помню", callback_data="srs_hard"),
            InlineKeyboardButton(text="🙂 Помню", callback_data="srs_good"),
        ]
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    try:
        audio = await tts.speak(f"{vn} ... {ex_vn}")
        await callback.message.answer_voice(voice=audio)
    except Exception as e:
        logging.error(f"TTS error: {e}")

    await callback.answer()


@main_router.callback_query(F.data.in_(["srs_hard", "srs_good"]), LessonState.reviewing_srs)
async def srs_answer(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cards = data["srs_cards"]
    idx = data["srs_index"]
    card = cards[idx]
    word_id = card[0]
    user_id = callback.from_user.id

    hard = callback.data == "srs_hard"
    await db.process_srs_answer(user_id, word_id, correct=not hard)
    await db.log_quiz_answer(user_id, word_id, correct=not hard)

    score = data.get("srs_score", 0) + (0 if hard else 1)
    next_idx = idx + 1

    if next_idx < len(cards):
        await state.update_data(srs_index=next_idx, srs_score=score)
        await show_srs_card(callback, state, cards[next_idx], next_idx, len(cards))
    else:
        total = len(cards)
        pct = int(score / total * 100)
        text = (
            f"🎉 <b>Сессия повторения завершена!</b>\n\n"
            f"🏆 Вспомнил: <b>{score}/{total}</b> ({pct}%)"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 В меню", callback_data="home")]
        ])
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        await state.clear()
        await callback.answer()


async def main():
    await db.init_db()
    await seed_words_to_db()
    print("🤖 Бот запущен! Нажми /start в Telegram.")
    print("🛠 Админ-панель: /admin")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())