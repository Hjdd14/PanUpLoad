"""SQLite-backed local storage for cloud drive credentials and configuration.

All sensitive fields are encrypted before storage (caller's responsibility).
"""

import sqlite3
import os
import json
from contextlib import contextmanager
from typing import Any

from panupdate.drivers.base import AccountInfo


class Database:
    """Manages the local SQLite database."""

    def __init__(self, data_dir: str):
        self._db_path = os.path.join(data_dir, "panupdate.db")
        os.makedirs(data_dir, exist_ok=True)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        """Create tables if they don't exist."""
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider    TEXT NOT NULL,
                    account_name TEXT NOT NULL,
                    access_token_enc  TEXT NOT NULL DEFAULT '',
                    refresh_token_enc TEXT NOT NULL DEFAULT '',
                    expires_at  REAL NOT NULL DEFAULT 0,
                    extra_enc   TEXT NOT NULL DEFAULT '{}',
                    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS config (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path   TEXT NOT NULL,
                    file_size   INTEGER NOT NULL DEFAULT 0,
                    provider    TEXT NOT NULL,
                    remote_path TEXT NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'pending',
                    error_msg   TEXT,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS backup_jobs (
                    id              TEXT PRIMARY KEY,
                    source_paths    TEXT NOT NULL DEFAULT '[]',
                    status          TEXT NOT NULL DEFAULT 'pending',
                    total_tasks     INTEGER NOT NULL DEFAULT 0,
                    completed_tasks INTEGER NOT NULL DEFAULT 0,
                    success_count   INTEGER NOT NULL DEFAULT 0,
                    fail_count      INTEGER NOT NULL DEFAULT 0,
                    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                    completed_at    TEXT
                );

                CREATE TABLE IF NOT EXISTS backup_tasks (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id      TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    file_name   TEXT NOT NULL,
                    file_size   INTEGER NOT NULL DEFAULT 0,
                    provider    TEXT NOT NULL,
                    remote_dir  TEXT NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'pending',
                    error       TEXT,
                    file_id     TEXT,
                    progress    REAL NOT NULL DEFAULT 0.0,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
                );
            """)

    # --- Account operations ---

    def save_account(self, info: AccountInfo, encrypt: Any) -> int:
        """Insert or update an account.

        - Same provider+name+token -> update existing (re-login)
        - Same provider+name but different token -> add suffix (new account)
        - Otherwise -> insert as new
        """
        extra_json = json.dumps(info.extra, ensure_ascii=False)
        encrypted_extra = encrypt(extra_json) if extra_json != '{}' else ''
        encrypted_token = encrypt(info.access_token)

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, access_token_enc FROM accounts WHERE provider=? AND account_name=?",
                (info.provider, info.account_name),
            ).fetchone()

            if existing:
                if existing["access_token_enc"] == encrypted_token:
                    # Re-login same account -> just update refresh/expiry
                    conn.execute(
                        """UPDATE accounts SET
                           refresh_token_enc=?, expires_at=?,
                           extra_enc=?, updated_at=datetime('now')
                           WHERE id=?""",
                        (encrypt(info.refresh_token), info.expires_at,
                         encrypted_extra, existing["id"]),
                    )
                    return existing["id"]
                # Same name, different token -> add suffix
                base = info.account_name
                n = 2
                while conn.execute(
                    "SELECT id FROM accounts WHERE provider=? AND account_name=?",
                    (info.provider, f"{base} ({n})"),
                ).fetchone():
                    n += 1
                info.account_name = f"{base} ({n})"

            cur = conn.execute(
                """INSERT INTO accounts
                   (provider, account_name, access_token_enc, refresh_token_enc,
                    expires_at, extra_enc)
                   VALUES (?,?,?,?,?,?)""",
                (info.provider, info.account_name,
                 encrypted_token, encrypt(info.refresh_token),
                 info.expires_at, encrypted_extra),
            )
            return cur.lastrowid

    def list_accounts(self) -> list[dict]:
        """List all stored accounts (tokens remain encrypted)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, provider, account_name, expires_at, created_at FROM accounts"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_account(self, account_id: int) -> dict | None:
        """Get a single account with encrypted token fields."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE id=?", (account_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_account(self, account_id: int) -> bool:
        """Delete an account. Returns True if deleted."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
            return cur.rowcount > 0

    def update_account_name(self, account_id: int, new_name: str) -> bool:
        """Update the display name of an account."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE accounts SET account_name=? WHERE id=?",
                (new_name, account_id),
            )
            return cur.rowcount > 0

    def count_accounts(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]

    # --- Config operations ---

    def get_config(self, key: str, default: Any = None) -> Any:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM config WHERE key=?", (key,)
            ).fetchone()
            if row is None:
                return default
            try:
                return json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                return row["value"]

    def set_config(self, key: str, value: Any) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    # --- Backup job operations ---

    def save_job(self, job_id: str, source_paths: list[str], status: str = "pending") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO backup_jobs (id, source_paths, status) VALUES (?, ?, ?)",
                (job_id, json.dumps(source_paths), status),
            )

    def update_job(self, job_id: str, **kwargs) -> None:
        """Update job fields. kwargs keys: status, total_tasks, completed_tasks, success_count, fail_count, completed_at."""
        if not kwargs:
            return
        sets = []
        values = []
        for key, val in kwargs.items():
            sets.append(f"{key}=?")
            values.append(val)
        values.append(job_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE backup_jobs SET {', '.join(sets)} WHERE id=?",
                values,
            )

    def get_job(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM backup_jobs WHERE id=?", (job_id,)).fetchone()
            if row:
                d = dict(row)
                d["source_paths"] = json.loads(d["source_paths"])
                return d
            return None

    def list_jobs(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM backup_jobs ORDER BY created_at DESC").fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["source_paths"] = json.loads(d["source_paths"])
                result.append(d)
            return result

    # --- Backup task operations ---

    def save_task(self, job_id: str, source_path: str, file_name: str, file_size: int, provider: str, remote_dir: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO backup_tasks
                   (job_id, source_path, file_name, file_size, provider, remote_dir)
                   VALUES (?,?,?,?,?,?)""",
                (job_id, source_path, file_name, file_size, provider, remote_dir),
            )
            return cur.lastrowid

    def update_task_status(self, task_id: int, status: str, progress: float = 0.0, error: str = "", file_id: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE backup_tasks SET status=?, progress=?, error=?, file_id=? WHERE id=?",
                (status, progress, error, file_id, task_id),
            )

    def get_tasks_for_job(self, job_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM backup_tasks WHERE job_id=? ORDER BY id",
                (job_id,),
            ).fetchall()
            return [dict(r) for r in rows]
