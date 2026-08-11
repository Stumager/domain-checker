"""HTTP-клиент gosom/google-maps-scraper и сборка тела задачи.

Контракт взят из web/job.go и web/web.go этого проекта:
  POST   /api/v1/jobs               -> {"id": "..."}
  GET    /api/v1/jobs/{id}          -> {"ID","Name","Date","Status","Data"}
  DELETE /api/v1/jobs/{id}
  GET    /api/v1/jobs/{id}/download -> CSV
Статусы: pending | working | ok | failed.

JobData.Validate() на стороне Go требует непустые keywords, ровно
двухбуквенный lang, ненулевые depth и max_time.
"""

import re

import requests

from . import geo_data
from .proxy_service import working_proxies

_CONFIG = {
    "GMAPS_API_URL": "http://localhost:8090",
    "GMAPS_TIMEOUT": 20.0,
    "GMAPS_DOWNLOAD_TIMEOUT": 120.0,
    "GMAPS_MAX_TIME": 600,
}


class GmapsUnavailable(RuntimeError):
    """Контейнер gmaps недоступен."""


class GmapsError(RuntimeError):
    """gmaps ответил ошибкой."""


def set_config(config: dict):
    for key, value in (config or {}).items():
        if value not in (None, ""):
            _CONFIG[key] = value


def _api_url(path: str) -> str:
    return str(_CONFIG["GMAPS_API_URL"]).rstrip("/") + path


def _request(method: str, path: str, **kwargs):
    kwargs.setdefault("timeout", float(_CONFIG["GMAPS_TIMEOUT"]))
    try:
        return requests.request(method, _api_url(path), **kwargs)
    except requests.RequestException as exc:
        raise GmapsUnavailable(f"Google Maps scraper is unreachable at {_api_url('')}: {exc}")


def pick(payload: dict, *names):
    """Достать поле независимо от регистра: Go отдаёт Status, спека обещает status."""
    if not isinstance(payload, dict):
        return None

    lowered = {str(key).lower(): value for key, value in payload.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def health() -> bool:
    try:
        return _request("GET", "/api/v1/jobs").status_code < 500
    except GmapsUnavailable:
        return False


def create_gmaps_job(payload: dict) -> str:
    response = _request("POST", "/api/v1/jobs", json=payload)
    if response.status_code >= 400:
        raise GmapsError(f"gmaps rejected the job ({response.status_code}): {response.text[:300]}")

    job_id = pick(response.json() or {}, "id")
    if not job_id:
        raise GmapsError("gmaps did not return a job id")

    return str(job_id)


def get_gmaps_job(gmaps_job_id: str) -> dict:
    response = _request("GET", f"/api/v1/jobs/{gmaps_job_id}")
    if response.status_code == 404:
        raise GmapsError("job not found in gmaps")
    if response.status_code >= 400:
        raise GmapsError(f"gmaps status error ({response.status_code})")

    return response.json() or {}


def delete_gmaps_job(gmaps_job_id: str):
    if not gmaps_job_id:
        return
    try:
        _request("DELETE", f"/api/v1/jobs/{gmaps_job_id}")
    except (GmapsUnavailable, GmapsError):
        pass


def download_gmaps_csv(gmaps_job_id: str) -> str:
    response = _request(
        "GET",
        f"/api/v1/jobs/{gmaps_job_id}/download",
        timeout=float(_CONFIG["GMAPS_DOWNLOAD_TIMEOUT"]),
    )
    if response.status_code >= 400:
        raise GmapsError(f"gmaps download failed ({response.status_code})")

    # requests defaults to ISO-8859-1 for text/* without an explicit charset.
    # The scraper emits UTF-8 CSV, so use the declared charset when present and
    # otherwise decode as UTF-8 to preserve non-ASCII business names/addresses.
    content_type = response.headers.get("Content-Type", "")
    charset = re.search(r"charset\s*=\s*([\\w.-]+)", content_type, re.IGNORECASE)
    response.encoding = charset.group(1) if charset else "utf-8"
    return response.text


# ---------------------------------------------------------------------------
# Сборка задачи
# ---------------------------------------------------------------------------

def normalize_lang(value: str) -> str:
    """Свести код языка к двухбуквенному; мусор заменить на 'en'.

    Обрезать до двух символов нельзя: из 'xyz' получился бы валидный на вид 'xy'.
    """
    lang = (value or "").strip().lower()
    if len(lang) == 2 and lang.isalpha():
        return lang

    # локали вида es-ES / es_ES приводим к базовому языку
    for separator in ("-", "_"):
        if separator in lang:
            head = lang.split(separator, 1)[0]
            if len(head) == 2 and head.isalpha():
                return head

    return "en"


def build_query(job: dict) -> str:
    custom = (job.get("custom_query") or "").strip()
    if custom:
        return custom
    return f"{job.get('niche', '')} in {job.get('city', '')}".strip()


def build_payload(job: dict, bbox=None) -> dict:
    """Собрать тело запроса для POST /api/v1/jobs."""
    cycle = int(job.get("cycle_count") or 0) + 1
    payload = {
        "name": f"{job.get('niche', '')} / {job.get('city', '')} #{cycle}".strip(),
        "keywords": [build_query(job)],
        "lang": normalize_lang(job.get("language")),
        "zoom": int(job.get("zoom") or 15),
        "depth": max(1, int(job.get("depth") or 10)),
        "max_time": int(_CONFIG["GMAPS_MAX_TIME"]),
        "email": False,
        "extra_reviews": False,
        "fast_mode": False,
        "proxies": working_proxies(job.get("owner_id") or 1),
    }

    if bbox:
        center = geo_data.bbox_center(bbox)
        if center:
            payload["lat"] = f"{center[0]:.6f}"
            payload["lon"] = f"{center[1]:.6f}"
            payload["radius"] = max(1000, geo_data.bbox_radius_m(bbox))
        # Текущий web API gmaps не читает grid_bbox/grid_cell/concurrency
        # (это флаги CLI) — отправляем для совместимости, лишние поля он игнорирует.
        payload["grid_bbox"] = geo_data.bbox_to_grid(bbox)

    payload["grid_cell"] = float(job.get("grid_cell") or 1.0)
    payload["concurrency"] = int(job.get("concurrency") or 4)
    return payload
