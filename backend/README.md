# Backend

Flask API server for the local DNS Checker UI.

## Setup

```bash
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
```

Optional configuration:

```bash
copy .env.example .env
```

## Run

```bash
python run.py
```

Or:

```bat
run.bat
```

Server default: `http://127.0.0.1:8080`

## Working app structure

```text
app/
  __init__.py
  browser_monitor.py
  models.py
  routes.py
  services/
  utils/
templates/
static/
config.py
run.py
```

## API

### `GET /api/status`

Returns current scan state.

### `POST /api/check`

Starts a new scan.

Request body fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `domains` | string | required | Newline-separated domain list |
| `threads` | int | 32 | DNS thread count |
| `rdap_recheck_errors` | bool | false | Re-check RDAP error results |
| `dns_enabled` | bool | true | Run DNS prefilter stage (NS/SOA) |
| `rdap_enabled` | bool | true | Run RDAP final check stage |

If a scan is already running, returns `409`.

### `POST /api/stop`

Requests graceful cancellation of the active scan.

### `GET /api/download/<type>`

Downloads one bucket as `.txt`.

Types:

- `available`
- `taken`
- `invalid`
- `errors`

### `GET /api/download-all`

Downloads all result buckets as `checker-results.zip`.

### `POST /api/archive`

Runs Wayback Machine lookup for one domain.

Example body:

```json
{
  "domain": "example.com",
  "proxy": "http://user:pass@host:port"
}
```

### `GET|POST /api/ping`

Browser heartbeat endpoint used by the local UI.

### `POST /api/browser-disconnect`

Browser disconnect endpoint used by the local UI.

## Important configuration

- `HOST`
- `PORT`
- `DEBUG`
- `CORS_ORIGINS`
- `MAX_DOMAINS`
- `FINAL_CHECK_ENABLED`
- `FINAL_CHECK_WORKERS`
- `RDAP_TIMEOUT`
- `RDAP_RETRIES`
- `DEFAULT_TLDS`
- `DNS_PREFILTER_STRICT_TLDS`
- `BROWSER_MONITOR_ENABLED`
- `BROWSER_MONITOR_TIMEOUT`
- `BROWSER_MONITOR_STARTUP_GRACE`
- `BROWSER_MONITOR_SHUTDOWN_DELAY`
- `AUTO_OPEN_BROWSER`
- `ARCHIVE_TOPIC_CHANGE_ONLY_IF_SPAM` — only flag topic shifts that lead to spam snapshots (default `1`)
- `ARCHIVE_CJK_DENSITY_THRESHOLD` — minimum CJK character density to label as ideographs (default `0.25`)
- `ARCHIVE_CHINESE_DENSITY_THRESHOLD` — minimum CJK density required before checking Chinese spam terms (default `0.15`)

See `.env.example` for a minimal template.

## Notes

- `create_app()` now loads default config even when called without arguments.
- CORS is off by default and is only enabled when `CORS_ORIGINS` is configured.
- Scan state is stored in memory inside `app.checker_state`.
