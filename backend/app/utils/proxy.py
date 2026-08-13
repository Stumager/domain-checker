"""Proxy URL parsing, masking and requests kwargs.

Shared by the Wayback fetcher and the Maps proxy pool, which is why these live
in utils rather than inside either feature package.
"""

from urllib.parse import urlsplit

DIRECT_LABEL = "Direct connection"


def normalize_proxy_url(value: str) -> str:
    """Normalize a proxy entry to a requests-compatible URL.

    Accepts ``host:port``, ``host:port:user:pass`` and anything already
    carrying a scheme.
    """
    token = (value or "").strip()
    if not token:
        return ""
    if "://" in token:
        return token

    parts = token.split(":")
    if len(parts) == 2:
        host, port = parts
        return f"http://{host}:{port}"
    if len(parts) == 4:
        host, port, username, password = parts
        return f"http://{username}:{password}@{host}:{port}"
    return f"http://{token}"


def mask_proxy_url(proxy_url: str) -> str:
    """Proxy URL without credentials, safe to show in the UI."""
    if not proxy_url:
        return DIRECT_LABEL

    try:
        parsed = urlsplit(proxy_url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        scheme = parsed.scheme or "http"
        return f"{scheme}://{host}{port}"
    except Exception:
        # Never leak credentials just because the URL failed to parse
        if "@" in proxy_url:
            return proxy_url.split("@", 1)[-1]
        return proxy_url


def proxy_kwargs(proxy_url: str) -> dict:
    """requests kwargs routing a call through *proxy_url*."""
    return {"proxies": {"http": proxy_url, "https": proxy_url}}
