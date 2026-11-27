#!/usr/bin/env python3
"""Tests for the cross-repository As-Is / To-Be report."""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "docpack"))

import as_is_to_be_report as report  # noqa: E402

TODAY = dt.date(2026, 11, 26)

STANDARD = """# 01 — Business process (As-Is / To-Be)

Service ตัวอย่าง

Last reviewed: 2026-11-20 · commit `abc1234`

## As-Is (จากโค้ดวันนี้)

```mermaid
flowchart LR
  A["<b>today</b>"] --> B
```

### As-Is: sub process

```mermaid
flowchart TD
  X --> Y
```

## To-Be (จาก spec)

```mermaid
flowchart LR
  A --> C
```

## Gap

| ID | As-Is → To-Be | Status | Owner | Evidence |
|---|---|---|---|---|
| G1 | a → b | done | claude | `specs/001/spec.md` |
| G2 | b → c | Done | claude |  |
| G3 | c → d | todo | owner | x |
| G4 | d → e | decide | owner | owner item |
"""

LEGACY = """# 01 — Business process (As-Is / To-Be)

old style

## As-Is

```mermaid
flowchart LR
  A --> B
```

## To-Be

ไม่มี To-Be ที่เขียนไว้
"""


class ParseTest(unittest.TestCase):
    def test_standard_file(self):
        doc = report.parse_doc(STANDARD, TODAY)
        self.assertTrue(doc["standard"], doc["problems"])
        self.assertEqual(doc["reviewed_on"], "2026-11-20")
        self.assertEqual(doc["reviewed_commit"], "abc1234")
        self.assertEqual(doc["summary"], "Service ตัวอย่าง")
        self.assertIn("<b>today</b>", doc["as_is"])
        self.assertNotIn("X --> Y", doc["as_is"])  # first block only
        self.assertIn("A --> C", doc["to_be"])

    def test_gap_status_rules(self):
        gaps = {g["id"]: g["status"] for g in report.parse_doc(STANDARD.replace("| todo | owner | x |", "| waiting | owner | x |"), TODAY)["gaps"]}
        self.assertEqual(gaps, {"G1": "done", "G2": "todo", "G3": "todo", "G4": "decide"})

    def test_legacy_file_is_not_standard(self):
        doc = report.parse_doc(LEGACY, TODAY)
        self.assertFalse(doc["standard"])
        self.assertIn("no Gap table", doc["problems"])
        self.assertIn("no Last reviewed line", doc["problems"])
        self.assertIsNone(doc["to_be"])

    def test_escaped_pipe_keeps_columns(self):
        text = STANDARD.replace("| G4 | d → e |", "| G4 | d \\| x → e |")
        gap = report.parse_doc(text, TODAY)["gaps"][3]
        self.assertEqual((gap["change"], gap["status"], gap["owner"]), ("d | x → e", "decide", "owner"))

    def test_invalid_date_is_a_problem_not_a_crash(self):
        doc = report.parse_doc(STANDARD.replace("2026-11-20", "2026-13-45"), TODAY)
        self.assertFalse(doc["standard"])
        self.assertIn("invalid Last reviewed date 2026-13-45", doc["problems"])

    def test_text_to_be_is_kept(self):
        text = STANDARD.replace("```mermaid\nflowchart LR\n  A --> C\n```", "ไม่มี To-Be ที่เขียนไว้ รอ owner")
        doc = report.parse_doc(text, TODAY)
        self.assertIsNone(doc["to_be"])
        self.assertEqual(doc["to_be_note"], "ไม่มี To-Be ที่เขียนไว้ รอ owner")
        self.assertTrue(doc["standard"], doc["problems"])

    def test_text_to_be_keeps_its_list(self):
        text = STANDARD.replace("```mermaid\nflowchart LR\n  A --> C\n```", "เป้าหมายมีเพียง:\n\n- ข้อหนึ่ง\n- ข้อสอง")
        self.assertEqual(report.parse_doc(text, TODAY)["to_be_note"], "เป้าหมายมีเพียง:\n\n- ข้อหนึ่ง\n- ข้อสอง")

    def test_review_line_needs_commit_and_no_unverified_marker(self):
        no_commit = report.parse_doc(STANDARD.replace(" · commit `abc1234`", ""), TODAY)
        self.assertIn("Last reviewed line has no commit", no_commit["problems"])
        unverified = report.parse_doc(STANDARD.replace("commit `abc1234`", "commit `abc1234` [ต้องยืนยัน]"), TODAY)
        self.assertFalse(unverified["standard"])

    def test_missing_to_be_and_empty_gap(self):
        text = STANDARD.split("## To-Be")[0] + "## Gap\n\n| ID | As-Is → To-Be | Status | Owner | Evidence |\n|---|---|---|---|---|\n"
        doc = report.parse_doc(text, TODAY)
        self.assertIn("no To-Be section", doc["problems"])
        self.assertIn("Gap table has no rows", doc["problems"])

    def test_gap_rows_only_from_the_contract_table(self):
        other = "\n| G9 | not a gap | done | x | y |\n"
        text = STANDARD.replace("## Gap\n", "## Gap\n" + other + "\n", 1)
        ids = [g["id"] for g in report.parse_doc(text, TODAY)["gaps"]]
        self.assertEqual(ids, ["G1", "G2", "G3", "G4"])
        no_header = STANDARD.replace("| ID | As-Is → To-Be | Status | Owner | Evidence |\n|---|---|---|---|---|\n", "")
        self.assertIn("no Gap table", report.parse_doc(no_header, TODAY)["problems"])

    def test_g0_marks_no_gap(self):
        table = "| ID | As-Is → To-Be | Status | Owner | Evidence |\n|---|---|---|---|---|\n"
        text = STANDARD.split("## Gap")[0] + "## Gap\n\n" + table + "| G0 | ไม่มี gap | done | owner | spec |\n"
        doc = report.parse_doc(text, TODAY)
        self.assertTrue(doc["standard"], doc["problems"])
        self.assertEqual(sum(doc["gap_counts"].values()), 0)

    def test_no_to_be_needs_a_decide_gap(self):
        text = STANDARD.replace("```mermaid\nflowchart LR\n  A --> C\n```", "ไม่มี To-Be ที่เขียนไว้").replace("| decide |", "| todo |")
        self.assertIn("no To-Be and no decide gap asking the owner for one", report.parse_doc(text, TODAY)["problems"])

    def test_malformed_gap_row_is_reported(self):
        text = STANDARD.replace("| G4 | d → e | decide | owner | owner item |", "| G4 | d → e | decide | owner |")
        doc = report.parse_doc(text, TODAY)
        self.assertIn("Gap table has rows that do not match the five columns", doc["problems"])
        self.assertEqual([g["id"] for g in doc["gaps"]], ["G1", "G2", "G3"])

    def test_single_checkout_as_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "solo"
            (repo / ".git").mkdir(parents=True)
            (repo / "docs" / "diagrams").mkdir(parents=True)
            (repo / "docs" / "diagrams" / "01-business-process.md").write_text(STANDARD, encoding="utf-8")
            data = report.collect(repo, TODAY)
            self.assertEqual((data["repositories"], data["with_doc"]), (1, 1))

    def test_short_separator_and_id_problems(self):
        short = STANDARD.replace("|---|---|---|---|---|", "| - | - | - | - | - |")
        self.assertEqual(len(report.parse_doc(short, TODAY)["gaps"]), 4)
        bad_id = report.parse_doc(STANDARD.replace("| G4 |", "| GAP4 |"), TODAY)
        self.assertIn("Gap table has rows whose ID is not G<number>", bad_id["problems"])
        both = report.parse_doc(STANDARD.replace("| G4 |", "| GAP4 |").replace("Last reviewed: 2026-11-20 · commit `abc1234`\n", ""), TODAY)
        self.assertIn("no Last reviewed line", both["problems"])

    def test_hub_checkout_root_still_finds_clones(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = Path(tmp) / "hub"
            (hub / ".git").mkdir(parents=True)
            (hub / "repositories" / "github" / "owner" / "public" / "alpha" / ".git").mkdir(parents=True)
            data = report.collect(hub, TODAY)
            self.assertEqual(data["missing"], ["repositories/alpha"])  # no remote: falls back to the folder layout

    def test_unknown_status_and_duplicate_ids_are_problems(self):
        typo = report.parse_doc(STANDARD.replace("| todo | owner | x |", "| waiting | owner | x |"), TODAY)
        self.assertIn("Gap status not one of done|doing|todo|blocked|decide: waiting", typo["problems"])
        dup = report.parse_doc(STANDARD.replace("| G4 |", "| G3 |"), TODAY)
        self.assertIn("duplicate Gap IDs: G3", dup["problems"])

    def test_written_text_to_be_needs_no_decide_gap(self):
        text = STANDARD.replace("```mermaid\nflowchart LR\n  A --> C\n```", "เป้าหมายตาม PRODUCT.md: ย้ายไป shared mode").replace("| decide |", "| todo |")
        self.assertNotIn("no To-Be and no decide gap asking the owner for one", report.parse_doc(text, TODAY)["problems"])

    def test_rows_without_outer_pipes_dupes_and_future_date(self):
        bare = STANDARD.replace("| G4 | d → e | decide | owner | owner item |", "G4 | d → e | decide | owner | owner item")
        self.assertEqual([g["id"] for g in report.parse_doc(bare, TODAY)["gaps"]], ["G1", "G2", "G3", "G4"])
        dup = report.parse_doc(STANDARD.replace("| G2 |", "| G1 |"), TODAY)
        self.assertEqual(sum(dup["gap_counts"].values()), 2)  # both G1 rows left out of the totals
        future = report.parse_doc(STANDARD.replace("2026-11-20", "2027-11-20"), TODAY)
        self.assertIn("Last reviewed date 2027-11-20 is in the future", future["problems"])

    def test_bold_no_target_phrase_still_needs_a_decide_gap(self):
        for marked in ("**ไม่มี To-Be ที่เขียนไว้** รอ owner", "_ไม่มี To-Be ที่เขียนไว้_", "> ไม่มี To-Be ที่เขียนไว้",
                       "1. ไม่มี To-Be ที่เขียนไว้", "+ ไม่มี To-Be ที่เขียนไว้", "2) **ไม่มี To-Be ที่เขียนไว้**"):
            text = STANDARD.replace("```mermaid\nflowchart LR\n  A --> C\n```", marked).replace("| decide |", "| todo |")
            self.assertIn("no To-Be and no decide gap asking the owner for one",
                          report.parse_doc(text, TODAY)["problems"], marked)

    def test_stale_g0_doc_still_says_no_gap(self):
        table = STANDARD.split("## Gap")[1].split("| G1 |")[0]
        text = STANDARD.split("## Gap")[0] + "## Gap" + table + "| G0 | ไม่มี gap | done | owner | spec |\n"
        doc = report.parse_doc(text.replace("2026-11-20", "2026-09-01"), TODAY)
        self.assertFalse(doc["standard"])  # stale still fails the standard
        page = report.render_system({"repo": "o/r", "path": "r", **doc})
        self.assertIn("ไม่มี Gap (As-Is ตรงกับ To-Be)", page)
        self.assertNotIn("แก้ตารางก่อน", page)

    def test_valid_g0_says_no_gap_when_only_the_review_line_is_wrong(self):
        table = STANDARD.split("## Gap")[1].split("| G1 |")[0]
        text = STANDARD.split("## Gap")[0] + "## Gap" + table + "| G0 | ไม่มี gap | done | owner | spec |\n"
        doc = report.parse_doc(text.replace(" · commit `abc1234`", ""), TODAY)
        self.assertIn("Last reviewed line has no commit", doc["problems"])
        self.assertTrue(doc["table_ok"])
        self.assertIn("ไม่มี Gap (As-Is ตรงกับ To-Be)", report.render_system({"repo": "o/r", "path": "r", **doc}))

    def test_only_the_contract_phrase_means_no_target(self):
        mixed = STANDARD.replace("```mermaid\nflowchart LR\n  A --> C\n```", "ยังไม่มี To-Be diagram แต่เป้าหมายคือย้ายไป shared mode").replace("| decide |", "| todo |")
        self.assertNotIn("no To-Be and no decide gap asking the owner for one", report.parse_doc(mixed, TODAY)["problems"])

    def test_phrase_inside_a_written_target_is_not_no_target(self):
        part = STANDARD.replace("```mermaid\nflowchart LR\n  A --> C\n```", "เป้าหมาย: ย้ายไป shared mode; ส่วน billing ไม่มี To-Be ที่เขียนไว้").replace("| decide |", "| todo |")
        self.assertNotIn("no To-Be and no decide gap asking the owner for one", report.parse_doc(part, TODAY)["problems"])

    def test_g0_must_be_the_only_row(self):
        mixed = STANDARD.replace("| G1 |", "| G0 |")
        self.assertIn("G0 (no gap) must be the only row", report.parse_doc(mixed, TODAY)["problems"])

    def test_bar_says_uncountable_when_all_rows_are_dropped(self):
        rows = [{"id": "G1"}, {"id": "G1"}]
        zero = {s: 0 for s in report.STATUSES}
        self.assertIn("ยังนับ Gap ไม่ได้", report.bar(zero, rows, False))
        self.assertIn("ไม่มี Gap", report.bar(zero, [{"id": "G0"}], True))
        self.assertIn("ยังไม่มีตาราง Gap", report.bar(zero, [], False))

    def test_stale_file(self):
        doc = report.parse_doc(STANDARD.replace("2026-11-20", "2026-09-01"), TODAY)
        self.assertFalse(doc["standard"])
        self.assertTrue(any(p.startswith("stale") for p in doc["problems"]))


class CollectTest(unittest.TestCase):
    def test_collect_and_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, text in (("alpha", STANDARD), ("beta", LEGACY), ("gamma", None)):
                repo = root / "owner" / "public" / name
                (repo / ".git").mkdir(parents=True)
                if text:
                    (repo / "docs" / "diagrams").mkdir(parents=True)
                    (repo / "docs" / "diagrams" / "01-business-process.md").write_text(text, encoding="utf-8")
                (repo / "node_modules" / "x" / ".git").mkdir(parents=True)  # never counted
            outside = root / "secret.md"
            outside.write_text(STANDARD, encoding="utf-8")
            linked = root / "owner" / "public" / "delta"
            (linked / ".git").mkdir(parents=True)
            (linked / "docs" / "diagrams").mkdir(parents=True)
            (linked / "docs" / "diagrams" / "01-business-process.md").symlink_to(outside)
            # A symlinked folder on the way down is refused too, even if it stays inside the clone.
            hop = root / "owner" / "public" / "epsilon"
            (hop / ".git").mkdir(parents=True)
            (hop / "real" / "diagrams").mkdir(parents=True)
            (hop / "real" / "diagrams" / "01-business-process.md").write_text(STANDARD, encoding="utf-8")
            (hop / "docs").symlink_to(hop / "real")
            data = report.collect(root, TODAY)
            self.assertEqual(data["repositories"], 5)
            self.assertEqual(data["with_doc"], 2)
            self.assertEqual(data["standard"], 1)
            self.assertEqual(data["missing"], ["owner/delta", "owner/epsilon", "owner/gamma"])  # symlinks are not followed
            self.assertEqual(data["systems"][0]["repo"], "owner/alpha")
            self.assertEqual(data["gap_totals"]["done"], 1)
            self.assertEqual(data["waiting_owner"], 1)  # G4 decide; G3 todo does not count
            page = report.render_html(data)
            self.assertTrue(page.startswith('<meta charset="utf-8">'))
            self.assertIn("<title>As-Is To-Be Board</title>", page[:8192])
            self.assertIn("mermaid.run", page)  # fallback renderer when the viewer does not draw diagrams
            self.assertIn('<pre class="mermaid">', page)
            self.assertIn("&lt;b&gt;today&lt;/b&gt;", page)  # diagram text is escaped
            self.assertNotIn("<b>today</b>", page)

    def test_same_fallback_name_keeps_both_and_worktree_counts_once(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "x" / "one" / "demo"  # fallback name x/demo
            b = root / "x" / "two" / "demo"  # same fallback name
            for repo in (a, b):
                repo.mkdir(parents=True)
                subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(a), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
            subprocess.run(["git", "-C", str(a), "worktree", "add", "-q", str(root / "wt" / "demo-task")], check=True)
            import os
            from unittest import mock
            with mock.patch.dict(os.environ, {"GIT_DIR": str(a / ".git")}):  # as inside a git hook
                data = report.collect(root, TODAY)
            self.assertEqual(data["missing"], ["x/demo", "x/demo"])  # both "demo" repos stay; the worktree folds into its clone

    def test_two_clones_of_one_github_repo_count_once(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main, spare = root / "a" / "public" / "demo", root / "b" / "backup" / "demo-copy"
            for repo in (main, spare):
                repo.mkdir(parents=True)
                subprocess.run(["git", "init", "-q", str(repo)], check=True)
                subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                                "git@github.com:owner/demo.git" if repo is main else "https://github.com/owner/demo"], check=True)
                (repo / "docs" / "diagrams").mkdir(parents=True)
                (repo / "docs" / "diagrams" / "01-business-process.md").write_text(STANDARD, encoding="utf-8")
            other = root / "a" / "public" / "other"
            other.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(other)], check=True)  # no origin: keyed by its git dir
            data = report.collect(root, TODAY)
            self.assertEqual(data["repositories"], 2)
            self.assertEqual([s["repo"] for s in data["systems"]], ["owner/demo"])
            self.assertEqual(data["systems"][0]["path"], "a/public/demo")  # first by path wins
            self.assertEqual(data["gap_totals"]["done"], 1)  # not doubled
            self.assertEqual(data["waiting_owner"], 1)

    def test_worktree_with_its_own_origin_alias_folds_into_its_clone(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clone, tree = root / "a" / "public" / "demo", root / "wt" / "demo-task"
            clone.mkdir(parents=True)
            git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
            subprocess.run(["git", "init", "-q", str(clone)], check=True)
            subprocess.run(["git", "-C", str(clone), "remote", "add", "origin", "https://github.com/owner/demo"], check=True)
            subprocess.run([*git, "-C", str(clone), "commit", "-q", "--allow-empty", "-m", "x"], check=True)
            subprocess.run(["git", "-C", str(clone), "worktree", "add", "-q", str(tree)], check=True)
            subprocess.run(["git", "-C", str(clone), "config", "extensions.worktreeConfig", "true"], check=True)
            subprocess.run(["git", "-C", str(tree), "config", "--worktree", "remote.origin.url", "gh-alias:owner/demo"], check=True)
            data = report.collect(root, TODAY)
            self.assertEqual(data["repositories"], 1)
            self.assertEqual(data["missing"], ["owner/demo"])

    def test_waiting_owner_skips_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "solo"
            (repo / ".git").mkdir(parents=True)
            (repo / "docs" / "diagrams").mkdir(parents=True)
            text = STANDARD.replace("| G3 | c → d | todo |", "| G4 | c → d | decide |")
            (repo / "docs" / "diagrams" / "01-business-process.md").write_text(text, encoding="utf-8")
            self.assertEqual(report.collect(repo, TODAY)["waiting_owner"], 0)


if __name__ == "__main__":
    unittest.main()
