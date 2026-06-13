"""Flask API routes for the checker application"""

import io
import os
import re
import socket
import ssl
import threading
import time
import zipfile
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit, unquote
import html

import requests
from flask import Blueprint, request, jsonify, send_file, current_app, render_template


@contextmanager
def _no_env_proxies():
    """Context manager that temporarily removes HTTP(S)_PROXY env vars."""
    keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def _perform_request(url: str, **kwargs):
    """Wrapper around requests.get that respects a custom 'no_env' flag.

    When kwargs contains no_env=True we clear environment proxy variables
    before making the request so that `requests` cannot pick up any proxies
    from the environment.  The flag is removed before passing arguments to
    requests.get().
    """
    no_env = kwargs.pop("no_env", False)
    if no_env:
        with _no_env_proxies():
            return requests.get(url, **kwargs)
    else:
        return requests.get(url, **kwargs)

from .models import CheckerState
from .utils import dedupe, parse_tlds
from .services import dns_check, rdap_check, expand_domains

web_bp = Blueprint("web", __name__)
api_bp = Blueprint("api", __name__, url_prefix="/api")


@web_bp.route("/")
def index():
    """Serve main page"""
    return render_template("index.html")


def get_checker_state() -> CheckerState:
    """Return the checker state attached to the current app."""
    return current_app.checker_state


ARCHIVE_CDX_HTTPS_URL = "https://web.archive.org/cdx/search/cdx"
ARCHIVE_CDX_HTTP_URL = "http://web.archive.org/cdx/search/cdx"

_SPAM_ORDER = ("porn", "casino", "pharma", "betting", "ideographs", "chinese", "doorway", "parked")
_SPAM_PATTERNS = {
    "porn": [
        re.compile(r"\bporn\b", re.IGNORECASE),
        re.compile(r"\bporno\b", re.IGNORECASE),
        re.compile(r"\bxxx\b", re.IGNORECASE),
        re.compile(r"\badult\b", re.IGNORECASE),
        re.compile(r"\bsex\b", re.IGNORECASE),
        re.compile(r"\bsexo\b", re.IGNORECASE),
        re.compile(r"\bsexe\b", re.IGNORECASE),
        re.compile(r"\badult[oa]s?\b", re.IGNORECASE),
        re.compile(r"\bpornograf\w*\b", re.IGNORECASE),
        re.compile(r"\berotik\b", re.IGNORECASE),
        re.compile(r"\bnackt\b", re.IGNORECASE),
        re.compile(r"\bsexy\b", re.IGNORECASE),
        re.compile(r"\bnsfw\b", re.IGNORECASE),
        re.compile(r"\bnude\b", re.IGNORECASE),
        re.compile(r"\bnaked\b", re.IGNORECASE),
        re.compile(r"\berotic(a)?\b", re.IGNORECASE),
        re.compile(r"\bhardcore\b", re.IGNORECASE),
        re.compile(r"\bteens?\b", re.IGNORECASE),
        re.compile(r"\bvirgin\b", re.IGNORECASE),
        re.compile(r"\banal\b", re.IGNORECASE),
        re.compile(r"\blesbian\b", re.IGNORECASE),
        re.compile(r"\bgay\b", re.IGNORECASE),
        re.compile(r"\bmilf\b", re.IGNORECASE),
        re.compile(r"\bwebcam\b", re.IGNORECASE),
        re.compile(r"\bcamgirls?\b", re.IGNORECASE),
        re.compile(r"\bescort(s)?\b", re.IGNORECASE),
        re.compile(r"\u0438\u043d\u0442\u0438\u043c", re.IGNORECASE),  # "РёРЅС‚РёРј"
        re.compile(r"\u0433\u043e\u043b\u044b\u0435", re.IGNORECASE),  # "РіРѕР»С‹Рµ"
        re.compile(r"\u043f\u043e\u0440\u043d\u043e", re.IGNORECASE),  # "порно"
        re.compile(r"\u044d\u0440\u043e\u0442", re.IGNORECASE),  # "эрот"
        re.compile(r"\u0441\u0435\u043a\u0441", re.IGNORECASE),  # "секс"
    ],
    "casino": [
        re.compile(r"\bcasino\b", re.IGNORECASE),
        re.compile(r"\bslots?\b", re.IGNORECASE),
        re.compile(r"\bslot\s+machine(s)?\b", re.IGNORECASE),
        re.compile(r"\bjackpot\b", re.IGNORECASE),
        re.compile(r"\broulette\b", re.IGNORECASE),
        re.compile(r"\bblackjack\b", re.IGNORECASE),
        re.compile(r"\btragamonedas\b", re.IGNORECASE),
        re.compile(r"\bspielcasino\b", re.IGNORECASE),
        re.compile(r"\bspielbank\b", re.IGNORECASE),
        re.compile(r"\bpoker\b", re.IGNORECASE),
        re.compile(r"\bbaccarat\b", re.IGNORECASE),
        re.compile(r"\bbingo\b", re.IGNORECASE),
        re.compile(r"\bkeno\b", re.IGNORECASE),
        re.compile(r"\bcraps\b", re.IGNORECASE),
        re.compile(r"\blottery\b", re.IGNORECASE),
        re.compile(r"\u0431\u0438\u043d\u0433\u043e", re.IGNORECASE),  # "Р±РёРЅРіРѕ"
        re.compile(r"\u0434\u0436\u0435\u043a\u043f\u043e\u0442", re.IGNORECASE),  # "РґР¶РµРєРїРѕС‚"
        re.compile(r"\u043a\u0430\u0437\u0438\u043d\u043e", re.IGNORECASE),  # "казино"
        re.compile(r"\u0441\u043b\u043e\u0442", re.IGNORECASE),  # "слот"
        re.compile(r"\u0440\u0443\u043b\u0435\u0442\u043a", re.IGNORECASE),  # "рулетк"
        re.compile(r"\u043f\u043e\u043a\u0435\u0440", re.IGNORECASE),  # "покер"
    ],
    "pharma": [
        re.compile(r"\bpharma\b", re.IGNORECASE),
        re.compile(r"\bpharmacy\b", re.IGNORECASE),
        re.compile(r"\bfarmacia\b", re.IGNORECASE),
        re.compile(r"\bpharmacie\b", re.IGNORECASE),
        re.compile(r"\bapotheke\b", re.IGNORECASE),
        re.compile(r"\bmedicamentos?\b", re.IGNORECASE),
        re.compile(r"\bmedicina(s)?\b", re.IGNORECASE),
        re.compile(r"\bremedios?\b", re.IGNORECASE),
        re.compile(r"\bprescription\b", re.IGNORECASE),
        re.compile(r"\brx\b", re.IGNORECASE),
        re.compile(r"\bpills?\b", re.IGNORECASE),
        re.compile(r"\bviagra\b", re.IGNORECASE),
        re.compile(r"\bcialis\b", re.IGNORECASE),
        re.compile(r"\blevitra\b", re.IGNORECASE),
        re.compile(r"\btramadol\b", re.IGNORECASE),
        re.compile(r"\bxanax\b", re.IGNORECASE),
        re.compile(r"\bvalium\b", re.IGNORECASE),
        re.compile(r"\bcelebrex\b", re.IGNORECASE),
        re.compile(r"\bvicodin\b", re.IGNORECASE),
        re.compile(r"\bpercocet\b", re.IGNORECASE),
        re.compile(r"\bambien\b", re.IGNORECASE),
        re.compile(r"\bphentermine\b", re.IGNORECASE),
        re.compile(r"\bprozac\b", re.IGNORECASE),
        re.compile(r"\bpaxil\b", re.IGNORECASE),
        re.compile(r"\bmeridia\b", re.IGNORECASE),
        re.compile(r"\bultram\b", re.IGNORECASE),
        re.compile(r"\bsoma\b", re.IGNORECASE),
        re.compile(r"\boxycodone\b", re.IGNORECASE),
        re.compile(r"\bhydrocodone\b", re.IGNORECASE),
        re.compile(r"\bbenzo\b", re.IGNORECASE),
        re.compile(r"\bbuy\s+pills\b", re.IGNORECASE),
        re.compile(r"\u0444\u0430\u0440\u043c\u0430", re.IGNORECASE),  # "фарма"
        re.compile(r"\u0430\u043f\u0442\u0435\u043a", re.IGNORECASE),  # "аптек"
        re.compile(r"\u0442\u0430\u0431\u043b\u0435\u0442", re.IGNORECASE),  # "таблет"
        re.compile(r"\u043b\u0435\u043a\u0430\u0440", re.IGNORECASE),  # "лекар"
    ],
    "betting": [
        re.compile(r"\bbetting\b", re.IGNORECASE),
        re.compile(r"\bbets?\b", re.IGNORECASE),
        re.compile(r"\bapuestas?\b", re.IGNORECASE),
        re.compile(r"\bapostas?\b", re.IGNORECASE),
        re.compile(r"\bscommess\w*\b", re.IGNORECASE),
        re.compile(r"\bparis?\s+sportifs?\b", re.IGNORECASE),
        re.compile(r"\bsportwetten\b", re.IGNORECASE),
        re.compile(r"\bwetten\b", re.IGNORECASE),
        re.compile(r"\bwager(s|ing)?\b", re.IGNORECASE),
        re.compile(r"\bodds\b", re.IGNORECASE),
        re.compile(r"\bsportsbook\b", re.IGNORECASE),
        re.compile(r"\bsports?\s*betting\b", re.IGNORECASE),
        re.compile(r"\bbookmaker\b", re.IGNORECASE),
        re.compile(r"\bparlay\b", re.IGNORECASE),
        re.compile(r"\u043a\u043e\u044d\u0444\u0444", re.IGNORECASE),  # "РєРѕСЌС„С„"
        re.compile(r"\u044d\u043a\u0441\u043f\u0440\u0435\u0441\u0441", re.IGNORECASE),  # "СЌРєСЃРїСЂРµСЃСЃ"
        re.compile(r"\u0441\u0442\u0430\u0432\u043a", re.IGNORECASE),  # "ставк"
        re.compile(r"\u0431\u0443\u043a\u043c\u0435\u043a\u0435\u0440", re.IGNORECASE),  # "букмекер"
        re.compile(r"\u0442\u043e\u0442\u0430\u043b\u0438\u0437\u0430\u0442\u043e\u0440", re.IGNORECASE),  # "тотализатор"
    ],
    "doorway": [
        re.compile(r"\bdoorway\b", re.IGNORECASE),
        re.compile(r"\bdoorway\s+pages?\b", re.IGNORECASE),
        re.compile(r"\bgateway\s+pages?\b", re.IGNORECASE),
        re.compile(r"\bbridge\s+pages?\b", re.IGNORECASE),
        re.compile(r"\bentry\s+pages?\b", re.IGNORECASE),
        re.compile(r"\bjump\s+pages?\b", re.IGNORECASE),
        re.compile(r"\blanding\s+pages?\b", re.IGNORECASE),
        re.compile(r"\u0434\u043e\u0440\u0432\u0435\u0439", re.IGNORECASE),  # "дорвей"
        re.compile(r"\u0434\u043e\u0440\u0432\u0435\u0438", re.IGNORECASE),  # "дорвеи"
    ],
    "parked": [
        re.compile(r"\bthis\s+domain\s+is\s+for\s+sale\b", re.IGNORECASE),
        re.compile(r"\bdomain\s+(?:name\s+)?for\s+sale\b", re.IGNORECASE),
        re.compile(r"\bdomain\s+for\s+sale\b", re.IGNORECASE),
        re.compile(r"\bthis\s+domain\s+is\s+parked\b", re.IGNORECASE),
        re.compile(r"\bparked\s+domain\b", re.IGNORECASE),
        re.compile(r"\bdomain\s+parking\b", re.IGNORECASE),
        re.compile(r"\bbuy\s+this\s+domain\b", re.IGNORECASE),
        re.compile(r"\bpurchase\s+this\s+domain\b", re.IGNORECASE),
        re.compile(r"\bmake\s+an?\s+offer\b", re.IGNORECASE),
        re.compile(r"\binquire\s+about\s+this\s+domain\b", re.IGNORECASE),
        re.compile(r"\bpremium\s+domain\b", re.IGNORECASE),
        re.compile(r"\bdomaine\s+(?:a|à)\s+vendre\b", re.IGNORECASE),
        re.compile(r"\bdominio\s+en\s+venta\b", re.IGNORECASE),
        re.compile(r"\bdominio\s+in\s+vendita\b", re.IGNORECASE),
        re.compile(r"\bdom(i|í)nio\s+(?:a|à)\s+venda\b", re.IGNORECASE),
        re.compile(r"\bdomain\s+zu\s+verkaufen\b", re.IGNORECASE),
        re.compile(r"\bdomain\s+kaufen\b", re.IGNORECASE),
        re.compile(r"\bдомен\s+прода[её]тся\b", re.IGNORECASE),
        re.compile(r"\bкупить\s+домен\b", re.IGNORECASE),
        re.compile(r"\bдомен\s+на\s+продаж[еу]\b", re.IGNORECASE),
        re.compile(r"\bдомен\s+припаркован\b", re.IGNORECASE),
        re.compile(r"\bпарковк[ау]\s+домен[ау]\b", re.IGNORECASE),
        re.compile(r"\bдомен\s+выставлен\s+на\s+продажу\b", re.IGNORECASE),
        re.compile(r"\bдомены?\s+на\s+продаж[еу]\b", re.IGNORECASE),
    ],
}
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_PARKED_BRANDS = [
    re.compile(r"\bsedo\b", re.IGNORECASE),
    re.compile(r"\bafternic\b", re.IGNORECASE),
    re.compile(r"\bhugedomains\b", re.IGNORECASE),
    re.compile(r"\bparkingcrew\b", re.IGNORECASE),
    re.compile(r"\bbodis\b", re.IGNORECASE),
    re.compile(r"\bsedoparking\b", re.IGNORECASE),
    re.compile(r"\bdan(?:\.com|\s+com)\b", re.IGNORECASE),
    re.compile(r"\buniregistry\b", re.IGNORECASE),
    re.compile(r"\bgodaddy\b", re.IGNORECASE),
    re.compile(r"\bnamecheap\b", re.IGNORECASE),
    re.compile(r"\bnamebright\b", re.IGNORECASE),
    re.compile(r"\bname(?:\.com|\s+com)\b", re.IGNORECASE),
    re.compile(r"\bsquadhelp\b", re.IGNORECASE),
    re.compile(r"\bbrandpa\b", re.IGNORECASE),
    re.compile(r"\bdomainmarket\b", re.IGNORECASE),
    re.compile(r"\bflippa\b", re.IGNORECASE),
]
_CHINESE_SPAM_TERMS = [
    re.compile(r"\u5fae\u4fe1", re.IGNORECASE),  # еѕ®дїЎ (WeChat)
    re.compile(r"\u5382\u5bb6", re.IGNORECASE),  # 厂家
    re.compile(r"\u6279\u53d1", re.IGNORECASE),  # 批发
    re.compile(r"\u4f9b\u5e94", re.IGNORECASE),  # 供应
    re.compile(r"\u8054\u7cfb", re.IGNORECASE),  # 联系
    re.compile(r"\u4ef7\u683c", re.IGNORECASE),  # 价格
    re.compile(r"\u751f\u4ea7", re.IGNORECASE),  # 生产
    re.compile(r"\u8d2d\u4e70", re.IGNORECASE),  # 购买
    re.compile(r"\u5b9a\u5236", re.IGNORECASE),  # 定制
    re.compile(r"\u5de5\u5382", re.IGNORECASE),  # е·ҐеЋ‚
    re.compile(r"\u62a5\u4ef7", re.IGNORECASE),  # жЉҐд»·
    re.compile(r"\u8be2\u4ef7", re.IGNORECASE),  # иЇўд»·
    re.compile(r"\u8d77\u8ba2", re.IGNORECASE),  # иµ·и®ў (MOQ)
    re.compile(r"\u73b0\u8d27", re.IGNORECASE),  # зЋ°иґ§
    re.compile(r"\u91c7\u8d2d", re.IGNORECASE),  # і‡‡иґ­
    re.compile(r"\u6837\u54c1", re.IGNORECASE),  # ж ·е“Ѓ
    re.compile(r"\b(wholesale|manufacturer|supplier|factory|oem|odm|moq|rfq|quotation|quote|inquiry|price|wechat|whatsapp|alibaba|made\s+in\s+china|export|bulk|catalog|sample|contact\s+us)\b", re.IGNORECASE),
]

_LINK_ATTR_RE = re.compile(r"(?:href|src|data-href|data-url|data-link)\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
_PLAIN_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_DOORWAY_REDIRECT_RE = re.compile(r"(http-equiv\s*=\s*[\"']?refresh|window\.location|location\.href|document\.location)", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[^\W_]{4,}", re.IGNORECASE | re.UNICODE)
_STOPWORDS = {
    "this", "that", "with", "from", "your", "about", "have", "more", "free", "page",
    "home", "main", "site", "http", "https", "www", "html", "info", "news",
    "para", "como", "sobre", "mais", "com", "sem", "los", "las", "les", "des", "pour",
    "avec", "plus", "tous", "tout", "che", "come", "con", "per", "piu", "tutti",
    "der", "die", "und", "mit", "ein", "eine",
    "\u044d\u0442\u043e", "\u044d\u0442\u0430", "\u044d\u0442\u0438", "\u0432\u0430\u0448", "\u0432\u0430\u0448\u0430",
    "\u0447\u0442\u043e", "\u043a\u0430\u043a", "\u043d\u0430\u0448", "\u0442\u043e\u043b\u044c\u043a\u043e",
}

_TRACKING_PARAM_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "yclid", "msclkid", "dclid", "igshid", "cmpid",
    "clickid", "subid", "affid", "affiliate", "ref", "refid", "referrer",
}
_SCRIPT_PATTERNS = {
    "latin": re.compile(r"[A-Za-z]"),
    "cyrillic": re.compile(r"[\u0400-\u04FF]"),
    "cjk": re.compile(r"[\u4E00-\u9FFF]"),
    "hangul": re.compile(r"[\uAC00-\uD7A3]"),
    "hiragana_katakana": re.compile(r"[\u3040-\u30FF]"),
    "arabic": re.compile(r"[\u0600-\u06FF]"),
    "hebrew": re.compile(r"[\u0590-\u05FF]"),
    "devanagari": re.compile(r"[\u0900-\u097F]"),
    "thai": re.compile(r"[\u0E00-\u0E7F]"),
}

_URLHAUS_CACHE = {"ts": 0.0, "hosts": set()}
_BLOCKLIST_CACHE = {}
_URL_SPAM_KEYWORDS = {
    "porn": ["porn", "porno", "xxx", "adult", "sex", "sexy", "cam", "milf"],
    "casino": ["casino", "slot", "roulette", "blackjack", "jackpot", "poker", "bingo", "lotto"],
    "betting": ["bet", "bets", "betting", "bookmaker", "sportsbook", "odds", "wager", "parlay", "apuesta", "aposta"],
    "pharma": ["pharma", "pharmacy", "viagra", "cialis", "levitra", "xanax", "tramadol", "pills", "rx", "meds"],
    "chinese": ["wechat", "alibaba", "whatsapp", "madeinchina"],
    "doorway": ["doorway", "landing", "gateway", "bridge"],
    "parked": [
        "domain for sale", "buy domain", "buy this domain", "domain sale", "domain parking", "parked domain",
        "domain auction", "make offer", "sedo", "afternic", "hugedomains", "parkingcrew", "bodis", "sedoparking",
        "dan com", "uniregistry", "godaddy", "namecheap", "namebright", "name com", "squadhelp", "brandpa",
        "domainmarket", "flippa",
    ],
}


def _normalize_proxy_url(value: str) -> str:
    """Normalize a proxy entry to requests-compatible URL."""
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
    """Hide credentials when showing proxy in UI."""
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


def _proxy_kwargs(proxy_url: str):
    """Build requests kwargs for a specific proxy URL."""
    return {"proxies": {"http": proxy_url, "https": proxy_url}}


def _build_archive_request_candidates(proxy_url: str):
    """Build request candidates: manual proxy first, then optional direct fallback.

    The direct connection candidate sets explicit empty proxies to override any
    environment variables, ensuring we really connect to Wayback without the
    user-supplied proxy.
    """
    allow_direct_fallback = bool(current_app.config.get("ARCHIVE_DIRECT_FALLBACK", True))
    candidates = []

    if proxy_url:
        candidates.append({
            "mode": "proxy",
            "label": _mask_proxy_url(proxy_url),
            "req_kwargs": _proxy_kwargs(proxy_url),
        })

    if allow_direct_fallback or not proxy_url:
        # If the user supplied an explicit proxy we want to bypass environment
        # proxies when later falling back to a direct connection; otherwise we
        # simply leave `req_kwargs` empty so requests will use whatever the
        # system provides (env vars, WinINET settings, etc.).
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
    """Yield preferred CDX endpoints with optional HTTP fallback."""
    use_http_fallback = bool(current_app.config.get("ARCHIVE_CDX_ALLOW_HTTP_FALLBACK", True))
    yield ARCHIVE_CDX_HTTPS_URL
    if use_http_fallback:
        yield ARCHIVE_CDX_HTTP_URL


def _parse_cdx_page(payload):
    """Parse a CDX JSON page and extract rows + resume key + redirect column flag."""
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


def _fetch_archive_rows(domain: str, headers: dict, req_kwargs: dict, cdx_url: str, timeout_override=None, retries_override=None):
    """Fetch all archive rows in configured year range using resumeKey paging."""
    year_from = int(current_app.config.get("ARCHIVE_YEAR_FROM", 1998))
    year_to = int(current_app.config.get("ARCHIVE_YEAR_TO", 2026))
    page_size = int(current_app.config.get("ARCHIVE_CDX_PAGE_SIZE", 2000))
    max_pages = int(current_app.config.get("ARCHIVE_CDX_MAX_PAGES", 400))
    max_rows = int(current_app.config.get("ARCHIVE_CDX_MAX_ROWS", 600000))
    timeout = float(timeout_override if timeout_override is not None else current_app.config.get("ARCHIVE_TIMEOUT", 45))
    retries = int(retries_override if retries_override is not None else current_app.config.get("ARCHIVE_REQUEST_RETRIES", 3))
    max_seconds = float(current_app.config.get("ARCHIVE_MAX_SECONDS", 60))

    page_size = min(max(page_size, 100), 10000)
    max_pages = max(max_pages, 1)
    max_rows = max(max_rows, 1000)
    retries = min(max(retries, 1), 8)
    max_seconds = max(max_seconds, 5)

    base_params = {
        "url": domain,
        # Match Wayback "calendar" count for a domain home URL (not all pages/subdomains).
        "matchType": "exact",
        "output": "json",
        # Request explicit fields so any redirect metadata is returned.  By default
        # the CDX API only returns a handful of columns; without `fl` there is no
        # `redirect`/`redirecturl` field and our UI always shows "(no data)".
        # We ask for both names to be safe, the parser will pick whichever exists.
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


def _fmt_ts(ts: str) -> str:
    """Format Wayback timestamp to dd.mm.yyyy HH:MM."""
    if len(ts) < 12:
        return ts
    return f"{ts[6:8]}.{ts[4:6]}.{ts[:4]} {ts[8:10]}:{ts[10:12]}"


def _normalize_wayback_location(location: str) -> str:
    """Convert Wayback Location header to a human-friendly target URL."""
    loc = (location or "").strip()
    if not loc:
        return ""

    if loc.startswith("//"):
        return f"https:{loc}"

    # Typical Wayback redirect format:
    # /web/<timestamp><mode_>/http://target.example/path
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


def _probe_snapshot_redirect(ts: str, orig: str, headers: dict, req_kwargs: dict, timeout: float) -> tuple[str, bool]:
    """Resolve redirect target from a snapshot via id_ playback Location header.

    Proxies sometimes interfere with the raw Location header (e.g. by following
    redirects themselves or stripping the header).  When a proxy is in use we
    first try with the provided `req_kwargs`.  If the response contains no
    location or the request fails, we retry once without any proxies so that
    the direct Wayback response can be examined.  The return value is a pair
    `(redirect_target, used_direct)` where `used_direct` indicates whether the
    successful probe bypassed the proxy.
    """
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
    # first attempt using given kwargs (may include proxies)
    redirect_target = _single_probe(req_kwargs or {})

    # if proxies were used but no result obtained, try a direct probe.  When the
    # original kwargs included `no_env` it means the caller already wanted to
    # ignore environment proxies, so maintain that.  Otherwise a normal direct
    # probe still allows environment proxies (so corporate settings continue to
    # work when the user left the proxy field blank).
    if req_kwargs and req_kwargs.get("proxies") and not redirect_target:
        used_direct = True
        fb_kwargs = {"proxies": {"http": None, "https": None}}
        if req_kwargs.get("no_env"):
            fb_kwargs["no_env"] = True
        redirect_target = _single_probe(fb_kwargs)

    return redirect_target, used_direct


def _enrich_missing_redirects(rows, headers: dict, req_kwargs: dict):
    """Backfill missing redirect targets for 301/302 using lightweight snapshot probes."""
    if not rows:
        return rows, 0, 0, 0

    if not bool(current_app.config.get("ARCHIVE_REDIRECT_FETCH_ENABLED", True)):
        return rows, 0, 0, 0

    max_probe = max(int(current_app.config.get("ARCHIVE_REDIRECT_FETCH_MAX", 180)), 0)
    workers = max(int(current_app.config.get("ARCHIVE_REDIRECT_FETCH_WORKERS", 8)), 1)
    timeout = max(float(current_app.config.get("ARCHIVE_REDIRECT_FETCH_TIMEOUT", 6)), 1.0)

    missing = []
    empty_markers = {"", "-", "null", "none"}
    for idx, (ts, orig, status, redirect) in enumerate(rows):
        redirect_value = (redirect or "").strip()
        if status in ("301", "302") and redirect_value.lower() in empty_markers:
            missing.append(idx)

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


def _normalize_spam_text(value: str) -> str:
    """Normalize snapshot HTML or text for spam keyword matching."""
    if not value:
        return ""
    text = html.unescape(value)
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_url_for_spam(value: str) -> str:
    if not value:
        return ""
    token = html.unescape(value)
    try:
        token = unquote(token)
    except Exception:
        pass
    token = token.lower()
    token = re.sub(r"[^a-z0-9\u0400-\u04ff\u4e00-\u9fff]+", " ", token)
    token = re.sub(r"\s+", " ", token)
    return token.strip()


def _extract_host_from_url(value: str) -> str:
    if not value:
        return ""
    token = value.strip()
    if not token:
        return ""
    if token.startswith("//"):
        token = "http:" + token
    if not re.match(r"^https?://", token, re.IGNORECASE):
        token = "http://" + token.lstrip("/")
    try:
        host = urlsplit(token).hostname or ""
    except Exception:
        host = ""
    return host.lower()


def _dominant_script(text: str) -> tuple[str, int]:
    if not text:
        return "", 0
    counts = {key: len(pattern.findall(text)) for key, pattern in _SCRIPT_PATTERNS.items()}
    if not counts:
        return "", 0
    script = max(counts, key=counts.get)
    return script, counts.get(script, 0)


def _extract_link_candidates(raw_html: str) -> list[str]:
    if not raw_html:
        return []
    links = [m.group(1) for m in _LINK_ATTR_RE.finditer(raw_html)]
    links.extend(m.group(0) for m in _PLAIN_URL_RE.finditer(raw_html))
    return links


def _normalize_link_text(value: str) -> str:
    token = (value or "").strip()
    if not token:
        return ""
    low = token.lower()
    if low.startswith(("javascript:", "mailto:", "tel:")):
        return ""
    try:
        token = unquote(token)
    except Exception:
        pass
    return token


def _build_spam_haystacks(raw_html: str) -> tuple[str, str, str, str]:
    raw = html.unescape(raw_html or "")
    visible = re.sub(r"<[^>]+>", " ", raw)
    visible = _normalize_spam_text(visible)
    links = [_normalize_link_text(x) for x in _extract_link_candidates(raw)]
    link_blob = _normalize_spam_text(" ".join(x for x in links if x))
    combined = f"{visible} {link_blob}".strip()
    return visible, link_blob, combined, raw


def _count_tracking_params(url: str) -> int:
    if not url or "?" not in url:
        return 0
    try:
        query = urlsplit(url).query or ""
    except Exception:
        return 0
    if not query:
        return 0
    keys = [part.split("=", 1)[0].lower() for part in query.split("&") if part]
    return sum(1 for k in keys if k in _TRACKING_PARAM_KEYS)


def _analyze_snapshot_content(visible_text: str, link_blob: str, raw_html: str, base_domain: str) -> dict:
    links = [_normalize_link_text(x) for x in _extract_link_candidates(raw_html or "")]
    links = [x for x in links if x]
    total_links = len(links)
    external_links = 0
    tracking_hits = 0
    for link in links:
        host = _extract_host_from_url(link)
        if host and base_domain and not (host == base_domain or host.endswith("." + base_domain)):
            external_links += 1
        tracking_hits += _count_tracking_params(link)

    external_ratio = external_links / max(total_links, 1)
    tracking_ratio = tracking_hits / max(total_links, 1)
    link_spam_hits = _detect_spam_topics("", link_blob, "")
    keyword_stuffing = _looks_like_keyword_stuffing(visible_text)
    thin_content = len(visible_text) < 160 and total_links >= 8
    script, script_count = _dominant_script(visible_text)

    return {
        "text_len": len(visible_text),
        "link_total": total_links,
        "external_ratio": external_ratio,
        "tracking_ratio": tracking_ratio,
        "link_spam": link_spam_hits,
        "keyword_stuffing": keyword_stuffing,
        "thin_content": thin_content,
        "script": script,
        "script_count": script_count,
    }


def _build_topic_signature(text: str, ngram: int, max_ngrams: int) -> tuple[set[str], int]:
    cleaned = re.sub(r"[^\w]+", "", text or "", flags=re.UNICODE)
    cleaned = cleaned.replace("_", "")
    total = len(cleaned)
    if total < ngram:
        return set(), total
    ngram = max(3, int(ngram))
    max_ngrams = max(100, int(max_ngrams))
    step = max(1, total // max_ngrams)
    sig: set[str] = set()
    for i in range(0, total - ngram + 1, step):
        sig.add(cleaned[i:i + ngram])
        if len(sig) >= max_ngrams:
            break
    return sig, total


def _looks_like_keyword_stuffing(text: str) -> bool:
    if not text:
        return False
    tokens = [t for t in _TOKEN_RE.findall(text) if t not in _STOPWORDS]
    if len(tokens) < 200:
        return False
    counts = Counter(tokens)
    if not counts:
        return False
    top_count = max(counts.values())
    diversity = len(counts) / len(tokens)
    return (top_count / len(tokens)) > 0.12 and diversity < 0.45


def _looks_like_doorway(raw_html: str, text: str) -> bool:
    if raw_html and _DOORWAY_REDIRECT_RE.search(raw_html):
        if len(text) < 400 or _looks_like_keyword_stuffing(text):
            return True
    return _looks_like_keyword_stuffing(text)


def _looks_like_domain_parking(raw_html: str, text: str, link_text: str) -> bool:
    combined = " ".join(part for part in (text, link_text) if part).strip()
    if combined:
        for pattern in _SPAM_PATTERNS.get("parked", []):
            if pattern.search(combined):
                return True
        for pattern in _PARKED_BRANDS:
            if pattern.search(combined):
                if len(combined) < 700 or "domain" in combined or "домен" in combined:
                    return True
        if len(combined) < 320:
            if ("domain" in combined and ("sale" in combined or "buy" in combined or "offer" in combined)):
                return True
            if ("домен" in combined and ("продаж" in combined or "купить" in combined or "аукцион" in combined)):
                return True
    if raw_html:
        low = raw_html.lower()
        for pattern in _PARKED_BRANDS:
            if pattern.search(low):
                if len(text) < 500:
                    return True
    return False


def _detect_spam_topics(text: str, link_text: str = "", raw_html: str = "") -> list[str]:
    """Return list of spam topic keys detected in text and links."""
    combined = " ".join(part for part in (text, link_text) if part).strip()
    if not combined:
        return []

    hits = []
    for key in _SPAM_ORDER:
        if key == "ideographs":
            if _CJK_RE.search(combined):
                hits.append(key)
            continue
        if key == "chinese":
            if _CJK_RE.search(combined) and any(p.search(combined) for p in _CHINESE_SPAM_TERMS):
                hits.append(key)
            continue
        if key == "doorway":
            if _looks_like_doorway(raw_html, text):
                hits.append(key)
                continue
        if key == "parked":
            if _looks_like_domain_parking(raw_html, text, link_text):
                hits.append(key)
            continue
        patterns = _SPAM_PATTERNS.get(key, [])
        for pattern in patterns:
            if pattern.search(combined):
                hits.append(key)
                break
    return hits


def _detect_spam_from_url(value: str) -> list[str]:
    text = _normalize_url_for_spam(value)
    if not text:
        return []
    hits = []
    for key in _SPAM_ORDER:
        if key == "ideographs":
            if _CJK_RE.search(text):
                hits.append(key)
            continue
        if key == "chinese":
            if any(term in text for term in _URL_SPAM_KEYWORDS.get("chinese", [])):
                hits.append(key)
            continue
        if key == "doorway":
            if any(term in text for term in _URL_SPAM_KEYWORDS.get("doorway", [])):
                hits.append(key)
            continue
        if key == "parked":
            if any(term in text for term in _URL_SPAM_KEYWORDS.get("parked", [])):
                hits.append(key)
            elif ("domain" in text and ("sale" in text or "buy" in text or "offer" in text or "auction" in text)):
                hits.append(key)
            elif ("домен" in text and ("продаж" in text or "куп" in text or "аукцион" in text)):
                hits.append(key)
            continue
        keywords = _URL_SPAM_KEYWORDS.get(key, [])
        if keywords and any(term in text for term in keywords):
            hits.append(key)
    if not hits:
        hits = _detect_spam_topics(text)
    return hits


def _fetch_snapshot_sample(
    ts: str,
    orig: str,
    headers: dict,
    req_kwargs: dict,
    timeout: float,
    max_bytes: int,
    headers_override=None,
) -> str:
    """Fetch a small HTML sample for a snapshot."""
    url = f"https://web.archive.org/web/{ts}/{orig}"

    def _single_fetch(kwargs: dict) -> str:
        try:
            hdrs = headers_override if headers_override is not None else headers
            resp = _perform_request(
                url,
                headers=hdrs,
                timeout=timeout,
                allow_redirects=True,
                stream=True,
                **kwargs,
            )
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if content_type and ("text" not in content_type and "html" not in content_type and "xml" not in content_type):
                resp.close()
                return ""

            chunks = []
            total = 0
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if total >= max_bytes:
                    break
            resp.close()

            if not chunks:
                return ""
            data = b"".join(chunks)
            encoding = resp.encoding or "utf-8"
            return data.decode(encoding, errors="ignore")
        except Exception:
            return ""

    sample = _single_fetch(req_kwargs or {})

    if req_kwargs and req_kwargs.get("proxies") and not sample:
        fb_kwargs = {"proxies": {"http": None, "https": None}}
        if req_kwargs.get("no_env"):
            fb_kwargs["no_env"] = True
        sample = _single_fetch(fb_kwargs)

    return sample


def _probe_snapshot_spam(
    ts: str,
    orig: str,
    headers: dict,
    req_kwargs: dict,
    timeout: float,
    max_bytes: int,
    ngram: int,
    max_ngrams: int,
) -> tuple[list[str], set[str], int, dict]:
    """Return spam topic keys, topic signature, and content metrics for snapshot HTML."""
    sample = _fetch_snapshot_sample(ts, orig, headers, req_kwargs, timeout, max_bytes)
    visible_text, link_text, _combined, raw_html = _build_spam_haystacks(sample)
    hits = _detect_spam_topics(visible_text, link_text, raw_html)
    signature, text_len = _build_topic_signature(visible_text, ngram, max_ngrams)
    base_domain = _extract_host_from_url(orig)
    metrics = _analyze_snapshot_content(visible_text, link_text, raw_html, base_domain)
    return hits, signature, text_len, metrics


def _probe_snapshot_signature(
    ts: str,
    orig: str,
    headers: dict,
    req_kwargs: dict,
    timeout: float,
    max_bytes: int,
    ngram: int,
    max_ngrams: int,
    headers_override=None,
) -> tuple[set[str], int]:
    sample = _fetch_snapshot_sample(
        ts,
        orig,
        headers,
        req_kwargs,
        timeout,
        max_bytes,
        headers_override=headers_override,
    )
    visible_text, _link_text, _combined, _raw_html = _build_spam_haystacks(sample)
    signature, text_len = _build_topic_signature(visible_text, ngram, max_ngrams)
    return signature, text_len


def _is_spam_probe_candidate(status: str) -> bool:
    """Only check snapshots likely to have HTML content."""
    s = (status or "").strip()
    if not s or s == "-":
        return True
    return s.startswith("2") or s.startswith("3")


def _enrich_spam_flags(rows, headers: dict, req_kwargs: dict):
    """Probe snapshots for spam topics (keyword-based)."""
    if not rows:
        return {}, 0, 0, 0, {}, {}, [], {}

    if not bool(current_app.config.get("ARCHIVE_SPAM_CHECK_ENABLED", True)):
        return {}, 0, 0, 0, {}, {}, [], {}

    max_probe = max(int(current_app.config.get("ARCHIVE_SPAM_CHECK_MAX", 120)), 0)
    workers = max(int(current_app.config.get("ARCHIVE_SPAM_CHECK_WORKERS", 6)), 1)
    timeout = max(float(current_app.config.get("ARCHIVE_SPAM_CHECK_TIMEOUT", 6)), 1.0)
    max_bytes = max(int(current_app.config.get("ARCHIVE_SPAM_CHECK_MAX_BYTES", 250000)), 20000)
    ngram = int(current_app.config.get("ARCHIVE_TOPIC_NGRAM_SIZE", 4))
    max_ngrams = int(current_app.config.get("ARCHIVE_TOPIC_MAX_NGRAMS", 500))
    propagate_threshold = float(current_app.config.get("ARCHIVE_SPAM_PROPAGATE_THRESHOLD", 0.7))
    propagate_threshold = max(0.0, min(propagate_threshold, 1.0))

    eligible_total = 0
    candidates = []
    for idx, (ts, orig, status, _redirect) in enumerate(rows):
        if not ts or not orig or not _is_spam_probe_candidate(status):
            continue
        eligible_total += 1
        if len(candidates) < max_probe:
            candidates.append(idx)

    if not candidates or max_probe == 0:
        return {}, 0, 0, eligible_total, {}, {}, [], {}

    hits_by_idx = {}
    sig_by_idx = {}
    len_by_idx = {}
    metrics_by_idx = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(candidates))) as executor:
        futures = {
            executor.submit(
                _probe_snapshot_spam,
                rows[idx][0],
                rows[idx][1],
                headers,
                req_kwargs,
                timeout,
                max_bytes,
                ngram,
                max_ngrams,
            ): idx
            for idx in candidates
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                hits, sig, text_len, metrics = future.result()
            except Exception:
                hits, sig, text_len, metrics = [], set(), 0, {}
            if hits:
                hits_by_idx[idx] = hits
            if sig:
                sig_by_idx[idx] = sig
            if text_len:
                len_by_idx[idx] = text_len
            if metrics:
                metrics_by_idx[idx] = metrics

    propagate_labels: list[str] = []
    if hits_by_idx:
        ratio = len(hits_by_idx) / max(len(candidates), 1)
        if ratio >= propagate_threshold:
            union: set[str] = set()
            for vals in hits_by_idx.values():
                union.update(vals)
            if union:
                propagate_labels = sorted(union)

    return hits_by_idx, len(candidates), len(hits_by_idx), eligible_total, sig_by_idx, len_by_idx, propagate_labels, metrics_by_idx


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = a.intersection(b)
    union = a.union(b)
    if not union:
        return 0.0
    return len(inter) / len(union)


def _detect_topic_shifts(rows, signatures: dict, lengths: dict):
    """Detect topic shifts by comparing snapshot signatures."""
    if not rows:
        return {}, 0, 0
    if not bool(current_app.config.get("ARCHIVE_TOPIC_CHANGE_ENABLED", True)):
        return {}, 0, 0

    threshold = float(current_app.config.get("ARCHIVE_TOPIC_CHANGE_THRESHOLD", 0.18))
    min_chars = int(current_app.config.get("ARCHIVE_TOPIC_CHANGE_MIN_CHARS", 320))
    threshold = max(0.02, min(threshold, 0.95))
    min_chars = max(80, min_chars)

    shifts = {}
    checked = 0
    last_sig = None

    for idx, _row in enumerate(rows):
        sig = signatures.get(idx)
        if not sig:
            continue
        if lengths.get(idx, 0) < min_chars:
            continue
        checked += 1
        if last_sig is None:
            last_sig = sig
            continue
        similarity = _jaccard_similarity(sig, last_sig)
        if similarity < threshold:
            shifts[idx] = True
        last_sig = sig

    return shifts, checked, len(shifts)


def _detect_language_shifts(rows, scripts: dict, lengths: dict):
    """Detect dominant script shifts across snapshots."""
    if not rows:
        return {}, 0, 0
    if not bool(current_app.config.get("ARCHIVE_LANG_SHIFT_ENABLED", True)):
        return {}, 0, 0

    min_chars = int(current_app.config.get("ARCHIVE_LANG_SHIFT_MIN_CHARS", 280))
    min_chars = max(80, min_chars)

    shifts = {}
    checked = 0
    last_script = ""

    for idx, _row in enumerate(rows):
        script = scripts.get(idx, "")
        if not script:
            continue
        if lengths.get(idx, 0) < min_chars:
            continue
        checked += 1
        if not last_script:
            last_script = script
            continue
        if script != last_script:
            shifts[idx] = True
        last_script = script

    return shifts, checked, len(shifts)


def _detect_cloaking(rows, signatures: dict, lengths: dict, headers: dict, req_kwargs: dict):
    """Detect cloaking by comparing content with a bot User-Agent."""
    if not rows:
        return {}, 0, 0
    if not bool(current_app.config.get("ARCHIVE_CLOAK_CHECK_ENABLED", True)):
        return {}, 0, 0

    max_probe = max(int(current_app.config.get("ARCHIVE_CLOAK_CHECK_MAX", 40)), 0)
    timeout = max(float(current_app.config.get("ARCHIVE_CLOAK_CHECK_TIMEOUT", 6)), 1.0)
    max_bytes = max(int(current_app.config.get("ARCHIVE_CLOAK_CHECK_MAX_BYTES", 200000)), 20000)
    ngram = int(current_app.config.get("ARCHIVE_TOPIC_NGRAM_SIZE", 4))
    max_ngrams = int(current_app.config.get("ARCHIVE_TOPIC_MAX_NGRAMS", 500))
    threshold = float(current_app.config.get("ARCHIVE_CLOAK_CHECK_THRESHOLD", 0.18))
    min_chars = int(current_app.config.get("ARCHIVE_CLOAK_CHECK_MIN_CHARS", 280))
    bot_ua = current_app.config.get(
        "ARCHIVE_CLOAK_CHECK_UA",
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    )

    threshold = max(0.02, min(threshold, 0.95))
    min_chars = max(80, min_chars)

    candidates = []
    for idx, _row in enumerate(rows):
        if len(candidates) >= max_probe:
            break
        if lengths.get(idx, 0) >= min_chars and signatures.get(idx):
            candidates.append(idx)

    if not candidates or max_probe == 0:
        return {}, 0, 0

    bot_headers = dict(headers or {})
    bot_headers["User-Agent"] = bot_ua

    cloak_flags = {}
    with ThreadPoolExecutor(max_workers=min(6, len(candidates))) as executor:
        futures = {
            executor.submit(
                _probe_snapshot_signature,
                rows[idx][0],
                rows[idx][1],
                headers,
                req_kwargs,
                timeout,
                max_bytes,
                ngram,
                max_ngrams,
                bot_headers,
            ): idx
            for idx in candidates
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                sig_bot, text_len = future.result()
            except Exception:
                sig_bot, text_len = set(), 0
            sig_norm = signatures.get(idx, set())
            if not sig_norm or not sig_bot:
                continue
            if lengths.get(idx, 0) < min_chars or text_len < min_chars:
                continue
            similarity = _jaccard_similarity(sig_norm, sig_bot)
            if similarity < threshold:
                cloak_flags[idx] = True

    return cloak_flags, len(candidates), len(cloak_flags)


def _load_blocklist_file(path: str):
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


def _load_blocklists(paths_value: str):
    if not paths_value:
        return set()
    parts = [p.strip() for p in re.split(r"[;,]+", paths_value) if p.strip()]
    merged = set()
    for path in parts:
        merged.update(_load_blocklist_file(path))
    return merged


def _load_urlhaus_hosts(url: str, timeout: float, ttl: float):
    if not url:
        return set()
    now = time.monotonic()
    cached = _URLHAUS_CACHE.get("hosts") or set()
    ts = _URLHAUS_CACHE.get("ts", 0.0)
    if cached and (now - ts) < ttl:
        return cached

    hosts = set()
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


def _check_safe_browsing(urls: list[str], timeout: float):
    api_key = current_app.config.get("ARCHIVE_REPUTATION_SAFE_BROWSING_KEY", "")
    if not api_key:
        return None
    endpoint = f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={api_key}"
    client_id = current_app.config.get("ARCHIVE_REPUTATION_SAFE_BROWSING_CLIENT_ID", "checker")
    client_version = current_app.config.get("ARCHIVE_REPUTATION_SAFE_BROWSING_CLIENT_VERSION", "1.0")
    threat_types = [
        "MALWARE",
        "SOCIAL_ENGINEERING",
        "UNWANTED_SOFTWARE",
        "POTENTIALLY_HARMFUL_APPLICATION",
    ]
    payload = {
        "client": {"clientId": client_id, "clientVersion": client_version},
        "threatInfo": {
            "threatTypes": threat_types,
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


def _check_phishtank(url: str, timeout: float):
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


def _check_reputation(domain: str):
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


def _parse_rdap_event_date(events):
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


def _fetch_rdap_age_days(domain: str):
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


def _fetch_tls_age_days(domain: str):
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


def _compute_domain_risk(
    rows,
    spam_hits: dict,
    spam_checked: int,
    spam_flagged: int,
    spam_propagated_labels: list[str],
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
):
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
    parked_heavy = ("parked" in (spam_propagated_labels or [])) or parked_ratio >= 0.4 or url_parked_ratio >= 0.4
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
        avg_external = sum(m.get("external_ratio", 0.0) for m in metrics_by_idx.values()) / max(len(metrics_by_idx), 1)
        avg_links = sum(m.get("link_total", 0) for m in metrics_by_idx.values()) / max(len(metrics_by_idx), 1)
        if avg_external > 0.8 and avg_links > 15:
            flags.append("link_farm")
            score += 8
        avg_tracking = sum(m.get("tracking_ratio", 0.0) for m in metrics_by_idx.values()) / max(len(metrics_by_idx), 1)
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


def _get_domain_tld(domain: str) -> str:
    """Extract TLD from a normalized domain string."""
    d = (domain or "").strip().lower().rstrip(".")
    if "." not in d:
        return ""
    return d.rsplit(".", 1)[-1]


def _run_thread_pool(items, worker, max_workers: int, max_in_flight: int = None, should_cancel=None):
    """Run worker over items with bounded in-flight futures to reduce memory usage."""
    max_workers = max(1, int(max_workers))
    if max_in_flight is None:
        max_in_flight = max_workers * 4
    max_in_flight = max(max_workers, int(max_in_flight))

    iterator = iter(items)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}

        def _submit_next() -> bool:
            if should_cancel and should_cancel():
                return False
            try:
                item = next(iterator)
            except StopIteration:
                return False
            futures[executor.submit(worker, item)] = item
            return True

        while len(futures) < max_in_flight and _submit_next():
            pass

        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for fut in done:
                futures.pop(fut, None)
                try:
                    fut.result()
                except Exception:
                    pass

            while len(futures) < max_in_flight and _submit_next():
                pass


def _dedupe_results(state: CheckerState):
    """Deduplicate result buckets in-place."""
    with state.lock:
        state.available = dedupe(state.available)
        state.taken = dedupe(state.taken)
        state.invalid = dedupe(state.invalid)
        state.errors = dedupe(state.errors)


def run_check(
    state: CheckerState,
    domains_raw: list,
    threads: int,
    rdap_recheck_errors: bool,
    final_check_enabled: bool = True,
    final_check_workers: int = 12,
    dns_strict_tlds: list = None,
):
    """Main checking pipeline."""
    try:
        strict_set = set(dns_strict_tlds or [])

        with state.lock:
            state.stage = "dns"
            state.total = len(domains_raw)
            state.checked = 0
            state.final_total = 0
            state.final_checked = 0
            state.final_errors = 0
            state.available = []
            state.taken = []
            state.invalid = []
            state.errors = []
            state.current_domain = ""
            state.message = "Started (DNS prefilter)"

        def dns_worker(domain: str):
            if state.is_stop_requested():
                return

            try:
                result = dns_check(domain)
            except Exception:
                result = "error"

            if state.is_stop_requested():
                return

            tld = _get_domain_tld(domain)
            is_strict = bool(strict_set) and tld in strict_set

            with state.lock:
                if state.stop_requested:
                    return

                if result == "available":
                    state.available.append(domain)
                elif result == "taken":
                    state.taken.append(domain)
                elif result == "invalid":
                    state.invalid.append(domain)
                elif result == "unknown":
                    if is_strict:
                        state.taken.append(domain)
                    else:
                        state.errors.append(domain)
                else:
                    state.errors.append(domain)

                state.checked += 1
                state.current_domain = domain
                state.message = f"Checked {state.checked}/{state.total} (DNS prefilter)"

        _run_thread_pool(domains_raw, dns_worker, max_workers=max(1, threads), should_cancel=state.is_stop_requested)

        if state.is_stop_requested():
            _dedupe_results(state)
            state.finish(stage="stopped", message="Stopped by user.")
            return

        if not final_check_enabled:
            _dedupe_results(state)
            state.finish(stage="done", message="Done!")
            return

        with state.lock:
            available_candidates = dedupe(state.available)
            error_candidates = dedupe(state.errors)
            final_candidates = list(available_candidates)
            if rdap_recheck_errors:
                final_candidates.extend(error_candidates)
            final_candidates = dedupe(final_candidates)

            state.stage = "final"
            state.final_total = len(final_candidates)
            state.final_checked = 0
            state.final_errors = 0
            state.current_domain = ""
            state.message = f"Final check (RDAP): 0/{state.final_total}"
            state.available = []
            if rdap_recheck_errors:
                state.errors = []

        def final_worker(dom: str):
            if state.is_stop_requested():
                return

            try:
                res = rdap_check(dom)
            except Exception:
                res = "error"

            if state.is_stop_requested():
                return

            with state.lock:
                if state.stop_requested:
                    return

                if res == "available":
                    state.available.append(dom)
                elif res == "taken":
                    state.taken.append(dom)
                elif res == "invalid":
                    state.invalid.append(dom)
                else:
                    state.errors.append(dom)
                    state.final_errors += 1

                state.final_checked += 1
                state.current_domain = dom
                state.message = f"Final check (RDAP): {state.final_checked}/{state.final_total}"

        _run_thread_pool(
            final_candidates,
            final_worker,
            max_workers=max(1, final_check_workers),
            should_cancel=state.is_stop_requested,
        )

        _dedupe_results(state)
        if state.is_stop_requested():
            state.finish(stage="stopped", message="Stopped by user.")
        else:
            state.finish(stage="done", message="Done!")

    except Exception as e:
        print(f"ERROR in run_check: {e}")
        import traceback
        traceback.print_exc()
        _dedupe_results(state)
        state.fail(f"Error: {str(e)}")


@api_bp.route("/status", methods=["GET"])
def get_status():
    """Get current checker status"""
    return jsonify(get_checker_state().to_dict())


@api_bp.route("/check", methods=["POST"])
def start_check():
    """Start new domain check"""
    data = request.json or {}

    domains_str = (data.get("domains") or "").strip()
    # threads may come from the frontend; ensure it's a valid integer and fall
    # back to the default if parsing fails or the value is nonsensical.
    try:
        threads = int(data.get("threads", 32))
    except Exception:
        threads = 32
    tlds_raw = (data.get("tlds") or "").strip()
    rdap_recheck_errors = bool(data.get("rdap_recheck_errors", False))
    
    if not domains_str:
        return jsonify({"error": "No domains"}), 400
    
    lines = [x.strip() for x in domains_str.splitlines() if x.strip()]
    if not lines:
        return jsonify({"error": "No input lines"}), 400
    
    tlds = parse_tlds(tlds_raw)
    if not tlds:
        # fall back to default list configured in the app
        tlds = parse_tlds(current_app.config.get("DEFAULT_TLDS", ""))
    expanded_domains = expand_domains(lines, tlds=tlds)
    # protect against absurdly large batches
    max_domains = int(current_app.config.get("MAX_DOMAINS", 200000))
    if len(expanded_domains) > max_domains:
        return jsonify({"error": f"Too many domains ({len(expanded_domains)})"}), 400
    domains = expanded_domains
    if not domains:
        return jsonify({"error": "No domains after expansion"}), 400
    
    threads = max(1, min(128, threads))
    
    # Get config before starting thread
    final_check_enabled = current_app.config.get("FINAL_CHECK_ENABLED", True)
    final_check_workers = current_app.config.get("FINAL_CHECK_WORKERS", 12)
    
    dns_strict_raw = current_app.config.get("DNS_PREFILTER_STRICT_TLDS", "")
    dns_strict_tlds = parse_tlds(dns_strict_raw)
    state = get_checker_state()
    if not state.begin_run(len(domains)):
        return jsonify({"error": "Scan already in progress"}), 409

    # Start checking in background thread
    t = threading.Thread(
        target=run_check,
        args=(state, domains, threads, rdap_recheck_errors, final_check_enabled, final_check_workers, dns_strict_tlds),
    )
    t.daemon = True
    try:
        t.start()
    except Exception:
        state.fail("Error: could not start background worker")
        raise
    
    rdap_bootstrap_url = current_app.config.get("RDAP_BOOTSTRAP_URL", "")
    
    return jsonify({
        "status": "started",
        "final_check_enabled": final_check_enabled,
        "final_check_workers": final_check_workers,
        "rdap_bootstrap_url": rdap_bootstrap_url,
        "expanded_total": len(expanded_domains),
        "filtered_total": len(domains),
        "tlds": tlds,
        "rdap_recheck_errors": rdap_recheck_errors,
    })


@api_bp.route("/stop", methods=["POST"])
def stop_check():
    """Request cancellation for the active domain check."""
    state = get_checker_state()
    if not state.request_stop():
        return jsonify({"error": "No active scan"}), 409

    return jsonify({"status": "stopping"})


@api_bp.route("/download/<result_type>", methods=["GET"])
def download_results(result_type):
    """Download results as text file"""
    state = get_checker_state()

    with state.lock:
        if result_type == "available":
            data = "\n".join(state.available)
        elif result_type == "taken":
            data = "\n".join(state.taken)
        elif result_type == "invalid":
            data = "\n".join(state.invalid)
        elif result_type == "errors":
            data = "\n".join(state.errors)
        else:
            return jsonify({"error": "Invalid type"}), 400

    mem_file = io.BytesIO((data or "").encode("utf-8"))
    mem_file.seek(0)
    return send_file(
        mem_file,
        as_attachment=True,
        download_name=f"{result_type}.txt",
        mimetype="text/plain; charset=utf-8",
    )


@api_bp.route("/download-all", methods=["GET"])
def download_all_results():
    """Download all current results as a ZIP archive."""
    state = get_checker_state()
    with state.lock:
        payloads = {
            "available.txt": "\n".join(state.available),
            "taken.txt": "\n".join(state.taken),
            "invalid.txt": "\n".join(state.invalid),
            "errors.txt": "\n".join(state.errors),
        }

    mem_file = io.BytesIO()
    with zipfile.ZipFile(mem_file, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in payloads.items():
            archive.writestr(name, content or "")
    mem_file.seek(0)

    return send_file(
        mem_file,
        as_attachment=True,
        download_name="checker-results.zip",
        mimetype="application/zip",
    )


@api_bp.route("/archive", methods=["POST"])
def get_archive_data():
    """Get Wayback Machine archive data"""
    from .utils import normalize_domain

    payload = request.json or {}
    domain = payload.get("domain", "")
    domain = normalize_domain(domain)
    if not domain:
        return jsonify({"error": "No domain"}), 400
    raw_proxy = (payload.get("proxy") or "").strip()
    proxy_url = _normalize_proxy_url(raw_proxy) if raw_proxy else ""

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    proxy_state = {
        "enabled": bool(proxy_url),
        "mode": "proxy" if proxy_url else "direct",
        "current": _mask_proxy_url(proxy_url) if proxy_url else "Direct connection",
    }
    year_from = int(current_app.config.get("ARCHIVE_YEAR_FROM", 1998))
    year_to = int(current_app.config.get("ARCHIVE_YEAR_TO", 2026))
    verify_empty_fallback = bool(current_app.config.get("ARCHIVE_VERIFY_EMPTY_WITH_FALLBACK", True))
    proxy_timeout = float(current_app.config.get("ARCHIVE_PROXY_TIMEOUT", 10))
    proxy_retries = int(current_app.config.get("ARCHIVE_PROXY_REQUEST_RETRIES", 1))

    try:
        rows = []
        truncated = False
        used_connection = ""
        used_cdx_url = ""
        used_req_kwargs = {}
        last_error = None

        for candidate in _build_archive_request_candidates(proxy_url):
            cdx_urls = list(_iter_archive_cdx_urls())
            for cdx_url in cdx_urls:
                try:
                    timeout_value = proxy_timeout if candidate["mode"] == "proxy" else None
                    retries_value = proxy_retries if candidate["mode"] == "proxy" else None
                    rows, year_from, year_to, truncated, has_redirect_col = _fetch_archive_rows(
                        domain,
                        headers,
                        candidate["req_kwargs"],
                        cdx_url=cdx_url,
                        timeout_override=timeout_value,
                        retries_override=retries_value,
                    )
                    if truncated and not rows:
                        last_error = RuntimeError("Archive fetch exceeded time limit before first page.")
                        continue
                    if verify_empty_fallback and candidate["mode"] == "proxy" and not rows:
                        # Some proxies can return misleading empty CDX responses; try next candidate.
                        continue
                    if candidate["mode"] == "proxy" and rows:
                        redirect_fallback = bool(current_app.config.get("ARCHIVE_REDIRECT_FALLBACK_ON_MISSING_COLUMNS", True))
                        if redirect_fallback:
                            has_redirect_candidates = any(r[2] in ("301", "302") for r in rows)
                            if has_redirect_candidates and not has_redirect_col:
                                last_error = RuntimeError("Proxy CDX response missing redirect columns.")
                                continue
                    used_connection = candidate["label"]
                    used_cdx_url = cdx_url
                    used_req_kwargs = candidate["req_kwargs"]
                    break
                except Exception as exc:
                    last_error = exc
            if used_connection:
                break

        if not used_connection:
            message = "Wayback request failed via all attempted connections."
            if last_error:
                message = f"{message} Last error: {str(last_error)}"
            return jsonify({
                "results": [],
                "total_results": 0,
                "range": {"from": year_from, "to": year_to},
                "proxy": proxy_state,
                "used_connection": "",
                "cdx_endpoint": "",
                "fetch_error": message,
                "truncated": False,
            })

        rows, _redirect_resolved, _redirect_attempted, _redirect_direct = _enrich_missing_redirects(rows, headers, used_req_kwargs)
        (
            spam_hits,
            spam_checked,
            spam_flagged,
            spam_total,
            topic_sigs,
            topic_lengths,
            spam_propagated_labels,
            metrics_by_idx,
        ) = _enrich_spam_flags(rows, headers, used_req_kwargs)
        topic_shifts, topic_checked, topic_shifted = _detect_topic_shifts(rows, topic_sigs, topic_lengths)
        scripts_by_idx = {idx: m.get("script", "") for idx, m in (metrics_by_idx or {}).items()}
        language_shifts, language_checked, language_shifted = _detect_language_shifts(rows, scripts_by_idx, topic_lengths)
        cloaking_flags, cloaking_checked, cloaking_detected = _detect_cloaking(
            rows, topic_sigs, topic_lengths, headers, used_req_kwargs
        )

        results = []
        url_spam_count = 0
        url_spam_labels = Counter()
        for idx, (ts, orig, status, redirect) in enumerate(rows):
            # strip whitespace and normalize miscellaneous empty markers
            redirect_value = (redirect or "").strip()
            if redirect_value.lower() in ("-", "null", "none"):
                redirect_value = ""
            # some redirects in CDX are full Wayback URLs such as
            # "https://web.archive.org/web/20241222141713/https://target/...";
            # the UI should show just the target part.  Use the helper that is
            # already used elsewhere for probe responses.
            if redirect_value:
                redirect_value = _normalize_wayback_location(redirect_value)
            row_spam = spam_hits.get(idx, [])
            url_spam = _detect_spam_from_url(orig)
            if redirect_value:
                url_spam.extend(_detect_spam_from_url(redirect_value))
            if url_spam:
                url_spam_count += 1
                for label in url_spam:
                    if label:
                        url_spam_labels[label] += 1
            if row_spam or url_spam or spam_propagated_labels:
                merged = []
                for label in (row_spam + url_spam + spam_propagated_labels):
                    if label and label not in merged:
                        merged.append(label)
                row_spam = merged
            results.append({
                "date": _fmt_ts(ts),
                "status": status,
                "link": f"https://web.archive.org/web/{ts}/{orig}",
                "redirect": redirect_value,
                "spam": row_spam,
                "topic_shift": bool(topic_shifts.get(idx)),
                "language_shift": bool(language_shifts.get(idx)),
                "cloaking": bool(cloaking_flags.get(idx)),
            })

        reputation = _check_reputation(domain)
        rdap_age_days = _fetch_rdap_age_days(domain)
        tls_age_days = _fetch_tls_age_days(domain)
        risk = _compute_domain_risk(
            rows,
            spam_hits,
            spam_checked,
            spam_flagged,
            spam_propagated_labels,
            url_spam_count,
            url_spam_labels,
            metrics_by_idx,
            topic_checked,
            topic_shifted,
            language_checked,
            language_shifted,
            cloaking_checked,
            cloaking_detected,
            reputation,
            rdap_age_days,
            tls_age_days,
        )

        return jsonify({
            "results": results,
            "total_results": len(results),
            "range": {"from": year_from, "to": year_to},
            "proxy": proxy_state,
            "used_connection": used_connection,
            "cdx_endpoint": used_cdx_url,
            "redirects_resolved": _redirect_resolved,
            "redirects_probed": _redirect_attempted,
            "redirects_direct_fallback": _redirect_direct,
            "spam_checked": spam_checked,
            "spam_flagged": spam_flagged,
            "spam_total": spam_total,
            "topic_checked": topic_checked,
            "topic_shifted": topic_shifted,
            "language_checked": language_checked,
            "language_shifted": language_shifted,
            "cloaking_checked": cloaking_checked,
            "cloaking_detected": cloaking_detected,
            "reputation": reputation,
            "risk": risk,
            "fetch_error": "",
            "truncated": truncated,
        })
    except requests.Timeout:
        return jsonify({
            "results": [],
            "total_results": 0,
            "range": {"from": year_from, "to": year_to},
            "proxy": proxy_state,
            "used_connection": "",
            "cdx_endpoint": "",
            "fetch_error": "Wayback request timed out.",
            "truncated": False,
        })
    except Exception as exc:
        return jsonify({
            "results": [],
            "total_results": 0,
            "range": {"from": year_from, "to": year_to},
            "proxy": proxy_state,
            "used_connection": "",
            "cdx_endpoint": "",
            "fetch_error": f"Wayback request failed: {str(exc)}",
            "truncated": False,
        })
