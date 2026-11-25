#!/usr/bin/env python3
"""Tests for the CSV-first reusable matrix template contract."""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as REPO_ROOT  # noqa: E402  (the document tree, SPEC-001)
GUARD = TOOLS / "scripts" / "matrix_template_guard.py"
CSV_TEMPLATE = REPO_ROOT / "templates" / "matrices" / "matrix-template.csv"


def test_guard_passes() -> bool:
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        return False
    return True


def test_csv_is_blank_and_has_stable_identity_columns() -> bool:
    with CSV_TEMPLATE.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if len(rows) != 1:
        print("matrix-template.csv must contain a header only")
        return False
    header = rows[0]
    expected_prefix = ["matrix_id", "row_id", "matrix_type"]
    if header[:3] != expected_prefix:
        print(f"unexpected identity prefix: {header[:3]}")
        return False
    return True


def main() -> int:
    tests = [test_guard_passes, test_csv_is_blank_and_has_stable_identity_columns]
    passed = failed = 0
    for test in tests:
        print(f"[{'RUN':>4}] {test.__name__}")
        try:
            ok = test()
        except Exception as exc:  # pragma: no cover - test harness output
            print(f"  error: {exc}")
            ok = False
        if ok:
            passed += 1
            print("  PASS")
        else:
            failed += 1
            print("  FAIL")
    print(f"Results: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
