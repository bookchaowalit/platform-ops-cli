#!/usr/bin/env python3
"""Contract tests for the technology capability evidence guard."""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))

from technology_capability_guard import validate_repository  # noqa: E402
from _env import production_tools_catalog  # noqa: E402


EVIDENCE_FILES = (
    "technology-capability-evidence-product-engineering.md",
    "technology-capability-evidence-platform-security.md",
    "technology-capability-evidence-ai-systems.md",
    "technology-capability-evidence-data-engineering.md",
)


def copy_capability_fixture(source_root: Path, destination_root: Path) -> None:
    architecture = destination_root / "systems" / "architecture"
    architecture.mkdir(parents=True)
    for name in ("technology-capability-matrix.md", *EVIDENCE_FILES):
        shutil.copyfile(source_root / "systems" / "architecture" / name, architecture / name)


class TechnologyCapabilityGuardTests(unittest.TestCase):
    def test_repository_has_all_shared_dimensions_and_valid_statuses(self) -> None:
        report = validate_repository(ROOT, today=date(2026, 9, 30), max_age_days=30)

        self.assertEqual(report["errors"], [])
        self.assertGreaterEqual(len(report["warnings"]), 0)

    def test_stale_updated_date_is_a_warning_not_a_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="technology-capability-guard-") as directory:
            fixture_root = Path(directory)
            copy_capability_fixture(ROOT, fixture_root)
            matrix = fixture_root / "systems" / "architecture" / "technology-capability-matrix.md"
            matrix.write_text(
                re.sub(
                    r"(?m)^updated: \d{4}-\d{2}-\d{2}",
                    "updated: 2026-01-01",
                    matrix.read_text(encoding="utf-8"),
                    count=1,
                ),
                encoding="utf-8",
            )
            self.assertIn("updated: 2026-01-01", matrix.read_text(encoding="utf-8"))

            report = validate_repository(
                fixture_root, today=date(2026, 9, 30), max_age_days=30
            )

            self.assertEqual(report["errors"], [])
            self.assertTrue(any("older than 30 days" in warning for warning in report["warnings"]))

    def test_missing_shared_dimension_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="technology-capability-guard-") as directory:
            fixture_root = Path(directory)
            copy_capability_fixture(ROOT, fixture_root)
            ai_register = fixture_root / "systems" / "architecture" / "technology-capability-evidence-ai-systems.md"
            content = ai_register.read_text(encoding="utf-8")
            ai_register.write_text(
                content.replace(
                    "| Documentation, support & deprecation | `covered` for shared boundary |", "", 1
                ),
                encoding="utf-8",
            )

            report = validate_repository(
                fixture_root, today=date(2026, 9, 30), max_age_days=30
            )

            self.assertTrue(
                any("Documentation, support & deprecation" in error for error in report["errors"])
            )

    def test_missing_matrix_track_status_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="technology-capability-guard-") as directory:
            fixture_root = Path(directory)
            copy_capability_fixture(ROOT, fixture_root)
            ai_register = fixture_root / "systems" / "architecture" / "technology-capability-evidence-ai-systems.md"
            content = ai_register.read_text(encoding="utf-8")
            ai_register.write_text(
                content.replace(
                    "| Specialized Product Engineering | `partial` |",
                    "| Specialized Product Engineering | `unknown` |",
                    1,
                ),
                encoding="utf-8",
            )

            report = validate_repository(
                fixture_root, today=date(2026, 9, 30), max_age_days=30
            )

            self.assertTrue(
                any(
                    "Specialized Product Engineering" in error
                    and "no valid status" in error
                    for error in report["errors"]
                )
            )
            self.assertTrue(
                any(
                    "Specialized Product Engineering" in error
                    and "no evidence-register status" in error
                    for error in report["errors"]
                )
            )

    def test_invalid_shared_status_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="technology-capability-guard-") as directory:
            fixture_root = Path(directory)
            copy_capability_fixture(ROOT, fixture_root)
            product_register = fixture_root / "systems" / "architecture" / "technology-capability-evidence-product-engineering.md"
            content = product_register.read_text(encoding="utf-8")
            product_register.write_text(
                content.replace(
                    "| Learning and portfolio evidence | `partial` |",
                    "| Learning and portfolio evidence | `unknown` |",
                    1,
                ),
                encoding="utf-8",
            )

            report = validate_repository(
                fixture_root, today=date(2026, 9, 30), max_age_days=30
            )

            self.assertTrue(
                any("Learning and portfolio evidence" in error for error in report["errors"])
            )

    @unittest.skipUnless(
        production_tools_catalog().is_file(),
        "SKIP: book-production-platform checkout absent (set SOLO_BOOK_PRODUCTION_PLATFORM_ROOT)",
    )
    def test_shipped_tool_mapped_to_deferred_track_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="technology-capability-guard-") as directory:
            fixture_root = Path(directory)
            copy_capability_fixture(ROOT, fixture_root)
            ai_register = fixture_root / "systems" / "architecture" / "technology-capability-evidence-ai-systems.md"
            ai_register.write_text(
                ai_register.read_text(encoding="utf-8").replace(
                    "| Specialized Product Engineering | `partial` |",
                    "| Specialized Product Engineering | `deferred` |",
                    1,
                ),
                encoding="utf-8",
            )

            report = validate_repository(
                fixture_root, today=date(2026, 9, 30), max_age_days=30
            )

            self.assertTrue(any("deferred track" in error for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()
