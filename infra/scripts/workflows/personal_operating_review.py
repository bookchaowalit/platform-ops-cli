#!/usr/bin/env python3
"""Generate a privacy-safe weekly review of personal operating signals.

The review is a governance artifact for one canonical task. It reads counts,
dates, durations, schedules, and statuses from the local SQLite control plane;
it never copies raw diary, journal, habit-note, or routine-note content.

Usage:
    python3 personal_operating_review.py --cycle adaptive-foundation-2026-w34
    python3 personal_operating_review.py --cycle adaptive-foundation-2026-w34 --write
    python3 personal_operating_review.py --start 2026-08-10 --end 2026-08-16 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

WORKFLOWS_DIR = Path(__file__).resolve().parent
UTILS_DIR = WORKFLOWS_DIR.parent / "utils"
for import_path in (WORKFLOWS_DIR, UTILS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from _env import PROJECT_ROOT, ARCHITECTURE_ROOT  # noqa: E402
from cell_store import open_cell_store  # noqa: E402
from habits_store import open_habits_store  # noqa: E402


CONTRACT = "solo-empire.personal-operating-review.v1"
DEFAULT_OUTPUT_DIR = ARCHITECTURE_ROOT / "operations" / "evidence"
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def date_list(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def parse_schedule_days(value: Any) -> set[str]:
    if not value:
        return set()
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return set()
    if not isinstance(parsed, list):
        return set()
    return {str(item).strip().lower()[:3] for item in parsed if str(item).strip()}


def is_scheduled(habit: dict[str, Any], day: date) -> bool:
    frequency = str(habit["frequency"] or "daily").lower()
    schedule_days = parse_schedule_days(habit["schedule_days"])
    weekday = DAY_NAMES[day.weekday()]

    if schedule_days:
        return weekday in schedule_days
    if frequency == "daily":
        return True
    if frequency == "weekly":
        return day.weekday() == 6
    if frequency == "monthly":
        return day.day == 1
    return False


def select_cycle(slug: str | None) -> dict[str, Any] | None:
    """The requested (or current) cycle from the operating cell;"""
    store = open_cell_store("operating", "reporting-reader")
    if slug:
        rows = store.list("cycles", slug=slug)
    else:
        rows = store.list("cycles", status=["active", "in_review", "planned"])
        rank = {"active": 0, "in_review": 1}
        rows.sort(key=lambda r: (str(r["start_date"]), r["id"]), reverse=True)
        rows.sort(key=lambda r: rank.get(r["status"], 2))
    if not rows:
        return None
    return {key: rows[0][key] for key in ("id", "slug", "name", "status", "start_date", "end_date")}


def count_auto_daily_notes(start: date, end: date) -> int:
    entries_root = PROJECT_ROOT / "diary" / "entries"
    if not entries_root.is_dir():
        return 0
    count = 0
    for path in entries_root.glob("*/[0-9][0-9]/[0-9][0-9]/auto-daily.md"):
        parts = path.parts
        try:
            year, month, day = parts[-4], parts[-3], parts[-2]
            note_date = date.fromisoformat(f"{year}-{month}-{day}")
        except (IndexError, ValueError):
            continue
        if start <= note_date <= end:
            count += 1
    return count


def collect_snapshot(start: date,
    end: date,
    cycle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    days = date_list(start, end)
    start_text, end_text = start.isoformat(), end.isoformat()

    store = open_habits_store("reporting-reader")
    # ORDER BY priority, id (NULL priority sorts first, as in SQLite)
    habit_rows = sorted(
        store.habits(status="active"),
        key=lambda h: (h.get("priority") is not None, h.get("priority") or 0, h["id"]),
    )
    logs_by_habit: dict[int, list[dict[str, Any]]] = {}
    for log in store.habit_logs(date_from=start_text, date_to=end_text, completed=True):
        logs_by_habit.setdefault(log["habit_id"], []).append(log)
    habit_detail: list[dict[str, Any]] = []
    for habit in habit_rows:
        scheduled = sum(1 for day in days if is_scheduled(habit, day))
        logs = logs_by_habit.get(habit["id"], [])
        habit_detail.append(
            {
                "id": habit["id"],
                "name": habit["name"],
                "frequency": habit["frequency"],
                "scheduled": scheduled,
                "completed": len(logs),
                "minutes": int(sum(log.get("duration_minutes") or 0 for log in logs)),
                "last_completed": max((log["log_date"] for log in logs), default=None),
                "current_streak": int(habit["current_streak"] or 0),
                "best_streak": int(habit["best_streak"] or 0),
            }
        )

    scheduled_total = sum(item["scheduled"] for item in habit_detail)
    completed_total = sum(item["completed"] for item in habit_detail)

    routine_logs_by_id: dict[int, list[dict[str, Any]]] = {}
    for log in store.routine_logs(date_from=start_text, date_to=end_text):
        routine_logs_by_id.setdefault(log["routine_id"], []).append(log)
    routine_detail = [
        {
            "id": row["id"],
            "name": row["name"],
            "routine_type": row["routine_type"],
            "completions": len(routine_logs_by_id.get(row["id"], [])),
            "last_completed": max(
                (log["log_date"] for log in routine_logs_by_id.get(row["id"], [])), default=None
            ),
        }
        for row in sorted(store.routines(status="active"), key=lambda r: r["id"])
    ]

    return {
        "contract": CONTRACT,
        "generated_at": date.today().isoformat(),
        "cycle": cycle,
        "window": {"start": start_text, "end": end_text},
        "privacy": {
            "raw_journal_content_included": False,
            "raw_diary_content_included": False,
            "raw_habit_notes_included": False,
            "raw_routine_notes_included": False,
        },
        "journal": {
            "auto_daily_notes": count_auto_daily_notes(start, end),
        },
        "habits": {
            "active": len(habit_detail),
            "scheduled_occurrences": scheduled_total,
            "completed_occurrences": completed_total,
            "completion_rate": (
                round(completed_total / scheduled_total * 100, 2)
                if scheduled_total
                else None
            ),
            "detail": habit_detail,
        },
        "routines": {
            "active": len(routine_detail),
            "logged_completions": sum(item["completions"] for item in routine_detail),
            "detail": routine_detail,
        },
    }


def markdown_report(snapshot: dict[str, Any]) -> str:
    cycle = snapshot.get("cycle") or {}
    window = snapshot["window"]
    journal = snapshot["journal"]
    habits = snapshot["habits"]
    routines = snapshot["routines"]
    cycle_label = cycle.get("slug", "custom-window")
    cycle_name = cycle.get("name", "Custom review window")
    completion_rate = (
        f"{habits['completion_rate']:.2f}%"
        if habits["completion_rate"] is not None
        else "no_data"
    )

    lines = [
        "---",
        "title: Personal Operating Review",
        "aliases: [Diary Habit Routine Review, Personal Operating Review]",
        "tags: [operations, review, diary, habits, routines, privacy-safe]",
        "status: evidence",
        f"generated: {snapshot['generated_at']}",
        f"cycle: {cycle_label}",
        "---",
        "",
        "# Personal Operating Review",
        "",
        f"> Cycle: `{cycle_label}` — {cycle_name}",
        f"> Window: {window['start']} → {window['end']}",
        "> This artifact contains aggregate metadata only. Raw diary, journal, habit notes, and routine notes are not copied.",
        "",
        "## Signals",
        "",
        "| Signal | Value |",
        "|---|---:|",
        f"| Automatic diary activity notes | {journal['auto_daily_notes']} |",
        f"| Active habits | {habits['active']} |",
        f"| Scheduled habit occurrences | {habits['scheduled_occurrences']} |",
        f"| Completed habit occurrences | {habits['completed_occurrences']} |",
        f"| Habit completion rate | {completion_rate} |",
        f"| Active routines | {routines['active']} |",
        f"| Routine completions logged | {routines['logged_completions']} |",
        "",
        "## Habit coverage",
        "",
        "| Habit | Scheduled | Completed | Minutes | Last completed | Streak |",
        "|---|---:|---:|---:|---|---:|",
    ]
    for item in habits["detail"]:
        lines.append(
            f"| {item['name']} | {item['scheduled']} | {item['completed']} | "
            f"{item['minutes']} | {item['last_completed'] or 'none'} | {item['current_streak']} |"
        )

    lines.extend([
        "",
        "## Routine coverage",
        "",
        "| Routine | Type | Completions | Last completed |",
        "|---|---|---:|---|",
    ])
    for item in routines["detail"]:
        lines.append(
            f"| {item['name']} | {item['routine_type']} | {item['completions']} | "
            f"{item['last_completed'] or 'none'} |"
        )

    lines.extend([
        "",
        "## Owner review",
        "",
        "- Decision: `continue | adjust | pause | close` — Owner to record.",
        "- What to continue: Owner to record one evidence-backed practice.",
        "- What to change: Owner to record one habit, routine, or diary workflow adjustment.",
        "- Next action: Owner to record one concrete action for the next cycle.",
        "",
        "## Source and privacy contract",
        "",
        "- Source: diary filename metadata, and habits, routines and their logs from the habits cell API.",
        "- Raw diary and journal content remains in its source boundary and is not projected into this artifact.",
        "- This report is evidence for the `Personal Operating Review` task; it does not mark that task done or close the cycle.",
        "",
    ])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a privacy-safe personal operating review")
    parser.add_argument("--cycle", help="Operating cycle slug; defaults to the active/reviewable cycle")
    parser.add_argument("--start", type=parse_date, help="Review window start, YYYY-MM-DD")
    parser.add_argument("--end", type=parse_date, help="Review window end, YYYY-MM-DD")
    parser.add_argument("--write", action="store_true", help="Write the evidence Markdown file")
    parser.add_argument("--output", type=Path, help="Optional output path used with --write")
    parser.add_argument("--json", action="store_true", help="Print structured metadata instead of Markdown")
    return parser


def write_review(relative: str, report: str, *, root: Path = ARCHITECTURE_ROOT) -> str:
    """Write the review into the architecture-docs checkout (SPEC-001 phase 2).

    Git is the source until the phase 3 cutover: store-mirror copies main into the store with
    --prune, so a review written straight into the store would be deleted by the next push, and a
    store snapshot is replaced by the next refresh. Without a checkout the command stops.
    """
    if not (root / ".git").exists():
        raise SystemExit(
            f"architecture documents at {root} are not a Git checkout (a store snapshot?): Git is the source "
            "until the SPEC-001 phase 3 cutover, so clone architecture-docs or pass --output for a local file"
        )
    output = root / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    return str(output)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cycle = select_cycle(args.cycle)
    if args.cycle and cycle is None:
        raise SystemExit(f"cycle not found: {args.cycle}")
    start = args.start or (parse_date(cycle["start_date"]) if cycle else date.today())
    end = args.end or (parse_date(cycle["end_date"]) if cycle else start)
    snapshot = collect_snapshot(start, end, cycle)

    report = markdown_report(snapshot)
    if args.write:
        name = f"PERSONAL-OPERATING-REVIEW-{snapshot['cycle']['slug'] if snapshot['cycle'] else start.isoformat()}.md"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report, encoding="utf-8")
            target = str(args.output)
        else:
            target = write_review(f"operations/evidence/{name}", report)
        print(f"✅ Personal operating review written: {target}")

    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
