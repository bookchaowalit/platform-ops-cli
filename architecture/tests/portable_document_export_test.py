#!/usr/bin/env python3
"""Offline tests for the provider-neutral document catalog export."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))

from portable_document_export import (  # noqa: E402
    EXPORT_SCHEMA,
    FIELDS,
    SOURCE,
    build_rows,
    load_catalog,
    write_export,
)


class PortableDocumentExportTests(unittest.TestCase):
    def test_catalog_rows_are_unique_and_metadata_only(self):
        rows = build_rows(load_catalog(SOURCE))
        self.assertGreater(len(rows), 300)
        self.assertEqual(len(rows), len({row["document_id"] for row in rows}))
        self.assertEqual(tuple(rows[0]), FIELDS)
        self.assertNotIn("body", rows[0])
        self.assertNotIn("content", rows[0])

    def test_export_writes_stable_csv_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "document-catalog.csv"
            manifest_path = Path(directory) / "manifest.json"
            rows, manifest = write_export(SOURCE, output, manifest_path)

            with output.open(newline="", encoding="utf-8-sig") as handle:
                exported = list(csv.DictReader(handle))
            self.assertEqual(len(exported), len(rows))
            self.assertEqual(tuple(exported[0]), FIELDS)
            self.assertEqual(manifest["datasets"][0]["schema_version"], EXPORT_SCHEMA)
            self.assertEqual(manifest["datasets"][0]["row_count"], len(rows))
            self.assertFalse(manifest["datasets"][0]["body_content_included"])
            self.assertEqual(json.loads(manifest_path.read_text())["manifest_schema"], manifest["manifest_schema"])


if __name__ == "__main__":
    unittest.main()
