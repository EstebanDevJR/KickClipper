"""Persistencia en SQLite: trabajos (jobs) y clips candidatos."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any

from .config import settings

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT DEFAULT '',
    channel TEXT DEFAULT '',
    thumbnail TEXT DEFAULT '',
    duration REAL DEFAULT 0,
    status TEXT DEFAULT 'queued',          -- queued|downloading|transcribing|analyzing|scoring|rendering|done|error|cancelled
    stage_progress REAL DEFAULT 0,         -- 0..1 dentro de la etapa actual
    error TEXT DEFAULT '',
    options TEXT DEFAULT '{}',
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    start REAL NOT NULL,
    end REAL NOT NULL,
    score REAL DEFAULT 0,
    heuristic_score REAL DEFAULT 0,
    llm_score REAL DEFAULT 0,
    title TEXT DEFAULT '',
    description TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    hashtags TEXT DEFAULT '[]',
    transcript TEXT DEFAULT '',
    words TEXT DEFAULT '[]',               -- [[palabra, t0, t1], ...] para subtítulos
    status TEXT DEFAULT 'candidate',       -- candidate|rendering|rendered|error
    file TEXT DEFAULT '',
    render_options TEXT DEFAULT '{}',
    created_at REAL
);
"""


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(settings.db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        _migrate(_conn)
    return _conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Columnas añadidas después de la primera versión del esquema."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(clips)")}
    if "description" not in cols:
        conn.execute("ALTER TABLE clips ADD COLUMN description TEXT DEFAULT ''")
        conn.commit()


def _execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        cur = get_conn().execute(sql, params)
        get_conn().commit()
        return cur


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("options", "hashtags", "words", "render_options"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


# ---------- Jobs ----------

def create_job(url: str, options: dict | None = None) -> dict:
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    _execute(
        "INSERT INTO jobs (id, url, options, created_at, updated_at) VALUES (?,?,?,?,?)",
        (job_id, url, json.dumps(options or {}), now, now),
    )
    return get_job(job_id)


def get_job(job_id: str) -> dict | None:
    row = _execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return row_to_dict(row) if row else None


def list_jobs() -> list[dict]:
    rows = _execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    return [row_to_dict(r) for r in rows]


def update_job(job_id: str, **fields: Any) -> None:
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    _execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def delete_job(job_id: str) -> None:
    _execute("DELETE FROM jobs WHERE id=?", (job_id,))


# ---------- Clips ----------

def create_clip(job_id: str, **fields: Any) -> dict:
    clip_id = uuid.uuid4().hex[:12]
    for key in ("hashtags", "words", "render_options"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    cols = ["id", "job_id", "created_at", *fields.keys()]
    vals = [clip_id, job_id, time.time(), *fields.values()]
    _execute(
        f"INSERT INTO clips ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        tuple(vals),
    )
    return get_clip(clip_id)


def get_clip(clip_id: str) -> dict | None:
    row = _execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    return row_to_dict(row) if row else None


def list_clips(job_id: str) -> list[dict]:
    rows = _execute(
        "SELECT * FROM clips WHERE job_id=? ORDER BY score DESC", (job_id,)
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def update_clip(clip_id: str, **fields: Any) -> None:
    for key in ("hashtags", "words", "render_options"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    cols = ", ".join(f"{k}=?" for k in fields)
    _execute(f"UPDATE clips SET {cols} WHERE id=?", (*fields.values(), clip_id))


def delete_clips_for_job(job_id: str) -> None:
    _execute("DELETE FROM clips WHERE job_id=?", (job_id,))
