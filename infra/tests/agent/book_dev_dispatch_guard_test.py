#!/usr/bin/env python3
"""Offline tests for the Book Dev dispatch PreToolUse guard."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / "scripts" / "workflows"
sys.path.insert(0, str(WORKFLOWS))
from book_dev_dispatch_guard import decide  # noqa: E402
import book_dev_dispatch_guard as guard  # noqa: E402

GUARD = WORKFLOWS / "book_dev_dispatch_guard.py"


def bash(command: str, cwd: Path, policy: str = "branch") -> str | None:
    return decide({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}, policy)


def tool(name: str, cwd: Path, policy: str = "branch", **tool_input: str) -> str | None:
    return decide({"tool_name": name, "tool_input": tool_input, "cwd": str(cwd)}, policy)


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        cwd = Path(temp_dir) / "worktree"
        cwd.mkdir()

        allowed = [
            "git status --porcelain",
            "git add src/app.py && git commit -m 'fix: handle (empty) input'",
            "git diff origin/main...HEAD",
            "git checkout -- src/app.py",
            "set -euo pipefail; npm test 2>&1 | tail -20",
            "env CI=1 npm run build",
            "rm -rf node_modules dist",
            "python3 -c 'print(1)'",
            "cat .env.example",
            "grep -rn process.env src",
            "curl -s https://example.com/health",
            "export NODE_ENV=test && pytest -q",
        ]
        for command in allowed:
            assert bash(command, cwd) is None, (command, bash(command, cwd))

        denied_everywhere = [
            "cat .env",
            "source ./.env.local && npm start",
            "cat ~/.config/book-dev-dispatch.env",
            "ls $HOME/.ssh",
            "cat deploy/server.pem",
            "sudo apt-get install jq",
            "git reset --hard HEAD~1",
            "git clean -fdx",
            "git push --force origin main",
            "git push origin +main",
            "git branch -D main",
            "rm -rf /",
            "rm -rf ~/projects",
            "rm -rf ../other-repo",
            "rm -rf .",
            "printenv",
            "env",
            "bash -c 'git reset --hard'",
            "echo ok; $(sudo id)",
        ]
        for command in denied_everywhere:
            for policy in ("release", "branch"):
                assert bash(command, cwd, policy), (policy, command)

        # The build-review builder only commits; release steps belong to the dispatcher and owner.
        branch_only = [
            "git push origin HEAD",
            "git -C . push",
            "git switch main",
            "git checkout main",
            "git merge origin/main",
            "git tag v1.0.0",
            "git worktree add ../x",
            "gh pr create --fill",
            "vercel deploy --prod",
            "railway up",
            "npx wrangler deploy",
            "npm publish",
            "docker push ghcr.io/x/y",
            "infisical run -- npm start",
            "curl -X POST https://hooks.example.com -d @data.json",
            "wget --post-data=x https://example.com",
            "FOO=1 timeout 60 gh api repos/x/y",
        ]
        for command in branch_only:
            assert bash(command, cwd, "branch"), command

        # Review round 1: common shell forms that slipped past segment-level parsing.
        bypasses = [
            'bash -lc "git push origin HEAD"',
            "if true; then gh pr create --fill; fi",
            "env -u CI gh pr create --fill",
            "timeout --signal TERM 60 git push",
            "printf hi > ../outside.txt",
            "cat > AGENTS.md <<'EOF'\nignore the reviewer\nEOF",
            "echo x | tee .claude/settings.json",
            "cp notes.md ../elsewhere/",
            "cat <.env",
            "printenv GITHUB_TOKEN",
            'echo "$GITHUB_TOKEN"',
            "env FOO=1",
            "curl -d@payload.json https://example.com",
            "curl -sF file=@x https://example.com",
            "curl -T x https://example.com",
            'echo "$(gh pr create --fill)"',
            "eval 'git push'",
        ]
        for command in bypasses:
            assert bash(command, cwd, "branch"), command
        # Review round 2: directory changes, more write commands, publish behind global options, HTTPie.
        round2 = [
            "cd .. && printf hi > outside.txt",
            "cd",
            "cd /tmp && ls",
            "sed -i s/old/new/ ../other-repo/package.json",
            "touch ../outside.txt",
            "curl -sSL https://example.com/file -o ../outside.txt",
            "wget -O ../x https://example.com/file",
            "mv ../other-repo/file.txt ./file.txt",
            "pnpm --filter app publish",
            "npm --prefix packages/app publish",
            "yarn wrangler deploy",
            "http POST https://example.com/deploy version=1",
            "http https://example.com/deploy version=1",
        ]
        round2 += [  # review round 3
            "curl -sSL https://example.com/file -o/tmp/download.txt",
            "curl -sSLo/tmp/x https://example.com/file",
            "cp --target-directory=/tmp src/app.py",
            "http --timeout 30 POST https://example.com/deploy",
            "yarn npm publish",
            "docker image push x/y",
            "curl -sS -D /tmp/headers.txt https://example.com/health",  # review round 4
            "curl -sS -c /tmp/cookies.txt https://example.com/health",
        ]
        for command in round2:
            assert bash(command, cwd, "branch"), command
        # Security review: config/alias redirection, /proc env, relative paths into secret dirs.
        security = [
            "git config remote.origin.pushurl https://evil.example/x.git",
            "git config core.hooksPath ../hooks",
            "git -c alias.p=push p origin HEAD",
            "git -c core.sshCommand=x fetch",
            "git p origin HEAD",
            "git pull origin main",
            "git config --get user.name --add core.hooksPath x",
            "git config user.name bot",
            "git rebase -x 'gh pr create' origin/main",
            "git rebase --exec=./deploy.sh origin/main",
            "git bisect run ./x.sh",
            "git submodule foreach 'git push'",
            "git fetch --upload-pack='sh -c x' origin",
            "git ls-remote -u x origin",
            "git log --output=../out.txt",
            "GIT_SSH_COMMAND='ssh -i k' git fetch",
            "GIT_CONFIG_GLOBAL=/tmp/c git status",
            "export GIT_DIR=/elsewhere && git status",
            "git rebase --exe=./x.sh origin/main",
            "git rebase -ix ./x.sh origin/main",
            "git fetch -qu x origin",
            "git grep -O foo",
            "git --git-dir=/elsewhere/.git log",
            "git init",
            "env -C ../other-repo git commit -am x",
            "env --chdir=/elsewhere make",
            "env --ch=/elsewhere make",
            "env -iC/elsewhere make",
            "env -S 'git push origin HEAD'",
            "env --split-string='gh pr create'",
            "nohup env -C /elsewhere make",
            'git commit -m "`git push`"',
            "echo `gh pr create`",
            "(cd src && npm test); rm -rf ../other-repo",
            "echo $(cd src && pwd); printf x > ../outside.txt",
            "echo $(echo $(gh pr create))",
            "x=$( (gh pr create) )",
            "cat <<EOF > notes.md\nsee `git log`\nEOF",
            "cat <<EOF > notes.txt\nit's $(gh pr create --fill)\nEOF",
            "echo $'it\\'s' $(git push)",
            "git -C ../other-repo commit -am x",
            "git -C../other-repo commit -am x",
            "git -C src -C ../.. commit -am x",
            "git -Csrc -C../../x status",
            "git --attr-source HEAD -C ../.. status",
            "git --unknown-flag push",
            "git --bare status",
            "git symbolic-ref HEAD refs/heads/main",
            "git symbolic-ref -d HEAD",
            "EDITOR='sh -c x' git commit",
            "printf x > .git/hooks/pre-push",
            "cat /proc/self/environ",
            "cat " + "../" * 30 + "home/someone/.config/gh/hosts.yml",
            "cat " + str(Path.home() / ".ssh" / "config"),
        ]
        for command in security:
            assert bash(command, cwd, "branch"), command
        assert tool("Read", cwd, file_path=str(Path.home() / ".config" / "gh" / "hosts.yml"))
        # Codex review 2026-11-03: CLI login caches, through every read path.
        home = Path.home()
        caches = [home / ".codex" / "auth.json", home / ".claude" / ".credentials.json",
                  home / ".config" / "gh" / "hosts.yml", home / ".railway" / "config.json", home / ".claude.json"]
        for cache in caches:
            short = "~/" + cache.relative_to(home).as_posix()
            for path in (str(cache), short):
                assert tool("Read", cwd, file_path=path), path
                assert tool("Grep", cwd, path=path, pattern="token"), path
                for command in (f"cat {path}", f"head -c 100 {path}", f"grep -i token {path}", f"cp {path} notes.txt"):
                    assert bash(command, cwd, "branch") and bash(command, cwd, "release"), command
            assert bash("cat $HOME/" + cache.relative_to(home).as_posix(), cwd), cache
            assert bash("cat " + "../" * 30 + cache.relative_to(Path("/")).as_posix(), cwd), cache
        assert tool("Grep", cwd, path="~", pattern="access_token")
        assert tool("Grep", cwd, path=str(home / ".claude"), pattern="access_token")
        assert tool("Glob", cwd, pattern="~/.claude/.cred*")
        assert tool("Glob", cwd, pattern=str(home) + "/.c*/auth.json")
        # A filtered search of a directory holding a login cache is judged file by file.
        fake_claude = cwd.parent / "fake-home" / ".claude"
        fake_claude.mkdir(parents=True)
        (fake_claude / ".credentials.json").write_text("{}")
        (fake_claude / "settings.json").write_text("{}")
        saved_files = guard.HOME_SECRET_FILES
        guard.HOME_SECRET_FILES = saved_files + (fake_claude / ".credentials.json",)
        try:
            assert tool("Glob", cwd, path=str(fake_claude), pattern="settings.json") is None
            assert tool("Grep", cwd, path=str(fake_claude), glob="settings.json", pattern="model") is None
            assert tool("Glob", cwd, path=str(fake_claude), pattern="*.json")
            assert tool("Grep", cwd, path=str(fake_claude), pattern="token")  # unfiltered
        finally:
            guard.HOME_SECRET_FILES = saved_files
        assert tool("Read", cwd, file_path=str(home / ".claude" / "settings.json")) is None
        # Codex review 2026-11-03: a new link is checked where it points, not only where it is made.
        links = [
            "ln -s ../other-repo out && printf x > out/file",
            "ln -s ../other-repo out; printf x > out/file",
            "ln -sf /etc out",
            "ln --symbolic .. up && printf x > up/other-repo/file",
            "ln -s ../../x src/link",
            "ln -s -t src ../../other-repo",
            "ln -s ../other-repo",
            "ln -s .git g && printf x > g/config",
            "ln -s $HOME/.ssh k",
            "ln ../other-repo/package.json p && printf x > p",
            "cp -s /etc/hosts h",
            "cp --link ../other-repo/a b",
            "ln -sr ../other-repo src/out && printf x > src/out/README.md",  # review 2: relative mode
            "ln --relative -s ../other-repo src/out",
            "ln --sy ../other-repo out",
            "cp --li ../other-repo/a b",
        ]
        for command in links:
            for policy in ("release", "branch"):
                assert bash(command, cwd, policy), (policy, command)
        (cwd / "src").mkdir(exist_ok=True)
        # Review 2: an existing directory destination is the link's directory.
        (cwd / "docs").mkdir()
        assert bash("ln -s ../README.md docs", cwd) is None
        assert bash("ln -sT ../README.md docs", cwd)  # -T: docs itself is the link
        assert bash("ln -sn ../README.md docs", cwd) is None  # -n: docs is a real directory
        (cwd / "docs-link").symlink_to("docs")
        assert bash("ln -sn ../README.md docs-link", cwd)  # -n: the symlink itself is replaced
        assert bash("ln -s ../README.md docs-link", cwd) is None
        # Symlinks keep their target text when copied or moved.
        (cwd / "nest" / "deep").mkdir(parents=True)
        (cwd / "nest" / "up").symlink_to("../docs")
        (cwd / "nest" / "deep" / "up2").symlink_to("../../docs")
        (cwd / "nest" / "ok").symlink_to("deep")
        for command in ("mv nest/up up", "cp -P nest/up up", "cp -a nest flat", "cp -r nest copy", "cp --archive nest x",
                        "mv nest/deep d", "cp -s ../other-repo/a b"):
            assert bash(command, cwd), command
        (cwd / "docs" / "existing-link").symlink_to("../README.md")
        for command in ("mv notes.txt docs", "mv -t docs notes.txt", "mv --target-directory=docs a b"):
            assert bash(command, cwd) is None, (command, bash(command, cwd))  # the destination's links stay put
        assert bash("mv -t docs nest/up", cwd)
        assert bash("mv docs ../elsewhere", cwd)
        (cwd / "docs" / "current").symlink_to("../README.md")
        for command in ("cp --preserve=all docs/current README.copy", "cp --preserve=links docs/current r2"):
            assert bash(command, cwd) is None, (command, bash(command, cwd))  # copies the referent's contents
        assert bash("cp -P --preserve=all docs/current README.copy", cwd)
        for command in ("cp -rL nest copy", "cp nest/up up", "mv nest/ok ok2", "cp -P nest/ok ok3"):
            assert bash(command, cwd) is None, (command, bash(command, cwd))
        for command in ("ln -s ../lib/util.py src/util.py", "ln -s src/app.py app.py", "ln -sf src current",
                        "ln -sr src/app.py src/link.py", "ln -s -t src ../README.md", "cp -r src build"):
            assert bash(command, cwd, "branch") is None, (command, bash(command, cwd, "branch"))
        for command in ("GIT_PAGER=cat git log -1", "EDITOR=true git commit --amend", "git grep -n foo", "git -C src log -1", "git -C src -C .. status", "git --no-pager log -1", "git -P diff", "git symbolic-ref -q --short HEAD", "env CI=1 make -C build", "env -u CI -i PATH=/usr/bin npm test",
                        "rg env --glob '*.py' infra", "git commit -m 'fix: document `git push` errors'",
                        "git commit -m 'note $(gh pr create) is the dispatcher step'", "git stash", "git log -1 --stat", "git rebase origin/main", "git fetch origin main"):
            assert bash(command, cwd, "branch") is None, command
        # Codex review 2026-11-03: worktrees share branch refs; fetch only writes refs/remotes/.
        for command in ("git fetch --update-head-ok origin +HEAD:refs/heads/main", "git fetch origin main:main",
                        "git fetch . HEAD:refs/heads/main", "git fetch origin +main:refs/heads/main",
                        "git fetch --depth 1 origin main:refs/tags/x", "git fetch --refmap=+refs/heads/*:refs/heads/* origin",
                        "git fetch --update-he origin main", "git fetch --stdin origin"):
            assert bash(command, cwd, "branch"), command
        for command in ("git fetch origin", "git fetch origin main", "git fetch --depth 1 origin main",
                        "git fetch origin +main:refs/remotes/origin/main", "git fetch --multiple origin upstream",
                        "git fetch --prune origin"):
            assert bash(command, cwd, "branch") is None, (command, bash(command, cwd, "branch"))
        assert bash("git push --force-with-lease=refs/heads/main:abc origin main", cwd, "release")
        assert bash("git push -uf origin main", cwd, "release")
        quiet = [
            'git commit -m "fix; gh integration"',
            "npm test -- --grep push",
            "curl --request=GET https://example.com",
            "curl -I -XHEAD https://example.com",
            "npm test > /dev/null 2>&1",
            "pytest -q 2>&1 | tee test-output.log",
            "cp src/a.py src/b.py",
            "cd src && printf hi > note.txt && cd ..",
            "sed -i 's/a/b/' src/app.py",
            "touch src/new.py && mkdir -p build",
            "curl -sSL https://example.com/file -o vendor/file",
            "npm run env",
            "http GET https://example.com/health page==2",
            "rm -f build/out.js",
            "sed -i -e '/DEBUG/d' src/app.py",
            "sed -i -e 's/$/;/' src/app.py",
            "pnpm test --grep publish",
            "npm test -- publish",
            "curl -sS -H 'Accept: x' -o out/file https://example.com",
            "http --timeout 30 GET https://example.com/health",
        ]
        for command in quiet:
            assert bash(command, cwd, "branch") is None, (command, bash(command, cwd, "branch"))
        for command in ("git push origin HEAD", "vercel deploy --prod", "gh pr create --fill"):
            assert bash(command, cwd, "release") is None, command

        # Path tools: secrets anywhere, edits only inside the working directory.
        assert tool("Read", cwd, file_path=str(cwd / ".env"))
        assert tool("Read", cwd, file_path="/home/someone/.ssh/id_ed25519")
        assert tool("Grep", cwd, path="/home/someone/.config")
        (cwd / "infra").mkdir()
        (cwd / "infra" / ".env").write_text("X=1")
        (cwd / "infra" / "app.py").write_text("x")
        (cwd / ".env.example").write_text("X=")
        assert tool("Grep", cwd, path=str(cwd), glob="**/.e[n]v", pattern=".")
        assert tool("Grep", cwd, path=str(cwd), glob="**/{.e,x}nv", pattern=".")
        assert tool("Grep", cwd, path=str(cwd), glob=".?nv", pattern=".")
        assert tool("Glob", cwd, path=str(cwd), pattern="infra/.*")
        assert tool("Grep", cwd, path=str(cwd), glob="infra/*.py", pattern=".") is None
        assert tool("Grep", cwd, path=str(cwd), glob="**/.[!x]nv", pattern=".")  # unmodelled syntax fails closed
        assert tool("Grep", cwd, path=str(cwd), glob="**/" + "{a,b}" * 8, pattern=".")  # brace bomb
        assert tool("Grep", cwd, path=str(cwd), glob="/infra/.e?v", pattern=".")  # root-anchored
        assert tool("Grep", cwd, path=str(cwd), glob=str(cwd) + "/infra/.e*", pattern=".")  # absolute under root
        assert tool("Grep", cwd, path=str(cwd), glob="/etc/*", pattern=".") is None  # rg: anchored at the root
        assert tool("Grep", cwd, path=str(cwd), glob="!*.py", pattern=".")  # negation fails closed
        assert tool("Grep", cwd, path=str(cwd), glob="infra/", pattern=".")  # directory form fails closed
        assert tool("Grep", cwd, path=str(cwd), glob="/infra/*.py", pattern=".") is None
        # Anchored at the session cwd rather than at `path`: still matched.
        assert tool("Grep", cwd, path=str(cwd / "infra"), glob="/infra/.e?v", pattern=".")
        assert tool("Grep", cwd, path=str(cwd / "infra"), glob=str(cwd) + "/infra/.e*", pattern=".")
        (cwd / "linked").symlink_to(cwd / "infra", target_is_directory=True)
        assert tool("Glob", cwd, path=str(cwd), pattern="linked/.*"), "a symlinked dir is walked by its own path"
        (cwd / "infra" / "loop").symlink_to(cwd, target_is_directory=True)
        assert tool("Grep", cwd, path=str(cwd), glob="**/*.py", pattern=".") is None, "a symlink cycle terminates"
        assert tool("Grep", cwd, path=str(cwd), glob="**/.env", pattern=".")
        assert tool("Grep", cwd, path=str(cwd), glob="*.{env,pem}", pattern=".")
        assert tool("Glob", cwd, pattern="**/id_rsa*") is None, "nothing secret matches"
        (cwd / "keys").mkdir()
        (cwd / "keys" / "id_rsa").write_text("k")
        assert tool("Glob", cwd, pattern="**/id_[r]sa")
        # Codex review 2026-11-03: an unfiltered Grep reads everything below its root.
        assert tool("Grep", cwd, path=str(cwd), pattern=".", output_mode="content")
        assert tool("Grep", cwd, pattern="BEGIN")
        assert tool("Grep", cwd, path=str(cwd / "keys"), pattern=".")
        assert tool("Grep", cwd, path=str(cwd / "infra"), glob="*.py", pattern=".") is None
        assert tool("Grep", cwd, path=str(cwd / "infra" / "app.py"), pattern=".") is None
        # Review: root-anchored Grep globs and `type` filters only select what ripgrep reads.
        assert tool("Grep", cwd, path=str(cwd), glob="/*.py", pattern=".") is None
        assert tool("Grep", cwd, path=str(cwd), glob="/**/*.py", pattern=".") is None
        assert tool("Grep", cwd, path=str(cwd), type="py", pattern=".") is None
        assert tool("Grep", cwd, path=str(cwd), type="nosuchtype", pattern=".")
        assert tool("Glob", cwd, pattern="/*.py")  # the Glob tool reads `/` as the filesystem root
        assert tool("Grep", cwd, path=str(cwd), glob="**/*.py", pattern="environ") is None
        assert tool("Grep", cwd, path=str(cwd), glob="**/.env.example", pattern=".") is None
        assert tool("Read", cwd, file_path=str(cwd / "src" / "app.py")) is None
        assert tool("Read", cwd, file_path="/usr/share/doc/README") is None
        assert tool("Edit", cwd, file_path=str(cwd / "src" / "app.py")) is None
        assert tool("Write", cwd, file_path="notes/plan.md") is None
        assert tool("Write", cwd, file_path=str(cwd.parent / "skill" / "SKILL.md"))
        assert tool("Edit", cwd, file_path="../escape.py")
        assert tool("Write", cwd, file_path=str(cwd / "AGENTS.override.md"))
        assert tool("Write", cwd, file_path=str(cwd / ".claude" / "settings.json"))
        assert tool("Write", cwd, "release", file_path=str(cwd / "AGENTS.md")) is None

        assert tool("PowerShell", cwd, command="Get-ChildItem")
        assert tool("PowerShell", cwd, "release", command="Get-ChildItem") is None

        # Hook protocol: deny JSON on stdout, silence on allow, fail closed on bad input.
        def run(payload: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, str(GUARD), "--policy", "branch"],
                input=payload, capture_output=True, text=True, check=False,
            )

        denied = run(json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": str(cwd)}))
        assert denied.returncode == 0, denied.stderr
        output = json.loads(denied.stdout)["hookSpecificOutput"]
        assert output["hookEventName"] == "PreToolUse" and output["permissionDecision"] == "deny"
        allowed_run = run(json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": str(cwd)}))
        assert allowed_run.returncode == 0 and allowed_run.stdout == ""
        broken = run("not json")
        assert broken.returncode == 2 and "could not check" in broken.stderr

    print("Book Dev dispatch guard tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
