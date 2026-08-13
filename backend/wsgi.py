"""WSGI entry point.

    gunicorn -w 1 --threads 8 -b 0.0.0.0:8080 wsgi:app

**One worker only.** Scan progress lives in `app.checker_state` and the Maps
job pollers are daemon threads — both are per-process. A second worker would
answer /api/status without knowing about the scan the first worker is running,
so progress would appear to jump around or reset. Scale with --threads, not
with -w, until that state moves out of process memory.
"""

import os

from dotenv import load_dotenv

# Must run before config is imported: Config reads os.getenv at class-body time.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from app import create_app  # noqa: E402
from config import get_config  # noqa: E402

app = create_app(get_config())
