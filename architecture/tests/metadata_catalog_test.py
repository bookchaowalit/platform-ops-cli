#!/usr/bin/env python3
"""Offline tests for the provider-neutral metadata catalog contract."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))

from metadata_catalog_guard import (  # noqa: E402
    PRODUCT_CONTRACT_PATH,
    check_contract_products,
    validate,
)


def _asset(status: str, product_id: str = "ai_tools", repository: str = "book-ai-tools-data") -> dict:
    return {
        "id": "data-product.ai-tools",
        "status": status,
        "contract_product_id": product_id,
        "repository": repository,
        "schema_version": "ai_tools.v1",
    }


CONTRACTS = {
    "products": [
        {"id": "seo", "schema_version": "seo.v1", "repository": "book-seo-data"},
    ],
    "retired_rows": {"products": ["ai_tools"]},
}


class MetadataCatalogTests(unittest.TestCase):
    def test_repository_metadata_catalog_is_valid(self):
        self.assertEqual(validate(), [])

    def test_guard_reads_the_active_v4_freeze(self):
        self.assertEqual(PRODUCT_CONTRACT_PATH.name, "data-product-contracts-v4.yaml")
        contracts = yaml.safe_load(PRODUCT_CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(contracts["freeze_id"], "data-product-contracts-v4")
        self.assertEqual(contracts["supersedes"], "data-product-contracts-v3")
        ids = [item["id"] for item in contracts["products"]]
        self.assertNotIn("ai_tools", ids)
        self.assertNotIn("flights", ids)
        self.assertEqual(ids, ["crypto", "stocks", "fx", "defi", "seo"])
        ports = [item["port"] for item in contracts["products"]]
        self.assertEqual(ports, [8101, 8102, 8103, 8104, 8106])
        self.assertIn("flights", contracts["retired_rows"]["products"])
        self.assertIn(8105, contracts["retired_rows"]["ports"])
        for consumer in contracts["consumers"]:
            self.assertEqual(consumer["must_match_ports"], [8101, 8102, 8103, 8104, 8106])

    def test_flights_asset_is_deprecated_after_v4(self):
        catalog = yaml.safe_load((ROOT / "systems/ai-data/metadata-catalog/catalog.yaml").read_text(encoding="utf-8"))
        flights = [a for a in catalog["assets"] if a.get("contract_product_id") == "flights"]
        self.assertEqual(len(flights), 1)
        self.assertEqual(flights[0]["status"], "deprecated")

    def test_deprecated_asset_may_point_at_a_retired_row(self):
        errors = check_contract_products({"data-product.ai-tools": _asset("deprecated")}, CONTRACTS)
        self.assertEqual(errors, [])

    def test_live_asset_pointing_at_a_retired_row_fails(self):
        errors = check_contract_products({"data-product.ai-tools": _asset("planned")}, CONTRACTS)
        self.assertEqual(len(errors), 1)
        self.assertIn("retired", errors[0])

    def test_asset_pointing_at_an_unknown_row_still_fails(self):
        errors = check_contract_products(
            {"data-product.ai-tools": _asset("deprecated", product_id="nope")}, CONTRACTS
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("missing from frozen contract", errors[0])


if __name__ == "__main__":
    unittest.main()
