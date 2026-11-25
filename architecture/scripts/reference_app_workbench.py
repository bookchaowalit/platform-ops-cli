#!/usr/bin/env python3
"""Validate and compose the first three software reference-app blueprints.

SOLO_EMPIRE_CI=1 validates registered metadata when ignored product checkouts
are absent. Local validation and --require-checkouts require their directories.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from _env import PROJECT_ROOT, SIBLINGS_ROOT  # noqa: E402
from software_program_plan import (  # noqa: E402
    compose_program_plan,
    find_program,
    load_inputs,
    load_yaml,
    relative,
    role_catalog_map,
    validate_registry,
)


WORKBENCH_PATH = PROJECT_ROOT / "systems" / "architecture" / "reference-app-workbench.yaml"
EXPECTED_REFERENCE_APP_COUNT = 3
EXPECTED_REFERENCE_APP_IDS = {
    "reference-developer-tool",
    "reference-data-api",
    "reference-ai-assistant",
}
ALLOWED_APP_STATUSES = {"blueprint-ready", "implemented-local"}
REQUIRED_APP_FIELDS = {
    "id",
    "name",
    "status",
    "program_id",
    "archetype_id",
    "owner_role",
    "user",
    "interface",
    "platform",
    "delivery_model",
    "purpose",
    "delivery_boundary",
    "scope",
    "vertical_slice",
    "acceptance_criteria",
    "role_focus",
    "step_focus",
    "risk_controls",
    "next_action",
}
REQUIRED_FOCUS_FIELDS = {"purpose", "deliverable", "quality_gate"}
REQUIRED_STEP_FIELDS = {"purpose", "done_when", "evidence"}


def non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def implementation_path_errors(
    local_path: str, *, require_checkouts: bool = False, warnings: list[str] | None = None,
) -> list[str]:
    path = Path(local_path)
    if (
        not local_path.strip() or any(ord(char) < 32 for char in local_path)
        or local_path != path.as_posix() or "\\" in local_path or ":" in local_path
        or path.is_absolute() or ".." in path.parts
        or not (PROJECT_ROOT / path).resolve().is_relative_to(PROJECT_ROOT.resolve())
    ):
        return [f"implementation local_path must be a contained repository-relative path: {local_path}"]
    if (PROJECT_ROOT / path).is_dir():
        return []
    # Implementation repositories are sibling checkouts (../<repo>); the recorded
    # local_path keeps the original Book Dev layout; the checkout part is mapped to ../<repo>.
    if path.parts[:4] == ("projects", "product", "engineering", "book-dev") and len(path.parts) > 4:
        # book-dev/github/<owner>/<private|public>/<repo>/<subpath>; shorter paths end at the repo.
        sibling = SIBLINGS_ROOT / Path(*path.parts[7:]) if len(path.parts) > 7 else SIBLINGS_ROOT / path.name
        if sibling.is_dir():
            return []
        if not require_checkouts and os.environ.get("SOLO_REQUIRE_SIBLINGS") != "1":
            if warnings is not None:
                warnings.append(f"SKIP: sibling checkout not present ({sibling}); not verified: {local_path}")
            return []
    return [f"implementation local_path does not resolve locally: {local_path}"]


def validate_workbench(
    workbench: Any,
    taxonomy: dict[str, Any],
    archetype_doc: dict[str, Any],
    role_catalog: dict[str, Any],
    *, require_checkouts: bool = False, warnings: list[str] | None = None,
) -> list[str]:
    """Return deterministic structural errors for the reference-app registry."""

    errors: list[str] = []
    if not isinstance(workbench, dict):
        return ["reference workbench root must be a mapping"]
    if workbench.get("kind") != "software-reference-app-workbench":
        errors.append("reference workbench kind is invalid")
    if workbench.get("version") != 1:
        errors.append("reference workbench version must be 1")
    if workbench.get("status") != "current":
        errors.append("reference workbench status must be current")
    for field in ("description", "factory_source", "program_plan_contract"):
        if not non_empty(workbench.get(field)):
            errors.append(f"reference workbench {field} must be non-empty")

    boundary = workbench.get("implementation_boundary")
    if not isinstance(boundary, dict):
        errors.append("implementation_boundary must be a mapping")
    else:
        for field in ("repository_policy", "repository_status", "runtime_status"):
            if not non_empty(boundary.get(field)):
                errors.append(f"implementation_boundary {field} must be non-empty")
        for field in ("allowed_in_this_slice", "blocked_in_this_slice"):
            if not as_string_list(boundary.get(field)):
                errors.append(f"implementation_boundary {field} must be a non-empty list")

    programs = taxonomy.get("programs") if isinstance(taxonomy, dict) else []
    programs = programs if isinstance(programs, list) else []
    program_map = {
        row.get("id"): row
        for row in programs
        if isinstance(row, dict) and non_empty(row.get("id"))
    }
    archetypes = archetype_doc.get("archetypes") if isinstance(archetype_doc, dict) else {}
    archetypes = archetypes if isinstance(archetypes, dict) else {}
    roles = role_catalog_map(role_catalog)
    steps = archetype_doc.get("steps") if isinstance(archetype_doc, dict) else []
    step_ids = {
        step.get("id")
        for step in steps
        if isinstance(step, dict) and non_empty(step.get("id"))
    }

    apps = workbench.get("reference_apps")
    if not isinstance(apps, list):
        return errors + ["reference_apps must be a list"]
    if len(apps) != EXPECTED_REFERENCE_APP_COUNT:
        errors.append(
            f"reference workbench must contain {EXPECTED_REFERENCE_APP_COUNT} apps, found {len(apps)}"
        )

    app_ids: set[str] = set()
    program_ids: set[str] = set()
    for index, app in enumerate(apps, 1):
        label = f"reference_apps[{index}]"
        if not isinstance(app, dict):
            errors.append(f"{label} must be a mapping")
            continue

        missing = sorted(REQUIRED_APP_FIELDS - set(app))
        if missing:
            errors.append(f"{label} missing fields: {missing}")
        app_id = app.get("id")
        if not non_empty(app_id):
            errors.append(f"{label} id must be non-empty")
        elif app_id in app_ids:
            errors.append(f"duplicate reference app id: {app_id}")
        else:
            app_ids.add(app_id)
        for field in (
            "name",
            "status",
            "program_id",
            "archetype_id",
            "owner_role",
            "user",
            "interface",
            "platform",
            "delivery_model",
            "purpose",
            "delivery_boundary",
            "next_action",
        ):
            if not non_empty(app.get(field)):
                errors.append(f"{label} {field} must be non-empty")
        if app.get("status") not in ALLOWED_APP_STATUSES:
            errors.append(f"{label} status must be one of {sorted(ALLOWED_APP_STATUSES)}")

        program_id = app.get("program_id")
        if isinstance(program_id, str):
            if program_id in program_ids:
                errors.append(f"duplicate reference program in workbench: {program_id}")
            program_ids.add(program_id)
        program = program_map.get(program_id)
        if program is None:
            errors.append(f"{label} program_id does not resolve: {program_id}")
        elif app.get("archetype_id") != program.get("archetype"):
            errors.append(
                f"{label} archetype_id {app.get('archetype_id')} does not match program archetype {program.get('archetype')}"
            )

        archetype = archetypes.get(app.get("archetype_id"))
        if not isinstance(archetype, dict):
            errors.append(f"{label} archetype_id does not resolve: {app.get('archetype_id')}")
            archetype_roles: set[str] = set()
        else:
            archetype_roles = set(as_string_list(archetype.get("required_roles"))) | set(
                as_string_list(archetype.get("supporting_roles"))
            )
        if app.get("owner_role") not in archetype_roles:
            errors.append(f"{label} owner_role is not in the program role set: {app.get('owner_role')}")

        scope = app.get("scope")
        if not isinstance(scope, dict):
            errors.append(f"{label}.scope must be a mapping")
        else:
            in_scope = as_string_list(scope.get("in_scope"))
            out_scope = as_string_list(scope.get("out_of_scope"))
            if not in_scope:
                errors.append(f"{label}.scope.in_scope must be a non-empty list")
            if not out_scope:
                errors.append(f"{label}.scope.out_of_scope must be a non-empty list")
            if set(in_scope) & set(out_scope):
                errors.append(f"{label}.scope has overlapping in/out items")

        vertical_slice = app.get("vertical_slice")
        if not isinstance(vertical_slice, dict):
            errors.append(f"{label}.vertical_slice must be a mapping")
        else:
            for field in ("id", "purpose"):
                if not non_empty(vertical_slice.get(field)):
                    errors.append(f"{label}.vertical_slice {field} must be non-empty")
            if not as_string_list(vertical_slice.get("flow")):
                errors.append(f"{label}.vertical_slice flow must be a non-empty list")
            if not as_string_list(vertical_slice.get("acceptance_criteria")):
                errors.append(f"{label}.vertical_slice acceptance_criteria must be a non-empty list")

        acceptance = app.get("acceptance_criteria")
        if not isinstance(acceptance, list) or not acceptance:
            errors.append(f"{label}.acceptance_criteria must be a non-empty list")
        else:
            acceptance_ids: set[str] = set()
            for criteria_index, criterion in enumerate(acceptance, 1):
                criteria_label = f"{label}.acceptance_criteria[{criteria_index}]"
                if not isinstance(criterion, dict):
                    errors.append(f"{criteria_label} must be a mapping")
                    continue
                for field in ("id", "statement"):
                    if not non_empty(criterion.get(field)):
                        errors.append(f"{criteria_label} {field} must be non-empty")
                criterion_id = criterion.get("id")
                if isinstance(criterion_id, str):
                    if criterion_id in acceptance_ids:
                        errors.append(f"{label} duplicate acceptance criterion: {criterion_id}")
                    acceptance_ids.add(criterion_id)
                if not as_string_list(criterion.get("evidence")):
                    errors.append(f"{criteria_label} evidence must be a non-empty list")

        role_focus = app.get("role_focus")
        if not isinstance(role_focus, dict):
            errors.append(f"{label}.role_focus must be a mapping")
        else:
            if set(role_focus) != archetype_roles:
                errors.append(
                    f"{label}.role_focus must cover exactly the program role set; "
                    f"missing={sorted(archetype_roles - set(role_focus))} "
                    f"extra={sorted(set(role_focus) - archetype_roles)}"
                )
            for role_id, focus in role_focus.items():
                focus_label = f"{label}.role_focus.{role_id}"
                if role_id not in roles:
                    errors.append(f"{focus_label} is not an atomic role")
                if not isinstance(focus, dict):
                    errors.append(f"{focus_label} must be a mapping")
                    continue
                missing_focus = sorted(REQUIRED_FOCUS_FIELDS - set(focus))
                if missing_focus:
                    errors.append(f"{focus_label} missing fields: {missing_focus}")
                for field in REQUIRED_FOCUS_FIELDS:
                    if not non_empty(focus.get(field)):
                        errors.append(f"{focus_label} {field} must be non-empty")

        step_focus = app.get("step_focus")
        if not isinstance(step_focus, dict):
            errors.append(f"{label}.step_focus must be a mapping")
        else:
            if set(step_focus) != step_ids:
                errors.append(
                    f"{label}.step_focus must cover exactly the universal steps; "
                    f"missing={sorted(step_ids - set(step_focus))} "
                    f"extra={sorted(set(step_focus) - step_ids)}"
                )
            for step_id, focus in step_focus.items():
                focus_label = f"{label}.step_focus.{step_id}"
                if not isinstance(focus, dict):
                    errors.append(f"{focus_label} must be a mapping")
                    continue
                missing_step = sorted(REQUIRED_STEP_FIELDS - set(focus))
                if missing_step:
                    errors.append(f"{focus_label} missing fields: {missing_step}")
                for field in ("purpose", "done_when"):
                    if not non_empty(focus.get(field)):
                        errors.append(f"{focus_label} {field} must be non-empty")
                if not as_string_list(focus.get("evidence")):
                    errors.append(f"{focus_label} evidence must be a non-empty list")

        if not as_string_list(app.get("risk_controls")):
            errors.append(f"{label}.risk_controls must be a non-empty list")

        implementation = app.get("implementation")
        if app.get("status") == "implemented-local":
            if not isinstance(implementation, dict):
                errors.append(f"{label}.implementation must be a mapping for implemented-local apps")
            else:
                for field in (
                    "status",
                    "local_path",
                    "repository_status",
                    "remaining_gate",
                    "evidence",
                ):
                    if not non_empty(implementation.get(field)):
                        errors.append(f"{label}.implementation {field} must be non-empty")
                if implementation.get("status") != "baseline-implemented":
                    errors.append(f"{label}.implementation status must be baseline-implemented")
                if not as_string_list(implementation.get("implemented_surface")):
                    errors.append(f"{label}.implementation implemented_surface must be a non-empty list")
                if not as_string_list(implementation.get("implemented_steps")):
                    errors.append(f"{label}.implementation implemented_steps must be a non-empty list")
                local_path = implementation.get("local_path")
                if isinstance(local_path, str):
                    errors.extend(f"{label}.{error}" for error in implementation_path_errors(
                        local_path, require_checkouts=require_checkouts, warnings=warnings,
                    ))

    if app_ids != EXPECTED_REFERENCE_APP_IDS:
        errors.append(
            "reference app id coverage mismatch: "
            f"missing={sorted(EXPECTED_REFERENCE_APP_IDS - app_ids)} "
            f"extra={sorted(app_ids - EXPECTED_REFERENCE_APP_IDS)}"
        )
    if len(program_ids) != EXPECTED_REFERENCE_APP_COUNT:
        errors.append("reference apps must map to three distinct programs")
    return errors


def load_workbench() -> dict[str, Any]:
    value = load_yaml(WORKBENCH_PATH)
    if not isinstance(value, dict):
        raise ValueError(f"reference workbench root must be a mapping: {relative(WORKBENCH_PATH)}")
    return value


def compose_reference_plan(
    app: dict[str, Any],
    taxonomy: dict[str, Any],
    archetype_doc: dict[str, Any],
    roles: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    programs = taxonomy["programs"]
    program = find_program(programs, app["program_id"])
    if program is None:
        raise ValueError(f"reference app program does not resolve: {app['program_id']}")
    plan = compose_program_plan(program, archetype_doc, roles)
    role_focus = app["role_focus"]
    step_focus = app["step_focus"]
    for package in plan["role_work_packages"]:
        step_id = package["parent_step_id"].rsplit(":", 1)[-1]
        role_id = package["role_id"]
        package["reference_app_focus"] = {
            "role": role_focus[role_id],
            "step": step_focus[step_id],
        }

    plan["reference_app"] = {
        "id": app["id"],
        "name": app["name"],
        "status": app["status"],
        "owner_role": app["owner_role"],
        "purpose": app["purpose"],
        "delivery_boundary": app["delivery_boundary"],
        "scope": app["scope"],
        "vertical_slice": app["vertical_slice"],
        "acceptance_criteria": app["acceptance_criteria"],
        "risk_controls": app["risk_controls"],
        "next_action": app["next_action"],
    }
    if isinstance(app.get("implementation"), dict):
        plan["reference_app"]["implementation"] = app["implementation"]
    plan["summary"].update(
        {
            "reference_app_id": app["id"],
            "reference_app_name": app["name"],
            "reference_app_status": app["status"],
            "reference_role_focus_count": len(role_focus),
            "reference_step_focus_count": len(step_focus),
            "acceptance_criteria_count": len(app["acceptance_criteria"]),
            "implementation_status": (
                app["implementation"]["status"]
                if isinstance(app.get("implementation"), dict)
                else "not-started"
            ),
        }
    )
    return plan


def summarize_reference_app(
    app: dict[str, Any],
    taxonomy: dict[str, Any],
    archetype_doc: dict[str, Any],
    roles: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    plan = compose_reference_plan(app, taxonomy, archetype_doc, roles)
    summary = dict(plan["summary"])
    summary["delivery_boundary"] = app["delivery_boundary"]
    summary["vertical_slice_id"] = app["vertical_slice"]["id"]
    summary["risk_controls"] = list(app["risk_controls"])
    return summary


def check_result(errors: list[str]) -> dict[str, Any]:
    return {
        "contract": "solo-empire.software-reference-app-workbench.v1",
        "status": "ok" if not errors else "failed",
        "workbench": relative(WORKBENCH_PATH),
        "errors": errors,
    }


def emit(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    if isinstance(value, dict) and "errors" in value:
        print(f"Reference-app workbench: {value['status'].upper()}")
        for error in value["errors"]:
            print(f"  FAIL: {error}")
        for warning in value.get("warnings", []):
            print(f"  {warning}")
        if not value["errors"]:
            print("  OK: three reference-app contracts are valid")
        return
    if isinstance(value, dict) and "summary" in value:
        summary = value["summary"]
        print(f"{summary['reference_app_name']} ({summary['reference_app_id']})")
        print(
            f"  Program: {summary['number']}. {summary['name']} | "
            f"Archetype: {summary['archetype']} | Risk: {summary['risk_tier']}"
        )
        print(
            f"  Roles: {summary['role_count']} | Role packages: {summary['role_work_package_count']} | "
            f"Steps: {summary['universal_step_count']}"
        )
        print(f"  Vertical slice: {summary['vertical_slice_id']} | Status: {summary['reference_app_status']}")
        return
    if isinstance(value, list):
        for row in value:
            print(
                f"{row['reference_app_id']:<28} program={row['number']:>3} "
                f"{row['id']:<30} roles={row['role_count']} "
                f"packages={row['role_work_package_count']}"
            )
        return
    print(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the reference-app workbench and source registries")
    parser.add_argument("--all", action="store_true", help="list all reference-app blueprint summaries")
    parser.add_argument("--app", help="show a complete reference-app blueprint by id or name")
    parser.add_argument("--program", help="show the reference-app blueprint for a program id or number")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--require-checkouts", action="store_true", help="require local runtime checkouts even in CI")
    args = parser.parse_args()
    warnings: list[str] = []

    try:
        taxonomy, archetypes, role_catalog, workflow = load_inputs()
        errors = validate_registry(taxonomy, archetypes, role_catalog, workflow)
        workbench = load_workbench()
        errors.extend(validate_workbench(workbench, taxonomy, archetypes, role_catalog,
                                         require_checkouts=args.require_checkouts, warnings=warnings))
    except (OSError, ValueError) as exc:
        errors = [str(exc)]
        taxonomy, archetypes, role_catalog, workbench = {}, {}, {}, {}

    if errors:
        emit(check_result(errors), args.json)
        return 1

    apps = workbench["reference_apps"]
    roles = role_catalog_map(role_catalog)
    if args.check or not any((args.all, args.app, args.program)):
        result = check_result([])
        result["warnings"] = warnings
        result.update(
            {
                "reference_app_count": len(apps),
                "reference_app_ids": [app["id"] for app in apps],
                "program_ids": [app["program_id"] for app in apps],
                "universal_step_count": len(archetypes["steps"]),
            }
        )
        emit(result, args.json)
        return 0

    selected_app: dict[str, Any] | None = None
    if args.app:
        normalized = args.app.strip().lower()
        selected_app = next(
            (
                app
                for app in apps
                if str(app.get("id", "")).lower() == normalized
                or str(app.get("name", "")).lower() == normalized
            ),
            None,
        )
        if selected_app is None:
            emit(check_result([f"reference app does not resolve: {args.app}"]), args.json)
            return 1
    elif args.program:
        normalized = args.program.strip().lower()
        selected_app = next(
            (
                app
                for app in apps
                if str(app.get("program_id", "")).lower() == normalized
                or str(app.get("number", "")).lower() == normalized
            ),
            None,
        )
        if selected_app is None:
            program = find_program(taxonomy["programs"], args.program)
            if program is not None:
                selected_app = next((app for app in apps if app["program_id"] == program["id"]), None)
        if selected_app is None:
            emit(check_result([f"reference app for program does not resolve: {args.program}"]), args.json)
            return 1

    if selected_app is not None:
        emit(compose_reference_plan(selected_app, taxonomy, archetypes, roles), args.json)
        return 0

    rows = [summarize_reference_app(app, taxonomy, archetypes, roles) for app in apps]
    emit(rows, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
