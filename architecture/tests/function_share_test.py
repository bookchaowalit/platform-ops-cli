#!/usr/bin/env python3
"""Regression tests for the universal function share report."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as REPO_ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))

import function_share  # noqa: E402
import role_coverage_guard as guard  # noqa: E402


class FunctionShareTest(unittest.TestCase):
    def test_function_ids_match_guard(self) -> None:
        self.assertEqual(set(function_share.load_function_ids()), guard.FUNCTION_IDS)

    def test_parse_functions_rejects_unknown_and_dedupes(self) -> None:
        self.assertEqual(
            function_share.parse_functions("build-creation,function:build-creation, growth-distribution"),
            ["build-creation", "growth-distribution"],
        )
        with self.assertRaises(ValueError):
            function_share.parse_functions("enablement-support")

    def test_report_uses_diary_turns_and_flags_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            day_dir = Path(temp_dir) / "2026" / "10" / "07"
            day_dir.mkdir(parents=True)
            (day_dir / function_share.CONVERSATION_FILENAME).write_text(json.dumps([
                {"id": "a", "functions": ["build-creation"]},
                {"id": "b", "functions": ["build-creation", "governance-assurance"]},
                {"id": "c", "functions": ["growth-distribution"]},
                {"id": "d"},
            ]), encoding="utf-8")
            report = function_share.build_report(
                since="2026-10-07", until="2026-10-07", stage_id="build-to-assure", entries_root=Path(temp_dir),
            )
        self.assertEqual(report["basis"], "diary_turns")
        self.assertEqual(report["diary"], {"tagged_turns": 3, "untagged_turns": 1})
        self.assertEqual(report["actual_share"]["build-creation"], 50.0)
        self.assertEqual(report["drift"]["build-creation"], 5.0)
        self.assertIn("growth-distribution", report["over_threshold"])
        self.assertIn("build-to-assure", function_share.render_report(report))

    def test_empty_window_reports_no_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report = function_share.build_report(
                since="2026-10-01", until="2026-10-02", entries_root=Path(temp_dir),
            )
        self.assertTrue(all(value is None for value in report["drift"].values()))
        self.assertEqual(report["stage"]["id"], function_share.load_stage(None)["id"])
        self.assertEqual(report["over_threshold"], [])


if __name__ == "__main__":
    unittest.main()
