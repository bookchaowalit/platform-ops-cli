#!/usr/bin/env python3
"""Exercise the owner CLI's read-only delivery route without live service configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[3]
TSX = ROOT / "infra/node_modules/.bin/tsx"
MCP = Path(os.environ.get("SOLO_MCP_ROOT", ROOT.parent / "platform-mcp-aggregator"))
ARCH = Path(os.environ.get("SOLO_ARCHITECTURE_ROOT", ROOT.parent / "architecture-docs"))
# The exporter moved out of the document tree into this repository (architecture-docs SPEC-001);
# the CLI passes it to the MCP adapter itself, so the test does not.
TOOLS = ROOT / "architecture"


class DeliveryCliContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        for path in (TSX, MCP / "src/delivery-context.ts", TOOLS / "scripts/delivery_context.py",
                     ARCH / "docs/registries/ROLE-CATALOG.yaml"):
            if not path.is_file():
                raise unittest.SkipTest("delivery CLI prerequisites or owning sibling checkout unavailable")

    def call(self, *args: str) -> tuple[int, dict, str]:
        env = os.environ.copy()
        env.update(EMPIRE_CLIENT_CONFIG="/nonexistent/delivery-contract.env",
                   EMPIRE_TASKS_API="", SOLO_EMPIRE_TASKS_BACKEND="",
                   SOLO_MCP_ROOT=str(MCP), SOLO_ARCHITECTURE_ROOT=str(ARCH))
        env.pop("SOLO_ARCHITECTURE_TOOLS", None)
        result = subprocess.run([str(TSX), "infra/cli.ts", "delivery", *args],
                                cwd=ROOT, env=env, capture_output=True, text=True,
                                timeout=45, check=False)
        payload = json.loads(result.stdout)
        self.assertEqual("solo-empire.delivery-context.v1", payload["contract"])
        self.assertFalse(payload["capabilities"]["runtime_integrated"])
        self.assertTrue(all(value == "not_verified" for value in payload["authority"].values()))
        return result.returncode, payload, result.stderr

    def test_overview_exposes_all_roles_without_live_tasks(self) -> None:
        code, payload, _ = self.call("overview", "--json")
        self.assertEqual(0, code)
        self.assertEqual("ready", payload["status"])
        roles = payload["data"]["roles"]
        self.assertEqual(50, len(roles))
        self.assertEqual(50, len({row["id"] for row in roles}))
        self.assertEqual(10, len(payload["sources"]))
        for source in payload["sources"]:
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")

    def test_family_role_retains_documents_and_specialist_limits(self) -> None:
        code, payload, _ = self.call("role", "--role", "people-operations-talent-acquisition", "--json")
        self.assertEqual(0, code)
        role = payload["data"]
        self.assertEqual("people-operations-talent-acquisition", role["id"])
        self.assertEqual("family-process-only", role["specialist_refinement_status"])
        self.assertEqual([], role["owner_step_ids"])
        self.assertEqual([], role["reviewer_step_ids"])
        self.assertTrue(role["groups"])
        self.assertTrue(role["position_templates"])
        self.assertTrue(role["limitations"])

    def test_refinement_is_specific_to_requested_step(self) -> None:
        code, payload, _ = self.call("refinement", "--step", "BA03", "--json")
        self.assertEqual(0, code)
        self.assertEqual("BA03", payload["filters"]["step"])
        rendered = json.dumps(payload["data"])
        self.assertIn("BA03.Q01", rendered)
        self.assertIn("BA03.C01", rendered)
        self.assertNotIn("PD01.Q01", rendered)

    def test_invalid_args_fail_without_echoing_input(self) -> None:
        marker = "unrecognized-private-input-marker"
        code, payload, stderr = self.call("overview", "--" + marker, "--json")
        self.assertEqual(2, code)
        self.assertEqual("invalid", payload["status"])
        self.assertFalse(payload["capabilities"]["definition_available"])
        self.assertNotIn(marker, json.dumps(payload) + stderr)
        self.assertNotIn("Traceback", stderr)


if __name__ == "__main__":
    unittest.main()
