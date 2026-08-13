"""Helper utility functions"""

from typing import List


def dedupe(lst: List[str]) -> List[str]:
    """Remove duplicates while preserving order"""
    return list(dict.fromkeys(lst))


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
