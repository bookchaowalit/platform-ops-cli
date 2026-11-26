"""Locations of sibling repositories for tests; absent siblings make tests skip, never fail.

Resolution mirrors scripts/utils/_env.py: SOLO_<NAME>_ROOT, else ../<repo> next to the
ops-toolkit checkout. ops-toolkit has no path back to the registry hub.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

OPS_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(OPS_ROOT / "infra" / "scripts" / "utils"))
from _env import architecture_root, legacy_env_name  # noqa: E402


def sibling(repo: str) -> Path:
    override = os.environ.get(f"SOLO_{legacy_env_name(repo)}_ROOT")
    return Path(override).expanduser() if override else OPS_ROOT.parent / repo


# Same lookup as the scripts (checkout in either visibility folder, else the store snapshot).
ARCHITECTURE_ROOT = architecture_root()
KNOWLEDGE_ROOT = sibling("knowledge-base")
SKILLS_ROOT = sibling("platform-agent-skills")


def exit_unless_exists(path: Path, label: str) -> None:
    """For script-style tests: print a SKIP line and exit 0 when `path` is missing."""
    if not Path(path).exists():
        print(f"SKIP: {label} not found at {path}")
        raise SystemExit(0)


def requires(path: Path, label: str):
    """unittest decorator: skip the test (class or method) when the sibling path is missing."""
    import unittest

    return unittest.skipUnless(Path(path).exists(), f"{label} not found at {path}")
