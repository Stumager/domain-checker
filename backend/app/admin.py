"""Админ-панель: управление учётными записями."""

import secrets
import string

from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash

from . import db
from .auth import EMAIL_RE, create_user, current_user, is_admin

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")

PASSWORD_LENGTH = 14
_PASSWORD_ALPHABET = string.ascii_letters + string.digits


@admin_bp.before_request
def _require_admin():
    # login_required уже отработал в app-level before_request (auth.py),
    # так что сюда попадают только аутентифицированные запросы.
    if not is_admin():
        return jsonify({"error": "Admin access required"}), 403


def _generate_password() -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


def _admin_count() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM users WHERE role = 'admin'")
    return int(row["n"]) if row else 0


@admin_bp.route("/users", methods=["GET"])
def list_users():
    rows = db.query_all("SELECT id, email, role, created_at FROM users ORDER BY id")
    return jsonify(rows)


@admin_bp.route("/users", methods=["POST"])
def add_user():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    role = (data.get("role") or "user").strip().lower()

    if not EMAIL_RE.match(email):
        return jsonify({"error": "Invalid email"}), 400
    if role not in ("admin", "user"):
        return jsonify({"error": "Role must be 'admin' or 'user'"}), 400
    if db.query_one("SELECT id FROM users WHERE email = ?", (email,)):
        return jsonify({"error": "This email is already registered"}), 409

    password = _generate_password()
    user = create_user(email, password, role)
    # Only returned on creation — never recoverable afterwards, only resettable.
    user["password"] = password
    return jsonify(user), 201


@admin_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
def reset_password(user_id):
    target = db.query_one("SELECT id, email FROM users WHERE id = ?", (user_id,))
    if not target:
        return jsonify({"error": "User not found"}), 404

    password = _generate_password()
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(password), user_id),
    )
    return jsonify({"id": target["id"], "email": target["email"], "password": password})


@admin_bp.route("/users/<int:user_id>/role", methods=["PATCH"])
def change_role(user_id):
    data = request.json or {}
    role = (data.get("role") or "").strip().lower()
    if role not in ("admin", "user"):
        return jsonify({"error": "Role must be 'admin' or 'user'"}), 400

    target = db.query_one("SELECT id, role FROM users WHERE id = ?", (user_id,))
    if not target:
        return jsonify({"error": "User not found"}), 404

    if target["role"] == "admin" and role == "user" and _admin_count() <= 1:
        return jsonify({"error": "Cannot demote the last remaining admin"}), 409

    db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    return jsonify({"id": user_id, "role": role})


@admin_bp.route("/users/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):
    target = db.query_one("SELECT id, role FROM users WHERE id = ?", (user_id,))
    if not target:
        return jsonify({"error": "User not found"}), 404

    me = current_user()
    if me and int(me["id"]) == user_id:
        return jsonify({"error": "You cannot delete your own account"}), 409
    if target["role"] == "admin" and _admin_count() <= 1:
        return jsonify({"error": "Cannot delete the last remaining admin"}), 409

    # Deliberately does not touch their maps_jobs/maps_domains/maps_proxies —
    # those are scraped business data (leads), not account state, and should
    # survive an account being removed.
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return jsonify({"ok": True})
