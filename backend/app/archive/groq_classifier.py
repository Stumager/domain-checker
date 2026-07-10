"""Groq LLM-based semantic classifier for Wayback snapshot content."""

import json
import threading
import time
import requests

_config_lock = threading.Lock()
_api_key: str = ""
_model: str = "llama-3.1-8b-instant"
_timeout: float = 8.0

# Sliding-window rate limiter — stays under Groq free-tier 30 RPM limit.
# Workers block here before each API call so we never get 429s.
_rate_lock = threading.Lock()
_call_times: list = []   # timestamps of calls in the last 60 s
_MAX_RPM: int = 25       # leave a small buffer below the 30 RPM cap

_BAD_TOPICS = frozenset({"porn", "casino", "pharma", "betting", "chinese", "doorway", "parked"})
_VALID_TOPICS = _BAD_TOPICS | {"legit", "unknown"}

_SYSTEM_PROMPT = (
    "You are a web content classifier for archived webpages. "
    "Classify the page into exactly ONE of these categories:\n\n"
    "- porn: adult/sexual content, nudity, erotic material, cam sites, OnlyFans-style pages\n"
    "- casino: online casino, slot machines, gambling games, free spins, no-deposit bonuses\n"
    "- pharma: pharmaceutical spam — buy pills online, no prescription required, cheap medications, "
    "Viagra/Cialis/Tramadol/Xanax offers\n"
    "- betting: sports betting, bookmakers, odds, wagering, free bets (1xbet, bet365, Melbet, etc.)\n"
    "- chinese: Chinese-language wholesale/manufacturer/supplier commercial spam (批发/供应/厂家 pattern)\n"
    "- doorway: thin keyword-stuffed SEO page designed purely to rank in search engines, "
    "often has many links and almost no real content\n"
    "- parked: parked or for-sale domain (Sedo, Afternic, GoDaddy Parking, HugeDomains, etc.)\n"
    "- legit: legitimate website with real original content\n"
    "- unknown: page is empty, too short to classify, or content is unreadable\n\n"
    "Key rules:\n"
    "  1. If ANY spam signal is present (even one relevant keyword in context), classify as the matching bad topic.\n"
    "  2. A page with many outbound links, thin text, and repeated keywords is likely 'doorway'.\n"
    "  3. A page with a 'buy this domain' or parking-service fingerprint is 'parked'.\n"
    "  4. Only use 'legit' if the page clearly has real informational, commercial, or editorial content "
    "with no spam signals.\n"
    "  5. Use 'unknown' only when the content is completely unreadable or less than a few words.\n\n"
    'Respond ONLY with a single JSON object on one line:\n'
    '{"topic": "CATEGORY", "is_bad": true/false, "reason": "one short sentence"}\n\n'
    "is_bad must be true for: porn, casino, pharma, betting, chinese, doorway, parked.\n"
    "is_bad must be false for: legit, unknown."
)


def set_config(api_key, model="llama-3.1-8b-instant", timeout=8.0):
    global _api_key, _model, _timeout
    with _config_lock:
        _api_key = (api_key or "").strip()
        _model = (model or "llama-3.1-8b-instant").strip()
        _timeout = max(3.0, float(timeout))


def is_enabled():
    with _config_lock:
        return bool(_api_key)


def _acquire_rate_slot() -> None:
    """Block the calling thread until a rate-limit slot is available."""
    while True:
        with _rate_lock:
            now = time.time()
            cutoff = now - 60.0
            # Drop timestamps outside the current 60-second window
            while _call_times and _call_times[0] < cutoff:
                _call_times.pop(0)
            if len(_call_times) < _MAX_RPM:
                _call_times.append(now)
                return
            # Window is full — find out when the oldest slot frees up
            next_free = _call_times[0] + 60.0
        time.sleep(max(0.05, next_free - time.time()))


def classify_snapshot(visible_text):
    with _config_lock:
        api_key = _api_key
        model = _model
        timeout = _timeout

    if not api_key:
        return {"topic": "unknown", "is_bad": False, "reason": "groq_disabled"}
    text = (visible_text or "").strip()
    if len(text) < 40:
        return {"topic": "unknown", "is_bad": False, "reason": "insufficient content"}
    text = text[:3000]

    _acquire_rate_slot()   # wait here if we're at the rate limit

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.0,
                "max_tokens": 120,
                "response_format": {"type": "json_object"},
            },
            timeout=timeout,
        )
    except requests.Timeout:
        return {"topic": "unknown", "is_bad": False, "reason": "groq_timeout"}
    except Exception:
        return {"topic": "unknown", "is_bad": False, "reason": "groq_error"}

    if resp.status_code == 429:
        # Shouldn't happen with the rate limiter, but handle gracefully anyway
        return {"topic": "unknown", "is_bad": False, "reason": "groq_rate_limited"}
    if resp.status_code != 200:
        return {"topic": "unknown", "is_bad": False, "reason": f"groq_http_{resp.status_code}"}
    try:
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        topic = parsed.get("topic", "unknown")
        if topic not in _VALID_TOPICS:
            topic = "unknown"
        is_bad = bool(parsed.get("is_bad", topic in _BAD_TOPICS))
        reason = str(parsed.get("reason", ""))[:300]
        return {"topic": topic, "is_bad": is_bad, "reason": reason}
    except Exception:
        return {"topic": "unknown", "is_bad": False, "reason": "groq_parse_error"}
