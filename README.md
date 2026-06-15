# Domain Checker

A local desktop tool for bulk domain availability checking with DNS prefiltering,
RDAP verification, Wayback Machine history analysis, spam detection, and a persistent
Domain DB for tracking known domains across sessions.

## What it does

### Domain Checker tab

Paste a list of domains or bare labels (e.g. `example` → `example.es`, `example.it`, …)
and the tool runs a two-stage pipeline:

1. **DNS prefilter** — parallel NS/SOA lookup via dnspython classifies every domain
   as `available`, `taken`, or `unknown`
2. **RDAP final check** — refines candidates through the RDAP API with automatic
   WHOIS fallback for TLDs that have no RDAP endpoint

After a scan, available domains are automatically compared against your Domain DB —
the results panel shows which ones are **new** (not in any bucket) and which are
**already known**, with one-click options to copy or add them to a bucket.

### Web Archive modal

Fetches full Wayback Machine history for any domain on demand:

- Spam content detection — casino, pharma, adult, doorway, and parked-page patterns
  (≥2 unique pattern matches required to assign a label)
- Topic shift detection across snapshots using n-gram Jaccard similarity
- Language shift detection across snapshots
- Cloaking detection — compares bot UA vs. normal UA responses (disabled by default)
- Reputation checks — Google Safe Browsing, PhishTank, URLhaus host feed
- Domain age (RDAP) and TLS certificate age

### Domain DB tab

A persistent local database (stored in `localStorage`) for managing known domains:

- Organize domains into **TLD buckets** (`.com`, `.net`, `.ru`, …)
- Buckets are created automatically when adding domains from scan results
- Import via **drag & drop** (.txt / .csv) or paste — raw URLs, `www.` prefixes,
  and paths are automatically normalized
- **Search** within a bucket, **paginate** large lists (50 items at a time)
- **Export** any bucket as `.txt`
- Post-scan comparison highlights new domains not yet in any bucket

Results can be downloaded as `.txt` per category or a single `.zip`.
The app opens automatically in your browser and shuts down when the tab is closed.

## Screenshots

Run the app and capture screenshots, then place them in `docs/screenshots/`.

![Main checker](%D0%A1%D0%BD%D0%B8%D0%BC%D0%BE%D0%BA%20%D1%8D%D0%BA%D1%80%D0%B0%D0%BD%D0%B0%202026-06-15%20164557.png)
![Web Archive](%D0%A1%D0%BD%D0%B8%D0%BC%D0%BE%D0%BA%20%D1%8D%D0%BA%D1%80%D0%B0%D0%BD%D0%B0%202026-06-15%20164632.png)
![Domain DB](%D0%A1%D0%BD%D0%B8%D0%BC%D0%BE%D0%BA%20%D1%8D%D0%BA%D1%80%D0%B0%D0%BD%D0%B0%202026-06-15%20165042.png)

## Tech stack

| Layer               | Technology                      |
| ------------------- | ------------------------------- |
| Backend             | Python 3.10+, Flask 2.3.3       |
| DNS resolution      | dnspython 2.4.2                 |
| HTTP / RDAP / WHOIS | requests 2.31.0, socket         |
| Concurrency         | threading, ThreadPoolExecutor   |
| Archive             | Wayback Machine CDX API         |
| Frontend            | Vanilla JS, CSS (no frameworks) |
| Persistence         | Browser localStorage            |

## Requirements

- Python 3.10 or newer
- Windows / macOS / Linux

## Setup

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env   # then edit .env as needed
```

## Run

```bash
cd backend
python run.py
```

The app opens automatically at `http://127.0.0.1:8080` and exits when the browser
tab is closed.

**Windows shortcut:** double-click `backend/run.bat` — it installs dependencies
and starts the server in one step.

## Project structure

```
backend/
├── app/
│   ├── archive/
│   │   ├── fetcher.py          # CDX API, pagination, redirect probing, proxy support
│   │   ├── spam_detector.py    # Content spam analysis, topic/language shift, cloaking
│   │   └── reputation.py       # Safe Browsing, PhishTank, URLhaus, risk scoring
│   ├── services/
│   │   ├── dns_checker.py      # DNS prefilter — NS/SOA via dnspython (1.1.1.1, 8.8.8.8)
│   │   ├── rdap_service.py     # RDAP final check with per-TLD concurrency and WHOIS fallback
│   │   └── domain_processor.py # Label → domain expansion and deduplication
│   ├── utils/
│   │   ├── validators.py       # normalize_domain, to_ascii, is_valid_domain
│   │   └── helpers.py          # dedupe, parse_tlds, filter_domains_by_tlds
│   ├── models.py               # Thread-safe scan state
│   ├── check_pipeline.py       # Two-stage DNS + RDAP checking pipeline
│   ├── browser_monitor.py      # Browser heartbeat monitor, auto-shutdown
│   └── routes.py               # All API endpoints
├── static/
│   ├── css/style.css           # All styles (CSS custom properties + component system)
│   └── js/app.js               # Frontend logic + Domain DB (localStorage)
├── templates/
│   └── index.html              # Single-page app shell
├── config.py                   # All settings via environment variables
├── run.py                      # Entry point
├── run.bat                     # Windows one-click launcher
└── requirements.txt
```

## API reference

### `GET /`
Serves the single-page app (`index.html`).

---

### `GET /api/status`
Returns current scan state.

**Response:**
```json
{
  "status": "idle | running | done | error",
  "total": 0,
  "processed": 0,
  "available": 0,
  "taken": 0,
  "invalid": 0,
  "errors": 0,
  "current_domain": ""
}
```

---

### `POST /api/check`
Start a domain availability scan.

**Request body:**
```json
{
  "domains": "example\ntest.com",
  "threads": 32,
  "tlds": "es it pl",
  "rdap_recheck_errors": false
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `domains` | string | required | Newline-separated domains or bare labels |
| `threads` | int | `32` | DNS prefilter thread count (1–128) |
| `tlds` | string | _(server default)_ | TLDs for label expansion; uses `DEFAULT_TLDS` if empty |
| `rdap_recheck_errors` | bool | `false` | Run RDAP on DNS-error domains too |

**Response (200):**
```json
{
  "status": "started",
  "expanded_total": 480,
  "filtered_total": 480,
  "tlds": ["es", "it"],
  "final_check_enabled": true,
  "final_check_workers": 12,
  "rdap_recheck_errors": false,
  "rdap_bootstrap_url": "https://data.iana.org/rdap/dns.json"
}
```

**Error (409):** scan already in progress.

---

### `POST /api/stop`
Request the active scan to stop.

**Response (200):** `{"status": "stopping"}`  
**Error (409):** no active scan.

---

### `GET /api/download/<result_type>`
Download one result category as a `.txt` file.

`result_type` must be one of: `available`, `taken`, `invalid`, `errors`.

---

### `GET /api/download-all`
Download all four result categories as `checker-results.zip`.

---

### `POST /api/archive`
Fetch and analyze Wayback Machine history for a domain.

**Request body:**
```json
{
  "domain": "example.com",
  "proxy": "http://user:pass@ip:port"
}
```

| Field | Type | Description |
|---|---|---|
| `domain` | string | Domain to look up |
| `proxy` | string | Optional proxy (`ip:port` or `http://user:pass@ip:port`) |

**Response (200):**
```json
{
  "results": [
    {
      "date": "2018-03-14 12:00:00",
      "status": "200",
      "link": "https://web.archive.org/web/20180314120000/http://example.com/",
      "redirect": "",
      "spam": ["casino"],
      "topic_shift": false,
      "language_shift": false,
      "cloaking": false
    }
  ],
  "total_results": 1,
  "range": {"from": 1998, "to": 2026},
  "proxy": {"enabled": false, "mode": "direct", "current": "Direct connection"},
  "used_connection": "direct",
  "cdx_endpoint": "https://web.archive.org/cdx/search/cdx",
  "redirects_resolved": 0,
  "redirects_probed": 0,
  "redirects_direct_fallback": 0,
  "spam_checked": 45,
  "spam_flagged": 3,
  "spam_total": 3,
  "topic_checked": 45,
  "topic_shifted": 1,
  "language_checked": 45,
  "language_shifted": 0,
  "cloaking_checked": 0,
  "cloaking_detected": 0,
  "reputation": {},
  "risk": {},
  "fetch_error": "",
  "truncated": false
}
```

---

### `GET /api/ping` · `POST /api/ping`
Browser heartbeat. Called by the frontend to keep the process alive.
Accepts optional `session` query parameter or `X-Browser-Session` header.

---

### `POST /api/browser-disconnect`
Signal that the browser tab was closed. Triggers the shutdown timer.

---

## Configuration

Copy `.env.example` to `backend/.env` and adjust as needed.
All variables are optional — the defaults work out of the box.

### Server

| Variable | Default | Description |
|---|---|---|
| `HOST` | `127.0.0.1` | Bind address |
| `PORT` | `8080` | HTTP port |
| `DEBUG` | `False` | Enable Flask debug mode |
| `SECRET_KEY` | _(random)_ | Flask secret key |
| `CORS_ORIGINS` | _(empty)_ | Comma-separated allowed CORS origins; empty = disabled |
| `MAX_DOMAINS` | `200000` | Max domains accepted per scan request |
| `AUTO_OPEN_BROWSER` | `1` | Open browser automatically on startup |

### Browser monitor

| Variable | Default | Description |
|---|---|---|
| `BROWSER_MONITOR_ENABLED` | `1` | Exit process when browser tab closes |
| `BROWSER_MONITOR_TIMEOUT` | `60` | Seconds without a ping before exit |
| `BROWSER_MONITOR_STARTUP_GRACE` | `30` | Seconds after startup before monitoring begins |
| `BROWSER_MONITOR_SHUTDOWN_DELAY` | `3` | Seconds to wait before process exit |

### Scan / RDAP

| Variable | Default | Description |
|---|---|---|
| `DEFAULT_TLDS` | `es it pl fr de pt nl be se fi no dk tr in ca br mx co` | TLDs appended to bare labels |
| `DNS_PREFILTER_STRICT_TLDS` | `com in co mx` | TLDs where DNS result is trusted without RDAP |
| `FINAL_CHECK_ENABLED` | `1` | Enable RDAP second-pass check |
| `FINAL_CHECK_WORKERS` | `12` | RDAP parallel workers |
| `RDAP_BOOTSTRAP_URL` | `https://data.iana.org/rdap/dns.json` | IANA RDAP endpoint registry |
| `RDAP_TIMEOUT` | `4.0` | Per-request RDAP timeout (seconds) |
| `RDAP_RETRIES` | `2` | RDAP retry count on transient failure |
| `RDAP_BACKOFF_BASE` | `0.6` | Retry back-off base (seconds) |
| `RDAP_BACKOFF_JITTER` | `0.25` | Retry back-off jitter (seconds) |
| `RDAP_CONCURRENCY_DEFAULT` | `12` | Default RDAP concurrency |
| `RDAP_CONCURRENCY_ES` | `2` | RDAP concurrency cap for `.es` |
| `RDAP_CONCURRENCY_IT` | `4` | RDAP concurrency cap for `.it` |
| `RDAP_TLD_OVERRIDES` | _(empty)_ | JSON map of TLD → RDAP endpoint |
| `RDAP_CONCURRENCY_OVERRIDES` | _(empty)_ | JSON map of TLD → concurrency limit |
| `RDAP_SESSION_POOL_CONNECTIONS` | `32` | HTTP connection pool size |
| `RDAP_SESSION_POOL_MAXSIZE` | `64` | HTTP connection pool max size |
| `RDAP_FORBIDDEN_FALLBACK` | `1` | Fall back to WHOIS on HTTP 403 |
| `RDAP_PARSE_ERROR_BODY` | `1` | Try to parse RDAP error response bodies |
| `RDAP_RESTRICTED_ENABLE` | `1` | Track restricted/rate-limited TLDs |
| `RDAP_RESTRICTED_TTL` | `3600` | Restricted TLD cache lifetime (seconds) |
| `WHOIS_SERVER_OVERRIDES` | _(empty)_ | JSON map of TLD → WHOIS server |
| `WHOIS_NOT_FOUND_OVERRIDES` | _(empty)_ | JSON map of TLD → "not found" response text |
| `WHOIS_BOOTSTRAP_ENABLED` | `1` | Use IANA WHOIS bootstrap |
| `WHOIS_BOOTSTRAP_SERVER` | `whois.iana.org` | IANA bootstrap WHOIS server |

### Archive / Wayback

| Variable | Default | Description |
|---|---|---|
| `ARCHIVE_YEAR_FROM` | `1998` | Earliest snapshot year to fetch |
| `ARCHIVE_YEAR_TO` | `2026` | Latest snapshot year to fetch |
| `ARCHIVE_TIMEOUT` | `45` | CDX request timeout (seconds) |
| `ARCHIVE_REQUEST_RETRIES` | `3` | CDX retry count |
| `ARCHIVE_MAX_SECONDS` | `60` | Hard time budget for the full CDX fetch |
| `ARCHIVE_PROXY_TIMEOUT` | `10` | Timeout when fetching via proxy (seconds) |
| `ARCHIVE_PROXY_REQUEST_RETRIES` | `1` | Retry count for proxied CDX requests |
| `ARCHIVE_DIRECT_FALLBACK` | `1` | Fall back to direct connection when proxy yields no rows |
| `ARCHIVE_VERIFY_EMPTY_WITH_FALLBACK` | `1` | Re-verify empty proxy result via direct connection |
| `ARCHIVE_CDX_ALLOW_HTTP_FALLBACK` | `1` | Try HTTP CDX endpoint if HTTPS fails |
| `ARCHIVE_CDX_PAGE_SIZE` | `2000` | Rows per CDX page |
| `ARCHIVE_CDX_MAX_PAGES` | `400` | Maximum CDX pages to fetch |
| `ARCHIVE_CDX_MAX_ROWS` | `600000` | Absolute row cap across all pages |
| `ARCHIVE_REDIRECT_FETCH_ENABLED` | `1` | Probe 301/302 redirect destinations |
| `ARCHIVE_REDIRECT_FETCH_MAX` | `180` | Max redirects to probe |
| `ARCHIVE_REDIRECT_FETCH_WORKERS` | `8` | Parallel redirect probe workers |
| `ARCHIVE_REDIRECT_FETCH_TIMEOUT` | `6` | Per-redirect probe timeout (seconds) |
| `ARCHIVE_REDIRECT_FALLBACK_ON_MISSING_COLUMNS` | `1` | Retry via direct if proxy response lacks redirect columns |

### Spam detection

| Variable | Default | Description |
|---|---|---|
| `ARCHIVE_SPAM_CHECK_ENABLED` | `1` | Enable spam content analysis |
| `ARCHIVE_SPAM_CHECK_MAX` | `120` | Max snapshots to inspect per domain |
| `ARCHIVE_SPAM_CHECK_WORKERS` | `6` | Parallel spam-check workers |
| `ARCHIVE_SPAM_CHECK_TIMEOUT` | `6` | Per-snapshot fetch timeout (seconds) |
| `ARCHIVE_SPAM_CHECK_MAX_BYTES` | `250000` | Max response body size to analyze |
| `ARCHIVE_SPAM_PROPAGATE_THRESHOLD` | `0.7` | Fraction of checked snapshots that must share a label to propagate it to all |
| `ARCHIVE_TOPIC_CHANGE_ENABLED` | `1` | Enable topic shift detection |
| `ARCHIVE_TOPIC_CHANGE_THRESHOLD` | `0.18` | Jaccard dissimilarity threshold for a topic shift |
| `ARCHIVE_TOPIC_CHANGE_MIN_CHARS` | `320` | Min snapshot text length to include in topic analysis |
| `ARCHIVE_TOPIC_NGRAM_SIZE` | `4` | N-gram size for topic fingerprinting |
| `ARCHIVE_TOPIC_MAX_NGRAMS` | `500` | Max n-grams retained per snapshot signature |
| `ARCHIVE_LANG_SHIFT_ENABLED` | `1` | Enable language shift detection |
| `ARCHIVE_LANG_SHIFT_MIN_CHARS` | `280` | Min snapshot text length for language detection |
| `ARCHIVE_CLOAK_CHECK_ENABLED` | `0` | Enable cloaking detection (makes live HTTP requests) |
| `ARCHIVE_CLOAK_CHECK_MAX` | `40` | Max snapshots to probe for cloaking |
| `ARCHIVE_CLOAK_CHECK_TIMEOUT` | `6` | Per-snapshot cloaking probe timeout (seconds) |
| `ARCHIVE_CLOAK_CHECK_MAX_BYTES` | `200000` | Max response size for cloaking probe |
| `ARCHIVE_CLOAK_CHECK_THRESHOLD` | `0.18` | Jaccard dissimilarity threshold for cloaking detection |
| `ARCHIVE_CLOAK_CHECK_MIN_CHARS` | `280` | Min chars for a snapshot to be considered in cloaking check |
| `ARCHIVE_CLOAK_CHECK_UA` | Googlebot UA | User-agent string for the bot-simulation probe |
| `ARCHIVE_REPUTATION_CHECK_ENABLED` | `1` | Enable reputation checks (Safe Browsing, PhishTank, URLhaus) |
| `ARCHIVE_REPUTATION_TIMEOUT` | `6` | Reputation API request timeout (seconds) |
| `ARCHIVE_REPUTATION_SAFE_BROWSING_KEY` | _(empty)_ | Google Safe Browsing API key |
| `ARCHIVE_REPUTATION_SAFE_BROWSING_CLIENT_ID` | `checker` | Safe Browsing client ID |
| `ARCHIVE_REPUTATION_SAFE_BROWSING_CLIENT_VERSION` | `1.0` | Safe Browsing client version |
| `ARCHIVE_REPUTATION_PHISHTANK_KEY` | _(empty)_ | PhishTank API key (optional) |
| `ARCHIVE_REPUTATION_BLOCKLIST_PATHS` | _(empty)_ | Colon-separated paths to local plaintext blocklist files |
| `ARCHIVE_REPUTATION_URLHAUS_HOSTFILE_URL` | _(empty)_ | URLhaus host-feed URL |
| `ARCHIVE_REPUTATION_URLHAUS_TTL` | `3600` | URLhaus feed cache lifetime (seconds) |
| `ARCHIVE_RDAP_CHECK_ENABLED` | `1` | Look up domain age via RDAP in the archive view |
| `ARCHIVE_RDAP_TIMEOUT` | `6` | RDAP timeout for archive age lookup (seconds) |
| `ARCHIVE_RDAP_ENDPOINT` | `https://rdap.org/domain/` | RDAP endpoint used for archive age lookup |
| `ARCHIVE_TLS_CHECK_ENABLED` | `1` | Probe TLS certificate to determine cert age |
| `ARCHIVE_TLS_TIMEOUT` | `4` | TLS probe timeout (seconds) |
| `ARCHIVE_NOT_SUITABLE_SCORE` | `50` | Risk score threshold for a "not suitable" verdict |

## License

MIT — see [LICENSE](LICENSE).
