#!/usr/bin/env python3
"""Read-only delivery context tests with isolated, non-client policy fixtures."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
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
import delivery_context as context  # noqa: E402


class DeliveryContextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.canonical = context.load_context(ROOT)
        cls.source_bytes = {path: context.bounded_bytes(context.safe_local(ROOT, path))
                            for path in context.SOURCE_PATHS}

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "architecture-docs"
        self.knowledge = self.root.parent / "knowledge-base"
        patch = mock.patch.dict(os.environ, {"SOLO_KNOWLEDGE_ROOT": str(self.knowledge)})
        patch.start()
        self.addCleanup(patch.stop)
        for ref in self.canonical["artifacts"].values():
            root = self.root if ref["owner_repository"] == "architecture-docs" else self.knowledge
            path = root / ref["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Policy template\n\nคำถามและหลักฐาน 👩🏽‍💻 ไม่มีผลตรวจของลูกค้า\n", encoding="utf-8")
        for path, raw in self.source_bytes.items():
            destination = self.root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        workflow = context.parse_yaml(self.source_bytes[context.WORKFLOW].decode())
        for lane in workflow["lanes"]:
            for path in lane["templates"]:
                destination = self.root / path
                if not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text("# Template\n\nStatic guidance only.\n")

    def load(self) -> dict:
        return context.load_context(self.root)

    def invoke(self, args: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = context.main(args, root=self.root)
        return code, stdout.getvalue(), stderr.getvalue()

    def source(self, path: str) -> dict:
        return context.parse_yaml((self.root / path).read_text(encoding="utf-8"))

    def save(self, path: str, data: dict) -> None:
        (self.root / path).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    def test_all_roles_truthful_coverage_and_provenance(self) -> None:
        data = self.load()
        overview = context.export(data, self.root)
        self.assertEqual({"roles": 55, "dedicated": 17, "family": 38,
            "roles_with_direct_delivery_steps": 11, "roles_with_family_process_only": 44,
            "delivery_steps": 72, "role_groups": 10}, overview["data"]["counts"])
        self.assertEqual(55, len(overview["data"]["roles"]))
        self.assertEqual(10, len(overview["data"]["groups"]))
        self.assertEqual(10, len(overview["sources"]))
        for source in overview["sources"]:
            self.assertEqual(hashlib.sha256((self.root / source["path"]).read_bytes()).hexdigest(), source["sha256"])
        self.assertEqual({"not_verified"}, set(overview["authority"].values()))
        self.assertEqual({"definition_available": True, "runtime_integrated": False}, overview["capabilities"])
        self.assertEqual({"role": None, "step": None, "artifact_id": None, "offset": None, "limit": None}, overview["filters"])
        json.dumps(overview, allow_nan=False)

    def test_family_only_role_returns_policy_and_controls_not_fake_steps(self) -> None:
        data = self.load()
        roles = [row for row in data["roles"] if row["specialist_refinement_status"] == "family-process-only"]
        self.assertEqual(44, len(roles))
        for row in roles:
            self.assertTrue(row["position_templates"])
            self.assertEqual(4, len(row["work_controls"]))
            self.assertEqual([], row["owner_step_ids"])
            self.assertEqual([], row["reviewer_step_ids"])
            self.assertTrue(row["groups"][0]["subprocesses"])
        role = context.export(data, self.root, view="role", role="finance-accounting")["data"]
        self.assertEqual("family-process-only", role["specialist_refinement_status"])
        self.assertEqual("pilot-ready", role["groups"][0]["declared_maturity"])
        self.assertNotIn("gaps", role["groups"][0])
        self.assertEqual([], context.export(data, self.root, view="refinement", role=role["id"])["data"]["steps"])

    def test_views_use_actual_step_definitions_and_document_mappings(self) -> None:
        data = self.load()
        for view in ("workflow", "documents", "refinement"):
            result = context.export(data, self.root, view=view, role="product-manager", step="PD01")
            self.assertTrue(result["data"]["partial_view"])
            self.assertEqual("product-manager", result["data"]["role_context"]["id"])
        source = next(row for row in data["workflow"]["steps"] if row["id"] == "PD01")
        workflow = context.export(data, self.root, view="workflow", step="PD01")["data"]
        self.assertEqual([source], workflow["steps"])
        documents = context.export(data, self.root, view="documents", step="PD01")["data"]
        self.assertEqual(["PD01"], [row["step_id"] for row in documents["step_documents"]])
        refinement = context.export(data, self.root, view="refinement", step="PD01")["data"]["steps"]
        self.assertEqual(3, len(refinement[0]["questions"]))
        self.assertEqual(source["owner_role"], refinement[0]["owner_role"])
        self.assertEqual(1, context.export(data, self.root, view="refinement", step="PD01")["data"]["view"]["matching_steps"])

    def test_unknown_identifiers_and_conflicting_filters(self) -> None:
        data = self.load()
        cases = [({"view": "role", "role": "unknown-role"}, "role_unknown"),
                 ({"view": "workflow", "step": "PD99"}, "step_unknown"),
                 ({"view": "refinement", "step": "../private"}, "step_identifier_invalid"),
                 ({"view": "role", "role": "bad/private"}, "role_identifier_invalid"),
                 ({"view": "workflow", "role": "finance-accounting", "step": "PD01"}, "filter_conflict"),
                 ({"view": "overview", "role": "product-manager"}, "view_filter_invalid"),
                 ({"view": "role"}, "role_filter_required"),
                 ({"view": "role", "role": "product-manager", "step": "PD01"}, "role_filter_required"),
                 ({"view": "workflow", "offset": 0}, "pagination_filter_invalid"),
                 ({"view": "artifact"}, "artifact_filter_invalid")]
        for args, expected in cases:
            with self.subTest(args=args):
                with self.assertRaisesRegex(context.ContextError, "^" + expected + "$" ):
                    context.export(data, self.root, **args)

    def test_artifact_id_pagination_and_unicode_integrity(self) -> None:
        data = self.load()
        path = context.GUIDES[0]
        expected = "artifact-" + hashlib.sha256(("architecture-docs:" + path).encode()).hexdigest()[:32]
        self.assertEqual(expected, context.artifact_id(path))
        raw = (self.root / path).read_bytes()
        body = raw.decode("utf-8")
        first = context.export(data, self.root, view="artifact", artifact=expected, offset=7, limit=10)
        self.assertEqual(body[7:17], first["data"]["body"])
        self.assertEqual(17, first["data"]["next_offset"])
        self.assertEqual(len(body), first["data"]["total"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), first["data"]["sha256"])
        self.assertEqual("unicode-codepoint", first["data"]["unit"])
        end = context.export(data, self.root, view="artifact", artifact=expected, offset=len(body), limit=10)
        self.assertEqual("", end["data"]["body"])
        self.assertIsNone(end["data"]["next_offset"])
        self.assertFalse(end["data"]["truncated"])
        default = context.export(data, self.root, view="artifact", artifact=expected)
        self.assertEqual((0, 8000), (default["filters"]["offset"], default["filters"]["limit"]))

    def test_unknown_artifact_denied_before_body_read(self) -> None:
        data = self.load()
        with mock.patch.object(context, "bounded_bytes", side_effect=AssertionError("unexpected body read")):
            for identifier in ("../../diary/private.md", "artifact-" + "0" * 32, context.artifact_id("README.md")):
                with self.assertRaisesRegex(context.ContextError, "artifact_unknown"):
                    context.export(data, self.root, view="artifact", artifact=identifier)

    def test_artifact_pagination_bounds_and_filter_modes(self) -> None:
        data = self.load()
        identifier = context.artifact_id(context.GUIDES[0])
        for offset, limit in ((-1, 1), (0, 0), (0, 16001), (True, 1), (0, True), (99999, 1)):
            with self.assertRaises(context.ContextError):
                context.export(data, self.root, view="artifact", artifact=identifier, offset=offset, limit=limit)
        with self.assertRaisesRegex(context.ContextError, "view_filter_invalid"):
            context.export(data, self.root, view="artifact", artifact=identifier, role="product-manager")

    def test_exact_knowledge_policy_allowlist_without_following_links(self) -> None:
        data = self.load()
        policies = [ref for ref in data["artifacts"].values() if ref["owner_repository"] == "knowledge-base"]
        self.assertEqual(30, len(policies))
        policy = policies[0]
        private = self.knowledge / "diary/entries/private.md"
        private.parent.mkdir(parents=True)
        private.write_text("never-read-this-payload")
        (self.knowledge / policy["path"]).write_text("# Policy\n[link](../../diary/entries/private.md)\n")
        result = context.export(data, self.root, view="artifact", artifact=policy["artifact_id"])
        self.assertEqual("knowledge-base", result["data"]["owner_repository"])
        self.assertNotIn("never-read-this-payload", json.dumps(result))
        with self.assertRaisesRegex(context.ContextError, "artifact_unknown"):
            context.export(data, self.root, view="artifact", artifact=context.artifact_id("diary/entries/private.md", "knowledge-base"))

    def test_optional_knowledge_checkout_unavailable_is_explicit(self) -> None:
        with mock.patch.dict(os.environ, {"SOLO_KNOWLEDGE_ROOT": str(self.root.parent / "missing-policy") } ):
            data = self.load()
            role = context.export(data, self.root, view="role", role="product-manager")
            ref = role["data"]["groups"][0]["process"]
            self.assertEqual("unavailable", ref["availability"])
            with self.assertRaisesRegex(context.ArtifactUnavailable, "artifact_unavailable"):
                context.export(data, self.root, view="artifact", artifact=ref["artifact_id"])
            code, output, _ = self.invoke(["--json", "--view", "artifact", "--artifact-id", ref["artifact_id"]])
            self.assertEqual(2, code)
            failure = json.loads(output)
            self.assertEqual("unavailable", failure["status"])
            self.assertEqual("artifact", failure["view"])
            self.assertEqual([{ "code": "artifact_unavailable"}], failure["errors"])

    def test_template_and_artifact_symlinks_and_config_paths_denied(self) -> None:
        data = self.load()
        path = self.root / context.GUIDES[0]
        path.unlink()
        path.symlink_to(self.root / context.GUIDES[1])
        with self.assertRaises(context.PackError):
            context.export(data, self.root, view="artifact", artifact=context.artifact_id(context.GUIDES[0]))
        with self.assertRaises(context.PackError):
            self.load()
        path.unlink()
        path.write_text("# Static guide")
        positions = self.source(context.POSITIONS)
        positions["positions"][0]["templates"] = ["templates/config/private.md"]
        self.save(context.POSITIONS, positions)
        with self.assertRaises(context.PackError):
            self.load()
        with mock.patch.object(context, "bounded_bytes", side_effect=AssertionError("unsafe source read")):
            with self.assertRaises(context.PackError):
                context.load_context(self.root / "config")

    def test_position_bijection_membership_and_template_checks(self) -> None:
        original = self.source(context.POSITIONS)
        mutations = [lambda d: d["positions"].append(copy.deepcopy(d["positions"][0])),
                     lambda d: d["positions"].pop(),
                     lambda d: d["positions"][0].update(role_groups=["unknown-group"]),
                     lambda d: d["positions"][0].update(templates=["templates/nonexistent.md"])]
        for mutate in mutations:
            data = copy.deepcopy(original)
            mutate(data)
            self.save(context.POSITIONS, data)
            with self.assertRaises(context.PackError):
                self.load()

    def test_strict_json_yaml_aliases_and_duplicate_keys_fail_safely(self) -> None:
        original = self.source_bytes[context.COVERAGE].decode()
        suffixes = ("\nextra: 2026-10-13\n", "\nextra: .nan\n", "\nextra: &value [one]\nalias: *value\n",
                    "\nextra: one\nextra: two\n")
        for suffix in suffixes:
            (self.root / context.COVERAGE).write_text(original + suffix)
            code, output, error = self.invoke(["--json"])
            self.assertEqual(2, code)
            self.assertEqual("invalid", json.loads(output)["status"])
            self.assertNotIn("extra:", output + error)

    def test_source_pointer_denied_before_old_validator_or_private_read(self) -> None:
        data = self.source(context.WORKFLOW)
        data["source_catalog"]["roles"] = "config/private.yaml"
        self.save(context.WORKFLOW, data)
        with mock.patch.object(context, "load_sources", side_effect=AssertionError("old validator used")):
            with self.assertRaisesRegex(context.ContextError, "workflow_source_pointer_invalid"):
                self.load()

    def test_source_changed_during_validator_read_is_rejected(self) -> None:
        original = context.validate_documents
        def mutate_during_validation(data, root):
            result = original(data, root)
            roles = self.source(context.ROLES)
            roles["roles"][0]["summary"] += " source changed"
            self.save(context.ROLES, roles)
            return result
        with mock.patch.object(context, "validate_documents", side_effect=mutate_during_validation):
            with self.assertRaisesRegex(context.ContextError, "source_changed_during_read"):
                self.load()

    def test_registry_cannot_expand_the_authorized_knowledge_policy_paths(self) -> None:
        coverage = self.source(context.COVERAGE)
        extra = copy.deepcopy(coverage["role_groups"][0])
        extra.update(id="unapproved-group", charter="roles/unapproved-group/README.md",
            process="roles/unapproved-group/PROCESS.md",
            process_register="roles/unapproved-group/PROCESS-REGISTER.md")
        coverage["role_groups"].append(extra)
        self.save(context.COVERAGE, coverage)
        with self.assertRaisesRegex(context.ContextError, "group_registry_v1_required"):
            self.load()

    def test_refinement_export_rechecks_its_reopened_source_snapshot(self) -> None:
        data = self.load()
        original = context.refinement_view
        def mutate_after_export(*args, **kwargs):
            result = original(*args, **kwargs)
            roles = self.source(context.ROLES)
            roles["roles"][0]["summary"] += " modified during export"
            self.save(context.ROLES, roles)
            return result
        with mock.patch.object(context, "refinement_view", side_effect=mutate_after_export):
            with self.assertRaisesRegex(context.ContextError, "source_changed_during_read"):
                context.export(data, self.root, view="refinement", step="PD01")

    def test_generated_document_and_source_drift(self) -> None:
        self.assertEqual(0, self.invoke(["--write"])[0])
        self.assertEqual(0, self.invoke(["--check"])[0])
        generated = self.root / context.GENERATED
        generated.write_text(generated.read_text() + "drift\n")
        self.assertEqual(2, self.invoke(["--check"])[0])
        self.assertEqual(0, self.invoke(["--write"])[0])
        positions = self.source(context.POSITIONS)
        positions["positions"][0]["status"] = "covered-by-role-family"
        self.save(context.POSITIONS, positions)
        self.assertEqual(2, self.invoke(["--check"])[0])

    def test_cli_rejects_arbitrary_roots_paths_writes_and_private_echo(self) -> None:
        for args in (["--json", "--root", "/private-input-canary"],
                     ["--json", "--pack", "private-input-canary"],
                     ["--write", "--view", "overview"],
                     ["--json", "--view", "private-input-canary"],
                     ["--json", "--offset", "private-input-canary"]):
            code, output, error = self.invoke(list(args))
            self.assertEqual(2, code)
            self.assertNotIn("private-input-canary", output + error)
            if "--json" in args:
                failure = json.loads(output)
                self.assertEqual(set(("role", "step", "artifact_id", "offset", "limit")), set(failure["filters"]))


if __name__ == "__main__":
    unittest.main()
