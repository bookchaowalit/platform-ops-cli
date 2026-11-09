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
        assert review_cmd[review_cmd.index("-c") + 1] == "project_doc_max_bytes=0"
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
    models = dict(builder_model="gpt-6-luna", reviewer_model="gpt-6-astra")
    stamps = mock.patch("book_dev_ai_dispatch._now_stamp", side_effect=[f"20261109T0000{i:02d}Z" for i in range(10)])
    with stamps, mock.patch("book_dev_ai_dispatch.resolve_codex_cmd", return_value=["codex"]):
        report = dispatch(**codex, **models, dry_run=True)
        assert report["status"] == "dry_run", report
        build_cmd, review_cmd = (item["command"] for item in report["results"])
        assert build_cmd[build_cmd.index("--sandbox") + 1] == "workspace-write"
        assert "sandbox_workspace_write.network_access=false" in build_cmd
        assert {"sandbox_workspace_write.exclude_slash_tmp=true",
                "sandbox_workspace_write.exclude_tmpdir_env_var=true"} <= set(build_cmd)
        assert build_cmd[build_cmd.index("-m") + 1] == "gpt-6-luna"
        assert review_cmd[review_cmd.index("-m") + 1] == "gpt-6-astra"
        assert review_cmd[review_cmd.index("--sandbox") + 1] == "read-only"
        assert report["flow"]["same_family"] is True
        # No user config, MCP servers, plugins or app connectors in either Codex role.
        for cmd in (build_cmd, review_cmd):
            assert "--ignore-user-config" in cmd and "mcp_servers={}" in cmd, cmd
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
        assert [c["model"] for c in calls] == ["gpt-6-luna", "gpt-6-astra", "gpt-6-luna", "gpt-6-astra"]
        assert "do not commit" in calls[0]["prompt"].lower() and "do not commit" in calls[2]["prompt"].lower()
        log = subprocess.run(["git", "-C", str(repo), "log", "--format=%s", report["flow"]["branch"]],
                             capture_output=True, text=True).stdout.splitlines()
        assert [line.split(":")[0] for line in log] == ["codex-fix-1", "codex-build", "init"], log
        assert pr.call_args.kwargs["head"] == report["flow"]["reviews"][-1]["head"]
        assert "gpt-6-luna" in pr.call_args.kwargs["body"] and "gpt-6-astra" in pr.call_args.kwargs["body"]

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

        # A Codex build that edits nothing still ends as no_changes.
        calls.clear()
        with mock.patch("book_dev_ai_dispatch.run_provider",
                        side_effect=lambda p, **k: {"provider": p, "status": "dispatched", "last_message": ""}):
            report = dispatch(**codex, **models)
        assert report["status"] == "no_changes", report

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
            with mock.patch("book_dev_ai_dispatch.subprocess.run", return_value=completed) as run:
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
