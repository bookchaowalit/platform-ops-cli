#!/usr/bin/env python3
"""Focused tests for the privacy-safe personal operating review."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

WORKFLOWS = Path(__file__).resolve().parents[2] / "scripts" / "workflows"
SUPPORT = Path(__file__).resolve().parents[1] / "support"
sys.path.insert(0, str(WORKFLOWS))
sys.path.insert(0, str(SUPPORT))

from sqlite_cell_store import sqlite_cell_opener  # noqa: E402
from sqlite_habits_store import SqliteHabitsStore  # noqa: E402  (also isolates the habits API client switch)

from personal_operating_review import collect_snapshot, markdown_report, select_cycle  # noqa: E402


class PersonalOperatingReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.store = SqliteHabitsStore(self.conn)
        self.operating = sqlite_cell_opener(self.conn)("operating")  # the operating cell, over its legacy tables
        self.operating.create("cycles", {"slug": "test-cycle", "name": "Test Cycle", "status": "active",
                                         "start_date": "2026-08-10", "end_date": "2026-08-16", "focus": "f"})
        self.operating.create("cycles", {"slug": "later", "name": "Later", "status": "planned",
                                         "start_date": "2026-08-17", "end_date": "2026-08-23", "focus": "f"})
        cell_patcher = patch("personal_operating_review.open_cell_store", return_value=self.operating)
        cell_patcher.start()
        self.addCleanup(cell_patcher.stop)
        habit = self.store.create_habit({
            "name": "Journal / Reflection", "slug": "journal-reflection", "frequency": "daily",
            "schedule_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "status": "active", "priority": 1,
        })
        self.store.log_habit(habit["id"], "2026-08-11", {"duration_minutes": 15})
        routine = self.store.create_routine({"name": "Weekly Review", "slug": "weekly-review",
                                             "routine_type": "weekly_review", "status": "active"})
        self.store.log_routine(routine["id"], "2026-08-16")
        patcher = patch("personal_operating_review.open_habits_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.conn.close()

    def test_select_cycle_reads_the_operating_cell(self):
        current = select_cycle(None)
        self.assertEqual((current["slug"], current["status"]), ("test-cycle", "active"))
        self.assertEqual(select_cycle("later")["name"], "Later")
        self.assertIsNone(select_cycle("missing"))

    def test_snapshot_aggregates_without_private_content(self):
        snapshot = collect_snapshot(date(2026, 8, 10),
            date(2026, 8, 16),
            {"slug": "test-cycle", "name": "Test Cycle"},
        )
        self.assertEqual(snapshot["habits"]["completed_occurrences"], 1)
        self.assertEqual(snapshot["routines"]["logged_completions"], 1)
        self.assertFalse(snapshot["privacy"]["raw_journal_content_included"])

        report = markdown_report(snapshot)
        self.assertIn("Automatic diary activity notes", report)
        self.assertIn("Weekly Review", report)



class ReviewDestinationTest(unittest.TestCase):
    """SPEC-001 phase 2: a review goes into the checkout while Git is the source, else the store."""

    def test_a_git_checkout_gets_the_file(self) -> None:
        import tempfile
        import personal_operating_review as review
        root = Path(tempfile.mkdtemp())
        (root / ".git").mkdir()
        target = review.write_review("operations/evidence/R.md", "# R\n", root=root, open_store=lambda **_: None)
        self.assertEqual(target, str(root / "operations/evidence/R.md"))
        self.assertEqual((root / "operations/evidence/R.md").read_text(encoding="utf-8"), "# R\n")

    def test_a_snapshot_root_writes_to_the_store_with_if_version(self) -> None:
        import tempfile
        import personal_operating_review as review
        root = Path(tempfile.mkdtemp())  # no .git: a store snapshot
        calls: list[tuple] = []

        class FakeStore:
            def get(self, path):
                return (b"old", 3)

            def put(self, path, content, *, if_version):
                calls.append((path, content, if_version))
                return 4

        spaces: list[str] = []

        def opener(**kwargs):
            spaces.append(kwargs.get("space"))
            return FakeStore()

        target = review.write_review("operations/evidence/R.md", "# R\n", root=root, open_store=opener)
        self.assertEqual(spaces, ["architecture-docs"])
        self.assertEqual(calls, [("operations/evidence/R.md", b"# R\n", 3)])
        self.assertEqual(target, "store architecture-docs:operations/evidence/R.md (version 4)")
        self.assertFalse((root / "operations").exists())

    def test_a_snapshot_root_without_a_store_fails_instead_of_writing_locally(self) -> None:
        import tempfile
        import personal_operating_review as review
        root = Path(tempfile.mkdtemp())
        with self.assertRaises(SystemExit):
            review.write_review("operations/evidence/R.md", "# R\n", root=root, open_store=lambda **_: None)
        self.assertFalse((root / "operations").exists())

if __name__ == "__main__":
    unittest.main()
