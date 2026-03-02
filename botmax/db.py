"""
SQLite хранилище маппинга: tg_message_id -> max_message_id.
Персистентно между перезапусками.
"""

import sqlite3
import threading

_lock = threading.Lock()
_DB_PATH = "message_map.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS message_map "
        "(tg_id INTEGER PRIMARY KEY, max_id TEXT NOT NULL)"
    )
    return conn


def save_mapping(tg_id: int, max_id: str) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO message_map (tg_id, max_id) VALUES (?, ?)",
            (tg_id, max_id),
        )
        conn.commit()
        conn.close()


def get_max_id(tg_id: int) -> str | None:
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT max_id FROM message_map WHERE tg_id = ?", (tg_id,)
        ).fetchone()
        conn.close()
        return row[0] if row else None


def delete_mapping(tg_id: int) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM message_map WHERE tg_id = ?", (tg_id,))
        conn.commit()
        conn.close()


def get_all_mappings() -> list[tuple[int, str]]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT tg_id, max_id FROM message_map").fetchall()
        conn.close()
        return rows
