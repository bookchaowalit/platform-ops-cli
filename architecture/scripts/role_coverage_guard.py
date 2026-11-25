#!/usr/bin/env python3
"""Validate the role/process/template coverage registry."""

from __future__ import annotations

import sys
import re
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FAIL: PyYAML is required (import yaml)")
    raise SystemExit(1)

from _env import KNOWLEDGE_ROOT, PROJECT_ROOT, repo_rel, repo_file, is_external_ref  # noqa: E402


def ok_file(rel) -> bool:
    """Present here, or an evidence path in another repository (not verifiable from this checkout)."""
    return is_external_ref(str(rel)) or repo_file(rel).is_file()


def ok_dir(rel) -> bool:
    return is_external_ref(str(rel)) or repo_file(rel).is_dir()


REGISTRY = PROJECT_ROOT / "docs" / "registries" / "ROLE-COVERAGE.yaml"
ROLE_CATALOG = PROJECT_ROOT / "docs" / "registries" / "ROLE-CATALOG.yaml"
ROLE_CLASSIFICATIONS = PROJECT_ROOT / "docs" / "registries" / "ROLE-CLASSIFICATIONS.yaml"
ROLE_ECONOMIC_ALIGNMENT = PROJECT_ROOT / "docs" / "registries" / "ROLE-ECONOMIC-ALIGNMENT.yaml"
ROLES_INDEX = KNOWLEDGE_ROOT / "roles" / "README.md"
BA_PROCESS = KNOWLEDGE_ROOT / "roles" / "business-analyst" / "PROCESS.md"
PROCESS_TEMPLATE = PROJECT_ROOT / "templates" / "roles" / "process-standard.md"
BA_TEMPLATE_INDEX = PROJECT_ROOT / "templates" / "roles" / "business-analyst" / "README.md"
END_TO_END_FLOW = KNOWLEDGE_ROOT / "roles" / "END-TO-END-OPERATING-FLOW.md"
PILOT_CHECKLIST = KNOWLEDGE_ROOT / "roles" / "PILOT-EXECUTION-CHECKLIST.md"
MATURITY = {"not-started", "partial", "pilot-ready", "operational", "battle-tested"}
FUNCTION_IDS = {
    "direction-strategy", "discovery-analysis", "design-architecture",
    "build-creation", "operations-execution", "growth-distribution",
    "governance-assurance",
}


def rel(path: Path) -> str:
    return repo_rel(path)


def load_registry() -> dict:
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("registry must be a version 1 mapping")
    if data.get("kind") != "role-group-coverage-registry":
        raise ValueError("registry kind must be role-group-coverage-registry")
    if not isinstance(data.get("standard"), dict):
        raise ValueError("standard must be a mapping")
    roles = data.get("role_groups")
    if not isinstance(roles, list) or not roles:
        raise ValueError("role_groups must be a non-empty list")
    return data


def validate_function_rotation(data: object, system_ids: set[str]) -> list[str]:
    """Validate the evidence-gated function rotation plan against the seven functions."""
    if not isinstance(data, dict):
        return ["function_rotation must be a mapping"]
    errors: list[str] = []
    if data.get("source_function_registry") != rel(ROLE_CLASSIFICATIONS):
        errors.append(f"function_rotation source_function_registry must point to {rel(ROLE_CLASSIFICATIONS)}")
    path = data.get("vertical_path")
    if not isinstance(path, list) or not path or any(step not in FUNCTION_IDS for step in path):
        errors.append("function_rotation vertical_path must list known function IDs")
    stages = data.get("stages")
    if not isinstance(stages, list) or not stages:
        return errors + ["function_rotation stages must be a non-empty list"]
    seen: set[str] = set()
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("id"), str):
            errors.append("function_rotation stage must be a mapping with an id")
            continue
        stage_id = stage["id"]
        if stage_id in seen:
            errors.append(f"function_rotation duplicate stage: {stage_id}")
        seen.add(stage_id)
        if stage.get("economic_system") not in system_ids:
            errors.append(f"function_rotation {stage_id}: unknown economic_system")
        if stage.get("focus") not in FUNCTION_IDS:
            errors.append(f"function_rotation {stage_id}: focus must be a known function")
        if not isinstance(stage.get("exit_gate"), str) or not stage["exit_gate"].strip():
            errors.append(f"function_rotation {stage_id}: exit_gate is required")
        share = stage.get("target_share")
        if not isinstance(share, dict) or set(share) != FUNCTION_IDS:
            errors.append(f"function_rotation {stage_id}: target_share must cover all seven functions")
        elif any(type(v) is not int or v < 0 for v in share.values()) or sum(share.values()) != 100:
            errors.append(f"function_rotation {stage_id}: target_share must be non-negative integers summing to 100")
    if data.get("active_stage") not in seen:
        errors.append("function_rotation active_stage must name a declared stage")
    history = data.get("stage_history", [])
    if not isinstance(history, list):
        return errors + ["function_rotation stage_history must be a list"]
    for entry in history:
        if not isinstance(entry, dict) or entry.get("stage") not in seen:
            errors.append("function_rotation stage_history entry must name a declared stage")
            continue
        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"function_rotation stage_history {entry['stage']}: evidence is required")
            continue
        for item in evidence:
            if not isinstance(item, str) or not ok_file(item):
                errors.append(f"function_rotation stage_history {entry['stage']}: missing evidence {item}")
        if entry.get("stage") == data.get("active_stage"):
            errors.append("function_rotation active_stage cannot already be exited")
    return errors


def validate_classifications(data: object, atomic_role_ids: set[str]) -> list[str]:
    """Validate independent classification views without assigning human authority."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["role classifications must be a mapping"]
    for field, expected in (
        ("version", 1), ("kind", "role-classification-registry"),
        ("status", "current"), ("source_role_catalog", rel(ROLE_CATALOG)),
    ):
        if data.get(field) != expected:
            errors.append(f"role classifications {field} must be {expected}")
    policy = data.get("policy")
    for field in (
        "context_values_require_explicit_assignment", "classification_does_not_grant_authority",
    ):
        if not isinstance(policy, dict) or policy.get(field) is not True:
            errors.append(f"role classifications policy.{field} must be true")

    expected_axes = {
        "function": ("atomic-role", rel(ROLE_CLASSIFICATIONS)),
        "process-group": ("atomic-role", rel(REGISTRY)),
        "domain": ("work-context", "projects/workspaces.yaml"),
        "seniority": ("explicit-context", rel(ROLE_CLASSIFICATIONS)),
        "authority": ("explicit-context", rel(ROLE_CLASSIFICATIONS)),
    }

    def definitions(field: str, expected_ids: set[str]) -> dict[str, dict]:
        rows = data.get(field)
        result: dict[str, dict] = {}
        if not isinstance(rows, list) or not rows:
            errors.append(f"role classifications {field} must be a non-empty list")
            return result
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                errors.append(f"role classifications {field} contains an invalid definition")
                continue
            identifier = row["id"]
            if identifier in result:
                errors.append(f"role classifications {field}: duplicate id {identifier}")
            result[identifier] = row
            for label in ("name", "description"):
                if not isinstance(row.get(label), str) or not row[label].strip():
                    errors.append(f"role classifications {field}.{identifier}: missing {label}")
        if set(result) != expected_ids:
            errors.append(
                f"role classifications {field}: missing {sorted(expected_ids - set(result))}; "
                f"unknown {sorted(set(result) - expected_ids)}"
            )
        return result

    axes = definitions("classification_types", set(expected_axes))
    for axis, (scope, source) in expected_axes.items():
        row = axes.get(axis, {})
        if row.get("assignment_scope") != scope or row.get("source_registry") != source:
            errors.append(f"role classifications {axis}: expected scope {scope} and source {source}")
    functions = definitions("functions", FUNCTION_IDS)
    rows = data.get("role_functions")
    if not isinstance(rows, list) or not rows:
        errors.append("role classifications role_functions must be a non-empty list")
        rows = []
    mapped_roles: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("role_id"), str):
            errors.append("role classifications role_functions contains an invalid role mapping")
            continue
        role_id = row["role_id"]
        if role_id in mapped_roles:
            errors.append(f"role classifications duplicate role mapping: {role_id}")
        mapped_roles.add(role_id)
        values = row.get("functions")
        if not isinstance(values, list) or not values or any(not isinstance(v, str) for v in values):
            errors.append(f"role classifications {role_id}: functions must be non-empty string IDs")
            continue
        if len(set(values)) != len(values):
            errors.append(f"role classifications {role_id}: duplicate functions")
        for value in values:
            if value not in functions:
                errors.append(f"role classifications {role_id}: unknown function {value}")
    if mapped_roles != atomic_role_ids:
        errors.append(
            f"role classifications coverage: missing {sorted(atomic_role_ids - mapped_roles)}; "
            f"unknown {sorted(mapped_roles - atomic_role_ids)}"
        )
    return errors


def check() -> int:
    print("=" * 70)
    print("Solo Empire — Role-Group Coverage Guard")
    print("=" * 70)
    errors: list[str] = []

    for path in (
        REGISTRY,
        ROLE_CATALOG,
        ROLE_CLASSIFICATIONS,
        ROLE_ECONOMIC_ALIGNMENT,
        ROLES_INDEX,
        BA_PROCESS,
        PROCESS_TEMPLATE,
        BA_TEMPLATE_INDEX,
        END_TO_END_FLOW,
        PILOT_CHECKLIST,
    ):
        if not path.is_file():
            errors.append(f"missing required file: {rel(path)}")

    if errors:
        for error in errors:
            print(f"  FAIL: {error}")
        return 1

    try:
        data = load_registry()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"  FAIL: {exc}")
        return 1

    standard = data["standard"]
    if data.get("atomic_role_catalog") != rel(ROLE_CATALOG):
        errors.append(f"atomic_role_catalog must point to {rel(ROLE_CATALOG)}")
    try:
        role_catalog = yaml.safe_load(ROLE_CATALOG.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        role_catalog = None
        errors.append(f"invalid atomic role catalog: {exc}")
    atomic_role_ids: set[str] = set()
    if isinstance(role_catalog, dict) and role_catalog.get("kind") == "atomic-role-catalog":
        atomic_roles = role_catalog.get("roles")
        if not isinstance(atomic_roles, list) or not atomic_roles:
            errors.append("atomic role catalog must declare a non-empty roles list")
        else:
            for atomic_role in atomic_roles:
                if not isinstance(atomic_role, dict) or not isinstance(atomic_role.get("id"), str):
                    errors.append("atomic role catalog contains a role without an id")
                    continue
                atomic_role_ids.add(atomic_role["id"])
    elif role_catalog is not None:
        errors.append("atomic role catalog kind must be atomic-role-catalog")

    if isinstance(role_catalog, dict) and role_catalog.get("classification_registry") != rel(ROLE_CLASSIFICATIONS):
        errors.append(f"atomic role catalog classification_registry must point to {rel(ROLE_CLASSIFICATIONS)}")
    try:
        classifications = yaml.safe_load(ROLE_CLASSIFICATIONS.read_text(encoding="utf-8"))
        errors.extend(validate_classifications(classifications, atomic_role_ids))
    except (OSError, yaml.YAMLError) as exc:
        errors.append(f"invalid role classifications registry: {exc}")
    if not (KNOWLEDGE_ROOT / "projects/workspaces.yaml").is_file():
        errors.append("missing domain source registry: projects/workspaces.yaml")

    try:
        economic_alignment = yaml.safe_load(
            ROLE_ECONOMIC_ALIGNMENT.read_text(encoding="utf-8")
        )
    except (OSError, yaml.YAMLError) as exc:
        economic_alignment = None
        errors.append(f"invalid role economic alignment registry: {exc}")
    economic_role_ids: list[str] = []
    economic_system_ids: set[str] = set()
    if isinstance(economic_alignment, dict):
        if economic_alignment.get("version") != 1:
            errors.append("role economic alignment registry must be version 1")
        if economic_alignment.get("kind") != "role-economic-alignment":
            errors.append("role economic alignment registry kind is invalid")
        if economic_alignment.get("source_role_catalog") != rel(ROLE_CATALOG):
            errors.append(
                f"role economic alignment source_role_catalog must point to {rel(ROLE_CATALOG)}"
            )
        systems = economic_alignment.get("economic_systems")
        if not isinstance(systems, list) or not systems:
            errors.append("role economic alignment must declare economic_systems")
        else:
            for system in systems:
                if isinstance(system, dict) and isinstance(system.get("id"), str):
                    economic_system_ids.add(system["id"])
                else:
                    errors.append("role economic alignment has an economic system without an id")
        families = economic_alignment.get("role_families")
        if not isinstance(families, list) or not families:
            errors.append("role economic alignment must declare role_families")
        else:
            for family in families:
                if not isinstance(family, dict):
                    errors.append("role economic alignment role_families must contain mappings")
                    continue
                family_id = family.get("id", "<missing>")
                family_roles = family.get("role_ids")
                if not isinstance(family_roles, list) or not family_roles:
                    errors.append(f"{family_id}: role_ids must be a non-empty list")
                else:
                    economic_role_ids.extend(
                        role_id for role_id in family_roles if isinstance(role_id, str)
                    )
                family_goals = family.get("goals")
                if not isinstance(family_goals, dict) or set(family_goals) != economic_system_ids:
                    errors.append(
                        f"{family_id}: goals must cover every declared economic system"
                    )
        errors.extend(
            validate_function_rotation(economic_alignment.get("function_rotation"), economic_system_ids)
        )
    if atomic_role_ids:
        economic_role_set = set(economic_role_ids)
        duplicates = sorted(
            role_id for role_id in economic_role_set if economic_role_ids.count(role_id) > 1
        )
        missing_economic_roles = sorted(atomic_role_ids - economic_role_set)
        unknown_economic_roles = sorted(economic_role_set - atomic_role_ids)
        if duplicates:
            errors.append("role economic alignment duplicates roles: " + ", ".join(duplicates))
        if missing_economic_roles:
            errors.append(
                "roles missing economic alignment: " + ", ".join(missing_economic_roles)
            )
        if unknown_economic_roles:
            errors.append(
                "economic alignment references unknown roles: " + ", ".join(unknown_economic_roles)
            )
    required_sections = standard.get("required_process_sections")
    if not isinstance(required_sections, list) or not required_sections:
        errors.append("standard.required_process_sections must be a non-empty list")
    required_artifacts = standard.get("required_artifact_sections")
    if not isinstance(required_artifacts, list) or not required_artifacts:
        errors.append("standard.required_artifact_sections must be a non-empty list")

    roles = data["role_groups"]
    for field, path in (
        ("cross_role_flow", END_TO_END_FLOW),
        ("pilot_checklist", PILOT_CHECKLIST),
    ):
        declared = data.get(field)
        if declared != rel(path):
            errors.append(f"{field} must point to {rel(path)}")
    pilot_records = data.get("pilot_records")
    if not isinstance(pilot_records, list) or not pilot_records:
        errors.append("pilot_records must be a non-empty list")
    else:
        for record in pilot_records:
            if not isinstance(record, str) or not ok_file(record):
                errors.append(f"missing pilot record: {record}")

    seen: set[str] = set()
    template_counts: dict[str, int] = {}
    template_index_count = 0
    subprocess_counts: dict[str, int] = {}
    role_group_ids = {
        role.get("id")
        for role in roles
        if isinstance(role, dict) and isinstance(role.get("id"), str)
    }
    if isinstance(role_catalog, dict):
        for atomic_role in role_catalog.get("roles", []) if isinstance(role_catalog.get("roles"), list) else []:
            if not isinstance(atomic_role, dict):
                continue
            for group_id in atomic_role.get("role_groups", []) if isinstance(atomic_role.get("role_groups"), list) else []:
                if group_id not in role_group_ids:
                    errors.append(f"atomic role {atomic_role.get('id')}: unknown role group: {group_id}")

    for index, role in enumerate(roles, 1):
        if not isinstance(role, dict):
            errors.append(f"roles[{index}] must be a mapping")
            continue
        role_id = role.get("id")
        if not isinstance(role_id, str) or not role_id.strip():
            errors.append(f"roles[{index}] missing id")
            continue
        if role_id in seen:
            errors.append(f"duplicate role id: {role_id}")
        seen.add(role_id)
        if not isinstance(role.get("name"), str) or not role["name"].strip():
            errors.append(f"{role_id}: name is required")
        maturity = role.get("maturity")
        if maturity not in MATURITY:
            errors.append(f"{role_id}: invalid maturity {maturity!r}")
        for field in ("owner_boundary", "process"):
            value = role.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{role_id}: {field} is required")
            elif not ok_file(value) and not ok_dir(value):
                errors.append(f"{role_id}: missing {field}: {value}")
        charter = role.get("charter")
        if not isinstance(charter, str) or not charter.strip():
            errors.append(f"{role_id}: charter is required")
        elif not ok_file(charter):
            errors.append(f"{role_id}: missing charter: {charter}")
        else:
            charter_text = (repo_file(charter)).read_text(encoding="utf-8")
            charter_contract = {
                "document_control": ("## Document control",),
                "mission": ("## Mission", "## Mission and outcomes"),
                "scope": ("## Scope", "## Scope and boundaries", "## Scope and exclusions"),
                "responsibility": (
                    "## Accountability and interfaces",
                    "## Responsibilities and decision rights",
                ),
                "artifacts": (
                    "## Standard outputs",
                    "## Standard outputs and gates",
                    "## Required processes and artifacts",
                ),
                "controls": ("## Quality gates", "## Controls and stop conditions", "## Controls"),
                "metrics": ("## Metrics", "## Metrics and evidence", "## Metrics and maturity"),
                "approval": ("## Approval",),
            }
            for key, alternatives in charter_contract.items():
                if not any(heading in charter_text for heading in alternatives):
                    errors.append(f"{role_id}: charter missing required section: {key}: {charter}")
        skill = role.get("skill")
        if skill is not None and (
            not isinstance(skill, str) or not ok_file(skill)
        ):
            errors.append(f"{role_id}: missing skill: {skill}")
        templates = role.get("required_templates", [])
        if not isinstance(templates, list) or not templates:
            errors.append(f"{role_id}: required_templates must be a non-empty list")
        else:
            template_counts[role_id] = len(templates)
            template_dirs = {
                Path(template).parent.as_posix()
                for template in templates
                if isinstance(template, str)
                and template.startswith("templates/roles/")
                and Path(template).parent.as_posix() != "templates/roles"
            }
            for template_dir in sorted(template_dirs):
                index_path = repo_file(template_dir) / "README.md"
                if not index_path.is_file():
                    errors.append(f"{role_id}: missing template directory index: {index_path}")
                else:
                    index_text = index_path.read_text(encoding="utf-8")
                    if not any(marker in index_text for marker in ("| Artifact |", "| File |")):
                        errors.append(f"{role_id}: template index has no artifact map: {index_path}")
                    else:
                        template_index_count += 1
            for template in templates:
                if not isinstance(template, str) or not ok_file(template):
                    errors.append(f"{role_id}: missing template: {template}")
                elif template.startswith("templates/roles/"):
                    text = (repo_file(template)).read_text(encoding="utf-8")
                    if "## Document control" not in text:
                        errors.append(f"{role_id}: artifact missing document control: {template}")
                    approval_headings = (
                        "## Approval",
                        "## Approval and",
                        "## Baseline approval",
                        "## Review and approval",
                        "## Acceptance and sign-off",
                        "## Sign-off and next action",
                    )
                    if not any(heading in text for heading in approval_headings):
                        errors.append(f"{role_id}: artifact missing approval/sign-off: {template}")
        for field in ("evidence_paths", "metrics", "gaps"):
            if not isinstance(role.get(field, []), list):
                errors.append(f"{role_id}: {field} must be a list")

        process_path = role.get("process")
        if isinstance(process_path, str) and ok_file(process_path):
            process_text = (repo_file(process_path)).read_text(encoding="utf-8")
            role_process_contract = {
                "document_control": ("## Document control",),
                "purpose_scope": ("## Purpose and scope", "## Purpose, scope, and boundaries"),
                "trigger_entry_exit": (
                    "## Trigger, entry criteria, and exit criteria",
                    "## Inputs, outputs, and entry/exit criteria",
                ),
                "inputs_outputs": (
                    "## Inputs, outputs, and systems of record",
                    "## Inputs, outputs, and entry/exit criteria",
                ),
                "roles_raci": ("## Roles and RACI", "## Accountability and interfaces"),
                "steps_and_controls": ("## Steps and controls", "## Operating steps"),
                "risks_dependencies": (
                    "## Risks, dependencies, and assumptions",
                    "## Controls and exceptions",
                ),
                "exceptions_escalation": (
                    "## Exceptions and escalation",
                    "## Controls and exceptions",
                ),
                "evidence_traceability": (
                    "## Handoff and references",
                    "## Evidence and handoff",
                ),
                "metrics_review": ("## Metrics and review", "## Metrics"),
                "handoff_references": ("## Changelog",),
            }
            for key in required_sections or []:
                alternatives = role_process_contract.get(key)
                if alternatives and not any(heading in process_text for heading in alternatives):
                    errors.append(f"{role_id}: process missing required section: {key}: {process_path}")
            for heading in ("## Quality gate and acceptance", "## Approval"):
                if heading not in process_text:
                    errors.append(f"{role_id}: process missing required section: {heading}: {process_path}")

        process_register = role.get("process_register")
        if not isinstance(process_register, str) or not process_register.strip():
            errors.append(f"{role_id}: process_register is required")
        elif not ok_file(process_register):
            errors.append(f"{role_id}: missing process_register: {process_register}")
        else:
            register_text = (repo_file(process_register)).read_text(encoding="utf-8")
            register_sections = re.findall(r"^## [A-Z][A-Z0-9]+-\d+ — ", register_text, re.MULTILINE)
            declared_subprocesses = role.get("subprocesses", [])
            if not isinstance(declared_subprocesses, list) or len(declared_subprocesses) < 5:
                errors.append(f"{role_id}: subprocesses must declare at least five items")
            else:
                subprocess_counts[role_id] = len(declared_subprocesses)
                if len(register_sections) != len(declared_subprocesses):
                    errors.append(
                        f"{role_id}: subprocess count mismatch: registry={len(declared_subprocesses)}, "
                        f"register={len(register_sections)}"
                    )
            for heading in (
                "## Document control",
                "## Process inventory",
                "## Metrics and review",
                "## Approval",
            ):
                if heading not in register_text:
                    errors.append(f"{role_id}: process register missing section: {heading}: {process_register}")
            for template in templates if isinstance(templates, list) else []:
                if template.startswith(f"templates/roles/{role_id}/"):
                    stem = Path(template).stem
                    if stem not in register_text:
                        errors.append(
                            f"{role_id}: process register does not map template {template}: {process_register}"
                        )

    process_text = PROCESS_TEMPLATE.read_text(encoding="utf-8")
    if "## Quality gate and acceptance" not in process_text:
        errors.append("process template missing quality gate and acceptance")
    if "## Approval" not in process_text:
        errors.append("process template missing approval section")
    process_contract = {
        "document_control": "## Document control",
        "purpose_scope": "## Purpose, scope, and boundaries",
        "trigger_entry_exit": "## Trigger, entry criteria, and exit criteria",
        "inputs_outputs": "## Inputs, outputs, and systems of record",
        "roles_raci": "## Roles and RACI",
        "steps_and_controls": "## Steps and controls",
        "risks_dependencies": "## Risks, dependencies, and assumptions",
        "exceptions_escalation": "## Exceptions and escalation",
        "evidence_traceability": "## Handoff and references",
        "metrics_review": "## Metrics and review",
        "handoff_references": "## Changelog",
    }
    for key in required_sections or []:
        heading = process_contract.get(key)
        if heading and heading not in process_text:
            errors.append(f"process template missing required contract section: {key}")
        elif not heading:
            errors.append(f"unknown required process section: {key}")

    flow_text = END_TO_END_FLOW.read_text(encoding="utf-8")
    for heading in (
        "## End-to-end flow",
        "## Handoff contracts",
        "## Cross-role gate rules",
        "## Escalation matrix",
        "## Pilot acceptance",
    ):
        if heading not in flow_text:
            errors.append(f"cross-role flow missing section: {heading}")
    for role in roles:
        if isinstance(role, dict) and role.get("name") not in flow_text:
            errors.append(f"cross-role flow missing role: {role.get('name')}")

    pilot_text = PILOT_CHECKLIST.read_text(encoding="utf-8")
    for heading in (
        "## Pilot rules",
        "## Role execution checklist",
        "## Cross-role handoff checks",
        "## Pilot result",
        "## Lessons and maturity decision",
    ):
        if heading not in pilot_text:
            errors.append(f"pilot checklist missing section: {heading}")
    for role in roles:
        if isinstance(role, dict) and role.get("name") not in pilot_text:
            errors.append(f"pilot checklist missing role: {role.get('name')}")

    ba = next((role for role in roles if role.get("id") == "business-analyst"), None)
    if ba is None:
        errors.append("business-analyst role is required")
    elif ba.get("maturity") not in {"pilot-ready", "operational", "battle-tested"}:
        errors.append("business-analyst must be at least pilot-ready")

    if errors:
        print(f"  FAIL: {len(errors)} issue(s)")
        for error in errors:
            print(f"    - {error}")
        return 1

    print(f"  ✅ role groups: {len(roles)}")
    print(f"  ✅ required process sections: {len(required_sections)}")
    print(f"  ✅ required artifact sections: {len(required_artifacts)}")
    print(f"  ✅ role-specific template declarations: {sum(template_counts.values())}")
    for role_id, count in template_counts.items():
        print(f"     - {role_id}: {count}")
    print(f"  ✅ role template indexes: {template_index_count}")
    print(f"  ✅ role-specific subprocess declarations: {sum(subprocess_counts.values())}")
    print("  ✅ cross-role flow and pilot checklist cover all roles")
    print(f"  ✅ pilot evidence records: {len(pilot_records)}")
    print("  ✅ all declared paths exist")
    print("  ✅ role-group coverage registry and atomic-role mappings are structurally valid")
    print(f"  ✅ independent classifications: 5 types, 7 functions, {len(atomic_role_ids)} role mappings")
    print(f"  ✅ function rotation stages and target shares are valid")
    print(f"  ✅ economic alignment covers {len(economic_role_ids)} atomic roles across {len(economic_system_ids)} systems")
    print("Result: OK")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    from _env import skip_without_knowledge

    skip_without_knowledge("role_coverage_guard.py")
    raise SystemExit(check())
