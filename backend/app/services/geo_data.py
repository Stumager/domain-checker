"""Гео-данные для модуля Maps: страны/города, язык страны и bbox через Nominatim."""

import json
import logging
import math
import os
import threading

import requests

from .. import db
from ..utils import apply_config, now_iso

logger = logging.getLogger(__name__)

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
    apply_config(_CONFIG, config, source="geo_data")


def _data_dir() -> str:
    """Каталог app/data."""
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

    Дёшево (десятки миллисекунд), поэтому вызывается синхронно на старте.
    """
    global _LANG_WARMED

    try:
        from countryinfo import all_countries

        countries = all_countries() or []
    except Exception:
        # Not fatal: default_language() falls back to "en" per country, but the
        # Maps job then scrapes in the wrong language, so make it visible.
        logger.warning("countryinfo unavailable — country languages will fall back to 'en'", exc_info=True)
        countries = []

    by_name, by_iso = {}, {}
    for country in countries:
        try:
            info = country.info() or {}
        except Exception:
            continue

        lang = _first_two_letter(info.get("languages"))
        if not lang:
            continue

        name = (info.get("name") or "").strip()
        if name:
            by_name[name.casefold()] = lang

        # geo.json (dr5hn) and countryinfo do not always spell a country the
        # same way, so index the alternates too rather than falling back to "en".
        for alternate in info.get("altSpellings") or []:
            token = str(alternate or "").strip()
            if len(token) > 2:
                by_name.setdefault(token.casefold(), lang)

        iso2 = ((info.get("ISO") or {}).get("alpha2") or "").strip().upper()
        if iso2:
            by_iso[iso2] = lang

    with _CACHE_LOCK:
        _LANG_BY_NAME.update(by_name)
        _LANG_BY_ISO.update(by_iso)
        _LANG_WARMED = True


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
        (city.casefold(), country.casefold(), json.dumps(bbox), now_iso()),
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
    except Exception as exc:
        logger.warning("Nominatim lookup failed for %s, %s: %s", city, country, exc)
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


def bbox_to_cells(bbox, cell_km: float = 1.0) -> list:
    """Split a city bbox into real geographic cells for measurable coverage."""
    if not bbox or len(bbox) != 4:
        return []

    south, north, west, east = (float(value) for value in bbox)
    if north <= south or east <= west:
        return []

    cell_km = max(0.1, float(cell_km or 1.0))
    lat_step = cell_km / 111.32
    rows = max(1, math.ceil((north - south) / lat_step))
    cells = []

    for row in range(rows):
        cell_south = south + row * lat_step
        cell_north = min(north, cell_south + lat_step)
        center_lat = (cell_south + cell_north) / 2.0
        lon_step = cell_km / (111.32 * max(0.1, math.cos(math.radians(center_lat))))
        columns = max(1, math.ceil((east - west) / lon_step))
        for column in range(columns):
            cell_west = west + column * lon_step
            cell_east = min(east, cell_west + lon_step)
            cells.append([cell_south, cell_north, cell_west, cell_east])

    return cells
