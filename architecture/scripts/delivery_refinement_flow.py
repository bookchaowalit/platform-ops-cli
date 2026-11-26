#!/usr/bin/env python3
"""Validate and render static delivery refinement guidance; never infer live readiness."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from html import escape
from pathlib import Path
from typing import Any

from delivery_document_pack import PackError, bounded_read, read_yaml, safe_local, strings, text
from product_delivery_flow import SOURCE_FIELDS, json_compatibility_errors, validate_definition

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
DEFINITION = Path("systems/architecture/delivery-refinement-standard.yaml")
GENERATED = Path("docs/lifecycle/PRODUCT-DELIVERY-REFINEMENT-STEPS.md")
WORKFLOW = Path("systems/architecture/product-delivery-workflow.yaml")
GUIDE = Path("docs/lifecycle/PRODUCT-DELIVERY-REFINEMENT.md")
TEMPLATES = {"context": "templates/document-pack/context-and-cause-assessment.md",
             "step": "templates/document-pack/step-refinement-record.md"}
CONTRACT = "solo-empire.delivery-refinement-standard.v1"
CONTEXT_IDS = frozenset({"outcome", "business-model", "actors", "process", "lifecycle", "scope", "data",
                         "integration", "scale", "experience", "jurisdiction", "constraints", "authority", "measurement"})
PROFILE_IDS = frozenset({"client-service", "internal-ops", "b2b-saas", "consumer-product",
                         "ai-product", "data-product", "regulated-system", "incident-hotfix"})
REVIEW_LEVEL_IDS = frozenset({"exploration", "delivery", "high-impact"})
MAX_ENTRIES = 1000
STEP_FIELDS = {"step_id", "context_refs", "profile_refs", "questions", "evidence_checks",
               "refine_triggers", "exit_criteria"}
DERIVED_FIELDS = ("title", "lane", "owner_role", "reviewer_roles", "lifecycle_stage_id",
                  "acceptance", "approval_required", "approval_authority", "depends_on", "feedback_routes")


class CleanArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's default may echo an unknown argument containing a private payload.
        super().error("arguments_invalid; ใช้ --help เพื่อดู options")


def load_sources(root: Path) -> tuple[dict, dict]:
    """Read fixed workflow and preflight its declared registries with safe boundaries."""
    workflow = read_yaml(safe_local(root, str(WORKFLOW)))
    if not isinstance(workflow, dict) or json_compatibility_errors(workflow):
        raise PackError("source_workflow_not_json_mapping")
    if "refinement_standard" in workflow and workflow["refinement_standard"] != str(DEFINITION):
        raise PackError("source_workflow_refinement_pointer_invalid")
    catalogs = workflow.get("source_catalog")
    if not isinstance(catalogs, dict):
        raise PackError("source_catalog_missing")
    source_data = {}
    for key in SOURCE_FIELDS:
        path = safe_local(root, catalogs.get(key))
        source_data[key] = bounded_read(path) if key == "uiux_flow" else read_yaml(path)
    if validate_definition(workflow, root):
        raise PackError("source_workflow_invalid")
    role_data = source_data["roles"]
    roles = {row["id"]: row.get("name", row["id"]) for row in role_data["roles"]}
    return workflow, roles


def validate_standard(data: Any, root: Path = ROOT) -> list[dict]:
    errors: list[dict] = []
    def fail(code: str, field: str) -> None:
        errors.append({"code": code, "field": field})
    if not isinstance(data, dict) or json_compatibility_errors(data):
        return [{"code": "strict_json_mapping_required", "field": "definition"}]
    for field, expected in (("contract", CONTRACT), ("status", "design-ready"),
                             ("runtime_status", "not-integrated"),
                             ("workflow_source", str(WORKFLOW)), ("guide", str(GUIDE)),
                             ("templates", TEMPLATES)):
        if data.get(field) != expected:
            fail("fixed_value_mismatch", field)
    if type(data.get("version")) is not int or data["version"] != 1:
        fail("version_unsupported", "version")
    try:
        safe_local(root, str(GUIDE))
        for path in TEMPLATES.values():
            safe_local(root, path)
        workflow, _ = load_sources(root)
    except (PackError, OSError, TypeError, KeyError, ValueError, RecursionError):
        fail("source_or_path_invalid", "sources")
        workflow = {"steps": []}
    known_steps = {row["id"] for row in workflow["steps"]}
    def text_field(row: dict, key: str, field: str) -> None:
        if not text(row.get(key)) or len(row[key]) > 10000:
            fail("nonempty_bounded_text_required", field + "." + key)
    def list_field(row: dict, key: str, field: str, *, minimum: int = 1) -> list[str]:
        values = row.get(key)
        if (not strings(values) or len(values) < minimum or len(values) > MAX_ENTRIES
                or any(len(value) > 10000 for value in values)):
            fail("unique_nonempty_text_list_required", field + "." + key)
            return []
        return values
    def rows(field: str, required_keys: set[str]) -> dict:
        entries = data.get(field)
        index = {}
        if not isinstance(entries, list) or not entries or len(entries) > MAX_ENTRIES:
            fail("nonempty_bounded_rows_required", field)
            return index
        for offset, row in enumerate(entries):
            where = f"{field}[{offset}]"
            if not isinstance(row, dict) or set(row) != required_keys:
                fail("row_shape_invalid", where)
                continue
            identifier = row.get("id")
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,99}", identifier):
                fail("identifier_invalid", where + ".id")
                continue
            if identifier in index:
                fail("duplicate_identifier", where + ".id")
            index[identifier] = row
        return index
    dimensions = rows("context_dimensions", {"id", "prompt"})
    if set(dimensions) != CONTEXT_IDS or len(dimensions) != 14:
        fail("context_dimensions_v1_required", "context_dimensions")
    for offset, row in enumerate(dimensions.values()):
        text_field(row, "prompt", f"context_dimensions[{offset}]")
    profiles = rows("case_profiles", {"id", "focus", "evidence_expectations"})
    if set(profiles) != PROFILE_IDS or len(profiles) != 8:
        fail("case_profiles_v1_required", "case_profiles")
    for offset, row in enumerate(profiles.values()):
        text_field(row, "focus", f"case_profiles[{offset}]")
        list_field(row, "evidence_expectations", f"case_profiles[{offset}]")
    levels = rows("review_levels", {"id", "when", "required_review"})
    if set(levels) != REVIEW_LEVEL_IDS:
        fail("review_levels_v1_required", "review_levels")
    for offset, row in enumerate(levels.values()):
        text_field(row, "when", f"review_levels[{offset}]")
        text_field(row, "required_review", f"review_levels[{offset}]")
    list_field(data, "invariants", "definition", minimum=10)
    entries = data.get("steps")
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        fail("steps_list_invalid", "steps")
        return errors
    mapped = []
    for offset, row in enumerate(entries):
        where = f"steps[{offset}]"
        if not isinstance(row, dict) or set(row) != STEP_FIELDS:
            fail("step_shape_invalid_no_duplicated_runtime_fields", where)
            continue
        step_id = row.get("step_id")
        if not isinstance(step_id, str) or step_id not in known_steps:
            fail("step_reference_unknown", where + ".step_id")
            continue
        mapped.append(step_id)
        for field, allowed in (("context_refs", CONTEXT_IDS), ("profile_refs", PROFILE_IDS)):
            refs = list_field(row, field, where)
            if not set(refs).issubset(allowed):
                fail("reference_unknown", where + "." + field)
        for field, prefix, keys, minimum in (("questions", "Q", {"id", "prompt"}, 3),
                                            ("evidence_checks", "C", {"id", "method", "required_proof"}, 2)):
            checks = row.get(field)
            if not isinstance(checks, list) or len(checks) < minimum or len(checks) > MAX_ENTRIES:
                fail("question_or_check_count_invalid", where + "." + field)
                continue
            identifiers = set()
            for position, check in enumerate(checks):
                location = f"{where}.{field}[{position}]"
                if not isinstance(check, dict) or set(check) != keys:
                    fail("question_or_check_shape_invalid", location)
                    continue
                identifier = check.get("id")
                pattern = re.escape(step_id) + r"\." + prefix + r"(?:0[1-9]|[1-9][0-9])"
                if not isinstance(identifier, str) or re.fullmatch(pattern, identifier) is None:
                    fail("question_or_check_identifier_invalid", location + ".id")
                    continue
                if identifier in identifiers:
                    fail("question_or_check_identifier_duplicate", location + ".id")
                identifiers.add(identifier)
                for key in keys - {"id"}:
                    text_field(check, key, location)
        for field in ("refine_triggers", "exit_criteria"):
            list_field(row, field, where, minimum=2)
    if len(mapped) != 72 or len(mapped) != len(set(mapped)) or set(mapped) != known_steps:
        fail("all_72_steps_exactly_once_required", "steps")
    return errors


def export_view(data: dict, root: Path = ROOT, role: str | None = None) -> dict:
    workflow, roles = load_sources(root)
    if role is not None and role not in roles:
        raise PackError("role_unknown")
    records = {row["step_id"]: row for row in data["steps"]}
    exported = copy.deepcopy(data)
    selected = []
    for source in workflow["steps"]:
        if role and role != source["owner_role"] and role not in source["reviewer_roles"]:
            continue
        row = copy.deepcopy(records[source["id"]])
        row.update({field: copy.deepcopy(source[field]) for field in DERIVED_FIELDS})
        for field in ("primary_tools", "supporting_tools"):
            row[field] = [{"id": tool_id, **copy.deepcopy(workflow["tools"][tool_id])}
                          for tool_id in source[field]]
        selected.append(row)
    exported["steps"] = selected
    exported["source_workflow"] = {"path": str(WORKFLOW), "contract": workflow["contract"],
        "version": workflow["version"], "runtime_status": workflow["runtime_status"],
        "source_catalog": copy.deepcopy(workflow["source_catalog"]),
        "step_ids": [row["id"] for row in workflow["steps"]]}
    exported["view"] = {"role": role, "partial": role is not None, "scope": "owner_or_reviewer" if role else "all_steps",
                        "matching_steps": len(selected), "total_steps": 72}
    exported["verification_basis"] = "static_definitions_and_repository_sources"
    exported["authority"] = {"content_truth": "not_verified", "live_readiness": "not_verified",
                             "business_acceptance": "not_verified", "release_authorization": "not_verified"}
    return exported


def render_markdown(data: dict, root: Path = ROOT, role: str | None = None) -> str:
    view = export_view(data, root, role)
    dimensions = {row["id"]: row["prompt"] for row in data["context_dimensions"]}
    profiles = {row["id"]: row for row in data["case_profiles"]}
    lines = ["---", "title: Product Delivery Refinement Steps",
        "tags: [product-delivery, refinement, context, evidence]", "created: 2026-10-13", "updated: 2026-10-13",
        "status: generated", "description: Static context questions and evidence refinement for72 delivery steps.",
        "---", "", "# Product Delivery Refinement Steps", "",
        "สร้างจาก `systems/architecture/delivery-refinement-standard.yaml` และ canonical workflow ด้วย",
        "`task run -- scripts/delivery_refinement_flow.py --write`; ห้ามแก้ generated document ด้วยมือ", "",
        "นี่คือคำถาม วิธีตรวจ และเกณฑ์ผ่านที่กำหนดไว้ ไม่ใช่ผลตรวจ ไม่รับรอง content truth, live readiness,",
        "business acceptance หรือ release authorization (`runtime_status: not-integrated`)", "",
        "อ่าน [หลัก refinement](PRODUCT-DELIVERY-REFINEMENT.md), [flow72ขั้น](PRODUCT-DELIVERY-STEPS.md),",
        "[context template](../../templates/document-pack/context-and-cause-assessment.md) และ",
        "[step record](../../templates/document-pack/step-refinement-record.md)", ""]
    if role:
        lines += [f"มุมมองบางส่วนของ `{role}`: ขั้นที่เป็น owner หรือ reviewer; dependency อื่นอยู่ใน definition ฉบับเต็ม", ""]
    lines += [f"จำนวนขั้นในมุมมองนี้: {len(view['steps'])}", "", "## Invariants", ""]
    lines.extend(f"- {value}" for value in data["invariants"])
    lines += ["", "## ระดับการทบทวน", ""]
    for level in data["review_levels"]:
        lines += [f"- `{level['id']}` — เมื่อ: {level['when']}; review: {level['required_review']}"]
    lines.append("")
    def items(label: str, values: list[str]) -> None:
        lines.extend([f"**{label}**", ""])
        lines.extend([f"- {value}" for value in values] if values else ["- ไม่มี"])
        lines.append("")
    for row in view["steps"]:
        lines += [f'<a id="{escape(row["step_id"].lower(), quote=True)}"></a>', "",
                  f"## {row['step_id']} — {row['title']}", "",
                  f"Owner: `{row['owner_role']}`; reviewers: " + ", ".join(f"`{r}`" for r in row["reviewer_roles"]), "",
                  f"Lane: `{row['lane']}`; lifecycle: `{row['lifecycle_stage_id']}`", ""]
        for field, label in (("primary_tools", "เครื่องมือหลักที่แนะนำ"), ("supporting_tools", "เครื่องมือสนับสนุนที่แนะนำ")):
            items(label, [f"{tool['name']} (`{tool['id']}`) — {tool['purpose']}" for tool in row[field]])
        items("บริบทที่ต้องประเมิน", [f"`{ref}` — {dimensions[ref]}" for ref in row["context_refs"]])
        profile_lines = []
        for ref in row["profile_refs"]:
            profile = profiles[ref]
            profile_lines.append(f"`{ref}` — {profile['focus']}; หลักฐานที่คาดหวัง: " + "; ".join(profile["evidence_expectations"]))
        items("กรณีที่ต้องพิจารณา", profile_lines)
        items("คำถามก่อนเดินต่อ", [f"`{q['id']}` — {q['prompt']}" for q in row["questions"]])
        items("วิธีตรวจและหลักฐานที่ต้องได้", [f"`{c['id']}` — วิธี: {c['method']}; proof: {c['required_proof']}" for c in row["evidence_checks"]])
        items("เงื่อนไขส่งกลับ refinement", row["refine_triggers"])
        items("เกณฑ์ออกจาก refinement", row["exit_criteria"])
        items("เกณฑ์ยอมรับจาก canonical workflow", row["acceptance"])
        lines += [f"ต้องอนุมัติ: {'ใช่' if row['approval_required'] else 'ไม่เพิ่มจาก workflow'}; authority: {row['approval_authority']}", ""]
        items("Dependencies", [f"`{ref}`" for ref in row["depends_on"]])
        items("Feedback routes จาก canonical workflow", [f"`{ref}`" for ref in row["feedback_routes"]])
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    parser = CleanArgumentParser(description="ตรวจและ render delivery refinement definition แบบ offline")
    modes = parser.add_mutually_exclusive_group()
    for mode in ("check", "write", "json", "markdown"):
        modes.add_argument(f"--{mode}", action="store_true")
    parser.add_argument("--role", help="known atomic role สำหรับ owner/reviewer view")
    args = parser.parse_args(argv)
    if args.role and not (args.json or args.markdown):
        parser.error("--role ใช้กับ --json หรือ --markdown เท่านั้น")
    errors: list[dict] = []
    try:
        data = read_yaml(safe_local(root, str(DEFINITION)))
        errors = validate_standard(data, root)
        if errors:
            raise PackError("definition_invalid")
        if args.role:
            _, roles = load_sources(root)
            if args.role not in roles:
                errors = [{"code": "role_unknown", "field": "role"}]
                raise PackError("role_unknown")
        if args.json:
            print(json.dumps(export_view(data, root, args.role), ensure_ascii=False, indent=2, allow_nan=False))
        elif args.markdown:
            print(render_markdown(data, root, args.role), end="")
        elif args.write:
            from _env import refuse_snapshot_write  # noqa: E402
            refuse_snapshot_write("delivery_refinement_flow", root)
            target = safe_local(root, str(GENERATED), exists=False)
            if not target.parent.is_dir():
                raise PackError("generated_parent_missing")
            target.write_text(render_markdown(data, root), encoding="utf-8")
            print("สร้าง refinement72ขั้นแล้ว; static definition ไม่ใช่ผลตรวจจริง")
        else:
            if bounded_read(safe_local(root, str(GENERATED))) != render_markdown(data, root):
                errors = [{"code": "generated_drift", "field": "generated"}]
                raise PackError("generated_drift")
            print("delivery refinement: passed; static72steps; runtime_status=not-integrated; authority=not_verified")
        return 0
    except (PackError, OSError, TypeError, KeyError, ValueError, RecursionError):
        errors = errors[:100] or [{"code": "source_schema_or_path_invalid", "field": "definition"}]
        if args.json:
            print(json.dumps({"contract": "solo-empire.delivery-refinement-check.v1", "status": "invalid",
                "errors": errors, "runtime_status": "not-integrated", "authority": "not_verified"},
                ensure_ascii=False, indent=2, allow_nan=False))
        else:
            print("delivery refinement ไม่ผ่าน: " + ", ".join(row["code"] for row in errors), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
