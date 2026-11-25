"""Metadata-only source coverage must retain policy provenance and fail on drift."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))
import source_product_coverage as coverage


def fixture():
    return {
        "schema_version": coverage.SCHEMA_VERSION,
        "expected_product_count": 1,
        "products": [{
            "id": "book-scholarship-data",
            "repository": "bookchaowalit/opportunity-scholarship-data",
            "commit": "a" * 40,
            "manifest_path": "sources.json",
            "expected_source_count": 1,
            "sources": [{
                "id": "official-program",
                "policy_path": "src/book_scholarship_data/source.json",
                "permission_status": "needs-review",
                "permission_basis": "Official public terms require owner review.",
                "reviewed_at": "2026-09-07",
            }],
        }],
    }


class SourceProductCoverageTests(unittest.TestCase):
    def test_pending_permission_is_preserved_without_authorization(self):
        data = fixture()
        self.assertEqual(coverage.validate(data), [])
        report = coverage.build_report(data)
        self.assertFalse(report["registry_authorizes_collection"])
        source = report["products"][0]["sources"][0]
        self.assertEqual(source["permission_status"], "needs-review")
        self.assertEqual(source["reviewed_at"], "2026-09-07")
        self.assertIn("@" + "a" * 40 + ":src/", source["evidence_path"])
        self.assertEqual(report["source_count"], 1)

    def test_missing_policy_fields_fail_closed(self):
        for field in ("permission_status", "permission_basis", "reviewed_at", "policy_path"):
            with self.subTest(field=field):
                data = fixture()
                del data["products"][0]["sources"][0][field]
                self.assertTrue(coverage.validate(data))

    def test_invalid_dates_and_unsafe_policy_paths_are_rejected(self):
        for field, value in (("reviewed_at", "2026-02-30"),
                             ("policy_path", "../.env"),
                             ("policy_path", "/tmp/source.json"),
                             ("policy_path", "src/secrets.json")):
            with self.subTest(field=field, value=value):
                data = fixture()
                data["products"][0]["sources"][0][field] = value
                self.assertTrue(coverage.validate(data))

    def test_missing_product_source_and_duplicate_ids_are_rejected(self):
        for mutation in ("missing_product", "missing_source", "duplicate_product", "duplicate_source"):
            with self.subTest(mutation=mutation):
                data = fixture()
                product = data["products"][0]
                if mutation == "missing_product":
                    data["products"] = []
                elif mutation == "missing_source":
                    product["sources"] = []
                elif mutation == "duplicate_product":
                    data["products"].append(copy.deepcopy(product))
                    data["expected_product_count"] = 2
                else:
                    product["sources"].append(copy.deepcopy(product["sources"][0]))
                    product["expected_source_count"] = 2
                self.assertTrue(coverage.validate(data))

    def test_bad_commit_and_repository_are_rejected(self):
        for field, value in (("commit", "main"), ("repository", "../another-checkout")):
            data = fixture()
            data["products"][0][field] = value
            self.assertTrue(coverage.validate(data))

    def test_source_verification_detects_policy_and_manifest_drift(self):
        data = fixture()
        policy = {"id": "official-program", "permission_status": "needs-review",
                  "permission_evidence": "Official public terms require owner review.",
                  "reviewed_at": "2026-09-07"}
        def read(_repository, _revision, path):
            return {"enabled_sources": ["official-program"]} if path == "sources.json" else policy
        with patch.object(coverage, "_git_json", side_effect=read):
            self.assertEqual(coverage.verify_sources(data, Path("/explicit/tools")), [])
            data["products"][0]["sources"][0]["permission_status"] = "approved"
            self.assertTrue(any("policy drift" in error for error in
                                coverage.verify_sources(data, Path("/explicit/tools"))))
        with patch.object(coverage, "_git_json", return_value={"enabled_sources": []}):
            self.assertTrue(any("manifest drift" in error for error in
                                coverage.verify_sources(fixture(), Path("/explicit/tools"))))

    def test_unavailable_source_evidence_fails_closed(self):
        with patch.object(coverage, "_git_json", side_effect=FileNotFoundError):
            errors = coverage.verify_sources(fixture(), Path("/missing/tools"))
        self.assertTrue(errors)
        self.assertTrue(all("unavailable" in error for error in errors))

    def test_inventory_reference_cannot_escape_owning_repository(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / coverage.COVERAGE_PATH
            path.parent.mkdir(parents=True)
            path.write_text("source_product_inventory:\n  path: ../outside.yaml\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                coverage.load_inventory(root)

    def test_registered_inventory_has_all_fourteen_products_and_policy_evidence(self):
        data = coverage.load_inventory(ROOT)
        self.assertEqual(coverage.validate(data), [])
        report = coverage.build_report(data)
        self.assertEqual(report["product_count"], 14)
        self.assertEqual(report["source_count"], 75)
        self.assertTrue(all(source["evidence_path"] for product in report["products"] for source in product["sources"]))


if __name__ == "__main__":
    unittest.main()
