"""Script to fetch and display Wayback Machine snapshots"""

import requests


def get_snapshots(domain):
    """
    Fetch and display all snapshots for a domain
    
    Args:
        domain: Domain name to search
    """
    print(f"\n🔍 Searching snapshots for: {domain}\n")
    
    cdx_url = f"https://web.archive.org/cdx/search/cdx?url={domain}&output=json&collapse=digest"
    response = requests.get(cdx_url)

    if response.status_code != 200:
        print("❌ Error connecting to CDX API")
        return

    data = response.json()

    if len(data) <= 1:
        print("ℹ️  No snapshots found.")
        return

    header = data[0]
    records = data[1:]

    for entry in records:
        timestamp = entry[1]
        original_url = entry[2]
        status_code = entry[4]
        archive_url = f"https://web.archive.org/web/{timestamp}id_/{original_url}"

        redirect_target = ""
        # Check for redirects
        if status_code.startswith("30"):
            try:
                archived_response = requests.get(archive_url, allow_redirects=False, timeout=10)
                location = archived_response.headers.get("Location", "— unknown —")
                redirect_target = f" → {location}"
            except Exception as e:
                redirect_target = f" → Error: {e}"

        print(f"[{timestamp}] {status_code} | {original_url}{redirect_target}")


if __name__ == "__main__":
    domain = input("🌐 Enter domain (e.g., example.com): ").strip()
    get_snapshots(domain)
