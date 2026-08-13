"""Minimal regression tests for the local Flask app."""

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

import requests


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
import app.check_pipeline as check_pipeline  # noqa: E402
import app.routes as routes  # noqa: E402


class BaseAppTestCase(unittest.TestCase):
    """Каждый тест получает свою временную БД и залогиненного клиента."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="checker-test-")
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)

    def create_app_and_client(self, login=True, **overrides):
        config = {
            "TESTING": True,
            "LOG_LEVEL": "WARNING",
            "FINAL_CHECK_ENABLED": True,
            "DEFAULT_TLDS": "com",
            "SECRET_KEY": "test-secret",
            "MAPS_DB_PATH": os.path.join(self._tmpdir, "maps.db"),
        }
        config.update(overrides)
        app = create_app(config)
        client = app.test_client()

        if login:
            response = client.post("/api/auth/login", json={
                "email": app.config["SEED_ADMIN_EMAIL"],
                "password": app.config["SEED_ADMIN_PASSWORD"],
            })
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        return app, client

    def create_client(self, **overrides):
        return self.create_app_and_client(**overrides)[1]


class CheckerAppTests(BaseAppTestCase):

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

        status = client.get("/api/status")

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

        with patch.object(check_pipeline, "dns_check", side_effect=slow_dns):
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

        with patch.object(check_pipeline, "dns_check", return_value="unknown"):
            start = client.post("/api/check", json={"domains": "example.net", "threads": 1})
            self.assertEqual(start.status_code, 200)

            final_status = self.wait_for_terminal_state(client)
            self.assertEqual(final_status["available"], 0)
            self.assertEqual(final_status["errors"], 1)

    def test_download_all_returns_zip(self):
        client = self.create_client(FINAL_CHECK_ENABLED=False)

        with patch.object(check_pipeline, "dns_check", return_value="available"):
            client.post("/api/check", json={"domains": "example.com", "threads": 1})
            self.wait_for_terminal_state(client)

        response = client.get("/api/download-all")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/zip")


def _dr_response(status_code=200, rating=42):
    """Stand-in for an Ahrefs reply."""
    reply = Mock()
    reply.status_code = status_code
    reply.json.return_value = {"domain_rating": {"domain_rating": rating}}
    return reply


class DrCheckTests(BaseAppTestCase):

    def test_batch_is_resolved_in_parallel(self):
        client = self.create_client(DR_WORKERS=4)
        domains = ["a.com", "b.com", "c.com", "d.com"]

        with patch.object(routes.requests, "get", return_value=_dr_response(rating=17.4)) as mocked:
            response = client.post("/api/dr-check", json={"domains": domains})

        self.assertEqual(response.status_code, 200)
        results = response.get_json()["results"]

        self.assertEqual(mocked.call_count, len(domains))
        self.assertEqual([r["domain"] for r in results], domains)
        # 17.4 must come back rounded, the way the table renders it
        self.assertEqual({r["dr"] for r in results}, {17})

    def test_single_domain_form_still_works(self):
        client = self.create_client()

        with patch.object(routes.requests, "get", return_value=_dr_response(rating=8)):
            response = client.post("/api/dr-check", json={"domain": "https://www.example.com/x"})

        results = response.get_json()["results"]
        self.assertEqual(len(results), 1)
        # URL noise is stripped before the lookup
        self.assertEqual(results[0]["domain"], "www.example.com")
        self.assertEqual(results[0]["dr"], 8)

    def test_rate_limit_is_retried_then_reported(self):
        client = self.create_client(DR_RETRIES=1, DR_RETRY_BACKOFF=0)

        with patch.object(routes.requests, "get", return_value=_dr_response(status_code=429)) as mocked:
            response = client.post("/api/dr-check", json={"domains": ["a.com"]})

        result = response.get_json()["results"][0]
        self.assertEqual(mocked.call_count, 2)  # initial attempt + one retry
        self.assertIsNone(result["dr"])
        self.assertEqual(result["error"], "rate limited")

    def test_one_failure_does_not_sink_the_batch(self):
        client = self.create_client(DR_WORKERS=2)

        def flaky(url, **kwargs):
            if kwargs["params"]["target"] == "bad.com":
                raise requests.Timeout()
            return _dr_response(rating=55)

        with patch.object(routes.requests, "get", side_effect=flaky):
            response = client.post("/api/dr-check", json={"domains": ["good.com", "bad.com"]})

        results = {r["domain"]: r for r in response.get_json()["results"]}
        self.assertEqual(results["good.com"]["dr"], 55)
        self.assertIsNone(results["bad.com"]["dr"])
        self.assertEqual(results["bad.com"]["error"], "timeout")

    def test_batch_over_the_cap_is_rejected(self):
        client = self.create_client(DR_MAX_BATCH=2)

        response = client.post("/api/dr-check", json={"domains": ["a.com", "b.com", "c.com"]})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Too many domains", response.get_json()["error"])

    def test_empty_input_is_rejected(self):
        client = self.create_client()

        self.assertEqual(client.post("/api/dr-check", json={"domains": []}).status_code, 400)
        self.assertEqual(client.post("/api/dr-check", json={"domain": "  "}).status_code, 400)
        self.assertEqual(client.post("/api/dr-check", json={"domains": "a.com"}).status_code, 400)


class AuthTests(BaseAppTestCase):
    def test_api_requires_login(self):
        client = self.create_client(login=False)

        self.assertEqual(client.get("/api/status").status_code, 401)
        self.assertEqual(client.get("/api/maps/niches").status_code, 401)

    def test_page_serves_login_form_when_anonymous(self):
        client = self.create_client(login=False)

        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("loginForm", response.get_data(as_text=True))

    def test_login_me_and_logout(self):
        app, client = self.create_app_and_client()

        me = client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.get_json()["email"], app.config["SEED_ADMIN_EMAIL"])

        self.assertEqual(client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(client.get("/api/auth/me").status_code, 401)
        self.assertEqual(client.get("/api/status").status_code, 401)

    def test_login_rejects_bad_password(self):
        app, client = self.create_app_and_client(login=False)

        response = client.post("/api/auth/login", json={
            "email": app.config["SEED_ADMIN_EMAIL"],
            "password": "wrong",
        })
        self.assertEqual(response.status_code, 401)

    def test_register_validates_and_rejects_duplicates(self):
        app, client = self.create_app_and_client(login=False)

        self.assertEqual(client.post("/api/auth/register", json={
            "email": "bad", "password": "secret1"}).status_code, 400)
        self.assertEqual(client.post("/api/auth/register", json={
            "email": "new@example.com", "password": "123"}).status_code, 400)
        self.assertEqual(client.post("/api/auth/register", json={
            "email": "new@example.com", "password": "secret1"}).status_code, 201)
        self.assertEqual(client.post("/api/auth/register", json={
            "email": "new@example.com", "password": "secret1"}).status_code, 409)


class MapsApiTests(BaseAppTestCase):
    def test_niches_endpoint_returns_twenty(self):
        client = self.create_client()

        payload = client.get("/api/maps/niches").get_json()
        self.assertEqual(len(payload), 20)
        self.assertIn("restaurants", [item["value"] for item in payload])

    def test_geo_endpoint_returns_countries_with_cities(self):
        client = self.create_client()

        payload = client.get("/api/maps/geo").get_json()
        self.assertGreater(len(payload), 100)

        spain = next(item for item in payload if item["code"] == "ES")
        self.assertTrue(spain["cities"])
        madrid = next(city for city in spain["cities"] if city["name"] == "Madrid")
        self.assertIn("lat", madrid)
        self.assertIn("lng", madrid)

    def test_job_start_returns_503_when_gmaps_unreachable(self):
        import requests as requests_lib

        from app.services import geo_data, gmaps_client

        client = self.create_client()

        with patch.object(geo_data, "fetch_bbox", return_value=[40.3, 40.6, -3.8, -3.5]), \
             patch.object(gmaps_client.requests, "request",
                          side_effect=requests_lib.ConnectionError("refused")):
            response = client.post("/api/maps/job/start", json={
                "niche": "restaurants", "country": "Spain", "city": "Madrid",
            })

        self.assertEqual(response.status_code, 503)
        self.assertIn("unreachable", response.get_json()["error"])

    def test_job_start_validates_input(self):
        client = self.create_client()

        response = client.post("/api/maps/job/start", json={"niche": "restaurants"})
        self.assertEqual(response.status_code, 400)

    def test_status_is_empty_before_any_job(self):
        client = self.create_client()

        payload = client.get("/api/maps/job/status").get_json()
        self.assertIsNone(payload["job"])
        self.assertEqual(payload["domains"], 0)

    def test_domains_pagination_filters_and_export(self):
        from app import db

        client = self.create_client()

        with db.get_connection() as conn:
            for index in range(120):
                tld = "es" if index % 2 == 0 else "it"
                conn.execute(
                    "INSERT INTO maps_domains (domain, job_id, country, city, niche, "
                    "business_name, discovered_at) VALUES (?, 1, 'Spain', 'Madrid', "
                    "'restaurants', ?, '2026-01-01T00:00:00+00:00')",
                    (f"shop{index:03d}.{tld}", f"Shop {index}"),
                )

        first = client.get("/api/maps/domains?page=1&limit=50").get_json()
        self.assertEqual(first["total"], 120)
        self.assertEqual(first["pages"], 3)
        self.assertEqual(len(first["items"]), 50)
        self.assertEqual(sorted(first["tlds"]), ["es", "it"])

        filtered = client.get("/api/maps/domains?tld=es").get_json()
        self.assertEqual(filtered["total"], 60)
        # список TLD не схлопывается при выбранном фильтре
        self.assertEqual(sorted(filtered["tlds"]), ["es", "it"])

        searched = client.get("/api/maps/domains?search=shop001").get_json()
        self.assertEqual(searched["total"], 1)

        txt = client.get("/api/maps/domains/export?format=txt&tld=it")
        self.assertEqual(txt.status_code, 200)
        self.assertEqual(len(txt.get_data(as_text=True).splitlines()), 60)

        csv_response = client.get("/api/maps/domains/export?format=csv")
        body = csv_response.get_data(as_text=True)
        self.assertTrue(body.startswith("﻿"))
        self.assertIn("domain;business;country;city;niche;discovered", body)

    def test_search_treats_wildcards_literally(self):
        from app import db

        client = self.create_client()
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO maps_domains (domain, job_id, country, city, niche, "
                "business_name, discovered_at) VALUES ('real.es', 1, '', '', '', '', '')"
            )

        payload = client.get("/api/maps/domains?search=%25").get_json()
        self.assertEqual(payload["total"], 0)

    def test_sessions_and_export_status_filters(self):
        from app import db

        client = self.create_client()
        first_job = db.execute(
            "INSERT INTO maps_jobs (niche, country, city, status) VALUES ('cafes', 'Spain', 'Madrid', 'stopped')"
        )
        second_job = db.execute(
            "INSERT INTO maps_jobs (niche, country, city, status) VALUES ('gyms', 'Italy', 'Rome', 'running')"
        )
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO maps_domains (domain, job_id, country, city, discovered_at) VALUES "
                "('new.es', ?, 'Spain', 'Madrid', '2026-01-01T00:00:00+00:00')", (first_job,)
            )
            conn.execute(
                "INSERT INTO maps_domain_sessions (domain, job_id, discovered_at) VALUES "
                "('new.es', ?, '2026-01-01T00:00:00+00:00')", (first_job,)
            )
            conn.execute(
                "INSERT INTO maps_domains (domain, job_id, country, city, discovered_at, exported_at) VALUES "
                "('old.it', ?, 'Italy', 'Rome', '2026-01-02T00:00:00+00:00', '2026-01-03T00:00:00+00:00')", (second_job,)
            )
            conn.execute(
                "INSERT INTO maps_domain_sessions (domain, job_id, discovered_at) VALUES "
                "('old.it', ?, '2026-01-02T00:00:00+00:00')", (second_job,)
            )

        sessions = client.get("/api/maps/sessions").get_json()
        self.assertEqual({row["id"] for row in sessions}, {first_job, second_job})
        self.assertEqual(client.get("/api/maps/domains?export_status=new").get_json()["total"], 1)
        self.assertEqual(client.get(f"/api/maps/domains?session={second_job}").get_json()["total"], 1)
        self.assertEqual(client.get("/api/maps/domains?active=1").get_json()["total"], 1)

        exported = client.get("/api/maps/domains/export?format=txt&export_status=new")
        self.assertEqual(exported.get_data(as_text=True), "new.es")
        self.assertEqual(client.get("/api/maps/domains?export_status=new").get_json()["total"], 0)

    def test_maps_data_isolated_between_accounts(self):
        from app import db

        app, admin = self.create_app_and_client()
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO maps_domains (owner_id, domain, country, city, discovered_at) "
                "VALUES (1, 'admin-only.es', 'Spain', 'Madrid', '')"
            )

        other = app.test_client()
        response = other.post("/api/auth/register", json={
            "email": "maps-other@example.com", "password": "secret1"
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(other.get("/api/maps/domains?export_status=all").get_json()["total"], 0)
        self.assertEqual(admin.get("/api/maps/domains?export_status=all").get_json()["total"], 1)

    def test_proxy_crud(self):
        client = self.create_client()

        added = client.post("/api/maps/proxies", json={"proxies": "1.2.3.4:8080\n5.6.7.8:3128"})
        self.assertEqual(added.status_code, 201)
        self.assertEqual(added.get_json()["added"], 2)

        # повторное добавление не создаёт дублей
        self.assertEqual(
            client.post("/api/maps/proxies", json={"proxies": "1.2.3.4:8080"}).get_json()["added"], 0)

        listed = client.get("/api/maps/proxies").get_json()
        self.assertEqual(len(listed["items"]), 2)
        self.assertEqual(listed["items"][0]["status"], "unknown")

        proxy_id = listed["items"][0]["id"]
        self.assertEqual(client.delete(f"/api/maps/proxies/{proxy_id}").status_code, 200)
        self.assertEqual(client.delete(f"/api/maps/proxies/{proxy_id}").status_code, 404)


class MapsServiceTests(BaseAppTestCase):
    def test_download_csv_decodes_utf8_when_server_omits_charset(self):
        from app.services import gmaps_client

        class FakeResponse:
            status_code = 200
            headers = {"Content-Type": "text/csv"}
            content = "title,website\nCafé,https://cafe.es\n".encode("utf-8")
            encoding = "iso-8859-1"

            @property
            def text(self):
                return self.content.decode(self.encoding)

        response = FakeResponse()

        with patch.object(gmaps_client, "_request", return_value=response):
            csv_text = gmaps_client.download_gmaps_csv("live-job")

        self.assertIn("Café", csv_text)

    def test_normalize_domain_strips_scheme_www_and_path(self):
        from app.services.maps_service import normalize_domain

        self.assertEqual(normalize_domain("https://www.example.com/menu?a=1"), "example.com")
        self.assertEqual(normalize_domain("HTTP://Example.COM/"), "example.com")
        self.assertEqual(normalize_domain("http://sub.example.co.uk/x"), "sub.example.co.uk")
        self.assertEqual(normalize_domain(""), "")
        self.assertEqual(normalize_domain("not a domain"), "")
        self.assertEqual(normalize_domain("localhost"), "")

    def test_ingest_csv_applies_tld_filter_and_skips_duplicates(self):
        from app.services import maps_service

        self.create_client()

        csv_text = (
            "input_id,link,title,category,website\n"
            "1,http://a,Cafe Uno,cafe,https://www.cafeuno.es/menu\n"
            "2,http://b,Bar Dos,bar,http://bardos.it\n"
            "3,http://c,No Site,bar,\n"
            "4,http://d,Cafe Uno Again,cafe,https://cafeuno.es\n"
        )
        job = {
            "id": 1, "country": "Spain", "city": "Madrid",
            "niche": "cafes", "tld_filter": "es",
        }

        self.assertEqual(maps_service.ingest_csv(csv_text, job), 1)
        # второй прогон не добавляет уже известный домен
        self.assertEqual(maps_service.ingest_csv(csv_text, job), 0)

        row = maps_service.db.query_one("SELECT * FROM maps_domains WHERE domain = 'cafeuno.es'")
        self.assertEqual(row["business_name"], "Cafe Uno")
        self.assertEqual(row["niche"], "cafes")

    def test_ingest_csv_without_filter_keeps_every_tld(self):
        from app.services import maps_service

        self.create_client()
        csv_text = (
            "title,website\n"
            "Uno,https://cafeuno.es\n"
            "Dos,http://bardos.it\n"
        )

        self.assertEqual(maps_service.ingest_csv(csv_text, {"id": 1, "tld_filter": ""}), 2)

    def test_build_payload_matches_gmaps_contract(self):
        from app.services import maps_service

        self.create_client()

        job = {
            "niche": "dentists", "city": "Madrid", "country": "Spain",
            "language": "es", "depth": 12, "zoom": 14, "concurrency": 6,
            "grid_cell": 2.0, "custom_query": "", "cycle_count": 0,
        }
        payload = maps_service.build_payload(job, bbox=[40.3, 40.6, -3.8, -3.5])

        # обязательные поля JobData.Validate() на стороне Go
        self.assertEqual(payload["keywords"], ["dentists in Madrid"])
        self.assertEqual(len(payload["lang"]), 2)
        self.assertGreater(payload["depth"], 0)
        self.assertGreater(payload["max_time"], 0)
        self.assertIsInstance(payload["proxies"], list)
        self.assertIsInstance(payload["keywords"], list)
        # lat/lon строками, как в Go-структуре
        self.assertIsInstance(payload["lat"], str)
        self.assertIsInstance(payload["lon"], str)

    def test_build_payload_falls_back_to_en_for_bad_language(self):
        from app.services import maps_service

        self.create_client()
        payload = maps_service.build_payload(
            {"niche": "gyms", "city": "Rome", "language": "xyz", "depth": 1})
        self.assertEqual(payload["lang"], "en")

        for bad in ("", "1", "e", "русский", None):
            self.assertEqual(
                maps_service.build_payload({"city": "Rome", "language": bad, "depth": 1})["lang"],
                "en",
            )
        # локаль сводится к базовому языку
        self.assertEqual(
            maps_service.build_payload({"city": "Rome", "language": "es-ES", "depth": 1})["lang"],
            "es",
        )

    def test_custom_query_overrides_generated_one(self):
        from app.services import maps_service

        self.create_client()
        payload = maps_service.build_payload(
            {"niche": "gyms", "city": "Rome", "custom_query": "vegan gyms in Rome", "depth": 1})
        self.assertEqual(payload["keywords"], ["vegan gyms in Rome"])

    def test_bbox_helpers(self):
        from app.services import geo_data

        bbox = [40.0, 41.0, -4.0, -3.0]
        self.assertEqual(geo_data.bbox_center(bbox), (40.5, -3.5))
        self.assertEqual(geo_data.bbox_to_grid(bbox), "40.0,-4.0,41.0,-3.0")
        self.assertGreater(geo_data.bbox_radius_m(bbox), 0)

    def test_bbox_is_cached_in_db_after_first_lookup(self):
        from app.services import geo_data

        self.create_client()

        fake = [{"boundingbox": ["40.3", "40.6", "-3.8", "-3.5"]}]
        with patch.object(geo_data.requests, "get") as mocked:
            mocked.return_value.json.return_value = fake
            mocked.return_value.raise_for_status.return_value = None

            first = geo_data.fetch_bbox("Madrid", "Spain")
            second = geo_data.fetch_bbox("Madrid", "Spain")

        self.assertEqual(first, [40.3, 40.6, -3.8, -3.5])
        self.assertEqual(second, first)
        # второй вызов обслужен из кэша
        self.assertEqual(mocked.call_count, 1)

    def test_nominatim_called_with_required_user_agent(self):
        from app.services import geo_data

        self.create_client()

        with patch.object(geo_data.requests, "get") as mocked:
            mocked.return_value.json.return_value = [{"boundingbox": ["1", "2", "3", "4"]}]
            mocked.return_value.raise_for_status.return_value = None
            geo_data.fetch_bbox("Lisbon", "Portugal")

        headers = mocked.call_args.kwargs["headers"]
        self.assertEqual(headers["User-Agent"], "DomainChecker/1.0")

    def _insert_running_job(self, **overrides):
        from app import db

        fields = {
            "niche": "cafes", "country": "Spain", "city": "Madrid",
            "language": "es", "tld_filter": "", "depth": 10,
        }
        fields.update(overrides)
        return db.execute(
            "INSERT INTO maps_jobs (niche, country, city, language, tld_filter, depth, "
            "status, cycle_count, gmaps_job_id) VALUES (?, ?, ?, ?, ?, ?, 'running', 0, 'g1')",
            tuple(fields.values()),
        )

    def test_poll_loop_ingests_results_and_cycles_until_stopped(self):
        from app import db
        from app.services import geo_data, maps_service

        self.create_client()
        maps_service.set_config({"GMAPS_POLL_INTERVAL": 0.01})
        job_id = self._insert_running_job()

        created = {"count": 0}

        def fake_create(_payload):
            created["count"] += 1
            # после двух перезапусков цикла останавливаем задачу
            if created["count"] >= 2:
                db.execute("UPDATE maps_jobs SET status = 'stopped' WHERE id = ?", (job_id,))
            return f"g{created['count'] + 1}"

        with patch.object(geo_data, "fetch_bbox", return_value=None), \
             patch.object(maps_service, "get_gmaps_job", return_value={"Status": "ok"}), \
             patch.object(maps_service, "download_gmaps_csv",
                          return_value="title,website\nUno,https://www.uno.es/x\n"), \
             patch.object(maps_service, "create_gmaps_job", side_effect=fake_create), \
             patch.object(maps_service, "delete_gmaps_job"):
            maps_service._maps_poll_loop(job_id, "g1")

        job = maps_service.get_job(job_id)
        self.assertEqual(job["status"], "stopped")
        self.assertGreaterEqual(job["cycle_count"], 2)
        self.assertIsNotNone(db.query_one("SELECT 1 FROM maps_domains WHERE domain = 'uno.es'"))

    def test_poll_loop_exits_without_new_cycle_when_stopped(self):
        from app.services import geo_data, maps_service

        self.create_client()
        maps_service.set_config({"GMAPS_POLL_INTERVAL": 0.01})
        job_id = self._insert_running_job()

        maps_service.stop_job(job_id)

        with patch.object(geo_data, "fetch_bbox", return_value=None), \
             patch.object(maps_service, "get_gmaps_job", return_value={"Status": "ok"}), \
             patch.object(maps_service, "create_gmaps_job") as create_mock:
            maps_service._maps_poll_loop(job_id, "g1")

        create_mock.assert_not_called()
        self.assertEqual(maps_service.get_job(job_id)["status"], "stopped")

    def test_poll_loop_gives_up_after_repeated_failures(self):
        from app.services import geo_data, maps_service

        self.create_client()
        maps_service.set_config({"GMAPS_POLL_INTERVAL": 0.01})
        job_id = self._insert_running_job()

        with patch.object(geo_data, "fetch_bbox", return_value=None), \
             patch.object(maps_service, "get_gmaps_job",
                          side_effect=maps_service.GmapsUnavailable("down")):
            maps_service._maps_poll_loop(job_id, "g1")

        self.assertEqual(maps_service.get_job(job_id)["status"], "error")

    def test_start_job_rejects_second_concurrent_job(self):
        from app.services import maps_service

        self.create_client()
        self._insert_running_job()

        with self.assertRaises(maps_service.GmapsError):
            maps_service.start_job({"niche": "gyms", "country": "Spain", "city": "Madrid"})

    def test_stale_running_jobs_are_reset_on_startup(self):
        from app import db
        from app.services import maps_service

        self.create_client()
        db.execute(
            "INSERT INTO maps_jobs (niche, country, city, status) "
            "VALUES ('gyms', 'Spain', 'Madrid', 'running')"
        )

        maps_service.reset_stale_jobs()
        self.assertIsNone(maps_service.active_job())


if __name__ == "__main__":
    unittest.main()
