#!/usr/bin/env python3
"""Install the local user timer that runs the Book Dev Linear dispatch cycle.

Every two hours between 08:00 and 22:00 Asia/Bangkok, the service runs
``book_dev_linear_cycle.py`` once. Each run dispatches at most one opted-in
Linear issue; a run that is still going blocks the next start (oneshot).

Settings live in ``~/.config/book-dev-dispatch.env`` (optional):
    BOOK_DEV_LINEAR_LABEL=ai-dispatch
    BOOK_DEV_DISPATCH_PROVIDERS=claude
    BOOK_DEV_DISPATCH_MODE=implement
    BOOK_DEV_CLAUDE_CLI=/path/to/claude

Usage:
    python3 install_book_dev_dispatch_timer.py            # render and validate only
    python3 install_book_dev_dispatch_timer.py --activate # install and enable
    systemctl --user disable --now book-dev-dispatch.timer
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from book_dev_ai_dispatch import worst_case_seconds  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
UNIT = "book-dev-dispatch"
# The dispatcher's own worst case at the cycle's 1800 s per-CLI timeout: planner and its retry,
# parallel subtasks, then the combined review flow with fix rounds and escalation.
DISPATCH_TIMEOUT_SEC = 1800
SERVICE_TIMEOUT_SEC = worst_case_seconds(DISPATCH_TIMEOUT_SEC) + 600
SCHEDULE = "*-*-* 08,10,12,14,16,18,20,22:00:00 Asia/Bangkok"


def unit_quote(value: str) -> str:
    if any(c in value for c in "\n\r\x00"):
        raise ValueError("invalid_unit_path")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def render_units(root: Path, python: str, home: Path) -> dict[str, str]:
    path = ":".join([str(home / ".local/bin"), "/usr/local/bin", "/usr/bin", "/bin"])
    exec_args = [python, str(root / "infra/scripts/workflows/book_dev_linear_cycle.py"), "--json"]
    service = f"""[Unit]
Description=Dispatch one opted-in Linear issue to the Book Dev AI loop
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
UMask=0077
TimeoutStartSec={SERVICE_TIMEOUT_SEC}
WorkingDirectory={str(root).replace("%", "%%")}
Environment={unit_quote("PATH=" + path)}
Environment=GIT_TERMINAL_PROMPT=0
Environment=GH_PROMPT_DISABLED=1
Environment=TZ=Asia/Bangkok
EnvironmentFile=-%h/.config/book-dev-dispatch.env
ExecStart={" ".join(unit_quote(value) for value in exec_args)}
"""
    timer = f"""[Unit]
Description=Run the Book Dev Linear dispatch cycle every two hours (08:00-22:00)

[Timer]
OnCalendar={SCHEDULE}
RandomizedDelaySec=120
Persistent=false

[Install]
WantedBy=timers.target
"""
    return {f"{UNIT}.service": service, f"{UNIT}.timer": timer}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--activate", action="store_true", help="Install and enable the timer")
    parser.add_argument("--replace", action="store_true", help="Overwrite units that differ from this template")
    args = parser.parse_args()
    # systemctl --user needs the runtime dir; shells opened over SSH may lack it.
    runtime = Path(f"/run/user/{os.getuid()}")
    if not os.environ.get("XDG_RUNTIME_DIR") and runtime.is_dir():
        os.environ["XDG_RUNTIME_DIR"] = str(runtime)
    try:
        units = render_units(ROOT, sys.executable, Path.home())
        with tempfile.TemporaryDirectory(prefix="book-dev-dispatch-units-") as temporary:
            paths = []
            for name, content in units.items():
                path = Path(temporary) / name
                path.write_text(content)
                paths.append(str(path))
            validated = subprocess.run(["systemd-analyze", "--user", "verify", *paths],
                                       capture_output=True, text=True, timeout=15, check=False)
            if validated.returncode:
                print(json.dumps({"activated": False, "status": "blocked", "code": "unit_validation_failed",
                                  "detail": validated.stderr.strip()[:400]}))
                return 2
        if not args.activate:
            print(json.dumps({"activated": False, "status": "ready", "units_valid": True, "schedule": SCHEDULE}))
            return 0
        target = Path.home() / ".config/systemd/user"
        target.mkdir(parents=True, exist_ok=True)
        for name, content in units.items():
            path = target / name
            if path.is_symlink() or (path.exists() and path.read_text() != content and not args.replace):
                raise ValueError("existing_unit_conflict")
        for name, content in units.items():
            with tempfile.NamedTemporaryFile(mode="w", dir=target, delete=False) as handle:
                handle.write(content)
                pending = Path(handle.name)
            os.chmod(pending, 0o600)
            os.replace(pending, target / name)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True, timeout=15)
        subprocess.run(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"],
                       check=True, capture_output=True, timeout=15)
        print(json.dumps({"activated": True, "runtime": "local-user-systemd", "schedule": SCHEDULE}))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"activated": False, "status": "blocked", "code": str(exc) or type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
