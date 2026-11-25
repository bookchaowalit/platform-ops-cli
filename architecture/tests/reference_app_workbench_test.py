#!/usr/bin/env python3
"""Offline contract tests for the three software reference-app blueprints."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))

from reference_app_workbench import (  # noqa: E402
    EXPECTED_REFERENCE_APP_IDS,
    WORKBENCH_PATH,
    compose_reference_plan,
    load_workbench,
    validate_workbench,
)
from software_program_plan import load_inputs, role_catalog_map, validate_registry  # noqa: E402
import reference_app_workbench as workbench_module  # noqa: E402


def check_checkout_portability() -> None:
    owned = "projects/product/engineering/book-dev/github/example/book-tool"
    with tempfile.TemporaryDirectory(prefix="reference-app-portability-") as directory:
        base = Path(directory)
        root = base / "architecture-docs"
        root.mkdir()
        with patch.object(workbench_module, "PROJECT_ROOT", root), patch.object(workbench_module, "SIBLINGS_ROOT", base):
            # Sibling absent: skipped with a warning, an error when checkouts are required.
            assert workbench_module.implementation_path_errors(owned, require_checkouts=True)
            with patch.dict("os.environ", {"SOLO_REQUIRE_SIBLINGS": "1"}):
                assert workbench_module.implementation_path_errors(owned)
            with patch.dict("os.environ", {"SOLO_REQUIRE_SIBLINGS": "0"}):
                warnings: list[str] = []
                assert not workbench_module.implementation_path_errors(owned, warnings=warnings)
                assert len(warnings) == 1 and "SKIP" in warnings[0]
                for invalid in ("../outside", "/absolute", owned + "/../escape", "docs/missing", "C:\\outside", owned + "//child"):
                    assert workbench_module.implementation_path_errors(invalid), invalid
                (base / "book-tool").mkdir()
                warnings.clear()
                assert not workbench_module.implementation_path_errors(owned, require_checkouts=True, warnings=warnings)
                assert not warnings
                (base / "book-tool").rmdir()
                # Capability-dependent cases run after all portable assertions.
                (root / "docs").mkdir()
                try:
                    (root / "docs/outside-link").symlink_to(root.parent, target_is_directory=True)
                    (root / owned).parent.mkdir(parents=True)
                    (root / owned).symlink_to(root / "missing-target", target_is_directory=True)
                except OSError:
                    print("SKIP: host does not permit symlink creation (reference path cases)")
                else:
                    assert workbench_module.implementation_path_errors("docs/outside-link")


def main() -> int:
    check_checkout_portability()
    taxonomy, archetypes, role_catalog, workflow = load_inputs()
    errors = validate_registry(taxonomy, archetypes, role_catalog, workflow)
    assert not errors, errors

    workbench = load_workbench()
    errors = validate_workbench(workbench, taxonomy, archetypes, role_catalog)
    assert not errors, errors

    apps = workbench["reference_apps"]
    assert len(apps) == 3
    assert {app["id"] for app in apps} == EXPECTED_REFERENCE_APP_IDS
    assert {app["program_id"] for app in apps} == {
        "programming-language-tool",
        "api-tool",
        "ai-assistant-copilot",
    }
    assert all(len(app["role_focus"]) > 0 for app in apps)
    assert all(len(app["step_focus"]) == 14 for app in apps)
    assert all(len(app["acceptance_criteria"]) >= 4 for app in apps)

    roles = role_catalog_map(role_catalog)
    expected_package_counts = {
        "reference-developer-tool": 13 * 14,
        "reference-data-api": 15 * 14,
        "reference-ai-assistant": 16 * 14,
    }
    for app in apps:
        plan = compose_reference_plan(app, taxonomy, archetypes, roles)
        assert plan["status"] == "blueprint-only"
        if app["id"] in {
            "reference-developer-tool",
            "reference-data-api",
            "reference-ai-assistant",
        }:
            assert plan["reference_app"]["status"] == "implemented-local"
            assert plan["reference_app"]["implementation"]["status"] == "baseline-implemented"
        else:
            assert plan["reference_app"]["status"] == "blueprint-ready"
        assert len(plan["parent_steps"]) == 14
        assert len(plan["role_work_packages"]) == expected_package_counts[app["id"]]
        assert all("reference_app_focus" in package for package in plan["role_work_packages"])
        assert all(package["reference_app_focus"]["role"]["purpose"].strip() for package in plan["role_work_packages"])
        assert all(package["reference_app_focus"]["step"]["done_when"].strip() for package in plan["role_work_packages"])

    cli = TOOLS / "scripts" / "reference_app_workbench.py"
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
    assert payload["reference_app_count"] == 3

    result = subprocess.run(
        [sys.executable, str(cli), "--app", "reference-ai-assistant", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["reference_app"]["id"] == "reference-ai-assistant"
    assert payload["program"]["id"] == "ai-assistant-copilot"
    assert payload["summary"]["reference_step_focus_count"] == 14

    print(f"reference_app_workbench_test: ok ({WORKBENCH_PATH.name}, {len(apps)} apps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
