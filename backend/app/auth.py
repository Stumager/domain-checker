"""Аутентификация: сессии Flask + хеши паролей werkzeug."""

import logging
import re
from functools import wraps

from flask import Blueprint, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import db
from .utils import now_iso

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# static — отдача css/js для самой формы логина
PUBLIC_ENDPOINTS = {"static"}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 6


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    return db.query_one("SELECT id, email, created_at FROM users WHERE id = ?", (user_id,))


def is_authenticated() -> bool:
    return current_user() is not None


def _unauthenticated():
    """API отвечает 401, обычные страницы — формой входа."""
    if request.path.startswith("/api/"):
        return jsonify({"error": "Authentication required"}), 401
    return render_template("login.html"), 200


def login_required(view):
    """Требовать вход для конкретного обработчика."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_authenticated():
            return _unauthenticated()
        return view(*args, **kwargs)

    return wrapper


def _is_public_request() -> bool:
    if request.method == "OPTIONS":
        return True
    if (request.endpoint or "") in PUBLIC_ENDPOINTS:
        return True
    return request.path.startswith("/api/auth/")


def seed_admin(app):
    """Создать учётку по умолчанию при первом запуске."""
    row = db.query_one("SELECT COUNT(*) AS n FROM users")
    if row and int(row["n"]) > 0:
        return

    email = (app.config.get("SEED_ADMIN_EMAIL") or "admin@checker.local").strip().lower()
    password = app.config.get("SEED_ADMIN_PASSWORD") or "admin123"

    db.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
        (email, generate_password_hash(password), now_iso()),
    )

    # The password is deliberately not logged.
    logger.warning(
        "Seeded the default account %s from SEED_ADMIN_PASSWORD. Change this "
        "password after the first login.", email,
    )


def register_auth(app):
    """Подключить блюпринт и глобальную проверку входа."""
    app.register_blueprint(auth_bp)

    @app.before_request
    def _require_login():
        if _is_public_request() or is_authenticated():
            return None
        return _unauthenticated()

    seed_admin(app)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not EMAIL_RE.match(email):
        return jsonify({"error": "Invalid email"}), 400
    if len(password) < MIN_PASSWORD_LENGTH:
        return jsonify({"error": f"Password must be at least {MIN_PASSWORD_LENGTH} characters"}), 400

    if db.query_one("SELECT id FROM users WHERE email = ?", (email,)):
        return jsonify({"error": "This email is already registered"}), 409

    user_id = db.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
        (email, generate_password_hash(password), now_iso()),
    )
    session["user_id"] = user_id

    return jsonify({"id": user_id, "email": email}), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = db.query_one("SELECT id, email, password_hash FROM users WHERE email = ?", (email,))
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid email or password"}), 401

    session["user_id"] = user["id"]
    return jsonify({"id": user["id"], "email": user["email"]})


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.pop("user_id", None)
    return jsonify({"ok": True})


@auth_bp.route("/me", methods=["GET"])
def me():
    user = current_user()
    if not user:
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify(user)
