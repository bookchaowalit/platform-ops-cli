#!/usr/bin/env python3
"""Validate offline catalog snapshots and their immutable consumer evidence.

This checks local bytes and historical provenance, never live provider terms.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
RECORD = Path("systems/cloud-platform/catalog-freshness-v1.json")


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    record = json.loads((root / RECORD).read_text())
    if record.get("contract") != "solo-empire.catalog-freshness.v1":
        errors.append("unsupported evidence contract")
    checked = date.fromisoformat(record["validated_on"])
    if checked > date.today():
        errors.append("validation date is in the future")
    policy = record.get("policy", {})
    expected = {
        "selection_only": True,
        "live_paid_demand_included": False,
        "required_scraping_products_denominator_contribution": 0,
        "account_creation_approved": False,
        "deployment_approved": False,
        "registration_approved": False,
        "source_collection_approved": False,
        "license_grant": False,
        "all_internet_coverage_claimed": False,
    }
    for key, value in expected.items():
        if type(policy.get(key)) is not type(value) or policy.get(key) != value:
            errors.append(f"policy.{key}: selection boundary changed")
    catalogs = record.get("catalogs", [])
    if {entry.get("id") for entry in catalogs} != {"free-resource", "free-saas"} or len(
        catalogs
    ) != 2:
        errors.append("both distinct catalogs require one evidence record")
    for entry in catalogs:
        ident = entry["id"]
        source_commit = entry.get("source_commit", "")
        if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
            errors.append(f"{ident}: source commit must be immutable")
        source_url = f"https://github.com/bookchaowalit/architecture-docs/blob/{source_commit}/{entry['path']}"
        if entry.get("source_url") != source_url:
            errors.append(f"{ident}: source URL does not match pinned catalog")
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"{ident}: catalog path must stay local")
            continue
        raw = (root / relative).read_bytes()
        payload = json.loads(raw)
        if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
            errors.append(f"{ident}: catalog bytes changed; revalidate evidence")
        if payload.get("version") != entry.get("version"):
            errors.append(f"{ident}: catalog version differs")
        rows = payload["resources" if ident == "free-resource" else "services"]
        if len(rows) != entry.get("record_count"):
            errors.append(f"{ident}: record count differs")
        actual_dates = (
            {
                "updated": payload["updated"],
                "upstream_reviewed": payload["upstream_reviewed"],
            }
            if ident == "free-resource"
            else {"snapshot_date": payload["source"]["snapshot_date"]}
        )
        if entry.get("source_dates") != actual_dates:
            errors.append(f"{ident}: original source dates differ")
        if any(date.fromisoformat(value) > checked for value in actual_dates.values()):
            errors.append(f"{ident}: source date follows validation date")
        if entry.get("freshness_status") != "provider-terms-not-revalidated":
            errors.append(f"{ident}: offline check cannot assert provider freshness")
        if ident == "free-saas":
            if entry.get("workbook_sha256") != payload["source"]["sha256"]:
                errors.append("free-saas: evidence workbook fingerprint differs")
            file_name = payload["source"]["file_name"]
            if Path(file_name).name != file_name:
                errors.append("free-saas: workbook path must stay within sources")
                continue
            workbook = root / "systems/cloud-platform/sources" / file_name
            if (
                hashlib.sha256(workbook.read_bytes()).hexdigest()
                != payload["source"]["sha256"]
            ):
                errors.append(
                    "free-saas: workbook bytes differ from projection provenance"
                )
        consumers = entry.get("consumers", [])
        if not consumers:
            errors.append(f"{ident}: consumer evidence missing")
        for consumer in consumers:
            commit = consumer.get("commit", "")
            repo = consumer.get("repository", "")
            path = consumer.get("path", "")
            if not re.fullmatch(r"[0-9a-f]{40}", commit):
                errors.append(f"{ident}: consumer commit must be immutable")
            if not repo or not path or path.startswith("/") or ".." in Path(path).parts:
                errors.append(f"{ident}: consumer repository/path missing or invalid")
            expected_url = f"https://github.com/{repo}/blob/{commit}/{path}"
            if consumer.get("url") != expected_url:
                errors.append(f"{ident}: consumer URL does not match pinned source")
            if not re.fullmatch(r"[0-9a-f]{64}", consumer.get("sha256", "")):
                errors.append(f"{ident}: consumer source fingerprint missing")
    return errors


def main() -> int:
    try:
        errors = validate()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Catalog freshness evidence: invalid record ({type(exc).__name__})")
        return 1
    for error in errors:
        print(f"FAIL: {error}")
    if not errors:
        print(
            "Catalog freshness evidence: OK (2 offline snapshots; provider terms require review)"
        )
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
