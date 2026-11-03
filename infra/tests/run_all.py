#!/usr/bin/env python3
"""Run every infra/tests/**/*_test.py script and summarise the results.

Usage: python3 infra/tests/run_all.py [--jobs N] [--timeout S] [--only SUBSTR] [--list]

Each test runs in its own interpreter with the production Tasks API disabled
(EMPIRE_CLIENT_CONFIG=/nonexistent). Siblings that are absent make tests skip
themselves; this runner only reports exit codes. Exit 0 when nothing failed.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

INFRA = Path(__file__).resolve().parents[1]
TESTS = INFRA / "tests"


def run(path: Path, timeout: int) -> tuple[Path, int, str]:
    env = {
        **os.environ,
        "EMPIRE_CLIENT_CONFIG": "/nonexistent/empire-platform/client.env",
        "SOLO_EMPIRE_TASKS_BACKEND": "",
        "EMPIRE_TASKS_API": "",
        # Never reach the hosted knowledge store from tests, even with EMPIRE_KNOWLEDGE_API exported.
        "SOLO_DIARY_STORE": "off",
        "EMPIRE_KNOWLEDGE_API": "",
        "PYTHONUTF8": "1",
    }
    try:
        proc = subprocess.run(
            [sys.executable, str(path)], cwd=INFRA.parent, env=env,
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return path, proc.returncode, out[-1500:]
    except subprocess.TimeoutExpired:
        return path, 124, f"timed out after {timeout}s"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--only", default="")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    tests = sorted(p for p in TESTS.rglob("*_test.py") if args.only in p.as_posix())
    if args.list:
        for test in tests:
            print(test.relative_to(INFRA.parent))
        return 0
    failed = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for path, code, out in pool.map(lambda p: run(p, args.timeout), tests):
            rel = path.relative_to(INFRA.parent)
            print(f"{'ok  ' if code == 0 else 'FAIL'} {rel}")
            if code != 0:
                failed.append(rel)
                print("    " + out.strip().replace("\n", "\n    ")[-800:])
    print(f"\n{len(tests) - len(failed)}/{len(tests)} test scripts passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
