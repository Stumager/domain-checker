"""Гео-данные для модуля Maps: страны/города, язык страны и bbox через Nominatim."""

import json
import math
import os
import sys
import threading
from datetime import datetime, timezone

import requests

from .. import db

_CACHE_LOCK = threading.Lock()
_GEO_CACHE = None
_NICHES_CACHE = None
_LANG_CACHE = {}
_LANG_BY_NAME = {}
_LANG_BY_ISO = {}
_LANG_WARMED = False

_CONFIG = {
    "NOMINATIM_URL": "https://nominatim.openstreetmap.org/search",
    "NOMINATIM_USER_AGENT": "DomainChecker/1.0",
    "NOMINATIM_TIMEOUT": 15.0,
}


class GeoDataMissing(RuntimeError):
    """Файл с гео-данными не найден рядом с приложением."""


def set_config(config: dict):
    for key, value in (config or {}).items():
        if value not in (None, ""):
            _CONFIG[key] = value


def _data_dir() -> str:
    """Каталог app/data — с учётом запуска из собранного exe."""
    if getattr(sys, "frozen", False):
        for base in (getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable)):
            if not base:
                continue
            candidate = os.path.join(base, "app", "data")
            if os.path.isdir(candidate):
                return candidate

    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _load_json(filename: str):
    path = os.path.join(_data_dir(), filename)
    if not os.path.isfile(path):
        raise GeoDataMissing(f"{filename} not found in {_data_dir()}")

    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_geo():
    """Список стран с городами (кэшируется в памяти)."""
    global _GEO_CACHE
    if _GEO_CACHE is None:
        with _CACHE_LOCK:
            if _GEO_CACHE is None:
                _GEO_CACHE = _load_json("geo.json")
    return _GEO_CACHE


def load_niches():
    global _NICHES_CACHE
    if _NICHES_CACHE is None:
        with _CACHE_LOCK:
            if _NICHES_CACHE is None:
                _NICHES_CACHE = _load_json("niches.json")
    return _NICHES_CACHE


def find_country(name_or_code: str):
    """Найти страну по названию или ISO-коду."""
    needle = (name_or_code or "").strip().casefold()
    if not needle:
        return None

    for country in load_geo():
        if country["name"].casefold() == needle or country["code"].casefold() == needle:
            return country
    return None


def find_city(country: dict, city_name: str):
    needle = (city_name or "").strip().casefold()
    if not country or not needle:
        return None

    for city in country.get("cities") or []:
        if city["name"].casefold() == needle:
            return city
    return None


# ---------------------------------------------------------------------------
# Язык страны
# ---------------------------------------------------------------------------

def _first_two_letter(languages) -> str:
    """gmaps API принимает строго двухбуквенный код языка."""
    for item in languages or []:
        code = (item or "").strip().lower()
        if len(code) == 2 and code.isalpha():
            return code
    return ""


def warm_language_cache():
    """Разобрать весь справочник countryinfo разом.

    CountryInfo().all() занимает ~6 секунд (тянет geoJSON), поэтому вызывается
    один раз в фоновом потоке, а не на запросе.
    """
    global _LANG_WARMED

    try:
        from countryinfo import CountryInfo

        data = CountryInfo().all() or {}
    except Exception:
        data = {}

    by_name, by_iso = {}, {}
    for key, info in data.items():
        if not isinstance(info, dict):
            continue

        lang = _first_two_letter(info.get("languages"))
        if not lang:
            continue

        by_name[str(key).casefold()] = lang

        name = (info.get("name") or "").strip()
        if name:
            by_name[name.casefold()] = lang

        iso2 = ((info.get("ISO") or {}).get("alpha2") or "").strip().upper()
        if iso2:
            by_iso[iso2] = lang

    with _CACHE_LOCK:
        _LANG_BY_NAME.update(by_name)
        _LANG_BY_ISO.update(by_iso)
        _LANG_WARMED = True


def start_language_warmup():
    threading.Thread(target=warm_language_cache, daemon=True).start()


def language_ready() -> bool:
    return _LANG_WARMED


def default_language(country_name: str, country_code: str = "") -> str:
    """Двухбуквенный код языка страны, иначе 'en'."""
    code = (country_code or "").strip().upper()
    if code and code in _LANG_BY_ISO:
        return _LANG_BY_ISO[code]

    key = (country_name or "").strip().casefold()
    if not key:
        return "en"

    if key in _LANG_BY_NAME:
        return _LANG_BY_NAME[key]
    if key in _LANG_CACHE:
        return _LANG_CACHE[key]

    # Точечный запрос — быстрый, в отличие от полного справочника
    lang = "en"
    try:
        from countryinfo import CountryInfo

        lang = _first_two_letter(CountryInfo(country_name).languages()) or "en"
    except Exception:
        lang = "en"

    _LANG_CACHE[key] = lang
    return lang


def geo_with_languages():
    """Страны с городами и кодом языка — для выпадающих списков."""
    out = []
    for country in load_geo():
        out.append({
            "name": country["name"],
            "code": country["code"],
            "language": default_language(country["name"], country["code"]) if _LANG_WARMED else "",
            "cities": country["cities"],
        })
    return out


# ---------------------------------------------------------------------------
# Bounding box через Nominatim (с кэшем в БД)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cached_bbox(city: str, country: str):
    row = db.query_one(
        "SELECT bbox FROM maps_bbox_cache WHERE city = ? AND country = ?",
        (city.casefold(), country.casefold()),
    )
    if not row or not row["bbox"]:
        return None

    try:
        bbox = json.loads(row["bbox"])
    except (TypeError, ValueError):
        return None

    return bbox if isinstance(bbox, list) and len(bbox) == 4 else None


def _store_bbox(city: str, country: str, bbox):
    db.execute(
        "INSERT OR REPLACE INTO maps_bbox_cache (city, country, bbox, cached_at) VALUES (?, ?, ?, ?)",
        (city.casefold(), country.casefold(), json.dumps(bbox), _now_iso()),
    )


def fetch_bbox(city: str, country: str):
    """Вернуть [south, north, west, east] для города, кэшируя результат в БД."""
    city = (city or "").strip()
    country = (country or "").strip()
    if not city or not country:
        return None

    cached = _cached_bbox(city, country)
    if cached:
        return cached

    try:
        response = requests.get(
            _CONFIG["NOMINATIM_URL"],
            params={"q": f"{city},{country}", "format": "json", "limit": 1},
            # User-Agent обязателен по правилам Nominatim
            headers={"User-Agent": _CONFIG["NOMINATIM_USER_AGENT"]},
            timeout=float(_CONFIG["NOMINATIM_TIMEOUT"]),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None

    if not payload:
        return None

    raw = payload[0].get("boundingbox") or []
    if len(raw) != 4:
        return None

    try:
        bbox = [float(value) for value in raw]
    except (TypeError, ValueError):
        return None

    _store_bbox(city, country, bbox)
    return bbox


def bbox_to_grid(bbox) -> str:
    """Nominatim отдаёт [south, north, west, east]; gmaps ждёт minLat,minLon,maxLat,maxLon."""
    if not bbox or len(bbox) != 4:
        return ""

    south, north, west, east = bbox
    return f"{south},{west},{north},{east}"


def bbox_center(bbox):
    """Центр прямоугольника -> (lat, lon)."""
    if not bbox or len(bbox) != 4:
        return None

    south, north, west, east = bbox
    return ((south + north) / 2.0, (west + east) / 2.0)


def bbox_radius_m(bbox) -> int:
    """Радиус в метрах от центра bbox до его угла."""
    center = bbox_center(bbox)
    if not center:
        return 0

    south, north, west, east = bbox
    lat1, lon1 = center
    return _haversine_m(lat1, lon1, north, east)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    earth_radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return int(earth_radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))
