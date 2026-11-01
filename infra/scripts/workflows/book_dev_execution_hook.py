#!/usr/bin/env python3
"""Queue one Book Dev engineering-loop candidate for AI execution.

This is the outer execution hook for loop engineering:
  discovery handoff → select one candidate → write execution ticket → optional hook

It never edits nested repositories, never commits/pushes, and never calls a
model by default. Optional dispatch to Codex/Grok is opt-in via ``--dispatch``
or ``--invoke-hook`` + ``BOOK_DEV_AI_HOOK``.

Usage:
    python3 book_dev_execution_hook.py --json
    python3 book_dev_execution_hook.py --write --json
    python3 book_dev_execution_hook.py --write --dispatch grok,codex --json
    python3 book_dev_execution_hook.py --write --invoke-hook --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKFLOW_DIR = Path(__file__).resolve().parent
UTILS_DIR = WORKFLOW_DIR.parent / "utils"
if str(UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(UTILS_DIR))
if str(WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_DIR))

from _env import PROJECT_ROOT  # noqa: E402

CONTRACT = "solo-empire.book-dev-engineering-loop-execution.v1"
STATE_CONTRACT = "solo-empire.book-dev-engineering-loop-execution-state.v1"
DEFAULT_LOOP_DIR = PROJECT_ROOT / "data" / "runtime" / "agent-loop" / "book-dev"
DEFAULT_EXECUTION_DIR = DEFAULT_LOOP_DIR / "execution"
HOOK_ENV = "BOOK_DEV_AI_HOOK"
DEFAULT_HOOK_SCRIPT = WORKFLOW_DIR / "book_dev_ai_hook.sh"
HISTORY_KEEP = 20
OPEN_STATUSES = {"queued", "dispatched", "in_progress"}


def _clean(value: Any, limit: int = 280) -> str:
    return " ".join(str(value or "").split())[:limit].rstrip()


def _path_label(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_handoff(latest_json: Path) -> dict[str, Any]:
    """Load the discovery handoff packet."""

    if not latest_json.is_file():
        raise FileNotFoundError(f"missing handoff packet: {_path_label(latest_json)}")
    data = json.loads(latest_json.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("handoff packet must be a JSON object")
    return data


def load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def select_candidate(report: dict[str, Any]) -> dict[str, Any] | None:
    """Pick the first runnable candidate with a unique resolved repository.

    Open-task candidates are preferred. Synthetic dirty-repository candidates
    are accepted so the outer loop still queues work when the task queue is empty.
    """

    candidates = report.get("candidates") or []
    if not isinstance(candidates, list):
        return None

    def _is_runnable(candidate: dict[str, Any]) -> bool:
        task = candidate.get("task") or {}
        if task.get("status") == "blocked":
            return False
        repo = candidate.get("repository")
        if not isinstance(repo, dict) or not repo.get("path"):
            return False
        if repo.get("state") == "unreadable":
            return False
        return True

    open_task = [
        candidate for candidate in candidates
        if isinstance(candidate, dict)
        and candidate.get("source", "open_task") == "open_task"
        and _is_runnable(candidate)
    ]
    if open_task:
        return open_task[0]

    dirty = [
        candidate for candidate in candidates
        if isinstance(candidate, dict)
        and candidate.get("source") == "dirty_repository_without_open_task"
        and _is_runnable(candidate)
    ]
    if dirty:
        return dirty[0]

    for candidate in candidates:
        if isinstance(candidate, dict) and _is_runnable(candidate):
            return candidate
    return None


def candidate_fingerprint(candidate: dict[str, Any]) -> str:
    task = candidate.get("task") or {}
    repo = candidate.get("repository") or {}
    payload = {
        "task_id": task.get("id"),
        "title": task.get("title"),
        "repo_path": repo.get("path"),
        "repo_state": repo.get("state"),
        "recommendation": candidate.get("recommendation"),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_agent_startup_prompt(candidate: dict[str, Any], ticket_path: str) -> str:
    task = candidate.get("task") or {}
    repo = candidate.get("repository") or {}
    task_label = f"#{task.get('id')}" if task.get("id") is not None else "synthetic"
    return (
        "You are closing one Book Dev engineering-loop execution ticket.\n"
        f"Ticket: {ticket_path}\n"
        f"Task: {task_label} {_clean(task.get('title'))}\n"
        f"Repository: {repo.get('path')}\n"
        "Required steps:\n"
        "1. Read skills/book-dev-engineering-loop/SKILL.md\n"
        f"2. Read {ticket_path} and the linked context files\n"
        "3. Inspect the nested repository with git status before editing\n"
        "4. Preserve unrelated dirty work\n"
        "5. Implement only this one candidate\n"
        "6. Run the narrowest verification available\n"
        "7. After verification, follow the automatic release closeout: commit scoped files,\n"
        "   push the configured upstream, deploy through the documented target, and verify it\n"
        "8. Stop before production/destructive release actions or when a required gate is missing\n"
        "9. Return commit/push/deploy evidence and whether owner approval is required next"
    )


def build_ticket(
    *,
    report: dict[str, Any],
    candidate: dict[str, Any] | None,
    generated_at: str | None = None,
    force: bool = False,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a bounded execution ticket from the discovery report."""

    generated = generated_at or _now_iso()
    stop_conditions = [
        "Exactly one candidate may be executed from this ticket.",
        "Stop when verification evidence is collected for the selected task.",
        "After verification, complete the scoped commit/push/documented deploy closeout; stop before production, destructive, or ambiguous release actions.",
        "Stop if the nested repository is unreadable or the dirty tree is unrelated and unsafe to touch.",
        "Do not start a second candidate while this ticket is open.",
    ]
    policy = {
        "max_candidates": 1,
        "ai_provider_call_default": False,
        "nested_repository_writes_by_hook": False,
        "task_database_writes": False,
        "automatic_release_closeout": True,
        "automatic_merge": False,
        "human_approval_required_for_irreversible_actions": True,
        "timeout_minutes": 60,
        "token_budget": None,
        "external_hook_env": HOOK_ENV,
    }

    if candidate is None:
        return {
            "contract": CONTRACT,
            "generated_at": generated,
            "status": "skipped_no_candidate",
            "reason": "No runnable candidate with a unique resolved repository was available.",
            "selected": None,
            "source": {
                "handoff_contract": report.get("contract"),
                "handoff_generated_at": report.get("generated_at"),
                "handoff_sha256": None,
            },
            "policy": policy,
            "stop_conditions": stop_conditions,
            "hook": {
                "configured": bool(os.environ.get(HOOK_ENV, "").strip()),
                "invoked": False,
                "status": "not_requested",
            },
            "next_action": (
                "Wait for the next discovery loop run or broaden open Book Dev tasks "
                "with unique nested-repository mappings."
            ),
        }

    fingerprint = candidate_fingerprint(candidate)
    if (
        not force
        and previous
        and previous.get("status") in OPEN_STATUSES
        and previous.get("fingerprint") == fingerprint
    ):
        return {
            "contract": CONTRACT,
            "generated_at": generated,
            "status": "skipped_existing_open",
            "reason": "An open execution ticket already covers the same candidate fingerprint.",
            "selected": previous.get("selected"),
            "fingerprint": fingerprint,
            "previous_status": previous.get("status"),
            "previous_generated_at": previous.get("generated_at"),
            "source": previous.get("source") or {},
            "policy": policy,
            "stop_conditions": stop_conditions,
            "hook": {
                "configured": bool(os.environ.get(HOOK_ENV, "").strip()),
                "invoked": False,
                "status": "not_requested",
            },
            "next_action": (
                "Execute the existing open ticket with skills/book-dev-engineering-loop/SKILL.md, "
                "or re-queue with --force after it is completed or abandoned."
            ),
            "outputs": previous.get("outputs"),
        }

    task = candidate.get("task") or {}
    repo = candidate.get("repository") or {}
    return {
        "contract": CONTRACT,
        "generated_at": generated,
        "status": "queued",
        "reason": "Selected the top runnable candidate from the discovery handoff.",
        "fingerprint": fingerprint,
        "selected": candidate,
        "source": {
            "handoff_contract": report.get("contract"),
            "handoff_generated_at": report.get("generated_at"),
            "candidate_count": (report.get("summary") or {}).get("candidate_count"),
        },
        "policy": policy,
        "stop_conditions": stop_conditions,
        "verification": candidate.get("verification") or [],
        "approval_gate": candidate.get("approval_gate") or [],
        "agent_prompt": candidate.get("agent_prompt") or "",
        "agent_startup_prompt": None,  # filled after write with real path
        "hook": {
            "configured": bool(os.environ.get(HOOK_ENV, "").strip()),
            "invoked": False,
            "status": "not_requested",
            "env_var": HOOK_ENV,
        },
        "next_action": (
            f"Pick up {'task #' + str(task.get('id')) if task.get('id') is not None else 'synthetic dirty-repo review'} "
            f"in `{repo.get('path')}` using skills/book-dev-engineering-loop/SKILL.md, then complete "
            "the scoped commit/push/documented deploy closeout after verification."
        ),
    }


def render_execution_markdown(ticket: dict[str, Any]) -> str:
    selected = ticket.get("selected") or {}
    task = selected.get("task") or {}
    repo = selected.get("repository") or {}
    lines = [
        "# Book Dev Execution Ticket",
        "",
        "> Outer loop-engineering hook. This ticket authorizes inspection and bounded implementation only.",
        "> After verification, scoped commit/push and a documented non-production deploy are part of closeout; merge, production, destructive, and ambiguous release actions remain gated.",
        "",
        f"Generated: `{ticket.get('generated_at')}`  ",
        f"Contract: `{ticket.get('contract')}`  ",
        f"Status: `{ticket.get('status')}`",
        "",
        f"Reason: {ticket.get('reason')}",
        "",
    ]

    if not selected:
        lines.extend(
            [
                "## Selected candidate",
                "",
                "_No candidate was queued._",
                "",
                f"Next action: {ticket.get('next_action')}",
                "",
            ]
        )
        return "\n".join(lines)

    context = selected.get("context_files") or []
    lines.extend(
        [
            "## Selected candidate",
            "",
            (
                f"- Task: `{'#' + str(task.get('id')) if task.get('id') is not None else 'synthetic'}` "
                f"P{task.get('priority')} — {task.get('title')}"
            ),
            f"- Source: `{selected.get('source') or 'open_task'}`",
            f"- Status: `{task.get('status')}`",
            f"- Repository: `{repo.get('path')}` (`{repo.get('state')}`)",
            f"- Dirty files: `{repo.get('dirty_file_count')}`",
            f"- Recommendation: {selected.get('recommendation')}",
            f"- Context: {', '.join(f'`{item}`' for item in context) or 'none found'}",
            "",
            "## Agent startup prompt",
            "",
            "```text",
            ticket.get("agent_startup_prompt") or ticket.get("agent_prompt") or "",
            "```",
            "",
            "## Stop conditions",
            "",
        ]
    )
    for item in ticket.get("stop_conditions") or []:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Approval gate",
            "",
        ]
    )
    for item in ticket.get("approval_gate") or []:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Hook",
            "",
            f"- External hook env: `{HOOK_ENV}`",
            f"- Hook status: `{(ticket.get('hook') or {}).get('status')}`",
            f"- Invoked: `{(ticket.get('hook') or {}).get('invoked')}`",
            "",
            f"Next action: {ticket.get('next_action')}",
            "",
        ]
    )
    return "\n".join(lines)


def _rotate_history(history_dir: Path, keep: int = HISTORY_KEEP) -> None:
    if not history_dir.is_dir():
        return
    files = sorted(history_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in files[keep:]:
        try:
            stale.unlink()
        except OSError:
            continue


def write_ticket(ticket: dict[str, Any], execution_dir: Path) -> dict[str, str]:
    """Persist current ticket, markdown brief, state pointer, and history copy."""

    execution_dir.mkdir(parents=True, exist_ok=True)
    history_dir = execution_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    json_path = execution_dir / "current.json"
    markdown_path = execution_dir / "current.md"
    state_path = execution_dir / "state.json"
    outputs = {
        "json": _path_label(json_path),
        "markdown": _path_label(markdown_path),
        "state": _path_label(state_path),
    }

    ticket = dict(ticket)
    ticket["outputs"] = outputs
    if ticket.get("selected"):
        ticket["agent_startup_prompt"] = build_agent_startup_prompt(
            ticket["selected"],
            outputs["markdown"],
        )

    payload = json.dumps(ticket, ensure_ascii=False, indent=2) + "\n"
    json_path.write_text(payload, encoding="utf-8")
    markdown_path.write_text(render_execution_markdown(ticket), encoding="utf-8")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    task_id = ((ticket.get("selected") or {}).get("task") or {}).get("id") or "none"
    history_path = history_dir / f"{stamp}-task-{task_id}.json"
    history_path.write_text(payload, encoding="utf-8")
    _rotate_history(history_dir)
    outputs["history"] = _path_label(history_path)

    state = {
        "contract": STATE_CONTRACT,
        "last_run_at": ticket.get("generated_at"),
        "status": ticket.get("status"),
        "fingerprint": ticket.get("fingerprint"),
        "task_id": ((ticket.get("selected") or {}).get("task") or {}).get("id"),
        "current_json": outputs["json"],
        "current_markdown": outputs["markdown"],
        "history": outputs["history"],
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ticket["outputs"] = outputs
    # Keep agent_startup_prompt in the written json file.
    json_path.write_text(json.dumps(ticket, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return outputs


def invoke_external_hook(ticket: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    """Optionally run BOOK_DEV_AI_HOOK with ticket paths substituted.

    If BOOK_DEV_AI_HOOK is unset, fall back to the built-in Codex/Grok wrapper
    script so the loop is wired without custom shell config. Custom hooks are
    parsed as an argv template; shell operators, expansion, and redirection
    are intentionally not supported.
    """

    command_template = os.environ.get(HOOK_ENV, "").strip()
    using_default = False
    if not command_template and DEFAULT_HOOK_SCRIPT.is_file():
        command_template = f"bash {DEFAULT_HOOK_SCRIPT} {{ticket}}"
        using_default = True

    hook: dict[str, Any] = {
        "configured": bool(command_template),
        "invoked": False,
        "status": "not_configured",
        "env_var": HOOK_ENV,
        "using_default_wrapper": using_default,
        "command": None,
        "exit_code": None,
        "stdout_tail": None,
        "stderr_tail": None,
    }
    if not command_template:
        return hook

    outputs = ticket.get("outputs") or {}
    replacements = {
        "{ticket}": outputs.get("json") or "",
        "{markdown}": outputs.get("markdown") or "",
        "{task_id}": str(((ticket.get("selected") or {}).get("task") or {}).get("id") or ""),
    }
    try:
        if using_default:
            command = ["bash", str(DEFAULT_HOOK_SCRIPT), replacements["{ticket}"]]
        else:
            command = shlex.split(command_template)
            if not command:
                raise ValueError("BOOK_DEV_AI_HOOK must contain an executable")
            rendered_command = []
            for token in command:
                rendered = token
                for key, value in replacements.items():
                    rendered = rendered.replace(key, value)
                rendered_command.append(rendered)
            command = rendered_command
    except ValueError as exc:
        hook["status"] = "failed"
        hook["stderr_tail"] = _clean(exc, 400)
        return hook

    hook["command"] = shlex.join(command)

    if dry_run:
        hook["status"] = "dry_run"
        return hook

    # Provider runs can be long; allow up to 1 hour for the external hook process.
    try:
        completed = subprocess.run(
            command,
            shell=False,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        hook["status"] = "failed"
        hook["invoked"] = True
        hook["stderr_tail"] = _clean(exc, 400)
        return hook

    hook["invoked"] = True
    hook["exit_code"] = completed.returncode
    hook["stdout_tail"] = _clean(completed.stdout, 400)
    hook["stderr_tail"] = _clean(completed.stderr, 400)
    hook["status"] = "dispatched" if completed.returncode == 0 else "failed"
    return hook


def invoke_builtin_dispatch(
    ticket: dict[str, Any],
    *,
    providers: str,
    mode: str = "implement",
    dry_run: bool = False,
    max_turns: int = 24,
    timeout_sec: int = 1800,
    reviewer: str | None = None,
) -> dict[str, Any]:
    """Dispatch through the built-in Codex/Grok/Claude dispatcher."""

    from book_dev_ai_dispatch import dispatch, parse_providers  # local import

    outputs = ticket.get("outputs") or {}
    ticket_path = PROJECT_ROOT / (outputs.get("json") or "")
    if not ticket_path.is_file():
        return {
            "configured": True,
            "invoked": False,
            "status": "failed",
            "error": "execution ticket json path missing",
        }
    report = dispatch(
        ticket_path=ticket_path,
        providers=parse_providers(providers),
        mode=mode,
        max_turns=max_turns,
        timeout_sec=timeout_sec,
        dry_run=dry_run,
        write_ticket=True,
        reviewer=reviewer,
    )
    return {
        "configured": True,
        "invoked": not dry_run,
        "status": report.get("status"),
        "providers": report.get("providers"),
        "results": report.get("results"),
        "report": (report.get("outputs") or {}).get("report"),
        "env_var": "book_dev_ai_dispatch",
    }


def queue_execution(
    *,
    latest_json: Path = DEFAULT_LOOP_DIR / "latest.json",
    execution_dir: Path = DEFAULT_EXECUTION_DIR,
    write: bool = False,
    force: bool = False,
    invoke_hook: bool = False,
    hook_dry_run: bool = False,
    dispatch_providers: str | None = None,
    dispatch_mode: str = "implement",
    dispatch_max_turns: int = 24,
    dispatch_timeout_sec: int = 1800,
    dispatch_reviewer: str | None = None,
    generated_at: str | None = None,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or refresh the single-candidate execution ticket."""

    handoff = report if report is not None else load_handoff(latest_json)
    previous = load_optional_json(execution_dir / "current.json")
    candidate = select_candidate(handoff)
    ticket = build_ticket(
        report=handoff,
        candidate=candidate,
        generated_at=generated_at,
        force=force,
        previous=previous,
    )

    # Attach stable handoff digest when possible.
    if latest_json.is_file():
        digest = hashlib.sha256(latest_json.read_bytes()).hexdigest()
        source = dict(ticket.get("source") or {})
        source["handoff_sha256"] = digest
        source["handoff_json"] = _path_label(latest_json)
        ticket["source"] = source

    if write and ticket.get("status") != "skipped_existing_open":
        outputs = write_ticket(ticket, execution_dir)
        ticket["outputs"] = outputs
        if ticket.get("selected"):
            ticket["agent_startup_prompt"] = build_agent_startup_prompt(
                ticket["selected"],
                outputs["markdown"],
            )
            # rewrite markdown with startup prompt path filled
            (execution_dir / "current.md").write_text(
                render_execution_markdown(ticket),
                encoding="utf-8",
            )
            (execution_dir / "current.json").write_text(
                json.dumps(ticket, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    should_dispatch = bool(dispatch_providers) or invoke_hook or hook_dry_run
    if should_dispatch and ticket.get("status") in {"queued", "skipped_existing_open", "dispatched"}:
        if ticket.get("status") == "skipped_existing_open" and not force and not dispatch_providers:
            ticket["hook"] = {
                "configured": bool(os.environ.get(HOOK_ENV, "").strip()) or DEFAULT_HOOK_SCRIPT.is_file(),
                "invoked": False,
                "status": "skipped_existing_open",
                "env_var": HOOK_ENV,
            }
        else:
            # Ensure outputs exist when reusing an open ticket.
            if write and not ticket.get("outputs"):
                existing = load_optional_json(execution_dir / "current.json") or {}
                ticket["outputs"] = existing.get("outputs") or {
                    "json": _path_label(execution_dir / "current.json"),
                    "markdown": _path_label(execution_dir / "current.md"),
                }
            if dispatch_providers:
                hook_result = invoke_builtin_dispatch(
                    ticket,
                    providers=dispatch_providers,
                    mode=dispatch_mode,
                    dry_run=hook_dry_run,
                    max_turns=dispatch_max_turns,
                    timeout_sec=dispatch_timeout_sec,
                    reviewer=dispatch_reviewer,
                )
            else:
                hook_result = invoke_external_hook(ticket, dry_run=hook_dry_run)
            ticket["hook"] = hook_result
            if hook_result.get("status") in {"dispatched", "partial"}:
                ticket["status"] = "dispatched"
            elif hook_result.get("status") == "failed":
                ticket["status"] = "hook_failed"
            if write and ticket.get("outputs"):
                # Reload ticket if builtin dispatch already rewrote it.
                latest_ticket = load_optional_json(execution_dir / "current.json")
                if latest_ticket and latest_ticket.get("dispatch"):
                    ticket = latest_ticket
                else:
                    (execution_dir / "current.json").write_text(
                        json.dumps(ticket, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    (execution_dir / "current.md").write_text(
                        render_execution_markdown(ticket),
                        encoding="utf-8",
                    )
                state_path = execution_dir / "state.json"
                if state_path.is_file():
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    state["status"] = ticket.get("status")
                    state["hook_status"] = (ticket.get("hook") or {}).get("status")
                    state_path.write_text(
                        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )

    return ticket


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Queue one Book Dev candidate as a bounded AI execution ticket"
    )
    parser.add_argument("--write", action="store_true", help="Write execution/current.* artifacts")
    parser.add_argument("--json", action="store_true", help="Print the machine-readable ticket")
    parser.add_argument("--force", action="store_true", help="Replace an open ticket for the same candidate")
    parser.add_argument(
        "--invoke-hook",
        action="store_true",
        help=f"Run ${HOOK_ENV} or the built-in Codex/Grok wrapper after queueing",
    )
    parser.add_argument(
        "--hook-dry-run",
        action="store_true",
        help="Render/dispatch in dry-run mode without executing providers",
    )
    parser.add_argument(
        "--dispatch",
        metavar="PROVIDERS",
        help="Builtin dispatch providers, e.g. grok,codex (implies write+force recommended)",
    )
    parser.add_argument(
        "--dispatch-mode",
        choices=["plan", "implement"],
        default="implement",
        help="Builtin dispatch mode (default: implement)",
    )
    parser.add_argument("--dispatch-max-turns", type=int, default=24)
    parser.add_argument("--dispatch-timeout-sec", type=int, default=1800)
    parser.add_argument(
        "--dispatch-reviewer",
        choices=["codex"],
        help="Build-review flow: the reviewer gates the builder's branch before a PR",
    )
    parser.add_argument("--latest-json", type=Path, default=DEFAULT_LOOP_DIR / "latest.json")
    parser.add_argument("--execution-dir", type=Path, default=DEFAULT_EXECUTION_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        ticket = queue_execution(
            latest_json=args.latest_json,
            execution_dir=args.execution_dir,
            write=args.write or bool(args.dispatch) or args.invoke_hook or args.hook_dry_run,
            force=args.force or bool(args.dispatch),
            invoke_hook=args.invoke_hook or args.hook_dry_run,
            hook_dry_run=args.hook_dry_run,
            dispatch_providers=args.dispatch,
            dispatch_mode=args.dispatch_mode,
            dispatch_max_turns=args.dispatch_max_turns,
            dispatch_timeout_sec=args.dispatch_timeout_sec,
            dispatch_reviewer=args.dispatch_reviewer,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {"contract": CONTRACT, "status": "error", "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1

    if args.json:
        print(json.dumps(ticket, ensure_ascii=False, indent=2))
    else:
        selected = ticket.get("selected") or {}
        task = selected.get("task") or {}
        print(
            "Book Dev execution hook: "
            f"status={ticket.get('status')} "
            f"task=#{task.get('id') or '-'} "
            f"hook={(ticket.get('hook') or {}).get('status')}"
        )
        if args.write and ticket.get("outputs"):
            print(json.dumps(ticket["outputs"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
