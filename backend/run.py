"""Local development entry point.

Servers run the app through wsgi.py; this module exists so `python run.py`
still starts something usable while developing.
"""

import os

from dotenv import load_dotenv

# Must run before config is imported: Config reads os.getenv at class-body time.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from app import create_app  # noqa: E402
from config import get_config  # noqa: E402


if __name__ == "__main__":
    app = create_app(get_config())

    host = app.config.get("HOST", "0.0.0.0")
    port = app.config.get("PORT", 8080)

    print(f"Starting Domain Checker on http://{host}:{port}")
    app.run(
        host=host,
        port=port,
        debug=bool(app.config.get("DEBUG", False)),
        threaded=True,
        use_reloader=False,
    )
