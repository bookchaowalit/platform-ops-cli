#!/usr/bin/env python3
"""Validate enterprise position coverage and position-specific templates."""

from __future__ import annotations

from pathlib import Path

import yaml

from _env import PROJECT_ROOT  # noqa: E402


REGISTRY = PROJECT_ROOT / "docs" / "registries" / "POSITION-COVERAGE.yaml"
ROLE_CATALOG = PROJECT_ROOT / "docs" / "registries" / "ROLE-CATALOG.yaml"
ROLE_GROUPS = PROJECT_ROOT / "docs" / "registries" / "ROLE-COVERAGE.yaml"
TEMPLATE_REGISTRY = PROJECT_ROOT / "templates" / "TEMPLATE-REGISTRY.yaml"
ALLOWED_STATUSES = {"covered-by-role-family", "dedicated-package", "partial"}


def rel(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def main() -> int:
    print("=" * 70)
    print("Solo Empire — Position Coverage Guard")
    print("=" * 70)
    errors: list[str] = []
    for path in (REGISTRY, ROLE_CATALOG, ROLE_GROUPS, TEMPLATE_REGISTRY):
        if not path.is_file():
            errors.append(f"missing required file: {rel(path)}")
    if errors:
        for error in errors:
            print(f"  FAIL: {error}")
        return 1
    try:
        data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
        role_catalog = yaml.safe_load(ROLE_CATALOG.read_text(encoding="utf-8"))
        role_groups = yaml.safe_load(ROLE_GROUPS.read_text(encoding="utf-8"))
        template_catalog = yaml.safe_load(TEMPLATE_REGISTRY.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print(f"  FAIL: invalid YAML: {exc}")
        return 1
    if not isinstance(data, dict) or data.get("kind") != "position-coverage-registry":
        errors.append("position registry kind must be position-coverage-registry")
    if not isinstance(role_catalog, dict) or role_catalog.get("kind") != "atomic-role-catalog":
        errors.append("atomic role catalog kind must be atomic-role-catalog")
    if not isinstance(role_groups, dict) or role_groups.get("kind") != "role-group-coverage-registry":
        errors.append("role group registry kind must be role-group-coverage-registry")
    if not isinstance(template_catalog, dict) or template_catalog.get("kind") != "reusable-template-catalog":
        errors.append("template registry kind must be reusable-template-catalog")
    positions = data.get("positions", []) if isinstance(data, dict) else []
    if not isinstance(positions, list) or not positions:
        errors.append("atomic role template registry must declare a non-empty roles list")
        positions = []
    atomic_roles = role_catalog.get("roles", []) if isinstance(role_catalog, dict) else []
    if not isinstance(atomic_roles, list) or not atomic_roles:
        errors.append("atomic role catalog must declare a non-empty roles list")
        atomic_roles = []
    role_group_rows = role_groups.get("role_groups", []) if isinstance(role_groups, dict) else []
    role_group_ids = {
        item.get("id")
        for item in role_group_rows
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    catalog_by_id = {
        item.get("id"): item
        for item in atomic_roles
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if len(catalog_by_id) != len(atomic_roles):
        errors.append("atomic role catalog contains duplicate or invalid ids")
    for role_id, role in catalog_by_id.items():
        groups = role.get("role_groups")
        if not isinstance(groups, list) or not groups:
            errors.append(f"{role_id}: role_groups must be non-empty")
        for group_id in groups if isinstance(groups, list) else []:
            if group_id not in role_group_ids:
                errors.append(f"{role_id}: unknown role group {group_id}")
    ids: set[str] = set()
    statuses: dict[str, int] = {}
    declared_position_templates: set[str] = set()
    for index, position in enumerate(positions, 1):
        if not isinstance(position, dict):
            errors.append(f"positions[{index}] must be a mapping")
            continue
        position_id = position.get("id")
        if not isinstance(position_id, str) or not position_id.strip():
            errors.append(f"positions[{index}] missing id")
            continue
        if position_id in ids:
            errors.append(f"duplicate position id: {position_id}")
        ids.add(position_id)
        for field in ("name", "category", "primary_owner"):
            if not isinstance(position.get(field), str) or not position[field].strip():
                errors.append(f"{position_id}: missing {field}")
        status = position.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{position_id}: invalid status {status!r}")
        else:
            statuses[status] = statuses.get(status, 0) + 1
        role_groups_for_position = position.get("role_groups")
        if not isinstance(role_groups_for_position, list) or not role_groups_for_position:
            errors.append(f"{position_id}: role_groups must be non-empty")
        for group_id in role_groups_for_position if isinstance(role_groups_for_position, list) else []:
            if group_id not in role_group_ids:
                errors.append(f"{position_id}: unknown role group {group_id}")
        catalog_role = catalog_by_id.get(position_id)
        if catalog_role is None:
            errors.append(f"{position_id}: missing from atomic role catalog")
        elif catalog_role.get("role_groups") != role_groups_for_position:
            errors.append(f"{position_id}: role_groups do not match atomic role catalog")
        templates = position.get("templates")
        if not isinstance(templates, list) or not templates:
            errors.append(f"{position_id}: templates must be non-empty")
            continue
        for template in templates:
            if not isinstance(template, str) or not template.startswith("templates/"):
                errors.append(f"{position_id}: invalid template path {template!r}")
                continue
            path = PROJECT_ROOT / template
            if not path.is_file():
                errors.append(f"{position_id}: missing template {template}")
            if template.startswith("templates/positions/"):
                declared_position_templates.add(template)
    if ids != set(catalog_by_id):
        missing_from_template_registry = sorted(set(catalog_by_id) - ids)
        missing_from_catalog = sorted(ids - set(catalog_by_id))
        if missing_from_template_registry:
            errors.append(
                "atomic roles missing from template coverage registry: "
                + ", ".join(missing_from_template_registry)
            )
        if missing_from_catalog:
            errors.append(
                "template coverage entries missing from atomic role catalog: "
                + ", ".join(missing_from_catalog)
            )
    catalog_position_templates = {
        item.get("path")
        for item in template_catalog.get("position_templates", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    if declared_position_templates != catalog_position_templates:
        missing = sorted(declared_position_templates - catalog_position_templates)
        extra = sorted(catalog_position_templates - declared_position_templates)
        if missing:
            errors.append(f"template registry missing position templates: {', '.join(missing)}")
        if extra:
            errors.append(f"template registry has undeclared position templates: {', '.join(extra)}")
    for template in sorted(declared_position_templates):
        parts = Path(template).parts
        if len(parts) < 4:
            errors.append(f"invalid position template path: {template}")
            continue
        index_path = PROJECT_ROOT / Path(*parts[:3]) / "README.md"
        if not index_path.is_file():
            errors.append(f"position template folder missing README: {rel(index_path)}")
    if errors:
        for error in errors:
            print(f"  FAIL: {error}")
        print(f"Result: FAIL ({len(errors)} errors)")
        return 1
    print(f"  ✅ atomic real-world roles: {len(positions)}")
    for status in sorted(statuses):
        print(f"  ✅ {status}: {statuses[status]}")
    print(f"  ✅ position-specific templates: {len(declared_position_templates)}")
    print("  ✅ every atomic role has role-group mappings and existing templates")
    print("Result: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
