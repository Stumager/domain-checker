"""Main entry point for the application."""

import webbrowser
from threading import Timer

from app import create_app
from config import get_config


def open_browser(host, port):
    """Open the app in the default browser."""
    webbrowser.open_new(f"http://{host}:{port}/")


if __name__ == "__main__":
    config_class = get_config()
    app = create_app(config_class)

    host = app.config.get("HOST", "127.0.0.1")
    port = app.config.get("PORT", 8080)
    debug = bool(app.config.get("DEBUG", False))

    print(f"Starting DNS Checker on {host}:{port}")

    app.browser_monitor.start()
    if app.config.get("AUTO_OPEN_BROWSER", True):
        Timer(1.5, lambda: open_browser(host, port)).start()
    app.run(host=host, port=port, debug=debug, threaded=True, use_reloader=False)
