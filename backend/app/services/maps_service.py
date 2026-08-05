"""Фоновый сервис модуля Maps: жизненный цикл задач и разбор результатов.

Транспорт до gosom/google-maps-scraper вынесен в gmaps_client.
"""

import csv
import io
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

_CONFIG = {"GMAPS_POLL_INTERVAL": 30.0}

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
                "(domain, job_id, country, city, niche, business_name, discovered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    domain,
                    job.get("id"),
                    job.get("country") or "",
                    job.get("city") or "",
                    job.get("niche") or "",
                    business_name,
                    discovered_at,
                ),
            )
            added += cur.rowcount or 0

    return added


# ---------------------------------------------------------------------------
# Доступ к задачам в БД
# ---------------------------------------------------------------------------

def get_job(job_id: int):
    return db.query_one("SELECT * FROM maps_jobs WHERE id = ?", (job_id,))


def latest_job():
    return db.query_one("SELECT * FROM maps_jobs ORDER BY id DESC LIMIT 1")


def active_job():
    return db.query_one(
        "SELECT * FROM maps_jobs WHERE status = 'running' ORDER BY id DESC LIMIT 1"
    )


def domain_count(job_id=None) -> int:
    if job_id is None:
        row = db.query_one("SELECT COUNT(*) AS n FROM maps_domains")
    else:
        row = db.query_one("SELECT COUNT(*) AS n FROM maps_domains WHERE job_id = ?", (job_id,))
    return int(row["n"]) if row else 0


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


def start_job(params: dict) -> dict:
    """Создать задачу, запустить её в gmaps и поднять поток поллинга."""
    if active_job():
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

    job_id = db.execute(
        "INSERT INTO maps_jobs (niche, country, city, language, tld_filter, depth, concurrency, "
        "grid_cell, zoom, custom_query, status, created_at, last_run_at, cycle_count, gmaps_job_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, '', 0, '')",
        (
            niche,
            country_name,
            city,
            language,
            (params.get("tld_filter") or "").strip(),
            int(params.get("depth") or 10),
            int(params.get("concurrency") or 4),
            float(params.get("grid_cell") or 1.0),
            int(params.get("zoom") or 15),
            custom_query,
            _now_iso(),
        ),
    )

    job = get_job(job_id)
    try:
        gmaps_job_id = create_gmaps_job(build_payload(job, bbox))
    except Exception:
        db.execute("UPDATE maps_jobs SET status = 'error' WHERE id = ?", (job_id,))
        raise

    db.execute(
        "UPDATE maps_jobs SET gmaps_job_id = ?, last_run_at = ? WHERE id = ?",
        (gmaps_job_id, _now_iso(), job_id),
    )

    _stop_event(job_id).clear()
    thread = threading.Thread(target=_maps_poll_loop, args=(job_id, gmaps_job_id), daemon=True)
    thread.start()

    return dict(get_job(job_id), bbox=bbox)


def stop_job(job_id=None) -> dict:
    job = get_job(job_id) if job_id else active_job()
    if not job:
        raise GmapsError("No active maps job")

    db.execute("UPDATE maps_jobs SET status = 'stopped' WHERE id = ?", (job["id"],))
    _stop_event(job["id"]).set()
    delete_gmaps_job(job.get("gmaps_job_id") or "")

    return get_job(job["id"])


def _start_next_cycle(job_id: int) -> str:
    """Поставить новую задачу в gmaps, если наша всё ещё running."""
    job = get_job(job_id)
    if not job or job["status"] != "running":
        return ""

    bbox = geo_data.fetch_bbox(job["city"], job["country"])
    gmaps_job_id = create_gmaps_job(build_payload(job, bbox))
    db.execute(
        "UPDATE maps_jobs SET gmaps_job_id = ?, last_run_at = ? WHERE id = ?",
        (gmaps_job_id, _now_iso(), job_id),
    )
    return gmaps_job_id


def _fail_job(job_id: int):
    db.execute("UPDATE maps_jobs SET status = 'error' WHERE id = ?", (job_id,))


def _maps_poll_loop(job_id: int, gmaps_job_id: str):
    """Опрашивать gmaps, забирать результаты и бесконечно повторять цикл."""
    stop_event = _stop_event(job_id)
    interval = float(_CONFIG["GMAPS_POLL_INTERVAL"])
    current_id = gmaps_job_id
    failures = 0

    try:
        while not stop_event.is_set():
            if stop_event.wait(interval):
                break

            job = get_job(job_id)
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
            delete_gmaps_job(current_id)

            if stop_event.is_set():
                break

            try:
                current_id = _start_next_cycle(job_id)
            except (GmapsUnavailable, GmapsError):
                failures += 1
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    _fail_job(job_id)
                    break
                current_id = ""

            if not current_id:
                job = get_job(job_id)
                if not job or job["status"] != "running":
                    break
    finally:
        _drop_stop_event(job_id)
