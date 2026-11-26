#!/usr/bin/env python3
"""Read validated static delivery guidance; never infer runtime state or authority."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from delivery_document_pack import (CONTROLS, PackError, bounded_bytes, bounded_read,
    ensure_path_boundary, parse_yaml, safe_local, strings, text,
    validate_standard as validate_documents)
from delivery_refinement_flow import (DEFINITION as REFINEMENT, TEMPLATES as REFINEMENT_TEMPLATES,
    export_view as refinement_view, load_sources, validate_standard as validate_refinement)
from product_delivery_flow import json_compatibility_errors

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
CONTRACT = "solo-empire.delivery-context.v1"
GENERATED = "docs/lifecycle/PRODUCT-DELIVERY-ROLE-MAP.md"
WORKFLOW = "systems/architecture/product-delivery-workflow.yaml"
DOCUMENTS = "systems/architecture/delivery-document-standard.yaml"
ROLES = "docs/registries/ROLE-CATALOG.yaml"
COVERAGE = "docs/registries/ROLE-COVERAGE.yaml"
POSITIONS = "docs/registries/POSITION-COVERAGE.yaml"
TEMPLATES = "templates/TEMPLATE-REGISTRY.yaml"
WORKFLOW_SOURCES = {
    "lifecycle": "docs/lifecycle/DEVELOPMENT-LIFECYCLE.yaml", "roles": ROLES,
    "role_work_packages": "systems/architecture/book-dev-role-step-matrix.yaml",
    "uiux_flow": "docs/lifecycle/UIUX-DESIGN-DELIVERY-FLOW.md"}
GUIDES = (
    "docs/lifecycle/PRODUCT-DELIVERY-FLOW.md", "docs/lifecycle/PRODUCT-DELIVERY-STEPS.md",
    "docs/lifecycle/PRODUCT-DELIVERY-INTERFACE-CONTRACT.md",
    "docs/lifecycle/PRODUCT-DELIVERY-DOCUMENT-STANDARD.md",
    "docs/lifecycle/PRODUCT-DELIVERY-DOCUMENT-MATRIX.md",
    "docs/lifecycle/PRODUCT-DELIVERY-REFINEMENT.md",
    "docs/lifecycle/PRODUCT-DELIVERY-REFINEMENT-STEPS.md",
    "docs/lifecycle/PRODUCT-DELIVERY-TOOLS-ADOPTION.md", WORKFLOW_SOURCES["uiux_flow"])
SOURCE_PATHS = tuple(dict.fromkeys((WORKFLOW, DOCUMENTS, str(REFINEMENT), ROLES,
    COVERAGE, POSITIONS, TEMPLATES, *WORKFLOW_SOURCES.values())))
VIEWS = ("overview", "role", "workflow", "documents", "refinement", "artifact")
AUTHORITY = dict.fromkeys(("content_truth", "live_readiness", "business_acceptance",
                          "release_authorization"), "not_verified")
MAX_ROWS = 1000
MAX_LIMIT = 16000
GROUP_IDS = frozenset({"business-analyst", "client-discovery-and-proposals", "lead-research-and-crm",
    "content-distribution", "ai-product-delivery", "data-engineering", "security-and-privacy",
    "engagement-delivery", "operations-platform", "finance-and-legal"})


class ContextError(PackError):
    pass


class ArtifactUnavailable(ContextError):
    pass


class CleanParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ContextError("arguments_invalid")


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ContextError(code)


def indexed(data: dict, field: str) -> dict[str, dict]:
    rows = data.get(field)
    require(isinstance(rows, list) and 0 < len(rows) <= MAX_ROWS, "registry_rows_invalid")
    result: dict[str, dict] = {}
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get("id"), str)
                and re.fullmatch(r"[a-z][a-z0-9-]{0,99}", row["id"]) is not None,
                "registry_identifier_invalid")
        require(row["id"] not in result, "registry_identifier_duplicate")
        result[row["id"]] = row
    return result


def artifact_id(path: str, owner: str = "architecture-docs") -> str:
    return "artifact-" + hashlib.sha256((owner + ":" + path).encode("utf-8")).hexdigest()[:32]


def reference(path: str, owner: str = "architecture-docs", available: bool = True) -> dict:
    return {"artifact_id": artifact_id(path, owner), "path": path,
            "owner_repository": owner, "availability": "available" if available else "unavailable"}


def load_context(root: Path = ROOT) -> dict:
    """Preflight every source before existing validators may reopen fixed files."""
    ensure_path_boundary(root)
    sources, loaded, source_bytes = [], {}, {}
    for path in SOURCE_PATHS:
        raw = bounded_bytes(safe_local(root, path))
        source_bytes[path] = raw
        if path.endswith(".yaml"):
            data = parse_yaml(raw.decode("utf-8"))
            require(isinstance(data, dict) and not json_compatibility_errors(data),
                    "source_strict_json_required")
            loaded[path] = data
            version = data.get("version")
            require(type(version) is int and version > 0, "source_version_invalid")
        else:
            raw.decode("utf-8")
            version = "sha256:" + hashlib.sha256(raw).hexdigest()
        sources.append({"path": path, "version": version,
                        "sha256": hashlib.sha256(raw).hexdigest()})
    workflow, documents, refinement = loaded[WORKFLOW], loaded[DOCUMENTS], loaded[str(REFINEMENT)]
    require(workflow.get("source_catalog") == WORKFLOW_SOURCES, "workflow_source_pointer_invalid")
    require(workflow.get("document_standard") == DOCUMENTS
            and workflow.get("refinement_standard") == str(REFINEMENT), "standard_pointer_invalid")
    require(documents.get("workflow_definition") == WORKFLOW
            and documents.get("roles_source") == ROLES, "document_source_pointer_invalid")
    # These validators are used only after safe, duplicate-free, strict-JSON preflight.
    load_sources(root)
    require(not validate_documents(documents, root), "document_standard_invalid")
    require(not validate_refinement(refinement, root), "refinement_standard_invalid")
    registries = ((ROLES, "atomic-role-catalog"), (COVERAGE, "role-group-coverage-registry"),
                  (POSITIONS, "position-coverage-registry"), (TEMPLATES, "reusable-template-catalog"))
    for path, kind in registries:
        require(loaded[path].get("kind") == kind and loaded[path].get("version") == 1,
                "registry_contract_invalid")
    require(loaded[ROLES].get("role_groups_registry") == COVERAGE
            and loaded[ROLES].get("template_coverage_registry") == POSITIONS
            and loaded[COVERAGE].get("atomic_role_catalog") == ROLES
            and loaded[POSITIONS].get("canonical_role_catalog") == ROLES,
            "registry_source_pointer_invalid")
    roles, groups, positions = (indexed(loaded[ROLES], "roles"),
        indexed(loaded[COVERAGE], "role_groups"), indexed(loaded[POSITIONS], "positions"))
    require(set(groups) == GROUP_IDS, "group_registry_v1_required")
    require(set(roles) == set(positions), "role_position_bijection_required")
    manifest: dict[str, dict] = {}
    # The shared lookup (_env.KNOWLEDGE_ROOT knows the store-snapshot layout) for the default
    # document root; an explicit other root keeps its own sibling.
    import _env  # noqa: E402

    configured_knowledge = os.environ.get("SOLO_KNOWLEDGE_ROOT")
    if configured_knowledge:
        knowledge_root = Path(configured_knowledge)
    elif Path(root).resolve() == _env.PROJECT_ROOT.resolve():
        knowledge_root = _env.KNOWLEDGE_ROOT
    else:
        knowledge_root = Path(root).parent / "knowledge-base"
    knowledge_safe = False
    try:
        ensure_path_boundary(knowledge_root)
        knowledge_safe = knowledge_root.is_dir()
    except PackError:
        pass

    def register(path: Any, *, template: bool = False) -> dict:
        require(isinstance(path, str) and path.endswith(".md"), "artifact_path_invalid")
        if template:
            require(path.startswith("templates/"), "template_path_invalid")
        safe_local(root, path)  # Existence and every symlink/credential boundary, before body reads.
        ref = reference(path)
        manifest[ref["artifact_id"]] = ref
        return copy.deepcopy(ref)

    for path in GUIDES:
        register(path)
    if safe_local(root, GENERATED, exists=False).is_file():
        register(GENERATED)
    registered_templates: set[str] = set()
    for field in ("position_templates", "shared_markdown_templates"):
        rows = loaded[TEMPLATES].get(field)
        require(isinstance(rows, list) and len(rows) <= MAX_ROWS, "template_registry_invalid")
        seen: set[str] = set()
        for row in rows:
            require(isinstance(row, dict) and text(row.get("path")), "template_registry_invalid")
            require(row["path"] not in seen, "template_registry_duplicate")
            seen.add(row["path"])
            registered_templates.add(row["path"])
            if field == "position_templates":
                require(row.get("position") in roles
                        and row["path"] in positions[row["position"]].get("templates", []),
                        "position_template_membership_mismatch")
    for group_id, group in groups.items():
        require(text(group.get("name")) and group.get("maturity") in
                {"not-started", "partial", "pilot-ready", "operational", "battle-tested"},
                "group_metadata_invalid")
        require(strings(group.get("subprocesses"), empty=False)
                and strings(group.get("required_templates"), empty=False), "group_process_invalid")
        for field, leaf in (("charter", "README.md"), ("process", "PROCESS.md"),
                            ("process_register", "PROCESS-REGISTER.md")):
            require(group.get(field) == f"roles/{group_id}/{leaf}", "group_reference_invalid")
            available = False
            if knowledge_safe:
                try:
                    safe_local(knowledge_root, group[field])
                    available = True
                except PackError:
                    pass
            ref = reference(group[field], "knowledge-base", available)
            manifest[ref["artifact_id"]] = ref
        for path in group["required_templates"]:
            register(path, template=True)
    doc_refs = {row["id"]: register(row["template"], template=True)
                for row in documents["documents"]}
    refinement_refs = {key: register(path, template=True)
                       for key, path in REFINEMENT_TEMPLATES.items()}
    records, declared_position_templates = [], set()
    for role_id, role in roles.items():
        position = positions[role_id]
        require(all(text(role.get(field)) for field in ("name", "category", "summary")),
                "role_metadata_invalid")
        membership = role.get("role_groups")
        require(strings(membership, empty=False) and set(membership) <= set(groups),
                "role_group_membership_invalid")
        require(position.get("role_groups") == membership
                and position.get("name") == role["name"] and position.get("category") == role["category"],
                "position_membership_mismatch")
        require(position.get("status") in {"dedicated-package", "covered-by-role-family"}
                and text(position.get("primary_owner"))
                and strings(position.get("templates"), empty=False), "position_metadata_invalid")
        templates = []
        for path in position["templates"]:
            templates.append(register(path, template=True))
            if path.startswith("templates/positions/"):
                declared_position_templates.add(path)
        owner_steps = [step["id"] for step in workflow["steps"] if step["owner_role"] == role_id]
        reviewer_steps = [step["id"] for step in workflow["steps"] if role_id in step["reviewer_roles"]]
        direct = bool(owner_steps or reviewer_steps)
        family = [{"id": group_id, "name": groups[group_id]["name"],
            "declared_maturity": groups[group_id]["maturity"],
            "subprocesses": copy.deepcopy(groups[group_id]["subprocesses"]),
            **{field: copy.deepcopy(manifest[artifact_id(groups[group_id][field], "knowledge-base")])
               for field in ("charter", "process", "process_register")},
            "templates": [reference(path) for path in groups[group_id]["required_templates"]]}
            for group_id in membership]
        records.append({"id": role_id, "name": role["name"], "category": role["category"],
            "summary": role["summary"], "coverage_kind": "dedicated" if
                position["status"] == "dedicated-package" else "family",
            "primary_owner": position["primary_owner"], "groups": family,
            "position_templates": templates, "owner_step_ids": owner_steps,
            "reviewer_step_ids": reviewer_steps, "specialist_refinement_status":
                "direct-delivery-step-questions" if direct else "family-process-only",
            "document_standard": {"path": DOCUMENTS, "version": documents["version"]},
            "work_controls": {identifier: doc_refs[identifier] for identifier in sorted(CONTROLS)},
            "refinement_templates": refinement_refs,
            "limitations": ["Static guidance; content truth and live authority are not verified",
                "Family policy sources are optional; pilot, client, diary and private records are not read",
                "Mapped family subprocesses do not establish specialist step-by-step refinement"
                    if not direct else "Questions apply only to the directly mapped delivery steps"]})
    catalog_positions = {row["path"] for row in loaded[TEMPLATES]["position_templates"]}
    require(declared_position_templates == catalog_positions, "position_template_catalog_mismatch")
    require(all(path in registered_templates for path in REFINEMENT_TEMPLATES.values()),
            "refinement_template_unregistered")
    # Reject changes during validation rather than mixing versions in the provenance envelope.
    require(all(bounded_bytes(safe_local(root, path)) == raw for path, raw in source_bytes.items()),
            "source_changed_during_read")
    return {"sources": sources, "roles": records, "workflow": workflow,
            "documents": documents, "refinement": refinement, "artifacts": manifest,
            "document_template_refs": doc_refs,
            "roots": {"architecture-docs": root, "knowledge-base": knowledge_root}}


def selected_steps(context: dict, role: str | None, step: str | None) -> list[dict]:
    require(role is None or (isinstance(role, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,99}", role)),
            "role_identifier_invalid")
    require(step is None or (isinstance(step, str)
            and re.fullmatch(r"(?:PD|BA|SA|UX|DEV|QA|REL|OPS)[0-9]{2}", step)), "step_identifier_invalid")
    roles = {row["id"] for row in context["roles"]}
    steps = context["workflow"]["steps"]
    require(role is None or role in roles, "role_unknown")
    require(step is None or step in {row["id"] for row in steps}, "step_unknown")
    selected = [row for row in steps if (step is None or row["id"] == step)
        and (role is None or row["owner_role"] == role or role in row["reviewer_roles"])]
    require(not (role and step and not selected), "filter_conflict")
    return selected


def counts(context: dict) -> dict:
    roles = context["roles"]
    return {"roles": len(roles), "dedicated": sum(r["coverage_kind"] == "dedicated" for r in roles),
        "family": sum(r["coverage_kind"] == "family" for r in roles),
        "roles_with_direct_delivery_steps": sum(bool(r["owner_step_ids"] or r["reviewer_step_ids"]) for r in roles),
        "roles_with_family_process_only": sum(not(r["owner_step_ids"] or r["reviewer_step_ids"]) for r in roles),
        "delivery_steps": len(context["workflow"]["steps"]),
        "role_groups": len({g["id"] for r in roles for g in r["groups"]})}


def export(context: dict, root: Path, *, view: str = "overview", role: str | None = None,
           step: str | None = None, artifact: str | None = None,
           offset: int | None = None, limit: int | None = None) -> dict:
    require(view in VIEWS, "view_unknown")
    require((view == "artifact") == (artifact is not None), "artifact_filter_invalid")
    require(view == "artifact" or (offset is None and limit is None), "pagination_filter_invalid")
    require(view not in {"overview", "artifact"} or (role is None and step is None), "view_filter_invalid")
    require(view != "role" or (role is not None and step is None), "role_filter_required")
    filters = {"role": role, "step": step, "artifact_id": artifact, "offset": None, "limit": None}
    if view == "artifact":
        # Lookup precedes any file access; callers never supply a path.
        require(isinstance(artifact, str) and artifact in context["artifacts"], "artifact_unknown")
        start, size = 0 if offset is None else offset, 8000 if limit is None else limit
        require(type(start) is int and start >= 0 and type(size) is int and 1 <= size <= MAX_LIMIT,
                "pagination_invalid")
        ref = context["artifacts"][artifact]
        if ref["availability"] != "available":
            raise ArtifactUnavailable("artifact_unavailable")
        artifact_root = context["roots"][ref["owner_repository"]]
        ensure_path_boundary(artifact_root)
        raw = bounded_bytes(safe_local(artifact_root, ref["path"]))
        body = raw.decode("utf-8")
        require(start <= len(body), "offset_out_of_range")
        end = min(start + size, len(body))
        digest = hashlib.sha256(raw).hexdigest()
        filters.update(offset=start, limit=size)
        data = {**ref, "source": ref["path"], "version": "sha256:" + digest,
                "sha256": digest, "body": body[start:end], "offset": start,
                "limit": size, "total": len(body), "unit": "unicode-codepoint",
                "truncated": end < len(body), "next_offset": end if end < len(body) else None}
    else:
        steps = selected_steps(context, role, step)
        selected_ids = {row["id"] for row in steps}
        role_record = next((r for r in context["roles"] if r["id"] == role), None)
        if view == "overview":
            compact_roles = [{key: copy.deepcopy(record[key]) for key in
                ("id", "name", "category", "summary", "coverage_kind", "position_templates",
                 "owner_step_ids", "reviewer_step_ids", "specialist_refinement_status")}
                | {"group_ids": [group["id"] for group in record["groups"]]}
                for record in context["roles"]]
            groups = {group["id"]: group for record in context["roles"] for group in record["groups"]}
            data = {"counts": counts(context), "roles": compact_roles, "groups": list(groups.values()),
                    "guides": [reference(path) for path in GUIDES],
                    "limitations": ["Definitions and registered local templates only",
                        "Family processes have only declared maturity; specialist content truth and live runtime are not verified"]}
        elif view == "role":
            data = role_record
        elif view == "workflow":
            data = copy.deepcopy(context["workflow"])
            data.update(steps=copy.deepcopy(steps), partial_view=bool(role or step), role_context=role_record)
        elif view == "documents":
            data = copy.deepcopy(context["documents"])
            data.update(step_documents=[row for row in data["step_documents"] if row["step_id"] in selected_ids],
                partial_view=bool(role or step), role_context=role_record,
                template_refs=context["document_template_refs"])
        else:
            data = refinement_view(context["refinement"], root, role)
            data.update(steps=[row for row in data["steps"] if row["step_id"] in selected_ids],
                        partial_view=bool(role or step), role_context=role_record)
            data["view"].update(step=step, partial=bool(role or step),
                                matching_steps=len(data["steps"]))
            if step:
                data["view"]["scope"] = "owner_or_reviewer_and_step" if role else "single_step"
            require(all(hashlib.sha256(bounded_bytes(safe_local(root, source["path"]))).hexdigest()
                        == source["sha256"] for source in context["sources"]), "source_changed_during_read")
    return {"contract": CONTRACT, "status": "ready",
        "capabilities": {"definition_available": True, "runtime_integrated": False},
        "authority": AUTHORITY.copy(), "sources": copy.deepcopy(context["sources"]),
        "view": view, "filters": filters, "data": data}


def render_role_map(context: dict) -> str:
    total = counts(context)
    lines = ["---", "title: Product Delivery Role Map", "status: generated",
        "tags: [product-delivery, roles, context, templates]", "created: 2026-10-13",
        "updated: 2026-10-13", "description: Generated truthful role coverage and static delivery context map.",
        "---", "", "# Product Delivery Role Map", "",
        "สร้างด้วย `task run -- scripts/delivery_context.py --write`; ห้ามแก้ generated document ด้วยมือ", "",
        f"มี {total['roles']} roles: dedicated {total['dedicated']}, family {total['family']}; "
        f"{total['roles_with_direct_delivery_steps']} roles เป็น owner/reviewer ใน flow72ขั้น และ "
        f"{total['roles_with_family_process_only']} roles อ้างอิง family process โดยไม่มีขั้นตรงใน flowนี้", "",
        "ทุก role มี template และ role-group references แต่ไม่ได้หมายความว่าทุก specialist มีคำถาม refinement รายขั้นครบแล้ว", "",
        "Maturity ด้านล่างเป็นค่าที่ registry ประกาศ; ไม่ยืนยัน content truth, live readiness,",
        "business acceptance หรือ release authorization. `runtime_integrated: false`", "",
        "อ่าน [flow](PRODUCT-DELIVERY-FLOW.md), [document standard](PRODUCT-DELIVERY-DOCUMENT-STANDARD.md),",
        "[refinement](PRODUCT-DELIVERY-REFINEMENT.md), [tool adoption](PRODUCT-DELIVERY-TOOLS-ADOPTION.md),",
        "[role catalog](../registries/ROLE-CATALOG.yaml) และ",
        "[position coverage](../registries/POSITION-COVERAGE.yaml)", "",
        "## Role coverage", "",
        "| Role | Package | Owner steps | Reviewer steps | Refinement coverage | Groups / declared maturity | Templates |",
        "|---|---|---|---|---|---|---|"]
    def md(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ")
    for role in context["roles"]:
        groups = "; ".join(f"`{g['id']}` ({g['declared_maturity']})" for g in role["groups"])
        templates = "; ".join(f"[{Path(ref['path']).stem}](../../{ref['path']})" for ref in role["position_templates"])
        lines.append(f"| `{role['id']}` — {md(role['name'])} | {role['coverage_kind']} | "
            f"{', '.join(role['owner_step_ids']) or 'ไม่มี'} | {', '.join(role['reviewer_step_ids']) or 'ไม่มี'} | "
            f"{role['specialist_refinement_status']} | {groups} | {templates} |")
    lines += ["", "## Family process references", "",
        "Charter/process/register อยู่ใน knowledge-base; exporter อ่านได้เฉพาะ30 policy paths ที่ registry ระบุ "
        "เมื่อ checkout พร้อมใช้งาน. ไม่อ่าน pilot/client evidence, diary หรือไฟล์อื่นตามลิงก์", "",
        "| Group | Charter / process / register (external reference) | Declared subprocesses | Maturity |",
        "|---|---|---|---|"]
    groups = {g["id"]: g for role in context["roles"] for g in role["groups"]}
    for group in groups.values():
        refs = "; ".join("`knowledge-base:" + group[field]["path"] + "`"
                          for field in ("charter", "process", "process_register"))
        lines.append(f"| `{group['id']}` | {refs} | {', '.join(group['subprocesses'])} | {group['declared_maturity']} |")
    lines += ["", "## Shared work controls and refinement", "",
        "ทุก role ใช้ work-control4ชนิดร่วมกัน พร้อมเอกสารหลัก15ชนิดและ profile ตามบริบท. "
        "การอ้างอิงแม่แบบไม่ใช่หลักฐานว่างานผ่านการทบทวน", ""]
    for identifier in sorted(CONTROLS):
        ref = context["document_template_refs"][identifier]
        lines.append(f"- [{identifier}](../../{ref['path']})")
    for identifier, path in REFINEMENT_TEMPLATES.items():
        lines.append(f"- [{identifier} refinement template](../../{path})")
    lines += ["", "## Read-only tool entrypoint", "", "```bash",
        "task run -- scripts/delivery_context.py --json --view overview",
        "task run -- scripts/delivery_context.py --json --view role --role product-manager",
        "task run -- scripts/delivery_context.py --json --view refinement --step PD01",
        "```", "", "JSON contract `solo-empire.delivery-context.v1` มี source version/hash, "
        "capability และ authority limits. Artifact view อ่านเฉพาะ registered local ID และแบ่งหน้า Unicode codepoint. "
        "เครื่องมือนี้ไม่เขียนสถานะ runtime หรืออนุมัติงาน"]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    json_mode = "--json" in arguments
    parser = CleanParser(description="อ่าน static delivery context ตาม role/step/artifact แบบ offline")
    modes = parser.add_mutually_exclusive_group()
    for mode in ("check", "write", "json"):
        modes.add_argument("--" + mode, action="store_true")
    parser.add_argument("--view", choices=VIEWS)
    parser.add_argument("--role")
    parser.add_argument("--step")
    parser.add_argument("--artifact-id")
    parser.add_argument("--offset", type=int)
    parser.add_argument("--limit", type=int)
    context = None
    args = None
    try:
        args = parser.parse_args(arguments)
        modifiers = (args.view, args.role, args.step, args.artifact_id, args.offset, args.limit)
        require(args.json or all(value is None for value in modifiers), "json_mode_required")
        context = load_context(root)
        if args.json:
            result = export(context, root, view=args.view or "overview", role=args.role,
                step=args.step, artifact=args.artifact_id, offset=args.offset, limit=args.limit)
            print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        elif args.write:
            from _env import refuse_snapshot_write  # noqa: E402
            refuse_snapshot_write("delivery_context", root)
            target = safe_local(root, GENERATED, exists=False)
            require(target.parent.is_dir(), "generated_parent_missing")
            target.write_text(render_role_map(context), encoding="utf-8")
            print("สร้าง role map แล้ว; static context ไม่รับรองผลหรือ authority จริง")
        else:
            require(bounded_read(safe_local(root, GENERATED)) == render_role_map(context), "generated_drift")
            print("delivery context: passed; roles and sources validated; runtime_integrated=false; authority=not_verified")
        return 0
    except (PackError, OSError, UnicodeError, TypeError, KeyError, ValueError, RecursionError) as exc:
        code = str(exc) if isinstance(exc, ContextError) else "source_schema_or_path_invalid"
        if json_mode:
            print(json.dumps({"contract": CONTRACT, "status": "unavailable" if isinstance(exc, ArtifactUnavailable) else "invalid",
                "capabilities": {"definition_available": context is not None, "runtime_integrated": False},
                "authority": AUTHORITY.copy(), "sources": context["sources"] if context else [],
                "view": (args.view or "overview") if args else "overview",
                "filters": {"role": None, "step": None, "artifact_id": None, "offset": None, "limit": None},
                "data": None, "errors": [{"code": code}]}, ensure_ascii=False, allow_nan=False))
        else:
            print("delivery context ไม่ผ่าน: " + code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
