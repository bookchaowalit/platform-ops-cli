#!/usr/bin/env python3
"""Lifecycle guard regressions: the Measure and Learn stages close the loop after operate."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))
import development_lifecycle_guard as guard


class DevelopmentLifecycleGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "docs/lifecycle").mkdir(parents=True)
        (self.root / "templates").symlink_to(ROOT / "templates")
        self.registry = self.root / "docs/lifecycle/DEVELOPMENT-LIFECYCLE.yaml"
        self.guide = self.root / "docs/lifecycle/DEVELOPMENT-LIFECYCLE.md"
        self.data = yaml.safe_load((ROOT / "docs/lifecycle/DEVELOPMENT-LIFECYCLE.yaml").read_text())
        self.guide.write_text((ROOT / "docs/lifecycle/DEVELOPMENT-LIFECYCLE.md").read_text())
        for name, value in (("PROJECT_ROOT", self.root), ("REGISTRY", self.registry), ("GUIDE", self.guide)):
            original = getattr(guard, name)
            setattr(guard, name, value)
            self.addCleanup(setattr, guard, name, original)

    def run_guard(self) -> tuple[int, str]:
        self.registry.write_text(yaml.safe_dump(self.data, allow_unicode=True, sort_keys=False))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = guard.main()
        return code, out.getvalue()

    def stage(self, stage_id: str) -> dict:
        return next(s for s in self.data["stages"] if s["id"] == stage_id)

    def test_current_registry_passes(self):
        code, out = self.run_guard()
        self.assertEqual(code, 0, out)

    def test_measure_and_learn_follow_operate(self):
        ids = [s["id"] for s in self.data["stages"]]
        at = ids.index("operate-and-improve")
        self.assertEqual(ids[at + 1 : at + 3], ["measure", "learn"])

    def test_loop_stage_without_inputs_fails(self):
        del self.stage("measure")["inputs"]
        code, out = self.run_guard()
        self.assertEqual(code, 1)
        self.assertIn("stage measure: inputs must be a non-empty list", out)

    def test_loop_stage_without_owner_fails(self):
        self.stage("learn")["owner"] = " "
        code, out = self.run_guard()
        self.assertEqual(code, 1)
        self.assertIn("stage learn: missing owner", out)

    def test_loop_stages_out_of_order_fail(self):
        stages = self.data["stages"]
        learn = stages.pop(next(i for i, s in enumerate(stages) if s["id"] == "learn"))
        stages.append(learn)
        code, out = self.run_guard()
        self.assertEqual(code, 1)
        self.assertIn("must follow operate-and-improve", out)


if __name__ == "__main__":
    unittest.main()
