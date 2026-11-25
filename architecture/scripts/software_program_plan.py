#!/usr/bin/env python3
"""Validate and compose role-aware blueprints for the 160 software types."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - repository toolchain normally provides PyYAML
    print("FAIL: PyYAML is required (import yaml)")
    raise SystemExit(1)

from _env import PROJECT_ROOT  # noqa: E402


TAXONOMY_PATH = PROJECT_ROOT / "systems" / "architecture" / "software-program-taxonomy.yaml"
ARCHETYPE_PATH = PROJECT_ROOT / "systems" / "architecture" / "software-program-archetypes.yaml"
ROLE_CATALOG_PATH = PROJECT_ROOT / "docs" / "registries" / "ROLE-CATALOG.yaml"
WORKFLOW_PATH = PROJECT_ROOT / "systems" / "architecture" / "book-dev-domain-workflows.yaml"
EXPECTED_PROGRAM_COUNT = 160
EXPECTED_ARCHETYPE_COUNT = 13
EXPECTED_STEP_COUNT = 14
RISK_TIERS = {"learning", "standard", "high", "critical"}
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
QUALITY_DIMENSIONS = ("understanding", "execution", "quality", "repeatability", "transfer")


def relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def load_yaml(path: Path) -> Any:
    if not path.is_file():
        raise ValueError(f"missing required file: {relative(path)}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read {relative(path)}: {exc}") from exc
    return value


def non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def role_catalog_map(role_catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = role_catalog.get("roles") if isinstance(role_catalog, dict) else None
    if not isinstance(rows, list):
        return {}
    return {
        row["id"]: row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"].strip()
    }


def validate_registry(
    taxonomy: Any,
    archetype_doc: Any,
    role_catalog: Any,
    workflow_doc: Any,
) -> list[str]:
    """Return deterministic structural errors for the program registries."""

    errors: list[str] = []
    if not isinstance(taxonomy, dict):
        return ["taxonomy root must be a mapping"]
    if taxonomy.get("kind") != "software-program-taxonomy":
        errors.append("taxonomy kind must be software-program-taxonomy")
    if taxonomy.get("version") != 1:
        errors.append("taxonomy version must be 1")
    if taxonomy.get("program_count") != EXPECTED_PROGRAM_COUNT:
        errors.append(f"taxonomy program_count must be {EXPECTED_PROGRAM_COUNT}")

    programs = taxonomy.get("programs")
    if not isinstance(programs, list):
        return errors + ["taxonomy programs must be a list"]
    if len(programs) != EXPECTED_PROGRAM_COUNT:
        errors.append(f"taxonomy must contain {EXPECTED_PROGRAM_COUNT} rows, found {len(programs)}")

    number_map: dict[int, dict[str, Any]] = {}
    id_map: dict[str, dict[str, Any]] = {}
    required_program_fields = {"number", "id", "name", "archetype", "risk_tier", "interface", "platform", "purpose"}
    for index, program in enumerate(programs, 1):
        label = f"programs[{index}]"
        if not isinstance(program, dict):
            errors.append(f"{label} must be a mapping")
            continue
        missing = sorted(required_program_fields - set(program))
        if missing:
            errors.append(f"{label} missing fields: {missing}")
        number = program.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= EXPECTED_PROGRAM_COUNT:
            errors.append(f"{label} number must be between 1 and {EXPECTED_PROGRAM_COUNT}")
        elif number in number_map:
            errors.append(f"duplicate program number: {number}")
        else:
            number_map[number] = program
        program_id = program.get("id")
        if not isinstance(program_id, str) or not SLUG_PATTERN.fullmatch(program_id):
            errors.append(f"{label} id must be a lowercase kebab-case slug")
        elif program_id in id_map:
            errors.append(f"duplicate program id: {program_id}")
        else:
            id_map[program_id] = program
        for field in ("name", "archetype", "interface", "platform", "purpose"):
            if not non_empty(program.get(field)):
                errors.append(f"{label} {field} must be a non-empty string")
        if program.get("risk_tier") not in RISK_TIERS:
            errors.append(f"{label} risk_tier must be one of {sorted(RISK_TIERS)}")

    expected_numbers = set(range(1, EXPECTED_PROGRAM_COUNT + 1))
    if set(number_map) != expected_numbers:
        errors.append(
            "program number coverage mismatch: "
            f"missing={sorted(expected_numbers - set(number_map))} "
            f"extra={sorted(set(number_map) - expected_numbers)}"
        )

    if not isinstance(archetype_doc, dict):
        return errors + ["archetype registry root must be a mapping"]
    if archetype_doc.get("kind") != "software-program-archetype-registry":
        errors.append("archetype registry kind is invalid")
    if archetype_doc.get("version") != 1:
        errors.append("archetype registry version must be 1")

    steps = archetype_doc.get("steps")
    step_ids: set[str] = set()
    if not isinstance(steps, list):
        errors.append("archetype steps must be a list")
        steps = []
    if len(steps) != EXPECTED_STEP_COUNT:
        errors.append(f"archetype registry must contain {EXPECTED_STEP_COUNT} universal steps, found {len(steps)}")
    for index, step in enumerate(steps, 1):
        label = f"steps[{index}]"
        if not isinstance(step, dict):
            errors.append(f"{label} must be a mapping")
            continue
        for field in ("id", "title", "purpose", "lifecycle_stage_id", "next_step"):
            if not non_empty(step.get(field)):
                errors.append(f"{label} {field} must be a non-empty string")
        step_id = step.get("id")
        if isinstance(step_id, str):
            if step_id in step_ids:
                errors.append(f"duplicate universal step id: {step_id}")
            step_ids.add(step_id)
        evidence = as_string_list(step.get("evidence"))
        if not evidence:
            errors.append(f"{label} evidence must be a non-empty list")

    archetypes = archetype_doc.get("archetypes")
    if not isinstance(archetypes, dict):
        return errors + ["archetypes must be a mapping"]
    if len(archetypes) != EXPECTED_ARCHETYPE_COUNT:
        errors.append(f"archetype registry must contain {EXPECTED_ARCHETYPE_COUNT} archetypes, found {len(archetypes)}")

    roles = role_catalog_map(role_catalog if isinstance(role_catalog, dict) else {})
    workflow_packs = set()
    if isinstance(workflow_doc, dict) and isinstance(workflow_doc.get("capability_packs"), dict):
        workflow_packs = set(workflow_doc["capability_packs"])

    range_owners: dict[int, list[str]] = {}
    reference_ids: set[str] = set()
    for archetype_id, archetype in archetypes.items():
        label = f"archetypes.{archetype_id}"
        if not isinstance(archetype, dict):
            errors.append(f"{label} must be a mapping")
            continue
        for field in ("name", "purpose", "reference_program_id"):
            if not non_empty(archetype.get(field)):
                errors.append(f"{label} {field} must be a non-empty string")
        ranges = archetype.get("number_ranges")
        if not isinstance(ranges, list) or not ranges:
            errors.append(f"{label} number_ranges must be a non-empty list")
        else:
            for range_index, number_range in enumerate(ranges, 1):
                if (
                    not isinstance(number_range, list)
                    or len(number_range) != 2
                    or not all(isinstance(item, int) and not isinstance(item, bool) for item in number_range)
                    or number_range[0] > number_range[1]
                ):
                    errors.append(f"{label}.number_ranges[{range_index}] must be [start, end]")
                    continue
                for number in range(number_range[0], number_range[1] + 1):
                    range_owners.setdefault(number, []).append(archetype_id)

        packs = as_string_list(archetype.get("workflow_packs"))
        if not packs:
            errors.append(f"{label} workflow_packs must be non-empty")
        for pack in packs:
            if workflow_packs and pack not in workflow_packs:
                errors.append(f"{label} references unknown workflow pack: {pack}")

        required_roles = as_string_list(archetype.get("required_roles"))
        supporting_roles = as_string_list(archetype.get("supporting_roles"))
        if not required_roles:
            errors.append(f"{label} required_roles must be non-empty")
        overlap = sorted(set(required_roles) & set(supporting_roles))
        if overlap:
            errors.append(f"{label} role is both required and supporting: {overlap}")
        for role_id in required_roles + supporting_roles:
            if role_id not in roles:
                errors.append(f"{label} references unknown atomic role: {role_id}")

        reference_id = archetype.get("reference_program_id")
        if isinstance(reference_id, str):
            if reference_id in reference_ids:
                errors.append(f"duplicate reference program: {reference_id}")
            reference_ids.add(reference_id)
            program = id_map.get(reference_id)
            if program is None:
                errors.append(f"{label} reference_program_id does not resolve: {reference_id}")
            elif program.get("archetype") != archetype_id:
                errors.append(f"{label} reference program belongs to {program.get('archetype')}, not {archetype_id}")

        gaps = archetype.get("specialist_role_gaps", [])
        if not isinstance(gaps, list):
            errors.append(f"{label} specialist_role_gaps must be a list")
        else:
            for gap_index, gap in enumerate(gaps, 1):
                if not isinstance(gap, dict) or not non_empty(gap.get("id")) or not non_empty(gap.get("why")):
                    errors.append(f"{label}.specialist_role_gaps[{gap_index}] needs id and why")
                elif gap.get("status") != "proposed":
                    errors.append(f"{label}.specialist_role_gaps[{gap_index}] status must be proposed")

    for number in expected_numbers:
        owners = range_owners.get(number, [])
        if len(owners) != 1:
            errors.append(f"number {number} must belong to exactly one archetype, found {owners}")
        elif number in number_map and number_map[number].get("archetype") != owners[0]:
            errors.append(
                f"program {number} maps to {number_map[number].get('archetype')} but range owner is {owners[0]}"
            )

    role_templates = archetype_doc.get("role_templates")
    if not isinstance(role_templates, dict):
        errors.append("role_templates must be a mapping")
    else:
        required_template_roles = {
            role_id
            for archetype in archetypes.values()
            if isinstance(archetype, dict)
            for role_id in as_string_list(archetype.get("required_roles"))
        }
        missing_templates = sorted(required_template_roles - set(role_templates))
        if missing_templates:
            errors.append(f"missing role templates for required roles: {missing_templates}")
        for role_id, template in role_templates.items():
            if not isinstance(template, dict):
                errors.append(f"role_templates.{role_id} must be a mapping")
                continue
            for field in ("purpose", "action", "output", "gate"):
                if not non_empty(template.get(field)):
                    errors.append(f"role_templates.{role_id} {field} must be non-empty")

    return errors


def load_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    taxonomy = load_yaml(TAXONOMY_PATH)
    archetypes = load_yaml(ARCHETYPE_PATH)
    role_catalog = load_yaml(ROLE_CATALOG_PATH)
    workflow = load_yaml(WORKFLOW_PATH)
    return taxonomy, archetypes, role_catalog, workflow


def role_template(role_id: str, archetype_doc: dict[str, Any], roles: dict[str, dict[str, Any]]) -> dict[str, str]:
    declared = archetype_doc.get("role_templates", {}).get(role_id)
    if isinstance(declared, dict):
        return {field: str(declared[field]).strip() for field in ("purpose", "action", "output", "gate")}
    role = roles.get(role_id, {})
    name = role.get("name", role_id)
    summary = role.get("summary", f"รับผิดชอบงานของ {role_id}")
    return {
        "purpose": str(summary),
        "action": f"ตรวจและส่งมอบ contribution ของ {name} ตามขอบเขต step",
        "output": f"{name} work package",
        "gate": f"ผู้รับผิดชอบ {name} ทบทวนผลลัพธ์และหลักฐาน",
    }


def find_program(programs: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    normalized = query.strip().lower()
    for program in programs:
        if str(program.get("number")) == normalized or str(program.get("id", "")).lower() == normalized:
            return program
        if str(program.get("name", "")).lower() == normalized:
            return program
    return None


def summarize_program(program: dict[str, Any], archetype: dict[str, Any], step_count: int) -> dict[str, Any]:
    required_roles = as_string_list(archetype.get("required_roles"))
    supporting_roles = as_string_list(archetype.get("supporting_roles"))
    role_count = len(required_roles) + len(supporting_roles)
    gaps = archetype.get("specialist_role_gaps", [])
    gap_count = len(gaps) if isinstance(gaps, list) else 0
    return {
        "number": program["number"],
        "id": program["id"],
        "name": program["name"],
        "purpose": program["purpose"],
        "archetype": program["archetype"],
        "risk_tier": program["risk_tier"],
        "interface": program["interface"],
        "platform": program["platform"],
        "required_role_count": len(required_roles),
        "supporting_role_count": len(supporting_roles),
        "role_count": role_count,
        "role_work_package_count": role_count * step_count,
        "required_role_work_package_count": len(required_roles) * step_count,
        "specialist_role_gap_count": gap_count,
        "universal_step_count": step_count,
        "blueprint_status": "blueprint-only",
    }


def compose_program_plan(
    program: dict[str, Any],
    archetype_doc: dict[str, Any],
    roles: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    archetype = archetype_doc["archetypes"][program["archetype"]]
    steps = archetype_doc["steps"]
    required_roles = as_string_list(archetype.get("required_roles"))
    supporting_roles = as_string_list(archetype.get("supporting_roles"))
    role_rows = [
        {"role_id": role_id, "applicability": "required"}
        for role_id in required_roles
    ] + [
        {"role_id": role_id, "applicability": "supporting"}
        for role_id in supporting_roles
    ]

    role_work_packages: list[dict[str, Any]] = []
    for step_index, step in enumerate(steps):
        for role_row in role_rows:
            role_id = role_row["role_id"]
            template = role_template(role_id, archetype_doc, roles)
            previous = steps[step_index - 1]["id"] if step_index else None
            dependency = (
                f"program:{program['id']}:{previous}@{role_id}"
                if previous
                else f"program:{program['id']}:intake"
            )
            role_work_packages.append(
                {
                    "step_id": f"program:{program['id']}:{step['id']}@{role_id}",
                    "parent_step_id": f"program:{program['id']}:{step['id']}",
                    "role_id": role_id,
                    "applicability": role_row["applicability"],
                    "title": f"{step['title']} — {roles.get(role_id, {}).get('name', role_id)}",
                    "purpose": (
                        f"{template['purpose']} ในขั้น {step['title']} ของ {program['name']} "
                        f"เพื่อ {step['purpose']}"
                    ),
                    "entry_criteria": [
                        f"มีผลลัพธ์จาก {previous or 'program intake'} หรือมีเหตุผลที่บันทึกว่าขั้นนี้เริ่มได้",
                        f"role {role_id} ถูกประกาศเป็น {role_row['applicability']} ใน archetype นี้",
                    ],
                    "inputs": [
                        f"program brief: {program['id']}",
                        f"parent step: {step['id']}",
                        f"risk tier: {program['risk_tier']}",
                    ],
                    "actions": [
                        f"{template['action']} สำหรับ {program['name']}",
                        f"ผูก output กับ acceptance ของ parent step {step['id']}",
                    ],
                    "decisions": [
                        f"ยอมรับ ส่งกลับ หรือบันทึก exception สำหรับ {step['title']} ของ role {role_id}"
                    ],
                    "outputs": [f"{template['output']} สำหรับ {program['name']} ใน step {step['id']}"],
                    "completion_rule": (
                        f"{template['gate']} และ required evidence ของ parent step {step['id']} "
                        "ถูกผูกกับ artifact หรือ review record จริง"
                    ),
                    "quality_gate": (
                        f"{template['gate']}; ตรวจ controls ของ archetype "
                        f"({', '.join(archetype.get('controls', []))})"
                    ),
                    "required_evidence": list(step["evidence"]),
                    "evidence_refs": [],
                    "owner": role_id,
                    "dependencies": [dependency],
                    "risks": [
                        f"{program['risk_tier']} risk ของ {program['name']} ไม่ถูกตรวจใน step นี้",
                        f"role {role_id} ส่งมอบโดยไม่มีหลักฐานที่ resolve ได้",
                    ],
                    "rollback_target": (
                        f"กลับไป program:{program['id']}:{previous}"
                        if previous
                        else f"หยุดและทบทวน program:{program['id']}:intake"
                    ),
                    "next_step": step["next_step"],
                    "status": "upcoming",
                    "quality": {dimension: "unknown" for dimension in QUALITY_DIMENSIONS},
                }
            )

    gaps = archetype.get("specialist_role_gaps", [])
    specialist_gaps = list(gaps) if isinstance(gaps, list) else []
    summary = summarize_program(program, archetype, len(steps))
    summary["role_count"] = len(role_rows)
    summary["role_work_package_count"] = len(role_work_packages)
    summary["required_role_work_package_count"] = len(required_roles) * len(steps)
    return {
        "contract": "solo-empire.software-program-plan.v1",
        "status": "blueprint-only",
        "source_of_truth": {
            "program": relative(TAXONOMY_PATH),
            "archetype": relative(ARCHETYPE_PATH),
            "roles": relative(ROLE_CATALOG_PATH),
            "workflow_packs": relative(WORKFLOW_PATH),
        },
        "summary": summary,
        "program": program,
        "archetype": {
            "id": program["archetype"],
            "name": archetype["name"],
            "purpose": archetype["purpose"],
            "workflow_packs": archetype["workflow_packs"],
            "controls": archetype["controls"],
        },
        "role_set": {
            "required": required_roles,
            "supporting": supporting_roles,
            "specialist_role_gaps": specialist_gaps,
        },
        "parent_steps": [
            {
                "step_id": f"program:{program['id']}:{step['id']}",
                "title": step["title"],
                "purpose": step["purpose"],
                "lifecycle_stage_id": step["lifecycle_stage_id"],
                "required_evidence": list(step["evidence"]),
                "next_step": step["next_step"],
            }
            for step in steps
        ],
        "role_work_packages": role_work_packages,
        "completion_policy": (
            "This is an executable plan, not proof of completion. Record actual evidence "
            "with the universal step recorder only after the work and review occur."
        ),
    }


def check_result(errors: list[str]) -> dict[str, Any]:
    return {
        "contract": "solo-empire.software-program-plan.v1",
        "status": "ok" if not errors else "failed",
        "taxonomy": relative(TAXONOMY_PATH),
        "archetypes": relative(ARCHETYPE_PATH),
        "errors": errors,
    }


def emit(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    if isinstance(value, dict) and "errors" in value:
        print(f"Software program registry: {value['status'].upper()}")
        for error in value["errors"]:
            print(f"  FAIL: {error}")
        if not value["errors"]:
            print("  OK: taxonomy and archetype contracts are valid")
        return
    if isinstance(value, dict) and "summary" in value:
        summary = value["summary"]
        print(f"{summary['number']}. {summary['name']} ({summary['id']})")
        print(f"  Archetype: {summary['archetype']} | Risk: {summary['risk_tier']}")
        print(
            f"  Roles: {summary['role_count']} | Role packages: {summary['role_work_package_count']} "
            f"| Steps: {summary['universal_step_count']}"
        )
        print(f"  Status: {value['status']}")
        return
    if isinstance(value, list):
        for row in value:
            print(
                f"{row['number']:>3}  {row['id']:<42} {row['archetype']:<30} "
                f"roles={row['required_role_count']}+{row['supporting_role_count']}"
            )
        return
    print(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the taxonomy, archetypes, roles, and workflow packs")
    parser.add_argument("--all", action="store_true", help="list all 160 program blueprint summaries")
    parser.add_argument("--references", action="store_true", help="list the reference program for every archetype")
    parser.add_argument("--program", help="show the complete role-aware blueprint by number, id, or exact name")
    parser.add_argument("--archetype", help="list programs belonging to an archetype")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    try:
        taxonomy, archetype_doc, role_catalog, workflow_doc = load_inputs()
        errors = validate_registry(taxonomy, archetype_doc, role_catalog, workflow_doc)
    except (OSError, ValueError) as exc:
        errors = [str(exc)]
        taxonomy, archetype_doc, role_catalog, workflow_doc = {}, {}, {}, {}

    if errors:
        emit(check_result(errors), args.json)
        return 1

    programs = taxonomy["programs"]
    roles = role_catalog_map(role_catalog)
    archetypes = archetype_doc["archetypes"]

    if args.check or not any((args.all, args.references, args.program, args.archetype)):
        result = check_result([])
        result.update(
            {
                "program_count": len(programs),
                "archetype_count": len(archetypes),
                "universal_step_count": len(archetype_doc["steps"]),
                "atomic_role_count": len(roles),
            }
        )
        emit(result, args.json)
        return 0

    if args.program:
        program = find_program(programs, args.program)
        if program is None:
            emit(check_result([f"program does not resolve: {args.program}"]), args.json)
            return 1
        emit(compose_program_plan(program, archetype_doc, roles), args.json)
        return 0

    if args.archetype:
        archetype = archetypes.get(args.archetype)
        if archetype is None:
            emit(check_result([f"archetype does not resolve: {args.archetype}"]), args.json)
            return 1
        rows = [
            summarize_program(program, archetype, len(archetype_doc["steps"]))
            for program in programs
            if program["archetype"] == args.archetype
        ]
        emit(rows, args.json)
        return 0

    if args.references:
        reference_ids = {archetype["reference_program_id"] for archetype in archetypes.values()}
        rows = [
            summarize_program(program, archetypes[program["archetype"]], len(archetype_doc["steps"]))
            for program in programs
            if program["id"] in reference_ids
        ]
        emit(rows, args.json)
        return 0

    rows = [
        summarize_program(program, archetypes[program["archetype"]], len(archetype_doc["steps"]))
        for program in programs
    ]
    emit(rows, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
