#!/usr/bin/env python3
"""Document contract/readiness tests; isolated fixtures, no live services or private data."""

from __future__ import annotations

import contextlib
import copy
import datetime
import hashlib
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
import delivery_document_pack as pack  # noqa: E402
import product_delivery_flow_test as flow_tests  # noqa: E402


class DeliveryDocumentPackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.flow_fixture = flow_tests.ProductDeliveryFlowTest()
        self.flow_fixture.setUp()
        self.addCleanup(self.flow_fixture.doCleanups)
        self.root = self.flow_fixture.root
        self.flow_fixture.write("systems/architecture/product-delivery-workflow.yaml", yaml.safe_dump(self.flow_fixture.data))
        profiles = {"ai-product": ["ai-evaluation", "model-change"],
                    "data-product": ["data-contract", "data-quality", "lineage-retention"],
                    "security-sensitive": ["threat-model", "privacy-impact"],
                    "client-delivery": ["client-review-pack", "user-guide", "commercial-scope",
                                        "service-support", "release-notes"]}
        definitions = []
        for identifier in sorted(pack.CORE | pack.CONTROLS | {i for ids in profiles.values() for i in ids}):
            category = "core" if identifier in pack.CORE else "work-control" if identifier in pack.CONTROLS else "extension"
            template = f"templates/document-pack/{identifier}.md"
            definitions.append({"id": identifier, "title": identifier, "category": category,
                                "owner_role": "product-manager", "template": template,
                                "required_content": ["Identity and evidence"]})
            self.flow_fixture.write(template, "---\ntitle: Template\nstatus: template\n---\n\n# Draft\n\n[กรอกขอบเขต]\n")
        self.standard = {"contract": pack.CONTRACT, "version": 1, "status": "standard",
                         "workflow_definition": "systems/architecture/product-delivery-workflow.yaml",
                         "roles_source": "docs/registries/ROLE-CATALOG.yaml",
                         "core_doc_types": sorted(pack.CORE), "mandatory_work_types": sorted(pack.CONTROLS),
                         "profiles": profiles, "documents": definitions,
                         "step_documents": [{"step_id": row["id"], "doc_types": ["overview"],
                                             "profile_doc_types": []} for row in self.flow_fixture.data["steps"]]}
        self.flow_fixture.write(pack.STANDARD, yaml.safe_dump(self.standard))
        self.pack_root = self.root / "sample-work"
        self.pack_root.mkdir()

    def invoke(self, args: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = pack.main(args, root=self.root)
        return status, stdout.getvalue(), stderr.getvalue()

    def ready(self, *, kind: str = "feature-slice") -> dict:
        data = pack.draft_manifest(self.standard, "WORK-1", kind)
        data["work"].update(title="Bounded product work", outcome="Measured usability outcome",
                            scope="Approved interface change and test", owner="named-product-owner",
                            risk="Low; contract review and rollback available")
        for row in data["documents"]:
            doc = row["doc_type"]
            row.update(status="approved", owner="named-owner", decision_ref=f"DEC-{doc}",
                       evidence_refs=[f"EV-{doc}"])
            path = self.pack_root / row["artifact_path"]
            path.parent.mkdir(exist_ok=True)
            path.write_text("---\n" + yaml.safe_dump({"doc_type": doc, "work_id": "WORK-1", "version": "1",
                              "status": "approved"}) + "---\n\n# Controlled document\n\n"
                            "The agreed scope and assumptions are recorded with the accountable owner and exact revision.\n"
                            "The implementation and independent review evidence support the declared outcome and known limitations.\n")
            row["artifact_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            data["evidence"].append({"id": f"EV-{doc}", "doc_type": doc, "artifact_version": "1", "result": "passed",
                                     "source_ref": f"urn:evidence:{doc}:v1", "summary": "Declared document review passed"})
            data["decisions"].append({"id": f"DEC-{doc}", "kind": "approval", "authority": "named-reviewer",
                                      "decision": "approved", "doc_type": doc, "artifact_version": "1",
                                      "work_id": data["work"]["id"], "work_scope": data["work"]["scope"],
                                      "evidence_refs": [f"EV-{doc}"], "artifact_sha256": row["artifact_sha256"],
                                      "reason": "Declared scoped content accepted"})
        return data

    def row(self, data: dict, doc: str) -> dict:
        return next(row for row in data["documents"] if row["doc_type"] == doc)

    def codes(self, data: dict) -> set[str]:
        return {i["code"] for i in pack.pack_report(data, self.pack_root, self.standard)["blockers"]}

    def reuse_ready(self) -> tuple[dict, dict, dict]:
        data = self.ready()
        row = self.row(data, "prd")
        row.update(disposition="reuse", artifact_ref="urn:document:prd:v1")
        decision = next(d for d in data["decisions"] if d["doc_type"] == "prd")
        decision.update(kind="reuse", artifact_ref=row["artifact_ref"], work_id=data["work"]["id"],
                        work_scope=data["work"]["scope"])
        next(e for e in data["evidence"] if e["doc_type"] == "prd")["artifact_ref"] = row["artifact_ref"]
        return data, row, decision

    def resolved_feedback(self) -> tuple[dict, dict, dict]:
        data = self.ready()
        feedback = {"id": "FB-1", "doc_type": "prd", "artifact_version": "1", "type": "defect", "status": "resolved",
                    "owner": "named-owner", "decision_ref": "DEC-FB-1", "evidence_refs": ["EV-prd"]}
        decision = {"id": "DEC-FB-1", "kind": "feedback_resolution", "authority": "named-reviewer", "decision": "approved",
                    "doc_type": "prd", "artifact_version": "1", "evidence_refs": ["EV-prd"], "reason": "Retest passed",
                    "feedback_ids": ["FB-1"], "work_id": data["work"]["id"], "work_scope": data["work"]["scope"]}
        data["feedback"].append(feedback)
        data["decisions"].append(decision)
        return data, feedback, decision

    def test_manifest_path_components_block_before_any_manifest_read(self) -> None:
        for directory, filename in (("secrets", "pack-manifest.yaml"), (".config", "pack-manifest.yaml"),
                                    ("config", "pack-manifest.yaml"), ("Config", "pack-manifest.yaml"),
                                    ("credentials", "pack-manifest.yaml"), ("safe", ".env"),
                                    ("safe", "credentials.yaml"), ("safe", "config.json"),
                                    ("safe", "config.yaml"), ("safe", "config.yml"), ("safe", "config.ini")):
            with self.subTest(directory=directory, filename=filename):
                path = self.pack_root / directory / filename
                path.parent.mkdir(exist_ok=True)
                path.write_text("dummy fixture payload")
                with mock.patch.object(pack, "read_yaml", wraps=pack.read_yaml) as reader:
                    status, output, error = self.invoke(["--pack", str(path), "--json"])
                self.assertEqual(2, status)
                self.assertEqual("malformed", json.loads(output)["status"])
                self.assertNotIn(path, [call.args[0] for call in reader.call_args_list])
                self.assertNotIn("dummy fixture payload", output + error)
                self.assertNotIn(str(path), output + error)
        target = self.pack_root / "pack-manifest.yaml"
        target.write_text("dummy fixture payload")
        linked = self.pack_root / "linked.yaml"
        linked.symlink_to(target)
        for path in (linked, self.pack_root / "safe" / ".." / target.name):
            with mock.patch.object(pack, "read_yaml", wraps=pack.read_yaml) as reader:
                self.assertEqual(2, self.invoke(["--pack", str(path), "--json"])[0])
            self.assertNotIn(path, [call.args[0] for call in reader.call_args_list])

    def test_direct_pack_report_cannot_authorize_credential_root(self) -> None:
        data = self.ready()
        for root in (self.pack_root / "secrets", self.pack_root / ".config", self.pack_root / "config",
                     self.pack_root / "Config", self.pack_root / "credentials"):
            with self.subTest(root_component=root.name):
                root.mkdir(exist_ok=True)
                with mock.patch.object(pack, "safe_local") as local_reader:
                    report = pack.pack_report(data, root, self.standard)
                self.assertEqual("malformed", report["status"])
                local_reader.assert_not_called()

    def test_reuse_exact_artifact_and_current_work_binding(self) -> None:
        data, row, decision = self.reuse_ready()
        self.assertEqual(set(), self.codes(data))
        mutations = [lambda d, r, a: r.update(artifact_ref="urn:document:other-project-prd:v1"),
                     lambda d, r, a: d["work"].update(id="OTHER-WORK"),
                     lambda d, r, a: d["work"].update(scope="Different current outcome scope"),
                     lambda d, r, a: a.pop("artifact_ref"), lambda d, r, a: a.pop("work_id"),
                     lambda d, r, a: a.pop("work_scope"), lambda d, r, a: a.update(work_scope=["wrong-type"])]
        for mutate in mutations:
            candidate, row, decision = self.reuse_ready()
            mutate(candidate, row, decision)
            self.assertNotEqual("can_review", pack.pack_report(candidate, self.pack_root, self.standard)["status"])

    def test_reuse_explicit_evidence_artifact_must_match(self) -> None:
        data, row, _ = self.reuse_ready()
        evidence = next(e for e in data["evidence"] if e["doc_type"] == "prd")
        evidence["artifact_ref"] = row["artifact_ref"]
        self.assertEqual(set(), self.codes(data))
        evidence["artifact_ref"] = "urn:document:other-project-prd:v1"
        self.assertNotEqual("can_review", pack.pack_report(data, self.pack_root, self.standard)["status"])

    def test_reuse_requires_exact_artifact_evidence_even_when_row_and_decision_change(self) -> None:
        data, row, decision = self.reuse_ready()
        evidence = next(e for e in data["evidence"] if e["doc_type"] == "prd")
        evidence.pop("artifact_ref")
        self.assertNotEqual("can_review", pack.pack_report(data, self.pack_root, self.standard)["status"])
        evidence["artifact_ref"] = row["artifact_ref"]
        self.assertEqual(set(), self.codes(data))
        row["artifact_ref"] = decision["artifact_ref"] = "urn:document:other-project-prd:v1"
        self.assertNotEqual("can_review", pack.pack_report(data, self.pack_root, self.standard)["status"])
        evidence["artifact_ref"] = row["artifact_ref"]
        self.assertEqual(set(), self.codes(data))

    def test_every_active_decision_binds_current_work_scope(self) -> None:
        data = self.ready()
        data["work"]["scope"] = "Expanded scope after declared approval"
        self.assertIn("document_exact_version_approval_missing", self.codes(data))
        data = self.ready()
        self.row(data, "architecture").update(disposition="not_applicable", reason="Scoped nonsoftware exception")
        decision = next(d for d in data["decisions"] if d["doc_type"] == "architecture")
        decision["kind"] = "applicability"
        self.assertEqual(set(), self.codes(data))
        decision["work_scope"] = "Prior unrelated scope"
        self.assertIn("scoped_applicability_approval_missing", self.codes(data))
        data = self.ready()
        historical = {**data["decisions"][0], "id": "DEC-HISTORY", "work_id": "OLDER-WORK", "work_scope": "Older declared scope"}
        data["decisions"].append(historical)
        self.assertEqual(set(), self.codes(data))

    def test_feedback_resolution_only_named_ids_or_explicit_batch(self) -> None:
        data, feedback, decision = self.resolved_feedback()
        self.assertEqual(set(), self.codes(data))
        data["feedback"].append({**feedback, "id": "FB-2"})
        report = pack.pack_report(data, self.pack_root, self.standard)
        self.assertIn("FB-2", report["unresolved_feedback"])
        decision["feedback_ids"] = ["FB-1", "FB-2"]
        self.assertEqual(set(), self.codes(data))
        decision["feedback_ids"] = ["FB-1"]
        data["decisions"].append({**decision, "id": "DEC-FB-2", "feedback_ids": ["FB-2"]})
        data["feedback"][-1]["decision_ref"] = "DEC-FB-2"
        self.assertEqual(set(), self.codes(data))

    def test_feedback_resolution_binding_fields_and_targets_validated(self) -> None:
        mutations = [lambda d, f, a: a.pop("feedback_ids"), lambda d, f, a: a.update(feedback_ids=[]),
                     lambda d, f, a: a.update(feedback_ids=["UNKNOWN-FB"]),
                     lambda d, f, a: a.update(feedback_ids=["FB-1", "FB-1"]),
                     lambda d, f, a: a.update(feedback_ids="FB-1"),
                     lambda d, f, a: a.update(feedback_ids=[["wrong-type"]]),
                     lambda d, f, a: a.update(doc_type=["private-invalid-field"]),
                     lambda d, f, a: a.update(work_id="OTHER-WORK"),
                     lambda d, f, a: a.update(work_scope="Different scope"),
                     lambda d, f, a: a.pop("work_id"), lambda d, f, a: a.pop("work_scope"),
                     lambda d, f, a: f.update(doc_type="architecture", evidence_refs=["EV-architecture"]),
                     lambda d, f, a: f.update(artifact_version="old")]
        for mutate in mutations:
            data, feedback, decision = self.resolved_feedback()
            mutate(data, feedback, decision)
            self.assertNotEqual("can_review", pack.pack_report(data, self.pack_root, self.standard)["status"])

    def test_standard_mapping_frozen_types_and_valid_roles(self) -> None:
        self.assertEqual([], pack.validate_standard(self.standard, self.root))
        for field in ("core_doc_types", "mandatory_work_types"):
            candidate = copy.deepcopy(self.standard)
            candidate[field].pop()
            self.assertTrue(pack.validate_standard(candidate, self.root))
        for mutation in ("mapping", "docref", "profile", "role", "duplicate"):
            candidate = copy.deepcopy(self.standard)
            if mutation == "mapping": candidate["step_documents"].pop()
            if mutation == "docref": candidate["step_documents"][0]["doc_types"] = ["unknown"]
            if mutation == "profile": candidate["step_documents"][0]["profile_doc_types"] = ["overview"]
            if mutation == "role": candidate["documents"][0]["owner_role"] = "invented-role"
            if mutation == "duplicate": candidate["documents"].append(candidate["documents"][0])
            with self.subTest(mutation=mutation): self.assertTrue(pack.validate_standard(candidate, self.root))

    def test_standard_missing_template_and_workflow_cycle(self) -> None:
        candidate = copy.deepcopy(self.standard)
        candidate["documents"][0]["template"] = "templates/missing.md"
        self.assertTrue(pack.validate_standard(candidate, self.root))
        self.flow_fixture.data["steps"][0]["depends_on"] = ["STEP72"]
        self.flow_fixture.write("systems/architecture/product-delivery-workflow.yaml", yaml.safe_dump(self.flow_fixture.data))
        self.assertTrue(pack.validate_standard(self.standard, self.root))

    def test_strict_json_metadata_and_duplicate_yaml_keys(self) -> None:
        for value in (datetime.date(2026, 10, 13), {"set"}, float("nan"), float("inf"), b"binary"):
            with self.subTest(value_type=type(value).__name__):
                self.assertTrue(pack.validate_standard({**self.standard, "metadata": value}, self.root))
                report = pack.pack_report({"metadata": value}, self.pack_root, self.standard)
                self.assertEqual("malformed", report["status"])
        with self.assertRaises(pack.PackError): pack.parse_yaml("contract: a\ncontract: b\n")
        with self.assertRaises(pack.PackError): pack.parse_yaml("a: &shared [x]\nb: *shared\n")

    def test_draft_has_blockers_and_no_live_authority(self) -> None:
        report = pack.pack_report(pack.draft_manifest(self.standard), self.pack_root, self.standard)
        self.assertEqual("blocked", report["status"])
        self.assertGreater(report["blocker_count"], 19)
        self.assertEqual("local_files_and_declared_records", report["verification_basis"])
        self.assertEqual({"not_verified"}, set(report["authority"].values()))
        self.assertFalse(report["ai_decision_context"]["automatic_approval"])

    def test_create_ready_is_only_can_review_not_acceptance(self) -> None:
        report = pack.pack_report(self.ready(), self.pack_root, self.standard)
        self.assertEqual([], report["blockers"])
        self.assertEqual("can_review", report["next_action"])
        self.assertEqual(19, len(report["document_coverage"]))
        self.assertEqual(19, len(report["declared_evidence"]))
        self.assertEqual(19, len(report["declared_approvals"]))
        self.assertFalse(report["declared_approvals"][0]["authority_verified"])
        self.assertNotIn("agreed scope", json.dumps(report))

    def test_reuse_needs_stable_ref_scoped_decision_and_version(self) -> None:
        data = self.ready()
        row = self.row(data, "prd")
        row.update(disposition="reuse", artifact_ref="urn:document:prd:v1")
        decision = next(d for d in data["decisions"] if d["doc_type"] == "prd")
        self.assertIn("scoped_reuse_approval_missing", self.codes(data))
        decision.update(kind="reuse", artifact_ref=row["artifact_ref"])
        next(e for e in data["evidence"] if e["doc_type"] == "prd")["artifact_ref"] = row["artifact_ref"]
        self.assertEqual(set(), self.codes(data))
        row["artifact_ref"] = "https://token:password@example.invalid/private"
        self.assertIn("reuse_stable_ref_missing", self.codes(data))

    def test_not_applicable_with_named_approval_and_full_product_waiver(self) -> None:
        data = self.ready()
        self.row(data, "architecture").update(disposition="not_applicable", reason="Nonsoftware scoped activity")
        decision = next(d for d in data["decisions"] if d["doc_type"] == "architecture")
        decision["kind"] = "applicability"
        self.assertEqual(set(), self.codes(data))
        data["work"]["kind"] = "full-product"
        self.assertIn("scoped_applicability_approval_missing", self.codes(data))
        decision.update(kind="waiver", reason="Named approved scoped exception for this exact artifact")
        self.assertEqual(set(), self.codes(data))

    def test_missing_core_controls_and_client_profile_block(self) -> None:
        for doc in ("prd", "work-record"):
            data = self.ready()
            data["documents"] = [r for r in data["documents"] if r["doc_type"] != doc]
            self.assertIn("required_document_unaccounted", self.codes(data))
        data = self.ready()
        data["work"]["client_facing"] = True
        self.assertIn("client_delivery_profile_required", self.codes(data))
        data["work"].update(profiles=["client-delivery"], profile_reason="Client reviewed delivery")
        self.assertIn("required_document_unaccounted", self.codes(data))

    def test_work_controls_cannot_reuse_or_waive(self) -> None:
        for disposition in ("reuse", "not_applicable"):
            data = self.ready()
            self.row(data, "work-record")["disposition"] = disposition
            self.assertIn("work_control_must_create", self.codes(data))

    def test_duplicate_unknown_and_malformed_fields_cleanly_block(self) -> None:
        mutations = [lambda d: d["documents"].append(copy.deepcopy(d["documents"][0])),
                     lambda d: d["documents"][0].update(doc_type="unknown"),
                     lambda d: d["work"].update(profiles=["unknown"]),
                     lambda d: d["documents"][0].update(evidence_refs="EV"),
                     lambda d: d["work"].update(client_facing="true"),
                     lambda d: d["work"].update(kind=["private-invalid-field"]),
                     lambda d: d["decisions"][0].update(kind=["private-invalid-field"]),
                     lambda d: d["feedback"].append({"id": "FB-1", "doc_type": ["private-invalid-field"]}),
                     lambda d: d["evidence"].append(copy.deepcopy(d["evidence"][0]))]
        for mutate in mutations:
            data = self.ready()
            mutate(data)
            self.assertEqual("malformed", pack.pack_report(data, self.pack_root, self.standard)["status"])

    def test_stale_evidence_approval_document_version(self) -> None:
        for field in ("evidence", "decisions"):
            data = self.ready()
            data[field][0]["artifact_version"] = "old"
            self.assertIn("record_artifact_version_stale", self.codes(data))
        data = self.ready()
        self.row(data, "prd")["artifact_version"] = "2"
        self.assertIn("document_frontmatter_identity_version_status", self.codes(data))

    def test_content_edited_without_version_change_is_stale(self) -> None:
        data = self.ready()
        row = self.row(data, "prd")
        path = self.pack_root / row["artifact_path"]
        path.write_text(path.read_text() + "\nNew behavior added after approval; same declared version.\n")
        self.assertIn("document_content_digest_stale", self.codes(data))
        row["artifact_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertIn("document_exact_version_approval_missing", self.codes(data))

    def test_digest_uses_exact_crlf_bytes_and_same_content_snapshot(self) -> None:
        data = self.ready()
        row = self.row(data, "prd")
        path = self.pack_root / row["artifact_path"]
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        row["artifact_sha256"] = digest
        next(d for d in data["decisions"] if d["doc_type"] == "prd")["artifact_sha256"] = digest
        self.assertEqual(set(), self.codes(data))

    def test_failed_unavailable_pending_rejected_block(self) -> None:
        for result in ("failed", "unavailable", "not_run"):
            data = self.ready()
            data["evidence"][0]["result"] = result
            self.assertIn("evidence_not_passed", self.codes(data))
        for state in ("pending", "rejected"):
            data = self.ready()
            data["decisions"][0]["decision"] = state
            self.assertIn("decision_pending_or_rejected", self.codes(data))

    def test_feedback_needs_owned_exact_version_resolution_and_evidence(self) -> None:
        data = self.ready()
        feedback = {"id": "FB-1", "doc_type": "prd", "artifact_version": "1", "type": "defect", "status": "new",
                    "owner": "named-owner", "decision_ref": "", "evidence_refs": []}
        data["feedback"].append(feedback)
        self.assertIn("feedback_unresolved_or_unproved_resolution", self.codes(data))
        feedback["status"] = "resolved"
        self.assertIn("feedback_unresolved_or_unproved_resolution", self.codes(data))
        decision = {"id": "DEC-FB-1", "kind": "feedback_resolution", "authority": "named-reviewer", "decision": "approved",
                    "doc_type": "prd", "artifact_version": "1", "evidence_refs": ["EV-prd"], "reason": "Retest passed",
                    "feedback_ids": ["FB-1"], "work_id": data["work"]["id"], "work_scope": data["work"]["scope"]}
        data["decisions"].append(decision)
        feedback.update(decision_ref="DEC-FB-1", evidence_refs=["EV-prd"])
        self.assertEqual(set(), self.codes(data))
        feedback["artifact_version"] = "old"
        self.assertIn("record_artifact_version_stale", self.codes(data))

    def test_document_path_traversal_absolute_symlink_and_credentials(self) -> None:
        outside = self.root / "outside.md"
        outside.write_text("private-looking payload must not be read")
        linked = self.pack_root / "linked.md"
        linked.symlink_to(outside)
        for path in ("../outside.md", str(outside), "linked.md", ".env", ".git/config", "credentials.json"):
            data = self.ready()
            self.row(data, "prd")["artifact_path"] = path
            self.assertIn("document_local_path_boundary_or_missing", self.codes(data))

    def test_evidence_local_path_and_credential_boundary(self) -> None:
        data = self.ready()
        evidence = data["evidence"][0]
        evidence.pop("source_ref")
        evidence["path"] = "result.txt"
        (self.pack_root / "result.txt").write_text("passed")
        self.assertEqual(set(), self.codes(data))
        for path in ("../outside", ".env", ".git/config", "token.json", "/etc/passwd"):
            evidence["path"] = path
            self.assertIn("evidence_local_path_boundary_or_missing", self.codes(data))

    def test_placeholder_detection_does_not_reject_links_or_task_lists(self) -> None:
        data = self.ready()
        path = self.pack_root / self.row(data, "prd")["artifact_path"]
        baseline = path.read_text()
        path.write_text(baseline + "\n- [x] Evidence reviewed\nSee [design](design.md) for the precise decisions.\n")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.row(data, "prd")["artifact_sha256"] = digest
        next(d for d in data["decisions"] if d["doc_type"] == "prd")["artifact_sha256"] = digest
        self.assertEqual(set(), self.codes(data))
        for token in ("TODO", "TBD", "[ต้องยืนยัน]", "[กรอก owner]", "[DOC-ID]", "[WORK-ID; งานนี้]", "[safe summary]"):
            path.write_text(baseline + "\n" + token)
            self.assertIn("document_placeholder_or_empty", self.codes(data))

    def test_init_new_only_draft_identity_profile_and_no_facts(self) -> None:
        target = self.root / "fresh-pack"
        manifest_path = pack.init_pack(target, "WORK-2", "hotfix", [], True, self.standard, self.root)
        data = pack.read_yaml(manifest_path)
        self.assertEqual(24, len(data["documents"]))
        self.assertEqual(["client-delivery"], data["work"]["profiles"])
        self.assertEqual([], data["decisions"])
        self.assertEqual([], data["evidence"])
        self.assertEqual("", data["work"]["owner"])
        self.assertEqual("blocked", pack.pack_report(data, target, self.standard)["status"])
        doc = pack.read_yaml(manifest_path)["documents"][0]
        metadata, _ = pack.split_frontmatter((target / doc["artifact_path"]).read_text())
        self.assertEqual("draft", metadata["status"])
        self.assertEqual("WORK-2", metadata["work_id"])
        original = manifest_path.read_bytes()
        with self.assertRaises(pack.PackError): pack.init_pack(target, "WORK-3", "task", [], False, self.standard, self.root)
        self.assertEqual(original, manifest_path.read_bytes())
        link = self.root / "symlink-parent"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(pack.PackError): pack.init_pack(link / "new", "WORK-3", "task", [], False, self.standard, self.root)

    def test_matrix_determinism_drift_and_cli_strict_json(self) -> None:
        self.assertEqual(0, self.invoke(["--write"])[0])
        matrix = (self.root / pack.MATRIX).read_text()
        self.assertEqual(72, len([line for line in matrix.splitlines() if line.startswith("| STEP")]))
        self.assertEqual(matrix, pack.render_matrix(self.standard, self.root))
        self.assertEqual(0, self.invoke(["--check"])[0])
        self.assertEqual(self.standard, json.loads(self.invoke(["--json"])[1]))
        (self.root / pack.MATRIX).write_text(matrix + "drift")
        self.assertNotEqual(0, self.invoke(["--check"])[0])

    def test_pack_cli_exit_codes_and_no_raw_body(self) -> None:
        data = self.ready()
        manifest = self.pack_root / "pack-manifest.yaml"
        manifest.write_text(yaml.safe_dump(data))
        status, output, _ = self.invoke(["--pack", str(manifest), "--json"])
        self.assertEqual(0, status)
        self.assertEqual("can_review", json.loads(output)["status"])
        self.row(data, "prd")["status"] = "draft"
        manifest.write_text(yaml.safe_dump(data))
        status, output, _ = self.invoke(["--pack", str(manifest), "--json"])
        self.assertEqual(1, status)
        self.assertEqual("blocked", json.loads(output)["status"])
        manifest.write_text("documents: [private-looking-invalid-payload\n")
        status, output, error = self.invoke(["--pack", str(manifest), "--json"])
        self.assertEqual(2, status)
        self.assertNotIn("private-looking-invalid-payload", output + error)
        self.assertEqual("malformed", json.loads(output)["status"])

    def test_untrusted_reference_payload_is_not_echoed(self) -> None:
        data = self.ready()
        data["decisions"][0]["evidence_refs"] = ["private raw payload\nthat is not a reference"]
        data["evidence"][0]["artifact_version"] = "private raw payload\nnot a version"
        report = pack.pack_report(data, self.pack_root, self.standard)
        self.assertEqual("malformed", report["status"])
        self.assertNotIn("private raw payload", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
