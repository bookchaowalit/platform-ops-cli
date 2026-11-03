#!/usr/bin/env python3
"""Decision table and runners.txt parsing for the CI runner failover watchdog."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "platform"))

import ci_runner_failover as failover  # noqa: E402


class DecideTest(unittest.TestCase):
    def test_local_down_and_railway_stopped_starts_railway(self) -> None:
        self.assertEqual(failover.decide(False, False, False), "start")

    def test_local_down_and_railway_running_keeps_it(self) -> None:
        self.assertEqual(failover.decide(False, True, True), "none")

    def test_local_up_and_railway_idle_stops_it(self) -> None:
        self.assertEqual(failover.decide(True, True, False), "stop")

    def test_local_up_waits_for_railway_jobs(self) -> None:
        self.assertEqual(failover.decide(True, True, True), "wait")

    def test_local_up_and_railway_stopped_is_steady(self) -> None:
        self.assertEqual(failover.decide(True, False, False), "none")


class ReadRunnersTest(unittest.TestCase):
    def test_repo_and_org_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runners.txt"
            path.write_text(
                "# comment\n\nsales-crm=https://github.com/bookchaowalit/sales-crm\n"
                "org-ai=https://github.com/bookchaowalit-ai\n"
            )
            self.assertEqual(
                failover.read_runners(path),
                [("sales-crm", "repos/bookchaowalit/sales-crm"), ("org-ai", "orgs/bookchaowalit-ai")],
            )

    def test_checked_in_list_starts_with_a_repo(self) -> None:
        entries = failover.read_runners()
        self.assertGreaterEqual(len(entries), 1)
        self.assertTrue(entries[0][1].startswith("repos/"))


if __name__ == "__main__":
    unittest.main()
