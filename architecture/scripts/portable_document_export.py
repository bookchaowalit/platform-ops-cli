#!/usr/bin/env python3
"""Export the safe document catalog to portable CSV and manifest files.

The Markdown documents remain authoritative. This script exports catalog
metadata only, so the result can be imported into Sheets, Excel, or Notion
without turning the repository's knowledge base into a giant flat table.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
import sys
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
SOURCE = ROOT / "data" / "indexes" / "document-catalog.json"
DEFAULT_OUTPUT = ROOT / "data" / "exports" / "document-catalog.csv"
DEFAULT_MANIFEST = ROOT / "data" / "exports" / "manifest.json"
SOURCE_SCHEMA = "solo-empire.document-catalog.v1"
EXPORT_SCHEMA = "document-catalog-export.v1"
MANIFEST_SCHEMA = "solo-empire.portable-export-manifest.v1"

FIELDS = (
    "document_id",
    "path",
    "title",
    "document_type",
    "status",
    "root_area",
    "created",
    "updated",
    "tags",
    "aliases",
    "parent",
    "canonical",
    "description",
    "internal_link_count",
    "has_frontmatter",
    "related",
)


def _cell(value: Any) -> str:
    """Convert catalog values to deterministic, spreadsheet-friendly cells."""

    if value is None:
        return ""
    if isinstance(value, list):
        return " | ".join(_cell(item) for item in value if _cell(item))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def load_catalog(path: Path = SOURCE) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read document catalog {path}: {exc}") from exc
    if payload.get("schema_version") != SOURCE_SCHEMA:
        raise ValueError(
            f"expected {SOURCE_SCHEMA}, got {payload.get('schema_version')!r}"
        )
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("document catalog must contain a non-empty documents list")
    if any(not isinstance(document, dict) for document in documents):
        raise ValueError("document catalog documents must be objects")
    return payload


def build_rows(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for document in payload["documents"]:
        path = _cell(document.get("path"))
        if not path:
            raise ValueError("document catalog contains a row without path")
        if path in seen:
            raise ValueError(f"document catalog contains duplicate path {path!r}")
        seen.add(path)
        row = {
            "document_id": path,
            "path": path,
            "title": _cell(document.get("title")),
            "document_type": _cell(document.get("document_type")),
            "status": _cell(document.get("status")),
            "root_area": _cell(document.get("root_area")),
            "created": _cell(document.get("created")),
            "updated": _cell(document.get("updated")),
            "tags": _cell(document.get("tags")),
            "aliases": _cell(document.get("aliases")),
            "parent": _cell(document.get("parent")),
            "canonical": _cell(document.get("canonical")),
            "description": _cell(document.get("description")),
            "internal_link_count": _cell(document.get("internal_link_count")),
            "has_frontmatter": _cell(document.get("has_frontmatter")),
            "related": _cell(document.get("related")),
        }
        rows.append(row)
    return rows


def build_manifest(rows: list[dict[str, str]], output: Path) -> dict[str, Any]:
    return {
        "manifest_schema": MANIFEST_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "datasets": [
            {
                "dataset_id": "documentation-catalog",
                "schema_version": EXPORT_SCHEMA,
                "grain": "one indexed Markdown or MDX document per row",
                "format": "csv",
                "path": output.relative_to(ROOT).as_posix()
                if output.is_relative_to(ROOT)
                else str(output),
                "row_count": len(rows),
                "source": "data/indexes/document-catalog.json",
                "source_schema_version": SOURCE_SCHEMA,
                "privacy_class": "public_metadata_only",
                "retention_class": "disposable_export",
                "destinations": ["google_sheets", "microsoft_excel", "notion"],
                "body_content_included": False,
            }
        ],
    }


def write_export(
    source: Path = SOURCE,
    output: Path = DEFAULT_OUTPUT,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    payload = load_catalog(source)
    rows = build_rows(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)

    manifest = build_manifest(rows, output)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return rows, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json", action="store_true", help="print a JSON summary")
    args = parser.parse_args()

    source = args.source if args.source.is_absolute() else ROOT / args.source
    output = args.output if args.output.is_absolute() else ROOT / args.output
    manifest = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    try:
        rows, payload = write_export(source, output, manifest)
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 1

    summary = {
        "dataset_id": "documentation-catalog",
        "rows": len(rows),
        "csv": output.relative_to(ROOT).as_posix()
        if output.is_relative_to(ROOT)
        else str(output),
        "manifest": manifest.relative_to(ROOT).as_posix()
        if manifest.is_relative_to(ROOT)
        else str(manifest),
        "schema_version": payload["datasets"][0]["schema_version"],
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"Exported {summary['rows']} rows to {summary['csv']}")
        print(f"Manifest: {summary['manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
