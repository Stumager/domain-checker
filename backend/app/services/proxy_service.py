"""Хранение и проверка прокси для модуля Maps."""

import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from .. import db
from ..utils import apply_config, escape_like, mask_proxy_url, normalize_proxy_url, now_iso, proxy_kwargs

_CHECK_LOCK = threading.Lock()
_CHECK_RUNNING = False

_CONFIG = {
    "PROXY_CHECK_URL": "https://www.google.com",
    "PROXY_CHECK_TIMEOUT": 10.0,
    "PROXY_CHECK_WORKERS": 8,
}


def set_config(config: dict):
    apply_config(_CONFIG, config, source="proxy_service")


def parse_proxy_list(raw: str):
    """Разобрать текст из textarea в список нормализованных прокси."""
    out = []
    seen = set()

    for line in (raw or "").replace(",", "\n").splitlines():
        token = line.strip()
        if not token:
            continue

        proxy = normalize_proxy_url(token)
        if not proxy or proxy in seen:
            continue

        seen.add(proxy)
        out.append(proxy)

    return out


def add_proxies(raw: str, owner_id=1) -> int:
    """Добавить прокси, пропуская уже существующие. Вернуть число новых."""
    proxies = parse_proxy_list(raw)
    if not proxies:
        return 0

    added = 0
    with db.get_connection() as conn:
        for proxy in proxies:
            cur = conn.execute(
                "INSERT OR IGNORE INTO maps_proxies (owner_id, proxy, status, last_checked, added_at) "
                "VALUES (?, ?, 'unknown', '', ?)",
                (owner_id, proxy, now_iso()),
            )
            added += cur.rowcount or 0

    return added


def list_proxies(owner_id=1, page=None, limit=10, search=""):
    """Return proxies with optional search and pagination."""
    clauses = ["owner_id = ?"]
    params = [owner_id]
    search = (search or "").strip()
    if search:
        clauses.append("proxy LIKE ? ESCAPE '\\'")
        params.append(f"%{escape_like(search)}%")

    where = " AND ".join(clauses)
    total_row = db.query_one(f"SELECT COUNT(*) AS n FROM maps_proxies WHERE {where}", tuple(params))
    total = int(total_row["n"] if total_row else 0)
    query = (
        "SELECT id, proxy, status, last_checked, added_at FROM maps_proxies "
        f"WHERE {where} ORDER BY id"
    )
    if page is not None:
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, max(0, page - 1) * limit])
    rows = db.query_all(query, tuple(params))
    for row in rows:
        row["proxy_masked"] = mask_proxy_url(row["proxy"])
    return rows, total


def working_proxies(owner_id=1):
    """Рабочие прокси для передачи в gmaps."""
    rows = db.query_all(
        "SELECT proxy FROM maps_proxies WHERE owner_id = ? AND status = 'working' ORDER BY id",
        (owner_id,),
    )
    return [row["proxy"] for row in rows]


def delete_proxy(proxy_id: int, owner_id=1) -> bool:
    with db.get_connection() as conn:
        cur = conn.execute("DELETE FROM maps_proxies WHERE id = ? AND owner_id = ?", (proxy_id, owner_id))
        return bool(cur.rowcount)


def delete_failed_proxies(owner_id=1) -> int:
    """Delete all proxies that failed their last check."""
    with db.get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM maps_proxies WHERE owner_id = ? AND status = 'failed'",
            (owner_id,),
        )
        return cur.rowcount or 0


def is_checking() -> bool:
    return _CHECK_RUNNING


def _check_one(proxy: str) -> str:
    """Проверить прокси обычным GET и вернуть 'working'/'failed'."""
    try:
        response = requests.get(
            _CONFIG["PROXY_CHECK_URL"],
            timeout=float(_CONFIG["PROXY_CHECK_TIMEOUT"]),
            **proxy_kwargs(proxy),
        )
        return "working" if response.status_code < 400 else "failed"
    except Exception:
        return "failed"


def _check_all_worker(owner_id=1):
    global _CHECK_RUNNING
    try:
        rows = db.query_all("SELECT id, proxy FROM maps_proxies WHERE owner_id = ? ORDER BY id", (owner_id,))
        if not rows:
            return

        workers = max(1, min(32, int(_CONFIG["PROXY_CHECK_WORKERS"])))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            statuses = list(pool.map(lambda row: _check_one(row["proxy"]), rows))

        checked_at = now_iso()
        with db.get_connection() as conn:
            for row, status in zip(rows, statuses):
                conn.execute(
                    "UPDATE maps_proxies SET status = ?, last_checked = ? WHERE id = ?",
                    (status, checked_at, row["id"]),
                )
    finally:
        with _CHECK_LOCK:
            _CHECK_RUNNING = False


def start_check_all(owner_id=1) -> bool:
    """Запустить фоновую проверку всех прокси. False — проверка уже идёт."""
    global _CHECK_RUNNING
    with _CHECK_LOCK:
        if _CHECK_RUNNING:
            return False
        _CHECK_RUNNING = True

    thread = threading.Thread(target=_check_all_worker, args=(owner_id,), daemon=True)
    thread.start()
    return True
