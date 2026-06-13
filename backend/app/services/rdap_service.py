"""RDAP (Registration Data Access Protocol) service for domain checking"""

import json
import random
import socket
import threading
import time
from typing import Optional, Dict, List, Tuple
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter

from ..utils import normalize_domain, is_valid_domain, to_ascii, dedupe
from ..models import CheckerState

# Configuration (will be injected from config)
RDAP_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
RDAP_TIMEOUT = 4.0
RDAP_RETRIES = 2
RDAP_BACKOFF_BASE = 0.6
RDAP_BACKOFF_JITTER = 0.25
RDAP_CONCURRENCY_DEFAULT = 12
RDAP_CONCURRENCY_ES = 2
RDAP_CONCURRENCY_IT = 4
RDAP_TLD_OVERRIDES_JSON = ""
RDAP_CONCURRENCY_OVERRIDES_JSON = ""
RDAP_SESSION_POOL_CONNECTIONS = 32
RDAP_SESSION_POOL_MAXSIZE = 64
RDAP_FORBIDDEN_FALLBACK = True
RDAP_PARSE_ERROR_BODY = True
RDAP_RESTRICTED_ENABLE = True
RDAP_RESTRICTED_TTL = 3600.0

WHOIS_SERVER_OVERRIDES_JSON = ""
WHOIS_NOT_FOUND_OVERRIDES_JSON = ""
WHOIS_BOOTSTRAP_ENABLED = True
WHOIS_BOOTSTRAP_SERVER = "whois.iana.org"

# RDAP bootstrap cache
_rdap_bootstrap_lock = threading.Lock()
_rdap_tld_to_base: Optional[Dict[str, str]] = None
_rdap_tld_overrides: Optional[Dict[str, str]] = None
_rdap_concurrency_overrides: Optional[Dict[str, int]] = None
_thread_local = threading.local()

_rdap_sem_lock = threading.Lock()
_rdap_tld_semaphores: Dict[str, threading.Semaphore] = {}
_rdap_restricted_lock = threading.Lock()
_rdap_restricted_tlds: Dict[str, float] = {}

_whois_bootstrap_lock = threading.Lock()
_whois_bootstrap_cache: Dict[str, Optional[str]] = {}
_whois_server_overrides: Optional[Dict[str, str]] = None
_whois_not_found_overrides: Optional[Dict[str, List[str]]] = None

# WHOIS fallback for TLDs that do not provide stable RDAP bootstrap entries
_WHOIS_SERVER_BY_TLD: Dict[str, str] = {
    "mx": "whois.mx",
    "co": "whois.registry.co",
}

_WHOIS_NOT_FOUND_MARKERS_BY_TLD: Dict[str, List[str]] = {
    "mx": [
        "no_se_encontro_el_objeto",
        "object_not_found",
        "no se encontro el objeto",
    ],
    "co": [
        "domain not found",
        "the queried object does not exist",
    ],
}

_WHOIS_GENERIC_NOT_FOUND_MARKERS = [
    "no match for",
    "not found",
    "no entries found",
    "no data found",
    "nothing found",
    "status: available",
    "domain available",
    "domain not found",
]

_WHOIS_TRANSIENT_ERROR_MARKERS = [
    "limit exceeded",
    "try again later",
    "temporarily unavailable",
    "quota exceeded",
]


def set_config(config_dict: Dict):
    """Update RDAP configuration"""
    global RDAP_BOOTSTRAP_URL, RDAP_TIMEOUT, RDAP_RETRIES, RDAP_BACKOFF_BASE
    global RDAP_BACKOFF_JITTER, RDAP_CONCURRENCY_DEFAULT, RDAP_CONCURRENCY_ES
    global RDAP_CONCURRENCY_IT, RDAP_TLD_OVERRIDES_JSON, RDAP_CONCURRENCY_OVERRIDES_JSON
    global RDAP_SESSION_POOL_CONNECTIONS, RDAP_SESSION_POOL_MAXSIZE
    global RDAP_FORBIDDEN_FALLBACK, RDAP_PARSE_ERROR_BODY
    global RDAP_RESTRICTED_ENABLE, RDAP_RESTRICTED_TTL
    global WHOIS_SERVER_OVERRIDES_JSON, WHOIS_NOT_FOUND_OVERRIDES_JSON
    global WHOIS_BOOTSTRAP_ENABLED, WHOIS_BOOTSTRAP_SERVER
    
    RDAP_BOOTSTRAP_URL = config_dict.get("RDAP_BOOTSTRAP_URL", RDAP_BOOTSTRAP_URL)
    RDAP_TIMEOUT = config_dict.get("RDAP_TIMEOUT", RDAP_TIMEOUT)
    RDAP_RETRIES = config_dict.get("RDAP_RETRIES", RDAP_RETRIES)
    RDAP_BACKOFF_BASE = config_dict.get("RDAP_BACKOFF_BASE", RDAP_BACKOFF_BASE)
    RDAP_BACKOFF_JITTER = config_dict.get("RDAP_BACKOFF_JITTER", RDAP_BACKOFF_JITTER)
    RDAP_CONCURRENCY_DEFAULT = config_dict.get("RDAP_CONCURRENCY_DEFAULT", RDAP_CONCURRENCY_DEFAULT)
    RDAP_CONCURRENCY_ES = config_dict.get("RDAP_CONCURRENCY_ES", RDAP_CONCURRENCY_ES)
    RDAP_CONCURRENCY_IT = config_dict.get("RDAP_CONCURRENCY_IT", RDAP_CONCURRENCY_IT)
    RDAP_TLD_OVERRIDES_JSON = config_dict.get("RDAP_TLD_OVERRIDES_JSON", RDAP_TLD_OVERRIDES_JSON)
    RDAP_CONCURRENCY_OVERRIDES_JSON = config_dict.get("RDAP_CONCURRENCY_OVERRIDES_JSON", RDAP_CONCURRENCY_OVERRIDES_JSON)
    RDAP_SESSION_POOL_CONNECTIONS = config_dict.get("RDAP_SESSION_POOL_CONNECTIONS", RDAP_SESSION_POOL_CONNECTIONS)
    RDAP_SESSION_POOL_MAXSIZE = config_dict.get("RDAP_SESSION_POOL_MAXSIZE", RDAP_SESSION_POOL_MAXSIZE)
    RDAP_FORBIDDEN_FALLBACK = config_dict.get("RDAP_FORBIDDEN_FALLBACK", RDAP_FORBIDDEN_FALLBACK)
    RDAP_PARSE_ERROR_BODY = config_dict.get("RDAP_PARSE_ERROR_BODY", RDAP_PARSE_ERROR_BODY)
    RDAP_RESTRICTED_ENABLE = config_dict.get("RDAP_RESTRICTED_ENABLE", RDAP_RESTRICTED_ENABLE)
    RDAP_RESTRICTED_TTL = config_dict.get("RDAP_RESTRICTED_TTL", RDAP_RESTRICTED_TTL)
    WHOIS_SERVER_OVERRIDES_JSON = config_dict.get("WHOIS_SERVER_OVERRIDES_JSON", WHOIS_SERVER_OVERRIDES_JSON)
    WHOIS_NOT_FOUND_OVERRIDES_JSON = config_dict.get("WHOIS_NOT_FOUND_OVERRIDES_JSON", WHOIS_NOT_FOUND_OVERRIDES_JSON)
    WHOIS_BOOTSTRAP_ENABLED = config_dict.get("WHOIS_BOOTSTRAP_ENABLED", WHOIS_BOOTSTRAP_ENABLED)
    WHOIS_BOOTSTRAP_SERVER = config_dict.get("WHOIS_BOOTSTRAP_SERVER", WHOIS_BOOTSTRAP_SERVER)

    # reset parsed overrides and concurrency semaphores
    global _rdap_tld_overrides, _rdap_concurrency_overrides, _whois_server_overrides, _whois_not_found_overrides
    _rdap_tld_overrides = None
    _rdap_concurrency_overrides = None
    _whois_server_overrides = None
    _whois_not_found_overrides = None
    with _rdap_sem_lock:
        _rdap_tld_semaphores.clear()


def _get_session() -> requests.Session:
    """Get or create thread-local session"""
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=max(8, int(RDAP_SESSION_POOL_CONNECTIONS)),
            pool_maxsize=max(8, int(RDAP_SESSION_POOL_MAXSIZE)),
        )
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (DomainAvailabilityChecker)",
            "Accept": "application/rdap+json, application/json;q=0.9, */*;q=0.1",
        })
        _thread_local.session = s
    return s


def _parse_overrides() -> Dict[str, str]:
    """Parse RDAP TLD overrides from JSON"""
    global _rdap_tld_overrides
    if _rdap_tld_overrides is not None:
        return _rdap_tld_overrides
    
    m: Dict[str, str] = {}
    if RDAP_TLD_OVERRIDES_JSON:
        try:
            raw = json.loads(RDAP_TLD_OVERRIDES_JSON)
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(k, str) and isinstance(v, str) and k and v:
                        base = v.strip()
                        if not base.endswith("/"):
                            base += "/"
                        m[k.lower().lstrip(".")] = base
        except Exception:
            pass
    
    _rdap_tld_overrides = m
    return _rdap_tld_overrides


def _parse_concurrency_overrides() -> Dict[str, int]:
    """Parse per-TLD RDAP concurrency overrides from JSON."""
    global _rdap_concurrency_overrides
    if _rdap_concurrency_overrides is not None:
        return _rdap_concurrency_overrides

    m: Dict[str, int] = {}
    if RDAP_CONCURRENCY_OVERRIDES_JSON:
        try:
            raw = json.loads(RDAP_CONCURRENCY_OVERRIDES_JSON)
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if not isinstance(k, str) or not k:
                        continue
                    try:
                        cap = int(v)
                    except Exception:
                        continue
                    if cap <= 0:
                        continue
                    m[k.lower().lstrip(".")] = cap
        except Exception:
            pass

    _rdap_concurrency_overrides = m
    return _rdap_concurrency_overrides


def _parse_whois_server_overrides() -> Dict[str, str]:
    """Parse WHOIS server overrides from JSON."""
    global _whois_server_overrides
    if _whois_server_overrides is not None:
        return _whois_server_overrides

    m: Dict[str, str] = {}
    if WHOIS_SERVER_OVERRIDES_JSON:
        try:
            raw = json.loads(WHOIS_SERVER_OVERRIDES_JSON)
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(k, str) and isinstance(v, str) and k and v:
                        m[k.lower().lstrip(".")] = v.strip()
        except Exception:
            pass

    _whois_server_overrides = m
    return _whois_server_overrides


def _parse_whois_not_found_overrides() -> Dict[str, List[str]]:
    """Parse WHOIS not-found markers overrides from JSON."""
    global _whois_not_found_overrides
    if _whois_not_found_overrides is not None:
        return _whois_not_found_overrides

    m: Dict[str, List[str]] = {}
    if WHOIS_NOT_FOUND_OVERRIDES_JSON:
        try:
            raw = json.loads(WHOIS_NOT_FOUND_OVERRIDES_JSON)
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if not isinstance(k, str) or not k:
                        continue
                    if isinstance(v, str):
                        markers = [v.lower()]
                    elif isinstance(v, list):
                        markers = [str(x).lower() for x in v if str(x).strip()]
                    else:
                        continue
                    if markers:
                        m[k.lower().lstrip(".")] = markers
        except Exception:
            pass

    _whois_not_found_overrides = m
    return _whois_not_found_overrides


def _get_whois_server_for_tld(tld: str) -> str:
    """Resolve WHOIS server for a TLD using overrides and IANA bootstrap."""
    tld = (tld or "").lower().lstrip(".")
    if not tld:
        return ""

    overrides = _parse_whois_server_overrides()
    if overrides.get(tld):
        return overrides[tld]

    if _WHOIS_SERVER_BY_TLD.get(tld):
        return _WHOIS_SERVER_BY_TLD[tld]

    if not WHOIS_BOOTSTRAP_ENABLED:
        return ""

    with _whois_bootstrap_lock:
        if tld in _whois_bootstrap_cache:
            return _whois_bootstrap_cache[tld] or ""

    resp = _whois_query(WHOIS_BOOTSTRAP_SERVER, tld, timeout=max(4.0, RDAP_TIMEOUT + 1.0))
    server = ""
    if resp:
        for line in resp.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().lower()
            if key in ("refer", "whois"):
                server = value.strip()
                break

    with _whois_bootstrap_lock:
        _whois_bootstrap_cache[tld] = server or None

    return server


def _is_rdap_restricted(tld: str) -> bool:
    """Return True if RDAP should be skipped for a restricted TLD."""
    if not RDAP_RESTRICTED_ENABLE:
        return False
    now = time.monotonic()
    with _rdap_restricted_lock:
        expires = _rdap_restricted_tlds.get(tld)
        if expires and expires > now:
            return True
        if expires:
            _rdap_restricted_tlds.pop(tld, None)
    return False


def _mark_rdap_restricted(tld: str):
    """Mark RDAP as restricted for a TLD for a short TTL."""
    if not RDAP_RESTRICTED_ENABLE:
        return
    ttl = max(60.0, float(RDAP_RESTRICTED_TTL))
    with _rdap_restricted_lock:
        _rdap_restricted_tlds[tld] = time.monotonic() + ttl


def load_rdap_bootstrap() -> Dict[str, str]:
    """Load RDAP bootstrap data from IANA"""
    global _rdap_tld_to_base
    with _rdap_bootstrap_lock:
        if _rdap_tld_to_base is not None:
            return _rdap_tld_to_base
        
        out: Dict[str, str] = {}
        try:
            s = _get_session()
            r = s.get(RDAP_BOOTSTRAP_URL, timeout=10)
            r.raise_for_status()
            data = r.json()
            
            for svc in data.get("services", []):
                if not (isinstance(svc, list) and len(svc) >= 2):
                    continue
                tlds = svc[0] or []
                urls = svc[1] or []
                if not urls:
                    continue
                base = urls[0]
                if not isinstance(base, str) or not base:
                    continue
                if not base.endswith("/"):
                    base += "/"
                for tld in tlds:
                    if isinstance(tld, str) and tld:
                        out[tld.lower().lstrip(".")] = base
            
            _rdap_tld_to_base = out
            return _rdap_tld_to_base
        except Exception:
            _rdap_tld_to_base = {}
            return _rdap_tld_to_base


def _get_tld_semaphore(tld: str) -> threading.Semaphore:
    """Get semaphore for TLD rate limiting"""
    tld = (tld or "").lower().lstrip(".")
    with _rdap_sem_lock:
        if tld in _rdap_tld_semaphores:
            return _rdap_tld_semaphores[tld]
        
        cap = RDAP_CONCURRENCY_DEFAULT
        overrides = _parse_concurrency_overrides()
        if overrides.get(tld):
            cap = overrides[tld]
        elif tld == "es":
            cap = RDAP_CONCURRENCY_ES
        elif tld == "it":
            cap = RDAP_CONCURRENCY_IT
        
        _rdap_tld_semaphores[tld] = threading.Semaphore(max(1, cap))
        return _rdap_tld_semaphores[tld]


def _sleep_backoff(attempt: int, retry_after: Optional[str] = None):
    """Sleep with exponential backoff"""
    if retry_after:
        try:
            sec = float(retry_after.strip())
            time.sleep(min(20.0, max(0.2, sec)))
            return
        except Exception:
            pass
    base = RDAP_BACKOFF_BASE * (2 ** attempt)
    jitter = random.uniform(-RDAP_BACKOFF_JITTER, 0.0 + RDAP_BACKOFF_JITTER)
    time.sleep(min(20.0, base + jitter))


def _rdap_hint_from_json(data) -> Optional[str]:
    """Interpret RDAP JSON error payloads that sometimes come with 200 OK."""
    if not isinstance(data, dict):
        return None

    error_code = data.get("errorCode")
    if isinstance(error_code, int):
        if error_code == 404:
            return "available"
        if error_code == 400:
            return "invalid"

    title = str(data.get("title") or "").lower()
    if "not found" in title or "object does not exist" in title:
        return "available"
    if "invalid" in title or "bad request" in title:
        return "invalid"

    return None


def _rdap_hint_from_response(resp: requests.Response) -> Optional[str]:
    """Attempt to parse RDAP response JSON for embedded error hints."""
    if not RDAP_PARSE_ERROR_BODY:
        return None

    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "json" not in ctype and "rdap" not in ctype:
        return None

    try:
        data = resp.json()
    except Exception:
        return None

    return _rdap_hint_from_json(data)


def _rdap_try_get(url: str) -> Tuple[int, Optional[str]]:
    """Make RDAP GET request with retries, returning status and optional hint."""
    s = _get_session()
    last_sc = 0
    last_hint: Optional[str] = None
    
    for attempt in range(RDAP_RETRIES + 1):
        try:
            resp = s.get(url, timeout=RDAP_TIMEOUT, allow_redirects=True, stream=True)
            try:
                sc = resp.status_code
                last_sc = sc
                hint = None
                if sc in (200, 400):
                    hint = _rdap_hint_from_response(resp)
                    if hint:
                        return sc, hint
                last_hint = hint
                if sc == 429 or (500 <= sc <= 599):
                    if attempt < RDAP_RETRIES:
                        _sleep_backoff(attempt, resp.headers.get("Retry-After"))
                        continue
                return sc, hint
            finally:
                resp.close()
        except Exception:
            last_sc = 0
            last_hint = None
            if attempt < RDAP_RETRIES:
                _sleep_backoff(attempt)
                continue
            return 0, None
    
    return last_sc, last_hint


def _whois_query(server: str, query: str, timeout: float = 8.0, max_bytes: int = 256000) -> str:
    """Run a WHOIS query and return raw text response."""
    try:
        with socket.create_connection((server, 43), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall((query + "\r\n").encode("utf-8", "ignore"))
            try:
                sock.shutdown(socket.SHUT_WR)
            except Exception:
                pass

            chunks: List[bytes] = []
            total = 0
            while total < max_bytes:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)

        return b"".join(chunks).decode("utf-8", "ignore")
    except Exception:
        return ""


def _whois_check(domain_ascii: str, tld: str) -> Optional[str]:
    """
    WHOIS fallback checker.

    Returns:
        Optional[str]: "available" | "taken" | "error" | None (unsupported TLD)
    """
    tld = (tld or "").lower().lstrip(".")
    server = _get_whois_server_for_tld(tld)
    if not server:
        return None

    resp = _whois_query(server, domain_ascii, timeout=max(4.0, RDAP_TIMEOUT + 1.0))
    if not resp.strip():
        return "error"

    resp_l = resp.lower()

    if any(marker in resp_l for marker in _WHOIS_TRANSIENT_ERROR_MARKERS):
        return "error"

    overrides = _parse_whois_not_found_overrides()
    tld_markers = overrides.get(tld, []) or _WHOIS_NOT_FOUND_MARKERS_BY_TLD.get(tld, [])
    markers = tld_markers or _WHOIS_GENERIC_NOT_FOUND_MARKERS
    if any(marker in resp_l for marker in markers):
        return "available"

    return "taken"


def rdap_check(domain: str) -> str:
    """
    Check domain availability via RDAP
    
    Returns:
        str: "available" | "taken" | "invalid" | "error"
    """
    domain = normalize_domain(domain)
    if not is_valid_domain(domain):
        return "invalid"
    
    domain_ascii = to_ascii(domain)
    parts = domain_ascii.split(".")
    if len(parts) < 2:
        return "invalid"
    tld = parts[-1].lower()

    if _is_rdap_restricted(tld):
        whois_res = _whois_check(domain_ascii, tld)
        if whois_res is not None:
            return whois_res
        return "error"
    
    bases: List[str] = []
    
    overrides = _parse_overrides()
    if overrides.get(tld):
        bases.append(overrides[tld])
    
    # explicit ccTLD fixes
    if tld == "es":
        bases.append("https://rdap.nic.es/rdap/")
        bases.append("https://rdap.nic.es/")
    if tld == "it":
        bases.append("https://rdap.nic.it/")
    
    # .com explicit
    if tld == "com":
        bases.append("https://rdap.verisign.com/com/v1/")
    
    m = load_rdap_bootstrap()
    if m.get(tld):
        bases.append(m[tld])
    
    bases = list(dict.fromkeys([b if b.endswith("/") else (b + "/") for b in bases]))
    if not bases:
        whois_res = _whois_check(domain_ascii, tld)
        if whois_res is not None:
            return whois_res
        return "error"
    
    path = "domain/" + quote(domain_ascii, safe=".-")
    sem = _get_tld_semaphore(tld)
    
    with sem:
        for base in bases:
            url = base + path
            sc, hint = _rdap_try_get(url)

            if hint == "available":
                return "available"
            if hint == "invalid":
                return "invalid"

            if sc == 401 or sc == 403:
                if RDAP_FORBIDDEN_FALLBACK:
                    _mark_rdap_restricted(tld)
                    whois_res = _whois_check(domain_ascii, tld)
                    if whois_res is not None:
                        return whois_res
                    return "error"
                return "taken"
            if sc == 200:
                return "taken"
            if sc == 404:
                return "available"
            if sc == 400:
                return "invalid"
            if sc == 0:
                continue
            if sc == 429 or (500 <= sc <= 599):
                continue

    whois_res = _whois_check(domain_ascii, tld)
    if whois_res is not None:
        return whois_res

    return "error"
