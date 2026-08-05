"""Хранение и проверка прокси для модуля Maps."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from .. import db
from ..archive.fetcher import _mask_proxy_url, _normalize_proxy_url, _proxy_kwargs

_CHECK_LOCK = threading.Lock()
_CHECK_RUNNING = False

_CONFIG = {
    "PROXY_CHECK_URL": "https://www.google.com",
    "PROXY_CHECK_TIMEOUT": 10.0,
    "PROXY_CHECK_WORKERS": 8,
}


def set_config(config: dict):
    for key, value in (config or {}).items():
        if value not in (None, ""):
            _CONFIG[key] = value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_proxy_list(raw: str):
    """Разобрать текст из textarea в список нормализованных прокси."""
    out = []
    seen = set()

    for line in (raw or "").replace(",", "\n").splitlines():
        token = line.strip()
        if not token:
            continue

        proxy = _normalize_proxy_url(token)
        if not proxy or proxy in seen:
            continue

        seen.add(proxy)
        out.append(proxy)

    return out


def add_proxies(raw: str) -> int:
    """Добавить прокси, пропуская уже существующие. Вернуть число новых."""
    proxies = parse_proxy_list(raw)
    if not proxies:
        return 0

    added = 0
    with db.get_connection() as conn:
        for proxy in proxies:
            cur = conn.execute(
                "INSERT OR IGNORE INTO maps_proxies (proxy, status, last_checked, added_at) "
                "VALUES (?, 'unknown', '', ?)",
                (proxy, _now_iso()),
            )
            added += cur.rowcount or 0

    return added


def list_proxies():
    rows = db.query_all(
        "SELECT id, proxy, status, last_checked, added_at FROM maps_proxies ORDER BY id"
    )
    for row in rows:
        row["proxy_masked"] = _mask_proxy_url(row["proxy"])
    return rows


def working_proxies():
    """Рабочие прокси для передачи в gmaps."""
    rows = db.query_all("SELECT proxy FROM maps_proxies WHERE status = 'working' ORDER BY id")
    return [row["proxy"] for row in rows]


def delete_proxy(proxy_id: int) -> bool:
    with db.get_connection() as conn:
        cur = conn.execute("DELETE FROM maps_proxies WHERE id = ?", (proxy_id,))
        return bool(cur.rowcount)


def is_checking() -> bool:
    return _CHECK_RUNNING


def _check_one(proxy: str) -> str:
    """Проверить прокси обычным GET и вернуть 'working'/'failed'."""
    try:
        response = requests.get(
            _CONFIG["PROXY_CHECK_URL"],
            timeout=float(_CONFIG["PROXY_CHECK_TIMEOUT"]),
            **_proxy_kwargs(proxy),
        )
        return "working" if response.status_code < 400 else "failed"
    except Exception:
        return "failed"


def _check_all_worker():
    global _CHECK_RUNNING
    try:
        rows = db.query_all("SELECT id, proxy FROM maps_proxies ORDER BY id")
        if not rows:
            return

        workers = max(1, min(32, int(_CONFIG["PROXY_CHECK_WORKERS"])))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            statuses = list(pool.map(lambda row: _check_one(row["proxy"]), rows))

        checked_at = _now_iso()
        with db.get_connection() as conn:
            for row, status in zip(rows, statuses):
                conn.execute(
                    "UPDATE maps_proxies SET status = ?, last_checked = ? WHERE id = ?",
                    (status, checked_at, row["id"]),
                )
    finally:
        with _CHECK_LOCK:
            _CHECK_RUNNING = False


def start_check_all() -> bool:
    """Запустить фоновую проверку всех прокси. False — проверка уже идёт."""
    global _CHECK_RUNNING
    with _CHECK_LOCK:
        if _CHECK_RUNNING:
            return False
        _CHECK_RUNNING = True

    thread = threading.Thread(target=_check_all_worker, daemon=True)
    thread.start()
    return True
