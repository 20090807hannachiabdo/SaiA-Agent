"""
SaiA Database Manager (Self-Healing & Auto-Migrating)
Handles SQLite disk persistence, automated scheduled workflows,
and cross-session memory integration.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import uuid
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

APP_NAME = "SaiA"
SCHEMA_VERSION = 5
PREF_COLUMNS = ("theme", "language", "user_nickname", "selected_model", "active_role")

CORE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS user_preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    theme TEXT DEFAULT 'light',
    language TEXT DEFAULT 'ar',
    user_nickname TEXT DEFAULT '',
    selected_model TEXT DEFAULT 'gemini-3.5-flash-lite',
    active_role TEXT DEFAULT 'student'
);
INSERT OR IGNORE INTO user_preferences (id) VALUES (1);

CREATE TABLE IF NOT EXISTS ai_model (
    id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    description TEXT,
    version TEXT,
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO ai_model (id, model_name, description, version) VALUES
('gemini-3.5-flash-lite', 'Gemini 3.5 Flash Lite', 'Ultra-fast, tool-optimized model', '3.5.0'),
('gemini-3.5-flash', 'Gemini 3.5 Flash', 'Deep reasoning and workflow orchestration', '3.5.0'),
('gemma-2-2b-it', 'Gemma 2 2B (Local/Fast)', 'Google ultra-lightweight & fast open model', '2.0.0'),
('gemma-2-9b-it', 'Gemma 2 9B (Local/Smart)', 'Google high-performance balanced open model', '2.0.0'),
('gemma-2-27b-it', 'Gemma 2 27B (Local/Pro)', 'Google high-capacity deep reasoning open model', '2.0.0');

CREATE TABLE IF NOT EXISTS specialist_roles (
    id TEXT PRIMARY KEY,
    name_ar TEXT NOT NULL,
    name_en TEXT NOT NULL,
    icon TEXT DEFAULT '⚡',
    system_prompt_addon TEXT NOT NULL,
    is_active INTEGER DEFAULT 1
);

INSERT OR IGNORE INTO specialist_roles (id, name_ar, name_en, icon, system_prompt_addon) VALUES
('student', 'طالب متفوق دراسياً', 'Genius Student', '🎓', 
 'ROLE: Academic Genius & Master Tutor. Approach topics using the Feynman Technique and structured proofs. Provide step-by-step breakdowns, interactive self-test flashcards, and conceptual clarity to help the user achieve perfect academic performance.'),
('coder', 'مهندس برمجيات محترف', 'Senior Coder', '💻', 
 'ROLE: Principal Software Engineer. Focus on clean architecture, production-ready code, Big-O efficiency, error resilience, and security best practices.'),
('thinker', 'المفكر الاستراتيجي', 'Deep Thinker', '🧠', 
 'ROLE: Strategic Analyst & Philosophy Thinker. Break down intricate problems into first principles, analyze cognitive biases, and map edge-case risks.'),
('career', 'مرشد مهني واحترافي', 'Career Mentor', '🧭', 
 'ROLE: Executive Career Coach. Deliver actionable career progression strategies, resume optimization, and interview preparation.'),
('general', 'المساعد الشامل', 'General Assistant', '⚡', 
 'ROLE: Universal Autonomous Assistant. Fast, direct, versatile, and dedicated to executing user instructions safely on Windows.');

CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    role_id TEXT DEFAULT 'student',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    file_path TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_longterm_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT DEFAULT 'general',
    memory_key TEXT NOT NULL UNIQUE,
    memory_value TEXT NOT NULL,
    importance INTEGER DEFAULT 3,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    trigger_type TEXT DEFAULT 'interval',
    trigger_value TEXT NOT NULL,
    prompt TEXT NOT NULL,
    role_id TEXT DEFAULT 'student',
    is_active INTEGER DEFAULT 1,
    last_run DATETIME,
    next_run DATETIME,
    workflow_json TEXT DEFAULT '{}'
);
"""


def get_app_data_dir() -> str:
    if sys.platform == "win32":
        base = os.getenv("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.getenv("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _resource_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "storage")
    return os.path.dirname(os.path.abspath(__file__))


class Database:
    def __init__(
        self,
        sql_path_name: str = "database.sql",
        db_path_name: str = "database.db",
        DEV: bool = False,
    ):
        self.DEV = DEV
        self.sql_path = os.path.join(_resource_dir(), sql_path_name)
        self.db_path = os.path.join(get_app_data_dir(), db_path_name)

    def exists(self) -> bool:
        return os.path.isfile(self.db_path)

    def _read_sql(self) -> str:
        if os.path.isfile(self.sql_path):
            try:
                with open(self.sql_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        return content
            except Exception:
                pass
        return CORE_SCHEMA_SQL

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_tables(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(self._read_sql())
            cols = [r[1] for r in conn.execute("PRAGMA table_info(user_preferences)").fetchall()]
            if "active_role" not in cols:
                conn.execute("ALTER TABLE user_preferences ADD COLUMN active_role TEXT DEFAULT 'student'")

            # Guarantee all Gemma models are present in pre-existing databases
            gemma_models = [
                ('gemma-2-2b-it', 'Gemma 2 2B (Local/Fast)', 'Google ultra-lightweight & fast open model', '2.0.0'),
                ('gemma-2-9b-it', 'Gemma 2 9B (Local/Smart)', 'Google high-performance balanced open model', '2.0.0'),
                ('gemma-2-27b-it', 'Gemma 2 27B (Local/Pro)', 'Google high-capacity deep reasoning open model', '2.0.0'),
            ]
            for m in gemma_models:
                conn.execute(
                    "INSERT OR IGNORE INTO ai_model (id, model_name, description, version) VALUES (?, ?, ?, ?)",
                    m,
                )

            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        finally:
            conn.close()

    def auto_sync_memories_from_history(self) -> None:
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute("SELECT content FROM chat_messages WHERE role = 'user' ORDER BY id ASC").fetchall()
                with conn:
                    for r in rows:
                        text = r["content"]
                        m_age = re.search(r"(?:عمري|عمرِي)\s*(?:هو)?\s*[:=]?\s*(\d+)(?:\s*سنة|\s*عام)?", text)
                        if m_age:
                            conn.execute(
                                """
                                INSERT INTO user_longterm_memory (category, memory_key, memory_value, importance)
                                VALUES ('personal', 'user_age', ?, 5)
                                ON CONFLICT(memory_key) DO UPDATE SET memory_value = excluded.memory_value
                                """,
                                (f"{m_age.group(1)} سنة",),
                            )
                        m_name = re.search(r"(?:اسمي|اسمِي)\s*(?:هو)?\s*[:=]?\s*([^\n,.!?]+)", text)
                        if m_name and len(m_name.group(1).split()) <= 4:
                            conn.execute(
                                """
                                INSERT INTO user_longterm_memory (category, memory_key, memory_value, importance)
                                VALUES ('personal', 'user_name', ?, 5)
                                ON CONFLICT(memory_key) DO UPDATE SET memory_value = excluded.memory_value
                                """,
                                (m_name.group(1).strip(),),
                            )
        except Exception:
            pass

    def create_database(self, force: bool = False) -> None:
        if force and self.exists():
            try:
                os.remove(self.db_path)
            except OSError:
                pass

        if not self.exists():
            tmp_path = self.db_path + ".tmp"
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

            conn = sqlite3.connect(tmp_path)
            try:
                conn.executescript(self._read_sql())
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.commit()
            finally:
                conn.close()
            os.replace(tmp_path, self.db_path)
        else:
            self._ensure_tables()

        self.auto_sync_memories_from_history()

    # ------------------ Preferences ------------------
    def get_preferences(self) -> Dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM user_preferences WHERE id = 1").fetchone()
        return dict(row) if row else {}

    def set_preferences(self, **values: Any) -> None:
        filtered = {k: v for k, v in values.items() if k in PREF_COLUMNS}
        if not filtered:
            return
        assignments = ", ".join(f"{col} = ?" for col in filtered)
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    f"UPDATE user_preferences SET {assignments} WHERE id = 1",
                    tuple(filtered.values()),
                )

    # ------------------ Specialist Roles ------------------
    def list_roles(self) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, name_ar, name_en, icon, system_prompt_addon FROM specialist_roles WHERE is_active = 1 ORDER BY rowid"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_role(self, role_id: str) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id, name_ar, name_en, icon, system_prompt_addon FROM specialist_roles WHERE id = ?",
                (role_id,),
            ).fetchone()
        return dict(row) if row else None

    # ------------------ Sessions & History ------------------
    def create_session(self, title: str = "محادثة جديدة", role_id: str = "student") -> str:
        session_id = str(uuid.uuid4())
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO chat_sessions (id, title, role_id) VALUES (?, ?, ?)",
                    (session_id, title, role_id),
                )
        return session_id

    def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, title, role_id, created_at, updated_at FROM chat_sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_session(self) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id, title, role_id, created_at, updated_at FROM chat_sessions ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def update_session_title(self, session_id: str, title: str) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "UPDATE chat_sessions SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (title.strip(), session_id),
                )

    def delete_session(self, session_id: str) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        file_path: Optional[str] = None,
    ) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO chat_messages (session_id, role, content, file_path) VALUES (?, ?, ?, ?)",
                    (session_id, role, content, file_path),
                )
                conn.execute(
                    "UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (session_id,),
                )

    def get_session_history(self, session_id: str, limit: int = 30) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT role, content, file_path, created_at FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_past_sessions_history(self, exclude_session_id: str, limit: int = 15) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT role, content, created_at 
                FROM chat_messages 
                WHERE session_id != ? 
                ORDER BY id DESC LIMIT ?
                """,
                (exclude_session_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ------------------ Long-Term Memory ------------------
    def get_longterm_memories(self, limit: int = 25) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, category, memory_key, memory_value, importance FROM user_longterm_memory ORDER BY importance DESC, updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_longterm_memory(
        self,
        memory_key: str,
        memory_value: str,
        category: str = "general",
        importance: int = 3,
    ) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO user_longterm_memory (category, memory_key, memory_value, importance)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(memory_key) DO UPDATE SET
                    memory_value = excluded.memory_value,
                    category = excluded.category,
                    importance = excluded.importance,
                    updated_at = CURRENT_TIMESTAMP
                    """,
                    (category, memory_key, memory_value, importance),
                )

    # ------------------ Automation Workflows ------------------
    def upsert_scheduled_task(
        self,
        task_id: str,
        title: str,
        trigger_type: str,
        trigger_val: str,
        prompt: str,
        role_id: str,
        workflow_json: str = "{}",
    ) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO scheduled_tasks (id, title, trigger_type, trigger_value, prompt, role_id, workflow_json, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    trigger_type = excluded.trigger_type,
                    trigger_value = excluded.trigger_value,
                    prompt = excluded.prompt,
                    role_id = excluded.role_id,
                    workflow_json = excluded.workflow_json
                    """,
                    (task_id, title, trigger_type, trigger_val, prompt, role_id, workflow_json),
                )

    def list_scheduled_tasks(self) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM scheduled_tasks ORDER BY rowid DESC").fetchall()
        return [dict(r) for r in rows]

    def toggle_scheduled_task(self, task_id: str, active: bool) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("UPDATE scheduled_tasks SET is_active = ? WHERE id = ?", (1 if active else 0, task_id))

    def delete_scheduled_task(self, task_id: str) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))

    def update_task_execution(self, task_id: str, last_run: str) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("UPDATE scheduled_tasks SET last_run = ? WHERE id = ?", (last_run, task_id))

    # ------------------ Models ------------------
    def list_models(self) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT id, model_name, description, version FROM ai_model ORDER BY rowid").fetchall()
        return [dict(r) for r in rows]
