#!/usr/bin/env python3
"""PreToolUse guard for headless Claude Code runs started by the Book Dev dispatcher.

Target repositories carry no Claude Code settings, so the dispatcher passes this
hook with ``--settings``. It reads the hook payload on stdin and denies:

  all policies
    - secret paths (``.env``, keys, ``~/.ssh``, ``~/.config``, ...) and
      credential-named environment variables, in any tool
    - writes outside the run's working directory (edit tools, shell
      redirections, ``tee``/``cp``/``mv``/``dd``); /dev is fine
    - sudo, ``git reset --hard``, ``git clean``, force pushes, recursive ``rm``
      outside the working directory, environment dumps
  ``branch`` (build-review worktree)
    - every push, branch switch, merge, tag or worktree change
    - GitHub, deploy, cloud, secret-manager and publish CLIs
    - outbound HTTP writes (curl/wget with a body or non-GET method)
    - writes to agent instruction files (AGENTS.md, CLAUDE.md, .claude/, ...)

Shell commands are tokenized with quoting respected, split on control
operators, and nested ``sh -c``/``eval``/``$(...)`` text is checked too. It is
defense in depth on top of the prompt rules and the dispatcher's own checks,
not a sandbox: a program written to evade string matching still can. Any
payload it cannot parse is denied.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path

POLICIES = ("release", "branch")

_LEAD = r"(^|[/\s'\"=<>(:,])"
_TAIL = r"($|[\s'\"/;|&)<>,:])"
SECRET_PATH_RE = re.compile(
    _LEAD + r"("
    r"\.env(\.(?!example\b|sample\b|template\b|dist\b)[\w.-]+)?"
    r"|[\w.-]*\.(pem|key|p12|pfx|keystore|jks)"
    r"|id_(rsa|dsa|ecdsa|ed25519)[\w.-]*"
    r"|/proc/[^/\s]+/(environ|cmdline|mem)"
    r"|\.netrc|\.git-credentials|\.npmrc|\.pypirc|credentials\.json|book-dev-dispatch\.env"
    r")" + _TAIL
    + r"|" + _LEAD + r"(~|\$HOME|\$\{HOME\}|/home/[^/\s]+|/root)/\.(ssh|config|aws|gnupg|docker|kube|infisical)(/|$|[\s'\"<>])",
    re.IGNORECASE,
)
SECRET_ENV_RE = re.compile(
    r"\$\{?[A-Z0-9_]*(TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY|_PAT)\b",
)
AGENT_INSTRUCTION_RE = re.compile(
    r"(^|/)((AGENTS|CLAUDE|GEMINI)[^/]*\.md$|\.(agents|codex|claude|gemini|cursor)/|\.github/(agents|copilot-instructions))",
    re.IGNORECASE,
)
PATH_KEYS = ("file_path", "path", "notebook_path")
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
# Wrappers that run another program, with the options that take a separate value.
WRAPPERS: dict[str, set[str]] = {
    "env": {"-u", "--unset", "-C", "--chdir"},
    "command": set(), "builtin": set(), "exec": {"-a"}, "nohup": set(), "time": {"-f", "-o"},
    "nice": {"-n", "--adjustment"}, "ionice": {"-c", "-n", "-p"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-I", "-i", "-n", "-P", "-L", "-l", "-d", "-E", "-e", "-s", "-a", "--arg-file", "--delimiter", "--max-args"},
    "npx": {"-p", "--package"}, "bunx": {"-p", "--package"}, "uvx": {"--from", "--with", "-p", "--python"},
    "pipx": set(), "watch": {"-n", "--interval"}, "flock": {"-w", "--timeout"}, "setsid": set(),
}
SHELL_KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "!", "{", "}", "[[", "]]"}
SKIP_SEGMENT = {"for", "case", "select", "esac", "in", "function"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "pwsh", "powershell"}
ESCALATE = {"sudo", "su", "doas", "pkexec", "runuser"}
# Release actions the build-review builder must leave to the dispatcher and owner.
BRANCH_DENY_CLI = {
    "gh", "hub", "glab", "vercel", "railway", "wrangler", "flyctl", "fly", "netlify", "heroku",
    "render", "kubectl", "helm", "terraform", "tofu", "pulumi", "aws", "gcloud", "gsutil", "az",
    "doppler", "infisical", "vault", "op", "twine", "ssh", "scp", "rsync", "sftp",
}
PUBLISH = {"npm", "pnpm", "yarn", "bun", "cargo", "gem", "poetry", "uv", "docker", "podman"}
PUBLISH_VERBS = {"publish", "push", "login", "deprecate", "unpublish", "owner", "dist-tag", "yank"}
# Git subcommands the builder may run in a worktree branch; anything else (aliases,
# push, merge, switch, remote, config writes ...) is denied.
GIT_BRANCH_ALLOW = {
    "add", "am", "apply", "blame", "branch", "cat-file", "check-attr", "check-ignore", "checkout",
    "cherry", "cherry-pick", "commit", "describe", "diff", "diff-files", "diff-index", "diff-tree",
    "fetch", "format-patch", "grep", "help", "log", "ls-files", "ls-remote", "ls-tree", "merge-base",
    "mv", "name-rev", "notes", "range-diff", "rebase", "reflog", "reset", "restore", "rev-list", "rev-parse",
    "revert", "rm", "shortlog", "show", "show-ref", "stash", "status", "symbolic-ref", "var",
    "version", "whatchanged",
}
# Options of allowed subcommands that run an arbitrary program.
GIT_EXEC_OPTS = {"--exec", "--upload-pack", "--receive-pack", "--ext-diff", "--textconv", "--open-files-in-pager"}
# Short forms per subcommand: rebase -x, fetch/ls-remote -u, grep -O (also inside clusters such as -ux).
GIT_EXEC_SHORT = {"rebase": "x", "fetch": "u", "ls-remote": "u", "grep": "O"}
# Environment overrides that change which config, hooks, ssh or editor git runs.
GIT_ENV_RE = re.compile(
    r"^(GIT_CONFIG\w*|GIT_SSH\w*|GIT_EXEC_PATH|GIT_DIR|GIT_WORK_TREE|GIT_COMMON_DIR|GIT_ASKPASS|SSH_ASKPASS"
    r"|GIT_PROXY_COMMAND|GIT_EXTERNAL_DIFF|GIT_EDITOR|GIT_SEQUENCE_EDITOR|GIT_TEMPLATE_DIR|GIT_INDEX_FILE)="
    r"|^(EDITOR|VISUAL|PAGER|GIT_PAGER|LESSOPEN|LESSCLOSE)=(?!(cat|true|:|less|more)?$)"
)
GIT_BRANCH_DENY = {"push", "switch", "merge", "tag", "worktree", "remote", "filter-branch", "filter-repo", "update-ref"}
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
CURL_VALUE_SHORT = set("oHuAewmxbcKErYyzCPQUDJ")
CONTROL_OPS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;", "\n"}
SUBST_RE = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")


def _deny(reason: str) -> str:
    return f"Book Dev dispatch guard: {reason}"


class _Ctx:
    """Where a shell command runs: the permitted root and the directory after any `cd`."""

    def __init__(self, root: Path, policy: str) -> None:
        self.root = root
        self.here = root
        self.policy = policy


def _resolve(path: str, base: Path) -> Path | None:
    if "$" in path:
        return None  # a variable could point anywhere
    target = Path(os.path.expanduser(path))
    if not target.is_absolute():
        target = base / target
    try:
        return target.resolve()
    except OSError:
        return None


def _within(resolved: Path | None, root: Path) -> bool:
    return resolved is not None and (resolved == root or root in resolved.parents)


HOME_SECRET_DIRS = tuple(Path.home() / name for name in (".ssh", ".config", ".aws", ".gnupg", ".docker", ".kube", ".infisical"))


def _secret_resolved(path: str, base: Path) -> bool:
    """A relative path such as ../../.ssh/id_rsa still lands in a secret directory."""
    resolved = _resolve(path, base)
    return resolved is not None and (
        any(resolved == secret or secret in resolved.parents for secret in HOME_SECRET_DIRS)
        or bool(SECRET_PATH_RE.search(" " + resolved.as_posix() + " "))
    )


def _write_reason(path: str, ctx: _Ctx) -> str | None:
    resolved = _resolve(path, ctx.here)
    if resolved is None:
        return "write target could not be resolved"
    if Path("/dev") in resolved.parents:
        return None
    if not _within(resolved, ctx.root):
        return "writes are limited to the working directory"
    if ".git" in resolved.relative_to(ctx.root).parts:
        return "git metadata (config, hooks) is not writable"
    if ctx.policy == "branch" and AGENT_INSTRUCTION_RE.search(resolved.relative_to(ctx.root).as_posix()):
        return "agent instruction files go to the owner, not the builder"
    return None


def _writes_reason(paths: list[str], ctx: _Ctx) -> str | None:
    for path in paths:
        reason = _write_reason(path, ctx)
        if reason:
            return reason
    return None


def _tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in CONTROL_OPS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def _strip_prefix(argv: list[str]) -> list[str]:
    """Drop keywords, env assignments and wrappers so argv[0] is the real program."""
    while argv:
        head = argv[0]
        name = os.path.basename(head)
        if head in SHELL_KEYWORDS or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", head):
            argv = argv[1:]
        elif name in WRAPPERS and len(argv) > 1:
            takes_value = WRAPPERS[name]
            rest = argv[1:]
            while rest:
                if rest[0] == "--":
                    rest = rest[1:]
                    break
                if rest[0] in takes_value:
                    rest = rest[2:]
                elif rest[0].startswith("-") or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", rest[0]):
                    rest = rest[1:]
                elif name == "timeout" and re.fullmatch(r"[\d.]+[smhd]?", rest[0]):
                    rest = rest[1:]
                    break
                else:
                    break
            if not rest:
                return [name]  # bare `env FOO=1` still prints the environment
            argv = rest
        else:
            break
    return argv


def _git_reason(args: list[str], policy: str) -> str | None:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if policy == "branch" and (args[i] == "-c" or args[i].startswith(("-c", "--config-env"))):
            return "git -c overrides (hooks, aliases, remotes, credentials) are not allowed"
        if policy == "branch" and args[i].split("=", 1)[0] in {"--git-dir", "--work-tree", "--exec-path", "--namespace"}:
            return "git must run on this worktree's own repository"
        i += 2 if args[i] in GIT_VALUE_OPTS else 1
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1:]
    short = "".join(item[1:] for item in rest if re.fullmatch(r"-[A-Za-z]+", item))
    longs = {item.split("=", 1)[0] for item in rest if item.startswith("--")}
    if sub == "reset" and "--hard" in longs:
        return "git reset --hard discards work"
    if sub == "clean":
        return "git clean deletes untracked work"
    if sub == "push" and (
        "f" in short or "d" in short
        or longs & {"--force", "--force-with-lease", "--force-if-includes", "--mirror", "--delete", "--prune"}
        or any(item.startswith(("+", ":")) for item in rest)
    ):
        return "force or deleting pushes are never allowed"
    if sub == "branch" and ({"D", "d", "m", "M", "f"} & set(short) or longs & {"--delete", "--move", "--force"}):
        return "deleting, renaming or forcing branches is not allowed"
    if policy == "branch":
        if sub not in GIT_BRANCH_ALLOW or sub in GIT_BRANCH_DENY:
            return f"git {sub} is not on the builder's list; commit on this branch only"
        long_opts = {item.split("=", 1)[0] for item in rest if item.startswith("--") and len(item) > 2}
        # Git accepts unambiguous prefixes (`--exe`), so a prefix of an exec option counts too.
        prefixed = any(len(opt) >= 4 and any(full.startswith(opt) for full in GIT_EXEC_OPTS) for opt in long_opts)
        short_exec = GIT_EXEC_SHORT.get(sub)
        clustered = short_exec is not None and any(
            item.startswith("-") and not item.startswith("--") and short_exec in item[1:] for item in rest
        )
        if prefixed or clustered:
            return f"git {sub} options that run another program are not allowed"
        output = next((item.split("=", 1)[1] for item in rest if item.startswith("--output=")), None)
        if output is None and "--output" in rest and rest.index("--output") + 1 < len(rest):
            output = rest[rest.index("--output") + 1]
        if output is not None:
            return "git --output is not allowed; redirect inside the working directory instead"
        if sub in GIT_BRANCH_DENY:
            return f"git {sub} is the dispatcher's or owner's step; commit on this branch only"
        if sub == "checkout" and "--" not in rest:
            return "do not switch branches; use `git checkout -- <path>` or `git restore` for files"
    return None


def _parse(args: list[str], value_opts: set[str]) -> tuple[list[str], dict[str, list[str]]]:
    """Split args into positionals and option values (`-o x`, `-ox`, `-sSLo x`, `--opt=x`, `--opt x`)."""
    positional: list[str] = []
    values: dict[str, list[str]] = {}
    i = 0
    while i < len(args):
        item = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        if item == "--":
            positional += args[i + 1:]
            break
        if item.startswith("--"):
            name, eq, value = item.partition("=")
            if name in value_opts:
                if eq:
                    values.setdefault(name, []).append(value)
                elif nxt is not None:
                    values.setdefault(name, []).append(nxt)
                    i += 1
        elif item.startswith("-") and len(item) > 1:
            for j, char in enumerate(item[1:], start=1):
                if f"-{char}" in value_opts:
                    rest = item[j + 1:]
                    if rest:
                        values.setdefault(f"-{char}", []).append(rest)
                    elif nxt is not None:
                        values.setdefault(f"-{char}", []).append(nxt)
                        i += 1
                    break
        else:
            positional.append(item)
        i += 1
    return positional, values


# Package-manager global options whose value is not the subcommand.
PM_VALUE_OPTS = {"--filter", "-F", "--prefix", "-C", "--dir", "--workspace", "--cwd", "--registry",
                 "--userconfig", "--loglevel", "--cache", "--config", "-c", "--context", "-H", "--host"}


def _http_write(program: str, args: list[str]) -> bool:
    long_writes = {"--data", "--data-raw", "--data-binary", "--data-urlencode", "--form", "--form-string",
                   "--upload-file", "--json", "--post-data", "--post-file", "--body-data", "--body-file"}
    methods: list[str] = []
    if program in {"http", "https", "xh"}:
        # HTTPie: `http [METHOD] URL [field=value | field:=json | field@file ...]`
        positional = [item for item in args if not item.startswith("-")]
        if {"POST", "PUT", "PATCH", "DELETE"} & set(positional):
            return True
        if any(re.match(r"[^=:@\s]+(:=@?|=@?|@)(?!=)", item) and "==" not in item for item in positional[1:]):
            return True
    for i, item in enumerate(args):
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if item.startswith("--"):
            name, _, value = item.partition("=")
            if name in long_writes:
                return True
            if name in {"--request", "--method"}:
                methods.append(value or nxt)
        elif item.startswith("-") and len(item) > 1 and program in {"curl", "xh", "http", "https"}:
            flags = item[1:]
            for j, char in enumerate(flags):
                if char in "dFT":
                    return True
                if char == "X":
                    methods.append(flags[j + 1:] or nxt)
                    break
                if char in CURL_VALUE_SHORT:
                    break
    return any(method.upper() not in {"GET", "HEAD", "OPTIONS"} for method in methods)


def _segment_reason(argv: list[str], ctx: _Ctx, depth: int) -> str | None:
    # Redirections: `> f`, `>> f`, `&> f` are writes; `>&2`, `< f` and here-doc words are not.
    words: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if set(token) <= set("<>&"):
            target = argv[i + 1] if i + 1 < len(argv) else ""
            if ">" in token and target and not re.fullmatch(r"\d+|-", target):
                reason = _write_reason(target, ctx)
                if reason:
                    return reason
            i += 2
            continue
        words.append(token)
        i += 1
    if ctx.policy == "branch":
        for k, word in enumerate(argv):
            if os.path.basename(word) != "env":
                continue
            # env may only set or unset variables; --chdir, --split-string and the rest are denied.
            rest = argv[k + 1:]
            while rest:
                opt = rest[0]
                if opt in {"-u", "--unset"}:
                    rest = rest[2:]
                elif opt in {"-i", "--ignore-environment", "-"} or opt.startswith("--unset=") or re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_]*=.*", opt
                ):
                    rest = rest[1:]
                elif opt.startswith("-"):
                    return f"env {opt} is not allowed; only VAR=value, -i and -u are"
                else:
                    break
    if ctx.policy == "branch" and any(GIT_ENV_RE.match(word) for word in argv):
        return "git environment overrides (config, ssh, hooks, editor) are not allowed"
    for word in argv:
        if ("/" in word or word.startswith((".", "~"))) and _secret_resolved(word.split("=", 1)[-1], ctx.here):
            return "the command touches a secret or credential path"
    argv = _strip_prefix(words)
    if not argv or os.path.basename(argv[0]) in SKIP_SEGMENT:
        return None
    program = os.path.basename(argv[0]).lower()
    if program.endswith(".exe"):
        program = program[:-4]
    args = argv[1:]
    policy = ctx.policy
    positional = [item for item in args if not item.startswith("-")]
    if program in {"cd", "pushd"}:
        target = _resolve(positional[0], ctx.here) if positional else None
        if not _within(target, ctx.root):
            return "stay inside the working directory (cd)"
        ctx.here = target  # type: ignore[assignment]
        return None
    if program in ESCALATE:
        return f"{program} is not allowed"
    if program in SHELLS:
        for j, item in enumerate(args):
            is_c = (re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", item) and not item.startswith("--")) or item.lower() in {
                "-command", "-encodedcommand", "-ec"}
            if is_c:
                if item.lower() in {"-encodedcommand", "-ec"}:
                    return "encoded PowerShell commands cannot be checked"
                if j + 1 < len(args):
                    return _command_reason(args[j + 1], ctx, depth + 1)
        return None
    if program == "eval" and args:
        return _command_reason(" ".join(args), ctx, depth + 1)
    names = [item for item in args if not item.startswith(("-", "+"))]
    if program == "printenv" or (program in {"env", "export", "declare", "typeset"} and not names) or (
        program == "set" and not args
    ):
        return "reading environment variables can expose credentials"
    if program == "git":
        for k, item in enumerate(args[:-1]):
            if item == "-C" and policy == "branch" and not _within(_resolve(args[k + 1], ctx.here), ctx.root):
                return "git -C must stay inside the working directory"
        return _git_reason(args, policy)
    # `npx`-style runners execute a binary; `npm run <script>` names a package script.
    runs_binary = {"npm": {"exec", "x"}, "pnpm": {"exec", "dlx"}, "yarn": {"exec", "dlx"}, "bun": {"x"}, "uv": {"run"}}
    if program in runs_binary and len(positional) > 1 and positional[0] in runs_binary[program]:
        runner = args[args.index(positional[0]) + 1:]
        while runner and runner[0].startswith("-"):
            runner = runner[1:]
        reason = _segment_reason(runner, ctx, depth)
        if reason:
            return reason
    if program in {"yarn", "pnpm", "bun"} and positional[:1] and positional[0] in BRANCH_DENY_CLI and policy == "branch":
        return f"{program} {positional[0]} runs a release or cloud CLI"
    if program == "rm" and any(re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*|--recursive", item) for item in args):
        targets = [item for item in args if not item.startswith("-")]
        if not targets or any(item in {"*", ".", "./"} or not _within(_resolve(item, ctx.here), ctx.root) for item in targets):
            return "recursive rm is limited to paths inside the working directory"
    targets: list[str] = []
    if program in {"tee", "touch", "mkdir", "truncate", "rmdir", "unlink", "rm", "chmod", "chown", "shred"}:
        targets = positional[1:] if program in {"chmod", "chown"} else positional
    elif program in {"cp", "install", "ln"}:
        paths, opts = _parse(args, {"-t", "--target-directory", "-S", "--suffix", "-m", "--mode", "-o", "--owner", "-g", "--group"})
        target_dirs = opts.get("-t", []) + opts.get("--target-directory", [])
        targets = target_dirs or (paths[-1:] if len(paths) > 1 else [])
    elif program == "mv":
        targets = positional  # the source disappears too
    elif program == "dd":
        targets = [item[3:] for item in args if item.startswith("of=")]
    elif program in {"sed", "perl"} and any(re.fullmatch(r"-[A-Za-z]*i.*|--in-place(=.*)?", item) for item in args):
        paths, opts = _parse(args, {"-e", "--expression", "-f", "--file", "-l", "--line-length"})
        scripted = any(name in opts for name in ("-e", "--expression", "-f", "--file"))
        targets = paths if scripted else paths[1:]
    elif program in {"curl", "wget"}:
        out_opts = ({"-o", "--output", "--output-dir", "-D", "--dump-header", "-c", "--cookie-jar",
                     "--trace", "--trace-ascii", "--stderr", "--libcurl", "--etag-save", "--hsts", "--alt-svc"} if program == "curl"
                    else {"-O", "--output-document", "-P", "--directory-prefix", "-o", "--output-file", "-a", "--append-output"})
        value_opts = out_opts | ({"-H", "-d", "-u", "-A", "-e", "-X", "-F", "-T", "-m", "-w", "-x", "-b", "-c", "-K", "-E", "-r", "-C", "-Y", "-y", "-z", "-U", "-Q", "-P", "-D", "-J"}
                                 if program == "curl" else set())
        _, opts = _parse(args, value_opts)
        targets = [value for name in out_opts for value in opts.get(name, [])]
    reason = _writes_reason(targets, ctx)
    if reason:
        return reason
    if policy == "branch":
        if program in BRANCH_DENY_CLI:
            return f"{program} performs release, cloud or remote actions reserved for the dispatcher and owner"
        # The subcommand after global options (`pnpm --filter app publish`), plus the next word
        # for `yarn npm publish` / `docker image push`; later words are arguments (`--grep publish`).
        words, _ = _parse(args[:args.index("--")] if "--" in args else args, PM_VALUE_OPTS)
        verbs = PUBLISH_VERBS & set(words[:2] if words[:1] in (["npm"], ["image"], ["buildx"]) else words[:1])
        if program in {"docker", "podman"} and "--push" in args:
            return f"{program} --push publishes an image"
        if program in PUBLISH and verbs:
            return f"{program} {sorted(verbs)[0]} is a release action"
        if program in {"curl", "wget", "http", "https", "xh"} and _http_write(program, args):
            return "outbound HTTP writes are not allowed from the builder"
    return None


def _command_reason(command: str, ctx: _Ctx, depth: int = 0) -> str | None:
    if depth > 4:
        return "nested shells are too deep to check"
    if SECRET_PATH_RE.search(command):
        return "the command touches a secret or credential path"
    if SECRET_ENV_RE.search(command):
        return "the command reads a credential-named environment variable"
    for match in SUBST_RE.finditer(command):
        reason = _command_reason(match.group(1) or match.group(2) or "", ctx, depth + 1)
        if reason:
            return reason
    for segment in _segments(_tokens(command.replace("`", " ; "))):
        reason = _segment_reason(segment, ctx, depth)
        if reason:
            return reason
    return None


def decide(payload: dict, policy: str) -> str | None:
    """Return a deny reason, or None to let Claude Code's normal permissions decide."""

    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    cwd = Path(payload.get("cwd") or os.getcwd()).resolve()
    if tool in SHELL_TOOLS:
        reason = _command_reason(str(tool_input.get("command") or ""), _Ctx(cwd, policy))
        return _deny(reason) if reason else None
    for key in PATH_KEYS:
        value = tool_input.get(key)
        if not isinstance(value, str) or not value:
            continue
        if SECRET_PATH_RE.search(" " + value + " ") or _secret_resolved(value, cwd):
            return _deny("secret and credential paths are off limits")
        if tool in WRITE_TOOLS:
            reason = _write_reason(value, _Ctx(cwd, policy))
            if reason:
                return _deny(reason)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PreToolUse guard for Book Dev dispatcher Claude runs.")
    parser.add_argument("--policy", choices=POLICIES, default="branch")
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        reason = decide(payload, args.policy)
    except Exception as exc:  # fail closed: an unreadable call is not allowed through
        print(_deny(f"could not check this tool call ({type(exc).__name__})"), file=sys.stderr)
        return 2
    if reason:
        json.dump({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
