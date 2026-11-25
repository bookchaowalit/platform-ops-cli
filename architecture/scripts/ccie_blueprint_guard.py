#!/usr/bin/env python3
"""Validate the CCIE Enterprise Infrastructure blueprint coverage registry.

Every blueprint part must exist with Cisco's weight, every topic must be in
its part document's topic map, and every lab a topic names must be in that
document's lab table. Prints the study progress per part.
"""

from __future__ import annotations

import re
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FAIL: PyYAML is required (import yaml)")
    raise SystemExit(1)

from _env import PROJECT_ROOT  # noqa: E402


SECTION = PROJECT_ROOT / "systems" / "architecture" / "ccie-enterprise-infrastructure"
REGISTRY = SECTION / "blueprint-v1.1.yaml"
# Weights and level-two topic counts from Cisco's v1.1 blueprint PDF. A topic
# dropped from the registry, or a part re-weighted, fails here.
EXPECTED_PARTS = {
    "1.0": (30, 36),
    "2.0": (25, 12),
    "3.0": (15, 4),
    "4.0": (15, 25),
    "5.0": (15, 9),
}
DONE_STATUSES = ("labbed", "verified")


def rel(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def table_ids(text: str) -> set[str]:
    """First-column values of every Markdown table row."""
    return {m.group(1) for m in re.finditer(r"^\|\s*([^|\s][^|]*?)\s*\|", text, re.M)}


def main() -> int:
    print("=" * 70)
    print("Solo Empire — CCIE EI Blueprint Coverage Guard")
    print("=" * 70)
    errors: list[str] = []

    if not REGISTRY.is_file():
        print(f"  FAIL: missing registry: {rel(REGISTRY)}")
        return 1
    try:
        data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(f"  FAIL: cannot read registry: {exc}")
        return 1
    if not isinstance(data, dict) or data.get("kind") != "ccie-blueprint-coverage":
        print("  FAIL: registry kind must be ccie-blueprint-coverage")
        return 1

    statuses = data.get("statuses") or []
    platforms = data.get("platforms") or {}
    parts = {str(p.get("id")): p for p in data.get("parts") or [] if isinstance(p, dict)}
    if set(parts) != set(EXPECTED_PARTS):
        errors.append(f"parts must be exactly {sorted(EXPECTED_PARTS)}, got {sorted(parts)}")

    docs: dict[str, str] = {}
    for part_id, part in parts.items():
        expected = EXPECTED_PARTS.get(part_id)
        if expected and part.get("weight") != expected[0]:
            errors.append(f"part {part_id} weight must be {expected[0]}, got {part.get('weight')}")
        doc = SECTION / str(part.get("doc", ""))
        if not part.get("doc") or not doc.is_file():
            errors.append(f"part {part_id} document missing: {part.get('doc')}")
        else:
            docs[part_id] = doc.read_text(encoding="utf-8")

    seen: set[str] = set()
    per_part: dict[str, list[dict]] = {part_id: [] for part_id in parts}
    for index, topic in enumerate(data.get("topics") or []):
        if not isinstance(topic, dict):
            errors.append(f"topics[{index}] must be a mapping")
            continue
        topic_id = str(topic.get("id", ""))
        label = f"topic {topic_id or index}"
        if not topic_id or not topic.get("title"):
            errors.append(f"{label} needs id and title")
            continue
        if topic_id in seen:
            errors.append(f"{label} is duplicated")
        seen.add(topic_id)
        part_id = topic_id.split(".")[0] + ".0"
        if part_id not in parts:
            errors.append(f"{label} belongs to no part")
            continue
        per_part[part_id].append(topic)
        if topic.get("status") not in statuses:
            errors.append(f"{label} status {topic.get('status')!r} not in {statuses}")
        if topic.get("platform") not in platforms:
            errors.append(f"{label} platform {topic.get('platform')!r} not in platforms")
        lab = str(topic.get("lab", ""))
        if not re.fullmatch(rf"L{part_id[0]}-\d\d", lab):
            errors.append(f"{label} lab {lab!r} must look like L{part_id[0]}-NN")
        text = docs.get(part_id)
        if text is not None:
            rows = table_ids(text)
            if topic_id not in rows:
                errors.append(f"{label} missing from the topic map in {parts[part_id]['doc']}")
            if lab and lab not in rows:
                errors.append(f"{label} lab {lab} missing from the lab table in {parts[part_id]['doc']}")

    for part_id, topics in per_part.items():
        expected = EXPECTED_PARTS.get(part_id)
        if expected and len(topics) != expected[1]:
            errors.append(f"part {part_id} must have {expected[1]} topics, has {len(topics)}")

    total = sum(len(t) for t in per_part.values())
    print(f"  Topics: {total}")
    for part_id in sorted(per_part):
        topics = per_part[part_id]
        counts = {s: sum(1 for t in topics if t.get("status") == s) for s in statuses}
        done = sum(counts.get(s, 0) for s in DONE_STATUSES)
        title = parts[part_id].get("title", "")
        breakdown = ", ".join(f"{s} {n}" for s, n in counts.items() if n)
        print(f"  {part_id} {title} ({parts[part_id].get('weight')}%): {done}/{len(topics)} labbed — {breakdown}")

    if errors:
        for error in errors:
            print(f"  FAIL: {error}")
        return 1
    print("  Result: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
