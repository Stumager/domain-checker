"""Helper utility functions"""

from datetime import datetime, timezone
from typing import List


def now_iso() -> str:
    """UTC timestamp in the format every table in the SQLite schema stores."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def escape_like(value: str) -> str:
    """Escape LIKE wildcards so a search term matches literally.

    Pair with ``ESCAPE '\\'`` in the query.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def dedupe(lst: List[str]) -> List[str]:
    """Remove duplicates while preserving order"""
    return list(dict.fromkeys(lst))


def split_list(raw: str) -> List[str]:
    """Split a comma/semicolon/space separated env value, preserving case."""
    parts: List[str] = []
    for token in (raw or "").replace(",", " ").replace(";", " ").split():
        item = token.strip()
        if item:
            parts.append(item)
    return dedupe(parts)


def parse_tlds(raw: str) -> List[str]:
    """Parse TLD string into list of TLDs"""
    raw = (raw or "").strip().lower()
    if not raw:
        return []
    parts: List[str] = []
    for token in raw.replace(",", " ").replace(";", " ").split():
        t = token.strip().lower().lstrip(".")
        if t:
            parts.append(t)
    return dedupe(parts)
