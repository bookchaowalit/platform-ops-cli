#!/usr/bin/env python3
"""Edit the architecture documents in the hosted store (SPEC-001 D3, phase 3).

The documents live in store space ``architecture-docs``; Git holds only this tooling. An edit is:

    arch_store.py pull --task fix-links       # working copy in $SOLO_WORKTREES_ROOT/arch-docs-fix-links
    ... edit files there (and regenerate catalogs with the usual tasks, ROOT = the working copy) ...
    arch_store.py status <dir>                 # what would be written
    arch_store.py push <dir>                   # review gate -> `task check` -> write with if_version
    arch_store.py revert <path> <version>      # write an old version back as the newest

The working copy is a throwaway local Git repository: its first commit is the pulled tree, so
``git diff`` shows the edit, and ``.git/store-manifest.json`` records the version each document had
when it was pulled. ``push`` writes nothing when ``task check`` fails, when a document changed in the
store since the pull (``if_version``), or when the review gate stops it. The review gate (owner
decision Q2) runs Codex on edits under ``docs/architecture/decisions/`` (ADRs) or of more than five
documents and stops on a [P0]-[P2] finding; ``--reviewed`` records that the owner reviewed it instead.

The store calls go through platform-knowledge-api ``scripts/store_sync.py`` (its checkout:
``SOLO_KNOWLEDGE_API_ROOT``, else beside this repository's main clone), which mints the tokens;
nothing secret and no document text is printed here.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import TOOLS_ROOT, main_checkout  # noqa: E402

SPACE = "architecture-docs"
MANIFEST = Path(".git") / "store-manifest.json"
REVIEW_FILE = Path(".git") / "arch-review.txt"
ADR_PREFIX = "docs/architecture/decisions/"
REVIEW_OVER = 5  # more documents than this need a review (Q2)
BLOCKING = re.compile(r"\[P[0-2]\]")
GIT = ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
       "-c", "user.name=arch-store", "-c", "user.email=arch-store@localhost"]

Runner = Callable[..., subprocess.CompletedProcess]


def _run(command: Sequence[str], *, cwd: Path | None = None, env: dict | None = None,
         capture: bool = False, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(list(command), cwd=cwd, env=env, text=True, input=stdin,
                          capture_output=capture, check=False)


def knowledge_api_root(environ=os.environ) -> Path:
    override = (environ.get("SOLO_KNOWLEDGE_API_ROOT") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    parent = main_checkout(TOOLS_ROOT.parent).parent
    for candidate in (parent / "platform-knowledge-api", parent / "book-products" / "platform-knowledge-api"):
        if (candidate / "scripts" / "store_sync.py").is_file():
            return candidate
    raise SystemExit("arch_store: no platform-knowledge-api checkout found; set SOLO_KNOWLEDGE_API_ROOT")


URL_KEYS = ("EMPIRE_KNOWLEDGE_API", "SOLO_WEB_PUBLIC_ORIGIN")


def store_env(environ=os.environ) -> dict[str, str]:
    """The environment plus the store's two URLs (not secrets) from client.env when unset, as the
    hub's knowledge_snapshot.py does, so a plain shell works without sourcing anything."""
    env = dict(environ)
    configured = (environ.get("EMPIRE_CLIENT_CONFIG") or "").strip()
    client = Path(configured).expanduser() if configured else Path.home() / ".config" / "empire-platform" / "client.env"
    try:
        lines = client.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if sep and key in URL_KEYS and not env.get(key):
            env[key] = value.strip().strip("'\"")
    return env


def store_sync(args: Sequence[str], *, run: Runner = _run, environ=os.environ) -> int:
    api = knowledge_api_root(environ)
    command = ["uv", "run", "--frozen", "--extra", "mcp", "python", "scripts/store_sync.py", *args]
    return run(command, cwd=api, env=store_env(environ)).returncode


def default_dir(task: str, environ=os.environ) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,60}", task):
        raise SystemExit("arch_store: --task is lower-case letters, digits, '.', '_' or '-'")
    base = Path(environ.get("SOLO_WORKTREES_ROOT") or Path.home() / "bookchaowalit-empire" / "worktrees")
    return base.expanduser() / f"arch-docs-{task}"


def snapshot(environ=os.environ) -> Path | None:
    configured = (environ.get("SOLO_ARCHITECTURE_SNAPSHOT") or "").strip()
    folder = Path(configured).expanduser() if configured else Path.home() / ".cache" / "solo-empire" / SPACE
    return folder.resolve() if (folder / ".snapshot.json").is_file() else None


def pull(directory: Path, *, run: Runner = _run, environ=os.environ) -> int:
    directory = directory.expanduser().absolute()
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        print(f"arch_store: {directory} exists and is not empty; push or remove it first", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="arch-manifest-") as scratch:
        manifest = Path(scratch) / "store-manifest.json"
        args = ["pull", "--space", SPACE, "--out", str(directory), "--manifest", str(manifest)]
        reuse = snapshot(environ)
        if reuse is not None:
            args += ["--reuse", str(reuse)]  # unchanged documents are copied, not fetched
        if store_sync(args, run=run, environ=environ) != 0:
            shutil.rmtree(directory, ignore_errors=True)
            return 1
        for step in (["init", "-q"], ["add", "-A", "-f"], ["commit", "-q", "-m", "pulled from store space architecture-docs"]):
            if run([*GIT, "-C", str(directory), *step]).returncode != 0:
                print(f"arch_store: git {step[0]} failed in {directory}", file=sys.stderr)
                return 1
        shutil.move(str(manifest), directory / MANIFEST)
    print(f"working copy: {directory}\nedit there, then: task arch:push DIR={directory}")
    return 0


def changed_paths(directory: Path, *, run: Runner = _run) -> list[str]:
    """Paths that differ from the last pull or push (git's view, so ignored files stay out)."""
    if run([*GIT, "-C", str(directory), "add", "-A"]).returncode != 0:
        raise SystemExit(f"arch_store: {directory} is not a working copy (git add failed)")
    result = run([*GIT, "-C", str(directory), "diff", "--cached", "--name-only", "-z", "--no-renames", "HEAD"],
                 capture=True)
    return sorted(p for p in (result.stdout or "").split("\0") if p)


def needs_review(paths: Sequence[str]) -> bool:
    return len(paths) > REVIEW_OVER or any(p.startswith(ADR_PREFIX) for p in paths)


REVIEW_PROMPT = (
    "You review an edit to architecture documents in this git working copy. Run `git diff --cached HEAD` "
    "to see it. Look for wrong or contradicting facts, broken links or references, decisions (ADRs) "
    "changed without a recorded reason, secrets or private personal data, and accidental deletions. "
    "List each finding on its own line starting with a severity tag [P0] (harmful), [P1] (wrong), "
    "[P2] (should fix) or [P3] (nit), then the path. If there is nothing to report, answer exactly: no findings."
)


def review(directory: Path, *, run: Runner = _run) -> int:
    """Codex read-only review of the staged edit (Q2). 0 = pass, 1 = blocking finding, 2 = unavailable."""
    if shutil.which("codex") is None and run is _run:
        print("arch_store: this edit needs a review (ADR or more than 5 documents) and codex is not "
              "installed; review the diff yourself and push again with --reviewed", file=sys.stderr)
        return 2
    result = run(["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "-C", str(directory),
                  REVIEW_PROMPT], capture=True, stdin="")
    text = result.stdout or ""
    (directory / REVIEW_FILE).write_text(text, encoding="utf-8")
    if result.returncode != 0:
        print(f"arch_store: the review did not run (codex exit {result.returncode}); push again with "
              "--reviewed after reviewing the diff yourself", file=sys.stderr)
        return 2
    blocking = [line for line in text.splitlines() if BLOCKING.search(line)]
    if blocking:
        print(f"arch_store: review found {len(blocking)} blocking finding(s); nothing written. "
              f"Full review: {directory / REVIEW_FILE}", file=sys.stderr)
        for line in blocking[:20]:
            print(f"  {line.strip()}", file=sys.stderr)
        return 1
    return 0


def check(directory: Path, *, run: Runner = _run) -> int:
    """`task check` of this tooling against the working copy (the old PR gate)."""
    env = {**os.environ, "SOLO_ARCHITECTURE_ROOT": str(directory)}
    return run(["task", "--taskfile", str(TOOLS_ROOT / "Taskfile.yml"), "check"], cwd=directory, env=env).returncode


def push(directory: Path, *, reviewed: bool = False, run: Runner = _run, environ=os.environ) -> int:
    directory = directory.expanduser().resolve()
    if not (directory / MANIFEST).is_file():
        print(f"arch_store: {directory} has no {MANIFEST}; start with `task arch:pull`", file=sys.stderr)
        return 2
    paths = changed_paths(directory, run=run)
    if not paths:
        print("nothing to push")
        return 0
    print(f"{len(paths)} document(s) changed")
    if needs_review(paths) and not reviewed:
        verdict = review(directory, run=run)
        if verdict:
            return verdict
    if check(directory, run=run) != 0:
        print("arch_store: `task check` failed against the working copy; nothing written", file=sys.stderr)
        return 1
    code = store_sync(["commit", "--space", SPACE, "--root", str(directory),
                       "--manifest", str(directory / MANIFEST)], run=run, environ=environ)
    if code == 0:
        # The pushed tree is the new base: the next diff and push start from here.
        run([*GIT, "-C", str(directory), "commit", "-q", "-m", "pushed to store space architecture-docs"])
    return code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Edit the architecture documents in the hosted store")
    commands = parser.add_subparsers(dest="command", required=True)
    p_pull = commands.add_parser("pull", help="new working copy from the store")
    where = p_pull.add_mutually_exclusive_group(required=True)
    where.add_argument("--task", help="name: $SOLO_WORKTREES_ROOT/arch-docs-<task>")
    where.add_argument("--dir", type=Path, help="an explicit new or empty folder")
    p_status = commands.add_parser("status", help="what push would write")
    p_status.add_argument("dir", type=Path)
    p_push = commands.add_parser("push", help="check, then write the changed documents")
    p_push.add_argument("dir", type=Path)
    p_push.add_argument("--reviewed", action="store_true", help="the owner reviewed this edit (skips Codex)")
    p_revert = commands.add_parser("revert", help="write an old version of one document back")
    p_revert.add_argument("path")
    p_revert.add_argument("version", type=int)
    args = parser.parse_args(argv)
    if args.command == "pull":
        return pull(args.dir if args.dir else default_dir(args.task))
    if args.command == "status":
        directory = args.dir.expanduser().resolve()
        for path in changed_paths(directory):
            print(f"  {path}")
        return store_sync(["commit", "--space", SPACE, "--root", str(directory),
                           "--manifest", str(directory / MANIFEST), "--dry-run"])
    if args.command == "push":
        return push(args.dir, reviewed=args.reviewed)
    return store_sync(["revert", "--space", SPACE, "--path", args.path, "--version", str(args.version)])


if __name__ == "__main__":
    raise SystemExit(main())
