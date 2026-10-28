"""
core/undo.py - removes the last exchange (your last message + everything SaiA answered after it)
from a chat session, so the chat button "Undo" can take it back.

I have not seen storage/database.py, so this works in this order:
  1. db.delete_last_exchange(session_id)   - used as-is if your Database class has such a method
  2. otherwise it opens the app's SQLite file, finds the messages table by its columns
     (session_id + role + content), and deletes the rows from your last message onward.
Returns {"user_text": str, "removed": int} or None when the session has nothing to undo.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")


def _candidate_sources(db: Any) -> Iterator[Any]:
    """Connections / file paths the Database object (or its module) already knows about."""
    holders = [db, type(db), sys.modules.get(type(db).__module__)]
    for holder in holders:
        if holder is None:
            continue
        try:
            items = list(vars(holder).items())
        except TypeError:
            continue
        for _, value in items:
            if isinstance(value, sqlite3.Connection):
                yield value
            elif isinstance(value, (str, os.PathLike)):
                text = os.fspath(value)
                if text.lower().endswith(_DB_SUFFIXES):
                    yield text
    # documented default of the project (see automation_service: "database.db")
    yield str(Path(__file__).resolve().parent.parent / "database.db")
    yield "database.db"


def _find_messages_table(conn: sqlite3.Connection) -> Optional[str]:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for name in tables:
        cols = {r[1].lower() for r in conn.execute(f'PRAGMA table_info("{name}")')}
        if {"session_id", "role", "content"} <= cols:
            return name
    return None


def _undo_on_connection(conn: sqlite3.Connection, session_id: str) -> Optional[Dict[str, Any]]:
    table = _find_messages_table(conn)
    if not table:
        return None
    rows = conn.execute(
        f'SELECT rowid, role, content FROM "{table}" WHERE session_id = ? ORDER BY rowid', (session_id,)
    ).fetchall()
    last_user = next((i for i in range(len(rows) - 1, -1, -1) if rows[i][1] == "user"), None)
    if last_user is None:
        return {"user_text": "", "removed": 0, "empty": True}
    doomed = [r[0] for r in rows[last_user:]]
    conn.executemany(f'DELETE FROM "{table}" WHERE rowid = ?', [(rid,) for rid in doomed])
    conn.commit()
    return {"user_text": rows[last_user][2] or "", "removed": len(doomed)}


def delete_last_exchange(db: Any, session_id: str) -> Optional[Dict[str, Any]]:
    native = getattr(db, "delete_last_exchange", None)
    if callable(native):
        result = native(session_id)
        if isinstance(result, dict):
            return result if result.get("removed", 1) else None
        return {"user_text": str(result or ""), "removed": 1} if result else None

    tried = set()
    last_error: Optional[Exception] = None
    for source in _candidate_sources(db):
        key = id(source) if isinstance(source, sqlite3.Connection) else str(source)
        if key in tried:
            continue
        tried.add(key)
        own = not isinstance(source, sqlite3.Connection)
        if own and not os.path.isfile(source):
            continue
        conn = sqlite3.connect(source, timeout=5) if own else source
        try:
            result = _undo_on_connection(conn, session_id)
        except sqlite3.Error as exc:
            last_error = exc
            result = None
        finally:
            if own:
                conn.close()
        if result is None:
            continue
        return None if result.get("empty") else result
    if last_error:
        raise RuntimeError(f"database error: {last_error}")
    raise RuntimeError("could not locate the messages table (add Database.delete_last_exchange)")
