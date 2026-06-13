"""Spam and content analysis for Wayback Machine snapshots.

Detects spam topics (porn, casino, pharma, betting, parked domains, doorways),
topic/language shifts between snapshots, and cloaking via bot-UA comparison.
"""

import html
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit, unquote

from flask import current_app

from .fetcher import _perform_request


# ---------------------------------------------------------------------------
# Spam topic ordering and keyword patterns
# ---------------------------------------------------------------------------

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
        re.compile(r"интим", re.IGNORECASE),
        re.compile(r"голые", re.IGNORECASE),
        re.compile(r"порно", re.IGNORECASE),
        re.compile(r"эрот", re.IGNORECASE),
        re.compile(r"секс", re.IGNORECASE),
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
        re.compile(r"бинго", re.IGNORECASE),
        re.compile(r"джекпот", re.IGNORECASE),
        re.compile(r"казино", re.IGNORECASE),
        re.compile(r"слот", re.IGNORECASE),
        re.compile(r"рулетк", re.IGNORECASE),
        re.compile(r"покер", re.IGNORECASE),
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
        re.compile(r"фарма", re.IGNORECASE),
        re.compile(r"аптек", re.IGNORECASE),
        re.compile(r"таблет", re.IGNORECASE),
        re.compile(r"лекар", re.IGNORECASE),
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
        re.compile(r"коэфф", re.IGNORECASE),
        re.compile(r"экспресс", re.IGNORECASE),
        re.compile(r"ставк", re.IGNORECASE),
        re.compile(r"букмекер", re.IGNORECASE),
        re.compile(r"тотализатор", re.IGNORECASE),
    ],
    "doorway": [
        re.compile(r"\bdoorway\b", re.IGNORECASE),
        re.compile(r"\bdoorway\s+pages?\b", re.IGNORECASE),
        re.compile(r"\bgateway\s+pages?\b", re.IGNORECASE),
        re.compile(r"\bbridge\s+pages?\b", re.IGNORECASE),
        re.compile(r"\bentry\s+pages?\b", re.IGNORECASE),
        re.compile(r"\bjump\s+pages?\b", re.IGNORECASE),
        re.compile(r"\blanding\s+pages?\b", re.IGNORECASE),
        re.compile(r"дорвей", re.IGNORECASE),
        re.compile(r"дорвеи", re.IGNORECASE),
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

_CJK_RE = re.compile(r"[一-鿿]")

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
    re.compile(r"微信", re.IGNORECASE),
    re.compile(r"厂家", re.IGNORECASE),
    re.compile(r"批发", re.IGNORECASE),
    re.compile(r"供应", re.IGNORECASE),
    re.compile(r"联系", re.IGNORECASE),
    re.compile(r"价格", re.IGNORECASE),
    re.compile(r"生产", re.IGNORECASE),
    re.compile(r"购买", re.IGNORECASE),
    re.compile(r"定制", re.IGNORECASE),
    re.compile(r"工厂", re.IGNORECASE),
    re.compile(r"报价", re.IGNORECASE),
    re.compile(r"询价", re.IGNORECASE),
    re.compile(r"起订", re.IGNORECASE),
    re.compile(r"现货", re.IGNORECASE),
    re.compile(r"采购", re.IGNORECASE),
    re.compile(r"样品", re.IGNORECASE),
    re.compile(
        r"\b(wholesale|manufacturer|supplier|factory|oem|odm|moq|rfq|quotation|quote"
        r"|inquiry|price|wechat|whatsapp|alibaba|made\s+in\s+china|export|bulk|catalog"
        r"|sample|contact\s+us)\b",
        re.IGNORECASE,
    ),
]

_LINK_ATTR_RE = re.compile(
    r"(?:href|src|data-href|data-url|data-link)\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_PLAIN_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_DOORWAY_REDIRECT_RE = re.compile(
    r"(http-equiv\s*=\s*[\"']?refresh|window\.location|location\.href|document\.location)",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[^\W_]{4,}", re.IGNORECASE | re.UNICODE)

_STOPWORDS = {
    "this", "that", "with", "from", "your", "about", "have", "more", "free", "page",
    "home", "main", "site", "http", "https", "www", "html", "info", "news",
    "para", "como", "sobre", "mais", "com", "sem", "los", "las", "les", "des", "pour",
    "avec", "plus", "tous", "tout", "che", "come", "con", "per", "piu", "tutti",
    "der", "die", "und", "mit", "ein", "eine",
    "это", "эта", "эти",
    "ваш", "ваша",
    "что", "как", "наш",
    "только",
}

_TRACKING_PARAM_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "yclid", "msclkid", "dclid", "igshid", "cmpid",
    "clickid", "subid", "affid", "affiliate", "ref", "refid", "referrer",
}

_SCRIPT_PATTERNS = {
    "latin": re.compile(r"[A-Za-z]"),
    "cyrillic": re.compile(r"[Ѐ-ӿ]"),
    "cjk": re.compile(r"[一-鿿]"),
    "hangul": re.compile(r"[가-힣]"),
    "hiragana_katakana": re.compile(r"[぀-ヿ]"),
    "arabic": re.compile(r"[؀-ۿ]"),
    "hebrew": re.compile(r"[֐-׿]"),
    "devanagari": re.compile(r"[ऀ-ॿ]"),
    "thai": re.compile(r"[฀-๿]"),
}

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


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

def _normalize_spam_text(value: str) -> str:
    """Lowercase, unescape HTML entities, collapse whitespace."""
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
    token = re.sub(r"[^a-z0-9Ѐ-ӿ一-鿿]+", " ", token)
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


# ---------------------------------------------------------------------------
# Content metrics
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Spam detection
# ---------------------------------------------------------------------------

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
            if "domain" in combined and ("sale" in combined or "buy" in combined or "offer" in combined):
                return True
            if "домен" in combined and ("продаж" in combined or "купить" in combined or "аукцион" in combined):
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
            elif "domain" in text and ("sale" in text or "buy" in text or "offer" in text or "auction" in text):
                hits.append(key)
            elif "домен" in text and ("продаж" in text or "куп" in text or "аукцион" in text):
                hits.append(key)
            continue
        keywords = _URL_SPAM_KEYWORDS.get(key, [])
        if keywords and any(term in text for term in keywords):
            hits.append(key)
    if not hits:
        hits = _detect_spam_topics(text)
    return hits


# ---------------------------------------------------------------------------
# Snapshot fetching and probing
# ---------------------------------------------------------------------------

def _fetch_snapshot_sample(
    ts: str,
    orig: str,
    headers: dict,
    req_kwargs: dict,
    timeout: float,
    max_bytes: int,
    headers_override=None,
) -> str:
    """Fetch a bounded HTML sample for a single Wayback snapshot."""
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
            if content_type and (
                "text" not in content_type
                and "html" not in content_type
                and "xml" not in content_type
            ):
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
    """Return spam topics, n-gram signature, text length, and content metrics."""
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
        ts, orig, headers, req_kwargs, timeout, max_bytes,
        headers_override=headers_override,
    )
    visible_text, _link_text, _combined, _raw_html = _build_spam_haystacks(sample)
    signature, text_len = _build_topic_signature(visible_text, ngram, max_ngrams)
    return signature, text_len


def _is_spam_probe_candidate(status: str) -> bool:
    """Return True for status codes that typically serve HTML content."""
    s = (status or "").strip()
    if not s or s == "-":
        return True
    return s.startswith("2") or s.startswith("3")


def _enrich_spam_flags(rows: list, headers: dict, req_kwargs: dict):
    """Parallel spam probe over snapshot rows; returns per-index results."""
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
                rows[idx][0], rows[idx][1],
                headers, req_kwargs,
                timeout, max_bytes, ngram, max_ngrams,
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

    return (
        hits_by_idx, len(candidates), len(hits_by_idx), eligible_total,
        sig_by_idx, len_by_idx, propagate_labels, metrics_by_idx,
    )


# ---------------------------------------------------------------------------
# Topic shifts, language shifts, cloaking
# ---------------------------------------------------------------------------

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


def _detect_topic_shifts(rows: list, signatures: dict, lengths: dict) -> tuple[dict, int, int]:
    """Detect topic shifts by comparing consecutive snapshot n-gram signatures."""
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
        if not sig or lengths.get(idx, 0) < min_chars:
            continue
        checked += 1
        if last_sig is None:
            last_sig = sig
            continue
        if _jaccard_similarity(sig, last_sig) < threshold:
            shifts[idx] = True
        last_sig = sig

    return shifts, checked, len(shifts)


def _detect_language_shifts(rows: list, scripts: dict, lengths: dict) -> tuple[dict, int, int]:
    """Detect dominant writing-script changes across snapshots."""
    if not rows:
        return {}, 0, 0
    if not bool(current_app.config.get("ARCHIVE_LANG_SHIFT_ENABLED", True)):
        return {}, 0, 0

    min_chars = max(80, int(current_app.config.get("ARCHIVE_LANG_SHIFT_MIN_CHARS", 280)))

    shifts = {}
    checked = 0
    last_script = ""

    for idx, _row in enumerate(rows):
        script = scripts.get(idx, "")
        if not script or lengths.get(idx, 0) < min_chars:
            continue
        checked += 1
        if not last_script:
            last_script = script
            continue
        if script != last_script:
            shifts[idx] = True
        last_script = script

    return shifts, checked, len(shifts)


def _detect_cloaking(
    rows: list,
    signatures: dict,
    lengths: dict,
    headers: dict,
    req_kwargs: dict,
) -> tuple[dict, int, int]:
    """Detect cloaking by comparing normal vs. bot-UA snapshot content."""
    if not rows:
        return {}, 0, 0
    if not bool(current_app.config.get("ARCHIVE_CLOAK_CHECK_ENABLED", True)):
        return {}, 0, 0

    max_probe = max(int(current_app.config.get("ARCHIVE_CLOAK_CHECK_MAX", 40)), 0)
    timeout = max(float(current_app.config.get("ARCHIVE_CLOAK_CHECK_TIMEOUT", 6)), 1.0)
    max_bytes = max(int(current_app.config.get("ARCHIVE_CLOAK_CHECK_MAX_BYTES", 200000)), 20000)
    ngram = int(current_app.config.get("ARCHIVE_TOPIC_NGRAM_SIZE", 4))
    max_ngrams = int(current_app.config.get("ARCHIVE_TOPIC_MAX_NGRAMS", 500))
    threshold = max(0.02, min(float(current_app.config.get("ARCHIVE_CLOAK_CHECK_THRESHOLD", 0.18)), 0.95))
    min_chars = max(80, int(current_app.config.get("ARCHIVE_CLOAK_CHECK_MIN_CHARS", 280)))
    bot_ua = current_app.config.get(
        "ARCHIVE_CLOAK_CHECK_UA",
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    )

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
                rows[idx][0], rows[idx][1],
                headers, req_kwargs,
                timeout, max_bytes, ngram, max_ngrams,
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
            if _jaccard_similarity(sig_norm, sig_bot) < threshold:
                cloak_flags[idx] = True

    return cloak_flags, len(candidates), len(cloak_flags)
