#!/usr/bin/env python3
"""Build a safe daily Book Dev engineering-loop handoff.

The workflow discovers open Book Dev tasks and inspects the local nested Git
repositories without modifying them.  It writes a bounded execution packet
for an AI client or human operator; it does not call a model, create tasks,
change task status, commit, push, merge, delete, or publish anything.

Usage:
    python3 book_dev_loop.py --json
    python3 book_dev_loop.py --write --json
    python3 book_dev_loop.py --write --max-tasks 1
    python3 book_dev_loop.py --write --queue-execution --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

WORKFLOW_DIR = Path(__file__).resolve().parent
UTILS_DIR = WORKFLOW_DIR.parent / "utils"
if str(UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(UTILS_DIR))
if str(WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_DIR))

from _env import PROJECT_ROOT, book_dev_tree  # noqa: E402
from book_dev_execution_hook import queue_execution  # noqa: E402
from task_store import open_task_store  # noqa: E402

AUTOMATION_ID = "portfolio-reader"


CONTRACT = "solo-empire.book-dev-engineering-loop.v1"
BOOK_DEV_ROOT = book_dev_tree() or PROJECT_ROOT / "book-dev-unavailable"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "runtime" / "agent-loop" / "book-dev"
SKIP_DIRS = {".git", "node_modules", ".next", "dist", "build", "coverage", ".dart_tool"}
REPO_REF_RE = re.compile(r"\[Book Dev\]\s+([^\s—]+/[^\s—]+)")
MAX_UNMATCHED = 50
# Task descriptions carry the acceptance checklist the planner, builder and reviewer work from, so
# they keep their lines and get a generous cap (Thai is ~3 bytes per character).
DESCRIPTION_LIMIT = 12_000
TRUNCATED_MARK = "(truncated)"


def _clean(value: Any, limit: int = 280) -> str:
    """Return one bounded, whitespace-normalized text value."""

    return " ".join(str(value or "").split())[:limit].rstrip()


def _clean_block(value: Any, limit: int = DESCRIPTION_LIMIT) -> str:
    """Return multi-line text with each line's whitespace normalized and blank runs collapsed.

    Line breaks survive so a markdown checklist stays one item per line. When the text exceeds
    ``limit`` characters it is cut and ends with an explicit ``(truncated)`` marker.
    """

    lines: list[str] = []
    for raw in str(value or "").splitlines():
        line = " ".join(raw.split())
        if line or (lines and lines[-1]):
            lines.append(line)
    text = "\n".join(lines).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n{TRUNCATED_MARK}"


def _run_git(repo: Path, args: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """Run a read-only Git command without invoking a shell."""

    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", _clean(exc, 180)
    return result.returncode, result.stdout.strip(), _clean(result.stderr, 180)


def discover_repositories(root: Path, max_repos: int = 300) -> tuple[list[Path], bool]:
    """Find nested repositories at the Book Dev boundary, without entering them."""

    if not root.is_dir():
        return [], False

    repositories: list[Path] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        if ".git" in dirs or ".git" in files:
            repositories.append(current_path)
            # A nested checkout is a hard boundary.  Do not walk node_modules,
            # worktrees, or repositories contained inside the checkout.
            dirs[:] = []
            if len(repositories) >= max_repos:
                return sorted(repositories), True
            continue
        dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS)
    return sorted(repositories), False


def _repo_ref(repo: Path, root: Path) -> str | None:
    """Build the stable ``owner/repository`` reference used in task titles."""

    try:
        parts = repo.relative_to(root).parts
    except ValueError:
        return None
    if len(parts) < 3:
        return None
    return f"{parts[1]}/{repo.name}"


def inspect_repository(repo: Path, root: Path) -> dict[str, Any]:
    """Collect bounded, read-only repository evidence."""

    ref = _repo_ref(repo, root)
    # Clones sit beside this checkout, so the path may climb out of PROJECT_ROOT;
    # consumers resolve it as PROJECT_ROOT / path.
    relative = Path(os.path.relpath(repo, PROJECT_ROOT)).as_posix()
    status_code, status_text, status_error = _run_git(
        repo, ["status", "--porcelain=v1", "--untracked-files=normal"]
    )
    branch_code, branch, _ = _run_git(repo, ["branch", "--show-current"])
    upstream_code, upstream, _ = _run_git(
        repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
    )

    if status_code != 0:
        return {
            "ref": ref,
            "path": relative,
            "state": "unreadable",
            "dirty": None,
            "dirty_file_count": None,
            "branch": _clean(branch) if branch_code == 0 else None,
            "upstream": _clean(upstream) if upstream_code == 0 else None,
            "error_class": status_error or "git_status_failed",
        }

    status_lines = [line for line in status_text.splitlines() if line.strip()]
    return {
        "ref": ref,
        "path": relative,
        "state": "dirty" if status_lines else "clean",
        "dirty": bool(status_lines),
        "dirty_file_count": len(status_lines),
        "branch": _clean(branch) if branch_code == 0 and branch else None,
        "upstream": _clean(upstream) if upstream_code == 0 and upstream else None,
        "has_upstream": upstream_code == 0 and bool(upstream),
    }


def _ascii_lower(value: Any) -> str:
    """Lower-case only A-Z, as SQLite lower() and LIKE do."""

    return str(value or "").translate({code: code + 32 for code in range(ord("A"), ord("Z") + 1)})


def _is_book_dev_task(task: dict[str, Any]) -> bool:
    """The three filters the SQL used: title prefix, project %book%dev%, description %book dev%."""

    return (
        _ascii_lower(task["title"]).startswith("[book dev]")
        or re.search(r"book.*dev", _ascii_lower(task["project"]), re.DOTALL) is not None
        or "book dev" in _ascii_lower(task["description"])
    )


def load_book_dev_tasks() -> list[dict[str, Any]]:
    """Load the local canonical Book Dev task queue."""

    store = open_task_store(None, AUTOMATION_ID)
    status_rank = {"in_progress": 0, "todo": 1, "blocked": 2}
    rows = [
        task for task in store.list(include_archived=True)
        if task["status"] not in ("done", "cancelled") and _is_book_dev_task(task)
    ]
    rows.sort(key=lambda task: (
        status_rank.get(task["status"], 3),
        task["priority"] is not None, task["priority"] if task["priority"] is not None else 0,
        task["due_date"] is None, task["due_date"] or "",
        task["id"],
    ))
    fields = ("id", "title", "description", "status", "priority", "project", "due_date", "updated_at", "parent_task_id")
    return [{field: task[field] for field in fields} for task in rows]


def extract_repo_ref(title: str) -> str | None:
    """Extract the owner/repository reference from a Book Dev task title."""

    match = REPO_REF_RE.search(title or "")
    return match.group(1).strip() if match else None


def _repo_index(repositories: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for repo in repositories:
        ref = repo.get("ref")
        if ref:
            index.setdefault(ref, []).append(repo)
    return index


def _context_files(repo: dict[str, Any] | None) -> list[str]:
    """Return only files that exist and are useful to the handoff."""

    files = [
        "repository-catalog/portfolio/BOOK-DEV-BACKLOG-BD.md",
        "repository-catalog/audits/BOOK-DEV-NESTED-TASK-CHECK-2026-08-11.md",
    ]
    if repo:
        repo_path = PROJECT_ROOT / repo["path"]
        for filename in ("README.md", "PRODUCT.md", "BOUNDARY.md", "TRUTH-PASS.md"):
            if (repo_path / filename).is_file():
                files.append(f"{repo['path']}/{filename}")
    return [path for path in files if (PROJECT_ROOT / path).is_file()]


def _recommendation(task: dict[str, Any], repo: dict[str, Any] | None, ambiguous: bool) -> str:
    if task["status"] == "blocked":
        return "Review the dependency or approval gate; do not execute the task."
    if ambiguous:
        return "Resolve the duplicate repository reference before execution; do not guess."
    if repo is None:
        return "Resolve the missing local checkout or task-to-repository mapping; do not clone automatically."
    if repo["state"] == "unreadable":
        return "Inspect the Git error class and stop until the repository can be read safely."
    if repo["dirty"]:
        return "Inspect the existing dirty diff and map it to this task before making any change."
    return "Open the repository, implement only this bounded task, then run the narrowest relevant verification."


def _agent_prompt(task: dict[str, Any], repo: dict[str, Any] | None, recommendation: str) -> str:
    repo_line = repo["path"] if repo else "the repository path resolved from the task reference"
    return (
        "You are the Book Dev engineering-loop implementer. Work on exactly one task.\n"
        f"Task: #{task['id']} {_clean(task['title'])}\n"
        f"Repository: {repo_line}\n"
        f"Required first action: {recommendation}\n"
        "Read the Book Dev loop skill and the listed context files before editing. "
        "Use the repository's own tests and conventions. Keep the change narrow. "
        "Do not touch another nested repository or the parent catalog. "
        "Stop and ask the owner before commit, push, merge, archive, transfer, delete, "
        "deployment, credential use, or when the dirty tree contains unrelated work."
    )


def build_candidate(
    task: dict[str, Any],
    repo_matches: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build one bounded AI handoff packet from deterministic evidence."""

    ambiguous = len(repo_matches) > 1
    repo = repo_matches[0] if len(repo_matches) == 1 else None
    recommendation = _recommendation(task, repo, ambiguous)
    repo_ref = extract_repo_ref(task.get("title", ""))
    return {
        "task": {
            "id": task["id"],
            "title": _clean(task["title"]),
            "description": _clean_block(task.get("description")),
            "status": task["status"],
            "priority": task["priority"],
            "project": _clean(task.get("project"), 120),
            "due_date": task.get("due_date"),
            "updated_at": task.get("updated_at"),
        },
        "repository": repo,
        "repository_ref": repo_ref,
        "recommendation": recommendation,
        "context_files": _context_files(repo),
        "verification": [
            "Capture the exact verification command and its output.",
            "Check the final diff against the task acceptance evidence.",
            "Record remaining blockers instead of claiming completion.",
        ],
        "approval_gate": [
            "No commit, push, merge, archive, transfer, delete, deploy, or issue closure without explicit owner approval.",
            "No automatic cleanup or reset of a dirty working tree.",
        ],
        "agent_prompt": _agent_prompt(task, repo, recommendation),
        "source": "open_task",
    }


def build_dirty_repo_candidate(repo: dict[str, Any]) -> dict[str, Any]:
    """Build a synthetic candidate when open Book Dev tasks are exhausted."""

    ref = repo.get("ref") or "unknown/repository"
    task = {
        "id": None,
        "title": f"[Book Dev] {ref} — review dirty changes",
        "description": (
            "Synthetic execution-loop candidate generated from a dirty nested "
            "repository with no matching open local task."
        ),
        "status": "todo",
        "priority": 3,
        "project": "Book Dev / Dirty-Tree Review",
        "due_date": None,
        "updated_at": None,
    }
    recommendation = (
        "Inspect the existing dirty diff, decide keep/commit-ready/discard with "
        "owner approval, and do not invent an open task database write."
    )
    return {
        "task": task,
        "repository": repo,
        "repository_ref": ref,
        "recommendation": recommendation,
        "context_files": _context_files(repo),
        "verification": [
            "Capture git status and a bounded diff summary.",
            "Record the disposition recommendation without auto-committing.",
            "Record remaining blockers instead of claiming completion.",
        ],
        "approval_gate": [
            "No commit, push, merge, archive, transfer, delete, deploy, or task creation without explicit owner approval.",
            "No automatic cleanup or reset of a dirty working tree.",
        ],
        "agent_prompt": _agent_prompt(task, repo, recommendation),
        "source": "dirty_repository_without_open_task",
    }


def build_report(
    tasks: list[dict[str, Any]],
    repositories: list[dict[str, Any]],
    *,
    scan_truncated: bool = False,
    max_tasks: int = 5,
    generated_at: str | None = None,
    book_dev_root: Path = BOOK_DEV_ROOT,
) -> dict[str, Any]:
    """Build a stable, provider-neutral loop report without side effects."""

    index = _repo_index(repositories)
    open_tasks = [task for task in tasks if task["status"] != "blocked"]
    blocked_tasks = [task for task in tasks if task["status"] == "blocked"]
    candidates: list[dict[str, Any]] = []
    unmatched_tasks: list[dict[str, Any]] = []
    task_refs: set[str] = set()

    for task in tasks:
        ref = extract_repo_ref(task.get("title", ""))
        if ref:
            task_refs.add(ref)
        matches = index.get(ref, []) if ref else []
        if task["status"] != "blocked" and (ref is None or len(matches) != 1):
            unmatched_tasks.append(
                {
                    "task_id": task["id"],
                    "title": _clean(task["title"]),
                    "repository_ref": ref,
                    "match_count": len(matches),
                }
            )
        if len(candidates) < max_tasks and task["status"] != "blocked":
            candidates.append(build_candidate(task, matches))

    dirty_without_task = [
        repo for repo in repositories
        if repo.get("dirty") and repo.get("ref") not in task_refs
    ]
    # Prefer open tasks. When the task queue is empty or short, keep the outer
    # loop useful by promoting dirty repositories without open tasks.
    if len(candidates) < max_tasks:
        ranked_dirty = sorted(
            dirty_without_task,
            key=lambda repo: (
                0 if repo.get("has_upstream") else 1,
                -(repo.get("dirty_file_count") or 0),
                repo.get("ref") or "",
            ),
        )
        seen_paths = {
            (item.get("repository") or {}).get("path")
            for item in candidates
            if isinstance(item.get("repository"), dict)
        }
        for repo in ranked_dirty:
            if len(candidates) >= max_tasks:
                break
            path = repo.get("path")
            if path in seen_paths:
                continue
            candidates.append(build_dirty_repo_candidate(repo))
            seen_paths.add(path)

    clean_count = sum(1 for repo in repositories if repo.get("state") == "clean")
    dirty_count = sum(1 for repo in repositories if repo.get("state") == "dirty")
    unreadable_count = sum(1 for repo in repositories if repo.get("state") == "unreadable")
    no_upstream_count = sum(
        1 for repo in repositories
        if repo.get("state") != "unreadable" and not repo.get("has_upstream")
    )
    task_candidate_count = sum(1 for item in candidates if item.get("source") == "open_task")
    dirty_candidate_count = sum(
        1 for item in candidates if item.get("source") == "dirty_repository_without_open_task"
    )

    report = {
        "contract": CONTRACT,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "source": {
            "task_store": "tasks cell API",
            "task_scope": "Book Dev open local tasks",
            "book_dev_root": _path_label(book_dev_root),
            "repository_scan": "read-only Git status and branch metadata",
        },
        "summary": {
            "open_task_count": len(tasks),
            "runnable_task_count": len(open_tasks),
            "blocked_task_count": len(blocked_tasks),
            "repository_count": len(repositories),
            "clean_repository_count": clean_count,
            "dirty_repository_count": dirty_count,
            "unreadable_repository_count": unreadable_count,
            "no_upstream_repository_count": no_upstream_count,
            "candidate_count": len(candidates),
            "open_task_candidate_count": task_candidate_count,
            "dirty_repository_candidate_count": dirty_candidate_count,
            "unmatched_open_task_count": len(unmatched_tasks),
            "dirty_repository_without_open_task_count": len(dirty_without_task),
            "repository_scan_truncated": scan_truncated,
        },
        "candidates": candidates,
        "discovery": {
            "open_tasks_without_unique_repository": unmatched_tasks[:MAX_UNMATCHED],
            "dirty_repositories_without_open_task": dirty_without_task[:MAX_UNMATCHED],
        },
        "workflow": {
            "moves": [
                "discovery",
                "bounded_handoff",
                "execution_ticket_queue",
                "optional_external_ai_hook",
                "human-approved_execution",
                "independent_verification",
                "state_persistence",
                "next_daily_trigger",
            ],
            "trigger": "daily cron job book_dev_loop",
            "handoff_file": "data/runtime/agent-loop/book-dev/latest.md",
            "execution_ticket": "data/runtime/agent-loop/book-dev/execution/current.md",
            "state_file": "data/runtime/agent-loop/book-dev/state.json",
            "ai_provider_call": False,
        },
        "policy": {
            "scan_read_only": True,
            "nested_repository_writes": False,
            "task_database_writes": False,
            "automatic_task_creation": False,
            "automatic_status_updates": False,
            "automatic_commit_push_merge": False,
            "execution_hook_default": "queue_only",
            "external_ai_hook_opt_in": "BOOK_DEV_AI_HOOK + --invoke-hook",
            "max_tasks_per_run": max_tasks,
            "human_approval_required_for_irreversible_actions": True,
        },
        "next_action": (
            "Read data/runtime/agent-loop/book-dev/execution/current.md (or latest.md), "
            "execute at most one candidate with skills/book-dev-engineering-loop/SKILL.md, "
            "and stop before irreversible Git actions."
        ),
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    """Render the bounded handoff packet for an AI client or human."""

    summary = report["summary"]
    lines = [
        "# Book Dev Engineering Loop",
        "",
        "> Generated from the canonical local task database and a read-only nested Git scan.",
        "> This file is a handoff packet; it does not authorize changes, commits, pushes, or merges.",
        "",
        f"Generated: `{report['generated_at']}`  ",
        f"Contract: `{report['contract']}`",
        "",
        "## Discovery summary",
        "",
        f"- Open Book Dev tasks: **{summary['open_task_count']}** ({summary['blocked_task_count']} blocked)",
        f"- Repositories scanned: **{summary['repository_count']}** — {summary['clean_repository_count']} clean / {summary['dirty_repository_count']} dirty / {summary['unreadable_repository_count']} unreadable",
        f"- Repositories without upstream: **{summary['no_upstream_repository_count']}**",
        f"- Dirty repositories without an open task: **{summary['dirty_repository_without_open_task_count']}**",
        "",
        "## AI handoff rules",
        "",
        "1. Read `skills/book-dev-engineering-loop/SKILL.md` before executing a candidate.",
        "2. Select one candidate only and keep the change inside its nested repository.",
        "3. Preserve unrelated dirty work; never reset or clean a working tree.",
        "4. Capture verification evidence and stop before commit, push, merge, archive, transfer, delete, or deploy.",
        "",
        "## Candidate queue",
        "",
    ]

    candidates = report["candidates"]
    if not candidates:
        lines.append("_No runnable Book Dev task was discovered._")
    else:
        for index, candidate in enumerate(candidates, 1):
            task = candidate["task"]
            repo = candidate["repository"]
            repo_display = repo["path"] if repo else (candidate["repository_ref"] or "unresolved")
            repo_state = repo["state"] if repo else "unresolved"
            lines.extend(
                [
                    f"### {index}. #{task['id']} P{task['priority']} — {task['title']}",
                    "",
                    f"- Status: `{task['status']}`",
                    f"- Repository: `{repo_display}` (`{repo_state}`)",
                    f"- Recommendation: {candidate['recommendation']}",
                    f"- Context: {', '.join(f'`{item}`' for item in candidate['context_files']) or 'none found'}",
                    "",
                ]
            )

    lines.extend(
        [
            "## Persistent state",
            "",
            "The machine-readable report is `latest.json`. The daily cron job may refresh both files; it never edits nested repositories or the task database.",
            "",
            "When `--queue-execution` is enabled, the outer hook also writes a single-candidate ticket at `execution/current.md`.",
            "",
            f"Next action: {report['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def _path_label(path: Path) -> str:
    """Prefer a repository-relative output path, including test temp paths safely."""

    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def write_outputs(report: dict[str, Any], output_dir: Path) -> dict[str, str]:
    """Write ignored runtime artifacts and a small state pointer."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "latest.json"
    markdown_path = output_dir / "latest.md"
    state_path = output_dir / "state.json"
    outputs = {
        "json": _path_label(json_path),
        "markdown": _path_label(markdown_path),
        "state": _path_label(state_path),
    }
    report["outputs"] = outputs
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    json_path.write_text(payload, encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    state = {
        "contract": "solo-empire.book-dev-engineering-loop-state.v1",
        "last_run_at": report["generated_at"],
        "last_report_sha256": digest,
        "latest_json": _path_label(json_path),
        "latest_markdown": _path_label(markdown_path),
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return outputs


def run_loop(
    *,
    book_dev_root: Path = BOOK_DEV_ROOT,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    max_tasks: int = 5,
    max_repos: int = 300,
    write: bool = False,
    queue_execution_ticket: bool = False,
    force_execution: bool = False,
    invoke_hook: bool = False,
    hook_dry_run: bool = False,
    dispatch_providers: str | None = None,
    dispatch_mode: str = "implement",
) -> dict[str, Any]:
    """Run the complete deterministic discovery, handoff, and optional execution queue."""

    if max_tasks < 1 or max_tasks > 50:
        raise ValueError("max_tasks must be between 1 and 50")
    if max_repos < 1 or max_repos > 1000:
        raise ValueError("max_repos must be between 1 and 1000")

    repositories, scan_truncated = discover_repositories(book_dev_root, max_repos=max_repos)
    snapshots = [inspect_repository(repo, book_dev_root) for repo in repositories]
    tasks = load_book_dev_tasks()
    report = build_report(
        tasks,
        snapshots,
        scan_truncated=scan_truncated,
        max_tasks=max_tasks,
        book_dev_root=book_dev_root,
    )
    if write:
        report["outputs"] = write_outputs(report, output_dir)
        if queue_execution_ticket or dispatch_providers:
            execution_dir = output_dir / "execution"
            ticket = queue_execution(
                latest_json=output_dir / "latest.json",
                execution_dir=execution_dir,
                write=True,
                force=force_execution or bool(dispatch_providers),
                invoke_hook=invoke_hook or hook_dry_run,
                hook_dry_run=hook_dry_run,
                dispatch_providers=dispatch_providers,
                dispatch_mode=dispatch_mode,
                report=report,
            )
            report["execution"] = {
                "status": ticket.get("status"),
                "task_id": ((ticket.get("selected") or {}).get("task") or {}).get("id"),
                "hook_status": (ticket.get("hook") or {}).get("status"),
                "outputs": ticket.get("outputs"),
                "next_action": ticket.get("next_action"),
                "dispatch": ticket.get("dispatch") or ticket.get("hook"),
            }
            report["outputs"]["execution_json"] = (ticket.get("outputs") or {}).get("json")
            report["outputs"]["execution_markdown"] = (ticket.get("outputs") or {}).get("markdown")
    elif queue_execution_ticket or dispatch_providers:
        raise ValueError("--queue-execution/--dispatch requires --write so the handoff packet exists")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a safe daily Book Dev AI handoff")
    parser.add_argument("--write", action="store_true", help="Write latest.json, latest.md, and state.json")
    parser.add_argument("--json", action="store_true", help="Print the machine-readable report")
    parser.add_argument(
        "--queue-execution",
        action="store_true",
        help="After writing the handoff, queue one bounded execution ticket",
    )
    parser.add_argument(
        "--force-execution",
        action="store_true",
        help="Replace an open execution ticket for the same candidate fingerprint",
    )
    parser.add_argument(
        "--invoke-hook",
        action="store_true",
        help="Opt-in: run $BOOK_DEV_AI_HOOK after queueing the execution ticket",
    )
    parser.add_argument(
        "--hook-dry-run",
        action="store_true",
        help="Render the external AI hook command without executing it",
    )
    parser.add_argument(
        "--dispatch",
        metavar="PROVIDERS",
        help="After queueing, dispatch to providers (e.g. grok,codex)",
    )
    parser.add_argument(
        "--dispatch-mode",
        choices=["plan", "implement"],
        default="implement",
        help="Dispatch mode when --dispatch is set",
    )
    parser.add_argument("--max-tasks", type=int, default=5)
    parser.add_argument("--max-repos", type=int, default=300)
    parser.add_argument("--book-dev-root", type=Path, default=BOOK_DEV_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = run_loop(
            book_dev_root=args.book_dev_root,
            output_dir=args.output_dir,
            max_tasks=args.max_tasks,
            max_repos=args.max_repos,
            write=args.write or bool(args.dispatch),
            queue_execution_ticket=args.queue_execution or bool(args.dispatch),
            force_execution=args.force_execution or bool(args.dispatch),
            invoke_hook=args.invoke_hook,
            hook_dry_run=args.hook_dry_run,
            dispatch_providers=args.dispatch,
            dispatch_mode=args.dispatch_mode,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"contract": CONTRACT, "status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = report["summary"]
        print(
            "Book Dev loop: "
            f"{summary['candidate_count']} candidate(s), "
            f"{summary['repository_count']} repo(s), "
            f"{summary['dirty_repository_count']} dirty, "
            f"{summary['blocked_task_count']} blocked"
        )
        if args.write:
            print(json.dumps(report.get("outputs", {}), ensure_ascii=False))
        execution = report.get("execution")
        if execution:
            print(
                "Execution hook: "
                f"status={execution.get('status')} "
                f"task=#{execution.get('task_id') or '-'} "
                f"hook={execution.get('hook_status')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
