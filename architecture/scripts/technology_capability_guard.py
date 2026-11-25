#!/usr/bin/env python3
"""Validate the Technology Capability Matrix evidence contract.

The guard is intentionally offline. It checks the canonical matrix, the four
evidence registers, their shared-dimension status vocabulary, front-matter
freshness, and explicit production-tool-to-track mappings.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

try:  # The repository normally provides PyYAML; the fallback keeps this guard testable offline.
    import yaml
except ImportError:  # pragma: no cover - exercised only on minimal hosts
    yaml = None


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import production_tools_catalog  # noqa: E402
MATRIX_RELATIVE = Path("systems/architecture/technology-capability-matrix.md")
EVIDENCE_RELATIVES = (
    Path("systems/architecture/technology-capability-evidence-product-engineering.md"),
    Path("systems/architecture/technology-capability-evidence-platform-security.md"),
    Path("systems/architecture/technology-capability-evidence-ai-systems.md"),
    Path("systems/architecture/technology-capability-evidence-data-engineering.md"),
)
VALID_STATUSES = frozenset({"covered", "partial", "gap", "deferred"})
SHIPPED_GATE_STATUSES = frozenset({"accepted", "pilot-ready", "needs-human-gate"})
DEFAULT_MAX_AGE_DAYS = 30
STATUS_RE = re.compile(r"`\s*(covered|partial|gap|deferred)\b", re.IGNORECASE)
UPDATED_RE = re.compile(r"^updated:\s*['\"]?([0-9]{4}-[0-9]{2}-[0-9]{2})['\"]?\s*$", re.MULTILINE)
TRACK_ALIASES = {
    "Architecture & Core Computer Science": frozenset(
        {"Architecture & Core Computer Science", "Architecture & Core CS"}
    ),
    "Systems, OS & Runtime Engineering": frozenset(
        {"Systems, OS & Runtime Engineering", "Systems, OS & Runtime"}
    ),
}


def markdown_section(text: str, heading: str) -> str | None:
    match = re.search(
        rf"(?ms)^##\s+{re.escape(heading)}\s*$\n?(.*?)(?=^##\s+|\Z)",
        text,
    )
    return match.group(1) if match else None


def markdown_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2 or all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        rows.append(cells)
    return rows


def extract_status(cell: str) -> str | None:
    match = STATUS_RE.search(cell)
    return match.group(1).lower() if match else None


def parse_updated(text: str) -> date | None:
    match = UPDATED_RE.search("\n".join(text.splitlines()[:30]))
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def parse_matrix_dimensions(text: str) -> list[str]:
    section = markdown_section(text, "Shared dimensions required for every Tech track")
    if section is None:
        return []
    dimensions: list[str] = []
    for cells in markdown_rows(section):
        if cells[0].isdigit() and len(cells) >= 2:
            dimensions.append(cells[1])
    return dimensions


def parse_matrix_tracks(text: str) -> list[str]:
    section = markdown_section(text, "Complete Tech track matrix")
    if section is None:
        return []
    tracks: list[str] = []
    for cells in markdown_rows(section):
        if cells[0].isdigit() and len(cells) >= 2:
            tracks.append(cells[1])
    return tracks


def parse_track_statuses(text: str) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    sections: list[tuple[int, list[list[str]]]] = []
    track_section = markdown_section(text, "Track coverage")
    if track_section is not None:
        sections.append((1, markdown_rows(track_section)))
    dimension_section = markdown_section(text, "33-dimension evidence")
    if dimension_section is not None:
        sections.append((2, markdown_rows(dimension_section)))

    statuses: dict[str, str] = {}
    for status_index, rows in sections:
        for cells in rows:
            if status_index == 1:
                if cells[0].lower() in {"track", "#"} or len(cells) <= status_index:
                    continue
                name_index = 0
            else:
                if not cells[0].isdigit() or len(cells) <= status_index:
                    continue
                name_index = 1
            track = cells[name_index]
            status = extract_status(cells[status_index])
            if not status:
                errors.append(f"track {track!r} has no valid status")
                continue
            if status not in VALID_STATUSES:
                errors.append(f"track {track!r} has invalid status {status!r}")
                continue
            statuses[track] = status
    if not sections:
        errors.append("missing track coverage rows")
    if not statuses:
        errors.append("missing track coverage rows")
    return statuses, errors


def track_variants(track: str) -> frozenset[str]:
    return TRACK_ALIASES.get(track, frozenset({track}))


def parse_shared_statuses(text: str) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    heading = next(
        (
            line[3:].strip()
            for line in text.splitlines()
            if line.startswith("## Shared-dimension coverage")
        ),
        None,
    )
    if heading is None:
        return {}, ["missing shared-dimension coverage section"]
    section = markdown_section(text, heading)
    if section is None:
        return {}, ["missing shared-dimension coverage section"]

    statuses: dict[str, str] = {}
    for cells in markdown_rows(section):
        if cells[0].lower() == "shared dimension":
            continue
        if len(cells) < 2:
            continue
        dimension = cells[0]
        status = extract_status(cells[1])
        if not status:
            errors.append(f"shared dimension {dimension!r} has no valid status")
            continue
        if status not in VALID_STATUSES:
            errors.append(f"shared dimension {dimension!r} has invalid status {status!r}")
            continue
        if dimension in statuses:
            errors.append(f"shared dimension {dimension!r} is duplicated")
        statuses[dimension] = status
    return statuses, errors


def parse_catalog_fallback(text: str) -> list[dict[str, Any]]:
    """Read only the catalog fields needed by this guard without PyYAML."""

    tools: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        id_match = re.match(r"^\s+- id:\s*([^\s#]+)\s*$", line)
        if id_match:
            current = {"id": id_match.group(1)}
            tools.append(current)
            continue
        if current is None:
            continue
        gate_match = re.match(r"^\s+gate_status:\s*([^\s#]+)\s*$", line)
        if gate_match:
            current["gate_status"] = gate_match.group(1)
            continue
        mapping_match = re.match(r"^\s+capability_tracks:\s*\[(.*)\]\s*$", line)
        if mapping_match:
            values = []
            for value in mapping_match.group(1).split(","):
                value = value.strip().strip("'\"")
                if value:
                    values.append(value)
            current["capability_tracks"] = values
    return tools


def load_catalog_tools(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    if not path.is_file():
        errors.append(f"missing {path.as_posix()}")
        return []
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as error:
            errors.append(f"{path.as_posix()} does not parse: {error}")
            return []
        tools = data.get("tools") if isinstance(data, dict) else None
        if isinstance(tools, list):
            return [tool for tool in tools if isinstance(tool, dict)]
        errors.append("production tools catalog must contain a tools list")
        return []
    return parse_catalog_fallback(text)


def check_updated(
    path: Path,
    text: str,
    today: date,
    max_age_days: int,
    errors: list[str],
    warnings: list[str],
) -> None:
    updated = parse_updated(text)
    label = path.as_posix()
    if updated is None:
        errors.append(f"{label} is missing a valid updated: front-matter date")
        return
    age = (today - updated).days
    if age > max_age_days:
        warnings.append(f"{label} updated {age} days ago, older than {max_age_days} days")


def validate_repository(
    root: Path = ROOT,
    *,
    today: date | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    catalog_path: Path | None = None,
) -> dict[str, list[str]]:
    """Return deterministic errors/warnings for a capability evidence tree."""

    today = today or date.today()
    errors: list[str] = []
    warnings: list[str] = []
    matrix_path = root / MATRIX_RELATIVE
    if not matrix_path.is_file():
        return {"errors": [f"missing {MATRIX_RELATIVE.as_posix()}"], "warnings": []}
    matrix_text = matrix_path.read_text(encoding="utf-8")
    check_updated(matrix_path, matrix_text, today, max_age_days, errors, warnings)

    dimensions = parse_matrix_dimensions(matrix_text)
    if len(dimensions) != 12 or len(set(dimensions)) != 12:
        errors.append(
            f"matrix must define exactly 12 unique shared dimensions; found {len(dimensions)}"
        )
    matrix_tracks = parse_matrix_tracks(matrix_text)
    if not matrix_tracks:
        errors.append("matrix is missing complete Tech track rows")

    track_statuses: dict[str, str] = {}
    for relative_path in EVIDENCE_RELATIVES:
        path = root / relative_path
        if not path.is_file():
            errors.append(f"missing {relative_path.as_posix()}")
            continue
        text = path.read_text(encoding="utf-8")
        check_updated(path, text, today, max_age_days, errors, warnings)
        statuses, track_errors = parse_track_statuses(text)
        errors.extend(f"{relative_path.as_posix()}: {error}" for error in track_errors)
        for track, status in statuses.items():
            track_statuses[track] = status
        shared, shared_errors = parse_shared_statuses(text)
        errors.extend(f"{relative_path.as_posix()}: {error}" for error in shared_errors)
        if dimensions:
            missing = sorted(set(dimensions) - set(shared))
            extra = sorted(set(shared) - set(dimensions))
            if missing:
                errors.extend(
                    f"{relative_path.as_posix()}: missing shared dimension {dimension!r}"
                    for dimension in missing
                )
            if extra:
                errors.extend(
                    f"{relative_path.as_posix()}: unknown shared dimension {dimension!r}"
                    for dimension in extra
                )

    for track in matrix_tracks:
        if not track_variants(track).intersection(track_statuses):
            errors.append(f"matrix track {track!r} has no evidence-register status")

    catalog_errors: list[str] = []
    catalog = catalog_path or production_tools_catalog()  # owned by book-production-platform
    if not catalog.is_file() and os.environ.get("SOLO_REQUIRE_SIBLINGS") != "1":
        warnings.append(f"SKIP production tools catalog absent ({catalog.as_posix()}); set SOLO_BOOK_PRODUCTION_PLATFORM_ROOT")
        tools = []
    else:
        tools = load_catalog_tools(catalog, catalog_errors)
    errors.extend(catalog_errors)
    known_tracks = set(matrix_tracks)
    for tool in tools:
        tool_id = str(tool.get("id", "<unknown>"))
        gate_status = str(tool.get("gate_status", ""))
        mappings = tool.get("capability_tracks", [])
        if mappings is None:
            mappings = []
        if not isinstance(mappings, list) or not all(isinstance(track, str) for track in mappings):
            errors.append(f"tool {tool_id} capability_tracks must be a list of strings")
            continue
        for track in mappings:
            if track not in known_tracks:
                errors.append(f"tool {tool_id} maps to unknown capability track {track!r}")
                continue
            if gate_status in SHIPPED_GATE_STATUSES and track_statuses.get(track) == "deferred":
                errors.append(
                    f"shipped production tool {tool_id} maps to deferred track {track!r}"
                )
    return {"errors": errors, "warnings": warnings}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=int(os.environ.get("TECH_CAPABILITY_MAX_AGE_DAYS", DEFAULT_MAX_AGE_DAYS)),
    )
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = validate_repository(args.root, today=args.today, max_age_days=args.max_age_days)
    for warning in report["warnings"]:
        print(f"WARN: {warning}")
    if report["errors"]:
        for error in report["errors"]:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(
        "Technology capability evidence guard: PASS "
        f"(12 shared dimensions across {len(EVIDENCE_RELATIVES)} registers)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
