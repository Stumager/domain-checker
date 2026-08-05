"""Flask application package."""

import os

from flask import Flask, jsonify, request
from flask_cors import CORS

from config import Config as DefaultConfig

from . import services
from .browser_monitor import BrowserMonitor
from .models import CheckerState


def _parse_cors_origins(value):
    raw = (value or "").strip()
    if not raw:
        return []

    parts = []
    for token in raw.replace(";", ",").split(","):
        item = token.strip()
        if item:
            parts.append(item)
    return parts


def _register_browser_routes(app: Flask):
    """Register browser heartbeat endpoints on the app."""

    def _get_session_id():
        return (request.args.get("session") or request.headers.get("X-Browser-Session") or "").strip()

    def ping():
        app.browser_monitor.ping(_get_session_id())
        return jsonify({"ok": True})

    def browser_disconnect():
        app.browser_monitor.disconnect(_get_session_id())
        return jsonify({"ok": True})

    app.add_url_rule("/api/ping", endpoint="browser_ping", view_func=ping, methods=["GET", "POST"])
    app.add_url_rule(
        "/api/browser-disconnect",
        endpoint="browser_disconnect",
        view_func=browser_disconnect,
        methods=["POST"],
    )


def create_app(config=None):
    """Application factory."""
    basedir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    app = Flask(
        __name__,
        template_folder=os.path.join(basedir, "templates"),
        static_folder=os.path.join(basedir, "static"),
    )

    app.config.from_object(DefaultConfig)
    if config:
        if isinstance(config, dict):
            app.config.update(config)
        else:
            app.config.from_object(config)

    cors_origins = _parse_cors_origins(app.config.get("CORS_ORIGINS", ""))
    if cors_origins:
        CORS(app, resources={r"/api/*": {"origins": cors_origins}})

    app.checker_state = CheckerState()
    app.browser_monitor = BrowserMonitor(
        enabled=bool(app.config.get("BROWSER_MONITOR_ENABLED", True)),
        timeout=int(app.config.get("BROWSER_MONITOR_TIMEOUT", 60)),
        startup_grace=int(app.config.get("BROWSER_MONITOR_STARTUP_GRACE", 30)),
        shutdown_delay=int(app.config.get("BROWSER_MONITOR_SHUTDOWN_DELAY", 3)),
    )
    _register_browser_routes(app)

    rdap_config = {
        "RDAP_BOOTSTRAP_URL": app.config.get("RDAP_BOOTSTRAP_URL"),
        "RDAP_TIMEOUT": app.config.get("RDAP_TIMEOUT"),
        "RDAP_RETRIES": app.config.get("RDAP_RETRIES"),
        "RDAP_BACKOFF_BASE": app.config.get("RDAP_BACKOFF_BASE"),
        "RDAP_BACKOFF_JITTER": app.config.get("RDAP_BACKOFF_JITTER"),
        "RDAP_CONCURRENCY_DEFAULT": app.config.get("RDAP_CONCURRENCY_DEFAULT"),
        "RDAP_CONCURRENCY_ES": app.config.get("RDAP_CONCURRENCY_ES"),
        "RDAP_CONCURRENCY_IT": app.config.get("RDAP_CONCURRENCY_IT"),
        "RDAP_TLD_OVERRIDES_JSON": app.config.get("RDAP_TLD_OVERRIDES_JSON"),
        "RDAP_CONCURRENCY_OVERRIDES_JSON": app.config.get("RDAP_CONCURRENCY_OVERRIDES_JSON"),
        "RDAP_SESSION_POOL_CONNECTIONS": app.config.get("RDAP_SESSION_POOL_CONNECTIONS"),
        "RDAP_SESSION_POOL_MAXSIZE": app.config.get("RDAP_SESSION_POOL_MAXSIZE"),
        "RDAP_FORBIDDEN_FALLBACK": app.config.get("RDAP_FORBIDDEN_FALLBACK"),
        "RDAP_PARSE_ERROR_BODY": app.config.get("RDAP_PARSE_ERROR_BODY"),
        "RDAP_RESTRICTED_ENABLE": app.config.get("RDAP_RESTRICTED_ENABLE"),
        "RDAP_RESTRICTED_TTL": app.config.get("RDAP_RESTRICTED_TTL"),
        "WHOIS_SERVER_OVERRIDES_JSON": app.config.get("WHOIS_SERVER_OVERRIDES_JSON"),
        "WHOIS_NOT_FOUND_OVERRIDES_JSON": app.config.get("WHOIS_NOT_FOUND_OVERRIDES_JSON"),
        "WHOIS_BOOTSTRAP_ENABLED": app.config.get("WHOIS_BOOTSTRAP_ENABLED"),
        "WHOIS_BOOTSTRAP_SERVER": app.config.get("WHOIS_BOOTSTRAP_SERVER"),
    }
    services.rdap_service.set_config(rdap_config)

    _init_maps_module(app)

    from .routes import api_bp, web_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp)

    from .auth import register_auth
    from .maps_routes import maps_bp

    app.register_blueprint(maps_bp)
    register_auth(app)

    return app


def _init_maps_module(app: Flask):
    """Поднять БД и настроить сервисы модуля Maps."""
    from . import db
    from .services import geo_data, maps_service, proxy_service

    db.configure(app.config.get("MAPS_DB_PATH", ""))
    db.init_db()

    geo_data.set_config({
        "NOMINATIM_URL": app.config.get("NOMINATIM_URL"),
        "NOMINATIM_USER_AGENT": app.config.get("NOMINATIM_USER_AGENT"),
        "NOMINATIM_TIMEOUT": app.config.get("NOMINATIM_TIMEOUT"),
    })
    maps_service.set_config({
        "GMAPS_API_URL": app.config.get("GMAPS_API_URL"),
        "GMAPS_TIMEOUT": app.config.get("GMAPS_TIMEOUT"),
        "GMAPS_DOWNLOAD_TIMEOUT": app.config.get("GMAPS_DOWNLOAD_TIMEOUT"),
        "GMAPS_POLL_INTERVAL": app.config.get("GMAPS_POLL_INTERVAL"),
        "GMAPS_MAX_TIME": app.config.get("GMAPS_MAX_TIME"),
    })
    proxy_service.set_config({
        "PROXY_CHECK_URL": app.config.get("PROXY_CHECK_URL"),
        "PROXY_CHECK_TIMEOUT": app.config.get("PROXY_CHECK_TIMEOUT"),
        "PROXY_CHECK_WORKERS": app.config.get("PROXY_CHECK_WORKERS"),
    })

    # Справочник языков грузится ~6 секунд, поэтому греем его в фоне
    if not app.config.get("TESTING"):
        geo_data.start_language_warmup()

    # Потоки поллинга не переживают рестарт — снимаем зависший статус running
    maps_service.reset_stale_jobs()
