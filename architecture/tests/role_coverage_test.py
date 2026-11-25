#!/usr/bin/env python3
"""Smoke test for role/process/template coverage."""

from __future__ import annotations

import subprocess
import sys
import copy
from pathlib import Path

import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import skip_without_knowledge  # noqa: E402

skip_without_knowledge("role_coverage_test.py")  # needs knowledge-base; skipped in CI when absent

import role_coverage_guard as guard  # noqa: E402


def test_classifications() -> None:
    assert callable(getattr(guard, "validate_classifications", None)), (
        "role coverage guard must validate the independent classification registry"
    )
    functions = [
        "direction-strategy", "discovery-analysis", "design-architecture",
        "build-creation", "operations-execution", "growth-distribution",
        "governance-assurance",
    ]
    registry_path = "docs/registries/ROLE-CLASSIFICATIONS.yaml"
    axes = [
        ("function", "atomic-role", registry_path),
        ("process-group", "atomic-role", "docs/registries/ROLE-COVERAGE.yaml"),
        ("domain", "work-context", "projects/workspaces.yaml"),
        ("seniority", "explicit-context", registry_path),
        ("authority", "explicit-context", registry_path),
    ]
    fixture = {
        "version": 1, "kind": "role-classification-registry", "status": "current",
        "source_role_catalog": "docs/registries/ROLE-CATALOG.yaml",
        "policy": {
            "context_values_require_explicit_assignment": True,
            "classification_does_not_grant_authority": True,
        },
        "classification_types": [
            {"id": axis, "name": axis, "description": "Independent view",
             "assignment_scope": scope, "source_registry": source}
            for axis, scope, source in axes
        ],
        "functions": [
            {"id": function, "name": function, "description": "Work function"}
            for function in functions
        ],
        "role_functions": [
            {"role_id": "engineer", "functions": ["design-architecture", "build-creation"]},
            {"role_id": "reviewer", "functions": ["governance-assurance"]},
        ],
    }
    roles = {"engineer", "reviewer"}
    assert guard.validate_classifications(fixture, roles) == []

    invalid_cases = [
        ("unknown function", lambda d: d["role_functions"][0]["functions"].append("unknown")),
        ("duplicate function", lambda d: d["role_functions"][0]["functions"].append("design-architecture")),
        ("missing role", lambda d: d["role_functions"].pop()),
        ("unknown role", lambda d: d["role_functions"].append({"role_id": "unknown", "functions": [functions[0]]})),
        ("duplicate role", lambda d: d["role_functions"].append(copy.deepcopy(d["role_functions"][0]))),
        ("empty functions", lambda d: d["role_functions"][0].update(functions=[])),
        ("non-string function", lambda d: d["role_functions"][0].update(functions=[{}])),
        ("missing definition", lambda d: d["functions"].pop()),
        ("duplicate definition", lambda d: d["functions"].append(copy.deepcopy(d["functions"][0]))),
        ("missing axis", lambda d: d["classification_types"].pop()),
        ("duplicate axis", lambda d: d["classification_types"].append(copy.deepcopy(d["classification_types"][0]))),
        ("implicit authority", lambda d: d["policy"].update(classification_does_not_grant_authority=False)),
        ("implicit seniority", lambda d: d["policy"].update(context_values_require_explicit_assignment=False)),
        ("wrong catalog", lambda d: d.update(source_role_catalog="other.yaml")),
        ("wrong axis source", lambda d: d["classification_types"][2].update(source_registry="other.yaml")),
        ("wrong axis scope", lambda d: d["classification_types"][4].update(assignment_scope="atomic-role")),
    ]
    for label, mutate in invalid_cases:
        candidate = copy.deepcopy(fixture)
        mutate(candidate)
        assert guard.validate_classifications(candidate, roles), label
    for malformed in (None, [], "bad", {"version": 1}):
        assert guard.validate_classifications(malformed, roles), repr(malformed)

    catalog = yaml.safe_load((ROOT / "docs/registries/ROLE-CATALOG.yaml").read_text(encoding="utf-8"))
    actual = yaml.safe_load((ROOT / registry_path).read_text(encoding="utf-8"))
    assert guard.validate_classifications(actual, {role["id"] for role in catalog["roles"]}) == []
    print("Role classifications: valid multi-function mappings and invalid contracts checked")


def test_function_rotation() -> None:
    systems = {"now", "later"}
    shares = {f: 0 for f in guard.FUNCTION_IDS}
    shares.update({"build-creation": 60, "governance-assurance": 40})
    fixture = {
        "source_function_registry": "docs/registries/ROLE-CLASSIFICATIONS.yaml",
        "vertical_path": ["build-creation", "governance-assurance"],
        "stages": [{
            "id": "s1", "economic_system": "now", "focus": "governance-assurance",
            "exit_gate": "evidence", "target_share": shares,
        }],
        "active_stage": "s1",
        "stage_history": [],
    }
    assert guard.validate_function_rotation(fixture, systems) == []
    invalid = [
        lambda d: d["stages"][0].update(economic_system="unknown"),
        lambda d: d["stages"][0].update(focus="management"),
        lambda d: d["stages"][0].update(exit_gate=""),
        lambda d: d["stages"][0]["target_share"].update({"build-creation": 61}),
        lambda d: d["stages"][0]["target_share"].pop("growth-distribution"),
        lambda d: d["stages"].append(copy.deepcopy(d["stages"][0])),
        lambda d: d.update(vertical_path=["unknown"]),
        lambda d: d.update(active_stage="unknown"),
        lambda d: d.update(stage_history=[{"stage": "s1", "evidence": ["AGENTS.md"]}]),
        lambda d: d.update(stage_history=[{"stage": "s1", "evidence": []}]),
        lambda d: d.update(stage_history=[{"stage": "s1", "evidence": ["missing/evidence.md"]}]),
    ]
    for mutate in invalid:
        candidate = copy.deepcopy(fixture)
        mutate(candidate)
        assert guard.validate_function_rotation(candidate, systems), candidate
    actual = yaml.safe_load(guard.ROLE_ECONOMIC_ALIGNMENT.read_text(encoding="utf-8"))
    actual_systems = {system["id"] for system in actual["economic_systems"]}
    assert guard.validate_function_rotation(actual["function_rotation"], actual_systems) == []
    print("Function rotation: evidence-gated stages and target shares checked")


def main() -> int:
    test_classifications()
    test_function_rotation()
    script = TOOLS / "scripts" / "role_coverage_guard.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    print(result.stdout, end="")
    print(result.stderr, end="")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
