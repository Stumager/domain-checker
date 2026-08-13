"""DNS checking service"""

import socket
import time
import threading

from ..utils import apply_config, normalize_domain, is_valid_domain, to_ascii

# Try to import dnspython for advanced DNS checking
try:
    import dns.resolver  # type: ignore
    import dns.exception  # type: ignore
    HAS_DNSPYTHON = True
except Exception:
    HAS_DNSPYTHON = False

_dns_thread_local = threading.local()

# Injected from the app factory: the check runs in worker threads with no
# app context, so current_app is not available here.
_CONFIG = {
    "DNS_RESOLVERS": ["1.1.1.1", "8.8.8.8"],
    "DNS_TIMEOUT": 1.6,
    "DNS_RETRIES": 2,
}


def set_config(config: dict):
    apply_config(_CONFIG, config, source="dns_checker")


def _get_dns_resolver(timeout: float):
    """Get or create a thread-local resolver bound to the configured nameservers."""
    nameservers = list(_CONFIG["DNS_RESOLVERS"])

    r = getattr(_dns_thread_local, "resolver", None)
    # Rebuild when the configured nameservers changed under us
    if r is None or r.nameservers != nameservers:
        r = dns.resolver.Resolver(configure=False)
        r.nameservers = nameservers
        _dns_thread_local.resolver = r

    # Keep timeouts up to date on each use
    r.lifetime = timeout
    r.timeout = min(1.2, max(0.4, timeout / 2))
    return r


def _dns_check_dnspython(domain: str, retries: int, timeout: float) -> str:
    """Check DNS using dnspython library"""
    domain = normalize_domain(domain)
    if not is_valid_domain(domain):
        return "invalid"
    
    qname = to_ascii(domain)
    
    r = _get_dns_resolver(timeout)
    
    def _try(rrtype: str) -> str:
        for attempt in range(retries + 1):
            try:
                ans = r.resolve(qname, rrtype)
                if getattr(ans, "rrset", None) is not None:
                    return "taken"
                return "taken"
            except dns.resolver.NXDOMAIN:
                return "available"
            except dns.resolver.NoNameservers:
                return "error"
            except dns.resolver.NoAnswer:
                return "unknown"
            except dns.exception.Timeout:
                if attempt < retries:
                    time.sleep(0.06 * (attempt + 1))
                    continue
                return "error"
            except Exception:
                if attempt < retries:
                    time.sleep(0.06 * (attempt + 1))
                    continue
                return "error"
        return "error"
    
    # NS -> SOA
    res_ns = _try("NS")
    if res_ns == "taken":
        return "taken"
    if res_ns == "available":
        return "available"
    if res_ns == "error":
        return "error"
    
    res_soa = _try("SOA")
    if res_soa == "taken":
        return "taken"
    if res_soa == "available":
        return "available"
    if res_soa == "error":
        return "error"
    
    # both NS/SOA NoAnswer -> unknown (let caller decide)
    return "unknown"


def _dns_check_socket(domain: str, retries: int) -> str:
    """Check DNS using socket (fallback)"""
    domain = normalize_domain(domain)
    if not is_valid_domain(domain):
        return "invalid"
    
    EAI_AGAIN = getattr(socket, "EAI_AGAIN", None)
    EAI_FAIL = getattr(socket, "EAI_FAIL", None)
    
    for attempt in range(retries + 1):
        try:
            socket.getaddrinfo(domain, None)
            return "taken"
        except socket.gaierror as e:
            if e.errno in (EAI_AGAIN, EAI_FAIL) and attempt < retries:
                time.sleep(0.06 * (attempt + 1))
                continue
            return "available"
        except Exception:
            if attempt < retries:
                time.sleep(0.06 * (attempt + 1))
                continue
            return "error"


def dns_check(domain: str) -> str:
    """
    Check domain availability via DNS
    
    Returns:
        str: "available" | "taken" | "invalid" | "error" | "unknown"
    """
    retries = int(_CONFIG["DNS_RETRIES"])
    if HAS_DNSPYTHON:
        return _dns_check_dnspython(domain, retries, float(_CONFIG["DNS_TIMEOUT"]))
    return _dns_check_socket(domain, retries)
