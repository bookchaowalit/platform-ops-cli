#!/usr/bin/env python3
"""Static refinement contract tests in isolated repositories, without live services."""

from __future__ import annotations

import contextlib
import copy
import datetime
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))
import delivery_refinement_flow as refinement  # noqa: E402
import product_delivery_flow_test as flow_tests  # noqa: E402


class DeliveryRefinementFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = flow_tests.ProductDeliveryFlowTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.fixture.write(refinement.WORKFLOW, yaml.safe_dump(self.fixture.data, allow_unicode=True))
        self.fixture.write(refinement.GUIDE, "# Refinement\n\nStatic context and authority boundaries.\n")
        for path in refinement.TEMPLATES.values():
            self.fixture.write(path, "# Template\n\nDraft only; no results.\n")
        contexts = sorted(refinement.CONTEXT_IDS)
        profiles = sorted(refinement.PROFILE_IDS)
        self.data = {"contract": refinement.CONTRACT, "version": 1, "status": "design-ready",
            "runtime_status": "not-integrated", "workflow_source": str(refinement.WORKFLOW),
            "guide": str(refinement.GUIDE), "templates": refinement.TEMPLATES.copy(),
            "context_dimensions": [{"id": identifier, "prompt": f"ประเมินบริบท {identifier}"} for identifier in contexts],
            "case_profiles": [{"id": identifier, "focus": f"กรณี {identifier}", "evidence_expectations": ["Version-bound source"]}
                              for identifier in profiles],
            "invariants": ["Definitions are not results", "AI recommends and cannot approve", "Scope is explicit",
                           "Evidence has versions", "Unknowns have owners", "Actors have context", "Data has provenance",
                           "Risk has named authority", "Review precedes acceptance", "Outcomes require measurement"],
            "review_levels": [{"id": identifier, "when": "Review condition for " + identifier,
                               "required_review": "Named owner/reviewer"} for identifier in sorted(refinement.REVIEW_LEVEL_IDS)],
            "steps": []}
        for source in self.fixture.data["steps"]:
            identifier = source["id"]
            self.data["steps"].append({"step_id": identifier, "context_refs": contexts[:2], "profile_refs": profiles[:1],
                "questions": [{"id": f"{identifier}.Q{i:02d}", "prompt": f"คำถามบริบท {i}"} for i in range(1, 4)],
                "evidence_checks": [{"id": f"{identifier}.C{i:02d}", "method": f"ตรวจแหล่งข้อมูล {i}",
                                     "required_proof": f"Source/version และ reviewer {i}"} for i in range(1, 3)],
                "refine_triggers": ["Conflicting requirements", "Missing evidence"],
                "exit_criteria": ["Conflict disposition recorded", "Exact-version evidence reviewed"]})
        self.save()

    def save(self, data: dict | None = None) -> None:
        self.fixture.write(refinement.DEFINITION, yaml.safe_dump(self.data if data is None else data, allow_unicode=True))

    def invoke(self, args: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = refinement.main(args, root=self.root)
        return status, stdout.getvalue(), stderr.getvalue()

    def errors(self, mutate) -> list[dict]:
        data = copy.deepcopy(self.data)
        mutate(data)
        return refinement.validate_standard(data, self.root)

    def test_valid_full_contract_and_inherited_data(self) -> None:
        self.assertEqual([], refinement.validate_standard(self.data, self.root))
        view = refinement.export_view(self.data, self.root)
        self.assertEqual(72, len(view["steps"]))
        self.assertEqual(14, len(view["context_dimensions"]))
        self.assertEqual(8, len(view["case_profiles"]))
        self.assertEqual("not-integrated", view["runtime_status"])
        self.assertEqual({"not_verified"}, set(view["authority"].values()))
        source, derived = self.fixture.data["steps"][0], view["steps"][0]
        self.assertEqual(source["owner_role"], derived["owner_role"])
        self.assertEqual(source["acceptance"], derived["acceptance"])
        self.assertEqual(self.fixture.data["tools"][source["primary_tools"][0]]["name"], derived["primary_tools"][0]["name"])
        self.assertNotIn("owner_role", self.data["steps"][0])

    def test_missing_duplicate_and_unknown_steps(self) -> None:
        for mutate in (lambda d: d["steps"].pop(),
                       lambda d: d["steps"].append(copy.deepcopy(d["steps"][0])),
                       lambda d: d["steps"][0].update(step_id="UNKNOWN-STEP")):
            self.assertTrue(self.errors(mutate))

    def test_fixed_context_and_profile_sets(self) -> None:
        for field in ("context_dimensions", "case_profiles", "review_levels"):
            self.assertTrue(self.errors(lambda d: d[field].pop()))
            self.assertTrue(self.errors(lambda d: d[field][0].update(id="invented-case")))
            self.assertTrue(self.errors(lambda d: d[field].append(copy.deepcopy(d[field][0]))))

    def test_refs_known_unique_and_nonempty(self) -> None:
        for field in ("context_refs", "profile_refs"):
            for value in (["invented-ref"], [], [""], "not-list", [["nested"]]):
                self.assertTrue(self.errors(lambda d: d["steps"][0].update({field: value})))
            self.assertTrue(self.errors(lambda d: d["steps"][0][field].append(d["steps"][0][field][0])))

    def test_question_and_check_ids_counts_and_proofs(self) -> None:
        mutations = [lambda d: d["steps"][0]["questions"].pop(),
                     lambda d: d["steps"][0]["evidence_checks"].pop(),
                     lambda d: d["steps"][0]["questions"][0].update(id="STEP02.Q01"),
                     lambda d: d["steps"][0]["questions"][0].update(id="STEP01.Q00"),
                     lambda d: d["steps"][0]["questions"][0].update(prompt=""),
                     lambda d: d["steps"][0]["evidence_checks"][0].update(method=[]),
                     lambda d: d["steps"][0]["evidence_checks"][0].update(required_proof=""),
                     lambda d: d["steps"][0]["questions"].append(copy.deepcopy(d["steps"][0]["questions"][0]))]
        for mutate in mutations:
            self.assertTrue(self.errors(mutate))

    def test_triggers_exit_criteria_and_metadata_shapes(self) -> None:
        for field in ("refine_triggers", "exit_criteria"):
            for value in (["only-one"], ["same", "same"], ["valid", ""], None):
                self.assertTrue(self.errors(lambda d: d["steps"][0].update({field: value})))
        for mutate in (lambda d: d["context_dimensions"][0].update(prompt=""),
                       lambda d: d["case_profiles"][0].update(evidence_expectations=[]),
                       lambda d: d.update(invariants=[]),
                       lambda d: d["invariants"].pop(),
                       lambda d: d.update(review_levels=[]),
                       lambda d: d["review_levels"][0].update(when="")):
            self.assertTrue(self.errors(mutate))

    def test_no_duplicated_role_fields_or_runtime_results(self) -> None:
        for field, value in (("owner_role", "product-manager"), ("title", "Shadow title"),
                             ("primary_tools", ["other-tool"]), ("actual_status", "passed")):
            self.assertTrue(self.errors(lambda d: d["steps"][0].update({field: value})))

    def test_strict_json_optional_metadata_date_nonfinite_and_recursion(self) -> None:
        values = [datetime.date(2026, 10, 13), float("nan"), float("inf"), {"set"}, b"bytes", {1: "nonstringkey"}]
        recursive = []
        recursive.append(recursive)
        values.append(recursive)
        for value in values:
            self.assertTrue(self.errors(lambda d: d.update(metadata=value)))
        metadata = {"as_of": "2026-10-13", "score": 1.25, "available": False, "note": None}
        self.assertEqual([], self.errors(lambda d: d.update(metadata=metadata)))

    def test_fixed_source_pointer_guide_templates_and_version(self) -> None:
        for field, value in (("workflow_source", ".env"), ("guide", "../outside.md"),
                             ("templates", {"context": "/etc/passwd", "step": "wrong"}),
                             ("contract", "unknown"), ("version", True), ("version", 2),
                             ("status", "approved"), ("runtime_status", "ready")):
            self.assertTrue(self.errors(lambda d: d.update({field: value})))

    def test_canonical_workflow_cycle_or_missing_template_blocks(self) -> None:
        self.fixture.data["steps"][0]["depends_on"] = ["STEP72"]
        self.fixture.write(refinement.WORKFLOW, yaml.safe_dump(self.fixture.data))
        self.assertTrue(refinement.validate_standard(self.data, self.root))
        self.fixture.data["steps"][0]["depends_on"] = []
        self.fixture.write(refinement.WORKFLOW, yaml.safe_dump(self.fixture.data))
        self.assertEqual([], refinement.validate_standard(self.data, self.root))
        (self.root / refinement.TEMPLATES["context"]).unlink()
        self.assertTrue(refinement.validate_standard(self.data, self.root))

    def test_canonical_refinement_pointer_when_present_is_fixed(self) -> None:
        self.fixture.data["refinement_standard"] = str(refinement.DEFINITION)
        self.fixture.write(refinement.WORKFLOW, yaml.safe_dump(self.fixture.data))
        self.assertEqual([], refinement.validate_standard(self.data, self.root))
        self.fixture.data["refinement_standard"] = ".env"
        self.fixture.write(refinement.WORKFLOW, yaml.safe_dump(self.fixture.data))
        self.assertTrue(refinement.validate_standard(self.data, self.root))

    def test_role_export_owner_reviewer_and_unknown_role_clean(self) -> None:
        role = "product-manager"
        view = refinement.export_view(self.data, self.root, role)
        expected = [s["id"] for s in self.fixture.data["steps"] if s["owner_role"] == role or role in s["reviewer_roles"]]
        self.assertEqual(expected, [s["step_id"] for s in view["steps"]])
        self.assertTrue(view["view"]["partial"])
        reviewer = refinement.export_view(self.data, self.root, "solution-architect")
        self.assertEqual(72, reviewer["view"]["matching_steps"])
        status, output, error = self.invoke(["--json", "--role", "private-looking-invalid-value"])
        self.assertEqual(2, status)
        self.assertEqual("invalid", json.loads(output)["status"])
        self.assertNotIn("private-looking-invalid-value", output + error)

    def test_export_strict_json_and_canonical_changes_derive_without_duplication(self) -> None:
        self.fixture.data["tools"]["editor"]["name"] = "Renamed canonical editor"
        self.fixture.write(refinement.WORKFLOW, yaml.safe_dump(self.fixture.data))
        status, output, _ = self.invoke(["--json"])
        self.assertEqual(0, status)
        decoded = json.loads(output, parse_constant=lambda v: self.fail("nonfinite JSON"))
        self.assertEqual("Renamed canonical editor", decoded["steps"][0]["primary_tools"][0]["name"])
        self.assertEqual("static_definitions_and_repository_sources", decoded["verification_basis"])

    def test_generated_drift_determinism_and_write_only_fixed_target(self) -> None:
        before = {p.relative_to(self.root) for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(0, self.invoke(["--write"])[0])
        added = {p.relative_to(self.root) for p in self.root.rglob("*") if p.is_file()} - before
        self.assertEqual({refinement.GENERATED}, added)
        generated = (self.root / refinement.GENERATED).read_text()
        self.assertEqual(72, len([line for line in generated.splitlines() if line.startswith("## STEP")]))
        self.assertEqual(72, generated.count('<a id="step'))
        self.assertIn('<a id="step01"></a>', generated)
        self.assertEqual(generated, refinement.render_markdown(self.data, self.root))
        self.assertEqual(0, self.invoke(["--check"])[0])
        self.assertEqual(0, self.invoke([])[0])
        (self.root / refinement.GENERATED).write_text(generated + "drift\n")
        self.assertEqual(2, self.invoke(["--check"])[0])

    def test_markdown_role_view_has_authority_boundary_and_tools(self) -> None:
        status, output, _ = self.invoke(["--markdown", "--role", "product-manager"])
        self.assertEqual(0, status)
        self.assertIn("มุมมองบางส่วน", output)
        self.assertIn("content truth", output)
        self.assertIn("not-integrated", output)
        self.assertIn("editor", output)
        self.assertIn("STEP01.Q01", output)
        self.assertIn("STEP01.C01", output)

    def test_malformed_yaml_alias_duplicate_key_and_private_payload_no_echo(self) -> None:
        for content in ("contract: a\ncontract: b\n", "a: &ref [x]\nb: *ref\n", "private-payload: [broken\n"):
            self.fixture.write(refinement.DEFINITION, content)
            status, output, error = self.invoke(["--json"])
            self.assertEqual(2, status)
            self.assertEqual("invalid", json.loads(output)["status"])
            self.assertNotIn("private-payload", output + error)

    def test_generated_symlink_and_source_credential_boundary(self) -> None:
        outside = self.root / "outside.md"
        outside.write_text("untouched outside fixture")
        (self.root / refinement.GENERATED).symlink_to(outside)
        self.assertEqual(2, self.invoke(["--write"])[0])
        self.assertEqual("untouched outside fixture", outside.read_text())
        self.fixture.data["source_catalog"]["roles"] = ".env"
        self.fixture.write(".env", "dummy fixture; must not be read")
        self.fixture.write(refinement.WORKFLOW, yaml.safe_dump(self.fixture.data))
        with mock.patch.object(refinement, "read_yaml", wraps=refinement.read_yaml) as reader:
            self.assertTrue(refinement.validate_standard(self.data, self.root))
        self.assertNotIn(self.root / ".env", [call.args[0] for call in reader.call_args_list])

    def test_invalid_cli_arguments_never_echo_private_values(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                refinement.main(["--unknown", "private-invalid-payload"], root=self.root)
        self.assertEqual(2, raised.exception.code)
        self.assertNotIn("private-invalid-payload", stdout.getvalue() + stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
