#!/usr/bin/env python3
"""Validate the reusable Markdown template catalog contract."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from _env import PROJECT_ROOT  # noqa: E402


TEMPLATES_ROOT = PROJECT_ROOT / "templates"
CONTRACT = TEMPLATES_ROOT / "TEMPLATE-CONTRACT.md"
REGISTRY = TEMPLATES_ROOT / "TEMPLATE-REGISTRY.yaml"
APPROVAL_HEADING = re.compile(
    r"^## .*\b(?:approval|acceptance|sign[- ]?off|quality gate)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)


def rel(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def is_artifact(path: Path) -> bool:
    return path.suffix.lower() == ".md" and path.name != "README.md" and path != CONTRACT


def has_frontmatter_fields(text: str) -> list[str]:
    errors: list[str] = []
    if not text.startswith("---\n"):
        return ["missing YAML frontmatter"]
    closing = text.find("\n---", 4)
    if closing == -1:
        return ["frontmatter is not closed"]
    frontmatter = text[4:closing]
    for field in ("title", "status", "tags", "created", "updated", "description"):
        if not re.search(rf"^{re.escape(field)}:", frontmatter, re.MULTILINE):
            errors.append(f"frontmatter missing {field}")
    return errors


def main() -> int:
    print("=" * 70)
    print("Solo Empire — Template Catalog Guard")
    print("=" * 70)
    errors: list[str] = []
    if not TEMPLATES_ROOT.is_dir():
        print("  FAIL: templates directory is missing")
        return 1
    if not CONTRACT.is_file():
        errors.append(f"missing template contract: {rel(CONTRACT)}")
    if not REGISTRY.is_file():
        errors.append(f"missing template registry: {rel(REGISTRY)}")

    registry: dict = {}
    if REGISTRY.is_file():
        try:
            loaded = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            errors.append(f"invalid template registry YAML: {exc}")
        else:
            if not isinstance(loaded, dict) or loaded.get("kind") != "reusable-template-catalog":
                errors.append("template registry kind must be reusable-template-catalog")
            else:
                registry = loaded

    artifacts = sorted(path for path in TEMPLATES_ROOT.rglob("*.md") if is_artifact(path))
    if not artifacts:
        errors.append("no reusable Markdown artifacts found")
    for path in artifacts:
        text = path.read_text(encoding="utf-8")
        for error in has_frontmatter_fields(text):
            errors.append(f"{rel(path)}: {error}")
        if "## Document control" not in text:
            errors.append(f"{rel(path)}: missing ## Document control")
        if not APPROVAL_HEADING.search(text):
            errors.append(f"{rel(path)}: missing approval/acceptance/sign-off/quality-gate section")

    shared_paths = {
        item.get("path")
        for item in registry.get("shared_markdown_templates", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    yaml_paths = {
        item.get("path")
        for item in registry.get("yaml_templates", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    csv_paths = {
        item.get("path")
        for item in registry.get("csv_templates", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    position_paths = {
        item.get("path")
        for item in registry.get("position_templates", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    lifecycle_paths: set[str] = set()
    role_paths: set[str] = set()
    source_registries = registry.get("source_registries", {})
    if isinstance(source_registries, dict):
        lifecycle_registry = PROJECT_ROOT / str(source_registries.get("lifecycle", ""))
        role_registry = PROJECT_ROOT / str(source_registries.get("role_groups", ""))
        for source_path, destination in ((lifecycle_registry, lifecycle_paths), (role_registry, role_paths)):
            if not source_path.is_file():
                errors.append(f"missing source registry: {rel(source_path)}")
                continue
            try:
                source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                errors.append(f"invalid source registry YAML {rel(source_path)}: {exc}")
                continue
            if destination is lifecycle_paths:
                for stage in source.get("stages", []) if isinstance(source, dict) else []:
                    if isinstance(stage, dict):
                        destination.update(item for item in stage.get("templates", []) if isinstance(item, str))
            else:
                role_rows = source.get("role_groups", source.get("roles", [])) if isinstance(source, dict) else []
                for role in role_rows:
                    if isinstance(role, dict):
                        destination.update(item for item in role.get("required_templates", []) if isinstance(item, str))

    covered_paths = lifecycle_paths | role_paths | shared_paths | position_paths
    for path in artifacts:
        path_str = rel(path)
        if path_str not in covered_paths:
            errors.append(f"{path_str}: not covered by lifecycle, role, or shared template registry")
    for path_str in sorted(covered_paths):
        if not (PROJECT_ROOT / path_str).is_file():
            errors.append(f"registry references missing Markdown template: {path_str}")

    actual_yaml_paths = {
        rel(path)
        for path in TEMPLATES_ROOT.rglob("*.yaml")
        if path != REGISTRY
    }
    for path_str in sorted(actual_yaml_paths - yaml_paths):
        errors.append(f"{path_str}: not listed in yaml_templates")
    for path_str in sorted(yaml_paths - actual_yaml_paths):
        errors.append(f"registry references missing YAML template: {path_str}")
    actual_csv_paths = {rel(path) for path in TEMPLATES_ROOT.rglob("*.csv")}
    for path_str in sorted(actual_csv_paths - csv_paths):
        errors.append(f"{path_str}: not listed in csv_templates")
    for path_str in sorted(csv_paths - actual_csv_paths):
        errors.append(f"registry references missing CSV template: {path_str}")
    for path_str in sorted(position_paths):
        if not (PROJECT_ROOT / path_str).is_file():
            errors.append(f"registry references missing position template: {path_str}")

    yaml_contract_count = 0
    for item in registry.get("yaml_templates", []):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            errors.append("yaml_templates contains an entry without a path")
            continue
        path_str = item["path"]
        path = PROJECT_ROOT / path_str
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            errors.append(f"{path_str}: invalid YAML: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{path_str}: YAML root must be a mapping")
            continue
        for key in item.get("required_top_level_keys", []):
            if not isinstance(key, str) or key not in data:
                errors.append(f"{path_str}: missing required top-level key: {key}")
        for key, expected in item.get("expected_values", {}).items():
            if data.get(key) != expected:
                errors.append(f"{path_str}: expected {key}={expected!r}, found {data.get(key)!r}")
        yaml_contract_count += 1

    directories = sorted({path.parent for path in artifacts})
    index_count = 0
    for directory in directories:
        index = directory / "README.md"
        if not index.is_file():
            errors.append(f"{rel(directory)}: missing README.md template index")
            continue
        index_count += 1
        index_text = index.read_text(encoding="utf-8")
        if (
            "| File |" not in index_text
            and "| Artifact |" not in index_text
            and "| Folder |" not in index_text
            and "| Role |" not in index_text
            and "| Role group |" not in index_text
        ):
            errors.append(f"{rel(index)}: missing file/artifact/folder map")

    if errors:
        for error in errors:
            print(f"  FAIL: {error}")
        print(f"Result: FAIL ({len(errors)} errors)")
        return 1

    print(f"  ✅ reusable Markdown artifacts: {len(artifacts)}")
    print(f"  ✅ lifecycle/role-covered Markdown templates: {len(lifecycle_paths | role_paths)}")
    print(f"  ✅ explicitly shared Markdown templates: {len(shared_paths)}")
    print(f"  ✅ position-specific Markdown templates: {len(position_paths)}")
    print(f"  ✅ machine-readable YAML templates: {len(yaml_paths)}")
    print(f"  ✅ YAML schema contracts: {yaml_contract_count}")
    print(f"  ✅ template indexes: {index_count}")
    print("  ✅ every artifact has metadata, document control, and a gate section")
    print("  ✅ template contract exists and all artifact directories are indexed")
    print("Result: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
