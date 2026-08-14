"""Configuration module for the Domain Checker application"""

import os


class Config:
    """Base configuration"""

    # Flask settings.
    # SECRET_KEY has no fallback on purpose: a per-process random key would give
    # every WSGI worker its own key, so sessions would break at random.
    SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
    DEBUG = os.getenv("DEBUG", "False") == "True"
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "8080"))
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "").strip()
    MAX_DOMAINS = int(os.getenv("MAX_DOMAINS", "200000"))

    # Session cookie hardening. SECURE defaults off so plain-HTTP local runs work;
    # turn it on wherever the app is served over HTTPS.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip()

    # RDAP settings
    RDAP_BOOTSTRAP_URL = os.getenv("RDAP_BOOTSTRAP_URL", "https://data.iana.org/rdap/dns.json")
    FINAL_CHECK_ENABLED = os.getenv("FINAL_CHECK_ENABLED", "1") == "1"
    FINAL_CHECK_WORKERS = int(os.getenv("FINAL_CHECK_WORKERS", "20"))
    RDAP_TIMEOUT = float(os.getenv("RDAP_TIMEOUT", "7.0"))
    
    # Per-TLD overrides (JSON): {"com":"https://rdap.verisign.com/com/v1/","es":"https://rdap.nic.es/rdap/"}
    RDAP_TLD_OVERRIDES_JSON = os.getenv("RDAP_TLD_OVERRIDES", "").strip()
    # Per-TLD RDAP concurrency overrides (JSON): {"fr":4,"de":4}
    RDAP_CONCURRENCY_OVERRIDES_JSON = os.getenv("RDAP_CONCURRENCY_OVERRIDES", "").strip()
    
    # RDAP concurrency limits for specific TLDs
    RDAP_CONCURRENCY_DEFAULT = int(os.getenv("RDAP_CONCURRENCY_DEFAULT", "12"))
    RDAP_CONCURRENCY_ES = int(os.getenv("RDAP_CONCURRENCY_ES", "2"))
    RDAP_CONCURRENCY_IT = int(os.getenv("RDAP_CONCURRENCY_IT", "4"))

    # RDAP session pooling and fallback behavior
    RDAP_SESSION_POOL_CONNECTIONS = int(os.getenv("RDAP_SESSION_POOL_CONNECTIONS", "32"))
    RDAP_SESSION_POOL_MAXSIZE = int(os.getenv("RDAP_SESSION_POOL_MAXSIZE", "64"))
    RDAP_FORBIDDEN_FALLBACK = os.getenv("RDAP_FORBIDDEN_FALLBACK", "1") == "1"
    RDAP_PARSE_ERROR_BODY = os.getenv("RDAP_PARSE_ERROR_BODY", "1") == "1"
    RDAP_RESTRICTED_ENABLE = os.getenv("RDAP_RESTRICTED_ENABLE", "1") == "1"
    RDAP_RESTRICTED_TTL = float(os.getenv("RDAP_RESTRICTED_TTL", "600"))

    # WHOIS fallback tuning
    WHOIS_SERVER_OVERRIDES_JSON = os.getenv("WHOIS_SERVER_OVERRIDES", "").strip()
    WHOIS_NOT_FOUND_OVERRIDES_JSON = os.getenv("WHOIS_NOT_FOUND_OVERRIDES", "").strip()
    WHOIS_BOOTSTRAP_ENABLED = os.getenv("WHOIS_BOOTSTRAP_ENABLED", "1") == "1"
    WHOIS_BOOTSTRAP_SERVER = os.getenv("WHOIS_BOOTSTRAP_SERVER", "whois.iana.org").strip()
    
    # RDAP retry behavior
    RDAP_RETRIES = int(os.getenv("RDAP_RETRIES", "2"))
    RDAP_BACKOFF_BASE = float(os.getenv("RDAP_BACKOFF_BASE", "0.6"))
    RDAP_BACKOFF_JITTER = float(os.getenv("RDAP_BACKOFF_JITTER", "0.25"))
    
    # Default TLDs for label expansion
    DEFAULT_TLDS = os.getenv("DEFAULT_TLDS", "es it pl fr de pt nl be se fi no dk tr in ca br mx co").strip()
    # TLDs where DNS prefilter is trusted; others treat unknown as RDAP candidates
    DNS_PREFILTER_STRICT_TLDS = os.getenv("DNS_PREFILTER_STRICT_TLDS", "com in co mx vn").strip()

    # DNS prefilter stage
    DNS_RESOLVERS = os.getenv("DNS_RESOLVERS", "1.1.1.1 8.8.8.8").strip()
    DNS_TIMEOUT = float(os.getenv("DNS_TIMEOUT", "1.6"))
    DNS_RETRIES = int(os.getenv("DNS_RETRIES", "2"))


    # Wayback archive settings
    ARCHIVE_USER_AGENT = os.getenv(
        "ARCHIVE_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    ).strip()
    ARCHIVE_YEAR_FROM = int(os.getenv("ARCHIVE_YEAR_FROM", "1998"))
    ARCHIVE_YEAR_TO = int(os.getenv("ARCHIVE_YEAR_TO", "2026"))
    ARCHIVE_TIMEOUT = float(os.getenv("ARCHIVE_TIMEOUT", "45"))
    ARCHIVE_REQUEST_RETRIES = int(os.getenv("ARCHIVE_REQUEST_RETRIES", "3"))
    ARCHIVE_MAX_SECONDS = float(os.getenv("ARCHIVE_MAX_SECONDS", "60"))
    ARCHIVE_PROXY_TIMEOUT = float(os.getenv("ARCHIVE_PROXY_TIMEOUT", "10"))
    ARCHIVE_PROXY_REQUEST_RETRIES = int(os.getenv("ARCHIVE_PROXY_REQUEST_RETRIES", "1"))
    ARCHIVE_DIRECT_FALLBACK = os.getenv("ARCHIVE_DIRECT_FALLBACK", "1") == "1"
    ARCHIVE_VERIFY_EMPTY_WITH_FALLBACK = os.getenv("ARCHIVE_VERIFY_EMPTY_WITH_FALLBACK", "1") == "1"
    ARCHIVE_CDX_ALLOW_HTTP_FALLBACK = os.getenv("ARCHIVE_CDX_ALLOW_HTTP_FALLBACK", "1") == "1"
    ARCHIVE_CDX_PAGE_SIZE = int(os.getenv("ARCHIVE_CDX_PAGE_SIZE", "2000"))
    ARCHIVE_CDX_MAX_PAGES = int(os.getenv("ARCHIVE_CDX_MAX_PAGES", "400"))
    ARCHIVE_CDX_MAX_ROWS = int(os.getenv("ARCHIVE_CDX_MAX_ROWS", "600000"))
    ARCHIVE_REDIRECT_FETCH_ENABLED = os.getenv("ARCHIVE_REDIRECT_FETCH_ENABLED", "1") == "1"
    ARCHIVE_REDIRECT_FETCH_MAX = int(os.getenv("ARCHIVE_REDIRECT_FETCH_MAX", "180"))
    ARCHIVE_REDIRECT_FETCH_WORKERS = int(os.getenv("ARCHIVE_REDIRECT_FETCH_WORKERS", "8"))
    ARCHIVE_REDIRECT_FETCH_TIMEOUT = float(os.getenv("ARCHIVE_REDIRECT_FETCH_TIMEOUT", "6"))
    ARCHIVE_REDIRECT_FALLBACK_ON_MISSING_COLUMNS = os.getenv("ARCHIVE_REDIRECT_FALLBACK_ON_MISSING_COLUMNS", "1") == "1"
    ARCHIVE_SPAM_CHECK_ENABLED = os.getenv("ARCHIVE_SPAM_CHECK_ENABLED", "1") == "1"
    ARCHIVE_SPAM_CHECK_MAX = int(os.getenv("ARCHIVE_SPAM_CHECK_MAX", "120"))
    ARCHIVE_SPAM_CHECK_WORKERS = int(os.getenv("ARCHIVE_SPAM_CHECK_WORKERS", "6"))
    ARCHIVE_SPAM_CHECK_TIMEOUT = float(os.getenv("ARCHIVE_SPAM_CHECK_TIMEOUT", "6"))
    ARCHIVE_SPAM_CHECK_MAX_BYTES = int(os.getenv("ARCHIVE_SPAM_CHECK_MAX_BYTES", "250000"))
    ARCHIVE_SPAM_PROPAGATE_THRESHOLD = float(os.getenv("ARCHIVE_SPAM_PROPAGATE_THRESHOLD", "0.7"))
    ARCHIVE_TOPIC_CHANGE_ENABLED = os.getenv("ARCHIVE_TOPIC_CHANGE_ENABLED", "1") == "1"
    ARCHIVE_TOPIC_CHANGE_THRESHOLD = float(os.getenv("ARCHIVE_TOPIC_CHANGE_THRESHOLD", "0.18"))
    ARCHIVE_TOPIC_CHANGE_MIN_CHARS = int(os.getenv("ARCHIVE_TOPIC_CHANGE_MIN_CHARS", "320"))
    ARCHIVE_TOPIC_NGRAM_SIZE = int(os.getenv("ARCHIVE_TOPIC_NGRAM_SIZE", "4"))
    ARCHIVE_TOPIC_MAX_NGRAMS = int(os.getenv("ARCHIVE_TOPIC_MAX_NGRAMS", "500"))
    ARCHIVE_LANG_SHIFT_ENABLED = os.getenv("ARCHIVE_LANG_SHIFT_ENABLED", "1") == "1"
    ARCHIVE_LANG_SHIFT_MIN_CHARS = int(os.getenv("ARCHIVE_LANG_SHIFT_MIN_CHARS", "280"))
    ARCHIVE_CLOAK_CHECK_ENABLED = os.getenv("ARCHIVE_CLOAK_CHECK_ENABLED", "0") == "1"
    ARCHIVE_CLOAK_CHECK_MAX = int(os.getenv("ARCHIVE_CLOAK_CHECK_MAX", "40"))
    ARCHIVE_CLOAK_CHECK_TIMEOUT = float(os.getenv("ARCHIVE_CLOAK_CHECK_TIMEOUT", "6"))
    ARCHIVE_CLOAK_CHECK_MAX_BYTES = int(os.getenv("ARCHIVE_CLOAK_CHECK_MAX_BYTES", "200000"))
    ARCHIVE_CLOAK_CHECK_THRESHOLD = float(os.getenv("ARCHIVE_CLOAK_CHECK_THRESHOLD", "0.18"))
    ARCHIVE_CLOAK_CHECK_MIN_CHARS = int(os.getenv("ARCHIVE_CLOAK_CHECK_MIN_CHARS", "280"))
    ARCHIVE_CLOAK_CHECK_UA = os.getenv(
        "ARCHIVE_CLOAK_CHECK_UA",
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    )
    ARCHIVE_REPUTATION_CHECK_ENABLED = os.getenv("ARCHIVE_REPUTATION_CHECK_ENABLED", "1") == "1"
    ARCHIVE_REPUTATION_TIMEOUT = float(os.getenv("ARCHIVE_REPUTATION_TIMEOUT", "6"))
    ARCHIVE_REPUTATION_SAFE_BROWSING_KEY = os.getenv("ARCHIVE_REPUTATION_SAFE_BROWSING_KEY", "").strip()
    ARCHIVE_REPUTATION_SAFE_BROWSING_CLIENT_ID = os.getenv("ARCHIVE_REPUTATION_SAFE_BROWSING_CLIENT_ID", "checker")
    ARCHIVE_REPUTATION_SAFE_BROWSING_CLIENT_VERSION = os.getenv("ARCHIVE_REPUTATION_SAFE_BROWSING_CLIENT_VERSION", "1.0")
    ARCHIVE_REPUTATION_PHISHTANK_KEY = os.getenv("ARCHIVE_REPUTATION_PHISHTANK_KEY", "").strip()
    ARCHIVE_REPUTATION_BLOCKLIST_PATHS = os.getenv("ARCHIVE_REPUTATION_BLOCKLIST_PATHS", "").strip()
    ARCHIVE_REPUTATION_URLHAUS_HOSTFILE_URL = os.getenv("ARCHIVE_REPUTATION_URLHAUS_HOSTFILE_URL", "").strip()
    ARCHIVE_REPUTATION_URLHAUS_TTL = float(os.getenv("ARCHIVE_REPUTATION_URLHAUS_TTL", "3600"))
    ARCHIVE_RDAP_CHECK_ENABLED = os.getenv("ARCHIVE_RDAP_CHECK_ENABLED", "1") == "1"
    ARCHIVE_RDAP_TIMEOUT = float(os.getenv("ARCHIVE_RDAP_TIMEOUT", "6"))
    ARCHIVE_RDAP_ENDPOINT = os.getenv("ARCHIVE_RDAP_ENDPOINT", "https://rdap.org/domain/").strip()
    ARCHIVE_TLS_CHECK_ENABLED = os.getenv("ARCHIVE_TLS_CHECK_ENABLED", "1") == "1"
    ARCHIVE_TLS_TIMEOUT = float(os.getenv("ARCHIVE_TLS_TIMEOUT", "4"))
    ARCHIVE_NOT_SUITABLE_SCORE = int(os.getenv("ARCHIVE_NOT_SUITABLE_SCORE", "50"))
    ARCHIVE_TOPIC_CHANGE_ONLY_IF_SPAM = os.getenv("ARCHIVE_TOPIC_CHANGE_ONLY_IF_SPAM", "1") == "1"
    ARCHIVE_CJK_DENSITY_THRESHOLD = float(os.getenv("ARCHIVE_CJK_DENSITY_THRESHOLD", "0.25"))
    ARCHIVE_CHINESE_DENSITY_THRESHOLD = float(os.getenv("ARCHIVE_CHINESE_DENSITY_THRESHOLD", "0.15"))
    ARCHIVE_CJK_MIN_CHARS = int(os.getenv("ARCHIVE_CJK_MIN_CHARS", "10"))

    # Maps Scraper (gosom/google-maps-scraper)
    GMAPS_API_URL = os.getenv("GMAPS_API_URL", "http://localhost:8090")
    GMAPS_TIMEOUT = float(os.getenv("GMAPS_TIMEOUT", "20"))
    GMAPS_DOWNLOAD_TIMEOUT = float(os.getenv("GMAPS_DOWNLOAD_TIMEOUT", "120"))
    # Как часто фоновый поток опрашивает статус задачи в gmaps
    GMAPS_POLL_INTERVAL = float(os.getenv("GMAPS_POLL_INTERVAL", "30"))
    # max_time обязателен для gmaps API (в секундах)
    GMAPS_MAX_TIME = int(os.getenv("GMAPS_MAX_TIME", "600"))
    GMAPS_DEFAULT_DEPTH = int(os.getenv("GMAPS_DEFAULT_DEPTH", "10"))
    GMAPS_DEFAULT_ZOOM = int(os.getenv("GMAPS_DEFAULT_ZOOM", "15"))
    GMAPS_DEFAULT_CONCURRENCY = int(os.getenv("GMAPS_DEFAULT_CONCURRENCY", "4"))
    GMAPS_DEFAULT_GRID_CELL = float(os.getenv("GMAPS_DEFAULT_GRID_CELL", "1.0"))

    # Nominatim (bbox по городу)
    NOMINATIM_URL = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search").strip()
    NOMINATIM_USER_AGENT = os.getenv("NOMINATIM_USER_AGENT", "DomainChecker/1.0").strip()
    NOMINATIM_TIMEOUT = float(os.getenv("NOMINATIM_TIMEOUT", "15"))

    # Проверка прокси
    PROXY_CHECK_URL = os.getenv("PROXY_CHECK_URL", "https://www.google.com").strip()
    PROXY_CHECK_TIMEOUT = float(os.getenv("PROXY_CHECK_TIMEOUT", "10"))
    PROXY_CHECK_WORKERS = int(os.getenv("PROXY_CHECK_WORKERS", "8"))

    # SQLite для модуля Maps и учётных записей. Пусто -> backend/data/maps.db
    MAPS_DB_PATH = os.getenv("MAPS_DB_PATH", "").strip()

    # Учётка, создаваемая при первом запуске.
    # Без дефолта намеренно: seed_admin() падает, если пароль не задан.
    SEED_ADMIN_EMAIL = os.getenv("SEED_ADMIN_EMAIL", "admin@checker.local").strip()
    SEED_ADMIN_PASSWORD = os.getenv("SEED_ADMIN_PASSWORD", "")

    # Groq LLM semantic classifier for Wayback snapshots
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
    GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
    GROQ_TIMEOUT = float(os.getenv("GROQ_TIMEOUT", "8"))
    # Max snapshots Groq analyzes per domain, evenly spread across the full time range.
    # At 25 RPM free tier: 60 snapshots ≈ 2.5 min; 100 ≈ 4 min.
    GROQ_SNAPSHOT_MAX = int(os.getenv("GROQ_SNAPSHOT_MAX", "60"))


class DevelopmentConfig(Config):
    """Development configuration"""


class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False


def get_config(env=None):
    """Get configuration based on environment"""
    if env is None:
        env = os.getenv("FLASK_ENV", "development")
    
    if env == "production":
        return ProductionConfig
    return DevelopmentConfig
