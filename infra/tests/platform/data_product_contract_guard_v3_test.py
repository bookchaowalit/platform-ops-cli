"""The contract guard checks data-product-contracts-v3, which retires ai_tools (:8107).

Owner approved the retirement on 2026-10-23 (architecture-docs 89a679c) so
bookchaowalit/tech-ai-tools-data can be archived. Self-contained: the freeze is
written to a temp file, so the test runs without an architecture-docs checkout.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

import data_product_contract_guard as guard  # noqa: E402

V3_IDS = ["crypto", "stocks", "fx", "defi", "flights", "seo"]
ROWS = [
    ("crypto", 8101, "crypto.v1", "book-crypto-data", "book_crypto"),
    ("stocks", 8102, "stock.v1", "book-stock-data", "book_stock"),
    ("fx", 8103, "fx.v1", "book-fx-data", "book_fx"),
    ("defi", 8104, "defi.v1", "book-defi-data", "book_defi"),
    ("flights", 8105, "flight.v1", "book-flight-data", "book_flight"),
    ("seo", 8106, "seo.v1", "book-seo-data", "book_seo"),
]
HEADER = """freeze_id: data-product-contracts-v3
status: frozen
retired_rows:
  products: [ai_tools]
  ports: [8107]
endpoints:
  refresh:
    method: POST
    path: /v1/refresh
    default_status: 403
envelope:
  required_keys:
    - schema_version
    - source
    - retrieved_at
    - data_status
    - items
    - next_cursor
policy_defaults:
  FREE_ONLY: true
  ALLOW_PAID_PROVIDERS: false
  ALLOW_EXTERNAL_WRITES: false
  ALLOW_REFRESH: false
products:
"""


def freeze_text(rows) -> str:
    body = "".join(
        f"  - id: {pid}\n    port: {port}\n    schema_version: {schema}\n"
        f"    repository: {repo}\n    package: {package}\n    role: domain_source\n"
        for pid, port, schema, repo, package in rows
    )
    return HEADER + body + "consumers:\n  - id: x\n"


class ContractGuardV3Tests(unittest.TestCase):
    def check(self, text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "data-product-contracts-v3.yaml"
            path.write_text(text, encoding="utf-8")
            with patch.object(guard, "FREEZE_PATH", path):
                freeze = guard.parse_freeze_yaml()
                errors: list[str] = []
                guard.check_freeze_file(errors, freeze)
        return errors

    def test_guard_targets_v3(self):
        self.assertEqual(guard.FREEZE_PATH.name, "data-product-contracts-v3.yaml")
        self.assertEqual([p["id"] for p in guard.EXPECTED_PRODUCTS], V3_IDS)
        for spec in guard.CONSUMER_SPECS:
            self.assertEqual(spec["ports"], [8101, 8102, 8103, 8104, 8105, 8106])

    def test_six_product_v3_freeze_passes(self):
        self.assertEqual(self.check(freeze_text(ROWS)), [])

    def test_retired_ai_tools_row_fails(self):
        rows = ROWS + [("ai_tools", 8107, "ai_tools.v1", "book-ai-tools-data", "book_ai_tools")]
        errors = self.check(freeze_text(rows))
        self.assertTrue(any("8107" in error for error in errors), errors)

    def test_v2_freeze_id_is_rejected(self):
        errors = self.check(freeze_text(ROWS).replace("data-product-contracts-v3", "data-product-contracts-v2", 1))
        self.assertTrue(any("freeze_id" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
