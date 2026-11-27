#!/usr/bin/env python3
"""Measure the share of work across the seven universal functions, counted in tagged diary turns (not hours).

Source: diary conversation summaries tagged with ``functions`` (one unit per
turn, split evenly across its functions).

The result is compared with a function rotation stage target from
docs/registries/ROLE-ECONOMIC-ALIGNMENT.yaml. Only summaries and tags are read;
no raw conversation text is loaded.

Usage:
    python3 function_share.py --since 2026-10-01 --until 2026-10-07
    python3 function_share.py --stage assure-to-design --json
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import ARCHITECTURE_ROOT, KNOWLEDGE_ROOT  # noqa: E402
from knowledge_store import open_store  # noqa: E402

# The role registries live in the hosted store, space architecture-docs (SPEC-001 D3, 2026-11-26).
# A local copy wins when it has the file, found as `_env.architecture_root()` finds it:
# SOLO_ARCHITECTURE_ROOT, else the hub's snapshot of the store space, else a checkout. Only then is
# the store called. Diary entries live in the knowledge-base checkout (SOLO_KNOWLEDGE_ROOT).
ARCHITECTURE_SPACE = "architecture-docs"
REGISTRIES = "docs/registries"
CLASSIFICATIONS_NAME = "ROLE-CLASSIFICATIONS.yaml"
ALIGNMENT_NAME = "ROLE-ECONOMIC-ALIGNMENT.yaml"

ROLE_CLASSIFICATIONS = ARCHITECTURE_ROOT / REGISTRIES / CLASSIFICATIONS_NAME
ROLE_ECONOMIC_ALIGNMENT = ARCHITECTURE_ROOT / REGISTRIES / ALIGNMENT_NAME
DEFAULT_ENTRIES_ROOT = KNOWLEDGE_ROOT / "diary" / "entries"
CONVERSATION_FILENAME = "conversation-summaries.json"
DRIFT_THRESHOLD = 15
TAG_PREFIX = "function:"


@functools.lru_cache(maxsize=None)
def _store_registry(name: str) -> str:
    """One registry document from the architecture-docs store space, read once per process."""
    store = open_store(space=ARCHITECTURE_SPACE, diary=False)
    if store is None:
        raise FileNotFoundError(
            f"{REGISTRIES}/{name}: no local architecture-docs copy and the knowledge store is not "
            "configured (EMPIRE_KNOWLEDGE_API + SOLO_WEB_PUBLIC_ORIGIN, env or client.env)"
        )
    found = store.get(f"{REGISTRIES}/{name}")
    if found is None:
        raise FileNotFoundError(f"{REGISTRIES}/{name} is not in store space {ARCHITECTURE_SPACE}")
    return found[0].decode("utf-8")


def _registry(name: str, path: Path | None) -> Any:
    """An explicit path is read as given; otherwise a local copy, else the store."""
    local = path if path is not None else ARCHITECTURE_ROOT / REGISTRIES / name
    if path is not None or local.is_file():
        return yaml.safe_load(local.read_text(encoding="utf-8"))
    return yaml.safe_load(_store_registry(name))


def load_function_ids(path: Path | None = None) -> list[str]:
    """Return function IDs in registry order."""
    data = _registry(CLASSIFICATIONS_NAME, path)
    return [row["id"] for row in data["functions"]]


def parse_functions(value: str | list[str] | None, valid: list[str] | None = None) -> list[str]:
    """Normalize a comma-separated or list value into known, de-duplicated function IDs."""
    if not value:
        return []
    items = value.split(",") if isinstance(value, str) else value
    valid = valid if valid is not None else load_function_ids()
    result: list[str] = []
    for item in items:
        function_id = str(item).strip().removeprefix(TAG_PREFIX)
        if not function_id:
            continue
        if function_id not in valid:
            raise ValueError(f"unknown function: {function_id}; expected one of {', '.join(valid)}")
        if function_id not in result:
            result.append(function_id)
    return result


def load_stage(stage_id: str | None, path: Path | None = None) -> dict[str, Any]:
    """Return the requested rotation stage, defaulting to the active stage."""
    rotation = _registry(ALIGNMENT_NAME, path)["function_rotation"]
    stages = rotation["stages"]
    stage_id = stage_id or rotation.get("active_stage") or stages[0]["id"]
    for stage in stages:
        if stage["id"] == stage_id:
            return stage
    raise ValueError(f"unknown rotation stage: {stage_id}")


def _days(since: str, until: str) -> list[str]:
    start, end = date.fromisoformat(since), date.fromisoformat(until)
    return [(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)]


def collect_diary(entries_root: Path, since: str, until: str, valid: list[str]) -> dict[str, Any]:
    """Count diary turns per function; each turn contributes one unit split across its functions."""
    units = {function_id: 0.0 for function_id in valid}
    tagged = untagged = 0
    for day in _days(since, until):
        year, month, dom = day.split("-")
        path = Path(entries_root) / year / month / dom / CONVERSATION_FILENAME
        if not path.is_file():
            continue
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        for row in rows if isinstance(rows, list) else []:
            functions = [f for f in (row.get("functions") or []) if f in units] if isinstance(row, dict) else []
            if not functions:
                untagged += 1
                continue
            tagged += 1
            for function_id in functions:
                units[function_id] += 1 / len(functions)
    return {"units": units, "tagged": tagged, "untagged": untagged}


def _shares(values: dict[str, float]) -> dict[str, float]:
    total = sum(values.values())
    return {key: (round(value * 100 / total, 1) if total else 0.0) for key, value in values.items()}


def build_report(
    *,
    since: str,
    until: str,
    stage_id: str | None = None,
    entries_root: Path = DEFAULT_ENTRIES_ROOT,
) -> dict[str, Any]:
    valid = load_function_ids()
    stage = load_stage(stage_id)
    diary = collect_diary(entries_root, since, until, valid)
    basis = "diary_turns"
    actual = _shares(diary["units"])
    target = {function_id: stage["target_share"].get(function_id, 0) for function_id in valid}
    has_data = any(actual.values())
    drift = {
        function_id: round(actual[function_id] - target[function_id], 1) if has_data else None
        for function_id in valid
    }
    return {
        "contract": "solo-empire.function-share.v1",
        "window": {"since": since, "until": until},
        "stage": {"id": stage["id"], "focus": stage["focus"], "economic_system": stage["economic_system"]},
        "basis": basis,
        "actual_share": actual,
        "target_share": target,
        "drift": drift,
        "over_threshold": [f for f, d in drift.items() if d is not None and abs(d) > DRIFT_THRESHOLD],
        "diary": {"tagged_turns": diary["tagged"], "untagged_turns": diary["untagged"]},
    }


def render_report(report: dict[str, Any]) -> str:
    window, stage = report["window"], report["stage"]
    lines = [
        f"Function share {window['since']} → {window['until']}",
        f"Stage: {stage['id']} (focus {stage['focus']}, {stage['economic_system']})",
        f"Basis: {report['basis']} · diary tagged {report['diary']['tagged_turns']}"
        f" / untagged {report['diary']['untagged_turns']}",
        "",
        f"{'function':<22} {'actual':>7} {'target':>7} {'drift':>7}",
    ]
    for function_id, actual in report["actual_share"].items():
        drift = report["drift"][function_id]
        flag = " ⚠" if function_id in report["over_threshold"] else ""
        drift_text = "—" if drift is None else f"{drift:+.1f}"
        lines.append(
            f"{function_id:<22} {actual:>6.1f}% {report['target_share'][function_id]:>6}% {drift_text:>7}{flag}"
        )
    if report["drift"] and all(value is None for value in report["drift"].values()):
        lines.append("\nยังไม่มีข้อมูลที่ติด function ในช่วงนี้ — ใช้ --functions ตอนบันทึก diary")
    elif report["over_threshold"]:
        lines.append(f"\nเกิน ±{DRIFT_THRESHOLD} จุด: บันทึกเหตุผลใน quarterly review")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Share of tagged diary turns across the seven universal functions")
    today = date.today()
    parser.add_argument("--since", default=(today - timedelta(days=6)).isoformat())
    parser.add_argument("--until", default=today.isoformat())
    parser.add_argument("--stage", help="Rotation stage ID; defaults to function_rotation.active_stage")
    parser.add_argument("--entries-root", type=Path, default=DEFAULT_ENTRIES_ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = build_report(
        since=args.since, until=args.until, stage_id=args.stage,
        entries_root=args.entries_root,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
