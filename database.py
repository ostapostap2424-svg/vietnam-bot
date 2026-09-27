# ──────────────────────────────────────────────
# РАБОТА С БАЗОЙ ДАННЫХ (SQLite)
# ──────────────────────────────────────────────

import aiosqlite
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path("vietnamese_bot.db")


async def init_db():
    """Создание всех таблиц при первом запуске."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            started_at TEXT DEFAULT (datetime('now')),
            current_day INTEGER DEFAULT 1,
            streak INTEGER DEFAULT 0,
            last_active TEXT
        );

        CREATE TABLE IF NOT EXISTS user_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            lesson_id TEXT,
            completed_at TEXT DEFAULT (datetime('now')),
            score INTEGER DEFAULT 0,
            mode TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day INTEGER,
            vn TEXT,
            transcription TEXT,
            ru TEXT,
            example_vn TEXT,
            example_tr TEXT,
            example_ru TEXT
        );

        CREATE TABLE IF NOT EXISTS srs_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            word_id INTEGER,
            next_review TEXT,
            interval_days INTEGER DEFAULT 1,
            ease_factor REAL DEFAULT 2.5,
            repetitions INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (word_id) REFERENCES words(id)
        );

        CREATE TABLE IF NOT EXISTS quiz_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            word_id INTEGER,
            correct INTEGER,
            answered_at TEXT DEFAULT (datetime('now'))
        );
        """)
        await db.commit()


async def get_or_create_user(user_id: int, username: str):
    """Получить пользователя или создать нового."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                await db.execute(
                    "UPDATE users SET last_active=datetime('now'), username=? "
                    "WHERE user_id=?",
                    (username, user_id)
                )
                await db.commit()
                return row
            else:
                await db.execute(
                    "INSERT INTO users (user_id, username, last_active) "
                    "VALUES (?, ?, datetime('now'))",
                    (user_id, username)
                )
                await db.commit()
                return (user_id, username, datetime.now(), 1, 0, datetime.now())


async def update_streak(user_id: int):
    """Обновление серии дней (стрик)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT last_active, streak FROM users WHERE user_id=?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return
            last_active, streak = row
            last_date = datetime.fromisoformat(last_active).date()
            today = datetime.now().date()
            diff = (today - last_date).days

            if diff == 0:
                new_streak = streak
            elif diff == 1:
                new_streak = streak + 1
            else:
                new_streak = 1

        await db.execute(
            "UPDATE users SET streak=?, last_active=datetime('now') "
            "WHERE user_id=?",
            (new_streak, user_id)
        )
        await db.commit()
        return new_streak


async def save_progress(user_id: int, lesson_id: str, score: int, mode: str):
    """Сохранить результат урока."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_progress (user_id, lesson_id, score, mode) "
            "VALUES (?, ?, ?, ?)",
            (user_id, lesson_id, score, mode)
        )
        async with db.execute(
            "SELECT current_day FROM users WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            current_day = row[0] if row else 1
            lesson_day = int(lesson_id.split("_")[0].replace("day", ""))
            if lesson_day >= current_day:
                await db.execute(
                    "UPDATE users SET current_day=? WHERE user_id=?",
                    (lesson_day + 1, user_id)
                )
        await db.commit()


async def add_word_to_srs(user_id: int, word_id: int):
    """Добавить слово в карточки повторения."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM srs_cards WHERE user_id=? AND word_id=?",
            (user_id, word_id)
        ) as cur:
            if await cur.fetchone():
                return
        tomorrow = (datetime.now() + timedelta(days=1)).isoformat()
        await db.execute(
            "INSERT INTO srs_cards (user_id, word_id, next_review) "
            "VALUES (?, ?, ?)",
            (user_id, word_id, tomorrow)
        )
        await db.commit()


async def process_srs_answer(user_id: int, word_id: int, correct: bool):
    """Обновить карточку после ответа (упрощённый SM-2)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, interval_days, ease_factor, repetitions "
            "FROM srs_cards WHERE user_id=? AND word_id=?",
            (user_id, word_id)
        ) as cur:
            card = await cur.fetchone()
            if not card:
                return
            card_id, interval, ease, reps = card

        if correct:
            reps += 1
            if reps == 1:
                interval = 1
            elif reps == 2:
                interval = 3
            elif reps == 3:
                interval = 7
            else:
                interval = int(interval * ease)
            ease = max(1.3, ease + 0.1)
        else:
            reps = 0
            interval = 1
            ease = max(1.3, ease - 0.2)

        next_review = (datetime.now() + timedelta(days=interval)).isoformat()
        await db.execute(
            "UPDATE srs_cards SET interval_days=?, ease_factor=?, "
            "repetitions=?, next_review=? WHERE id=?",
            (interval, ease, reps, next_review, card_id)
        )
        await db.commit()


async def get_due_cards(user_id: int, limit: int = 10):
    """Получить карточки, которые пора повторять."""
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().isoformat()
        async with db.execute(
            """
            SELECT w.id, w.vn, w.transcription, w.ru, w.example_vn,
                   w.example_tr, w.example_ru, s.id as card_id
            FROM srs_cards s
            JOIN words w ON s.word_id = w.id
            WHERE s.user_id = ? AND s.next_review <= ?
            ORDER BY s.next_review ASC
            LIMIT ?
            """,
            (user_id, now, limit)
        ) as cur:
            return await cur.fetchall()


async def log_quiz_answer(user_id: int, word_id: int, correct: bool):
    """Записать ответ на вопрос квиза."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO quiz_answers (user_id, word_id, correct) "
            "VALUES (?, ?, ?)",
            (user_id, word_id, 1 if correct else 0)
        )
        await db.commit()