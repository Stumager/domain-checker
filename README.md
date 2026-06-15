# Domain Checker

A local desktop tool for bulk domain availability checking with DNS prefiltering,
RDAP verification, Wayback Machine history analysis, and a persistent Domain DB
for tracking known domains across sessions.

## What it does

### DNS Checker tab

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
- Topic shift and language shift detection across snapshots
- Cloaking detection — compares bot UA vs. normal UA responses
- Reputation checks — Google Safe Browsing, PhishTank, URLhaus host feed
- Domain age (RDAP) and TLS certificate age
- Composite risk score with per-flag breakdown

### Domain DB tab

A persistent local database (stored in `localStorage`) for managing known domains:

- Organize domains into **TLD buckets** (`.com`, `.net`, `.ru`, …)
- Import via **drag & drop** (.txt / .csv) or paste — raw URLs, `www.` prefixes,
  and paths are automatically normalized
- **Search** within a bucket, **paginate** large lists (50 items at a time)
- **Export** any bucket as `.txt`
- Post-scan comparison highlights new domains not yet in any bucket

Results can be downloaded as `.txt` per category or a single `.zip`.
The app opens automatically in your browser and shuts down when the tab is closed.

## Screenshots

![DNS Checker UI](<Снимок экрана 2026-06-13 201041.png>)
![Web Archive UI](<Снимок экрана 2026-06-13 201525.png>)

## Tech stack

| Layer               | Technology                      |
| ------------------- | ------------------------------- |
| Backend             | Python 3.10+, Flask 2.3         |
| DNS resolution      | dnspython 2.4                   |
| HTTP / RDAP / WHOIS | requests, socket                |
| Concurrency         | threading, ThreadPoolExecutor   |
| Archive             | Wayback Machine CDX API         |
| Frontend            | Vanilla JS, CSS (no frameworks) |
| Persistence         | Browser localStorage            |

## Requirements

- Python 3.10 or newer
- Windows / macOS / Linux

## Running locally

```bash
cd backend
pip install -r requirements.txt
python run.py
```

The app opens automatically at `http://127.0.0.1:8080`.

**Windows shortcut:** double-click `backend/run.bat` — it installs dependencies
and starts the server in one step.

## Configuration

Copy `.env.example` to `.env` and adjust as needed:

```bash
cp backend/.env.example backend/.env
```

| Variable                               | Default                  | Description                                     |
| -------------------------------------- | ------------------------ | ----------------------------------------------- |
| `PORT`                                 | `8080`                   | Local server port                               |
| `DEFAULT_TLDS`                         | `es it pl fr de pt nl …` | TLDs used for label expansion                   |
| `FINAL_CHECK_ENABLED`                  | `1`                      | Enable RDAP second-pass check                   |
| `ARCHIVE_REPUTATION_SAFE_BROWSING_KEY` | _(empty)_                | Google Safe Browsing API key (optional)         |
| `ARCHIVE_CLOAK_CHECK_ENABLED`          | `0`                      | Enable cloaking detection (makes live requests) |

See `.env.example` for the full list of options.

## Project structure

```
backend/
├── app/
│   ├── archive/
│   │   ├── fetcher.py        # CDX API, pagination, proxy support
│   │   ├── spam_detector.py  # Content analysis, topic/language shift, cloaking
│   │   └── reputation.py     # Safe Browsing, PhishTank, URLhaus, risk score
│   ├── services/             # DNS and RDAP checking
│   ├── utils/                # Validators, helpers
│   ├── models.py             # Thread-safe scan state
│   ├── check_pipeline.py     # Two-stage checking pipeline
│   └── routes.py             # API endpoints
├── static/
│   ├── css/style.css         # All styles (CSS custom properties + component system)
│   └── js/app.js             # Frontend logic + Domain DB (localStorage)
├── templates/index.html      # Single-page app shell
├── config.py                 # All settings via environment variables
├── run.py                    # Entry point
└── requirements.txt
```

## License

MIT — see [LICENSE](LICENSE).
