#!/usr/bin/env python3
"""Offline tests for Book Dev AI dispatch (Codex/Grok/Claude Code)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "workflows"))
from book_dev_ai_dispatch import (  # noqa: E402
    build_prompt,
    build_provider_command,
    dispatch,
    parse_providers,
    open_pull_request,
    parse_verdict,
    provider_availability,
    provider_env,
    resolve_claude_cmd,
    resolve_repo_dir,
    run_provider,
)


def sample_ticket(tmp: Path) -> Path:
    (tmp / "repo" / ".git").mkdir(parents=True)
    selected = {
        "task": {
            "id": 2135,
            "title": "[Book Dev] owner/repo — review dirty changes",
            "status": "todo",
            "priority": 2,
        },
        "repository": {
            "path": str(tmp / "repo"),
            "state": "dirty",
            "dirty_file_count": 3,
        },
        "source": "open_task",
        "recommendation": "Inspect dirty diff first.",
        "agent_prompt": "Work on one task.",
    }
    ticket = {
        "contract": "solo-empire.book-dev-engineering-loop-execution.v1",
        "status": "queued",
        "selected": selected,
        "agent_startup_prompt": "startup",
        "outputs": {
            "json": str(tmp / "current.json"),
            "markdown": str(tmp / "current.md"),
        },
    }
    path = tmp / "current.json"
    path.write_text(json.dumps(ticket), encoding="utf-8")
    (tmp / "current.md").write_text("# ticket\n", encoding="utf-8")
    return path


def git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
        ["commit", "-q", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True)


def fake_provider(verdicts: list[str], *, commit: bool = True, files: tuple[str, ...] = (), leave_dirty: bool = False,
                  rewrite_base: bool = False):
    """Builder rounds commit a file; review rounds write the next verdict."""

    calls: list[dict] = []

    def run(provider, *, prompt, mode, dispatch_dir, cwd, label=None, prompt_file=None, isolated_review=False, guard="release", **_):
        calls.append({
            "isolated": isolated_review, "guard": guard, "dispatch_dir": dispatch_dir,
            "provider": provider, "mode": mode, "cwd": cwd, "label": label, "prompt": prompt,
            "prompt_file": prompt_file.read_text() if prompt_file else None,
            "detached": subprocess.run(["git", "-C", str(cwd), "symbolic-ref", "-q", "HEAD"], capture_output=True).returncode != 0,
            "head": subprocess.run(["git", "-C", str(cwd), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        })
        last = dispatch_dir / f"{label}.last-message.md"
        if mode == "implement" and commit:
            for name in files:
                (cwd / name).parent.mkdir(parents=True, exist_ok=True)
                (cwd / name).write_text("x")
            if leave_dirty:
                (cwd / "untracked.txt").write_text("not committed")
            (cwd / f"{label}.txt").write_text(label)
            subprocess.run(["git", "-C", str(cwd), "add", "--", f"{label}.txt", *files], check=True)
            subprocess.run(["git", "-C", str(cwd), "commit", "-q", "-m", label], check=True)
            if rewrite_base:  # fold the work into an amended base commit
                subprocess.run(["git", "-C", str(cwd), "reset", "-q", "--soft", "HEAD~1"], check=True)
                subprocess.run(["git", "-C", str(cwd), "commit", "-q", "--amend", "-m", "rewritten"], check=True)
        if mode == "plan":
            last.write_text(f"finding\nVERDICT: {verdicts.pop(0)}\n")
        return {"provider": provider, "status": "dispatched", "last_message": str(last)}

    return run, calls


def build_review_tests(tmp: Path) -> None:
    # Model defaults come from the environment; the timer's values must not leak into these tests.
    for name in ("BOOK_DEV_BUILDER_MODEL", "BOOK_DEV_REVIEWER_MODEL", "BOOK_DEV_BUILDER_EFFORT",
                 "BOOK_DEV_REVIEWER_EFFORT", "BOOK_DEV_ESCALATE", "BOOK_DEV_ESCALATION_MODEL", "BOOK_DEV_PARALLEL"):
        os.environ.pop(name, None)
    # The flow tests below exercise one builder; the planner has its own tests (planner_tests).
    os.environ["BOOK_DEV_PLANNER"] = "none"
    assert parse_verdict("x\nVERDICT: PASS") == "pass"
    assert parse_verdict("**VERDICT: BLOCKING**") == "blocking"
    assert parse_verdict("VERDICT: PASS\nlater\nVERDICT: BLOCKING") == "blocking"
    assert parse_verdict("no verdict") == "unknown"
    # Codex review 2026-11-03: only a standalone terminal verdict counts.
    assert parse_verdict("`VERDICT: PASS`.\n") == "pass"
    assert parse_verdict("> **VERDICT: BLOCKING**") == "blocking"
    assert parse_verdict("VERDICT: PASS (blocking issues remain)") == "unknown"
    assert parse_verdict("End with `VERDICT: PASS`.\nVERDICT: PASS\n- [P1] auth bypass in api.py") == "unknown"
    assert parse_verdict("VERDICT: PASS\n\nFindings:\n- [P1] x") == "unknown"
    assert parse_verdict("VERDICT: BLOCKING\nactually fine\nVERDICT: PASS") == "unknown"
    assert parse_verdict("VERDICT: PASSED") == "unknown"
    assert parse_verdict("__VERDICT: BLOCKING__\nVERDICT: PASS") == "unknown"
    assert parse_verdict("_VERDICT: BLOCKING_") == "blocking"
    assert parse_verdict("I first said VERDICT: BLOCKING\n**VERDICT: PASS**") == "unknown"
    assert parse_verdict("") == "unknown"
    from book_dev_ai_dispatch import AGENT_INSTRUCTION_RE
    for path in ("AGENTS.md", "x/AGENTS.override.md", "CLAUDE.local.md", ".agents/skills/a/SKILL.md", ".codex/config.toml"):
        assert AGENT_INSTRUCTION_RE.search(path), path
    for path in ("src/agents.py", "docs/agent.md", "README.md"):
        assert not AGENT_INSTRUCTION_RE.search(path), path

    ticket_path = sample_ticket(tmp)
    repo = tmp / "repo"
    (repo / ".git").rmdir()
    git_repo(repo)
    origin = tmp / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)
    pushed_main = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    # A local commit not on origin must not enter the reviewed branch.
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "local-only"], check=True)
    common = dict(ticket_path=ticket_path, providers=["claude"], mode="implement", write_ticket=False, reviewer="codex")

    for bad in (dict(providers=["claude", "grok"]), dict(mode="plan"), dict(providers=["codex"]), dict(reviewer="grok"),
                dict(providers=["grok"])):
        try:
            dispatch(**{**common, **bad})
            raise AssertionError(f"expected rejection: {bad}")
        except ValueError:
            pass

    # Runs within one second would share a stamp, so each dispatch gets its own.
    stamps = mock.patch("book_dev_ai_dispatch._now_stamp", side_effect=[f"20261101T0000{i:02d}Z" for i in range(20)])
    with stamps, mock.patch("book_dev_ai_dispatch.resolve_claude_cmd", return_value=["claude"]), mock.patch(
        "book_dev_ai_dispatch.resolve_codex_cmd", return_value=["codex"]
    ):
        report = dispatch(**common, dry_run=True)
        assert report["status"] == "dry_run", report
        assert [item["provider"] for item in report["results"]] == ["claude", "codex"]
        review_cmd = report["results"][1]["command"]
        assert review_cmd[review_cmd.index("--sandbox") + 1] == "read-only"
        assert "project_doc_max_bytes=0" in review_cmd
        assert report["policy"]["automatic_release_closeout"] is False
        assert report["flow"]["branch"].startswith("ai/2135-")

        # Blocking once, then pass: one fix round, then a PR from the branch.
        run, calls = fake_provider(["BLOCKING", "PASS"])
        # A builder-planted symlink at the review report name must not be followed.
        victim = tmp / "victim.txt"
        victim.write_text("keep me")
        real_run = run

        def run(provider, **kwargs):
            result = real_run(provider, **kwargs)
            if kwargs["label"] == "claude-build":
                (kwargs["dispatch_dir"] / "codex-review-0.last-message.md").symlink_to(victim)
            return result

        opened = {"status": "opened", "url": "https://github.com/o/r/pull/1"}
        with mock.patch("book_dev_ai_dispatch.run_provider", side_effect=run), mock.patch(
            "book_dev_ai_dispatch.open_pull_request", return_value=opened
        ) as pr:
            report = dispatch(**common)
        assert report["status"] == "dispatched", report
        assert [c["label"] for c in calls] == ["claude-build", "codex-review-0", "claude-fix-1", "codex-review-1"]
        assert calls[1]["mode"] == "plan" and "VERDICT" in calls[1]["prompt"]
        # Each review runs in its own detached checkout of the exact commit, removed afterwards.
        assert calls[1]["detached"] and calls[1]["cwd"] != calls[0]["cwd"] and not calls[1]["cwd"].exists()
        assert calls[1]["isolated"] and not calls[0]["isolated"]
        assert {c["guard"] for c in calls if c["mode"] == "implement"} == {"branch"}, "builder runs under the branch guard"
        assert calls[0]["cwd"].parent not in calls[1]["cwd"].parents, "review checkout lives outside the builder's dirs"
        assert Path(report["flow"]["reviews"][0]["report"]).read_text().startswith("finding")
        assert victim.read_text() == "keep me" and not Path(report["flow"]["reviews"][0]["report"]).is_symlink()
        assert calls[3]["head"] in calls[3]["prompt"] and calls[3]["head"] != calls[1]["head"]
        assert pr.call_args.kwargs["head"] == calls[3]["head"] == report["flow"]["reviews"][-1]["head"]
        # The fix round reaches prompt-file providers (Grok) too.
        assert "Fix round 1" in calls[2]["prompt_file"] and "finding" in calls[2]["prompt_file"]
        assert report["flow"]["agent_instruction_files"] == []
        assert "do not push" in calls[0]["prompt"].lower() and "Fix round 1" in calls[2]["prompt"]
        assert f"Repository: {calls[0]['cwd']}" in calls[0]["prompt"]
        assert "push the configured upstream" not in calls[0]["prompt"] and "startup" not in calls[0]["prompt"]
        worktree = calls[0]["cwd"]
        assert worktree != repo.resolve() and not worktree.exists(), "worktree removed after the PR"
        assert [r["verdict"] for r in report["flow"]["reviews"]] == ["blocking", "pass"]
        branch = report["flow"]["branch"]
        assert pr.call_args.kwargs["branch"] == branch and pr.call_args.kwargs["base_branch"] == "main"
        log = subprocess.run(["git", "-C", str(repo), "log", "--format=%s", branch], capture_output=True, text=True).stdout
        assert log.split() == ["claude-fix-1", "claude-build", "init"], "branch starts at origin/main"
        assert report["flow"]["base"] == pushed_main and pushed_main in calls[1]["prompt"]
        assert subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True).stdout.strip() == "main"

        # Still blocking after MAX_FIX_ROUNDS: escalate, keep the worktree, open no PR.
        run, calls = fake_provider(["BLOCKING"] * 3)
        with mock.patch("book_dev_ai_dispatch.run_provider", side_effect=run), mock.patch(
            "book_dev_ai_dispatch.open_pull_request"
        ) as pr:
            report = dispatch(**common)
        assert report["status"] == "review_blocked", report
        assert len(report["flow"]["reviews"]) == 3
        pr.assert_not_called()
        assert calls[0]["cwd"].exists()

        # Commits plus leftover edits: nothing unreviewed reaches a PR.
        run, calls = fake_provider(["PASS"], leave_dirty=True)
        with mock.patch("book_dev_ai_dispatch.run_provider", side_effect=run), mock.patch(
            "book_dev_ai_dispatch.open_pull_request"
        ) as pr:
            report = dispatch(**common)
        assert report["status"] == "uncommitted_changes", report
        assert [c["label"] for c in calls] == ["claude-build"] and report["flow"]["worktree_kept"]
        pr.assert_not_called()

        # A branch that no longer descends from the base would review a different diff than the PR.
        run, calls = fake_provider([], rewrite_base=True)
        with mock.patch("book_dev_ai_dispatch.run_provider", side_effect=run), mock.patch(
            "book_dev_ai_dispatch.open_pull_request"
        ) as pr:
            report = dispatch(**common)
        assert report["status"] == "failed" and "no longer descends" in report["flow"]["error"], report
        assert [c["label"] for c in calls] == ["claude-build"] and report["flow"]["worktree_kept"]
        pr.assert_not_called()

        # A commit that changes instruction files the reviewer loads never reaches the reviewer.
        run, calls = fake_provider([], files=("AGENTS.override.md", "two words/claude.local.md"))
        with mock.patch("book_dev_ai_dispatch.run_provider", side_effect=run), mock.patch(
            "book_dev_ai_dispatch.open_pull_request"
        ) as pr:
            report = dispatch(**common)
        assert report["status"] == "review_blocked", report
        assert report["flow"]["agent_instruction_files"] == ["AGENTS.override.md", "two words/claude.local.md"]
        assert [c["label"] for c in calls] == ["claude-build"]
        pr.assert_not_called()

        # No commits: nothing to review, worktree and branch discarded.
        run, calls = fake_provider([], commit=False)
        with mock.patch("book_dev_ai_dispatch.run_provider", side_effect=run):
            report = dispatch(**common)
        assert report["status"] == "no_changes", report
        assert [c["label"] for c in calls] == ["claude-build"] and not calls[0]["cwd"].exists()

        # Edits left uncommitted, or a failed build with edits, keep the worktree.
        def edit_only(provider, *, cwd, label=None, dispatch_dir, status="dispatched", **_):
            (cwd / "wip.txt").write_text("wip")
            return {"provider": provider, "status": status, "last_message": str(dispatch_dir / f"{label}.md")}

        for status, expected in (("dispatched", "uncommitted_changes"), ("timeout", "timeout")):
            seen: list[Path] = []
            with mock.patch(
                "book_dev_ai_dispatch.run_provider",
                side_effect=lambda *a, **k: (seen.append(k["cwd"]), edit_only(*a, status=status, **k))[1],
            ):
                report = dispatch(**common)
            assert report["status"] == expected, report
            assert report["flow"]["worktree_kept"] is True and seen[0].exists()

    # Codex (Luna) builder: sandboxed, no network, edits only; the dispatcher commits each round.
    codex = dict(common, providers=["codex"], reviewer="codex")
    for bad in (dict(), dict(builder_model="gpt-6-luna"), dict(builder_model="m", reviewer_model="m")):
        try:
            dispatch(**{**codex, **bad})
            raise AssertionError(f"expected rejection: {bad}")
        except ValueError:
            pass
    models = dict(builder_model="gpt-6-luna", reviewer_model="gpt-6.1-sol")
    stamps = mock.patch("book_dev_ai_dispatch._now_stamp", side_effect=[f"20261109T0000{i:02d}Z" for i in range(60)])
    with stamps, mock.patch("book_dev_ai_dispatch.resolve_codex_cmd", return_value=["codex"]):
        report = dispatch(**codex, **models, dry_run=True)
        assert report["status"] == "dry_run", report
        build_cmd, review_cmd = (item["command"] for item in report["results"])
        assert build_cmd[build_cmd.index("--sandbox") + 1] == "workspace-write"
        assert "sandbox_workspace_write.network_access=false" in build_cmd
        assert {"sandbox_workspace_write.exclude_slash_tmp=true",
                "sandbox_workspace_write.exclude_tmpdir_env_var=true"} <= set(build_cmd)
        assert build_cmd[build_cmd.index("-m") + 1] == "gpt-6-luna"
        assert review_cmd[review_cmd.index("-m") + 1] == "gpt-6.1-sol"
        assert review_cmd[review_cmd.index("--sandbox") + 1] == "read-only"
        assert report["flow"]["same_family"] is True
        # Luna thinks at xhigh by default, the reviewer at high; both overridable, unknown values refused.
        assert "model_reasoning_effort=xhigh" in build_cmd and "model_reasoning_effort=high" in review_cmd
        assert report["flow"]["escalation"] == {"to": "claude", "model": "opus", "used": False}
        with mock.patch.dict(os.environ, {"BOOK_DEV_BUILDER_EFFORT": "medium"}):
            low = dispatch(**codex, **models, dry_run=True)["results"][0]["command"]
        assert "model_reasoning_effort=medium" in low
        try:
            dispatch(**codex, **models, builder_effort="turbo", dry_run=True)
            raise AssertionError("expected rejection of an unknown effort")
        except ValueError:
            pass
        # No user config, MCP servers, plugins or app connectors in either Codex role.
        for cmd in (build_cmd, review_cmd):
            assert "--ignore-user-config" in cmd and "--ignore-rules" in cmd and "mcp_servers={}" in cmd, cmd
            assert 'web_search="disabled"' in cmd, cmd
            assert cmd[cmd.index("apps") - 1] == "--disable" and cmd[cmd.index("plugins") - 1] == "--disable"
        assert report["flow"]["builder_model"] == "gpt-6-luna"

        calls: list[dict] = []
        verdicts = ["BLOCKING", "PASS"]

        def edit_only_build(provider, *, prompt, mode, dispatch_dir, cwd, label=None, **kwargs):
            calls.append({"label": label, "mode": mode, "prompt": prompt, "guard": kwargs.get("guard"),
                          "model": kwargs.get("model"), "cwd": cwd})
            last = dispatch_dir / f"{label}.last-message.md"
            if mode == "implement":
                (cwd / f"{label}.txt").write_text(label)  # no commit: the sandbox cannot write the gitdir
            else:
                last.write_text(f"finding\nVERDICT: {verdicts.pop(0)}\n")
            return {"provider": provider, "status": "dispatched", "last_message": str(last)}

        opened = {"status": "opened", "url": "https://github.com/o/r/pull/2"}
        with mock.patch("book_dev_ai_dispatch.run_provider", side_effect=edit_only_build), mock.patch(
            "book_dev_ai_dispatch.open_pull_request", return_value=opened
        ) as pr:
            report = dispatch(**codex, **models)
        assert report["status"] == "dispatched", report
        assert [c["label"] for c in calls] == ["codex-build", "codex-review-0", "codex-fix-1", "codex-review-1"]
        assert [c["model"] for c in calls] == ["gpt-6-luna", "gpt-6.1-sol", "gpt-6-luna", "gpt-6.1-sol"]
        assert "do not commit" in calls[0]["prompt"].lower() and "do not commit" in calls[2]["prompt"].lower()
        log = subprocess.run(["git", "-C", str(repo), "log", "--format=%s", report["flow"]["branch"]],
                             capture_output=True, text=True).stdout.splitlines()
        assert [line.split(":")[0] for line in log] == ["codex-fix-1", "codex-build", "init"], log
        assert pr.call_args.kwargs["head"] == report["flow"]["reviews"][-1]["head"]
        assert "gpt-6-luna" in pr.call_args.kwargs["body"] and "gpt-6.1-sol" in pr.call_args.kwargs["body"]

        # A builder that writes a credential it read elsewhere never reaches the reviewer or a PR.
        calls.clear()

        def leak(provider, *, mode, dispatch_dir, cwd, label=None, **_):
            calls.append({"label": label})
            if mode == "implement":
                (cwd / "notes.txt").write_text("token = gh" + "p_" + "A" * 36 + "\n")
            return {"provider": provider, "status": "dispatched", "last_message": str(dispatch_dir / "x")}

        with mock.patch("book_dev_ai_dispatch.run_provider", side_effect=leak), mock.patch(
            "book_dev_ai_dispatch.open_pull_request"
        ) as pr:
            report = dispatch(**codex, **models)
        assert report["status"] == "review_blocked" and report["flow"]["secret_findings"] == ["notes.txt"], report
        assert "A" * 36 not in json.dumps(report), "the value itself is never reported"
        assert [c["label"] for c in calls] == ["codex-build"]
        pr.assert_not_called()

        # A builder that repoints the worktree's .git file stops the run before any dispatcher git call.
        def swap(provider, *, mode, dispatch_dir, cwd, label=None, **_):
            if mode == "implement":
                (cwd / "x.txt").write_text("x")
                (cwd / ".git").write_text("gitdir: /tmp/evil\n")
            return {"provider": provider, "status": "dispatched", "last_message": str(dispatch_dir / "x")}

        with mock.patch("book_dev_ai_dispatch.run_provider", side_effect=swap), mock.patch(
            "book_dev_ai_dispatch.open_pull_request"
        ) as pr:
            report = dispatch(**codex, **models)
        assert report["status"] == "failed" and "pointer" in report["results"][-1]["error"], report
        assert report["flow"]["worktree_kept"] is True
        pr.assert_not_called()

        # A repo filter that would run a script never runs in any dispatcher git command, and the
        # committed blob is the raw content (scanned for secrets, never an LFS pointer).
        ran = tmp / "filter-ran.txt"
        evil = tmp / "evil-filter.sh"
        evil.write_text(f"#!/bin/sh\ntouch {ran}\ncat\n")
        evil.chmod(0o755)
        for key, value in (("filter.x.clean", str(evil)), ("filter.x.smudge", str(evil)),
                           ("filter.x.process", str(evil)), ("filter.x.required", "true")):
            subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)

        def filtered_build(provider, *, mode, dispatch_dir, cwd, label=None, **_):
            if mode == "implement":
                (cwd / ".gitattributes").write_text("*.bin filter=x\n")
                (cwd / f"{label}.bin").write_text("data\n")
            else:
                (dispatch_dir / f"{label}.last-message.md").write_text("ok\nVERDICT: PASS\n")
            return {"provider": provider, "status": "dispatched", "last_message": str(dispatch_dir / f"{label}.last-message.md")}

        with mock.patch("book_dev_ai_dispatch.run_provider", side_effect=filtered_build), mock.patch(
            "book_dev_ai_dispatch.open_pull_request", return_value=opened
        ):
            report = dispatch(**codex, **models)
        subprocess.run(["git", "-C", str(repo), "config", "--remove-section", "filter.x"], check=True)
        assert report["status"] == "dispatched" and not ran.exists(), report

        # The dispatcher's commit never signs: gpg.program could be a script in the checkout.
        signer = tmp / "signer.sh"
        signed = tmp / "signed.txt"
        signer.write_text(f"#!/bin/sh\ntouch {signed}\nexit 1\n")
        signer.chmod(0o755)
        subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "true"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "gpg.program", str(signer)], check=True)
        verdicts[:] = ["PASS"]
        with mock.patch("book_dev_ai_dispatch.run_provider", side_effect=edit_only_build), mock.patch(
            "book_dev_ai_dispatch.open_pull_request", return_value=opened
        ):
            report = dispatch(**codex, **models)
        subprocess.run(["git", "-C", str(repo), "config", "--unset", "commit.gpgsign"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "--unset", "gpg.program"], check=True)
        assert report["status"] == "dispatched" and not signed.exists(), report

        # Still blocking after the builder's fix rounds: Claude writes a plan once (read-only, fresh
        # checkout, every review report), the builder gets one more round on it, then the owner.
        def escalation_run(verdict_list: list[str], plan_status: str = "dispatched"):
            seen: list[dict] = []

            def run(provider, *, prompt, mode, dispatch_dir, cwd, label=None, **kwargs):
                seen.append({"provider": provider, "mode": mode, "label": label, "prompt": prompt,
                             "guard": kwargs.get("guard"), "model": kwargs.get("model"), "cwd": cwd})
                last = dispatch_dir / f"{label}.last-message.md"
                if provider == "claude":
                    last.write_text("PLAN: change foo() in a.py\n")
                    return {"provider": provider, "status": plan_status, "last_message": str(last)}
                if mode == "implement":
                    (cwd / f"{label}.txt").write_text(label)
                else:
                    last.write_text(f"finding {label}\nVERDICT: {verdict_list.pop(0)}\n")
                return {"provider": provider, "status": "dispatched", "last_message": str(last)}
            return run, seen

        run, seen = escalation_run(["BLOCKING", "BLOCKING", "BLOCKING", "PASS"])
        with mock.patch("book_dev_ai_dispatch.run_provider", side_effect=run), mock.patch(
            "book_dev_ai_dispatch.open_pull_request", return_value=opened
        ):
            report = dispatch(**codex, **models)
        assert report["status"] == "dispatched", report
        assert [s["label"] for s in seen] == ["codex-build", "codex-review-0", "codex-fix-1", "codex-review-1",
                                             "codex-fix-2", "codex-review-2", "claude-plan", "codex-fix-3", "codex-review-3"]
        plan_call = seen[6]
        assert plan_call["mode"] == "plan" and plan_call["model"] == "opus" and plan_call["guard"] == "branch"
        assert all(f"finding codex-review-{i}" in plan_call["prompt"] for i in range(3)), "every review report reaches the planner"
        assert "+codex-fix-2" in plan_call["prompt"], "the planner gets the diff (plan mode has no shell)"
        assert plan_call["cwd"] != seen[4]["cwd"] and not plan_call["cwd"].exists(), "fresh checkout, removed after"
        assert "PLAN: change foo() in a.py" in seen[7]["prompt"] and "escalated" in seen[7]["prompt"]
        assert report["flow"]["escalation"]["used"] is True and report["flow"]["escalation"]["status"] == "dispatched"

        # Blocking again after the planned round, or no usable plan: the owner decides.
        for verdicts_left, plan_status, labels in (
            (["BLOCKING"] * 4, "dispatched", 9),
            (["BLOCKING"] * 3, "failed", 7),
        ):
            run, seen = escalation_run(list(verdicts_left), plan_status)
            with mock.patch("book_dev_ai_dispatch.run_provider", side_effect=run), mock.patch(
                "book_dev_ai_dispatch.open_pull_request"
            ) as pr:
                report = dispatch(**codex, **models)
            assert report["status"] == "review_blocked" and len(seen) == labels, (report["status"], [s["label"] for s in seen])
            pr.assert_not_called()

        # BOOK_DEV_ESCALATE=none stops at the owner after the builder's own rounds.
        run, seen = escalation_run(["BLOCKING"] * 3)
        with mock.patch.dict(os.environ, {"BOOK_DEV_ESCALATE": "none"}), mock.patch(
            "book_dev_ai_dispatch.run_provider", side_effect=run
        ):
            report = dispatch(**codex, **models)
        assert report["status"] == "review_blocked" and "claude-plan" not in [s["label"] for s in seen]

        # A Codex build that edits nothing still ends as no_changes.
        calls.clear()
        with mock.patch("book_dev_ai_dispatch.run_provider",
                        side_effect=lambda p, **k: {"provider": p, "status": "dispatched", "last_message": ""}):
            report = dispatch(**codex, **models)
        assert report["status"] == "no_changes", report

    # Secret scan: every commit, binary-safe, quoted names, fail-closed.
    from book_dev_ai_dispatch import SECRET_SCAN_FAILED, secret_findings
    scan = tmp / "scan"
    git_repo(scan)
    def sha() -> str:
        return subprocess.run(["git", "-C", str(scan), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    def commit(name: str, data: bytes) -> None:
        (scan / name).write_bytes(data)
        subprocess.run(["git", "-C", str(scan), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(scan), "commit", "-q", "-m", name], check=True)
    start = sha()
    token = b"gh" + b"p_" + b"B" * 36
    commit("app.py", b"key = '" + token + b"'\n")
    commit("app.py", b"key = None\n")  # removed again: the history still carries it
    assert secret_findings(scan, start, sha()) == ["app.py"]
    start = sha()
    commit("logo.png", b"\x89PNG\r\n\x1a\n\xff\xfe\x00binary")
    commit("ไฟล์ x.txt", b"+++ b/fake\n+" + token + b"\n")  # non-ASCII name, diff-looking content
    assert secret_findings(scan, start, sha()) == ["ไฟล์ x.txt"]
    commit(".env", b"A=1\n")
    assert ".env" in secret_findings(scan, start, sha())
    start = sha()
    commit("client certificate.p12", b"\x30\x82binary")
    commit("prod credentials.env", b"DB=1\n")
    commit("conf.txt", b"DATABASE_URL=postgres://app:s3cretpass@db.internal:5432/x\n")
    assert secret_findings(scan, start, sha()) == ["client certificate.p12", "prod credentials.env", "conf.txt"]
    assert secret_findings(scan, start, start) == []
    # The env files this setup uses, Linear tokens and dotenv assignments; submodule gitlinks are not blobs.
    start = sha()
    commit("book-dev-dispatch.env", b"BOOK_DEV_DISPATCH_MODE=implement\n")
    commit("cfg.txt", b"LINEAR_API_KEY=lin_api_" + b"C" * 40 + b"\n")
    commit("settings.ini", b"export SERVICE_TOKEN='abcdefghijklmnopqrstuvwxyz012345'\n")
    commit("app2.py", b"API_KEY = os.environ.get('API_KEY')\n")  # reads the env: not a literal credential
    assert secret_findings(scan, start, sha()) == ["book-dev-dispatch.env", "cfg.txt", "settings.ini"]
    start = sha()
    sub_sha = sha()
    subprocess.run(["git", "-C", str(scan), "update-index", "--add", "--cacheinfo", f"160000,{sub_sha},vendor/lib"], check=True)
    (scan / ".gitmodules").write_text('[submodule "vendor/lib"]\n\tpath = vendor/lib\n\turl = https://example.com/lib.git\n')
    subprocess.run(["git", "-C", str(scan), "add", ".gitmodules"], check=True)
    subprocess.run(["git", "-C", str(scan), "commit", "-q", "-m", "submodule"], check=True)
    assert secret_findings(scan, start, sha()) == [], "a gitlink is skipped, not a scan failure"
    # A parentless commit merged in (unrelated history) is diffed against the empty tree.
    start = sha()
    subprocess.run(["git", "-C", str(scan), "checkout", "-q", "--orphan", "side"], check=True)
    subprocess.run(["git", "-C", str(scan), "rm", "-rq", "--cached", "."], check=True)
    commit("orphan.txt", token + b"\n")
    commit("orphan.txt", b"clean\n")  # gone before the merge: only the root commit carries it
    subprocess.run(["git", "-C", str(scan), "checkout", "-q", "-f", "main"], check=True)
    subprocess.run(["git", "-C", str(scan), "merge", "-q", "--allow-unrelated-histories", "-m", "m", "side"], check=True)
    assert "orphan.txt" in secret_findings(scan, start, sha())
    with mock.patch("book_dev_ai_dispatch._git_bytes",
                    return_value=subprocess.CompletedProcess([], 124, b"", b"timed out")):
        assert secret_findings(scan, start, sha()) == [SECRET_SCAN_FAILED], "a failed scan blocks"

    # Every configured filter driver (git-lfs included) becomes a no-op for dispatcher git commands.
    from book_dev_ai_dispatch import filter_overrides
    no_global = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
    no_global.start()
    assert filter_overrides(scan) == ()
    subprocess.run(["git", "-C", str(scan), "config", "filter.lfs.process", "git-lfs filter-process"], check=True)
    subprocess.run(["git", "-C", str(scan), "config", "filter.my.tool.clean", "./scripts/clean.sh"], check=True)
    args = filter_overrides(scan)
    assert args is not None
    assert "filter.lfs.process=" in args and "filter.lfs.clean=cat" in args and "filter.my.tool.clean=cat" in args, args
    # A filter behind a conditional include (onbranch:ai/**) is neutralised too, whatever the branch.
    extra = scan / "branch-only.gitconfig"
    extra.write_text('[filter "sneaky"]\n\tclean = ./scripts/evil.sh\n')
    subprocess.run(["git", "-C", str(scan), "config", "includeIf.onbranch:ai/**.path", str(extra)], check=True)
    cond = filter_overrides(scan)
    assert cond is not None and "filter.sneaky.clean=cat" in cond, cond
    subprocess.run(["git", "-C", str(scan), "config", "--remove-section", "includeIf.onbranch:ai/**"], check=True)
    subprocess.run(["git", "-C", str(scan), "config", "--remove-section", "filter.lfs"], check=True)
    subprocess.run(["git", "-C", str(scan), "config", "--remove-section", "filter.my.tool"], check=True)
    # Long uppercase lines do not make the dotenv pattern backtrack for long.
    import time
    from book_dev_ai_dispatch import SECRET_CONTENT_BYTES_RE
    began = time.monotonic()
    SECRET_CONTENT_BYTES_RE.search(b"A" * 200_000 + b"\n")
    assert time.monotonic() - began < 2, "bounded quantifiers"
    no_global.stop()

    # Nothing a builder starts outlives the provider call.
    from book_dev_ai_dispatch import _run_process
    marker = tmp / "late.txt"
    done = _run_process(["sh", "-c", f"(sleep 1; touch {marker}) >/dev/null 2>&1 & echo started"],
                        input=None, cwd=str(tmp), env=None, timeout=30)
    assert done.returncode == 0 and done.stdout.strip() == "started"
    time.sleep(2)
    assert not marker.exists(), "the background child was killed with the group"
    # A double-forked daemon that left the group with setsid is still killed: the dispatcher is
    # its subreaper (without that it is reparented to init and survives).
    escaped = tmp / "escaped.txt"
    daemon = tmp / "daemonize.py"
    daemon.write_text(
        "import os, sys, time\n"
        "if os.fork(): os._exit(0)\n"
        "os.setsid()\n"
        "if os.fork(): os._exit(0)\n"
        "fd = os.open(os.devnull, os.O_RDWR)\n"
        "[os.dup2(fd, n) for n in (0, 1, 2)]\n"
        "time.sleep(1)\n"
        "open(sys.argv[1], 'w').write('escaped')\n"
    )
    _run_process([sys.executable, str(daemon), str(escaped)], input=None, cwd=str(tmp), env=None, timeout=30)
    time.sleep(2)
    assert not escaped.exists(), "a setsid double-fork daemon does not outlive the call"
    # A process that ignores reaping is reported, not waited on forever.
    import book_dev_ai_dispatch as dispatch_module
    with mock.patch.object(dispatch_module, "_descendants", side_effect=[{999999}, set(), {999999}]), \
            mock.patch.object(dispatch_module.os, "kill"), mock.patch.object(dispatch_module.os, "waitpid", return_value=(0, 0)), \
            mock.patch.object(dispatch_module, "REAP_TIMEOUT_SEC", 0.2), \
            mock.patch.object(dispatch_module.Path, "exists", return_value=True):
        began = time.monotonic()
        try:
            dispatch_module._kill_descendants()
            raise AssertionError("expected ContainmentError")
        except dispatch_module.ContainmentError:
            pass
        assert time.monotonic() - began < 5

    # Containment fails closed: no subreaper means no build.
    from book_dev_ai_dispatch import ContainmentError
    fake_libc = mock.Mock()
    fake_libc.prctl.return_value = -1
    with mock.patch("ctypes.CDLL", return_value=fake_libc):
        try:
            _run_process(["true"], input=None, cwd=str(tmp), env=None, timeout=10)
            raise AssertionError("expected ContainmentError")
        except ContainmentError:
            pass
    (tmp / "contain").mkdir()
    with mock.patch("book_dev_ai_dispatch._become_subreaper", side_effect=ContainmentError("no subreaper")), \
            mock.patch("book_dev_ai_dispatch.resolve_codex_cmd", return_value=["true"]):
        result = run_provider("codex", prompt="p", prompt_file=tmp / "contain" / "p.md", mode="implement",
                              max_turns=1, timeout_sec=30, dry_run=False, dispatch_dir=tmp / "contain",
                              cwd=tmp, guard="branch")
    assert result["status"] == "failed" and "subreaper" in result["error"], result

    # npx would run node_modules/.bin/codex from the builder's tree: refused before any worktree exists.
    with mock.patch("book_dev_ai_dispatch.resolve_codex_cmd", return_value=["/usr/bin/npx", "--yes", "@openai/codex"]):
        report = dispatch(**codex, **models)
    assert report["status"] == "unavailable" and "codex" in report["flow"]["error"], report

    # Each builder keeps only its own auth variables.
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "o", "CLAUDE_CODE_OAUTH_TOKEN": "c"}):
        codex_env, claude_env = provider_env("codex", "branch"), provider_env("claude", "branch")
    assert codex_env["OPENAI_API_KEY"] == "o" and "CLAUDE_CODE_OAUTH_TOKEN" not in codex_env
    assert claude_env["CLAUDE_CODE_OAUTH_TOKEN"] == "c" and "OPENAI_API_KEY" not in claude_env

    # Builder commands: model flags and the Codex network-off sandbox.
    with mock.patch("book_dev_ai_dispatch.resolve_claude_cmd", return_value=["claude"]):
        claude_cmd = build_provider_command("claude", prompt_file=tmp / "p", mode="implement", max_turns=3, cwd=tmp,
                                            last_message_path=tmp / "l", guard="branch", model="opus")
    assert claude_cmd[claude_cmd.index("--model") + 1] == "opus"
    assert provider_env("codex", "release") is None, "release flow keeps the env"
    with mock.patch.dict(os.environ, {"GH_TOKEN": "x", "PATH": "/bin"}):
        env = provider_env("codex", "branch")
        assert env is not None and "GH_TOKEN" not in env and env["PATH"] == "/bin"

    # The push sends the reviewed commit even when the branch has moved on since.
    reviewed = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "later"], check=True)
    with mock.patch("book_dev_ai_dispatch.shutil.which", return_value=None):
        pushed = open_pull_request(repo, branch="ai/x", base_branch="main", head=reviewed, title="t", body="b")
    assert pushed["status"] == "pr_failed", pushed
    remote = subprocess.run(["git", "-C", str(origin), "rev-parse", "refs/heads/ai/x"], capture_output=True, text=True)
    assert remote.stdout.strip() == reviewed

    # A missing reviewer CLI is reported before any worktree exists.
    with mock.patch("book_dev_ai_dispatch.resolve_claude_cmd", return_value=["claude"]), mock.patch(
        "book_dev_ai_dispatch.resolve_codex_cmd", return_value=None
    ):
        report = dispatch(**common)
    assert report["status"] == "unavailable", report
    assert not Path(report["flow"]["worktree"]).exists()


def planner_tests(tmp: Path) -> None:
    from book_dev_ai_dispatch import integrate, parse_plan, subtask_ticket, validate_plan

    # Plan parsing: the last ```json block; every subtask is a full brief with disjoint relative paths.
    def brief(sid: str, title: str, paths: list[str], step: str) -> dict:
        return {"id": sid, "title": title, "goal": f"{title} works for the list page",
                "context": [f"{paths[0]}:1 is where {sid} lives; tests follow tests/test_api.py style"],
                "paths": paths, "out_of_scope": ["the database schema"], "interfaces": ["GET /x returns {\"items\": []}"],
                "tests_first": [f"test_{sid} asserts {step}"], "steps": [step], "edge_cases": ["empty list", "<div> markup"],
                "acceptance": [f"{step} is covered by a passing test"],
                "verify": [{"command": "python -m pytest -q", "expect": "all tests pass"}],
                "stop_when": ["a change outside paths is needed"]}

    good = {"subtasks": [brief("api", "API", ["src/api/", "tests/test_api.py"], "add GET /x"),
                         brief("ui", "UI", ["web/x.tsx"], "show x")]}
    text = "thinking\n```json\n{\"subtasks\": []}\n```\nfinal:\n```json\n" + json.dumps(good) + "\n```\n"
    plan = parse_plan(text)
    assert plan and [t["id"] for t in plan] == ["api", "ui"], plan
    problems: list[str] = []
    assert parse_plan("no json", problems) is None and "json" in problems[0]
    for bad_paths in (["src/api/x.py"], ["/etc/passwd"], ["../up"], ["src/*.py"], ["AGENTS.md"], []):
        broken = json.loads(json.dumps(good))
        broken["subtasks"][1]["paths"] = bad_paths
        assert validate_plan(broken) is None, bad_paths  # overlap with src/api/, absolute, parent, glob, agent file, empty
    dup = json.loads(json.dumps(good))
    dup["subtasks"][1]["id"] = "api"
    assert validate_plan(dup) is None
    # A thin plan (the old spec/acceptance/paths shape) is refused with every missing field named.
    thin = {"subtasks": [{"id": "a", "title": "A", "spec": "do it", "acceptance": "works", "paths": ["src/a.py"]}]}
    problems = []
    assert validate_plan(thin, problems) is None
    for field in ("goal", "context", "steps", "out_of_scope", "interfaces", "tests_first", "edge_cases", "verify", "stop_when"):
        assert any(f"`{field}`" in p for p in problems), (field, problems)
    for mutate, needle in ((lambda t: t.update(context=["it is fine"]), "cite the files"),
                           (lambda t: t.update(verify=[{"command": "pytest"}]), "expect"),
                           (lambda t: t.update(steps=["edit <path to file>"]), "placeholders"),
                           (lambda t: t.update(edge_cases=["..."]), "placeholders")):
        weak = json.loads(json.dumps(good))
        mutate(weak["subtasks"][0])
        problems = []
        assert validate_plan(weak, problems) is None and any(needle in p for p in problems), (needle, problems)
    # The brief the builder gets has every section, in working order.
    spec = plan[0]["spec"]
    order = ["## Goal", "## Known facts", "## Scope", "## Interfaces", "## Tests first", "## Steps", "## Edge cases",
             "## Acceptance criteria", "## Verification", "## Stop conditions"]
    assert [spec.index(h) for h in order] == sorted(spec.index(h) for h in order), spec
    assert "`python -m pytest -q` -> expected: all tests pass" in spec and "- [ ] add GET /x" in spec
    child = subtask_ticket({"selected": {"task": {"id": 1, "title": "T"}, "repository": {"path": "/r"}}}, plan[0])
    assert child["selected"]["scope_paths"] == ["src/api/", "tests/test_api.py"]
    assert child["selected"]["task"]["title"].startswith("[api]") and "add GET /x" in child["selected"]["task"]["description"]

    # The builder prompt carries the (sub)task's brief, not only its title.
    prompt = build_prompt(child, mode="implement", branch="ai/x", worktree=tmp)
    assert "add GET /x" in prompt and "## Acceptance criteria" in prompt

    # The planner gets one retry with the reasons when its plan is thin; still thin -> no plan.
    from book_dev_ai_dispatch import run_planner
    prepo = tmp / "planrepo"
    git_repo(prepo)
    pbase = subprocess.run(["git", "-C", str(prepo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    replies = ["```json\n" + json.dumps(thin) + "\n```", "```json\n" + json.dumps(good) + "\n```"]
    prompts: list[str] = []

    def fake_planner(provider, *, prompt, dispatch_dir, label, **_):
        prompts.append(prompt)
        (dispatch_dir / f"{label}.last-message.md").write_text(replies[len(prompts) - 1])
        (dispatch_dir / f"{label}.log").write_text("log\n")
        return {"provider": provider, "status": "dispatched"}

    pdir = tmp / "plandir"
    pdir.mkdir()
    with mock.patch("book_dev_ai_dispatch.run_provider", side_effect=fake_planner):
        got, rec = run_planner({"selected": {"task": {"title": "T", "description": "D"}}}, repo_dir=prepo, base=pbase,
                               dispatch_dir=pdir, model="opus", parallel=2, max_turns=5, timeout_sec=60)
    assert got and [t["id"] for t in got] == ["api", "ui"] and rec["plan_attempts"] == 2, rec
    assert any("`goal`" in p for p in rec["plan_problems"]), "first-round reasons stay in the report"
    assert "rejected" not in prompts[0] and "rejected" in prompts[1] and "`tests_first`" in prompts[1], prompts[1][-2000:]
    assert "\"spec\": \"do it\"" in prompts[1], "the previous plan comes back to be fixed"
    prompts.clear()
    replies[1] = replies[0]
    with mock.patch("book_dev_ai_dispatch.run_provider", side_effect=fake_planner):
        got, rec = run_planner({"selected": {"task": {"title": "T", "description": "D"}}}, repo_dir=prepo, base=pbase,
                               dispatch_dir=pdir, model="opus", parallel=2, max_turns=5, timeout_sec=60)
    assert got is None and len(prompts) == 2 and rec["plan_problems"], rec

    # A timed-out child gets SIGTERM, then SIGKILL; run_child returns promptly.
    from book_dev_ai_dispatch import run_child
    import time
    began = time.monotonic()
    out = run_child([sys.executable, "-c", "import time; time.sleep(60)"], timeout=1)
    assert out["status"] == "timeout" and time.monotonic() - began < 30

    # Integration: plumbing combines disjoint subtask branches into one commit (parents: base + heads).
    repo = tmp / "integ"
    git_repo(repo)
    def run(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()
    (repo / "keep.txt").write_text("k\n")
    (repo / "gone.txt").write_text("g\n")
    run("add", "-A")
    run("commit", "-q", "-m", "base")
    base = run("rev-parse", "HEAD")
    heads = {}
    for sid, files in (("a", {"src/a.py": "A\n"}), ("b", {"web/b.tsx": "B\n"})):
        run("checkout", "-q", "-b", f"sub-{sid}", base)
        for name, body in files.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(body)
        if sid == "b":
            run("rm", "-q", "gone.txt")
        run("add", "-A")
        run("commit", "-q", "-m", sid)
        heads[sid] = run("rev-parse", "HEAD")
    run("checkout", "-q", "main")
    combined = integrate(repo, base=base, heads=heads, branch="ai/combined", message="combined")
    assert combined and run("rev-parse", "refs/heads/ai/combined") == combined
    assert set(run("show", "-s", "--format=%P", combined).split()) == {base, *heads.values()}
    files = set(run("ls-tree", "-r", "--name-only", combined).split())
    assert files == {"keep.txt", "src/a.py", "web/b.tsx"}, files
    # A directory replaced by a file (removals before additions) combines too.
    run("checkout", "-q", "-b", "sub-d", base)
    (repo / "dirx").mkdir()
    (repo / "dirx" / "inner.txt").write_text("i\n")
    run("add", "-A")
    run("commit", "-q", "-m", "dir")
    with_dir = run("rev-parse", "HEAD")
    run("rm", "-rq", "dirx")
    (repo / "dirx").write_text("now a file\n")
    run("add", "-A")
    run("commit", "-q", "-m", "file")
    as_file = run("rev-parse", "HEAD")
    run("checkout", "-q", "main")
    swapped = integrate(repo, base=with_dir, heads={"d": as_file}, branch="ai/swap", message="m")
    assert swapped and "dirx" in run("ls-tree", "--name-only", swapped).split()
    # Two subtasks changing the same path, or a head not on base, refuse.
    run("checkout", "-q", "-b", "sub-c", base)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src/a.py").write_text("C\n")
    run("add", "-A")
    run("commit", "-q", "-m", "c")
    clash = run("rev-parse", "HEAD")
    run("checkout", "-q", "main")
    assert integrate(repo, base=base, heads={"a": heads["a"], "c": clash}, branch="ai/x", message="m") is None
    orphan = run("commit-tree", run("rev-parse", "HEAD^{tree}"), "-m", "orphan")
    assert integrate(repo, base=base, heads={"o": orphan}, branch="ai/y", message="m") is None

    # Orchestration (planner and children mocked): split -> children in parallel -> combine -> review -> PR.
    ticket_path = sample_ticket(tmp / "orch")
    work = tmp / "orch" / "repo"
    (work / ".git").rmdir()
    git_repo(work)
    origin = tmp / "orch" / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "main"], check=True)
    obase = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    child_heads = {}
    for sid, name in (("api", "src/api/x.py"), ("ui", "web/x.tsx")):
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", f"ai/sub-{sid}", obase], check=True)
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_text(sid)
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", sid], check=True)
        child_heads[sid] = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"], check=True)

    planned = {"provider": "claude", "status": "dispatched", "last_message": "plan"}
    children_seen: list[list[str]] = []

    def fake_child(command, timeout):
        children_seen.append(command)
        sid = command[command.index("--stamp") + 1].rsplit("-", 1)[1]
        return {"status": "passed", "flow": {"branch": f"ai/sub-{sid}", "head": child_heads[sid]}}

    reviews: list[dict] = []

    def fake_review(provider, *, prompt, mode, dispatch_dir, cwd, label=None, **_):
        reviews.append({"label": label, "prompt": prompt, "files": sorted(p.name for p in cwd.rglob("*") if p.is_file() and ".git" not in p.parts)})
        last = dispatch_dir / f"{label}.last-message.md"
        last.write_text("ok\nVERDICT: PASS\n")
        return {"provider": provider, "status": "dispatched", "last_message": str(last)}

    opened = {"status": "opened", "url": "https://github.com/o/r/pull/9"}
    common = dict(ticket_path=ticket_path, providers=["codex"], reviewer="codex", mode="implement", write_ticket=False,
                  builder_model="gpt-6-luna", reviewer_model="gpt-6.1-sol")
    with mock.patch("book_dev_ai_dispatch._now_stamp", side_effect=[f"20261110T0000{i:02d}Z" for i in range(20)]), \
            mock.patch.dict(os.environ, {"BOOK_DEV_PLANNER": "claude", "BOOK_DEV_PARALLEL": "2"}), \
            mock.patch("book_dev_ai_dispatch.resolve_codex_cmd", return_value=["codex"]), \
            mock.patch("book_dev_ai_dispatch.resolve_claude_cmd", return_value=["claude"]), \
            mock.patch("book_dev_ai_dispatch.run_planner", return_value=(plan, planned)), \
            mock.patch("book_dev_ai_dispatch.run_child", side_effect=fake_child), \
            mock.patch("book_dev_ai_dispatch.run_provider", side_effect=fake_review), \
            mock.patch("book_dev_ai_dispatch.open_pull_request", return_value=opened) as pr:
        report = dispatch(**common)
        assert report["status"] == "dispatched", report
        assert report["flow"]["name"] == "planned-parallel" and [c["id"] for c in report["flow"]["subtasks"]] == ["api", "ui"]
        assert all("--branch-only" in c and "--no-plan" in c for c in children_seen), "children open no PR and do not re-plan"
        assert all(c[c.index("--base") + 1] == obase for c in children_seen), "every child builds on the same base"
        assert all(c[c.index("--builder-effort") + 1] == "xhigh" and c[c.index("--reviewer-effort") + 1] == "high"
                   for c in children_seen), "resolved efforts reach every child"
        assert [r["label"] for r in reviews] == ["codex-review-0"], "the combined change is reviewed once more"
        assert {"x.py", "x.tsx"} <= set(reviews[0]["files"]), reviews[0]["files"]
        assert "Planned by Claude" in pr.call_args.kwargs["body"]

        # A subtask that does not pass blocks the whole job before anything is combined.
        def failing_child(command, timeout):
            sid = command[command.index("--stamp") + 1].rsplit("-", 1)[1]
            return {"status": "review_blocked" if sid == "ui" else "passed",
                    "flow": {"branch": f"ai/sub-{sid}", "head": child_heads[sid]}}
        reviews.clear()
        with mock.patch("book_dev_ai_dispatch.run_child", side_effect=failing_child):
            report = dispatch(**common)
        assert report["status"] == "review_blocked" and reviews == [], report
        # A child that could not run keeps its own status (the cycle retries "unavailable").
        with mock.patch("book_dev_ai_dispatch.run_child", side_effect=lambda c, t: {"status": "unavailable"}):
            assert dispatch(**common)["status"] == "unavailable"

        # One subtask: the normal flow, with the planner's spec in the builder prompt.
        one = [plan[0]]
        builds: list[str] = []

        def one_flow(provider, *, prompt, mode, dispatch_dir, cwd, label=None, **_):
            last = dispatch_dir / f"{label}.last-message.md"
            if mode == "implement":
                builds.append(prompt)
                (cwd / "src" / "api").mkdir(parents=True, exist_ok=True)
                (cwd / "src" / "api" / "y.py").write_text("y")
            else:
                last.write_text("ok\nVERDICT: PASS\n")
            return {"provider": provider, "status": "dispatched", "last_message": str(last)}

        with mock.patch("book_dev_ai_dispatch.run_planner", return_value=(one, planned)), \
                mock.patch("book_dev_ai_dispatch.run_provider", side_effect=one_flow):
            report = dispatch(**common)
        assert report["status"] == "dispatched" and "Planner's brief" in builds[0] and "add GET /x" in builds[0], report


def main() -> int:
    assert parse_providers("grok,codex") == ["grok", "codex"]
    assert parse_providers("codex,codex,grok") == ["codex", "grok"]
    assert parse_providers("claude,codex") == ["claude", "codex"]
    try:
        parse_providers("gemini")
        raise AssertionError("expected unknown provider error")
    except ValueError:
        pass

    with tempfile.TemporaryDirectory() as temp_dir:
        tmp = Path(temp_dir)
        ticket_path = sample_ticket(tmp)
        ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
        prompt = build_prompt(ticket, mode="implement")
        assert "automatic release closeout" in prompt
        assert "push the configured upstream" in prompt
        assert "owner/repo" in prompt

        # Dry-run should not require live provider success.
        report = dispatch(
            ticket_path=ticket_path,
            providers=["grok", "codex"],
            mode="plan",
            max_turns=2,
            timeout_sec=60,
            dry_run=True,
            write_ticket=True,
        )
        assert report["contract"] == "solo-empire.book-dev-engineering-loop-dispatch.v1"
        assert report["status"] in {"dry_run", "unavailable"}
        assert report["policy"]["automatic_release_closeout"] is True
        assert (tmp / "dispatch").is_dir()
        avail = provider_availability()
        assert "grok" in avail and "codex" in avail and "claude" in avail

        with mock.patch("book_dev_ai_dispatch.resolve_claude_cmd", return_value=["claude"]):
            for mode, permission in (("plan", "plan"), ("implement", "acceptEdits")):
                command = build_provider_command(
                    "claude",
                    prompt_file=tmp / "prompt.md",
                    mode=mode,
                    max_turns=3,
                    cwd=tmp,
                    last_message_path=tmp / "claude.last-message.md",
                )
                assert command[:2] == ["claude", "-p"]
                assert command[command.index("--permission-mode") + 1] == permission
                assert "--dangerously-skip-permissions" not in command
                tools = command[command.index("--allowedTools") + 1].split(",")
                assert "--tools" in command, "--allowedTools only grants permissions; it does not restrict availability"
                assert command[command.index("--tools") + 1].split(",") == tools
                assert command[command.index("--permission-prompts") + 1] == "none"
                assert "--strict-mcp-config" in command
                assert json.loads(command[command.index("--mcp-config") + 1]) == {"mcpServers": {}}
                # Target repositories carry no Claude settings: the guard hook rides along.
                hook = json.loads(command[command.index("--settings") + 1])["hooks"]["PreToolUse"][0]
                assert hook["matcher"] == "*"
                assert "book_dev_dispatch_guard.py" in hook["hooks"][0]["command"]
                assert hook["hooks"][0]["command"].endswith("--policy release")
                if mode == "plan":
                    assert "Edit" not in tools and "Bash" not in tools
                else:
                    assert "Edit" in tools and "Bash" in tools

        # Force unavailable path with mocked resolvers.
        with mock.patch("book_dev_ai_dispatch.resolve_grok_cmd", return_value=None), mock.patch(
            "book_dev_ai_dispatch.resolve_codex_cmd", return_value=None
        ):
            unavailable = dispatch(
                ticket_path=ticket_path,
                providers=["grok", "codex"],
                mode="plan",
                dry_run=False,
                write_ticket=False,
            )
            assert unavailable["status"] == "unavailable"
            assert all(item["status"] == "unavailable" for item in unavailable["results"])

        # The selected repository must be a real checkout; providers run inside it.
        assert resolve_repo_dir(ticket) == (tmp / "repo").resolve()
        try:
            resolve_repo_dir({"selected": {"repository": {"path": str(tmp / "missing")}}})
            raise AssertionError("expected missing checkout error")
        except ValueError:
            pass
        assert str(tmp / "current.md") in build_prompt(ticket, mode="plan")

        with mock.patch("book_dev_ai_dispatch.resolve_claude_cmd", return_value=["claude"]):
            command = build_provider_command(
                "claude",
                prompt_file=tmp / "prompt.md",
                mode="implement",
                max_turns=3,
                cwd=tmp,
                last_message_path=tmp / "claude.last-message.md",
                read_dirs=(tmp / "exec",),
            )
            assert command[command.index("--add-dir") + 1] == str(tmp / "exec")
            completed = mock.Mock(returncode=0, stdout="done", stderr="")
            (tmp / "dispatch" / "x").mkdir(parents=True)
            with mock.patch("book_dev_ai_dispatch._run_process", return_value=completed) as run:
                result = run_provider(
                    "claude",
                    prompt="p",
                    prompt_file=tmp / "prompt.md",
                    mode="implement",
                    max_turns=3,
                    timeout_sec=60,
                    dry_run=False,
                    dispatch_dir=tmp / "dispatch" / "x",
                    cwd=tmp / "repo",
                )
            assert result["status"] == "dispatched"
            # The builder runs without push/deploy credentials but keeps its own login.
            with mock.patch.dict(os.environ, {"GH_TOKEN": "x", "VERCEL_TOKEN": "x", "SSH_AUTH_SOCK": "/s",
                                              "RAILWAY_PROJECT_ID": "x", "SENTRY_DSN": "x", "JAVA_HOME": "/j",
                                              "APP_URL": "https://u:p@db.example/x", "KUBECONFIG": "/k", "MIRROR": "https://:tok@host/x",
                                              "CLAUDE_CODE_OAUTH_TOKEN": "c", "PATH": os.environ.get("PATH", "")}):
                env = provider_env("claude")
                assert "GH_TOKEN" not in env and "VERCEL_TOKEN" not in env and "SSH_AUTH_SOCK" not in env
                assert "MIRROR" not in env and "APP_URL" not in env and "KUBECONFIG" not in env
                assert "RAILWAY_PROJECT_ID" not in env and "SENTRY_DSN" not in env and env["JAVA_HOME"] == "/j"
                assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "c" and "PATH" in env
                assert provider_env("grok") is None and provider_env("codex", "release") is None
                assert "GH_TOKEN" not in provider_env("codex"), "a Codex worktree build is scrubbed too"
                assert provider_env("claude", "release") is None, "the legacy flow keeps its release credentials"
            assert run.call_args.kwargs["cwd"] == str(tmp / "repo")

    # Claude CLI lookup: explicit override, then the desktop app's newest build.
    with tempfile.TemporaryDirectory() as temp_dir:
        home = Path(temp_dir)
        builds = home / ".claude" / "remote" / "ccd-cli"
        builds.mkdir(parents=True)
        for name in ("2.1.9-aaa", "2.1.295-bbb", "notes.txt"):
            (builds / name).write_text("#!/bin/sh\n")
            (builds / name).chmod(0o755)
        env = {key: value for key, value in os.environ.items() if key != "BOOK_DEV_CLAUDE_CLI"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "book_dev_ai_dispatch.shutil.which", return_value=None
        ), mock.patch("book_dev_ai_dispatch.Path.home", return_value=home):
            assert resolve_claude_cmd() == [str(builds / "2.1.295-bbb")]
            os.environ["BOOK_DEV_CLAUDE_CLI"] = str(builds / "2.1.9-aaa")
            assert resolve_claude_cmd() == [str(builds / "2.1.9-aaa")]
            os.environ["BOOK_DEV_CLAUDE_CLI"] = str(home / "missing")
            assert resolve_claude_cmd() is None

    with tempfile.TemporaryDirectory() as temp_dir:
        build_review_tests(Path(temp_dir))
    with tempfile.TemporaryDirectory() as temp_dir:
        planner_tests(Path(temp_dir))

    # Git and gh timeouts come back as results, so the flow still writes its report.
    from book_dev_ai_dispatch import _git
    with mock.patch("book_dev_ai_dispatch.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 120)):
        timed_out = _git(Path("."), "fetch", "-q", "origin", "main")
        assert timed_out.returncode == 124 and "timed out" in timed_out.stderr
    with tempfile.TemporaryDirectory() as temp_dir:
        with mock.patch("book_dev_ai_dispatch._git", return_value=subprocess.CompletedProcess([], 0, "", "")), mock.patch(
            "book_dev_ai_dispatch.shutil.which", return_value="gh"
        ), mock.patch("book_dev_ai_dispatch.subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 120)):
            pr = open_pull_request(Path(temp_dir), branch="ai/x", base_branch="main", head="abc", title="t", body="b")
        assert pr["status"] == "pr_failed" and "timed out" in pr["error"], pr

    print("Book Dev AI dispatch tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
