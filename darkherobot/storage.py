"""Small SQLite store: group history, private memory, usage and budget.

Everything is local (one diddy.db file). Group history is pruned after
HISTORY_HOURS so the bot never keeps more than it needs.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

TASHKENT = timezone(timedelta(hours=5))


def day_key(now: float | None = None) -> str:
    return datetime.fromtimestamp(now or time.time(), TASHKENT).strftime("%Y-%m-%d")


def month_key(now: float | None = None) -> str:
    return datetime.fromtimestamp(now or time.time(), TASHKENT).strftime("%Y-%m")


@dataclass(frozen=True)
class HistoryLine:
    message_id: int
    ts: float
    author: str
    text: str
    is_bot: bool


class Storage:
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS group_messages (
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    ts REAL NOT NULL,
                    author TEXT NOT NULL,
                    text TEXT NOT NULL,
                    is_bot INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chat_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_group_messages_ts ON group_messages(chat_id, ts);

                CREATE TABLE IF NOT EXISTS private_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    text TEXT NOT NULL,
                    ts REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_private_turns_user ON private_turns(user_id, id);

                CREATE TABLE IF NOT EXISTS user_daily (
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    requests INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, day)
                );

                CREATE TABLE IF NOT EXISTS flags (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS spend (
                    month TEXT PRIMARY KEY,
                    usd REAL NOT NULL DEFAULT 0,
                    requests INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    searches INTEGER NOT NULL DEFAULT 0
                );
                """
            )

        self._migrate_persona()

    PERSONA_VERSION = 2

    def _migrate_persona(self) -> None:
        """When the bot's character changes, forget its old replies once, so the
        model does not copy its previous tone from memory. User messages stay."""
        with self._lock, self._db:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version >= self.PERSONA_VERSION:
                return
            self._db.execute("DELETE FROM private_turns")
            self._db.execute("DELETE FROM group_messages WHERE is_bot = 1")
            self._db.execute(f"PRAGMA user_version = {int(self.PERSONA_VERSION)}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- group history -------------------------------------------------
    def add_group_message(self, chat_id: int, message_id: int, author: str, text: str,
                          is_bot: bool = False, ts: float | None = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO group_messages VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, message_id, ts or time.time(), author[:64], text[:4000], int(is_bot)),
            )

    def group_history(self, chat_id: int, hours: int, limit: int) -> list[HistoryLine]:
        since = time.time() - hours * 3600
        with self._lock:
            rows = self._db.execute(
                "SELECT message_id, ts, author, text, is_bot FROM group_messages "
                "WHERE chat_id = ? AND ts >= ? ORDER BY ts DESC LIMIT ?",
                (chat_id, since, limit),
            ).fetchall()
        return [HistoryLine(r["message_id"], r["ts"], r["author"], r["text"], bool(r["is_bot"])) for r in reversed(rows)]

    def prune(self, history_hours: int, private_keep: int) -> None:
        cutoff = time.time() - history_hours * 3600
        with self._lock, self._db:
            self._db.execute("DELETE FROM group_messages WHERE ts < ?", (cutoff,))
            self._db.execute(
                "DELETE FROM private_turns WHERE id NOT IN ("
                " SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY id DESC) AS rn"
                " FROM private_turns) WHERE rn <= ?)",
                (max(0, private_keep) * 2,),
            )
            self._db.execute("DELETE FROM user_daily WHERE day < ?", (day_key(time.time() - 7 * 86400),))

    # ---- private memory ------------------------------------------------
    def add_private_turn(self, user_id: int, role: str, text: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO private_turns (user_id, role, text, ts) VALUES (?, ?, ?, ?)",
                (user_id, role, (text or "")[:6000], time.time()),
            )

    def private_turns(self, user_id: int, turns: int) -> list[tuple[str, str]]:
        if turns <= 0:
            return []
        with self._lock:
            rows = self._db.execute(
                "SELECT role, text FROM private_turns WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, turns * 2),
            ).fetchall()
        return [(r["role"], r["text"]) for r in reversed(rows)]

    def clear_private(self, user_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM private_turns WHERE user_id = ?", (user_id,))

    # ---- flags (survive restarts) ------------------------------------
    def get_flag(self, name: str, default: str = "") -> str:
        with self._lock:
            row = self._db.execute("SELECT value FROM flags WHERE name = ?", (name,)).fetchone()
        return row["value"] if row else default

    def set_flag(self, name: str, value: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO flags (name, value) VALUES (?, ?) "
                             "ON CONFLICT(name) DO UPDATE SET value = excluded.value", (name, value))

    # ---- limits and budget --------------------------------------------
    def take_daily_request(self, user_id: int, limit: int) -> bool:
        """Atomically count one request; False if the user is already at the limit."""
        day = day_key()
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT requests FROM user_daily WHERE user_id = ? AND day = ?", (user_id, day)
            ).fetchone()
            used = row["requests"] if row else 0
            if used >= limit:
                return False
            self._db.execute(
                "INSERT INTO user_daily (user_id, day, requests) VALUES (?, ?, 1) "
                "ON CONFLICT(user_id, day) DO UPDATE SET requests = requests + 1",
                (user_id, day),
            )
        return True

    def refund_daily_request(self, user_id: int) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE user_daily SET requests = MAX(0, requests - 1) WHERE user_id = ? AND day = ?",
                (user_id, day_key()),
            )

    def daily_used(self, user_id: int) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT requests FROM user_daily WHERE user_id = ? AND day = ?", (user_id, day_key())
            ).fetchone()
        return row["requests"] if row else 0

    def add_spend(self, usd: float, input_tokens: int, cached_tokens: int,
                  output_tokens: int, searches: int) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO spend (month, usd, requests, input_tokens, cached_tokens, output_tokens, searches) "
                "VALUES (?, ?, 1, ?, ?, ?, ?) ON CONFLICT(month) DO UPDATE SET "
                "usd = usd + excluded.usd, requests = requests + 1, "
                "input_tokens = input_tokens + excluded.input_tokens, "
                "cached_tokens = cached_tokens + excluded.cached_tokens, "
                "output_tokens = output_tokens + excluded.output_tokens, "
                "searches = searches + excluded.searches",
                (month_key(), usd, input_tokens, cached_tokens, output_tokens, searches),
            )

    def month_spend(self) -> dict:
        with self._lock:
            row = self._db.execute("SELECT * FROM spend WHERE month = ?", (month_key(),)).fetchone()
        if not row:
            return {"month": month_key(), "usd": 0.0, "requests": 0, "input_tokens": 0,
                    "cached_tokens": 0, "output_tokens": 0, "searches": 0}
        return dict(row)
