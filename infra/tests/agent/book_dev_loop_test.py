#!/usr/bin/env python3
"""Offline contract tests for the Book Dev engineering loop."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "workflows"))
from book_dev_loop import (  # noqa: E402
    DESCRIPTION_LIMIT,
    TRUNCATED_MARK,
    build_candidate,
    build_report,
    extract_repo_ref,
    render_markdown,
    write_outputs,
)
from book_dev_execution_hook import queue_execution  # noqa: E402


def task(task_id: int, title: str, status: str = "todo", priority: int = 2) -> dict:
    return {
        "id": task_id,
        "title": title,
        "description": "bounded task",
        "status": status,
        "priority": priority,
        "project": "Book Dev / Per-Repository",
        "due_date": None,
        "updated_at": "2026-08-11 00:00:00",
        "parent_task_id": 2126,
    }


def repo(ref: str, state: str = "clean", dirty: bool = False) -> dict:
    return {
        "ref": ref,
        "path": f"projects/product/engineering/book-dev/github/{ref.replace('/', '/book-apps/tools/')}",
        "state": state,
        "dirty": dirty,
        "dirty_file_count": 2 if dirty else 0,
        "branch": "main",
        "upstream": "origin/main",
        "has_upstream": True,
    }


def description_tests() -> None:
    """A long Thai Linear checklist keeps every line in the candidate; only past the cap is it marked cut."""
    items = [f"- [ ] {n}. แก้ปัญหา UX ข้อที่ {n}: " + "ตรวจทุกหน้าที่ 375/768/1440 " * 20 for n in range(1, 7)]
    description = "\n".join(["Linear BOO-394: https://linear.app/x", "", "## ต้องแก้", *items])
    assert len(description) > 600
    item = task(9, "[Book Dev] bookchaowalit/site — fix UX")
    item["description"] = description
    kept = build_candidate(item, [])["task"]["description"]
    for line in items:
        assert line.strip() in kept.splitlines(), line[:40]
    assert TRUNCATED_MARK not in kept

    item["description"] = "ก" * (DESCRIPTION_LIMIT + 10)
    cut = build_candidate(item, [])["task"]["description"]
    assert cut.endswith(f"\n{TRUNCATED_MARK}") and len(cut) <= DESCRIPTION_LIMIT + len(TRUNCATED_MARK) + 1


def main() -> int:
    description_tests()
    assert extract_repo_ref("[Book Dev] bookchaowalit/career-job-scraper — review dirty changes") == "bookchaowalit/career-job-scraper"
    assert extract_repo_ref("[Book Dev] missing mapping") is None

    report = build_report(
        [
            task(1, "[Book Dev] bookchaowalit/career-job-scraper — review dirty changes"),
            task(2, "[Book Dev] bookchaowalit/bookreading — verify tests", priority=3),
            task(3, "[Book Dev] bookchaowalit/blocked — needs approval", status="blocked"),
        ],
        [
            repo("bookchaowalit/career-job-scraper", state="dirty", dirty=True),
            repo("bookchaowalit/bookreading"),
            repo("bookchaowalit/untracked", state="dirty", dirty=True),
        ],
        generated_at="2026-08-11T00:00:00+00:00",
    )

    assert report["contract"] == "solo-empire.book-dev-engineering-loop.v1"
    assert report["summary"]["open_task_count"] == 3
    assert report["summary"]["blocked_task_count"] == 1
    # 2 open-task candidates + 1 synthetic dirty-repo candidate (max_tasks default 5)
    assert report["summary"]["candidate_count"] == 3
    assert report["summary"]["open_task_candidate_count"] == 2
    assert report["summary"]["dirty_repository_candidate_count"] == 1
    assert report["summary"]["dirty_repository_without_open_task_count"] == 1
    assert report["policy"]["scan_read_only"] is True
    assert report["policy"]["nested_repository_writes"] is False
    assert report["candidates"][0]["repository"]["state"] == "dirty"
    assert report["candidates"][0]["source"] == "open_task"
    assert report["candidates"][2]["source"] == "dirty_repository_without_open_task"
    assert "commit" in report["candidates"][0]["agent_prompt"]

    markdown = render_markdown(report)
    assert "Book Dev Engineering Loop" in markdown
    assert "does not authorize changes" in markdown

    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir)
        outputs = write_outputs(report, output_dir)
        assert Path(outputs["json"]).is_absolute() is True
        assert (output_dir / "latest.json").is_file()
        loaded = json.loads((output_dir / "latest.json").read_text(encoding="utf-8"))
        assert loaded["summary"] == report["summary"]
        assert (output_dir / "latest.md").is_file()
        assert (output_dir / "state.json").is_file()

        ticket = queue_execution(
            latest_json=output_dir / "latest.json",
            execution_dir=output_dir / "execution",
            write=True,
            force=True,
            report=report,
        )
        assert ticket["status"] == "queued"
        assert ticket["selected"]["task"]["id"] == 1
        assert (output_dir / "execution" / "current.md").is_file()
        assert "execution_ticket_queue" in report["workflow"]["moves"]

    print("Book Dev engineering loop tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
