#!/usr/bin/env python3
"""Offline contract tests for the 160 software program blueprint composer."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))

from software_program_plan import (  # noqa: E402
    ARCHETYPE_PATH,
    TAXONOMY_PATH,
    compose_program_plan,
    load_inputs,
    role_catalog_map,
    validate_registry,
)


def main() -> int:
    taxonomy, archetypes, role_catalog, workflow = load_inputs()
    errors = validate_registry(taxonomy, archetypes, role_catalog, workflow)
    assert not errors, errors

    programs = taxonomy["programs"]
    assert len(programs) == 160
    assert {row["number"] for row in programs} == set(range(1, 161))
    assert len({row["id"] for row in programs}) == 160
    assert len(archetypes["archetypes"]) == 13
    assert len(archetypes["steps"]) == 14

    roles = role_catalog_map(role_catalog)
    erp = next(row for row in programs if row["id"] == "erp")
    erp_plan = compose_program_plan(erp, archetypes, roles)
    assert erp_plan["status"] == "blueprint-only"
    assert erp_plan["summary"]["role_count"] == 21
    assert erp_plan["summary"]["role_work_package_count"] == 14 * 21
    assert erp_plan["summary"]["required_role_work_package_count"] == 14 * 13
    assert len(erp_plan["parent_steps"]) == 14
    assert len(erp_plan["role_work_packages"]) == 14 * 21
    assert all(package["purpose"].strip() for package in erp_plan["role_work_packages"])
    assert all(package["required_evidence"] for package in erp_plan["role_work_packages"])
    assert all(package["status"] == "upcoming" for package in erp_plan["role_work_packages"])
    assert all(all(value == "unknown" for value in package["quality"].values()) for package in erp_plan["role_work_packages"])
    assert "finance-accounting" in erp_plan["role_set"]["required"]
    assert "legal-compliance" in erp_plan["role_set"]["required"]
    assert erp_plan["role_set"]["specialist_role_gaps"]

    industrial = next(row for row in programs if row["id"] == "industrial-control-software")
    industrial_plan = compose_program_plan(industrial, archetypes, roles)
    assert industrial_plan["summary"]["risk_tier"] == "critical"
    assert any(gap["id"] == "ot-control-engineer" for gap in industrial_plan["role_set"]["specialist_role_gaps"])

    cli = TOOLS / "scripts" / "software_program_plan.py"
    result = subprocess.run(
        [sys.executable, str(cli), "--check", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["program_count"] == 160
    assert payload["archetype_count"] == 13

    print(f"software_program_plan_test: ok ({TAXONOMY_PATH.name}, {ARCHETYPE_PATH.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
