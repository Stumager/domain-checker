"""Utilities package"""

from .validators import normalize_domain, to_ascii, is_valid_domain
from .helpers import dedupe, escape_like, now_iso, parse_tlds, split_list
from .proxy import DIRECT_LABEL, mask_proxy_url, normalize_proxy_url, proxy_kwargs
from .settings import apply_config

__all__ = [
    "normalize_domain",
    "to_ascii",
    "is_valid_domain",
    "dedupe",
    "escape_like",
    "now_iso",
    "parse_tlds",
    "split_list",
    "DIRECT_LABEL",
    "mask_proxy_url",
    "normalize_proxy_url",
    "proxy_kwargs",
    "apply_config",
]
