"""Wayback Machine HTTP fetching layer.

Handles proxy management, CDX API pagination, snapshot fetching, and
redirect resolution. All config is read from Flask's current_app.
"""

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from urllib.parse import urlsplit

import requests
from flask import current_app


ARCHIVE_CDX_HTTPS_URL = "https://web.archive.org/cdx/search/cdx"
ARCHIVE_CDX_HTTP_URL = "http://web.archive.org/cdx/search/cdx"


# ---------------------------------------------------------------------------
# Core HTTP helpers
# ---------------------------------------------------------------------------

@contextmanager
def _no_env_proxies():
    """Temporarily remove HTTP(S)_PROXY env vars so requests ignores them."""
    keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def _perform_request(url: str, **kwargs):
    """Wrapper around requests.get with optional no_env=True to bypass env proxies."""
    no_env = kwargs.pop("no_env", False)
    if no_env:
        with _no_env_proxies():
            return requests.get(url, **kwargs)
    return requests.get(url, **kwargs)


# ---------------------------------------------------------------------------
# Proxy helpers
# ---------------------------------------------------------------------------

def _normalize_proxy_url(value: str) -> str:
    """Normalize a proxy entry to a requests-compatible URL."""
    token = (value or "").strip()
    if not token:
        return ""
    if "://" in token:
        return token
    parts = token.split(":")
    if len(parts) == 2:
        host, port = parts
        return f"http://{host}:{port}"
    if len(parts) == 4:
        host, port, username, password = parts
        return f"http://{username}:{password}@{host}:{port}"
    return f"http://{token}"


def _mask_proxy_url(proxy_url: str) -> str:
    """Return proxy URL with credentials stripped for display in the UI."""
    if not proxy_url:
        return "Direct connection"
    try:
        parsed = urlsplit(proxy_url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        scheme = parsed.scheme or "http"
        return f"{scheme}://{host}{port}"
    except Exception:
        if "@" in proxy_url:
            return proxy_url.split("@", 1)[-1]
        return proxy_url


def _proxy_kwargs(proxy_url: str) -> dict:
    """Build requests kwargs for a specific proxy URL."""
    return {"proxies": {"http": proxy_url, "https": proxy_url}}


def _build_archive_request_candidates(proxy_url: str) -> list:
    """Build request candidates: manual proxy first, then optional direct fallback."""
    allow_direct_fallback = bool(current_app.config.get("ARCHIVE_DIRECT_FALLBACK", True))
    candidates = []

    if proxy_url:
        candidates.append({
            "mode": "proxy",
            "label": _mask_proxy_url(proxy_url),
            "req_kwargs": _proxy_kwargs(proxy_url),
        })

    if allow_direct_fallback or not proxy_url:
        if proxy_url:
            direct_kwargs = {"proxies": {"http": None, "https": None}, "no_env": True}
        else:
            direct_kwargs = {}
        candidates.append({
            "mode": "direct",
            "label": "Direct connection",
            "req_kwargs": direct_kwargs,
        })

    return candidates


def _iter_archive_cdx_urls():
    """Yield CDX endpoints: HTTPS first, then optional HTTP fallback."""
    use_http_fallback = bool(current_app.config.get("ARCHIVE_CDX_ALLOW_HTTP_FALLBACK", True))
    yield ARCHIVE_CDX_HTTPS_URL
    if use_http_fallback:
        yield ARCHIVE_CDX_HTTP_URL


# ---------------------------------------------------------------------------
# CDX parsing and row fetching
# ---------------------------------------------------------------------------

def _parse_cdx_page(payload) -> tuple[list, str, bool]:
    """Parse a CDX JSON page; return (rows, resume_key, has_redirect_col)."""
    if not isinstance(payload, list) or len(payload) <= 1:
        return [], "", False

    header = payload[0] if isinstance(payload[0], list) else []
    rows = payload[1:]
    resume_key = ""

    if rows and isinstance(rows[-1], list) and len(rows[-1]) == 1 and isinstance(rows[-1][0], str):
        resume_key = rows[-1][0]
        rows = rows[:-1]
        if rows and rows[-1] == []:
            rows = rows[:-1]

    column_map = {}
    for idx, name in enumerate(header):
        key = str(name or "").strip().lower()
        if key and key not in column_map:
            column_map[key] = idx

    ts_idx = column_map.get("timestamp", 0)
    orig_idx = column_map.get("original", 1)
    status_idx = column_map.get("statuscode", 2)
    redirect_idx = column_map.get("redirect")
    if redirect_idx is None:
        redirect_idx = column_map.get("redirecturl")
    has_redirect_col = redirect_idx is not None

    def _cell(row, idx):
        if idx is None or idx < 0 or idx >= len(row):
            return ""
        return str(row[idx] or "")

    parsed = []
    for row in rows:
        if not isinstance(row, list):
            continue
        ts = _cell(row, ts_idx)
        orig = _cell(row, orig_idx)
        status = _cell(row, status_idx)
        redirect = _cell(row, redirect_idx)
        if len(ts) < 12 or not orig:
            continue
        parsed.append((ts, orig, status, redirect))
    return parsed, resume_key, has_redirect_col


def _fetch_archive_rows(
    domain: str,
    headers: dict,
    req_kwargs: dict,
    cdx_url: str,
    timeout_override=None,
    retries_override=None,
) -> tuple[list, int, int, bool, bool]:
    """Fetch all archive rows for a domain using resumeKey pagination."""
    year_from = int(current_app.config.get("ARCHIVE_YEAR_FROM", 1998))
    year_to = int(current_app.config.get("ARCHIVE_YEAR_TO", 2026))
    page_size = int(current_app.config.get("ARCHIVE_CDX_PAGE_SIZE", 2000))
    max_pages = int(current_app.config.get("ARCHIVE_CDX_MAX_PAGES", 400))
    max_rows = int(current_app.config.get("ARCHIVE_CDX_MAX_ROWS", 600000))
    timeout = float(
        timeout_override if timeout_override is not None
        else current_app.config.get("ARCHIVE_TIMEOUT", 45)
    )
    retries = int(
        retries_override if retries_override is not None
        else current_app.config.get("ARCHIVE_REQUEST_RETRIES", 3)
    )
    max_seconds = float(current_app.config.get("ARCHIVE_MAX_SECONDS", 60))

    page_size = min(max(page_size, 100), 10000)
    max_pages = max(max_pages, 1)
    max_rows = max(max_rows, 1000)
    retries = min(max(retries, 1), 8)
    max_seconds = max(max_seconds, 5)

    base_params = {
        "url": domain,
        "matchType": "exact",
        "output": "json",
        "fl": "timestamp,original,statuscode,redirect,redirecturl",
        "from": f"{year_from}0101",
        "to": f"{year_to}1231",
        "showResumeKey": "true",
        "limit": page_size,
    }

    rows = []
    resume_key = ""
    pages = 0
    truncated = False
    has_redirect_col = False
    started = time.monotonic()

    while pages < max_pages and len(rows) < max_rows:
        if (time.monotonic() - started) >= max_seconds:
            truncated = True
            break

        params = dict(base_params)
        if resume_key:
            params["resumeKey"] = resume_key

        last_error = None
        payload = None
        for _ in range(retries):
            try:
                elapsed = time.monotonic() - started
                remaining = max_seconds - elapsed
                if remaining <= 0:
                    truncated = True
                    break
                request_timeout = min(timeout, max(2.0, remaining))

                resp = _perform_request(
                    cdx_url,
                    params=params,
                    headers=headers,
                    timeout=request_timeout,
                    **req_kwargs,
                )
                resp.raise_for_status()
                payload = resp.json()
                last_error = None
                break
            except Exception as exc:
                last_error = exc

        if payload is None:
            if truncated:
                break
            if rows:
                truncated = True
                break
            raise last_error

        page_rows, next_resume_key, page_has_redirect = _parse_cdx_page(payload)
        if page_has_redirect:
            has_redirect_col = True
        pages += 1

        for item in page_rows:
            rows.append(item)
            if len(rows) >= max_rows:
                truncated = True
                break

        if truncated or not next_resume_key:
            break
        resume_key = next_resume_key

    if pages >= max_pages and resume_key:
        truncated = True

    rows.sort(key=lambda x: x[0], reverse=True)
    return rows, year_from, year_to, truncated, has_redirect_col


# ---------------------------------------------------------------------------
# Timestamp and URL formatting
# ---------------------------------------------------------------------------

def _fmt_ts(ts: str) -> str:
    """Format Wayback timestamp (14 chars) to dd.mm.yyyy HH:MM."""
    if len(ts) < 12:
        return ts
    return f"{ts[6:8]}.{ts[4:6]}.{ts[:4]} {ts[8:10]}:{ts[10:12]}"


def _normalize_wayback_location(location: str) -> str:
    """Convert a Wayback Location header value to a plain target URL."""
    loc = (location or "").strip()
    if not loc:
        return ""

    if loc.startswith("//"):
        return f"https:{loc}"

    m = re.match(r"^https?://web\.archive\.org/web/\d+[a-z_]*/(.+)$", loc)
    if m:
        loc = m.group(1)
    else:
        m = re.match(r"^/web/\d+[a-z_]*/(.+)$", loc)
        if m:
            loc = m.group(1)

    if loc.startswith("http:/") and not loc.startswith("http://"):
        loc = loc.replace("http:/", "http://", 1)
    if loc.startswith("https:/") and not loc.startswith("https://"):
        loc = loc.replace("https:/", "https://", 1)
    return loc


# ---------------------------------------------------------------------------
# Redirect probing
# ---------------------------------------------------------------------------

def _probe_snapshot_redirect(
    ts: str,
    orig: str,
    headers: dict,
    req_kwargs: dict,
    timeout: float,
) -> tuple[str, bool]:
    """Resolve redirect target via Wayback id_ playback Location header."""
    probe_url = f"https://web.archive.org/web/{ts}id_/{orig}"

    def _single_probe(kwargs: dict) -> str:
        try:
            resp = _perform_request(
                probe_url,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
                **kwargs,
            )
            loc = resp.headers.get("Location") or resp.headers.get("location") or ""
            return _normalize_wayback_location(loc)
        except Exception:
            return ""

    used_direct = False
    redirect_target = _single_probe(req_kwargs or {})

    if req_kwargs and req_kwargs.get("proxies") and not redirect_target:
        used_direct = True
        fb_kwargs = {"proxies": {"http": None, "https": None}}
        if req_kwargs.get("no_env"):
            fb_kwargs["no_env"] = True
        redirect_target = _single_probe(fb_kwargs)

    return redirect_target, used_direct


def _enrich_missing_redirects(
    rows: list,
    headers: dict,
    req_kwargs: dict,
) -> tuple[list, int, int, int]:
    """Backfill missing redirect targets for 301/302 rows via snapshot probes."""
    if not rows:
        return rows, 0, 0, 0

    if not bool(current_app.config.get("ARCHIVE_REDIRECT_FETCH_ENABLED", True)):
        return rows, 0, 0, 0

    max_probe = max(int(current_app.config.get("ARCHIVE_REDIRECT_FETCH_MAX", 180)), 0)
    workers = max(int(current_app.config.get("ARCHIVE_REDIRECT_FETCH_WORKERS", 8)), 1)
    timeout = max(float(current_app.config.get("ARCHIVE_REDIRECT_FETCH_TIMEOUT", 6)), 1.0)

    empty_markers = {"", "-", "null", "none"}
    missing = [
        idx
        for idx, (ts, orig, status, redirect) in enumerate(rows)
        if status in ("301", "302") and (redirect or "").strip().lower() in empty_markers
    ]

    if not missing or max_probe == 0:
        return rows, 0, 0, 0

    target_indexes = missing[:max_probe]
    resolved = {}

    with ThreadPoolExecutor(max_workers=min(workers, len(target_indexes))) as executor:
        futures = {
            executor.submit(
                _probe_snapshot_redirect,
                rows[idx][0],
                rows[idx][1],
                headers,
                req_kwargs,
                timeout,
            ): idx
            for idx in target_indexes
        }
        direct_count = 0
        for future in as_completed(futures):
            idx = futures[future]
            try:
                value, used_direct = future.result()
            except Exception:
                value, used_direct = "", False
            if used_direct:
                direct_count += 1
            if value:
                resolved[idx] = value

    if not resolved:
        return rows, 0, len(target_indexes), direct_count

    updated = []
    for idx, row in enumerate(rows):
        if idx in resolved:
            ts, orig, status, _redirect = row
            updated.append((ts, orig, status, resolved[idx]))
        else:
            updated.append(row)
    return updated, len(resolved), len(target_indexes), direct_count
