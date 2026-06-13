"""Utility script for checking 301 redirects and status codes"""

import requests
from urllib.parse import urlparse


def check_redirect(url):
    """
    Check HTTP status code and redirect chain
    
    Args:
        url: URL to check
    """
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    
    try:
        response = requests.head(url, allow_redirects=False, timeout=10)
        status = response.status_code
        
        print(f"Status: {status}")
        
        if status in (301, 302, 307, 308):
            location = response.headers.get("Location", "No redirect target")
            print(f"Redirect to: {location}")
        
        return status
    except Exception as e:
        print(f"Error: {e}")
        return None


if __name__ == "__main__":
    url = input("Enter URL to check: ").strip()
    check_redirect(url)
