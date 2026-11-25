#!/usr/bin/env python3
"""ADR-0007 design-floor drift: keep reference files stamped and find stale or edited copies.

Every shared CSS file starts with a header line:

    /* design-floor <name> <semver> sha256:<hash of everything after line 1> */

Modes:
  --stamp            recompute the hash of each reference file, rewrite its header
                     and record it in design-floor/releases.json (maintainer, on release)
  --check            verify reference headers match their content and releases.json
                     (runs in `task check`)
  --scan ROOT...     report vendored copies under ROOT: current, behind, edited, unknown
  --adoption ROOT... one row per git repository under ROOT that adopted the floor: its
                     design-floor.json, the status of its vendored files and the release of
                     the reusable CI job it calls (report only, always exits 0)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FLOOR = REPO / "design-floor"
RELEASES = FLOOR / "releases.json"
REFERENCE_FILES = ["tokens.css", "tailwind/v4.css", "aliases/shadcn.css", "presets/book-brand.css"]
HEADER = re.compile(r"^/\* design-floor (?P<name>[a-z0-9-]+) (?P<version>\d+\.\d+\.\d+) sha256:(?P<hash>\S+) \*/$")
FLOOR_JOB = re.compile(r"platform-ci-workflows/\.github/workflows/design-floor\.yml@\S+(?:\s*#\s*(v\d+\.\d+\.\d+))?")
SKIP_DIRS = {"node_modules", ".git", ".next", "out", "dist", ".worktrees", ".venv"}


def split(text: str) -> tuple[str, str]:
    first, _, rest = text.partition("\n")
    return first, rest


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def parse(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    first, body = split(text)
    m = HEADER.match(first.strip())
    if not m:
        return None
    return {**m.groupdict(), "actual": body_hash(body), "body": body}


def semver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def load_releases() -> dict:
    return json.loads(RELEASES.read_text(encoding="utf-8")) if RELEASES.exists() else {}


def stamp() -> int:
    releases = load_releases()
    for rel in REFERENCE_FILES:
        path = FLOOR / rel
        info = parse(path)
        if info is None:
            print(f"FAIL: {rel} has no design-floor header", file=sys.stderr)
            return 1
        known = releases.get(info["name"], {}).get(info["version"])
        if known and known != info["actual"]:
            print(f"FAIL: {rel} content changed but version {info['version']} is already released; bump the version", file=sys.stderr)
            return 1
        path.write_text(f"/* design-floor {info['name']} {info['version']} sha256:{info['actual']} */\n{info['body']}", encoding="utf-8")
        releases.setdefault(info["name"], {})[info["version"]] = info["actual"]
        print(f"  OK: stamped {rel} {info['name']} {info['version']}")
    RELEASES.write_text(json.dumps(releases, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def check() -> int:
    releases = load_releases()
    bad = 0
    for rel in REFERENCE_FILES:
        info = parse(FLOOR / rel)
        if info is None:
            print(f"  FAIL: {rel} has no design-floor header")
            bad += 1
            continue
        if info["hash"] != info["actual"]:
            print(f"  FAIL: {rel} header hash does not match content; run scripts/design_floor_drift.py --stamp (and bump the version if released)")
            bad += 1
        elif releases.get(info["name"], {}).get(info["version"]) != info["actual"]:
            print(f"  FAIL: {rel} {info['name']} {info['version']} is not recorded in releases.json")
            bad += 1
        else:
            print(f"  OK: {rel} {info['name']} {info['version']}")
    print("Design floor references: " + ("OK" if not bad else f"{bad} problem(s)"))
    return 1 if bad else 0


def classify(info: dict, releases: dict) -> str:
    versions = releases.get(info["name"], {})
    if info["hash"] != info["actual"]:
        return "edited"
    if versions.get(info["version"]) != info["actual"]:
        return "unknown"
    latest = max(versions, key=semver)
    return "current" if info["version"] == latest else f"behind (latest {latest})"


def scan(roots: list[str], as_json: bool) -> int:
    releases = load_releases()
    rows = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            if Path(dirpath).resolve().is_relative_to(FLOOR):
                continue
            for f in filenames:
                if not f.endswith(".css"):
                    continue
                path = Path(dirpath) / f
                info = parse(path)
                if info:
                    rows.append({"path": str(path), "name": info["name"], "version": info["version"], "status": classify(info, releases)})
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"{r['status']:<24} {r['name']:<18} {r['version']:<8} {r['path']}")
        print(f"{len(rows)} vendored file(s); {sum(r['status'] != 'current' for r in rows)} not current")
    return 1 if any(r["status"] in ("edited", "unknown") for r in rows) else 0


def adoption_rows(roots: list[str]) -> list[dict]:
    """Group floor configs and vendored files by the git repository that holds them."""
    releases = load_releases()
    repos: dict[Path, dict] = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            if here.resolve().is_relative_to(FLOOR):
                dirnames[:] = []
                continue
            if ".git" in dirnames or ".git" in filenames:
                # A clone of the floor's own source (this repository) is not an adopter.
                if (here / "design-floor" / "releases.json").exists():
                    dirnames[:] = []
                    continue
                repos.setdefault(here, {"config": [], "files": [], "ci": ""})
            # A checker's test fixtures are not an adoption.
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != "fixtures"]
            owner = next((r for r in sorted(repos, key=lambda p: len(p.parts), reverse=True) if here == r or r in here.parents), None)
            if owner is None:
                continue
            row = repos[owner]
            if "design-floor.json" in filenames:
                row["config"].append(str((here / "design-floor.json").relative_to(owner)))
            for f in filenames:
                if f.endswith(".css") and (info := parse(here / f)):
                    row["files"].append(f"{info['name']} {info['version']} {classify(info, releases)}")
                if here == owner / ".github" / "workflows" and f.endswith((".yml", ".yaml")):
                    # A commented-out call (`# uses: …`) is not an adoption.
                    text = (here / f).read_text(encoding="utf-8", errors="replace")
                    live = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
                    m = FLOOR_JOB.search(live)
                    if m:
                        row["ci"] = m.group(1) or "unpinned tag"
    return [
        {"repo": str(path), **row}
        for path, row in sorted(repos.items())
        if row["config"] or row["files"]
    ]


def adoption(roots: list[str], as_json: bool) -> int:
    rows = adoption_rows(roots)
    if as_json:
        print(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        print(f"{r['repo']}\n  config: {', '.join(r['config']) or 'none'}\n  files:  {'; '.join(r['files']) or 'none'}\n  ci:     {r['ci'] or 'none'}")
    print(f"{len(rows)} repo(s) adopted; {sum(bool(r['ci']) for r in rows)} run the CI job; {sum(not r['config'] for r in rows)} vendor files without a design-floor.json")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--stamp", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--scan", nargs="+", metavar="ROOT")
    g.add_argument("--adoption", nargs="+", metavar="ROOT")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.stamp:
        return stamp()
    if a.check:
        return check()
    if a.adoption:
        return adoption(a.adoption, a.json)
    return scan(a.scan, a.json)


if __name__ == "__main__":
    sys.exit(main())
