#!/usr/bin/env python3
"""Offline tests for the Linear → Book Dev execution ticket cycle."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "workflows"))
import book_dev_linear_cycle as cycle  # noqa: E402


def issue(identifier: str, title: str, *, priority: int = 2, labels: tuple[str, ...] = ("ai-dispatch",)) -> dict:
    return {
        "identifier": identifier,
        "title": title,
        "description": "Make it work.",
        "url": f"https://linear.app/x/issue/{identifier}",
        "priority": priority,
        "dueDate": None,
        "createdAt": f"2026-10-0{priority}T00:00:00Z",
        "updatedAt": "2026-10-15T00:00:00Z",
        "state": {"name": "Backlog", "type": "backlog"},
        "project": {"name": "Book Dev — Test"},
        "labels": {"nodes": [{"name": name} for name in labels]},
    }


def provider_run(result: mock.Mock) -> mock._patch:
    """Mock provider processes only; git status calls in the loop stay real."""

    # Provider CLIs go through _run_process (own session, process-group kill).
    return mock.patch("book_dev_ai_dispatch._run_process", return_value=result)


def make_repo(root: Path, owner: str, name: str) -> Path:
    repo = root / "github" / owner / "public" / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        tmp = Path(temp_dir)
        root = tmp / "book-dev"
        make_repo(root, "owner", "repo-a")
        make_repo(root, "owner", "dup")
        make_repo(root, "other", "dup")

        issues = [
            issue("BOO-3", "[repo-a] Low priority work", priority=4),
            issue("BOO-1", "[repo-a] Urgent fix", priority=1),
            issue("BOO-2", "[repo-a] Waiting", priority=1, labels=("ai-dispatch", "รอเจ้าของ")),
            issue("BOO-4", "[dup] Ambiguous checkout"),
            issue("BOO-5", "No repository prefix"),
            issue("BOO-6", "[missing-repo] Unknown checkout"),
        ]
        runnable, skipped = cycle.plan_issues(
            issues,
            cycle.index_repositories(root),
            book_dev_root=root,
            hold_labels=cycle.DEFAULT_HOLD_LABELS,
            dispatched={"BOO-3"},
        )
        assert [task["id"] for task, _ in runnable] == ["BOO-1"]
        first = runnable[0][0]
        assert first["title"] == "[Book Dev] owner/repo-a — Urgent fix"
        assert "https://linear.app/x/issue/BOO-1" in first["description"]
        reasons = {item["issue"]: item["reason"] for item in skipped}
        assert reasons == {
            "BOO-3": "already_dispatched",
            "BOO-2": "hold_label",
            "BOO-4": "repository_ambiguous",
            "BOO-5": "title_without_repository_prefix",
            "BOO-6": "repository_not_found",
        }

        # No credential: report and stop before any network call.
        with mock.patch.object(cycle, "call_linear") as call:
            report = cycle.run_cycle(
                label="ai-dispatch", hold_labels=(), providers="claude", mode="plan",
                dry_run=True, book_dev_root=root, output_dir=tmp / "out", state_dir=tmp / "state",
                token="",
            )
            assert report["status"] == "not_configured"
            call.assert_not_called()

        payload = {"data": {"issues": {"nodes": issues}}}
        common = dict(
            label="ai-dispatch", hold_labels=cycle.DEFAULT_HOLD_LABELS, providers="claude", mode="implement",
            book_dev_root=root, output_dir=tmp / "out", state_dir=tmp / "state", token="t",
        )
        with mock.patch.object(cycle, "call_linear", return_value=payload), mock.patch(
            "book_dev_ai_dispatch.resolve_claude_cmd", return_value=["claude"]
        ):
            # Dry run: ticket queued and command rendered, ledger untouched.
            report = cycle.run_cycle(dry_run=True, **common)
            assert report["issue"] == "BOO-1", report
            assert report["dispatch_status"] == "dry_run", report
            ticket = json.loads((tmp / "out" / "execution" / "current.json").read_text(encoding="utf-8"))
            assert ticket["selected"]["task"]["id"] == "BOO-1"
            assert not (tmp / "state" / "state.json").exists()

            # Real run (provider mocked): issue is recorded and skipped next time.
            completed = mock.Mock(returncode=0, stdout="ok", stderr="")
            with provider_run(completed) as fake:
                report = cycle.run_cycle(dry_run=False, **common)
                assert report["dispatch_status"] == "dispatched", report
                assert fake.call_args.kwargs["cwd"] == str((root / "github/owner/public/repo-a").resolve())
            state = json.loads((tmp / "state" / "state.json").read_text(encoding="utf-8"))
            assert [item["issue"] for item in state["dispatched"]] == ["BOO-1"]

            with provider_run(completed):
                report = cycle.run_cycle(dry_run=False, **common)
            assert report["issue"] == "BOO-3", report

        # The default timer flow: Claude builds, Codex reviews, rendered as a dry run.
        with mock.patch.object(cycle, "call_linear", return_value=payload), mock.patch(
            "book_dev_ai_dispatch.resolve_claude_cmd", return_value=["claude"]
        ), mock.patch("book_dev_ai_dispatch.resolve_codex_cmd", return_value=["codex"]):
            report = cycle.run_cycle(
                dry_run=True, reviewer=cycle.DEFAULT_REVIEWER, **{**common, "state_dir": tmp / "state-review"}
            )
        assert report["reviewer"] == "codex" and report["dispatch_status"] == "dry_run", report
        ticket = json.loads((tmp / "out" / "execution" / "current.json").read_text(encoding="utf-8"))
        dispatch_report = json.loads(Path(ticket["dispatch"]["report"]).read_text(encoding="utf-8"))
        assert dispatch_report["flow"]["builder"] == "claude" and dispatch_report["flow"]["reviewer"] == "codex"

        # A missing CLI does not consume the issue.
        with mock.patch.object(cycle, "call_linear", return_value=payload), mock.patch(
            "book_dev_ai_dispatch.resolve_claude_cmd", return_value=None
        ):
            state_dir = tmp / "state-unavailable"
            report = cycle.run_cycle(dry_run=False, **{**common, "state_dir": state_dir})
            assert report["dispatch_status"] == "unavailable", report
            assert not (state_dir / "state.json").exists()

        # A failed run (for example a CLI that is not logged in) retries, then gives up.
        failed = mock.Mock(returncode=1, stdout="Not logged in", stderr="")
        state_dir = tmp / "state-failed"
        with mock.patch.object(cycle, "call_linear", return_value=payload), mock.patch(
            "book_dev_ai_dispatch.resolve_claude_cmd", return_value=["claude"]
        ), provider_run(failed):
            for attempt in range(1, cycle.MAX_FAILED_ATTEMPTS + 1):
                report = cycle.run_cycle(dry_run=False, **{**common, "state_dir": state_dir})
                assert report["issue"] == "BOO-1" and report["failed_attempts"] == attempt, report
            report = cycle.run_cycle(dry_run=False, **{**common, "state_dir": state_dir})
            assert report["issue"] == "BOO-3", report

        with mock.patch.object(cycle, "call_linear", return_value={"errors": [{"message": "bad"}]}):
            report = cycle.run_cycle(dry_run=True, **common)
            assert report["status"] == "error"

    print("Book Dev Linear cycle tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
