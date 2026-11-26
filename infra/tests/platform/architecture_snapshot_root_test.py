#!/usr/bin/env python3
"""_env.architecture_root(): env -> checkout -> store snapshot (architecture-docs SPEC-001 phase 2)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "utils"))
import _env  # noqa: E402


class ArchitectureSnapshotRootTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        version = self.base / "arch.versions" / "v1"
        version.mkdir(parents=True)
        (version / ".snapshot.json").write_text("{}", encoding="utf-8")
        self.version = version
        self.link = self.base / "arch"
        self.link.symlink_to(version, target_is_directory=True)

    def test_snapshot_is_used_only_without_a_checkout(self) -> None:
        missing = self.base / "no-checkout"
        with patch.object(_env, "sibling_repo_root", return_value=missing), \
                patch.dict(os.environ, {"SOLO_ARCHITECTURE_SNAPSHOT": str(self.link)}):
            os.environ.pop("SOLO_ARCHITECTURE_ROOT", None)
            self.assertEqual(_env.architecture_root(), self.version.resolve())
        checkout = self.base / "checkout"
        checkout.mkdir()
        with patch.object(_env, "sibling_repo_root", return_value=checkout), \
                patch.dict(os.environ, {"SOLO_ARCHITECTURE_SNAPSHOT": str(self.link)}):
            os.environ.pop("SOLO_ARCHITECTURE_ROOT", None)
            self.assertEqual(_env.architecture_root(), checkout)

    def test_explicit_root_wins(self) -> None:
        with patch.dict(os.environ, {"SOLO_ARCHITECTURE_ROOT": str(self.base / "explicit"),
                                     "SOLO_ARCHITECTURE_SNAPSHOT": str(self.link)}):
            self.assertEqual(_env.architecture_root(), self.base / "explicit")


if __name__ == "__main__":
    unittest.main()
