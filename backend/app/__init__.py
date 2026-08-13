"""Flask application package."""

import os

from flask import Flask
from flask_cors import CORS

from config import Config as DefaultConfig

from . import services
from .logging_setup import configure_logging
from .models import CheckerState
from .utils import split_list


def _require_secret_key(app: Flask):
    """Fail fast instead of handing every worker a different signing key."""
    if app.config.get("SECRET_KEY"):
        return

    raise RuntimeError(
        "SECRET_KEY is not set. Sessions are signed with it, so a missing key "
        "would log everyone out on restart and break logins entirely once more "
        "than one WSGI worker is running. Generate one and put it in backend/.env:\n"
        '    python -c "import secrets; print(secrets.token_hex(32))"'
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

    configure_logging(app.config.get("LOG_LEVEL", "INFO"))
    _require_secret_key(app)

    cors_origins = split_list(app.config.get("CORS_ORIGINS", ""))
    if cors_origins:
        CORS(app, resources={r"/api/*": {"origins": cors_origins}})

    app.checker_state = CheckerState()

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

    services.dns_checker.set_config({
        "DNS_RESOLVERS": split_list(app.config.get("DNS_RESOLVERS", "")),
        "DNS_TIMEOUT": app.config.get("DNS_TIMEOUT"),
        "DNS_RETRIES": app.config.get("DNS_RETRIES"),
    })

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
        "GMAPS_DEFAULT_DEPTH": app.config.get("GMAPS_DEFAULT_DEPTH"),
        "GMAPS_DEFAULT_ZOOM": app.config.get("GMAPS_DEFAULT_ZOOM"),
        "GMAPS_DEFAULT_CONCURRENCY": app.config.get("GMAPS_DEFAULT_CONCURRENCY"),
        "GMAPS_DEFAULT_GRID_CELL": app.config.get("GMAPS_DEFAULT_GRID_CELL"),
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
