"""Flask API модуля Maps Scraper."""

import io
from datetime import datetime

from flask import Blueprint, jsonify, request, send_file

from . import db
from .services import geo_data, maps_service, proxy_service
from .services.geo_data import GeoDataMissing
from .services.maps_service import GmapsError, GmapsUnavailable

maps_bp = Blueprint("maps", __name__, url_prefix="/api/maps")

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500


def _like(value: str) -> str:
    """Экранировать спецсимволы LIKE, чтобы поиск не срабатывал как шаблон."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# Достаёт часть домена после последней точки средствами SQLite
_TLD_EXPR = "lower(replace(domain, rtrim(domain, replace(domain, '.', '')), ''))"


def _domain_filters(args, include_tld: bool = True):
    """Собрать WHERE и параметры для выборки доменов."""
    clauses, params = [], []

    country = (args.get("country") or "").strip()
    if country:
        clauses.append("country = ?")
        params.append(country)

    city = (args.get("city") or "").strip()
    if city:
        clauses.append("city = ?")
        params.append(city)

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


# ---------------------------------------------------------------------------
# Задачи
# ---------------------------------------------------------------------------

@maps_bp.route("/job/start", methods=["POST"])
def start_job():
    payload = request.json or {}
    try:
        job = maps_service.start_job(payload)
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
        job = maps_service.stop_job(payload.get("job_id"))
    except GmapsError as exc:
        return jsonify({"error": str(exc)}), 409
    except GmapsUnavailable as exc:
        return jsonify({"error": str(exc)}), 503

    return jsonify(job)


@maps_bp.route("/job/status", methods=["GET"])
def job_status():
    job = maps_service.active_job() or maps_service.latest_job()
    if not job:
        return jsonify({"job": None, "domains": 0, "total_domains": 0})

    return jsonify({
        "job": job,
        "domains": maps_service.domain_count(job["id"]),
        "total_domains": maps_service.domain_count(),
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

    where, params = _domain_filters(request.args)
    total_row = db.query_one(f"SELECT COUNT(*) AS n FROM maps_domains{where}", tuple(params))
    total = int(total_row["n"]) if total_row else 0

    rows = db.query_all(
        f"SELECT domain, business_name, country, city, niche, discovered_at "
        f"FROM maps_domains{where} ORDER BY discovered_at DESC, domain LIMIT ? OFFSET ?",
        tuple(params) + (limit, (page - 1) * limit),
    )

    # Список TLD строим без учёта самого фильтра по TLD,
    # иначе выпадающий список схлопнется до одного пункта.
    tld_where, tld_params = _domain_filters(request.args, include_tld=False)
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

    where, params = _domain_filters(request.args)
    rows = db.query_all(
        f"SELECT domain, business_name, country, city, niche, discovered_at "
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

    mem_file.seek(0)
    return send_file(mem_file, as_attachment=True, download_name=filename, mimetype=mimetype)


# ---------------------------------------------------------------------------
# Прокси
# ---------------------------------------------------------------------------

@maps_bp.route("/proxies", methods=["GET"])
def get_proxies():
    return jsonify({
        "items": proxy_service.list_proxies(),
        "checking": proxy_service.is_checking(),
    })


@maps_bp.route("/proxies", methods=["POST"])
def add_proxies():
    payload = request.json or {}
    raw = payload.get("proxies")
    if isinstance(raw, list):
        raw = "\n".join(str(item) for item in raw)

    added = proxy_service.add_proxies(raw or "")
    return jsonify({"added": added, "items": proxy_service.list_proxies()}), 201


@maps_bp.route("/proxies/check", methods=["POST"])
def check_proxies():
    if not proxy_service.start_check_all():
        return jsonify({"error": "Proxy check is already running"}), 409
    return jsonify({"status": "checking"})


@maps_bp.route("/proxies/<int:proxy_id>", methods=["DELETE"])
def delete_proxy(proxy_id):
    if not proxy_service.delete_proxy(proxy_id):
        return jsonify({"error": "Proxy not found"}), 404
    return jsonify({"ok": True})
