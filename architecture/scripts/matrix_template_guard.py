#!/usr/bin/env python3
"""Validate the CSV-first reusable matrix template contract."""

from __future__ import annotations

import csv
from pathlib import Path

import yaml

from _env import PROJECT_ROOT  # noqa: E402


MATRIX_ROOT = PROJECT_ROOT / "templates" / "matrices"
CSV_TEMPLATE = MATRIX_ROOT / "matrix-template.csv"
SCHEMA = MATRIX_ROOT / "matrix-schema.yaml"
MAPPINGS = MATRIX_ROOT / "sync-mappings.yaml"


def rel(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def load_yaml(path: Path, errors: list[str]) -> dict:
    if not path.is_file():
        errors.append(f"missing file: {rel(path)}")
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        errors.append(f"{rel(path)}: invalid YAML: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{rel(path)}: YAML root must be a mapping")
        return {}
    return value


def validate_csv(schema: dict, errors: list[str]) -> list[str]:
    if not CSV_TEMPLATE.is_file():
        errors.append(f"missing file: {rel(CSV_TEMPLATE)}")
        return []
    try:
        with CSV_TEMPLATE.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        errors.append(f"{rel(CSV_TEMPLATE)}: invalid CSV: {exc}")
        return []
    if not rows or not rows[0]:
        errors.append(f"{rel(CSV_TEMPLATE)}: missing CSV header")
        return []
    header = rows[0]
    if len(header) != len(set(header)):
        errors.append(f"{rel(CSV_TEMPLATE)}: duplicate CSV header")
    if len(rows) != 1:
        errors.append(f"{rel(CSV_TEMPLATE)}: starter must contain header only")
    field_order = schema.get("field_order", [])
    if header != field_order:
        errors.append(f"{rel(CSV_TEMPLATE)}: header does not match matrix-schema.yaml field_order")
    required_fields = schema.get("validation", {}).get("required_fields", [])
    missing_required = [field for field in required_fields if field not in header]
    if missing_required:
        errors.append(f"{rel(CSV_TEMPLATE)}: missing required fields: {missing_required}")
    identity_fields = schema.get("identity_fields", [])
    if any(field not in header for field in identity_fields):
        errors.append(f"{rel(CSV_TEMPLATE)}: identity fields must be CSV columns")
    return header


def validate_schema(schema: dict, errors: list[str]) -> None:
    expected = {"version": 1, "kind": "matrix-template-schema"}
    for key, value in expected.items():
        if schema.get(key) != value:
            errors.append(f"{rel(SCHEMA)}: expected {key}={value!r}")
    fields = schema.get("fields")
    if not isinstance(fields, list) or not fields:
        errors.append(f"{rel(SCHEMA)}: fields must be a non-empty list")
        return
    names = [field.get("name") for field in fields if isinstance(field, dict)]
    if len(names) != len(set(names)):
        errors.append(f"{rel(SCHEMA)}: duplicate field name")
    if names != schema.get("field_order"):
        errors.append(f"{rel(SCHEMA)}: fields order does not match field_order")
    required = {
        field.get("name")
        for field in fields
        if isinstance(field, dict) and field.get("required") is True
    }
    declared_required = set(schema.get("validation", {}).get("required_fields", []))
    if not required.issubset(declared_required):
        errors.append(f"{rel(SCHEMA)}: validation.required_fields omits required fields")
    if schema.get("canonical_file") != rel(CSV_TEMPLATE):
        errors.append(f"{rel(SCHEMA)}: canonical_file must point to {rel(CSV_TEMPLATE)}")


def validate_mappings(mappings: dict, header: list[str], errors: list[str]) -> None:
    expected = {"version": 1, "kind": "matrix-sync-mappings"}
    for key, value in expected.items():
        if mappings.get(key) != value:
            errors.append(f"{rel(MAPPINGS)}: expected {key}={value!r}")
    if mappings.get("schema") != rel(SCHEMA):
        errors.append(f"{rel(MAPPINGS)}: schema must point to {rel(SCHEMA)}")
    canonical = mappings.get("canonical", {})
    if canonical.get("path") != rel(CSV_TEMPLATE):
        errors.append(f"{rel(MAPPINGS)}: canonical.path must point to {rel(CSV_TEMPLATE)}")
    synchronization = mappings.get("synchronization", {})
    for key in ("default_direction", "writeback", "conflict_policy", "delete_policy"):
        if not isinstance(synchronization.get(key), str) or not synchronization[key].strip():
            errors.append(f"{rel(MAPPINGS)}: synchronization.{key} is required")
    binding = mappings.get("binding")
    if not isinstance(binding, dict):
        errors.append(f"{rel(MAPPINGS)}: binding must be a mapping")
    else:
        if binding.get("scope") != "workspace-local":
            errors.append(f"{rel(MAPPINGS)}: binding.scope must be workspace-local")
        if binding.get("source_template_policy") != "never_store_provider_links_in_this_template":
            errors.append(f"{rel(MAPPINGS)}: source template link policy is missing")
        if not isinstance(binding.get("cli"), str) or not binding["cli"].strip():
            errors.append(f"{rel(MAPPINGS)}: binding.cli is required")
        operations = binding.get("operations")
        if not isinstance(operations, dict) or any(name not in operations for name in ("status", "plan", "link")):
            errors.append(f"{rel(MAPPINGS)}: binding.operations must declare status, plan, and link")
    targets = mappings.get("targets")
    if not isinstance(targets, list) or not targets:
        errors.append(f"{rel(MAPPINGS)}: targets must be a non-empty list")
        return
    target_ids = [target.get("id") for target in targets if isinstance(target, dict)]
    if len(target_ids) != len(set(target_ids)):
        errors.append(f"{rel(MAPPINGS)}: duplicate target id")
    required_targets = {"google-sheets", "microsoft-excel", "notion", "markdown"}
    missing_targets = sorted(required_targets - set(target_ids))
    if missing_targets:
        errors.append(f"{rel(MAPPINGS)}: missing target mappings: {missing_targets}")
    for target in targets:
        if not isinstance(target, dict):
            errors.append(f"{rel(MAPPINGS)}: every target must be a mapping")
            continue
        target_id = target.get("id", "<unknown>")
        if not isinstance(target.get("direction"), str):
            errors.append(f"{rel(MAPPINGS)}: {target_id} missing direction")
        column_mapping = target.get("column_mapping")
        if isinstance(column_mapping, dict):
            invalid_fields = [
                canonical_field
                for canonical_field in column_mapping.values()
                if canonical_field not in header
            ]
            if invalid_fields:
                errors.append(
                    f"{rel(MAPPINGS)}: {target_id} maps unknown canonical fields: {sorted(set(invalid_fields))}"
                )
        field_order = target.get("field_order")
        if isinstance(field_order, list):
            invalid_fields = [field for field in field_order if field not in header]
            if invalid_fields:
                errors.append(
                    f"{rel(MAPPINGS)}: {target_id} orders unknown canonical fields: {sorted(set(invalid_fields))}"
                )


def main() -> int:
    print("=" * 70)
    print("Solo Empire — Matrix Template Guard")
    print("=" * 70)
    errors: list[str] = []
    schema = load_yaml(SCHEMA, errors)
    mappings = load_yaml(MAPPINGS, errors)
    validate_schema(schema, errors)
    header = validate_csv(schema, errors)
    validate_mappings(mappings, header, errors)
    if errors:
        for error in errors:
            print(f"  FAIL: {error}")
        print(f"Result: FAIL ({len(errors)} errors)")
        return 1
    target_count = len(mappings.get("targets", []))
    print(f"  ✅ CSV header fields: {len(header)}")
    print("  ✅ starter CSV is header-only and schema-aligned")
    print(f"  ✅ sync targets: {target_count}")
    print("  ✅ identity, conflict, and deletion policies are declared")
    print("Result: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
