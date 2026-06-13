"""Minimal regression tests for the local Flask app."""

import sys
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
import app.routes as routes  # noqa: E402


class CheckerAppTests(unittest.TestCase):
    def create_client(self, **overrides):
        config = {
            "TESTING": True,
            "BROWSER_MONITOR_ENABLED": False,
            "FINAL_CHECK_ENABLED": True,
            "DEFAULT_TLDS": "com",
        }
        config.update(overrides)
        app = create_app(config)
        return app.test_client()

    def wait_for_terminal_state(self, client, timeout=3.0):
        started = time.time()
        while time.time() - started < timeout:
            payload = client.get("/api/status").get_json()
            if payload and not payload.get("running"):
                return payload
            time.sleep(0.05)
        self.fail("Timed out waiting for terminal state")

    def test_create_app_has_core_routes(self):
        client = self.create_client()

        ping = client.post("/api/ping?session=test-browser")
        status = client.get("/api/status")

        self.assertEqual(ping.status_code, 200)
        self.assertEqual(status.status_code, 200)
        self.assertIn("running", status.get_json())

    def test_second_check_returns_409_while_first_is_running(self):
        client = self.create_client()
        entered = Event()
        release = Event()

        def blocking_run_check(state, *args, **kwargs):
            entered.set()
            release.wait(1.5)
            state.finish(stage="done", message="Done!")

        with patch.object(routes, "run_check", side_effect=blocking_run_check):
            first = client.post("/api/check", json={"domains": "example.com", "threads": 1})
            self.assertEqual(first.status_code, 200)
            self.assertTrue(entered.wait(1.0))

            second = client.post("/api/check", json={"domains": "example.org", "threads": 1})
            self.assertEqual(second.status_code, 409)

            release.set()
            self.wait_for_terminal_state(client)

    def test_stop_endpoint_stops_running_scan(self):
        client = self.create_client(FINAL_CHECK_ENABLED=False)

        def slow_dns(_domain):
            time.sleep(0.05)
            return "available"

        with patch.object(routes, "dns_check", side_effect=slow_dns):
            start = client.post(
                "/api/check",
                json={"domains": "\n".join(f"domain{i}.com" for i in range(30)), "threads": 1},
            )
            self.assertEqual(start.status_code, 200)

            stop = client.post("/api/stop")
            self.assertEqual(stop.status_code, 200)

            final_status = self.wait_for_terminal_state(client, timeout=5.0)
            self.assertEqual(final_status["stage"], "stopped")
            self.assertFalse(final_status["running"])

    def test_unknown_dns_without_final_check_stays_in_errors_only(self):
        client = self.create_client(FINAL_CHECK_ENABLED=False, DNS_PREFILTER_STRICT_TLDS="")

        with patch.object(routes, "dns_check", return_value="unknown"):
            start = client.post("/api/check", json={"domains": "example.net", "threads": 1})
            self.assertEqual(start.status_code, 200)

            final_status = self.wait_for_terminal_state(client)
            self.assertEqual(final_status["available"], 0)
            self.assertEqual(final_status["errors"], 1)

    def test_download_all_returns_zip(self):
        client = self.create_client(FINAL_CHECK_ENABLED=False)

        with patch.object(routes, "dns_check", return_value="available"):
            client.post("/api/check", json={"domains": "example.com", "threads": 1})
            self.wait_for_terminal_state(client)

        response = client.get("/api/download-all")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/zip")


if __name__ == "__main__":
    unittest.main()
