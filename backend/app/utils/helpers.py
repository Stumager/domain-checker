"""Helper utility functions"""

from typing import List, Sequence


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


def filter_domains_by_tlds(domains: Sequence[str], excluded_tlds: Sequence[str]) -> List[str]:
    """Filter out domains that end with any excluded TLD/suffix."""
    if not excluded_tlds:
        return list(domains)

    normalized = []
    for tld in excluded_tlds:
        t = (tld or "").strip().lower().lstrip(".")
        if t:
            normalized.append(t)

    normalized = dedupe(normalized)
    if not normalized:
        return list(domains)

    suffixes = tuple(f".{t}" for t in normalized)
    excluded_set = set(normalized)
    out: List[str] = []
    for domain in domains:
        d = (domain or "").strip().lower()
        if not d:
            continue
        if d in excluded_set or d.endswith(suffixes):
            continue
        out.append(domain)
    return out
