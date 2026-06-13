"""Domain processing and expansion service"""

from typing import List

from ..utils import normalize_domain, dedupe


def expand_domains(lines: List[str], tlds: List[str]) -> List[str]:
    """
    Expand a list of input lines into full domain names.

    - If a line already contains a dot, it is treated as a complete domain and
      included verbatim.
    - Otherwise the line is treated as a label; each TLD from ``tlds`` is
      appended to the label to form a domain.

    The output list is deduplicated while preserving order.

    Args:
        lines: List of domain lines or labels
        tlds: List of TLDs to expand labels with

    Returns:
        List of expanded domains
    """
    out: List[str] = []
    for line in lines:
        d = normalize_domain(line)
        if not d:
            continue

        if "." in d:
            out.append(d)
            continue

        if tlds:
            for tld in tlds:
                out.append(f"{d}.{tld}")
        else:
            out.append(d)

    expanded = dedupe(out)
    return expanded
