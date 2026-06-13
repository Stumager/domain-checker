# DNS Checker

Local Flask application for domain availability checks with:

- DNS prefiltering
- RDAP final verification
- Wayback Machine archive lookup
- TXT and ZIP exports

## Runtime source of truth

The working UI is served from:

- `backend/templates/index.html`
- `backend/static/js/app.js`
- `backend/static/css/style.css`

The old standalone `frontend/` implementation was removed as a runtime UI source. `frontend/README.md` now exists only as a note.

## Quick start

```bash
cd backend
python -m pip install -r requirements.txt
python run.py
```

Or on Windows:

```bat
backend\run.bat
```

Open `http://127.0.0.1:8080`.

## Main features

- Input domains or labels, one per line
- Expand labels using backend-configured default TLDs
- Start only one scan at a time
- Real stop endpoint with graceful cancellation
- Download results by bucket or as one ZIP archive
- Archive modal with redirect stats, risk info, and a `Hide N/A rows` toggle
- Drag-and-drop import for `.txt` and `.csv` files with domains or labels

## Project structure

```text
backend/
  app/
    __init__.py
    browser_monitor.py
    models.py
    routes.py
    services/
    utils/
  templates/
  static/
  .env.example
  config.py
  requirements.txt
  run.py
  run.bat
scripts/
  aggregate_csv.py
  check_redirects.py
  find_duplicates.py
  find_inter_duplicates.py
  wayback_snapshots.py
```

## Notes

- CORS is disabled by default. If you need cross-origin API access, set `CORS_ORIGINS`.
- Browser heartbeat routes live inside the app factory now, so `create_app()` returns a fully wired app.
- The project currently has no persistent database. Scan state lives in memory for the current process.
