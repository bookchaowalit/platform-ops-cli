#!/usr/bin/env python3
"""Offline evidence regressions: stale bytes, missing consumers, false freshness."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))
from catalog_freshness import RECORD, validate


class CatalogFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        directory = self.root / "systems/cloud-platform"
        directory.mkdir(parents=True)
        for name in (
            "catalog-freshness-v1.json",
            "free-resource-index.json",
            "free-saas-master-catalog-2026.json",
        ):
            shutil.copyfile(ROOT / "systems/cloud-platform" / name, directory / name)
        shutil.copytree(ROOT / "systems/cloud-platform/sources", directory / "sources")

    def edit_record(self, change):
        path = self.root / RECORD
        record = json.loads(path.read_text())
        change(record)
        path.write_text(json.dumps(record))

    def test_current_evidence_is_valid(self):
        self.assertEqual(validate(self.root), [])

    def test_changed_catalog_bytes_require_revalidation(self):
        path = self.root / "systems/cloud-platform/free-resource-index.json"
        path.write_text(path.read_text() + "\n")
        self.assertIn(
            "free-resource: catalog bytes changed; revalidate evidence",
            validate(self.root),
        )

    def test_missing_consumer_is_not_evidence(self):
        self.edit_record(lambda r: r["catalogs"][0].update(consumers=[]))
        self.assertIn("free-resource: consumer evidence missing", validate(self.root))

    def test_source_version_must_be_pinned(self):
        self.edit_record(lambda r: r["catalogs"][0].pop("source_commit"))
        self.assertIn(
            "free-resource: source commit must be immutable", validate(self.root)
        )

    def test_provider_freshness_cannot_be_inferred_from_local_validation(self):
        self.edit_record(
            lambda r: r["catalogs"][0].update(freshness_status="live-current")
        )
        self.assertIn(
            "free-resource: offline check cannot assert provider freshness",
            validate(self.root),
        )

    def test_catalog_cannot_count_as_scraping_product_or_approval(self):
        self.edit_record(
            lambda r: r["policy"].update(
                required_scraping_products_denominator_contribution=1
            )
        )
        self.assertIn(
            "policy.required_scraping_products_denominator_contribution: selection boundary changed",
            validate(self.root),
        )

    def test_workbook_change_breaks_provenance(self):
        source = (
            self.root
            / "systems/cloud-platform/sources/Free-SaaS-Master-Catalog-Expanded-2026.xlsx"
        )
        source.write_bytes(source.read_bytes() + b"changed")
        self.assertIn(
            "free-saas: workbook bytes differ from projection provenance",
            validate(self.root),
        )


if __name__ == "__main__":
    unittest.main()
