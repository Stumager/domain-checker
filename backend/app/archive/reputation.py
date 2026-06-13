"""Domain reputation and risk-scoring module.

Covers: Google Safe Browsing, PhishTank, URLhaus host-feed, local blocklists,
RDAP domain age, TLS certificate age, and composite risk scoring.
"""

import os
import re
import socket
import ssl
import time
from datetime import datetime, timezone

import requests
from flask import current_app


# ---------------------------------------------------------------------------
# Module-level caches (TTL-based)
# ---------------------------------------------------------------------------

_URLHAUS_CACHE: dict = {"ts": 0.0, "hosts": set()}
_BLOCKLIST_CACHE: dict = {}


# ---------------------------------------------------------------------------
# Local blocklist loading
# ---------------------------------------------------------------------------

def _load_blocklist_file(path: str) -> set:
    if not path:
        return set()
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        return set()
    cached = _BLOCKLIST_CACHE.get(path)
    if cached and cached.get("mtime") == mtime:
        return cached.get("hosts", set())

    hosts = set()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                token = line.split()[0]
                token = token.strip().lower().rstrip(".")
                if token:
                    hosts.add(token)
    except Exception:
        return set()

    _BLOCKLIST_CACHE[path] = {"mtime": mtime, "hosts": hosts}
    return hosts


def _load_blocklists(paths_value: str) -> set:
    if not paths_value:
        return set()
    parts = [p.strip() for p in re.split(r"[;,]+", paths_value) if p.strip()]
    merged: set = set()
    for path in parts:
        merged.update(_load_blocklist_file(path))
    return merged


# ---------------------------------------------------------------------------
# URLhaus host-feed (cached with TTL)
# ---------------------------------------------------------------------------

def _load_urlhaus_hosts(url: str, timeout: float, ttl: float) -> set:
    if not url:
        return set()
    now = time.monotonic()
    cached = _URLHAUS_CACHE.get("hosts") or set()
    ts = _URLHAUS_CACHE.get("ts", 0.0)
    if cached and (now - ts) < ttl:
        return cached

    hosts: set = set()
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.ok:
            for raw_line in resp.text.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                token = line.split(",")[0].strip()
                token = token.strip().lower().rstrip(".")
                if token:
                    hosts.add(token)
    except Exception:
        return cached

    if hosts:
        _URLHAUS_CACHE["hosts"] = hosts
        _URLHAUS_CACHE["ts"] = now
    return hosts or cached


# ---------------------------------------------------------------------------
# External reputation APIs
# ---------------------------------------------------------------------------

def _check_safe_browsing(urls: list, timeout: float) -> dict | None:
    api_key = current_app.config.get("ARCHIVE_REPUTATION_SAFE_BROWSING_KEY", "")
    if not api_key:
        return None
    endpoint = f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={api_key}"
    client_id = current_app.config.get("ARCHIVE_REPUTATION_SAFE_BROWSING_CLIENT_ID", "checker")
    client_version = current_app.config.get("ARCHIVE_REPUTATION_SAFE_BROWSING_CLIENT_VERSION", "1.0")
    payload = {
        "client": {"clientId": client_id, "clientVersion": client_version},
        "threatInfo": {
            "threatTypes": [
                "MALWARE",
                "SOCIAL_ENGINEERING",
                "UNWANTED_SOFTWARE",
                "POTENTIALLY_HARMFUL_APPLICATION",
            ],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": u} for u in urls if u],
        },
    }
    try:
        resp = requests.post(endpoint, json=payload, timeout=timeout)
        if not resp.ok:
            return {"error": f"HTTP {resp.status_code}"}
        data = resp.json() if resp.content else {}
        matches = data.get("matches", []) if isinstance(data, dict) else []
        return {"matches": matches}
    except Exception as exc:
        return {"error": str(exc)}


def _check_phishtank(url: str, timeout: float) -> dict | None:
    app_key = current_app.config.get("ARCHIVE_REPUTATION_PHISHTANK_KEY", "")
    if not app_key:
        return None
    endpoint = "https://checkurl.dev.phishtank.com/checkurl/"
    headers = {"User-Agent": f"phishtank/{app_key}"}
    try:
        resp = requests.post(
            endpoint,
            data={"url": url, "format": "json", "app_key": app_key},
            headers=headers,
            timeout=timeout,
        )
        if not resp.ok:
            return {"error": f"HTTP {resp.status_code}"}
        data = resp.json() if resp.content else {}
        results = data.get("results", {}) if isinstance(data, dict) else {}
        return {"results": results}
    except Exception as exc:
        return {"error": str(exc)}


def _check_reputation(domain: str) -> dict:
    if not bool(current_app.config.get("ARCHIVE_REPUTATION_CHECK_ENABLED", True)):
        return {"hits": [], "details": {}}

    timeout = float(current_app.config.get("ARCHIVE_REPUTATION_TIMEOUT", 6))
    urlhaus_ttl = float(current_app.config.get("ARCHIVE_REPUTATION_URLHAUS_TTL", 3600))
    hits = []
    details = {}

    base_url = f"https://{domain}/"

    sb = _check_safe_browsing([base_url, f"http://{domain}/"], timeout)
    if sb is not None:
        details["safe_browsing"] = sb
        if sb.get("matches"):
            hits.append("safe_browsing")

    pt = _check_phishtank(base_url, timeout)
    if pt is not None:
        details["phishtank"] = pt
        res = pt.get("results", {})
        if res.get("in_database") and res.get("valid"):
            hits.append("phishtank")

    blocklist_paths = current_app.config.get("ARCHIVE_REPUTATION_BLOCKLIST_PATHS", "")
    if blocklist_paths:
        blocklist = _load_blocklists(blocklist_paths)
        details["blocklist"] = {"entries": len(blocklist)}
        if domain in blocklist:
            hits.append("blocklist")

    urlhaus_url = current_app.config.get("ARCHIVE_REPUTATION_URLHAUS_HOSTFILE_URL", "")
    if urlhaus_url:
        hosts = _load_urlhaus_hosts(urlhaus_url, timeout, urlhaus_ttl)
        details["urlhaus"] = {"entries": len(hosts)}
        if domain in hosts:
            hits.append("urlhaus")

    return {"hits": hits, "details": details}


# ---------------------------------------------------------------------------
# RDAP domain age
# ---------------------------------------------------------------------------

def _parse_rdap_event_date(events) -> datetime | None:
    if not isinstance(events, list):
        return None
    for event in events:
        if not isinstance(event, dict):
            continue
        action = str(event.get("eventAction", "")).lower()
        if action in ("registration", "registered", "creation", "created"):
            value = event.get("eventDate")
            if not value:
                continue
            try:
                val = value.replace("Z", "+00:00")
                return datetime.fromisoformat(val)
            except Exception:
                continue
    return None


def _fetch_rdap_age_days(domain: str) -> int | None:
    if not bool(current_app.config.get("ARCHIVE_RDAP_CHECK_ENABLED", True)):
        return None
    timeout = float(current_app.config.get("ARCHIVE_RDAP_TIMEOUT", 6))
    endpoint = current_app.config.get("ARCHIVE_RDAP_ENDPOINT", "https://rdap.org/domain/")
    url = endpoint.rstrip("/") + "/" + domain
    try:
        resp = requests.get(url, timeout=timeout)
        if not resp.ok:
            return None
        data = resp.json() if resp.content else {}
        created = _parse_rdap_event_date(data.get("events"))
        if not created:
            return None
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0, int((now - created).total_seconds() / 86400))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# TLS certificate age
# ---------------------------------------------------------------------------

def _fetch_tls_age_days(domain: str) -> int | None:
    if not bool(current_app.config.get("ARCHIVE_TLS_CHECK_ENABLED", True)):
        return None
    timeout = float(current_app.config.get("ARCHIVE_TLS_TIMEOUT", 4))
    try:
        server_name = domain.encode("idna").decode("ascii")
        ctx = ssl.create_default_context()
        with socket.create_connection((server_name, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=server_name) as ssock:
                cert = ssock.getpeercert()
        not_before = cert.get("notBefore")
        if not not_before:
            return None
        created = datetime.strptime(not_before, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0, int((now - created).total_seconds() / 86400))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Composite risk scoring
# ---------------------------------------------------------------------------

def _compute_domain_risk(
    rows: list,
    spam_hits: dict,
    spam_checked: int,
    spam_flagged: int,
    spam_propagated_labels: list,
    url_spam_count: int,
    url_spam_labels: dict,
    metrics_by_idx: dict,
    topic_checked: int,
    topic_shifted: int,
    language_checked: int,
    language_shifted: int,
    cloaking_checked: int,
    cloaking_detected: int,
    reputation: dict,
    rdap_age_days,
    tls_age_days,
) -> dict:
    flags = []
    score = 0

    def _ratio(value: int, total: int) -> float:
        return value / max(total, 1)

    spam_ratio = _ratio(spam_flagged, spam_checked)
    url_spam_ratio = _ratio(url_spam_count, len(rows))
    spam_heavy = bool(spam_propagated_labels) or spam_ratio >= 0.6 or url_spam_ratio >= 0.6
    if spam_propagated_labels or spam_ratio >= 0.3 or url_spam_ratio >= 0.3:
        flags.append("spam_content")
        score += 40
        if spam_heavy:
            score += 15

    ideograph_hits = 0
    if spam_hits:
        for hits in spam_hits.values():
            if "ideographs" in hits:
                ideograph_hits += 1
    ideograph_ratio = _ratio(ideograph_hits, spam_checked)
    if ideograph_ratio >= 0.4:
        flags.append("ideographs")
        score += 8

    parked_hits = 0
    if spam_hits:
        for hits in spam_hits.values():
            if "parked" in hits:
                parked_hits += 1
    parked_ratio = _ratio(parked_hits, spam_checked)
    url_parked_ratio = _ratio((url_spam_labels or {}).get("parked", 0), len(rows))
    parked_heavy = (
        ("parked" in (spam_propagated_labels or []))
        or parked_ratio >= 0.4
        or url_parked_ratio >= 0.4
    )
    if "parked" in (spam_propagated_labels or []) or parked_ratio >= 0.2 or url_parked_ratio >= 0.2:
        flags.append("parked")
        score += 22
        if parked_heavy:
            score += 10

    if _ratio(topic_shifted, topic_checked) >= 0.2:
        flags.append("topic_shift")
        score += 12

    if _ratio(language_shifted, language_checked) >= 0.2:
        flags.append("language_shift")
        score += 10

    if _ratio(cloaking_detected, cloaking_checked) >= 0.1:
        flags.append("cloaking")
        score += 30

    link_spam_hits = sum(1 for m in metrics_by_idx.values() if (m.get("link_spam") or []))
    if _ratio(link_spam_hits, len(metrics_by_idx)) >= 0.2:
        flags.append("spam_links")
        score += 15

    stuffing_hits = sum(1 for m in metrics_by_idx.values() if m.get("keyword_stuffing"))
    if _ratio(stuffing_hits, len(metrics_by_idx)) >= 0.25:
        flags.append("keyword_stuffing")
        score += 8

    thin_hits = sum(1 for m in metrics_by_idx.values() if m.get("thin_content"))
    if _ratio(thin_hits, len(metrics_by_idx)) >= 0.25:
        flags.append("thin_content")
        score += 6

    if metrics_by_idx:
        avg_external = (
            sum(m.get("external_ratio", 0.0) for m in metrics_by_idx.values())
            / max(len(metrics_by_idx), 1)
        )
        avg_links = (
            sum(m.get("link_total", 0) for m in metrics_by_idx.values())
            / max(len(metrics_by_idx), 1)
        )
        if avg_external > 0.8 and avg_links > 15:
            flags.append("link_farm")
            score += 8

        avg_tracking = (
            sum(m.get("tracking_ratio", 0.0) for m in metrics_by_idx.values())
            / max(len(metrics_by_idx), 1)
        )
        if avg_tracking > 0.3:
            flags.append("tracking_links")
            score += 5

    if rdap_age_days is not None and rdap_age_days < 180:
        flags.append("young_domain")
        score += 8

    if tls_age_days is not None and tls_age_days < 90:
        flags.append("young_cert")
        score += 5

    rep_hits = reputation.get("hits") if reputation else []
    if rep_hits:
        flags.append("reputation_hit")
        score += 60

    score = min(score, 100)
    threshold = int(current_app.config.get("ARCHIVE_NOT_SUITABLE_SCORE", 50))
    not_suitable = score >= threshold or bool(rep_hits) or spam_heavy or parked_heavy

    return {
        "score": score,
        "not_suitable": not_suitable,
        "flags": flags,
        "spam_ratio": round(spam_ratio, 3),
        "url_spam_ratio": round(url_spam_ratio, 3),
        "parked_ratio": round(parked_ratio, 3),
        "url_parked_ratio": round(url_parked_ratio, 3),
        "topic_shift_ratio": round(_ratio(topic_shifted, topic_checked), 3),
        "language_shift_ratio": round(_ratio(language_shifted, language_checked), 3),
        "cloaking_ratio": round(_ratio(cloaking_detected, cloaking_checked), 3),
        "reputation_hits": rep_hits or [],
        "rdap_age_days": rdap_age_days,
        "tls_age_days": tls_age_days,
    }
