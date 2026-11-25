#!/usr/bin/env python3
"""Offline product-delivery contract tests using isolated synthetic repositories."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))
import product_delivery_flow as flow  # noqa: E402

V1_INTERFACES = (
    "http-api", "mcp-local", "mcp-hosted", "cli", "web", "mobile-web",
    "native-mobile", "desktop-ide", "design-tools", "storybook", "client-review",
    "notifications",
)
V1_VIEW_FIELDS = (
    "workflow_id", "definition_version", "instance_revision", "project_ref", "feature_ref",
    "active_step_ids", "role_assignments", "step_state", "blockers", "tool_requirements",
    "tool_runs", "artifact_refs", "gate_state", "feedback_summary", "allowed_actions",
    "updated_at", "freshness", "capability_status",
)


class ProductDeliveryFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        lane_roles = {
            "product": ["product-manager"], "ba": ["business-analyst"],
            "sa": ["solution-architect"], "ux": ["ux-ui-designer"],
            "dev": ["frontend-engineer", "backend-engineer"],
            "qa": ["qa-test-engineer"], "release": ["devops-sre-platform"],
            "operate": ["devops-sre-platform"],
        }
        role_ids = sorted({role for roles in lane_roles.values() for role in roles})
        sources = {
            "roles": "docs/registries/ROLE-CATALOG.yaml",
            "lifecycle": "docs/lifecycle/DEVELOPMENT-LIFECYCLE.yaml",
            "role_work_packages": "systems/architecture/book-dev-role-step-matrix.yaml",
            "uiux_flow": "docs/lifecycle/UIUX-DESIGN-DELIVERY-FLOW.md",
        }
        self.write(sources["roles"], yaml.safe_dump({"roles": [
            {"id": role, "name": f"ชื่อบทบาท {role}"} for role in role_ids]}))
        self.write(sources["lifecycle"], yaml.safe_dump({"stages": [
            {"id": "discovery-and-requirements"}, {"id": "operate-and-improve"}]}))
        self.write(sources["role_work_packages"], yaml.safe_dump({"packs": {
            "engineering": {"stages": {"discover": {
                "parent_step_id": "book-dev:engineering:discover",
                "work_packages": {role: {"role_id": role} for role in role_ids},
            }}}}}))
        self.write(sources["uiux_flow"], "# UI/UX\n\n" + "\n".join(
            f"### S{i:02d} — ขั้น {i}" for i in range(1, 25)))
        self.write("templates/brief.md", "# Brief\n")
        self.data = {
            "contract": flow.CONTRACT, "version": 1, "status": "design-ready",
            "runtime_status": "not-integrated", "source_catalog": sources,
            "tools": {"editor": {"name": "เครื่องมือแก้ไข", "purpose": "จัดทำ artifact"}},
            "lanes": [{"id": lane, "name": f"งาน {lane}", "role_ids": roles,
                       "templates": ["templates/brief.md"]} for lane, roles in lane_roles.items()],
            "interfaces": [{"id": identifier, "name": identifier, "kind": "consumer",
                            "owner_repository": "ops-toolkit", "readiness": "projection-needed",
                            "view_fields": list(V1_VIEW_FIELDS)} for identifier in V1_INTERFACES],
            "feedback_routes": [{"id": "requirements", "owner_role": "business-analyst",
                                 "target_steps": ["STEP01"], "requires_new_baseline": True,
                                 "retest": "ทบทวน acceptance และทดสอบซ้ำ"}],
            "steps": [],
        }
        for lane, count in flow.LANE_COUNTS.items():
            for offset in range(count):
                number = len(self.data["steps"]) + 1
                owner = lane_roles[lane][offset % len(lane_roles[lane])]
                self.data["steps"].append({
                    "id": f"STEP{number:02d}", "lane": lane, "title": f"ขั้นตอน {number}",
                    "owner_role": owner, "reviewer_roles": ["solution-architect"],
                    "lifecycle_stage_id": "discovery-and-requirements",
                    "parent_role_package": f"book-dev:engineering:discover@{owner}",
                    "primary_tools": ["editor"], "supporting_tools": [],
                    "inputs": ["ข้อมูลนำเข้า"], "actions": ["จัดทำและตรวจ artifact"],
                    "outputs": ["artifact ที่ตรวจแล้ว"], "artifact_location": "owning-repo/docs/brief.md",
                    "acceptance": ["artifact มี owner และ evidence"],
                    "approval_required": True, "approval_authority": "product owner",
                    "depends_on": [f"STEP{number - 1:02d}"] if number > 1 else [],
                    "feedback_routes": ["requirements"], "client_output": "brief ที่ตรวจแล้ว",
                    "uiux_step_refs": ["S01"],
                })

    def write(self, relative: str | Path, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def errors(self, mutate) -> list[str]:
        candidate = copy.deepcopy(self.data)
        mutate(candidate)
        return flow.validate_definition(candidate, self.root)

    def run_cli(self, arguments: list[str], data: dict | None = None) -> tuple[int, str, str]:
        self.write(flow.DEFINITION, yaml.safe_dump(self.data if data is None else data, allow_unicode=True))
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = flow.main(arguments, root=self.root)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_valid_complete_definition_and_backward_feedback(self) -> None:
        self.assertEqual(72, len(self.data["steps"]))
        self.assertEqual([], flow.validate_definition(self.data, self.root))
        # Feedback from a later step to STEP01 must not create a dependency cycle.
        self.assertEqual(["requirements"], self.data["steps"][-1]["feedback_routes"])
        self.data["steps"][-1]["lifecycle_stage_id"] = "operate-and-improve"
        self.assertEqual([], flow.validate_definition(self.data, self.root))

    def test_required_fields_types_and_empty_values(self) -> None:
        for field in ("title", "owner_role", "reviewer_roles", "lifecycle_stage_id", "primary_tools",
                      "supporting_tools", "inputs", "actions", "outputs", "artifact_location",
                      "acceptance", "approval_required", "approval_authority", "depends_on",
                      "feedback_routes", "client_output", "uiux_step_refs"):
            with self.subTest(field=field):
                self.assertTrue(self.errors(lambda d: d["steps"][0].pop(field)))
        for field in ("actions", "outputs", "acceptance", "inputs"):
            with self.subTest(empty=field):
                self.assertTrue(self.errors(lambda d: d["steps"][0].update({field: []})))
        self.assertTrue(self.errors(lambda d: d["steps"][0].update(approval_required="false")))
        self.assertTrue(self.errors(lambda d: d.update(version=True)))

    def test_duplicate_entity_ids_and_duplicate_yaml_keys(self) -> None:
        for field in ("steps", "lanes", "interfaces", "feedback_routes"):
            with self.subTest(field=field):
                self.assertTrue(self.errors(lambda d: d[field].append(copy.deepcopy(d[field][0]))))
        path = self.root / "duplicate.yaml"
        path.write_text("version: 1\nversion: 2\n", encoding="utf-8")
        with self.assertRaises(flow.DefinitionError):
            flow.read_yaml(path)

    def test_unknown_references(self) -> None:
        fields = {"owner_role": "unknown", "reviewer_roles": ["unknown"],
                  "primary_tools": ["unknown"], "supporting_tools": ["unknown"],
                  "depends_on": ["unknown"], "lifecycle_stage_id": "unknown",
                  "parent_role_package": "book-dev:engineering:discover@unknown",
                  "uiux_step_refs": ["S25"]}
        for field, value in fields.items():
            with self.subTest(field=field):
                self.assertTrue(self.errors(lambda d: d["steps"][0].update({field: value})))

    def test_uiux_references_require_declared_source_headings(self) -> None:
        wrong_source = self.errors(lambda d: d["source_catalog"].update(uiux_flow="templates/brief.md"))
        self.assertTrue(any("uiux" in error for error in wrong_source))
        source = self.data["source_catalog"]["uiux_flow"]
        original = (self.root / source).read_text(encoding="utf-8")
        missing_heading = original.replace("### S01 — ขั้น 1", "S01 — mention only")
        for suffix in ("", "\nS01..S24 เป็นคำกล่าวถึง\n",
                       "\n```markdown\n### S01 — ตัวอย่างใน code fence\n```\n"):
            with self.subTest(suffix=suffix):
                self.write(source, missing_heading + suffix)
                errors = flow.validate_definition(self.data, self.root)
                self.assertTrue(any("uiux_step_refs" in error and "S01" in error for error in errors))

    def test_dependency_cycle_and_self_link(self) -> None:
        self.assertTrue(self.errors(lambda d: d["steps"][0].update(depends_on=["STEP02"])))
        self.assertTrue(self.errors(lambda d: d["steps"][0].update(depends_on=["STEP01"])))

    def test_invalid_feedback_route(self) -> None:
        self.assertTrue(self.errors(lambda d: d["steps"][0].update(feedback_routes=["unknown"])))
        for field, value in (("target_steps", ["unknown"]), ("owner_role", "unknown"),
                             ("retest", ""), ("requires_new_baseline", "true")):
            with self.subTest(field=field):
                self.assertTrue(self.errors(lambda d: d["feedback_routes"][0].update({field: value})))

    def test_interfaces_require_canonical_consumers_and_shared_fields(self) -> None:
        for identifier in V1_INTERFACES:
            with self.subTest(interface=identifier):
                errors = self.errors(lambda d: d.update(
                    interfaces=[item for item in d["interfaces"] if item["id"] != identifier]))
                self.assertTrue(any(identifier in error for error in errors))
        for field in V1_VIEW_FIELDS:
            with self.subTest(field=field):
                errors = self.errors(lambda d: d["interfaces"][0]["view_fields"].remove(field))
                self.assertTrue(any(field in error for error in errors))
        self.assertTrue(self.errors(lambda d: d["interfaces"][0].update(view_fields=["bogus"])))
        extra = copy.deepcopy(self.data)
        extra["interfaces"].append({**copy.deepcopy(extra["interfaces"][0]), "id": "extra-consumer"})
        self.assertEqual([], flow.validate_definition(extra, self.root))

    def test_lane_role_template_and_package_coverage(self) -> None:
        self.assertTrue(self.errors(lambda d: d["steps"].pop()))
        self.assertTrue(self.errors(lambda d: d["lanes"].pop()))
        self.assertTrue(self.errors(lambda d: d["lanes"][0].update(templates=["missing.md"])))
        self.assertTrue(self.errors(lambda d: d["lanes"][0].update(templates=["../outside.md"])))
        self.assertTrue(self.errors(lambda d: d["steps"][0].update(
            parent_role_package="book-dev:engineering:discover@business-analyst")))
        self.assertTrue(self.errors(lambda d: d["steps"][0].update(owner_role="business-analyst")))
        self.assertTrue(self.errors(lambda d: d["steps"][0].update(status="done")))

    def test_generated_drift_and_explicit_write(self) -> None:
        self.assertTrue(flow.check_generated(self.data, self.root))
        result, _, stderr = self.run_cli(["--write"])
        self.assertEqual(0, result, stderr)
        self.assertEqual([], flow.check_generated(self.data, self.root))
        result, _, stderr = self.run_cli([])
        self.assertEqual(0, result, stderr)
        self.write(flow.GENERATED, (self.root / flow.GENERATED).read_text() + "stale\n")
        result, _, stderr = self.run_cli(["--check"])
        self.assertEqual(1, result)
        self.assertIn(str(flow.GENERATED), stderr)

    def test_json_and_role_filter_contract(self) -> None:
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        result, stdout, stderr = self.run_cli(["--json"])
        self.assertEqual(0, result, stderr)
        self.assertEqual(self.data, json.loads(stdout))
        result, stdout, stderr = self.run_cli(["--json", "--role", "business-analyst"])
        self.assertEqual(0, result, stderr)
        view = json.loads(stdout)
        self.assertEqual(flow.CONTRACT, view["contract"])
        self.assertEqual("partial", view["view"]["scope"])
        self.assertEqual("full-definition", view["view"]["dependency_scope"])
        self.assertEqual(10, len(view["steps"]))
        self.assertTrue(all(step["owner_role"] == "business-analyst" for step in view["steps"]))
        self.assertEqual(self.data["feedback_routes"], view["feedback_routes"])
        reviewer_view = flow.role_view(self.data, "solution-architect")
        self.assertEqual(72, len(reviewer_view["steps"]))
        self.assertFalse((self.root / flow.GENERATED).exists())
        after = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        added = set(after) - set(before)
        self.assertEqual({str(flow.DEFINITION)}, added)
        result, _, stderr = self.run_cli(["--json", "--role", "unknown"])
        self.assertEqual(1, result)
        self.assertIn("--role", stderr)

    def test_json_export_rejects_yaml_only_and_nonfinite_metadata(self) -> None:
        documents = (
            "description: 2026-10-13\n",
            "metadata: !!set {member: null}\n",
            "metadata: {score: .nan}\n",
            "metadata: {score: .inf}\n",
            "metadata: {score: -.inf}\n",
            "metadata: {7: value}\n",
            "metadata: !!binary aGVsbG8=\n",
        )
        for document in documents:
            with self.subTest(yaml=document.strip()):
                candidate = {**copy.deepcopy(self.data), **yaml.safe_load(document)}
                self.assertTrue(flow.validate_definition(candidate, self.root))
                result, stdout, stderr = self.run_cli(["--json"], candidate)
                self.assertEqual(1, result)
                self.assertEqual("", stdout)
                self.assertIn("JSON", stderr)
                self.assertNotIn("Traceback", stderr)

    def test_json_compatible_metadata_roundtrips(self) -> None:
        self.data["metadata"] = {"date": "2026-10-13", "finite": 1.25,
                                 "values": [None, False, 7, {"name": "note"}]}
        result, stdout, stderr = self.run_cli(["--json"])
        self.assertEqual(0, result, stderr)

        def reject_constant(value: str) -> None:
            raise ValueError(value)

        self.assertEqual(self.data, json.loads(stdout, parse_constant=reject_constant))

    def test_json_serialization_failure_returns_clean_error(self) -> None:
        with mock.patch.object(flow.json, "dumps", side_effect=TypeError("unsupported value")):
            result, stdout, stderr = self.run_cli(["--json"])
        self.assertEqual(1, result)
        self.assertEqual("", stdout)
        self.assertIn("JSON", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_markdown_includes_outputs_gate_tools_feedback_and_sources(self) -> None:
        result, stdout, stderr = self.run_cli(["--markdown", "--role", "business-analyst"])
        self.assertEqual(0, result, stderr)
        self.assertEqual(10, stdout.count("\n### STEP"))
        self.assertNotIn("### STEP01 —", stdout)
        for expected in ("ข้อมูลนำเข้า", "จัดทำและตรวจ artifact", "artifact ที่ตรวจแล้ว",
                         "owning-repo/docs/brief.md", "artifact มี owner และ evidence",
                         "product owner", "เครื่องมือแก้ไข", "ทบทวน acceptance และทดสอบซ้ำ",
                         "brief ที่ตรวจแล้ว", "S01", "not-integrated"):
            self.assertIn(expected, stdout)
        full = flow.render_markdown(self.data, self.root)
        self.assertEqual(72, full.count("\n### STEP"))
        metadata = yaml.safe_load(full.split("---", 2)[1])
        self.assertTrue(metadata["generated"])
        self.assertEqual("generated", metadata["status"])
        self.assertEqual("docs/lifecycle/README.md", metadata["parent"])
        self.assertEqual(full, flow.render_markdown(self.data, self.root))

    def test_schema_is_checked_before_drift_and_filter_cannot_write(self) -> None:
        candidate = copy.deepcopy(self.data)
        candidate["steps"][0]["actions"] = []
        result, _, stderr = self.run_cli(["--check"], candidate)
        self.assertEqual(1, result)
        self.assertIn("actions", stderr)
        self.assertNotIn(str(flow.GENERATED), stderr)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            flow.main(["--write", "--role", "business-analyst"], root=self.root)
        self.assertEqual(2, raised.exception.code)
        self.assertFalse((self.root / flow.GENERATED).exists())

    def test_malformed_containers_report_diagnostics(self) -> None:
        for field in ("source_catalog", "tools", "lanes", "interfaces", "feedback_routes", "steps"):
            with self.subTest(field=field):
                self.assertTrue(self.errors(lambda d: d.update({field: None})))
        self.assertTrue(flow.validate_definition(None, self.root))

    def test_write_cannot_follow_generated_symlink(self) -> None:
        outside = self.root / "preserved.md"
        outside.write_text("preserve this artifact\n", encoding="utf-8")
        (self.root / flow.GENERATED).symlink_to(outside)
        result, _, stderr = self.run_cli(["--write"])
        self.assertEqual(1, result)
        self.assertIn("symlink", stderr)
        self.assertEqual("preserve this artifact\n", outside.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
