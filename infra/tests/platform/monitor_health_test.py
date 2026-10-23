#!/usr/bin/env python3
"""Behavior checks for the read-only central monitor, using isolated evidence."""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/platform/monitor_health.py"
spec = importlib.util.spec_from_file_location("monitor_health", SCRIPT)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from sqlite_cell_store import SqliteCellStore  # noqa: E402
from sqlite_notifications_store import SqliteNotificationsStore  # noqa: E402


class MonitorHealthTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
        # Notifications live in the notifications cell; the SQLite double stands in for its API.
        self.notifications = SqliteNotificationsStore(sqlite3.connect(self.root / "notifications.db"))
        patcher = patch.object(monitor, "open_notifications_store", lambda *_a, **_k: self.notifications)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"SOLO_BOOK_PRODUCTION_PLATFORM_ROOT": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        # Scheduler jobs live in the automation cell; the SQLite double stands in for its API.
        self.automation = SqliteCellStore(sqlite3.connect(self.root / "automation.db"), "automation")
        self.automation.create("cron-jobs", {"name": "n", "schedule": "* * * * *", "job_type": "sync_calendar",
                                             "is_active": True, "last_status": "failed",
                                             "last_run_at": "2026-10-04 23:59:00"})
        cell_patcher = patch.object(monitor, "open_cell_store", lambda *_a, **_k: self.automation)
        cell_patcher.start()
        self.addCleanup(cell_patcher.stop)
        self.write_yaml("repository-catalog/registries/system-repositories.yaml",
                        "systems:\n  - id: workflow-platform\n    name: Workflow\n  - id: developer-platform\n    name: Developer\n")
        self.write_yaml("infra/platform/cloud-providers.yaml", "providers:\n  - id: example\n    name: Example\n")
        self.write_yaml("services/example/service.yaml", "name: example\nport: 9876\nhealth: /health\n")

    def tearDown(self):
        self.temp.cleanup()

    def write_yaml(self, rel, content):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def build(self, **kwargs):
        return monitor.build_report(self.root, now=self.now, probe=False, **kwargs)

    def check(self, report, ident):
        return next(c for c in report["checks"] if c["id"] == ident)

    def test_missing_evidence_never_healthy_and_inventory_is_complete(self):
        report = self.build()
        self.assertEqual(report["schema_version"], "solo-empire.monitor.v1")
        self.assertEqual({s["id"] for s in report["systems"]}, {"workflow-platform", "developer-platform"})
        cloud = self.check(report, "cloud:example")
        self.assertEqual((cloud["status"], cloud["coverage"], cloud["observed_at"]), ("unknown", "missing", None))
        self.assertEqual(self.check(report, "scraping")["status"], "unavailable")
        self.assertEqual(report["summary"]["total"], len(report["checks"]))
        self.assertEqual(sum(report["summary"][s] for s in ("healthy", "attention", "unknown", "unavailable")), len(report["checks"]))

    def test_cell_reads_are_aggregate_only_and_failed_jobs_visible(self):
        report = self.build()
        self.assertNotIn("database", {c["id"] for c in report["checks"]})  # no local database to inspect
        self.assertEqual(self.check(report, "cron")["status"], "unknown")  # --offline never calls the cell
        cron = monitor.cron_check(self.now)
        self.assertEqual((cron["status"], cron["metrics"]), ("error", {"total": 1, "failed": 1}))
        with patch.object(monitor, "open_cell_store", side_effect=monitor.ClientConfigError("off")):
            self.assertEqual(monitor.cron_check(self.now)["status"], "unavailable")
        self.assertNotIn("ai", {c["id"] for c in report["checks"]})  # AI telemetry lives in ai-gateway
        for retired in ("backups", "integrations", "security"):
            self.assertNotIn(retired, {c["id"] for c in report["checks"]})

    def test_notifications_come_from_the_service_and_degrade_when_unavailable(self):
        recent = (self.now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        self.notifications.queue({"title": "t", "message": "m", "notification_type": "x", "sent_at": recent})
        failed = self.notifications.queue({"title": "t2", "message": "m", "notification_type": "x", "sent_at": recent})[1]
        self.notifications.record_delivery(failed["id"], "failed", error="boom")
        check = monitor.notifications_check(self.now)
        self.assertEqual((check["status"], check["coverage"]), ("error", "observed"))
        self.assertEqual(check["metrics"], {"total": 2, "failed": 1, "delivered": 0})
        old = self.notifications.queue({"title": "o", "message": "m", "notification_type": "x",
                                        "sent_at": (self.now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")})[1]
        self.assertEqual(monitor.notifications_check(self.now)["metrics"]["total"], 2, old)
        with patch.object(monitor, "open_notifications_store", side_effect=monitor.ClientConfigError("off")):
            self.assertEqual(monitor.notifications_check(self.now)["status"], "unavailable")
        with patch.object(monitor, "open_notifications_store",
                          side_effect=monitor.NotificationsStoreError(503, "unavailable")):
            self.assertEqual(monitor.notifications_check(self.now)["status"], "unavailable")
        offline = self.check(self.build(), "notifications")  # --offline never calls the remote cell
        self.assertEqual((offline["status"], offline["coverage"]), ("unknown", "missing"))

    def test_old_report_keeps_original_time_and_is_stale(self):
        rel = "data/generated/briefings/scraping-collection-health.json"
        self.write_yaml(rel, json.dumps({"contract": "solo-empire.collection-health.v1", "generated_at": "2026-09-30T00:00:00Z", "status": "ok", "summary": {"required_products": 14, "end_to_end_ready": 14, "gap_count": 0, "validation_error_count": 0}}))
        check = self.check(self.build(), "scraping")
        self.assertEqual(check["observed_at"], "2026-09-30T00:00:00+00:00")
        self.assertEqual((check["freshness"], check["status"]), ("stale", "warning"))

    def test_malformed_and_future_report_cannot_be_healthy(self):
        rel = "data/generated/briefings/scraping-collection-health.json"
        for payload in ("[]", "not JSON", json.dumps({"contract": "wrong", "status": "ok"}), json.dumps({"contract": "solo-empire.collection-health.v1", "generated_at": "2099-01-01T00:00:00Z", "status": "ok", "summary": {}})):
            self.write_yaml(rel, payload)
            check = self.check(self.build(), "scraping")
            self.assertNotEqual(check["status"], "healthy")
            self.assertEqual(check["freshness"], "unknown")

    def test_inconsistent_collection_report_cannot_be_healthy(self):
        for summary in ({}, {"required_products": 14, "end_to_end_ready": 15, "gap_count": 0, "validation_error_count": 0}, {"required_products": 14, "end_to_end_ready": 2, "gap_count": 0, "validation_error_count": 0}):
            self.write_yaml("data/generated/briefings/scraping-collection-health.json", json.dumps({"contract": "solo-empire.collection-health.v1", "generated_at": "2026-10-04T23:00:00Z", "status": "ok", "summary": summary}))
            self.assertNotEqual(self.check(self.build(), "scraping")["status"], "healthy")

    def test_probe_does_not_follow_redirects_or_proxies_and_discards_body(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if self.path == "/healthz":
                    self.send_response(302)
                    self.send_header("Location", "http://example.invalid/private")
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ok","private":"must-not-appear"}')
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict("os.environ", {"HTTP_PROXY": "http://example.invalid:9999"}):
                observed = monitor.probe_health(server.server_port, "/health")
                redirected = monitor.probe_health(server.server_port, "/healthz")
            self.assertEqual(observed["status"], "healthy")
            self.assertNotIn("must-not-appear", json.dumps(observed))
            self.assertNotEqual(redirected["status"], "healthy")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_truncated_response_and_slow_stream_are_isolated(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        import time
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.end_headers()
                if self.path == "/health":
                    self.wfile.write(b'{"status":')
                else:
                    for _ in range(10):
                        try:
                            self.wfile.write(b' ')
                            self.wfile.flush()
                            time.sleep(0.2)
                        except (BrokenPipeError, ConnectionResetError):
                            return
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            broken = monitor.probe_health(server.server_port, "/health")
            start = time.monotonic()
            slow = monitor.probe_health(server.server_port, "/healthz")
            elapsed = time.monotonic() - start
            self.assertEqual(broken["status"], "unavailable")
            self.assertEqual(slow["status"], "unavailable")
            self.assertLess(elapsed, 1.5)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_unsafe_or_missing_health_target_is_a_gap(self):
        self.write_yaml("services/example/service.yaml", "port: 9876\nhealth: //example.invalid/private\n")
        with patch.object(monitor, "probe_health", side_effect=AssertionError("must not probe")):
            check = self.check(monitor.build_report(self.root, now=self.now, probe=True), "service:example")
        self.assertEqual((check["status"], check["coverage"]), ("unknown", "missing"))

    def test_data_products_come_from_the_v3_freeze(self):
        # v3 (2026-10-23) retires ai_tools on :8107; the monitor must not probe it.
        self.assertEqual(monitor.PRODUCT_SOURCE, "systems/architecture/data-product-contracts-v3.yaml")
        self.write_yaml(monitor.PRODUCT_SOURCE, "products:\n  - id: crypto\n    port: 8101\n")
        self.write_yaml("systems/architecture/data-product-contracts-v2.yaml",
                        "products:\n  - id: ai_tools\n    port: 8107\n")
        ids = {c["id"] for c in self.build()["checks"]}
        self.assertIn("data-product:crypto", ids)
        self.assertNotIn("data-product:ai_tools", ids)

    def test_registry_failure_is_visible(self):
        self.write_yaml("repository-catalog/registries/system-repositories.yaml", "systems: broken")
        report = monitor.build_report(self.root, now=self.now, probe=False)
        self.assertEqual(self.check(report, "system-registry")["status"], "unavailable")

    def test_malformed_or_duplicate_inventory_is_not_silently_dropped(self):
        self.write_yaml("infra/platform/cloud-providers.yaml", "providers:\n  - id: example\n    name: Example\n  - id: example\n    name: Duplicate\n  - missing: id\n")
        self.write_yaml("systems/architecture/data-product-contracts-v3.yaml", "products:\n  - bad: shape\n")
        report = self.build()
        self.assertEqual(self.check(report, "cloud-registry")["status"], "unavailable")
        self.assertEqual(self.check(report, "product-registry")["status"], "unavailable")
        self.assertEqual(sum(c["id"] == "cloud:example" for c in report["checks"]), 1)

    def test_missing_production_checkout_is_visible_without_reading_contents(self):
        self.write_yaml("catalog/production-tools.yaml", "tools:\n  - id: tool\n    name: Tool\n    owning_repo: projects/missing-tool\n")
        check = self.check(self.build(), "production-tools")
        self.assertEqual(check["status"], "error")
        self.assertEqual(check["metrics"]["missing_checkouts"], 1)

    def test_broken_checkout_symlink_does_not_hide_other_checks(self):
        (self.root / "broken").symlink_to("broken")
        self.write_yaml("catalog/production-tools.yaml", "tools:\n  - id: tool\n    name: Tool\n    owning_repo: broken\n")
        report = self.build()
        self.assertEqual(self.check(report, "production-tools")["status"], "error")


if __name__ == "__main__":
    unittest.main()
