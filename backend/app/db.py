"""SQLite-хранилище для модуля Maps и учётных записей.

Соединение открывается на каждую операцию: фоновые потоки поллинга и
проверки прокси работают с той же базой, а sqlite-соединение нельзя
переиспользовать между потоками.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager

_DB_PATH = None
_INIT_LOCK = threading.Lock()
_INITIALIZED = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS maps_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id      INTEGER NOT NULL DEFAULT 1,
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
    gmaps_job_id  TEXT    NOT NULL DEFAULT '',
    bbox          TEXT    NOT NULL DEFAULT '',
    total_cells   INTEGER NOT NULL DEFAULT 0,
    completed_cells INTEGER NOT NULL DEFAULT 0,
    current_cell  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS maps_domains (
    owner_id      INTEGER NOT NULL DEFAULT 1,
    domain        TEXT NOT NULL,
    job_id        INTEGER,
    country       TEXT NOT NULL DEFAULT '',
    city          TEXT NOT NULL DEFAULT '',
    niche         TEXT NOT NULL DEFAULT '',
    business_name TEXT NOT NULL DEFAULT '',
    discovered_at TEXT NOT NULL DEFAULT '',
    exported_at   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (owner_id, domain)
);

CREATE TABLE IF NOT EXISTS maps_domain_sessions (
    domain       TEXT NOT NULL,
    job_id       INTEGER NOT NULL,
    owner_id     INTEGER NOT NULL DEFAULT 1,
    discovered_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (domain, job_id)
);

CREATE TABLE IF NOT EXISTS maps_proxies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id     INTEGER NOT NULL DEFAULT 1,
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
    role          TEXT NOT NULL DEFAULT 'user',
    created_at    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_maps_domains_job     ON maps_domains(job_id);
CREATE INDEX IF NOT EXISTS idx_maps_domains_country ON maps_domains(country);
CREATE INDEX IF NOT EXISTS idx_maps_domains_city    ON maps_domains(city);
CREATE INDEX IF NOT EXISTS idx_maps_domain_sessions_job ON maps_domain_sessions(job_id);
"""


def default_db_path() -> str:
    """Путь к базе по умолчанию — backend/data/maps.db."""
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
            # Lightweight migrations for databases created by older versions.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(maps_domains)")}
            if "exported_at" not in columns:
                conn.execute("ALTER TABLE maps_domains ADD COLUMN exported_at TEXT NOT NULL DEFAULT ''")
            for table in ("maps_jobs", "maps_domains", "maps_proxies"):
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                if "owner_id" not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_id INTEGER NOT NULL DEFAULT 1")
            job_columns = {row[1] for row in conn.execute("PRAGMA table_info(maps_jobs)")}
            for name, definition in (
                ("bbox", "TEXT NOT NULL DEFAULT ''"),
                ("total_cells", "INTEGER NOT NULL DEFAULT 0"),
                ("completed_cells", "INTEGER NOT NULL DEFAULT 0"),
                ("current_cell", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in job_columns:
                    conn.execute(f"ALTER TABLE maps_jobs ADD COLUMN {name} {definition}")
            domain_columns = list(conn.execute("PRAGMA table_info(maps_domains)"))
            domain_is_global_pk = any(row[1] == "domain" and row[5] == 1 for row in domain_columns)
            if domain_is_global_pk:
                conn.execute(
                    "CREATE TABLE maps_domains_v2 ("
                    "owner_id INTEGER NOT NULL DEFAULT 1, domain TEXT NOT NULL, job_id INTEGER, "
                    "country TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '', "
                    "niche TEXT NOT NULL DEFAULT '', business_name TEXT NOT NULL DEFAULT '', "
                    "discovered_at TEXT NOT NULL DEFAULT '', exported_at TEXT NOT NULL DEFAULT '', "
                    "PRIMARY KEY (owner_id, domain))"
                )
                conn.execute(
                    "INSERT OR IGNORE INTO maps_domains_v2 "
                    "(owner_id, domain, job_id, country, city, niche, business_name, discovered_at, exported_at) "
                    "SELECT owner_id, domain, job_id, country, city, niche, business_name, discovered_at, exported_at "
                    "FROM maps_domains"
                )
                conn.execute("DROP TABLE maps_domains")
                conn.execute("ALTER TABLE maps_domains_v2 RENAME TO maps_domains")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_maps_domains_job ON maps_domains(job_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_maps_domains_country ON maps_domains(country)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_maps_domains_city ON maps_domains(city)")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(maps_domain_sessions)")}
            if "owner_id" not in columns:
                conn.execute("ALTER TABLE maps_domain_sessions ADD COLUMN owner_id INTEGER NOT NULL DEFAULT 1")
            user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
            if "role" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
                # The account that existed before roles did was the de facto admin —
                # make that explicit instead of locking everyone out of the panel.
                oldest_user = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
                if oldest_user:
                    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (oldest_user[0],))
            first_user = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
            if first_user:
                legacy_owner = int(first_user[0])
                for table in ("maps_jobs", "maps_domains", "maps_proxies", "maps_domain_sessions"):
                    conn.execute(f"UPDATE {table} SET owner_id = ? WHERE owner_id = 1", (legacy_owner,))
            conn.execute(
                "INSERT OR IGNORE INTO maps_domain_sessions (domain, job_id, owner_id, discovered_at) "
                "SELECT domain, job_id, owner_id, discovered_at FROM maps_domains WHERE job_id IS NOT NULL"
            )
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
