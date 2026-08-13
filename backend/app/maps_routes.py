"""Flask API модуля Maps Scraper."""

import io
from datetime import datetime

from flask import Blueprint, jsonify, request, send_file

from . import db
from .auth import current_user
from .services import geo_data, maps_service, proxy_service
from .services.geo_data import GeoDataMissing
from .services.maps_service import GmapsError, GmapsUnavailable
from .utils import escape_like, now_iso

maps_bp = Blueprint("maps", __name__, url_prefix="/api/maps")

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500


def _owner_id():
    return int(current_user()["id"])


def _like(value: str) -> str:
    """Обернуть экранированный поисковый запрос в LIKE-шаблон."""
    return f"%{escape_like(value)}%"


# Достаёт часть домена после последней точки средствами SQLite
_TLD_EXPR = "lower(replace(domain, rtrim(domain, replace(domain, '.', '')), ''))"


def _domain_filters(args, owner_id, include_tld: bool = True):
    """Собрать WHERE и параметры для выборки доменов."""
    clauses, params = ["maps_domains.owner_id = ?"], [owner_id]

    country = (args.get("country") or "").strip()
    if country:
        clauses.append("country = ?")
        params.append(country)

    city = (args.get("city") or "").strip()
    if city:
        clauses.append("city = ?")
        params.append(city)

    session = (args.get("session") or "").strip()
    if session:
        try:
            clauses.append(
                "EXISTS (SELECT 1 FROM maps_domain_sessions ds "
                "WHERE ds.domain = maps_domains.domain AND ds.job_id = ?)"
            )
            params.append(int(session))
        except ValueError:
            clauses.append("1 = 0")

    if (args.get("active") or "").strip().lower() in ("1", "true", "yes"):
        active = maps_service.active_job(owner_id)
        clauses.append(
            "EXISTS (SELECT 1 FROM maps_domain_sessions ds "
            "WHERE ds.domain = maps_domains.domain AND ds.job_id = ?)" if active else "1 = 0"
        )
        if active:
            params.append(active["id"])

    export_status = (args.get("export_status") or "all").strip().lower()
    if export_status == "new":
        clauses.append("(exported_at IS NULL OR exported_at = '')")
    elif export_status == "exported":
        clauses.append("exported_at IS NOT NULL AND exported_at <> ''")

    tld = (args.get("tld") or "").strip().lstrip(".").lower()
    if include_tld and tld:
        clauses.append("domain LIKE ? ESCAPE '\\'")
        params.append(f"%.{tld}")

    search = (args.get("search") or "").strip()
    if search:
        clauses.append("(domain LIKE ? ESCAPE '\\' OR business_name LIKE ? ESCAPE '\\')")
        params.extend([_like(search), _like(search)])

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


# ---------------------------------------------------------------------------
# Справочники
# ---------------------------------------------------------------------------

@maps_bp.route("/geo", methods=["GET"])
def get_geo():
    try:
        return jsonify(geo_data.geo_with_languages())
    except GeoDataMissing as exc:
        return jsonify({"error": str(exc)}), 500


@maps_bp.route("/niches", methods=["GET"])
def get_niches():
    try:
        return jsonify(geo_data.load_niches())
    except GeoDataMissing as exc:
        return jsonify({"error": str(exc)}), 500


@maps_bp.route("/sessions", methods=["GET"])
def get_sessions():
    rows = db.query_all(
        "SELECT j.id, j.created_at, j.status, j.niche, j.custom_query, j.country, j.city, "
        "(SELECT COUNT(DISTINCT ds.domain) FROM maps_domain_sessions ds WHERE ds.job_id = j.id) AS domains "
        "FROM maps_jobs j WHERE j.owner_id = ? ORDER BY j.id DESC", (_owner_id(),)
    )
    return jsonify(rows)


# ---------------------------------------------------------------------------
# Задачи
# ---------------------------------------------------------------------------

@maps_bp.route("/job/start", methods=["POST"])
def start_job():
    payload = request.json or {}
    try:
        job = maps_service.start_job(payload, _owner_id())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except GmapsUnavailable as exc:
        return jsonify({"error": str(exc)}), 503
    except GmapsError as exc:
        return jsonify({"error": str(exc)}), 409

    return jsonify(job), 201


@maps_bp.route("/job/stop", methods=["POST"])
def stop_job():
    payload = request.json or {}
    try:
        job = maps_service.stop_job(payload.get("job_id"), _owner_id())
    except GmapsError as exc:
        return jsonify({"error": str(exc)}), 409
    except GmapsUnavailable as exc:
        return jsonify({"error": str(exc)}), 503

    return jsonify(job)


@maps_bp.route("/job/status", methods=["GET"])
def job_status():
    owner_id = _owner_id()
    job = maps_service.active_job(owner_id) or maps_service.latest_job(owner_id)
    if not job:
        return jsonify({"job": None, "domains": 0, "total_domains": 0, "coverage": None})

    return jsonify({
        "job": job,
        "domains": maps_service.domain_count(job["id"], owner_id),
        "total_domains": maps_service.domain_count(owner_id=owner_id),
        "coverage": maps_service.coverage_progress(job),
    })


# ---------------------------------------------------------------------------
# Домены
# ---------------------------------------------------------------------------

@maps_bp.route("/domains", methods=["GET"])
def list_domains():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    try:
        limit = int(request.args.get("limit", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        limit = DEFAULT_PAGE_SIZE
    limit = max(1, min(MAX_PAGE_SIZE, limit))

    owner_id = _owner_id()
    where, params = _domain_filters(request.args, owner_id)
    total_row = db.query_one(f"SELECT COUNT(*) AS n FROM maps_domains{where}", tuple(params))
    total = int(total_row["n"]) if total_row else 0

    rows = db.query_all(
        f"SELECT domain, business_name, country, city, niche, discovered_at, exported_at "
        f"FROM maps_domains{where} ORDER BY discovered_at DESC, domain LIMIT ? OFFSET ?",
        tuple(params) + (limit, (page - 1) * limit),
    )

    # Список TLD строим без учёта самого фильтра по TLD,
    # иначе выпадающий список схлопнется до одного пункта.
    tld_where, tld_params = _domain_filters(request.args, owner_id, include_tld=False)
    tld_rows = db.query_all(
        f"SELECT DISTINCT {_TLD_EXPR} AS tld FROM maps_domains{tld_where} ORDER BY tld",
        tuple(tld_params),
    )

    return jsonify({
        "items": rows,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit if total else 0,
        "tlds": [row["tld"] for row in tld_rows if row["tld"]],
    })


@maps_bp.route("/domains/export", methods=["GET"])
def export_domains():
    export_format = (request.args.get("format") or "txt").strip().lower()
    if export_format not in ("txt", "csv"):
        return jsonify({"error": "Invalid format"}), 400

    owner_id = _owner_id()
    where, params = _domain_filters(request.args, owner_id)
    rows = db.query_all(
        f"SELECT domain, business_name, country, city, niche, discovered_at, exported_at "
        f"FROM maps_domains{where} ORDER BY domain",
        tuple(params),
    )

    stamp = datetime.now().strftime("%Y-%m-%d")
    if export_format == "txt":
        body = "\n".join(row["domain"] for row in rows)
        mem_file = io.BytesIO(body.encode("utf-8"))
        mimetype = "text/plain; charset=utf-8"
        filename = f"maps-domains-{stamp}.txt"
    else:
        lines = ["domain;business;country;city;niche;discovered"]
        for row in rows:
            values = [
                row["domain"],
                row["business_name"] or "",
                row["country"] or "",
                row["city"] or "",
                row["niche"] or "",
                row["discovered_at"] or "",
            ]
            # точка с запятой — разделитель, поэтому убираем её из значений
            lines.append(";".join(str(value).replace(";", ",") for value in values))
        # BOM, чтобы Excel открыл UTF-8 корректно
        mem_file = io.BytesIO(("﻿" + "\n".join(lines)).encode("utf-8"))
        mimetype = "text/csv; charset=utf-8"
        filename = f"maps-domains-{stamp}.csv"

    # An export is the user's acknowledgement that these domains were downloaded.
    # Keep this after selecting the rows so the exact same filter is applied.
    if rows:
        db.execute(
            f"UPDATE maps_domains SET exported_at = ?{where}",
            (now_iso(), *params),
        )

    mem_file.seek(0)
    return send_file(mem_file, as_attachment=True, download_name=filename, mimetype=mimetype)


# ---------------------------------------------------------------------------
# Прокси
# ---------------------------------------------------------------------------

@maps_bp.route("/proxies", methods=["GET"])
def get_proxies():
    owner_id = _owner_id()
    try:
        page = max(1, int(request.args.get("page", 1)))
        limit = min(100, max(1, int(request.args.get("limit", 10))))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid pagination parameters"}), 400
    items, total = proxy_service.list_proxies(
        owner_id, page=page, limit=limit, search=request.args.get("search", "")
    )
    return jsonify({
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit,
        "checking": proxy_service.is_checking(),
    })


@maps_bp.route("/proxies", methods=["POST"])
def add_proxies():
    payload = request.json or {}
    raw = payload.get("proxies")
    if isinstance(raw, list):
        raw = "\n".join(str(item) for item in raw)

    owner_id = _owner_id()
    added = proxy_service.add_proxies(raw or "", owner_id)
    items, total = proxy_service.list_proxies(owner_id, page=1, limit=10)
    return jsonify({"added": added, "items": items, "total": total}), 201


@maps_bp.route("/proxies/check", methods=["POST"])
def check_proxies():
    if not proxy_service.start_check_all(_owner_id()):
        return jsonify({"error": "Proxy check is already running"}), 409
    return jsonify({"status": "checking"})


@maps_bp.route("/proxies/<int:proxy_id>", methods=["DELETE"])
def delete_proxy(proxy_id):
    if not proxy_service.delete_proxy(proxy_id, _owner_id()):
        return jsonify({"error": "Proxy not found"}), 404
    return jsonify({"ok": True})


@maps_bp.route("/proxies/failed", methods=["DELETE"])
def delete_failed_proxies():
    deleted = proxy_service.delete_failed_proxies(_owner_id())
    return jsonify({"ok": True, "deleted": deleted})
