"""Фоновый сервис модуля Maps: жизненный цикл задач и разбор результатов.

Транспорт до gosom/google-maps-scraper вынесен в gmaps_client.
"""

import csv
import io
import json
import re
import threading
from datetime import datetime, timezone

from .. import db
from ..utils import normalize_domain as _strip_url, parse_tlds
from . import geo_data
from .gmaps_client import (
    GmapsError,
    GmapsUnavailable,
    build_payload,
    build_query,
    create_gmaps_job,
    delete_gmaps_job,
    download_gmaps_csv,
    get_gmaps_job,
    pick,
)
from .gmaps_client import set_config as set_client_config

_REGISTRY_LOCK = threading.Lock()
_STOP_EVENTS = {}

MAX_CONSECUTIVE_FAILURES = 5

_CONFIG = {
    "GMAPS_POLL_INTERVAL": 30.0,
    "GMAPS_DEFAULT_DEPTH": 10,
    "GMAPS_DEFAULT_ZOOM": 15,
    "GMAPS_DEFAULT_CONCURRENCY": 4,
    "GMAPS_DEFAULT_GRID_CELL": 1.0,
}

_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")

__all__ = [
    "GmapsError", "GmapsUnavailable", "build_payload", "build_query",
    "create_gmaps_job", "delete_gmaps_job", "download_gmaps_csv", "get_gmaps_job",
    "set_config", "normalize_domain", "ingest_csv", "start_job", "stop_job",
    "get_job", "latest_job", "active_job", "domain_count", "reset_stale_jobs",
]


def set_config(config: dict):
    """Настройки поллинга остаются здесь, остальное уходит в клиент."""
    for key, value in (config or {}).items():
        if key in _CONFIG and value not in (None, ""):
            _CONFIG[key] = value

    set_client_config(config)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Домены
# ---------------------------------------------------------------------------

def normalize_domain(url: str) -> str:
    """Привести ссылку к голому домену: без протокола, www и пути."""
    domain = _strip_url(url)
    if domain.startswith("www."):
        domain = domain[4:]

    if not domain or len(domain) > 253 or not _DOMAIN_RE.match(domain):
        return ""

    return domain


def _matches_tld_filter(domain: str, tlds) -> bool:
    if not tlds:
        return True
    return any(domain.endswith("." + tld) for tld in tlds)


def ingest_csv(text: str, job: dict) -> int:
    """Разобрать CSV gmaps и записать новые домены. Вернуть число добавленных."""
    if not text or not text.strip():
        return 0

    tlds = parse_tlds(job.get("tld_filter") or "")
    reader = csv.DictReader(io.StringIO(text))

    batch = {}
    for row in reader:
        domain = normalize_domain(row.get("website") or "")
        if not domain or not _matches_tld_filter(domain, tlds):
            continue
        if domain not in batch:
            batch[domain] = (row.get("title") or "").strip()

    if not batch:
        return 0

    discovered_at = _now_iso()
    added = 0
    with db.get_connection() as conn:
        for domain, business_name in batch.items():
            cur = conn.execute(
                "INSERT OR IGNORE INTO maps_domains "
                "(domain, owner_id, job_id, country, city, niche, business_name, discovered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    domain,
                    job.get("owner_id") or 1,
                    job.get("id"),
                    job.get("country") or "",
                    job.get("city") or "",
                    job.get("niche") or "",
                    business_name,
                    discovered_at,
                ),
            )
            added += cur.rowcount or 0
            if job.get("id"):
                conn.execute(
                    "INSERT OR IGNORE INTO maps_domain_sessions (domain, job_id, owner_id, discovered_at) "
                    "VALUES (?, ?, ?, ?)",
                    (domain, job["id"], job.get("owner_id") or 1, discovered_at),
                )

    return added


# ---------------------------------------------------------------------------
# Доступ к задачам в БД
# ---------------------------------------------------------------------------

def get_job(job_id: int, owner_id=None):
    sql = "SELECT * FROM maps_jobs WHERE id = ?"
    params = [job_id]
    if owner_id is not None:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    return db.query_one(sql, tuple(params))


def latest_job(owner_id=None):
    sql = "SELECT * FROM maps_jobs"
    params = []
    if owner_id is not None:
        sql += " WHERE owner_id = ?"
        params.append(owner_id)
    return db.query_one(sql + " ORDER BY id DESC LIMIT 1", tuple(params))


def active_job(owner_id=None):
    sql = "SELECT * FROM maps_jobs WHERE status = 'running'"
    params = []
    if owner_id is not None:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    return db.query_one(sql + " ORDER BY id DESC LIMIT 1", tuple(params))


def domain_count(job_id=None, owner_id=None) -> int:
    clauses, params = [], []
    if owner_id is not None:
        clauses.append("owner_id = ?")
        params.append(owner_id)
    if job_id is not None:
        clauses.append("job_id = ?")
        params.append(job_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    row = db.query_one("SELECT COUNT(*) AS n FROM maps_domains" + where, tuple(params))
    return int(row["n"]) if row else 0


def coverage_progress(job: dict) -> dict:
    """Return measured city coverage; only completed map cells count."""
    total = max(0, int(job.get("total_cells") or 0))
    completed = min(total, max(0, int(job.get("completed_cells") or 0)))
    return {
        "available": total > 0,
        "completed_cells": completed,
        "total_cells": total,
        "current_cell": min(total, completed + 1) if total else 0,
        "percent": round(completed * 100 / total, 1) if total else None,
    }


def reset_stale_jobs():
    """После рестарта приложения потоков поллинга нет — снимаем флаг running."""
    db.execute("UPDATE maps_jobs SET status = 'stopped' WHERE status = 'running'")


# ---------------------------------------------------------------------------
# Жизненный цикл задачи
# ---------------------------------------------------------------------------

def _stop_event(job_id: int) -> threading.Event:
    with _REGISTRY_LOCK:
        event = _STOP_EVENTS.get(job_id)
        if event is None:
            event = threading.Event()
            _STOP_EVENTS[job_id] = event
        return event


def _drop_stop_event(job_id: int):
    with _REGISTRY_LOCK:
        _STOP_EVENTS.pop(job_id, None)


def start_job(params: dict, owner_id=1) -> dict:
    """Создать задачу, запустить её в gmaps и поднять поток поллинга."""
    if active_job(owner_id):
        raise GmapsError("A maps job is already running")

    niche = (params.get("niche") or "").strip()
    city = (params.get("city") or "").strip()
    country_input = (params.get("country") or "").strip()
    custom_query = (params.get("custom_query") or "").strip()

    if not niche and not custom_query:
        raise ValueError("Niche is required")
    if not city or not country_input:
        raise ValueError("Country and city are required")

    country = geo_data.find_country(country_input)
    country_name = country["name"] if country else country_input
    country_code = country["code"] if country else ""

    language = (
        (params.get("language") or "").strip()
        or geo_data.default_language(country_name, country_code)
    )
    bbox = geo_data.fetch_bbox(city, country_name)
    cells = geo_data.bbox_to_cells(bbox, params.get("grid_cell") or _CONFIG["GMAPS_DEFAULT_GRID_CELL"])
    if not cells:
        raise ValueError("Could not determine a valid city coverage area")

    job_id = db.execute(
        "INSERT INTO maps_jobs (owner_id, niche, country, city, language, tld_filter, depth, concurrency, "
        "grid_cell, zoom, custom_query, status, created_at, last_run_at, cycle_count, gmaps_job_id, "
        "bbox, total_cells, completed_cells, current_cell) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, '', 0, '', ?, ?, 0, 0)",
        (
            owner_id,
            niche,
            country_name,
            city,
            language,
            (params.get("tld_filter") or "").strip(),
            int(params.get("depth") or _CONFIG["GMAPS_DEFAULT_DEPTH"]),
            int(params.get("concurrency") or _CONFIG["GMAPS_DEFAULT_CONCURRENCY"]),
            float(params.get("grid_cell") or _CONFIG["GMAPS_DEFAULT_GRID_CELL"]),
            int(params.get("zoom") or _CONFIG["GMAPS_DEFAULT_ZOOM"]),
            custom_query,
            _now_iso(),
            json.dumps(bbox),
            len(cells),
        ),
    )

    job = get_job(job_id, owner_id)
    try:
        gmaps_job_id = create_gmaps_job(build_payload(job, cells[0]))
    except Exception:
        db.execute("UPDATE maps_jobs SET status = 'error' WHERE id = ?", (job_id,))
        raise

    db.execute(
        "UPDATE maps_jobs SET gmaps_job_id = ?, last_run_at = ? WHERE id = ?",
        (gmaps_job_id, _now_iso(), job_id),
    )

    _stop_event(job_id).clear()
    thread = threading.Thread(
        target=_maps_poll_loop, args=(job_id, gmaps_job_id, owner_id), daemon=True
    )
    thread.start()

    return dict(get_job(job_id), bbox=bbox)


def stop_job(job_id=None, owner_id=1) -> dict:
    job = get_job(job_id, owner_id) if job_id else active_job(owner_id)
    if not job:
        raise GmapsError("No active maps job")

    db.execute("UPDATE maps_jobs SET status = 'stopped' WHERE id = ?", (job["id"],))
    _stop_event(job["id"]).set()
    delete_gmaps_job(job.get("gmaps_job_id") or "")

    return get_job(job["id"], owner_id)


def _start_next_cycle(job_id: int, owner_id=None) -> str:
    """Поставить новую задачу в gmaps, если наша всё ещё running."""
    job = get_job(job_id, owner_id)
    if not job or job["status"] != "running":
        return ""

    try:
        bbox = json.loads(job.get("bbox") or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        bbox = geo_data.fetch_bbox(job["city"], job["country"])
    cells = geo_data.bbox_to_cells(bbox, job.get("grid_cell") or _CONFIG["GMAPS_DEFAULT_GRID_CELL"])
    if not cells:
        # Keep legacy jobs (created before coverage tracking) running normally.
        gmaps_job_id = create_gmaps_job(build_payload(job, bbox))
        db.execute(
            "UPDATE maps_jobs SET gmaps_job_id = ?, last_run_at = ? WHERE id = ?",
            (gmaps_job_id, _now_iso(), job_id),
        )
        return gmaps_job_id

    completed = int(job.get("completed_cells") or 0)
    if completed >= len(cells):
        completed = 0
    db.execute(
        "UPDATE maps_jobs SET completed_cells = ?, current_cell = ? WHERE id = ?",
        (completed, completed, job_id),
    )
    job["completed_cells"] = completed
    job["current_cell"] = completed
    gmaps_job_id = create_gmaps_job(build_payload(job, cells[completed]))
    db.execute(
        "UPDATE maps_jobs SET gmaps_job_id = ?, last_run_at = ? WHERE id = ?",
        (gmaps_job_id, _now_iso(), job_id),
    )
    return gmaps_job_id


def _fail_job(job_id: int):
    db.execute("UPDATE maps_jobs SET status = 'error' WHERE id = ?", (job_id,))


def _maps_poll_loop(job_id: int, gmaps_job_id: str, owner_id=None):
    """Опрашивать gmaps, забирать результаты и бесконечно повторять цикл."""
    stop_event = _stop_event(job_id)
    interval = float(_CONFIG["GMAPS_POLL_INTERVAL"])
    current_id = gmaps_job_id
    failures = 0

    try:
        while not stop_event.is_set():
            if stop_event.wait(interval):
                break

            job = get_job(job_id, owner_id)
            if not job or job["status"] != "running":
                break

            try:
                status = str(pick(get_gmaps_job(current_id), "status") or "").strip().lower()
            except (GmapsUnavailable, GmapsError):
                failures += 1
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    _fail_job(job_id)
                    break
                continue

            if status in ("pending", "working"):
                failures = 0
                continue

            if status == "ok":
                failures = 0
                try:
                    ingest_csv(download_gmaps_csv(current_id), job)
                except (GmapsUnavailable, GmapsError):
                    pass
            elif status == "failed":
                failures += 1
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    _fail_job(job_id)
                    break
            else:
                continue

            db.execute(
                "UPDATE maps_jobs SET cycle_count = cycle_count + 1 WHERE id = ?", (job_id,)
            )
            job = get_job(job_id, owner_id)
            if job and int(job.get("total_cells") or 0) > 0:
                completed = min(
                    int(job["total_cells"]),
                    int(job.get("completed_cells") or 0) + 1,
                )
                db.execute(
                    "UPDATE maps_jobs SET completed_cells = ?, current_cell = ? WHERE id = ?",
                    (completed, completed, job_id),
                )
            delete_gmaps_job(current_id)

            if stop_event.is_set():
                break

            try:
                current_id = _start_next_cycle(job_id, owner_id)
            except (GmapsUnavailable, GmapsError):
                failures += 1
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    _fail_job(job_id)
                    break
                current_id = ""

            if not current_id:
                job = get_job(job_id, owner_id)
                if not job or job["status"] != "running":
                    break
    finally:
        _drop_stop_event(job_id)
