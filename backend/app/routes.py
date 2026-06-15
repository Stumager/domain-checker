"""Flask API routes for the checker application."""

import io
import threading
import zipfile
from collections import Counter

import requests
from flask import Blueprint, current_app, jsonify, render_template, request, send_file

from .archive.fetcher import (
    _build_archive_request_candidates,
    _enrich_missing_redirects,
    _fetch_archive_rows,
    _fmt_ts,
    _iter_archive_cdx_urls,
    _mask_proxy_url,
    _normalize_proxy_url,
    _normalize_wayback_location,
)
from .archive.reputation import (
    _check_reputation,
    _compute_domain_risk,
    _fetch_rdap_age_days,
    _fetch_tls_age_days,
)
from .archive.spam_detector import (
    _detect_cloaking,
    _detect_language_shifts,
    _detect_spam_from_url,
    _detect_topic_shifts,
    _enrich_spam_flags,
)
from .check_pipeline import run_check
from .models import CheckerState
from .services import expand_domains
from .utils import parse_tlds

web_bp = Blueprint("web", __name__)
api_bp = Blueprint("api", __name__, url_prefix="/api")


def get_checker_state() -> CheckerState:
    return current_app.checker_state


# ---------------------------------------------------------------------------
# Web routes
# ---------------------------------------------------------------------------

@web_bp.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API: checker
# ---------------------------------------------------------------------------

@api_bp.route("/status", methods=["GET"])
def get_status():
    return jsonify(get_checker_state().to_dict())


@api_bp.route("/check", methods=["POST"])
def start_check():
    data = request.json or {}

    domains_str = (data.get("domains") or "").strip()
    try:
        threads = int(data.get("threads", 32))
    except Exception:
        threads = 32
    tlds_raw = (data.get("tlds") or "").strip()
    rdap_recheck_errors = bool(data.get("rdap_recheck_errors", False))

    if not domains_str:
        return jsonify({"error": "No domains"}), 400

    lines = [x.strip() for x in domains_str.splitlines() if x.strip()]
    if not lines:
        return jsonify({"error": "No input lines"}), 400

    tlds = parse_tlds(tlds_raw)
    if not tlds:
        tlds = parse_tlds(current_app.config.get("DEFAULT_TLDS", ""))
    expanded_domains = expand_domains(lines, tlds=tlds)

    max_domains = int(current_app.config.get("MAX_DOMAINS", 200000))
    if len(expanded_domains) > max_domains:
        return jsonify({"error": f"Too many domains ({len(expanded_domains)})"}), 400
    if not expanded_domains:
        return jsonify({"error": "No domains after expansion"}), 400

    threads = max(1, min(128, threads))

    final_check_enabled = current_app.config.get("FINAL_CHECK_ENABLED", True)
    final_check_workers = current_app.config.get("FINAL_CHECK_WORKERS", 12)
    dns_strict_tlds = parse_tlds(current_app.config.get("DNS_PREFILTER_STRICT_TLDS", ""))

    state = get_checker_state()
    if not state.begin_run(len(expanded_domains)):
        return jsonify({"error": "Scan already in progress"}), 409

    t = threading.Thread(
        target=run_check,
        args=(state, expanded_domains, threads, rdap_recheck_errors,
              final_check_enabled, final_check_workers, dns_strict_tlds),
    )
    t.daemon = True
    try:
        t.start()
    except Exception:
        state.fail("Error: could not start background worker")
        raise

    return jsonify({
        "status": "started",
        "final_check_enabled": final_check_enabled,
        "final_check_workers": final_check_workers,
        "rdap_bootstrap_url": current_app.config.get("RDAP_BOOTSTRAP_URL", ""),
        "expanded_total": len(expanded_domains),
        "filtered_total": len(expanded_domains),
        "tlds": tlds,
        "rdap_recheck_errors": rdap_recheck_errors,
    })


@api_bp.route("/stop", methods=["POST"])
def stop_check():
    state = get_checker_state()
    if not state.request_stop():
        return jsonify({"error": "No active scan"}), 409
    return jsonify({"status": "stopping"})


# ---------------------------------------------------------------------------
# API: downloads
# ---------------------------------------------------------------------------

@api_bp.route("/download/<result_type>", methods=["GET"])
def download_results(result_type):
    state = get_checker_state()
    with state.lock:
        if result_type == "available":
            data = "\n".join(state.available)
        elif result_type == "taken":
            data = "\n".join(state.taken)
        elif result_type == "invalid":
            data = "\n".join(state.invalid)
        elif result_type == "errors":
            data = "\n".join(state.errors)
        else:
            return jsonify({"error": "Invalid type"}), 400

    mem_file = io.BytesIO((data or "").encode("utf-8"))
    mem_file.seek(0)
    return send_file(
        mem_file,
        as_attachment=True,
        download_name=f"{result_type}.txt",
        mimetype="text/plain; charset=utf-8",
    )


@api_bp.route("/download-all", methods=["GET"])
def download_all_results():
    state = get_checker_state()
    with state.lock:
        payloads = {
            "available.txt": "\n".join(state.available),
            "taken.txt": "\n".join(state.taken),
            "invalid.txt": "\n".join(state.invalid),
            "errors.txt": "\n".join(state.errors),
        }

    mem_file = io.BytesIO()
    with zipfile.ZipFile(mem_file, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in payloads.items():
            archive.writestr(name, content or "")
    mem_file.seek(0)
    return send_file(
        mem_file,
        as_attachment=True,
        download_name="checker-results.zip",
        mimetype="application/zip",
    )


# ---------------------------------------------------------------------------
# API: Wayback Machine archive analysis
# ---------------------------------------------------------------------------

@api_bp.route("/archive", methods=["POST"])
def get_archive_data():
    from .utils import normalize_domain

    payload = request.json or {}
    domain = normalize_domain(payload.get("domain", ""))
    if not domain:
        return jsonify({"error": "No domain"}), 400

    raw_proxy = (payload.get("proxy") or "").strip()
    proxy_url = _normalize_proxy_url(raw_proxy) if raw_proxy else ""

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    proxy_state = {
        "enabled": bool(proxy_url),
        "mode": "proxy" if proxy_url else "direct",
        "current": _mask_proxy_url(proxy_url) if proxy_url else "Direct connection",
    }
    year_from = int(current_app.config.get("ARCHIVE_YEAR_FROM", 1998))
    year_to = int(current_app.config.get("ARCHIVE_YEAR_TO", 2026))
    verify_empty_fallback = bool(current_app.config.get("ARCHIVE_VERIFY_EMPTY_WITH_FALLBACK", True))
    proxy_timeout = float(current_app.config.get("ARCHIVE_PROXY_TIMEOUT", 10))
    proxy_retries = int(current_app.config.get("ARCHIVE_PROXY_REQUEST_RETRIES", 1))

    def _empty_error(message: str) -> dict:
        return {
            "results": [],
            "total_results": 0,
            "range": {"from": year_from, "to": year_to},
            "proxy": proxy_state,
            "used_connection": "",
            "cdx_endpoint": "",
            "fetch_error": message,
            "truncated": False,
        }

    try:
        rows = []
        truncated = False
        used_connection = ""
        used_cdx_url = ""
        used_req_kwargs = {}
        last_error = None

        for candidate in _build_archive_request_candidates(proxy_url):
            for cdx_url in _iter_archive_cdx_urls():
                try:
                    timeout_value = proxy_timeout if candidate["mode"] == "proxy" else None
                    retries_value = proxy_retries if candidate["mode"] == "proxy" else None
                    rows, year_from, year_to, truncated, has_redirect_col = _fetch_archive_rows(
                        domain,
                        headers,
                        candidate["req_kwargs"],
                        cdx_url=cdx_url,
                        timeout_override=timeout_value,
                        retries_override=retries_value,
                    )
                    if truncated and not rows:
                        last_error = RuntimeError("Archive fetch exceeded time limit before first page.")
                        continue
                    if verify_empty_fallback and candidate["mode"] == "proxy" and not rows:
                        continue
                    if candidate["mode"] == "proxy" and rows:
                        redirect_fallback = bool(current_app.config.get(
                            "ARCHIVE_REDIRECT_FALLBACK_ON_MISSING_COLUMNS", True))
                        if redirect_fallback:
                            has_redirect_candidates = any(r[2] in ("301", "302") for r in rows)
                            if has_redirect_candidates and not has_redirect_col:
                                last_error = RuntimeError("Proxy CDX response missing redirect columns.")
                                continue
                    used_connection = candidate["label"]
                    used_cdx_url = cdx_url
                    used_req_kwargs = candidate["req_kwargs"]
                    break
                except Exception as exc:
                    last_error = exc
            if used_connection:
                break

        if not used_connection:
            message = "Wayback request failed via all attempted connections."
            if last_error:
                message = f"{message} Last error: {str(last_error)}"
            return jsonify(_empty_error(message))

        rows, _redirect_resolved, _redirect_attempted, _redirect_direct = (
            _enrich_missing_redirects(rows, headers, used_req_kwargs)
        )
        (
            spam_hits, spam_checked, spam_flagged, spam_total,
            topic_sigs, topic_lengths, spam_propagated_labels, metrics_by_idx,
        ) = _enrich_spam_flags(rows, headers, used_req_kwargs)

        topic_shifts, topic_checked, topic_shifted = _detect_topic_shifts(
            rows, topic_sigs, topic_lengths, spam_hits)
        scripts_by_idx = {idx: m.get("script", "") for idx, m in (metrics_by_idx or {}).items()}
        language_shifts, language_checked, language_shifted = _detect_language_shifts(
            rows, scripts_by_idx, topic_lengths)
        cloaking_flags, cloaking_checked, cloaking_detected = _detect_cloaking(
            rows, topic_sigs, topic_lengths, headers, used_req_kwargs)

        results = []
        url_spam_count = 0
        url_spam_labels: Counter = Counter()
        for idx, (ts, orig, status, redirect) in enumerate(rows):
            redirect_value = (redirect or "").strip()
            if redirect_value.lower() in ("-", "null", "none"):
                redirect_value = ""
            if redirect_value:
                redirect_value = _normalize_wayback_location(redirect_value)

            row_spam = spam_hits.get(idx, [])
            url_spam = _detect_spam_from_url(orig)
            if redirect_value:
                url_spam.extend(_detect_spam_from_url(redirect_value))
            if url_spam:
                url_spam_count += 1
                for label in url_spam:
                    if label:
                        url_spam_labels[label] += 1
            if row_spam or url_spam or spam_propagated_labels:
                merged = []
                for label in row_spam + url_spam + spam_propagated_labels:
                    if label and label not in merged:
                        merged.append(label)
                row_spam = merged

            results.append({
                "date": _fmt_ts(ts),
                "status": status,
                "link": f"https://web.archive.org/web/{ts}/{orig}",
                "redirect": redirect_value,
                "spam": row_spam,
                "topic_shift": bool(topic_shifts.get(idx)),
                "language_shift": bool(language_shifts.get(idx)),
                "cloaking": bool(cloaking_flags.get(idx)),
            })

        reputation = _check_reputation(domain)
        rdap_age_days = _fetch_rdap_age_days(domain)
        tls_age_days = _fetch_tls_age_days(domain)
        risk = _compute_domain_risk(
            rows,
            spam_hits, spam_checked, spam_flagged, spam_propagated_labels,
            url_spam_count, url_spam_labels, metrics_by_idx,
            topic_checked, topic_shifted,
            language_checked, language_shifted,
            cloaking_checked, cloaking_detected,
            reputation, rdap_age_days, tls_age_days,
        )

        return jsonify({
            "results": results,
            "total_results": len(results),
            "range": {"from": year_from, "to": year_to},
            "proxy": proxy_state,
            "used_connection": used_connection,
            "cdx_endpoint": used_cdx_url,
            "redirects_resolved": _redirect_resolved,
            "redirects_probed": _redirect_attempted,
            "redirects_direct_fallback": _redirect_direct,
            "spam_checked": spam_checked,
            "spam_flagged": spam_flagged,
            "spam_total": spam_total,
            "topic_checked": topic_checked,
            "topic_shifted": topic_shifted,
            "language_checked": language_checked,
            "language_shifted": language_shifted,
            "cloaking_checked": cloaking_checked,
            "cloaking_detected": cloaking_detected,
            "reputation": reputation,
            "risk": risk,
            "fetch_error": "",
            "truncated": truncated,
        })

    except requests.Timeout:
        return jsonify(_empty_error("Wayback request timed out."))
    except Exception as exc:
        return jsonify(_empty_error(f"Wayback request failed: {str(exc)}"))
