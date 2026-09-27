# ──────────────────────────────────────────────
# АДМИН-ПАНЕЛЬ
# ──────────────────────────────────────────────

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import aiosqlite
from datetime import datetime

from config import ADMIN_IDS
from database import DB_PATH


router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


class BroadcastState(StatesGroup):
    waiting_message = State()


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У тебя нет доступа.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Рассылка всем", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton(text="📚 Перезагрузить уроки", callback_data="admin_reload")],
    ])
    await message.answer(
        "🛠 <b>Админ-панель</b>\n\nВыбери действие:",
        parse_mode="HTML", reply_markup=kb
    )


@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total_users = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE last_active >= date('now', '-1 day')"
        ) as cur:
            active_today = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE last_active >= date('now', '-7 day')"
        ) as cur:
            active_week = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM user_progress") as cur:
            total_lessons = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM words") as cur:
            total_words = (await cur.fetchone())[0]
        async with db.execute("SELECT AVG(score) FROM user_progress") as cur:
            avg_score = (await cur.fetchone())[0] or 0

    text = (
        f"📊 <b>Общая статистика</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"🔥 Активных сегодня: <b>{active_today}</b>\n"
        f"📅 Активных за неделю: <b>{active_week}</b>\n"
        f"📖 Пройдено уроков: <b>{total_lessons}</b>\n"
        f"📝 Слов в базе: <b>{total_words}</b>\n"
        f"🎯 Средний балл: <b>{avg_score:.1f}</b>"
    )
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, username, current_day, streak, last_active "
            "FROM users ORDER BY last_active DESC LIMIT 20"
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        text = "👥 Пока нет пользователей."
    else:
        lines = ["👥 <b>Последние 20 пользователей:</b>\n"]
        for user_id, username, day, streak, last in rows:
            name = f"@{username}" if username else f"ID {user_id}"
            lines.append(f"  • {name} — День {day}, 🔥{streak}, был {last[:10]}")
        text = "\n".join(lines)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")]
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(BroadcastState.waiting_message)
    await callback.message.edit_text(
        "📢 <b>Рассылка</b>\n\n"
        "Отправь сообщение, которое получат ВСЕ пользователи.\n"
        "Поддерживается HTML-разметка.\n\n"
        "Для отмены: /cancel",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BroadcastState.waiting_message)
async def admin_broadcast_send(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            users = await cur.fetchall()

    sent = 0
    failed = 0
    for (user_id,) in users:
        try:
            await message.bot.send_message(
                user_id, message.text, parse_mode="HTML"
            )
            sent += 1
        except Exception:
            failed += 1

    await state.clear()
    await message.answer(
        f"✅ Рассылка завершена!\n"
        f"📨 Доставлено: <b>{sent}</b>\n"
        f"❌ Ошибок: <b>{failed}</b>",
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admin_reload")
async def admin_reload(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    import importlib
    import lessons
    importlib.reload(lessons)
    await lessons.seed_words_to_db()

    await callback.message.edit_text(
        "✅ <b>Уроки перезагружены!</b>\n\n"
        f"Всего уроков: <b>{len(lessons.get_all_lesson_ids())}</b>",
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Рассылка всем", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton(text="📚 Перезагрузить уроки", callback_data="admin_reload")],
    ])
    await callback.message.edit_text(
        "🛠 <b>Админ-панель</b>\n\nВыбери действие:",
        parse_mode="HTML", reply_markup=kb
    )
    await callback.answer()


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено.")