"""Domain validation utilities"""


def normalize_domain(d: str) -> str:
    """Normalize domain string for processing"""
    d = (d or "").strip().lower()
    d = d.replace("https://", "").replace("http://", "")
    d = d.strip()
    
    # strip path/query/fragment if user pasted URL
    d = d.split("/", 1)[0]
    d = d.split("?", 1)[0]
    d = d.split("#", 1)[0]
    
    d = d.rstrip(".").rstrip("/")
    return d


def to_ascii(domain: str) -> str:
    """Convert domain to ASCII (IDNA) format"""
    domain = normalize_domain(domain)
    try:
        return domain.encode("idna").decode("ascii")
    except Exception:
        return domain


def is_valid_domain(domain: str) -> bool:
    """Validate domain format"""
    domain = normalize_domain(domain)
    
    # restrictions
    if domain.endswith(".gov"):
        return False
    
    if len(domain) > 253:
        return False
    
    parts = domain.split(".")
    if len(parts) < 2:
        return False
    
    for part in parts:
        if len(part) == 0 or len(part) > 63:
            return False
        if not all(c.isalnum() or c == "-" for c in part):
            return False
        if part.startswith("-") or part.endswith("-"):
            return False
    
    return True
