#!/usr/bin/env python3
"""Switch the Railway CI runner (runner-multi) on and off behind the local primary.

Private-repo CI runs on this owner's machine (infra/ci-runner/local). Railway's runner-multi keeps
the same registrations as the fallback. This watchdog, run on a schedule by
.github/workflows/ci-runner-failover.yml, looks at the local runner of the first repository in
infra/ci-runner/local/runners.txt (one container serves them all):

- local offline and Railway stopped: check again after a grace period, then restart Railway.
- local online and Railway running: stop Railway once none of its runners is busy.

Environment: GH_TOKEN (lists runners of the repositories and organizations in runners.txt),
RAILWAY_TOKEN (project token of ci-runners), optional TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.
Prints one line per decision; never prints tokens.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

RAILWAY_API = "https://backboard.railway.com/graphql/v2"
PROJECT_ID = "c3ea0930-c0bb-4d7e-a747-2d3765efee3a"  # ci-runners
ENVIRONMENT_ID = "e16bb581-235e-4e52-ad8c-e04f0f8b9549"  # production
SERVICE_ID = "a27de6f9-1232-4d33-9943-50f42c457ad7"  # runner-multi
RUNNERS_FILE = Path(__file__).resolve().parents[2] / "ci-runner" / "local" / "runners.txt"


def read_runners(path: Path = RUNNERS_FILE) -> list[tuple[str, str]]:
    """(name, api path) per entry, e.g. ("sales-crm", "repos/bookchaowalit/sales-crm")."""
    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, url = line.split("=", 1)
        target = url.removeprefix("https://github.com/")
        entries.append((name, f"repos/{target}" if "/" in target else f"orgs/{target}"))
    return entries


def decide(local_online: bool, railway_running: bool, railway_busy: bool) -> str:
    """start, stop, wait (stop deferred until Railway's jobs finish) or none."""
    if not local_online and not railway_running:
        return "start"
    if local_online and railway_running:
        return "wait" if railway_busy else "stop"
    return "none"


def _request(url: str, headers: dict[str, str], body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    # Railway sits behind a WAF that refuses urllib's default User-Agent.
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "ci-runner-failover", **headers})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def github_runners(api_path: str) -> list[dict]:
    headers = {
        "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    return _request(f"https://api.github.com/{api_path}/actions/runners?per_page=100", headers)["runners"]


def railway(query: str, variables: dict | None = None) -> dict:
    # RAILWAY_TOKEN is the ci-runners project token (the workflow); RAILWAY_API_TOKEN, an account
    # token, is for a local --dry-run.
    if os.environ.get("RAILWAY_TOKEN"):
        headers = {"Project-Access-Token": os.environ["RAILWAY_TOKEN"]}
    else:
        headers = {"Authorization": f"Bearer {os.environ['RAILWAY_API_TOKEN']}"}
    headers["Content-Type"] = "application/json"
    out = _request(RAILWAY_API, headers, {"query": query, "variables": variables or {}})
    if out.get("errors"):
        raise RuntimeError(f"railway: {out['errors'][0].get('message')}")
    return out["data"]


def railway_deployment() -> dict | None:
    """Newest SUCCESS deployment of runner-multi: {id, deploymentStopped}."""
    data = railway(
        "query($i:DeploymentListInput!){deployments(first:10,input:$i)"
        "{edges{node{id status deploymentStopped}}}}",
        {"i": {"projectId": PROJECT_ID, "environmentId": ENVIRONMENT_ID, "serviceId": SERVICE_ID}},
    )
    for edge in data["deployments"]["edges"]:
        if edge["node"]["status"] == "SUCCESS":
            return edge["node"]
    return None


# Every local instance counts: local-<name> (instance 1) and local2-<name>, local3-... (run.sh
# CI_RUNNER_INSTANCE). Railway is only needed when all of them are offline.
LOCAL_RUNNER = re.compile(r"^local\d*-")


def any_local_online(runners: list[dict]) -> bool:
    return any(LOCAL_RUNNER.match(r["name"]) and r["status"] == "online" for r in runners)


def local_online(canary: str) -> bool:
    return any_local_online(github_runners(canary))


def railway_busy(entries: list[tuple[str, str]]) -> bool:
    return any(
        r["name"].startswith("railway-") and r["busy"] for _, path in entries for r in github_runners(path)
    )


def notify(text: str) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    try:
        _request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            {"Content-Type": "application/json"},
            {"chat_id": chat, "text": text},
        )
    except OSError as exc:  # an alert failure must not undo the switch
        print(f"telegram: {exc.__class__.__name__}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Switch Railway runner-multi behind the local CI runners.")
    parser.add_argument("--grace", type=int, default=180, help="seconds to re-check before starting Railway")
    parser.add_argument("--dry-run", action="store_true", help="decide and print, change nothing")
    args = parser.parse_args(argv)

    entries = read_runners()
    canary = entries[0][1]
    deployment = railway_deployment()
    if deployment is None:
        print("::error::runner-multi has no SUCCESS deployment; redeploy it by hand")
        return 1
    running = not deployment["deploymentStopped"]
    up = local_online(canary)
    action = decide(up, running, railway_busy(entries) if up and running else False)

    if action == "start" and args.grace:
        # A restart of the local container or a short network drop takes the runners offline for
        # a minute or two; do not wake Railway for that.
        time.sleep(args.grace)
        up = local_online(canary)
        action = decide(up, running, False)

    print(f"local={'online' if up else 'offline'} railway={'running' if running else 'stopped'} action={action}")
    if args.dry_run or action in ("none", "wait"):
        return 0
    if action == "start":
        railway("mutation($id:String!){deploymentRestart(id:$id)}", {"id": deployment["id"]})
        notify("CI runners: local runners offline, Railway runner-multi started as fallback.")
    else:
        railway("mutation($id:String!){deploymentStop(id:$id)}", {"id": deployment["id"]})
        notify("CI runners: local runners back online, Railway runner-multi stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
