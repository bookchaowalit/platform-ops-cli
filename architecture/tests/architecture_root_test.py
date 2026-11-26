#!/usr/bin/env python3
"""architecture_root(): how the tools find the document tree (SPEC-001). No documents needed."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import architecture_root  # noqa: E402


NO_SNAP = {"SOLO_ARCHITECTURE_SNAPSHOT": "/nonexistent/architecture-snapshot"}  # this machine may have one


def _ops(base: Path, visibility: str = "public") -> Path:
    tools = base / visibility / "platform-ops-cli" / "architecture"
    (tools / "scripts").mkdir(parents=True)
    return tools


class ArchitectureRootTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())

    def test_env_wins(self) -> None:
        tools = _ops(self.base)
        self.assertEqual(architecture_root({"SOLO_ARCHITECTURE_ROOT": str(self.base / "x")}, tools),
                         (self.base / "x").resolve())

    def test_legacy_layout_is_the_tools_folder(self) -> None:
        tools = _ops(self.base)
        (tools / "docs").mkdir()
        self.assertEqual(architecture_root(NO_SNAP, tools), tools)

    def test_plain_sibling_comes_before_the_private_folder(self) -> None:
        tools = _ops(self.base)
        sibling = self.base / "public" / "architecture-docs"
        sibling.mkdir()
        (self.base / "private" / "architecture-docs").mkdir(parents=True)
        self.assertEqual(architecture_root(NO_SNAP, tools), sibling)

    def test_private_folder_of_the_empire_layout(self) -> None:
        tools = _ops(self.base)
        private = self.base / "private" / "architecture-docs"
        private.mkdir(parents=True)
        self.assertEqual(architecture_root(NO_SNAP, tools), private)

    def test_linked_worktree_resolves_from_its_main_clone(self) -> None:
        main = self.base / "repositories" / "public" / "platform-ops-cli"
        (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
        private = self.base / "repositories" / "private" / "architecture-docs"
        private.mkdir(parents=True)
        worktree = self.base / "worktrees" / "wt"
        (worktree / "architecture" / "scripts").mkdir(parents=True)
        (worktree / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n")
        self.assertEqual(architecture_root(NO_SNAP, worktree / "architecture"), private)


    def test_linked_worktree_with_a_relative_gitdir(self) -> None:
        main = self.base / "repositories" / "public" / "platform-ops-cli"
        (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
        private = self.base / "repositories" / "private" / "architecture-docs"
        private.mkdir(parents=True)
        worktree = self.base / "worktrees" / "wt"
        (worktree / "architecture" / "scripts").mkdir(parents=True)
        (worktree / ".git").write_text("gitdir: ../../repositories/public/platform-ops-cli/.git/worktrees/wt\n")
        self.assertEqual(architecture_root(NO_SNAP, worktree / "architecture"), private.resolve())

    def test_the_snapshot_wins_over_a_frozen_checkout(self) -> None:
        tools = _ops(self.base)
        (self.base / "private" / "architecture-docs").mkdir(parents=True)
        version = self.base / "cache" / "v1"
        version.mkdir(parents=True)
        (version / ".snapshot.json").write_text("{}", encoding="utf-8")
        self.assertEqual(architecture_root({"SOLO_ARCHITECTURE_SNAPSHOT": str(version)}, tools), version.resolve())

    def test_store_snapshot_when_no_checkout_exists(self) -> None:
        tools = _ops(self.base)
        version = self.base / "cache" / "architecture-docs.versions" / "v1"
        version.mkdir(parents=True)
        (version / ".snapshot.json").write_text("{}", encoding="utf-8")
        link = self.base / "cache" / "architecture-docs"
        link.symlink_to(version, target_is_directory=True)
        got = architecture_root({"SOLO_ARCHITECTURE_SNAPSHOT": str(link)}, tools)
        self.assertEqual(got, version.resolve())
        (version / ".snapshot.json").unlink()  # no stamp: not a snapshot
        self.assertEqual(architecture_root({"SOLO_ARCHITECTURE_SNAPSHOT": str(link)}, tools),
                         self.base / "private" / "architecture-docs")

if __name__ == "__main__":
    unittest.main()
