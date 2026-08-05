"""SQLite-хранилище для модуля Maps и учётных записей.

Соединение открывается на каждую операцию: фоновые потоки поллинга и
проверки прокси работают с той же базой, а sqlite-соединение нельзя
переиспользовать между потоками.
"""

import os
import sqlite3
import sys
import threading
from contextlib import contextmanager

_DB_PATH = None
_INIT_LOCK = threading.Lock()
_INITIALIZED = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS maps_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    niche         TEXT    NOT NULL DEFAULT '',
    country       TEXT    NOT NULL DEFAULT '',
    city          TEXT    NOT NULL DEFAULT '',
    language      TEXT    NOT NULL DEFAULT '',
    tld_filter    TEXT    NOT NULL DEFAULT '',
    depth         INTEGER NOT NULL DEFAULT 10,
    concurrency   INTEGER NOT NULL DEFAULT 4,
    grid_cell     REAL    NOT NULL DEFAULT 1.0,
    zoom          INTEGER NOT NULL DEFAULT 15,
    custom_query  TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL DEFAULT 'running',
    created_at    TEXT    NOT NULL DEFAULT '',
    last_run_at   TEXT    NOT NULL DEFAULT '',
    cycle_count   INTEGER NOT NULL DEFAULT 0,
    gmaps_job_id  TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS maps_domains (
    domain        TEXT PRIMARY KEY,
    job_id        INTEGER,
    country       TEXT NOT NULL DEFAULT '',
    city          TEXT NOT NULL DEFAULT '',
    niche         TEXT NOT NULL DEFAULT '',
    business_name TEXT NOT NULL DEFAULT '',
    discovered_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS maps_proxies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    proxy        TEXT UNIQUE NOT NULL,
    status       TEXT NOT NULL DEFAULT 'unknown',
    last_checked TEXT NOT NULL DEFAULT '',
    added_at     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS maps_bbox_cache (
    city      TEXT NOT NULL,
    country   TEXT NOT NULL,
    bbox      TEXT NOT NULL DEFAULT '',
    cached_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (city, country)
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_maps_domains_job     ON maps_domains(job_id);
CREATE INDEX IF NOT EXISTS idx_maps_domains_country ON maps_domains(country);
CREATE INDEX IF NOT EXISTS idx_maps_domains_city    ON maps_domains(city);
"""


def default_db_path() -> str:
    """Путь к базе по умолчанию — backend/data/maps.db."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "data", "maps.db")


def configure(db_path: str = ""):
    """Задать путь к базе (вызывается из фабрики приложения)."""
    global _DB_PATH, _INITIALIZED
    with _INIT_LOCK:
        _DB_PATH = (db_path or "").strip() or default_db_path()
        _INITIALIZED = False


def get_db_path() -> str:
    if not _DB_PATH:
        configure()
    return _DB_PATH


@contextmanager
def get_connection():
    """Открыть соединение с включённым WAL и вернуть строки как dict."""
    path = get_db_path()
    init_db()

    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Создать файл базы и таблицы при первом обращении."""
    global _INITIALIZED
    if _INITIALIZED:
        return

    with _INIT_LOCK:
        if _INITIALIZED:
            return

        path = _DB_PATH or default_db_path()
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        conn = sqlite3.connect(path, timeout=15)
        try:
            # WAL позволяет читать во время записи из фоновых потоков
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

        _INITIALIZED = True


def query_all(sql: str, params=()):
    with get_connection() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def query_one(sql: str, params=()):
    with get_connection() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def execute(sql: str, params=()) -> int:
    """Выполнить запрос и вернуть lastrowid."""
    with get_connection() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid
