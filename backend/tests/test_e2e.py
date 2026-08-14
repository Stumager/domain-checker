"""Browser tests for the frontend.

The JS modules have no unit tests, so these cover the wiring that a static
check cannot: that `data-action` buttons reach their module, that delegated
handlers survive a re-render, and that a tab still talks to its endpoint.

Marked `e2e` so the fast suite can skip them:

    python -m pytest tests -q -m "not e2e"

Requires a browser:

    pip install -r requirements-dev.txt
    python -m playwright install chromium
"""

import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest.mock as mock
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
import app.check_pipeline as check_pipeline  # noqa: E402

pytestmark = pytest.mark.e2e

ADMIN_EMAIL = "admin@checker.local"
ADMIN_PASSWORD = "admin123"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server():
    """Serve the real app on a random port, in this process.

    Same process on purpose: the tests patch dns_check and requests.get, and a
    subprocess would not see those patches.
    """
    from werkzeug.serving import make_server

    tmpdir = tempfile.mkdtemp(prefix="checker-e2e-")
    app = create_app({
        "TESTING": True,
        "LOG_LEVEL": "WARNING",
        "SECRET_KEY": "e2e-secret",
        "DEFAULT_TLDS": "com",
        "SEED_ADMIN_EMAIL": ADMIN_EMAIL,
        "SEED_ADMIN_PASSWORD": ADMIN_PASSWORD,
        "MAPS_DB_PATH": os.path.join(tmpdir, "e2e.db"),
    })

    port = _free_port()
    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{port}"

    server.shutdown()
    thread.join(timeout=5)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def app_page(page, live_server):
    """A signed-in page with a clean localStorage."""
    page.goto(live_server)
    page.fill("#loginEmail", ADMIN_EMAIL)
    page.fill("#loginPassword", ADMIN_PASSWORD)
    page.click("#loginSubmit")
    page.wait_for_selector(".tab-btn[data-tab='checker']")

    page.evaluate("localStorage.removeItem('domainCheckerDB')")
    page.reload()
    page.wait_for_selector(".tab-btn[data-tab='checker']")
    return page


def test_login_rejects_a_bad_password(page, live_server):
    page.goto(live_server)
    page.fill("#loginEmail", ADMIN_EMAIL)
    page.fill("#loginPassword", "definitely-wrong")
    page.click("#loginSubmit")

    error = page.wait_for_selector("#loginError.active")
    assert "Invalid email or password" in error.inner_text()
    assert page.query_selector(".tab-btn") is None


def test_modules_load_without_console_errors(app_page):
    errors = []
    app_page.on("pageerror", lambda e: errors.append(str(e)))
    app_page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    for tab in ("domaindb", "maps", "checker"):
        app_page.click(f".tab-btn[data-tab='{tab}']")
        app_page.wait_for_selector(f"#tab-{tab}.active")

    assert errors == []


def test_scan_reports_results_and_seeds_db_buckets(app_page):
    """Covers the data-action dispatch and the checker -> domain-db call."""
    with mock.patch.object(check_pipeline, "dns_check", return_value="available"):
        app_page.fill("#domainsInput", "alpha.com\nbeta.com")
        assert app_page.inner_text("#domainCount") == "2"

        with mock.patch.object(check_pipeline, "rdap_check", return_value="available"):
            app_page.click("[data-action='startCheck']")
            app_page.wait_for_selector("#resultsSection.active", timeout=15000)
            app_page.wait_for_function(
                "document.getElementById('resultAvailable').textContent === '2'",
                timeout=15000,
            )

    # updateStatus() reaches into domain-db.js to auto-create buckets.
    # DomainDB debounces its localStorage write by 300ms, so wait for it.
    app_page.wait_for_function(
        "Object.keys(JSON.parse(localStorage.getItem('domainCheckerDB') || '{}')).includes('com')",
        timeout=5000,
    )


def test_archive_modal_opens_and_closes(app_page):
    app_page.click("[data-action='toggleArchiveModal']")
    assert app_page.is_visible("#archiveModal.active")

    app_page.click("#archiveModal [data-action='toggleArchiveModal']")
    app_page.wait_for_selector("#archiveModal.active", state="detached")


def test_domain_db_import_search_and_delete(app_page):
    """The delete button is rendered into innerHTML, so it only works delegated."""
    app_page.click(".tab-btn[data-tab='domaindb']")
    app_page.click("#dbAddTldBtn")
    app_page.fill("#dbTldInput", "com")
    app_page.press("#dbTldInput", "Enter")
    app_page.wait_for_selector("#dbTldList .db-tld-item[data-tld='com']")

    app_page.fill("#dbPasteArea", "alpha.com\nbeta.com\ngamma.com")
    app_page.click("[data-action='dbImportFromPaste']")
    app_page.wait_for_function(
        "document.querySelectorAll('#dbDomainList .db-domain-item').length === 3"
    )

    app_page.click("#dbDomainList [data-delete-domain='alpha.com']")
    app_page.wait_for_function(
        "document.querySelectorAll('#dbDomainList .db-domain-item').length === 2"
    )

    app_page.fill("#dbSearchInput", "beta")
    app_page.wait_for_function(
        "document.querySelectorAll('#dbDomainList .db-domain-item').length === 1"
    )
    assert app_page.inner_text("#dbDomainList .db-domain-name") == "beta.com"


def test_bucket_delete_needs_two_clicks_and_can_be_cancelled(app_page):
    app_page.click(".tab-btn[data-tab='domaindb']")
    app_page.click("#dbAddTldBtn")
    app_page.fill("#dbTldInput", "net")
    app_page.press("#dbTldInput", "Enter")
    app_page.wait_for_selector("#dbTldList .db-tld-item[data-tld='net']")

    app_page.click("#dbTldList [data-delete-tld='net']")
    app_page.wait_for_selector("#dbTldList .db-tld-item[data-tld='net'].confirm-delete")

    # clicking outside the sidebar disarms it
    app_page.click("#dbBucketTitle")
    app_page.wait_for_selector(
        "#dbTldList .db-tld-item[data-tld='net'].confirm-delete", state="detached"
    )
    assert app_page.is_visible("#dbTldList .db-tld-item[data-tld='net']")

    app_page.click("#dbTldList [data-delete-tld='net']")
    app_page.click("#dbTldList [data-delete-tld='net']")
    app_page.wait_for_selector("#dbTldList .db-tld-item[data-tld='net']", state="detached")


def test_maps_tab_loads_reference_data(app_page):
    app_page.click(".tab-btn[data-tab='maps']")
    app_page.wait_for_function(
        "document.querySelectorAll('#mapsCountryOptions option').length > 200",
        timeout=15000,
    )
    assert app_page.evaluate("document.querySelectorAll('#mapsNiche option').length") == 20

    # the country filter is swapped for a datalist-backed input at init
    app_page.fill("#mapsCountryFilter", "Spain")
    app_page.wait_for_function(
        "document.querySelectorAll('#mapsCityFilterOptions option').length > 0"
    )


def test_maps_city_field_works_before_a_country_is_picked(app_page):
    """City used to show nothing at all until Country had a value."""
    app_page.click(".tab-btn[data-tab='maps']")
    app_page.wait_for_function(
        "document.querySelectorAll('#mapsCountryOptions option').length > 200",
        timeout=15000,
    )

    # Empty City field, no Country yet -> must not be an empty datalist.
    assert app_page.evaluate("document.querySelectorAll('#mapsCityOptions option').length") > 0

    # An unambiguous city backfills Country. (Language backfill is verified
    # manually — under TESTING config, language warmup is skipped on purpose,
    # so every country's `language` is "" here regardless.)
    app_page.fill("#mapsCity", "Da Nang")
    app_page.wait_for_function("document.getElementById('mapsCountry').value === 'Vietnam'")

    # An ambiguous one (Madrid: Spain and Colombia both have one) must not guess.
    app_page.fill("#mapsCountry", "")
    app_page.fill("#mapsCity", "")
    app_page.fill("#mapsCity", "Madrid")
    app_page.wait_for_timeout(300)
    assert app_page.input_value("#mapsCountry") == ""


def test_active_tab_and_domains_draft_survive_a_reload(app_page):
    """A refresh used to always bounce back to Domain Checker with the
    textarea wiped, even with nothing server-side actually interrupted."""
    app_page.fill("#domainsInput", "alpha.com\nbeta.com")
    app_page.wait_for_timeout(500)  # past the 400ms debounce on the draft save
    app_page.click(".tab-btn[data-tab='maps']")
    app_page.wait_for_function(
        "document.querySelectorAll('#mapsCountryOptions option').length > 200",
        timeout=15000,
    )

    app_page.reload()
    app_page.wait_for_selector(".tab-btn[data-tab='checker']")

    assert app_page.evaluate(
        "document.querySelector('.tab-btn.active')?.dataset.tab"
    ) == "maps"
    assert app_page.evaluate(
        "document.querySelector('.tab-panel.active')?.id"
    ) == "tab-maps"
    # mapsInit() actually re-ran for the restored tab, not just the CSS class
    app_page.wait_for_function(
        "document.querySelectorAll('#mapsCountryOptions option').length > 200",
        timeout=15000,
    )

    app_page.click(".tab-btn[data-tab='checker']")
    assert app_page.input_value("#domainsInput") == "alpha.com\nbeta.com"


def test_sign_out_returns_to_the_login_form(app_page):
    app_page.click("[data-action='authLogout']")
    app_page.wait_for_selector("#loginForm")
    assert app_page.query_selector(".tab-btn") is None
