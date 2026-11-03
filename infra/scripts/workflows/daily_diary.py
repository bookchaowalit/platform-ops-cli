#!/usr/bin/env python3
"""Build a privacy-safe, deterministic daily activity diary.

The generated note is a system activity roll-up, not a replacement for a
personal reflection. It reads aggregate/metadata fields from the Tasks API
and the root repository's git history. Raw AI prompts,
responses, and journal content are deliberately never copied to the note.

Usage:
    python3 daily_diary.py --date 2026-08-11
    python3 daily_diary.py --date 2026-08-11 --write
    python3 daily_diary.py --date 2026-08-11 --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

WORKFLOWS_DIR = Path(__file__).resolve().parent
UTILS_DIR = WORKFLOWS_DIR.parent / "utils"
for import_path in (WORKFLOWS_DIR, UTILS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from _env import KNOWLEDGE_ROOT, PROJECT_ROOT  # noqa: E402
from function_share import load_function_ids, parse_functions  # noqa: E402
from knowledge_store import StoreConflict, StoreUnavailable, open_store  # noqa: E402
from task_store import ClientConfigError, TaskStoreError, open_task_store  # noqa: E402


CONTRACT = "solo-empire.daily-diary.v1"
# Diary entries live in the hosted knowledge store (platform-knowledge-api, space knowledge-base,
# path diary/entries/YYYY/MM/DD/...) when it is configured; the files under the knowledge-base
# checkout (SOLO_KNOWLEDGE_ROOT, default ../knowledge-base) are a private local copy, git-ignored.
DEFAULT_ENTRIES_ROOT = KNOWLEDGE_ROOT / "diary" / "entries"
DEFAULT_FILENAME = "auto-daily.md"
CONVERSATION_FILENAME = "conversation-summaries.json"
MAX_ITEMS = 30
MAX_CONVERSATION_SUMMARIES = 100


def _clean(value: Any, limit: int = 220) -> str:
    """Turn a user-controlled field into one safe Markdown line."""
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip()


def _date_arg(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _next_date(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def _date_dir(entries_root: str | Path, day: str) -> Path:
    root = Path(entries_root).expanduser().resolve()
    return root / day[0:4] / day[5:7] / day[8:10]


def open_diary_store():
    """The hosted store, or None when it is not configured (or switched off)."""
    try:
        return open_store()
    except StoreUnavailable:
        return None


def _store_path(day: str, filename: str) -> str:
    return f"diary/entries/{day[0:4]}/{day[5:7]}/{day[8:10]}/{filename}"


def load_conversation_summaries(
    entries_root: str | Path,
    day: str,
) -> list[dict[str, str]]:
    """Load agent-authored summaries without loading raw conversation text."""
    path = _date_dir(entries_root, day) / CONVERSATION_FILENAME
    if not path.is_file():
        return []
    try:
        return _parse_summaries(path.read_bytes())
    except OSError:
        return []


def _parse_summaries(raw: bytes) -> list[dict[str, str]]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []

    summaries: list[dict[str, str]] = []
    for item in value[:MAX_CONVERSATION_SUMMARIES]:
        if not isinstance(item, dict):
            continue
        summary = {
            "id": _clean(item.get("id"), 64),
            "topic": _clean(item.get("topic")),
            "question_summary": _clean(item.get("question_summary")),
            "outcome_summary": _clean(item.get("outcome_summary")),
            "next_step": _clean(item.get("next_step")),
        }
        functions = item.get("functions")
        if isinstance(functions, list):
            summary["functions"] = [_clean(f, 40) for f in functions if isinstance(f, str) and f][:7]
        if summary["id"] and summary["question_summary"] and summary["outcome_summary"]:
            summaries.append(summary)
    return summaries


def record_conversation_summary(
    *,
    day: str,
    topic: str,
    question_summary: str,
    outcome_summary: str,
    next_step: str = "",
    entries_root: str | Path = DEFAULT_ENTRIES_ROOT,
    conversation_id: str | None = None,
    functions: str | list[str] | None = None,
) -> dict[str, Any]:
    """Append one bounded conversation summary and deduplicate it by ID.

    All arguments are summaries supplied by the agent. Callers must not pass
    the raw user prompt, raw assistant response, credentials, or private
    journal content.
    """
    day = _date_arg(day)
    summary = {
        "topic": _clean(topic) or "general conversation",
        "question_summary": _clean(question_summary),
        "outcome_summary": _clean(outcome_summary),
        "next_step": _clean(next_step),
    }
    if not summary["question_summary"] or not summary["outcome_summary"]:
        raise ValueError("question_summary and outcome_summary are required")

    function_ids = parse_functions(functions, load_function_ids()) if functions else []

    stable_input = json.dumps({"date": day, **summary}, ensure_ascii=False, sort_keys=True)
    item_id = _clean(conversation_id, 64) if conversation_id else hashlib.sha256(
        stable_input.encode("utf-8")
    ).hexdigest()[:16]
    item = {"id": item_id, **summary}
    if function_ids:
        item["functions"] = function_ids

    target_dir = _date_dir(entries_root, day)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_dir.chmod(0o700)
    path = target_dir / CONVERSATION_FILENAME
    local = load_conversation_summaries(entries_root, day)

    def apply(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        rows = list(rows)
        match = next((row for row in rows if row["id"] == item_id), None)
        if match is not None and not (function_ids and match.get("functions") != function_ids):
            return rows[-MAX_CONVERSATION_SUMMARIES:], False
        if match is not None:
            rows[rows.index(match)] = item
        else:
            rows.append(item)
        return rows[-MAX_CONVERSATION_SUMMARIES:], True

    def write_local(rows: list[dict[str, Any]]) -> None:
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)

    rows, changed = apply(local)
    store = open_diary_store()
    store_status = "off"
    if store is not None:
        remote_path = _store_path(day, CONVERSATION_FILENAME)
        try:
            for _attempt in range(3):
                remote = store.get(remote_path)
                remote_rows = _parse_summaries(remote[0]) if remote else []
                # Keep the store's order, then anything only this machine had, then this item.
                known = {row["id"] for row in remote_rows}
                merged, _ = apply(remote_rows + [row for row in local if row["id"] not in known])
                if remote is not None and merged == remote_rows:
                    rows, store_status = merged, "synced"
                    break
                try:
                    store.put(
                        remote_path,
                        (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                        if_version=remote[1] if remote else 0,
                    )
                except StoreConflict:
                    continue
                rows, store_status = merged, "synced"
                break
            else:
                store_status = "failed: conflict"
        except StoreUnavailable as exc:
            store_status = f"failed: {exc}"
    if changed or store_status == "synced":
        write_local(rows)
    status = "recorded" if changed else "already_recorded"
    return {"status": status, "id": item_id, "path": str(path), "summary": item, "store": store_status}


def _git_commits(repo_root: Path, day: str) -> list[dict[str, str]]:
    """Read only root-repository commits for the requested local date."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "log",
                f"--since={day} 00:00:00",
                f"--until={_next_date(day)} 00:00:00",
                "--date=short",
                "--pretty=format:%h%x09%s",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0:
        return []

    commits: list[dict[str, str]] = []
    for line in result.stdout.splitlines()[:MAX_ITEMS]:
        sha, _, subject = line.partition("\t")
        if sha and subject:
            commits.append({"sha": _clean(sha, 16), "subject": _clean(subject)})
    return commits


def _overview(
    completed_tasks: list[dict[str, Any]],
    created_tasks: list[dict[str, Any]],
    conversation_summaries: list[dict[str, str]],
    git_commits: list[dict[str, str]],
    current_state: dict[str, int],
) -> dict[str, Any]:
    activity_count = (
        len(completed_tasks)
        + len(conversation_summaries)
        + len(git_commits)
    )

    if activity_count > 0:
        status = "มีแรงส่ง"
        reason = "มี activity ที่ตรวจสอบได้ในวันนี้"
    elif current_state["blocked_tasks"] > 0:
        status = "ติดขัด"
        reason = "ยังมีงานที่ถูกทำเครื่องหมาย blocked"
    else:
        status = "ยังไม่มีข้อมูลกิจกรรม"
        reason = "ไม่พบ task, สรุปบทสนทนา หรือ commit ของวันนี้"

    return {
        "status": status,
        "reason": reason,
        "completed_tasks": len(completed_tasks),
        "created_tasks": len(created_tasks),
        "conversation_summaries": len(conversation_summaries),
        "git_commits": len(git_commits),
        "blocked_tasks_current": current_state["blocked_tasks"],
        "overdue_tasks_current": current_state["overdue_tasks"],
    }


def _task_sections(day: str) -> tuple[list[dict], list[dict], dict[str, int]]:
    """Completed and created tasks of the day plus the current blocked/overdue counts, from the Tasks API."""
    store = open_task_store(None, "reporting-reader")
    task_columns = ("title", "project", "category", "priority")
    completed_tasks = [
        {**{k: t[k] for k in task_columns}, "completed_at": t["completed_at"]}
        for t in sorted(
            store.list(status=["done"], completed_between=(day, day), include_archived=True),
            key=lambda t: (t["completed_at"] or "", t["id"]),
        )[:MAX_ITEMS]
    ]
    created_tasks = [
        {**{k: t[k] for k in task_columns}, "created_at": t["created_at"]}
        for t in sorted(
            store.list(created_between=(day, day), include_archived=True),
            key=lambda t: (t["created_at"] or "", t["id"]),
        )[:MAX_ITEMS]
    ]
    current_state = {
        "blocked_tasks": store.stats(status=["blocked"])["total"],
        "overdue_tasks": sum(
            1 for t in store.list(due_before=day) if t["status"] not in ("done", "cancelled")
        ),
    }
    return completed_tasks, created_tasks, current_state


def build_daily_diary(
    *,
    as_of: str | date | None = None,
    repo_root: str | Path = PROJECT_ROOT,
    entries_root: str | Path = DEFAULT_ENTRIES_ROOT,
) -> dict[str, Any]:
    """Build the bounded daily payload without writing or mutating state."""
    day = as_of.isoformat() if isinstance(as_of, date) else (as_of or date.today().isoformat())
    day = _date_arg(day)

    try:
        completed_tasks, created_tasks, current_state = _task_sections(day)
        tasks_available = True
    except (ClientConfigError, TaskStoreError):
        # The conversation record and the git history stay useful when the Tasks API is unreachable.
        completed_tasks, created_tasks = [], []
        current_state = {"blocked_tasks": 0, "overdue_tasks": 0}
        tasks_available = False
    git_commits = _git_commits(Path(repo_root), day)
    conversation_summaries = load_conversation_summaries(entries_root, day)

    overview = _overview(
        completed_tasks,
        created_tasks,
        conversation_summaries,
        git_commits,
        current_state,
    )
    return {
        "contract": CONTRACT,
        "date": day,
        "privacy_class": "private",
        "tasks_available": tasks_available,
        "activities": {
            "completed_tasks": completed_tasks,
            "created_tasks": created_tasks,
            "conversation_summaries": conversation_summaries,
            "git_commits": git_commits,
        },
        "overview": overview,
        "policy": {
            "read_only_sources": [
                "tasks-api:tasks",
                "local:conversation_summaries.metadata_only",
                "git:root_repository_history",
            ],
            "raw_ai_prompt_included": False,
            "raw_ai_response_included": False,
            "raw_conversation_included": False,
            "raw_journal_content_included": False,
            "external_messages": False,
            "idempotent_file_write": True,
        },
    }


def _item_line(title: Any, suffix: str = "") -> str:
    cleaned = _clean(title) or "(ไม่มีชื่อ)"
    return f"- {cleaned}{suffix}"


def _render_items(rows: list[dict[str, Any]], render_row) -> list[str]:
    if not rows:
        return ["- ไม่มีข้อมูล"]
    return [render_row(row) for row in rows]


def render_daily_diary(payload: dict[str, Any]) -> str:
    """Render only bounded fields; never render prompt/response/content."""
    day = payload["date"]
    activities = payload["activities"]
    overview = payload["overview"]
    lines = [
        "---",
        "title: Auto Daily Activity Diary",
        f"date: {day}",
        "entry_type: system_activity",
        "privacy_class: private",
        "generated: true",
        f"contract: {payload['contract']}",
        "---",
        "",
        f"# กิจกรรมประจำวันที่ {day}",
        "",
        "> ไฟล์นี้สร้างจาก metadata ของระบบแบบอัตโนมัติ ไม่ใช่ personal reflection",
        "> และไม่คัดลอก prompt/response ของ AI หรือเนื้อหา journal ดิบ",
        "",
        "## ภาพรวม",
        f"- สถานะ: **{overview['status']}** — {overview['reason']}",
        f"- งานที่เสร็จ: {overview['completed_tasks']} รายการ",
        f"- งานที่สร้าง: {overview['created_tasks']} รายการ",
        f"- บทสนทนาที่ agent สรุปจากรอบคุย: {overview['conversation_summaries']} รายการ",
        f"- root git commits: {overview['git_commits']} รายการ",
        f"- งาน blocked ณ เวลาสร้าง: {overview['blocked_tasks_current']} รายการ",
        f"- งาน overdue ณ เวลาสร้าง: {overview['overdue_tasks_current']} รายการ",
        "",
        "## สิ่งที่ทำไป",
    ]
    lines.extend(
        _render_items(
            activities["completed_tasks"],
            lambda row: _item_line(
                row.get("title"),
                f" ({_clean(row.get('project')) or 'ไม่มี project'}; priority {row.get('priority') or 'n/a'})",
            ),
        )
    )
    lines.extend(["", "## งานที่เริ่ม/บันทึกเพิ่ม"])
    lines.extend(
        _render_items(
            activities["created_tasks"],
            lambda row: _item_line(
                row.get("title"),
                f" ({_clean(row.get('project')) or 'ไม่มี project'})",
            ),
        )
    )
    lines.extend(["", "## บทสนทนาที่สรุปจากรอบคุย"])
    lines.extend(
        _render_items(
            activities["conversation_summaries"],
            lambda row: _item_line(
                row.get("topic"),
                f" — {row.get('question_summary')} → {row.get('outcome_summary')}"
                + (f"; next: {row.get('next_step')}" if row.get("next_step") else "")
                + (f" [function: {', '.join(row['functions'])}]" if row.get("functions") else ""),
            ),
        )
    )
    lines.extend(["", "## Git activity ของ root repository"])
    lines.extend(
        _render_items(
            activities["git_commits"],
            lambda row: _item_line(f"`{_clean(row.get('sha'), 16)}` {_clean(row.get('subject'))}"),
        )
    )
    lines.extend(
        [
            "",
            "## ขอบเขตข้อมูล",
            "- แหล่งข้อมูลเป็น Tasks API (เมื่อเข้าถึงได้), agent-authored conversation summaries และ root git history เท่านั้น",
            "- ไม่ได้สแกน nested repositories และไม่รวม uncommitted files",
            "- สามารถรันซ้ำเพื่ออัปเดตไฟล์ของวันเดิมได้โดยไม่สร้างรายการซ้ำ",
            "",
        ]
    )
    return "\n".join(lines)


def refresh_local_summaries(entries_root: str | Path, day: str) -> str:
    """Merge the store's summaries for ``day`` into the local copy before a daily note is built,
    so a note uploaded from this machine never drops summaries recorded elsewhere."""
    store = open_diary_store()
    if store is None:
        return "off"
    try:
        remote = store.get(_store_path(day, CONVERSATION_FILENAME))
    except StoreUnavailable as exc:
        return f"failed: {exc}"
    if remote is None:
        return "synced"
    local = load_conversation_summaries(entries_root, day)
    rows = _parse_summaries(remote[0])
    known = {row["id"] for row in rows}
    rows = (rows + [row for row in local if row["id"] not in known])[-MAX_CONVERSATION_SUMMARIES:]
    if rows != local:
        target_dir = _date_dir(entries_root, day)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_dir.chmod(0o700)
        path = target_dir / CONVERSATION_FILENAME
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
    return "synced"


def write_daily_diary(payload: dict[str, Any], entries_root: str | Path = DEFAULT_ENTRIES_ROOT) -> Path:
    """Write the date-partitioned private Markdown note."""
    target_dir = _date_dir(entries_root, payload["date"])
    target_dir.mkdir(parents=True, exist_ok=True)
    target_dir.chmod(0o700)
    target = target_dir / DEFAULT_FILENAME
    rendered = render_daily_diary(payload)
    target.write_text(rendered, encoding="utf-8")
    target.chmod(0o600)
    store = open_diary_store()
    if store is not None:
        remote_path = _store_path(payload["date"], DEFAULT_FILENAME)
        try:
            # The daily note is regenerated from the summaries, so the newest render wins.
            for _attempt in range(3):
                current = store.get(remote_path)
                try:
                    store.put(remote_path, rendered.encode("utf-8"), if_version=current[1] if current else 0)
                    break
                except StoreConflict:
                    continue
        except StoreUnavailable as exc:
            print(f"warning: daily note kept locally; store {exc}", file=sys.stderr)
    return target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a privacy-safe daily activity diary")
    parser.add_argument("--date", type=_date_arg, default=date.today().isoformat())
    parser.add_argument("--entries-root", type=Path, default=DEFAULT_ENTRIES_ROOT)
    parser.add_argument("--repo-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--record-conversation",
        action="store_true",
        help="Record an agent-authored conversation summary and refresh the daily note",
    )
    parser.add_argument("--topic", default="general conversation")
    parser.add_argument("--question-summary")
    parser.add_argument("--outcome-summary")
    parser.add_argument("--next-step", default="")
    parser.add_argument("--conversation-id")
    parser.add_argument(
        "--functions",
        default="",
        help="Comma-separated universal function IDs for this turn, e.g. build-creation,governance-assurance",
    )
    parser.add_argument("--write", action="store_true", help="Write <knowledge-base>/diary/entries/YYYY/MM/DD/auto-daily.md")
    parser.add_argument("--json", action="store_true", help="Print the bounded payload as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    conversation_record = None
    if args.record_conversation:
        conversation_record = record_conversation_summary(
            day=args.date,
            topic=args.topic,
            question_summary=args.question_summary or "",
            outcome_summary=args.outcome_summary or "",
            next_step=args.next_step,
            entries_root=args.entries_root,
            conversation_id=args.conversation_id,
            functions=args.functions,
        )
    if not args.record_conversation:
        # record_conversation_summary already merged the store's summaries.
        refresh_local_summaries(args.entries_root, args.date)
    payload = build_daily_diary(
        as_of=args.date,
        repo_root=args.repo_root,
        entries_root=args.entries_root,
    )

    output_path = write_daily_diary(payload, args.entries_root) if (args.write or args.record_conversation) else None
    if args.json:
        result = dict(payload)
        if conversation_record:
            result["conversation_record"] = conversation_record
        if output_path:
            result["output_path"] = str(output_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_daily_diary(payload))
        if output_path:
            print(f"\n✅ Daily diary written: {output_path}")
        else:
            print("\n(dry-run — use --write to persist)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
