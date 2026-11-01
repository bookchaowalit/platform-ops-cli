#!/usr/bin/env python3
"""Turn opted-in Linear issues into Book Dev execution tickets and dispatch one.

One cycle:
    1. read Linear issues that carry the opt-in label (default ``ai-dispatch``)
       and are still in a backlog/unstarted state;
    2. drop issues with a hold label, issues already dispatched by an earlier
       cycle, and issues whose ``[repository]`` title prefix does not match
       exactly one local checkout;
    3. write the loop handoff and queue the first issue as the execution ticket;
    4. dispatch that ticket: by default Claude builds on a worktree branch,
       Codex reviews it read-only, and a passing branch becomes a pull request
       (set ``BOOK_DEV_DISPATCH_REVIEWER=none`` for the single-provider flow).

Linear is only read. The cycle never creates, moves, or comments on issues, and
dispatches at most one ticket per run. The ledger in ``state.json`` stops the
same issue from being dispatched twice; delete its entry to retry an issue. A
missing CLI never consumes an issue, and a failed run is retried up to
MAX_FAILED_ATTEMPTS times before it is recorded.

Usage:
    python3 book_dev_linear_cycle.py --dry-run --json
    python3 book_dev_linear_cycle.py --providers claude --mode implement
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKFLOW_DIR = Path(__file__).resolve().parent
UTILS_DIR = WORKFLOW_DIR.parent / "utils"
SYNC_DIR = WORKFLOW_DIR.parent / "sync"
for _path in (UTILS_DIR, WORKFLOW_DIR, SYNC_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from _env import PROJECT_ROOT, get_env  # noqa: E402
from book_dev_execution_hook import queue_execution  # noqa: E402
from book_dev_loop import (  # noqa: E402
    BOOK_DEV_ROOT,
    DEFAULT_OUTPUT_DIR,
    _repo_ref,
    build_report,
    discover_repositories,
    inspect_repository,
    write_outputs,
)
from linear_readonly import call_linear, has_graphql_error, safe_error  # noqa: E402

CONTRACT = "solo-empire.book-dev-linear-cycle.v1"
DEFAULT_LABEL = "ai-dispatch"
DEFAULT_HOLD_LABELS = ("รอเจ้าของ", "ติดเงื่อนไข")
DEFAULT_PROVIDERS = "claude"
DEFAULT_REVIEWER = "codex"
STATE_DIR = DEFAULT_OUTPUT_DIR / "linear"
TITLE_REPO_RE = re.compile(r"^\s*\[([A-Za-z0-9._-]+)\]\s*(.*)$")
MAX_ISSUES = 50
LEDGER_KEEP = 500
MAX_FAILED_ATTEMPTS = 3

ISSUE_QUERY = """
query BookDevIssues($label: String!, $first: Int!) {
  issues(
    first: $first
    orderBy: updatedAt
    filter: {
      labels: { some: { name: { eqIgnoreCase: $label } } }
      state: { type: { in: ["backlog", "unstarted"] } }
    }
  ) {
    nodes {
      identifier
      title
      description
      url
      priority
      dueDate
      createdAt
      updatedAt
      state { name type }
      project { name }
      labels { nodes { name } }
    }
  }
}
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _csv(raw: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if raw is None:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def fetch_issues(token: str, label: str) -> list[dict[str, Any]]:
    payload = call_linear(token, ISSUE_QUERY, {"label": label, "first": MAX_ISSUES})
    if has_graphql_error(payload):
        raise RuntimeError("linear_graphql_error")
    nodes = (((payload or {}).get("data") or {}).get("issues") or {}).get("nodes")
    if not isinstance(nodes, list):
        raise RuntimeError("linear_issues_missing_nodes")
    return [node for node in nodes if isinstance(node, dict)]


def _labels(issue: dict[str, Any]) -> list[str]:
    nodes = ((issue.get("labels") or {}).get("nodes")) or []
    return [str(node.get("name")) for node in nodes if isinstance(node, dict) and node.get("name")]


def _priority_rank(issue: dict[str, Any]) -> tuple[int, str]:
    # Linear: 1 urgent .. 4 low, 0 none. Unprioritised issues go last.
    priority = issue.get("priority") or 0
    return (priority if priority > 0 else 9, str(issue.get("createdAt") or ""))


def index_repositories(root: Path) -> dict[str, list[Path]]:
    """Map repository folder name to local checkouts (no Git commands)."""

    repos, _ = discover_repositories(root, max_repos=1000)
    index: dict[str, list[Path]] = {}
    for repo in repos:
        index.setdefault(repo.name, []).append(repo)
    return index


def issue_to_task(issue: dict[str, Any], ref: str) -> dict[str, Any]:
    match = TITLE_REPO_RE.match(issue.get("title") or "")
    summary = match.group(2) if match else issue.get("title")
    description = "\n".join(
        part for part in (
            f"Linear {issue.get('identifier')}: {issue.get('url')}",
            str(issue.get("description") or ""),
        ) if part
    )
    priority = issue.get("priority") or None
    return {
        "id": issue.get("identifier"),
        "title": f"[Book Dev] {ref} — {summary}",
        "description": description,
        "status": "todo",
        "priority": priority,
        "project": (issue.get("project") or {}).get("name") or "Linear",
        "due_date": issue.get("dueDate"),
        "updated_at": issue.get("updatedAt"),
        "parent_task_id": None,
    }


def plan_issues(
    issues: list[dict[str, Any]],
    repo_index: dict[str, list[Path]],
    *,
    book_dev_root: Path,
    hold_labels: tuple[str, ...],
    dispatched: set[str],
) -> tuple[list[tuple[dict[str, Any], Path]], list[dict[str, Any]]]:
    """Return runnable (task, repo) pairs in priority order, plus skipped issues."""

    holds = {label.casefold() for label in hold_labels}
    runnable: list[tuple[dict[str, Any], Path]] = []
    skipped: list[dict[str, Any]] = []
    for issue in sorted(issues, key=_priority_rank):
        identifier = issue.get("identifier")

        def skip(reason: str, **extra: Any) -> None:
            skipped.append({"issue": identifier, "reason": reason, **extra})

        if identifier in dispatched:
            skip("already_dispatched")
            continue
        held = [label for label in _labels(issue) if label.casefold() in holds]
        if held:
            skip("hold_label", labels=held)
            continue
        match = TITLE_REPO_RE.match(issue.get("title") or "")
        if not match:
            skip("title_without_repository_prefix")
            continue
        matches = repo_index.get(match.group(1), [])
        if len(matches) != 1:
            reason = "repository_not_found" if not matches else "repository_ambiguous"
            skip(reason, repository=match.group(1), match_count=len(matches))
            continue
        repo = matches[0]
        ref = _repo_ref(repo, book_dev_root) or match.group(1)
        runnable.append((issue_to_task(issue, ref), repo))
    return runnable, skipped


def load_state(state_dir: Path) -> dict[str, Any]:
    path = state_dir / "state.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
    return {"contract": CONTRACT, "dispatched": []}


def save_state(state_dir: Path, state: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    state["dispatched"] = state.get("dispatched", [])[-LEDGER_KEEP:]
    path = state_dir / "state.json"
    pending = path.with_suffix(".json.tmp")
    pending.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(pending, path)


def run_cycle(
    *,
    label: str,
    hold_labels: tuple[str, ...],
    providers: str,
    mode: str,
    dry_run: bool,
    max_turns: int = 24,
    timeout_sec: int = 1800,
    book_dev_root: Path = BOOK_DEV_ROOT,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    state_dir: Path = STATE_DIR,
    token: str | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "contract": CONTRACT,
        "generated_at": _now_iso(),
        "label": label,
        "mode": mode,
        "providers": providers,
        "reviewer": reviewer,
        "dry_run": dry_run,
    }
    token = token if token is not None else get_env("LINEAR_API_KEY")
    if not token:
        return {**report, "status": "not_configured", "missing": ["LINEAR_API_KEY"]}
    try:
        issues = fetch_issues(token, label)
    except Exception as exc:  # network, HTTP, or schema errors: report the class only
        return {**report, "status": "error", "error_class": safe_error(exc)}

    state = load_state(state_dir)
    dispatched = {item.get("issue") for item in state.get("dispatched", []) if isinstance(item, dict)}
    runnable, skipped = plan_issues(
        issues,
        index_repositories(book_dev_root),
        book_dev_root=book_dev_root,
        hold_labels=hold_labels,
        dispatched=dispatched,
    )
    report.update({"issue_count": len(issues), "runnable_count": len(runnable), "skipped": skipped})
    if not runnable:
        return {**report, "status": "idle"}

    tasks = [task for task, _ in runnable]
    # One snapshot per checkout: several issues may target the same repository.
    repos = list(dict.fromkeys(repo for _, repo in runnable))
    snapshots = [inspect_repository(repo, book_dev_root) for repo in repos]
    handoff = build_report(tasks, snapshots, max_tasks=len(tasks), book_dev_root=book_dev_root)
    # Only Linear issues are dispatched; dirty-tree reviews stay with the manual loop.
    handoff["candidates"] = [c for c in handoff["candidates"] if c.get("source") == "open_task"]
    handoff["outputs"] = write_outputs(handoff, output_dir)

    ticket = queue_execution(
        latest_json=output_dir / "latest.json",
        execution_dir=output_dir / "execution",
        write=True,
        force=True,
        dispatch_providers=providers,
        dispatch_mode=mode,
        dispatch_max_turns=max_turns,
        dispatch_timeout_sec=timeout_sec,
        dispatch_reviewer=reviewer,
        hook_dry_run=dry_run,
        report=handoff,
    )
    selected = ((ticket.get("selected") or {}).get("task") or {}).get("id")
    hook = ticket.get("hook") or {}
    report.update({
        "status": ticket.get("status"),
        "issue": selected,
        "dispatch_status": hook.get("status"),
        "dispatch_report": hook.get("report"),
        "ticket": (ticket.get("outputs") or {}).get("json"),
    })
    status = hook.get("status")
    if selected and not dry_run and hook.get("invoked") and status != "unavailable":
        attempts = state.setdefault("failed_attempts", {})
        if status == "failed":
            attempts[selected] = attempts.get(selected, 0) + 1
            report["failed_attempts"] = attempts[selected]
        if status != "failed" or attempts[selected] >= MAX_FAILED_ATTEMPTS:
            attempts.pop(selected, None)
            state.setdefault("dispatched", []).append({
                "issue": selected,
                "at": report["generated_at"],
                "status": status,
                "report": hook.get("report"),
            })
        save_state(state_dir, state)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dispatch one opted-in Linear issue to an AI provider")
    parser.add_argument("--label", default=os.environ.get("BOOK_DEV_LINEAR_LABEL", DEFAULT_LABEL))
    parser.add_argument(
        "--hold-labels",
        default=os.environ.get("BOOK_DEV_LINEAR_HOLD_LABELS"),
        help="Comma-separated labels that keep an issue out of dispatch",
    )
    parser.add_argument("--providers", default=os.environ.get("BOOK_DEV_DISPATCH_PROVIDERS", DEFAULT_PROVIDERS))
    parser.add_argument(
        "--mode",
        choices=["plan", "implement"],
        default=os.environ.get("BOOK_DEV_DISPATCH_MODE", "implement"),
    )
    parser.add_argument(
        "--reviewer",
        choices=["codex", "none"],
        default=os.environ.get("BOOK_DEV_DISPATCH_REVIEWER", DEFAULT_REVIEWER),
        help="Independent reviewer for the build-review flow; none dispatches one provider",
    )
    parser.add_argument("--max-turns", type=int, default=24)
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true", help="Queue the ticket and render commands only")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = run_cycle(
            label=args.label,
            hold_labels=_csv(args.hold_labels, DEFAULT_HOLD_LABELS),
            providers=args.providers,
            mode=args.mode,
            dry_run=args.dry_run,
            max_turns=args.max_turns,
            timeout_sec=args.timeout_sec,
            # Plan mode has nothing to review.
            reviewer=None if args.reviewer == "none" or args.mode == "plan" else args.reviewer,
        )
    except (OSError, ValueError) as exc:
        report = {"contract": CONTRACT, "status": "error", "error": str(exc)}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"Book Dev Linear cycle: status={report.get('status')} "
            f"issue={report.get('issue') or '-'} dispatch={report.get('dispatch_status') or '-'}"
        )
    return 1 if report.get("status") in {"error", "hook_failed"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
