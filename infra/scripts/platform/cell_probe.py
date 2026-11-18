#!/usr/bin/env python3
"""After a deploy, prove each online cell's full request path works: JWT, platform authz, database.

``systems_status.py`` only checks the public ``/health`` URL. This probe sends
one authenticated ``GET /v1/<plural>?limit=1`` per cell as the read-only
``deploy-verifier`` automation (empire-platform automations registry):

- ``read`` cells must answer 200, which proves token check, authz, and a database read;
- ``deny`` cells (diary, accounts, health: sensitive personal data, so the
  verifier has no scope there) must answer 403, which proves token check and
  authz without reading anything.

Only the status and the number of rows are reported; record contents are never
printed. The token comes from ``EMPIRE_AUTOMATION_TOKEN_DEPLOY_VERIFIER`` or
``~/.config/empire-platform/tokens/deploy-verifier.jwt`` (owner-only file).

    python3 infra/scripts/platform/cell_probe.py [--json] [--cells goals projects] [--direct]

Requests go through the edge gateway (``https://gateway.bookchaowalit.com/v1/<cell>``,
``EMPIRE_GATEWAY_URL`` to override), because cells in SPEC-002 ``enforce`` refuse direct calls
with 403 ``gateway_required``. That 403 never counts as a ``deny`` pass: it is reported as
``gateway_required``. ``--direct`` probes the registry URLs instead.

Exit code: 0 when every probed cell answers as expected, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
# The systems registry belongs to the registry repository: point SOLO_REGISTRY_DIR at its
# `repository-catalog` folder (there is no default).
_REGISTRY_DIR = os.environ.get("SOLO_REGISTRY_DIR")
REGISTRY = Path(_REGISTRY_DIR or "/nonexistent-solo-registry") / "registries" / "online-systems.yaml"


def load_systems() -> list[dict]:
    if not REGISTRY.is_file():
        raise SystemExit("online-systems.yaml not found: set SOLO_REGISTRY_DIR to the repository-catalog folder")
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["systems"]
# Same lookup order as task_store.automation_token, kept here so this script has no repo imports.
TOKEN_DIRS = [ROOT / ".runtime" / "automation-tokens", Path.home() / ".config" / "empire-platform" / "tokens"]

AUTOMATION = "deploy-verifier"
# cell -> (collection path, expected outcome); keep in step with the deploy-verifier scopes
CELLS: dict[str, tuple[str, str]] = {
    "goals": ("goals", "read"),
    "projects": ("projects", "read"),
    "intake": ("sources", "read"),
    "operating": ("cycles", "read"),
    "roles-skills": ("personal-roles", "read"),
    "automation": ("cron-jobs", "read"),
    "content": ("contents", "read"),
    "opportunities": ("opportunities", "read"),
    "learning": ("challenges", "read"),
    "time": ("time-entries", "read"),
    "decisions": ("decisions", "read"),
    "habits": ("habits", "read"),
    "notifications": ("notifications", "read"),
    "diary": ("journal-entries", "deny"),
    "accounts": ("identities", "deny"),
    "health": ("health-metrics", "deny"),
}
EXPECTED_STATUS = {"read": 200, "deny": 403}
TIMEOUT_S = 60  # serverless cells may be waking up
GATEWAY_URL = os.environ.get("EMPIRE_GATEWAY_URL", "https://gateway.bookchaowalit.com").rstrip("/")


def automation_token(name: str) -> str:
    key = "EMPIRE_AUTOMATION_TOKEN_" + name.upper().replace("-", "_")
    if os.environ.get(key):
        return os.environ[key]
    for directory in TOKEN_DIRS:
        path = directory / f"{name}.jwt"
        if path.is_file():
            if path.stat().st_mode & 0o077:
                raise RuntimeError(f"token file {path} must be readable by its owner only (chmod 600)")
            return path.read_text(encoding="utf-8").strip()
    raise RuntimeError(f"no token for automation {name!r}: set {key} or create {TOKEN_DIRS[-1] / (name + '.jwt')}")


def cell_urls() -> dict[str, str]:
    systems = load_systems()
    return {s["id"]: s["url"] for s in systems if s.get("status") == "online" and s.get("url")}


def gateway_urls(cells: list[str]) -> dict[str, str]:
    return {cell: f"{GATEWAY_URL}/v1/{cell}" for cell in cells}


def probe(cell: str, base_url: str, token: str) -> dict:
    plural, expect = CELLS[cell]
    request = urllib.request.Request(f"{base_url}/v1/{plural}?limit=1",
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    started = time.perf_counter()
    rows: int | None = None
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            status = response.status
            body = json.loads(response.read())
            items = body.get("items") if isinstance(body, dict) else body
            rows = len(items) if isinstance(items, list) else None
    except urllib.error.HTTPError as err:
        status = err.code
        if status == 403 and _is_gateway_required(err):
            return {"cell": cell, "expect": expect, "status": status, "ok": False, "error": "gateway_required",
                    "seconds": round(time.perf_counter() - started, 2)}
    except (urllib.error.URLError, TimeoutError) as err:
        status, rows = 0, None
        return {"cell": cell, "expect": expect, "status": status, "ok": False,
                "error": type(err).__name__, "seconds": round(time.perf_counter() - started, 2)}
    ok = status == EXPECTED_STATUS[expect]
    return {"cell": cell, "expect": expect, "status": status, "ok": ok, "rows": rows,
            "seconds": round(time.perf_counter() - started, 2)}


def _is_gateway_required(err: urllib.error.HTTPError) -> bool:
    try:
        body = json.loads(err.read(4096) or b"{}")
    except (ValueError, OSError):
        return False
    return isinstance(body, dict) and body.get("error") == "gateway_required"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--cells", nargs="*", choices=sorted(CELLS), help="probe only these cells")
    parser.add_argument("--direct", action="store_true", help="probe the registry URLs, not the gateway")
    args = parser.parse_args(argv)

    wanted = args.cells or sorted(CELLS)
    urls = cell_urls() if args.direct else gateway_urls(wanted)
    missing = [c for c in wanted if c not in urls]
    try:
        token = automation_token(AUTOMATION)
    except RuntimeError as err:
        print(f"cell_probe: {err}", file=sys.stderr)
        return 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda c: probe(c, urls[c], token), [c for c in wanted if c in urls]))
    results += [{"cell": c, "expect": CELLS[c][1], "status": None, "ok": False, "error": "not online in registry"}
                for c in missing]

    if args.json:
        print(json.dumps({"automation": AUTOMATION, "results": results}, indent=2))
    else:
        for r in sorted(results, key=lambda r: r["cell"]):
            detail = r.get("error") or (f"rows={r['rows']}" if r.get("rows") is not None else "")
            print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['cell']:14} expect {r['expect']:4} got {r['status']} "
                  f"{detail} {r.get('seconds', '')}s")
        bad = [r["cell"] for r in results if not r["ok"]]
        print(f"{len(results) - len(bad)}/{len(results)} cells answered as expected" + (f"; failing: {', '.join(bad)}" if bad else ""))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
