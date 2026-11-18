#!/usr/bin/env python3
"""Offline tests for the post-deploy cell probe."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLATFORM_SCRIPTS = ROOT / "scripts" / "platform"
if str(PLATFORM_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PLATFORM_SCRIPTS))

import cell_probe  # noqa: E402

AUTOMATIONS = (ROOT.parent.parent / "platform-empire"
               / "contracts/automations/v1/automations.json")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.headers.get("Authorization") != "Bearer test-token":
            self.send_response(401)
            self.end_headers()
            return
        if self.path.startswith("/v1/health-metrics"):
            body = json.dumps({"error": "gateway_required"}).encode()
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/v1/journal-entries"):
            self.send_response(403)
            self.end_headers()
            return
        body = json.dumps({"items": [{"id": 1, "secret": "never printed"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class CellProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_read_cell_ok_reports_rows_not_contents(self):
        result = cell_probe.probe("goals", self.url, "test-token")
        self.assertTrue(result["ok"])
        self.assertEqual((result["status"], result["rows"]), (200, 1))
        self.assertNotIn("never printed", json.dumps(result))

    def test_deny_cell_must_answer_403(self):
        self.assertTrue(cell_probe.probe("diary", self.url, "test-token")["ok"])

    def test_gateway_required_is_not_a_deny_pass(self):
        result = cell_probe.probe("health", self.url, "test-token")
        self.assertFalse(result["ok"])
        self.assertEqual((result["status"], result["error"]), (403, "gateway_required"))

    def test_probes_go_through_the_gateway_by_default(self):
        self.assertEqual(cell_probe.gateway_urls(["time"]), {"time": f"{cell_probe.GATEWAY_URL}/v1/time"})

    def test_wrong_token_fails(self):
        result = cell_probe.probe("goals", self.url, "wrong")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 401)

    def test_deny_cell_answering_200_fails(self):
        # accounts is a deny cell; the fake server lets it through, which must count as a failure
        self.assertFalse(cell_probe.probe("accounts", self.url, "test-token")["ok"])

    @unittest.skipUnless(AUTOMATIONS.is_file(), "empire-platform checkout not present")
    def test_probe_table_matches_deploy_verifier_scopes(self):
        registry = json.loads(AUTOMATIONS.read_text(encoding="utf-8"))
        scopes = next(a for a in registry["automations"] if a["id"] == "deploy-verifier")["scopes"]
        scoped_cells = {scope.split(".")[0] for scope in scopes}
        read_cells = {cell for cell, (_, expect) in cell_probe.CELLS.items() if expect == "read"}
        self.assertEqual(read_cells, scoped_cells)


if __name__ == "__main__":
    unittest.main()
