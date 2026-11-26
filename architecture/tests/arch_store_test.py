#!/usr/bin/env python3
"""arch_store.py: the edit flow after the cutover (SPEC-001 D3, AC7/AC9 tooling side). No network:
store_sync, `task check` and codex are replaced by a recording runner; git runs for real."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
import arch_store  # noqa: E402


class Recorder:
    """Runs git for real; answers store_sync / task / codex with canned results."""

    def __init__(self, *, check: int = 0, commit: int = 0, review: str = "no findings", review_rc: int = 0,
                 pulled: dict[str, str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.check, self.commit, self.review, self.review_rc = check, commit, review, review_rc
        self.pulled = pulled or {"docs/a.md": "# A\n", "docs/b.md": "# B\n"}

    def __call__(self, command, *, cwd=None, env=None, capture=False, stdin=None):
        command = list(command)
        self.calls.append(command)
        if command[0] == "git":
            return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=capture, check=False)
        if command[0] == "uv":
            args = command[command.index("scripts/store_sync.py") + 1:]
            if args[0] == "pull":
                out = Path(args[args.index("--out") + 1])
                for path, text in self.pulled.items():
                    (out / path).parent.mkdir(parents=True, exist_ok=True)
                    (out / path).write_text(text, encoding="utf-8")
                Path(args[args.index("--manifest") + 1]).write_text("{}", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, self.commit if args[0] == "commit" else 0, "", "")
        if command[0] == "task":
            self.check_env = env
            return subprocess.CompletedProcess(command, self.check, "", "")
        if command[0] == "codex":
            return subprocess.CompletedProcess(command, self.review_rc, self.review, "")
        raise AssertionError(command)

    def ran(self, name: str) -> list[list[str]]:
        if name in ("pull", "commit", "revert"):
            return [c for c in self.calls if c[0] == "uv" and name in c]
        return [c for c in self.calls if c[0] == name]


class ArchStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.env = {"SOLO_KNOWLEDGE_API_ROOT": str(self.base / "kapi"),
                    "SOLO_ARCHITECTURE_SNAPSHOT": str(self.base / "no-snapshot")}

    def working_copy(self, run: Recorder) -> Path:
        copy = self.base / "copy"
        self.assertEqual(arch_store.pull(copy, run=run, environ=self.env), 0)
        return copy

    def test_pull_makes_a_git_working_copy_with_a_manifest(self) -> None:
        run = Recorder()
        copy = self.working_copy(run)
        self.assertTrue((copy / arch_store.MANIFEST).is_file())
        self.assertEqual(arch_store.changed_paths(copy, run=run), [])
        pull = run.ran("pull")[0]
        self.assertIn("architecture-docs", pull)
        self.assertNotIn("--reuse", pull)  # no snapshot configured

    def test_pull_refuses_a_folder_with_files(self) -> None:
        busy = self.base / "busy"
        busy.mkdir()
        (busy / "x").write_text("x")
        self.assertEqual(arch_store.pull(busy, run=Recorder(), environ=self.env), 2)

    def test_pull_reuses_the_snapshot_when_there_is_one(self) -> None:
        snap = self.base / "snap"
        snap.mkdir()
        (snap / ".snapshot.json").write_text("{}")
        run = Recorder()
        env = {**self.env, "SOLO_ARCHITECTURE_SNAPSHOT": str(snap)}
        self.assertEqual(arch_store.pull(self.base / "copy", run=run, environ=env), 0)
        self.assertIn(str(snap.resolve()), run.ran("pull")[0])

    def test_push_with_nothing_changed_writes_nothing(self) -> None:
        run = Recorder()
        copy = self.working_copy(run)
        self.assertEqual(arch_store.push(copy, run=run, environ=self.env), 0)
        self.assertEqual(run.ran("commit"), [])
        self.assertEqual(run.ran("task"), [])

    def test_a_failing_check_writes_nothing(self) -> None:
        run = Recorder(check=1)
        copy = self.working_copy(run)
        (copy / "docs" / "a.md").write_text("# A\n[broken](missing.md)\n")
        self.assertEqual(arch_store.push(copy, run=run, environ=self.env), 1)
        self.assertEqual(run.ran("commit"), [])
        self.assertEqual(run.check_env["SOLO_ARCHITECTURE_ROOT"], str(copy.resolve()))

    def test_a_clean_push_commits_and_moves_the_base(self) -> None:
        run = Recorder()
        copy = self.working_copy(run)
        (copy / "docs" / "a.md").write_text("# A2\n")
        (copy / "docs" / "b.md").unlink()
        self.assertEqual(arch_store.changed_paths(copy, run=run), ["docs/a.md", "docs/b.md"])
        self.assertEqual(arch_store.push(copy, run=run, environ=self.env), 0)
        commit = run.ran("commit")[0]
        self.assertIn(str(copy.resolve() / arch_store.MANIFEST), commit)
        self.assertEqual(run.ran("codex"), [])  # 2 documents, no ADR: no review
        self.assertEqual(arch_store.changed_paths(copy, run=run), [])  # base moved

    def test_a_refused_store_commit_keeps_the_base(self) -> None:
        run = Recorder(commit=1)  # e.g. a conflict: someone else changed the document
        copy = self.working_copy(run)
        (copy / "docs" / "a.md").write_text("# mine\n")
        self.assertEqual(arch_store.push(copy, run=run, environ=self.env), 1)
        self.assertEqual(arch_store.changed_paths(copy, run=run), ["docs/a.md"])

    def test_review_gate_for_adrs_and_large_edits(self) -> None:
        self.assertTrue(arch_store.needs_review(["docs/architecture/decisions/ADR-0001-x.md"]))
        self.assertTrue(arch_store.needs_review([f"docs/{i}.md" for i in range(6)]))
        self.assertFalse(arch_store.needs_review([f"docs/{i}.md" for i in range(5)]))

    def test_a_blocking_review_finding_writes_nothing(self) -> None:
        run = Recorder(review="[P1] docs/architecture/decisions/ADR-0099.md: reverses ADR-0007 silently")
        copy = self.working_copy(run)
        adr = copy / "docs" / "architecture" / "decisions" / "ADR-0099.md"
        adr.parent.mkdir(parents=True)
        adr.write_text("# ADR\n")
        self.assertEqual(arch_store.push(copy, run=run, environ=self.env), 1)
        self.assertEqual(run.ran("task"), [])
        self.assertEqual(run.ran("commit"), [])
        self.assertIn("[P1]", (copy / arch_store.REVIEW_FILE).read_text())

    def test_a_nit_passes_and_reviewed_skips_codex(self) -> None:
        run = Recorder(review="[P3] docs/x.md: wording")
        copy = self.working_copy(run)
        for i in range(6):
            (copy / "docs" / f"n{i}.md").write_text("n\n")
        self.assertEqual(arch_store.push(copy, run=run, environ=self.env), 0)
        self.assertEqual(len(run.ran("codex")), 1)
        run2 = Recorder()
        copy2 = self.base / "copy2"
        self.assertEqual(arch_store.pull(copy2, run=run2, environ=self.env), 0)
        for i in range(6):
            (copy2 / "docs" / f"n{i}.md").write_text("n\n")
        self.assertEqual(arch_store.push(copy2, reviewed=True, run=run2, environ=self.env), 0)
        self.assertEqual(run2.ran("codex"), [])

    def test_a_review_that_does_not_run_stops_the_push(self) -> None:
        run = Recorder(review_rc=1)
        copy = self.working_copy(run)
        for i in range(6):
            (copy / "docs" / f"n{i}.md").write_text("n\n")
        self.assertEqual(arch_store.push(copy, run=run, environ=self.env), 2)
        self.assertEqual(run.ran("commit"), [])

    def test_push_needs_a_working_copy(self) -> None:
        plain = self.base / "plain"
        plain.mkdir()
        self.assertEqual(arch_store.push(plain, run=Recorder(), environ=self.env), 2)

    def test_task_names_are_safe(self) -> None:
        self.assertTrue(str(arch_store.default_dir("fix-links", {"SOLO_WORKTREES_ROOT": "/w"})).endswith(
            "/w/arch-docs-fix-links"))
        with self.assertRaises(SystemExit):
            arch_store.default_dir("../x", {})


if __name__ == "__main__":
    unittest.main()
