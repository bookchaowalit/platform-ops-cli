#!/usr/bin/env python3
"""Refresh or check the data snapshots that sibling service repositories ship.

architecture-docs owns the document catalog and the free SaaS catalog; the services
`bookchaowalit/platform-knowledge-api` and `bookchaowalit/platform-saas-catalog-api` ship deployable
snapshots. This script (run from architecture-docs) validates the generated file's shape, then
copies it into the sibling checkout (override SOLO_KNOWLEDGE_API_ROOT / SOLO_SAAS_CATALOG_ROOT)
or compares it for `check`. The service repositories never read from architecture-docs. A missing
sibling is skipped with a warning. Nothing is staged or
committed in the sibling; commit the refreshed snapshot there.

    service_catalog_sync.py sync  knowledge-api|saas-catalog
    service_catalog_sync.py check knowledge-api|saas-catalog
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from _env import PROJECT_ROOT, sibling_repo_root

MAX_BYTES = 20 * 1024 * 1024

# service -> (repo, source in architecture-docs, snapshot path in the sibling, marker key, marker value, list key, id key)
SERVICES = {
    "knowledge-api": (
        "platform-knowledge-api",
        "data/indexes/document-catalog.json",
        "data/document-catalog.json",
        ("schema_version", "solo-empire.document-catalog.v1"),
        ("documents", "path"),
    ),
    "saas-catalog": (
        "platform-saas-catalog-api",
        "systems/cloud-platform/free-saas-master-catalog-2026.json",
        "data/free-saas-master-catalog-2026.json",
        ("contract", "solo-empire.free-saas-catalog.v1"),
        ("services", "id"),
    ),
}


def validate_catalog(payload: Any, marker: tuple[str, str], records_spec: tuple[str, str]) -> list[str]:
    if not isinstance(payload, dict):
        return ["catalog must be a JSON object"]
    errors: list[str] = []
    key, expected = marker
    if payload.get(key) != expected:
        errors.append(f"{key} must be {expected}")
    list_key, id_key = records_spec
    records = payload.get(list_key)
    if not isinstance(records, list) or not records:
        return errors + [f"{list_key} must be a non-empty list"]
    seen: set[str] = set()
    for index, record in enumerate(records):
        ident = record.get(id_key) if isinstance(record, dict) else None
        if not isinstance(ident, str) or not ident:
            errors.append(f"{list_key}[{index}]: {id_key} must be a non-empty string")
        elif ident in seen:
            errors.append(f"duplicate {id_key}: {ident}")
        else:
            seen.add(ident)
    return errors


def is_current(payload: dict[str, Any], dest: Path) -> bool:
    try:
        return json.loads(dest.read_text(encoding="utf-8")) == payload
    except (OSError, ValueError):
        return False


def atomic_write(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".catalog-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("sync", "check"))
    parser.add_argument("service", choices=sorted(SERVICES))
    args = parser.parse_args(argv)

    repo, source_rel, dest_rel, marker, records_spec = SERVICES[args.service]
    root = sibling_repo_root(repo)
    if not root.is_dir():
        print(f"WARN: bookchaowalit/{repo} checkout not found at {root}; skipping")
        return 0
    source = PROJECT_ROOT / source_rel
    dest = root / dest_rel
    try:
        raw = source.read_bytes()
        if len(raw) > MAX_BYTES:
            raise ValueError(f"{source} exceeds the {MAX_BYTES} byte catalog limit")
        payload = json.loads(raw.decode("utf-8"))
        errors = validate_catalog(payload, marker, records_spec)
        if errors:
            raise ValueError("invalid catalog: " + "; ".join(errors[:5]))
    except (OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2

    current = is_current(payload, dest)
    count = len(payload[records_spec[0]])
    if args.action == "check":
        if current:
            print(f"OK: {dest} matches {source_rel} ({count} records)")
            return 0
        print(f"STALE: {dest} differs from {source_rel}; run `task {args.service}:catalog:sync`", file=sys.stderr)
        return 1
    if current:
        print(f"OK: {dest} already current ({count} records)")
        return 0
    atomic_write(dest, raw)
    print(f"OK: wrote {dest} ({count} records) from {source_rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
