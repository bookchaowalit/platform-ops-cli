#!/usr/bin/env python3
"""CCIE blueprint guard regressions: a dropped topic, part or lab mapping fails."""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))
import ccie_blueprint_guard as guard

SECTION = ROOT / "systems/architecture/ccie-enterprise-infrastructure"


class CcieBlueprintGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.section = self.root / "section"
        shutil.copytree(SECTION, self.section)
        self.registry = self.section / "blueprint-v1.1.yaml"
        self.data = yaml.safe_load(self.registry.read_text())
        for name, value in (("PROJECT_ROOT", self.root), ("SECTION", self.section), ("REGISTRY", self.registry)):
            original = getattr(guard, name)
            setattr(guard, name, value)
            self.addCleanup(setattr, guard, name, original)

    def run_guard(self) -> tuple[int, str]:
        self.registry.write_text(yaml.safe_dump(self.data, allow_unicode=True, sort_keys=False))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = guard.main()
        return code, out.getvalue()

    def test_current_registry_passes(self):
        code, out = self.run_guard()
        self.assertEqual(code, 0, out)
        self.assertIn("Topics: 86", out)

    def test_dropped_topic_fails(self):
        self.data["topics"] = [t for t in self.data["topics"] if t["id"] != "1.6.c"]
        code, out = self.run_guard()
        self.assertEqual(code, 1)
        self.assertIn("part 1.0 must have 36 topics", out)

    def test_wrong_weight_fails(self):
        self.data["parts"][1]["weight"] = 20
        code, out = self.run_guard()
        self.assertEqual(code, 1)
        self.assertIn("part 2.0 weight must be 25", out)

    def test_topic_missing_from_part_document_fails(self):
        doc = self.section / "03-transport-technologies.md"
        doc.write_text(doc.read_text().replace("| 3.1 |", "| x |"))
        code, out = self.run_guard()
        self.assertEqual(code, 1)
        self.assertIn("topic 3.1 missing from the topic map", out)

    def test_unknown_status_fails(self):
        self.data["topics"][0]["status"] = "done"
        code, out = self.run_guard()
        self.assertEqual(code, 1)
        self.assertIn("status 'done' not in", out)

    def test_progress_counts_labbed_topics(self):
        self.data["topics"][0]["status"] = "labbed"
        code, out = self.run_guard()
        self.assertEqual(code, 0, out)
        self.assertIn("1/36 labbed", out)


if __name__ == "__main__":
    unittest.main()
