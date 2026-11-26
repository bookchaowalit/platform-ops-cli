#!/usr/bin/env python3
"""Offline document accounting; declared records never establish live authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from product_delivery_flow import json_compatibility_errors, validate_definition

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
STANDARD = Path("systems/architecture/delivery-document-standard.yaml")
MATRIX = Path("docs/lifecycle/PRODUCT-DELIVERY-DOCUMENT-MATRIX.md")
MANIFEST = Path("templates/document-pack/pack-manifest.yaml")
CONTRACT = "solo-empire.delivery-document-standard.v1"
PACK_CONTRACT = "solo-empire.delivery-document-pack.v1"
CORE = frozenset({"overview", "prd", "architecture", "nfr-security", "runbook", "srs",
    "ux-flows", "test-uat", "rtm", "business-case", "business-flow", "business-rules-data",
    "trd-api-data", "delivery-raid", "decisions-glossary"})
CONTROLS = frozenset({"work-record", "evidence-register", "feedback-decision-log", "ai-decision-context"})
PROFILES = frozenset({"ai-product", "data-product", "security-sensitive", "client-delivery"})
KINDS = {"full-product", "feature-slice", "hotfix", "task"}
PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|FIXME)\b|\[(?:ต้องยืนยัน|กรอก[^\]]*|ระบุ[^\]]*)\]", re.I)
TEMPLATE_PLACEHOLDER = re.compile(r"\[(?:DOC-ID|WORK-ID[^\]]*|FACT-#+|EV-#+|ASM[^\]]*|statement|safe summary|source refs|refs|scope|roles?|owner|YYYY-MM-DD[^\]]*)\](?!\()", re.I)
MAX_BYTES = 2_000_000
MAX_RECORDS = 1000


class PackError(ValueError):
    """A bounded diagnostic; never include input file contents."""


class SafeUniqueLoader(yaml.SafeLoader):
    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            raise PackError("YAML aliases ไม่รองรับใน document pack")
        return super().compose_node(parent, index)


def _mapping(loader: SafeUniqueLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            if key in result:
                raise PackError("YAML มี mapping key ซ้ำ")
            result[key] = loader.construct_object(value_node, deep=True)
        except TypeError as exc:
            raise PackError("YAML mapping key ไม่ถูกต้อง") from exc
    return result


SafeUniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def strings(value: Any, *, empty: bool = True) -> bool:
    return (isinstance(value, list) and (empty or bool(value))
            and all(text(item) for item in value) and len(set(value)) == len(value))


def identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,199}", value))


def references(value: Any) -> bool:
    return strings(value) and all(identifier(item) for item in value)


def credential_path(path: Path) -> bool:
    """Deny credential-shaped paths even when the file is inside an allowed root."""
    for part in path.parts:
        lower = part.lower()
        if (lower in {".git", "config", ".config", ".ssh", ".aws", ".azure", ".kube", "cookies",
                      "credentials", "secrets", "id_rsa", "id_ed25519", ".netrc", ".npmrc", ".pypirc",
                      "config.json", "config.toml", "config.yaml", "config.yml", "config.ini", "settings.json"}
                or lower.startswith(".env")
                or re.search(r"(?:token|cookie|credential|secret)", lower)
                or lower.endswith((".pem", ".key", ".p12", ".pfx"))):
            return True
    return False


def ensure_path_boundary(path: Path) -> None:
    """Check every component, including the caller's cwd, before any file read."""
    absolute = path.absolute()
    if ".." in path.parts or credential_path(absolute):
        raise PackError("path ละเมิด credential/config boundary หรือ traversal")
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise PackError("path ห้ามผ่าน symlink")


def safe_local(root: Path, value: Any, *, exists: bool = True) -> Path:
    if not text(value):
        raise PackError("local path ต้องไม่ว่าง")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or credential_path(relative):
        raise PackError("local path ละเมิดขอบเขตไฟล์หรือเป็นชื่อ credential")
    base = root.resolve()
    candidate = base / relative
    current = base
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise PackError("local path ห้ามผ่าน symlink")
    if not candidate.resolve().is_relative_to(base):
        raise PackError("local path ต้องอยู่ภายใน pack/repository")
    if exists and not candidate.is_file():
        raise PackError("ไม่พบ local file ที่อ่านได้")
    return candidate


def bounded_bytes(path: Path) -> bytes:
    ensure_path_boundary(path)
    try:
        if path.stat().st_size > MAX_BYTES:
            raise PackError("ไฟล์เกินขนาดที่อนุญาต")
        with path.open("rb") as handle:
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise PackError("ไฟล์เกินขนาดที่อนุญาต")
        return data
    except OSError as exc:
        raise PackError("อ่าน local file ไม่สำเร็จ") from exc


def bounded_read(path: Path) -> str:
    try:
        return bounded_bytes(path).decode("utf-8")
    except UnicodeError as exc:
        raise PackError("local file ต้องเป็น UTF-8") from exc


def parse_yaml(content: str) -> Any:
    try:
        return yaml.load(content, Loader=SafeUniqueLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise PackError("YAML ไม่ถูกต้องหรือซ้อนระดับลึกเกินไป") from exc


def read_yaml(path: Path) -> Any:
    return parse_yaml(bounded_read(path))


def validate_standard(data: Any, root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict) or json_compatibility_errors(data):
        return ["standard ต้องเป็น mapping ที่รองรับ strict JSON"]
    if data.get("contract") != CONTRACT or type(data.get("version")) is not int or data["version"] != 1:
        errors.append("standard contract/version ไม่รองรับ")
    if data.get("status") != "standard":
        errors.append("standard status ต้องเป็น standard")
    for field, frozen in (("core_doc_types", CORE), ("mandatory_work_types", CONTROLS)):
        values = data.get(field)
        if not strings(values, empty=False) or set(values) != frozen:
            errors.append(f"{field} ต้องครบชุด v1 โดยไม่ซ้ำ")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != PROFILES:
        errors.append("profiles ต้องครบชุด v1")
        profiles = {}
    for values in profiles.values():
        if not strings(values, empty=False):
            errors.append("profile document types ต้องเป็นรายการไม่ซ้ำ")
    documents = data.get("documents")
    if not isinstance(documents, list) or not documents or len(documents) > MAX_RECORDS:
        return errors + ["documents ต้องเป็นรายการขนาดจำกัด"]
    doc_index = {}
    try:
        role_data = read_yaml(safe_local(root, data.get("roles_source")))
        roles = {row["id"] for row in role_data["roles"]}
    except (PackError, TypeError, KeyError):
        roles = set()
        errors.append("roles_source อ่านทะเบียนบทบาทไม่ได้")
    for row in documents:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{0,99}", row["id"])):
            errors.append("document definition ต้องมี id")
            continue
        identifier = row["id"]
        if identifier in doc_index:
            errors.append("document id ซ้ำ")
        doc_index[identifier] = row
        category = "core" if identifier in CORE else "work-control" if identifier in CONTROLS else "extension"
        if (row.get("category") != category or not isinstance(row.get("owner_role"), str)
                or row["owner_role"] not in roles):
            errors.append("document category/owner_role ไม่ตรงทะเบียน")
        if not text(row.get("title")) or not strings(row.get("required_content"), empty=False):
            errors.append("document title/required_content ไม่ครบ")
        try:
            template = safe_local(root, row.get("template"))
            if template.suffix != ".md":
                raise PackError("template ต้องเป็น Markdown")
        except PackError:
            errors.append("document template ไม่พบหรือออกนอกขอบเขต")
    extras = set()
    for values in profiles.values():
        if strings(values):
            extras.update(values)
    if set(doc_index) != CORE | CONTROLS | extras or extras & (CORE | CONTROLS):
        errors.append("document catalog/profile references ต้องครบและแยกประเภท")
    try:
        workflow = read_yaml(safe_local(root, data.get("workflow_definition")))
        if validate_definition(workflow, root):
            errors.append("workflow definition ไม่ผ่าน contract/dependency validation")
        steps = {row["id"] for row in workflow["steps"]}
    except (PackError, KeyError, TypeError, ValueError, RecursionError):
        errors.append("workflow_definition อ่านไม่ได้")
        steps = set()
    mappings = data.get("step_documents")
    if not isinstance(mappings, list) or len(mappings) > MAX_RECORDS:
        return errors + ["step_documents ต้องเป็นรายการขนาดจำกัด"]
    mapped = []
    for row in mappings:
        if not isinstance(row, dict) or not text(row.get("step_id")):
            errors.append("step mapping ต้องมี step_id")
            continue
        mapped.append(row["step_id"])
        for field, allowed, allow_empty in (("doc_types", CORE | CONTROLS, False),
                                             ("profile_doc_types", extras, True)):
            values = row.get(field)
            if not strings(values, empty=allow_empty) or not set(values).issubset(allowed):
                errors.append("step mapping มี document/profile reference ไม่ถูกต้อง")
    if len(mapped) != len(set(mapped)) or set(mapped) != steps or len(steps) != 72:
        errors.append("step mapping ต้องครบทุก72ขั้นโดยไม่ซ้ำ")
    return errors


def render_matrix(data: dict, root: Path = ROOT) -> str:
    workflow = read_yaml(safe_local(root, data["workflow_definition"]))
    documents = {row["id"]: row for row in data["documents"]}
    mappings = {row["step_id"]: row for row in data["step_documents"]}
    profiles = data["profiles"]
    lines = ["---", "title: Product Delivery Document Matrix",
        "tags: [product-delivery, document-pack, evidence, traceability]",
        "created: 2026-10-13", "updated: 2026-10-13", "status: generated",
        "description: Generated mapping of72 delivery steps to central document templates.",
        "---", "", "# Product Delivery Document Matrix", "",
        "สร้างจาก `systems/architecture/delivery-document-standard.yaml` ด้วย",
        "`task run -- scripts/delivery_document_pack.py --write` ห้ามแก้ตารางด้วยมือ", "",
        "ทุกงานต้องบัญชีเอกสารหลัก15ชนิดและสร้าง work-control4ชนิด; เอกสาร profile เพิ่มเมื่อเข้าเงื่อนไข", "",
        "อ่าน [มาตรฐาน document pack](PRODUCT-DELIVERY-DOCUMENT-STANDARD.md) และ",
        "[flow72ขั้น](PRODUCT-DELIVERY-STEPS.md) ประกอบ ตารางนี้ระบุชนิดเอกสาร ไม่ยืนยันว่ามีผลงานหรือผ่านการอนุมัติแล้ว", "",
        "| Step | ผู้รับผิดชอบ | เอกสาร/Template | เพิ่มตาม Profile |",
        "|---|---|---|---|"]
    def link(identifier: str) -> str:
        row = documents[identifier]
        return f"[{identifier}](../../{row['template']})"
    for step in workflow["steps"]:
        mapping = mappings[step["id"]]
        conditional = []
        for identifier in mapping["profile_doc_types"]:
            selected = ", ".join(name for name, ids in profiles.items() if identifier in ids)
            conditional.append(f"{link(identifier)} ({selected}; conditional)")
        lines.append(f"| {step['id']} — {step['title'].replace('|', '/')} | `{step['owner_role']}` | "
                     + ", ".join(link(i) for i in mapping["doc_types"])
                     + " | " + ("; ".join(conditional) or "—") + " |")
    lines += ["", "## เอกสารทุกชนิดและเจ้าของ", "", "| ชนิด | ประเภท | เจ้าของ | Template |",
              "|---|---|---|---|"]
    for row in data["documents"]:
        lines.append(f"| `{row['id']}` | {row['category']} | `{row['owner_role']}` | {link(row['id'])} |")
    return "\n".join(lines) + "\n"


def draft_manifest(standard: dict, work_id: str = "", kind: str = "task",
                   profiles: list[str] | None = None, client_facing: bool = False) -> dict:
    profiles = profiles or []
    needed = CORE | CONTROLS | {i for name in profiles for i in standard["profiles"][name]}
    rows = []
    for row in standard["documents"]:
        if row["id"] in needed:
            rows.append({"doc_type": row["id"], "disposition": "create", "status": "draft",
                         "owner": "", "artifact_path": f"documents/{row['id']}.md", "artifact_ref": "",
                         "artifact_version": "1", "artifact_sha256": "", "decision_ref": "", "evidence_refs": [], "reason": ""})
    return {"contract": PACK_CONTRACT, "standard_version": standard["version"], "pack_revision": "1",
            "work": {"id": work_id, "title": "", "kind": kind, "outcome": "", "scope": "", "owner": "",
                     "risk": "", "client_facing": client_facing, "profiles": profiles, "profile_reason": ""},
            "documents": rows, "evidence": [], "decisions": [], "feedback": []}


def stable_ref(value: Any) -> bool:
    # Reuse points to a versioned artifact; no fetch, credential use, or live claim.
    return (text(value) and len(value) <= 1000 and not PLACEHOLDER.search(value)
            and "\n" not in value and not re.search(r"://[^/\s]*@", value)
            and (value.startswith(("urn:", "https://")) or value.startswith("repo:"))
            and not re.search(r"[?&](?:token|key|secret|credential)=", value, re.I))


def split_frontmatter(content: str) -> tuple[dict, str]:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise PackError("document ต้องมี YAML frontmatter")
    for offset, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            metadata = parse_yaml("".join(lines[1:offset]))
            if not isinstance(metadata, dict):
                raise PackError("frontmatter ต้องเป็น mapping")
            return metadata, "".join(lines[offset + 1:])
    raise PackError("frontmatter ไม่ปิด")


def substantive(body: str) -> bool:
    if PLACEHOLDER.search(body) or TEMPLATE_PLACEHOLDER.search(body):
        return False
    content_lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if (not stripped or stripped.startswith("#") or re.fullmatch(r"[|:\-\s]+", stripped)
                or stripped.endswith(":") or stripped == "- [ ]"):
            continue
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) > 1 and not cells[-1]:
                continue
        content_lines.append(stripped)
    return len(content_lines) >= 2 and len(" ".join(content_lines)) >= 80


def pack_report(pack: Any, pack_root: Path, standard: dict) -> dict:
    """Validate structure and declared completion. Never export private document bodies."""
    issues: list[dict] = []
    declared = {"evidence": 0, "decisions": 0, "feedback": 0}
    evidence_projection: list[dict] = []
    decision_projection: list[dict] = []
    coverage: list[dict] = []
    unresolved: list[str] = []
    def issue(code: str, *, doc: str = "", malformed: bool = False) -> None:
        issues.append({"code": code, "doc_type": doc if doc in known else "", "malformed": malformed})
    known = {row["id"] for row in standard["documents"]}
    def result() -> dict:
        blockers = issues[:250]
        return {"contract": "solo-empire.delivery-document-readiness.v1",
                "verification_basis": "local_files_and_declared_records",
                "authority": {"live_authorization": "not_verified", "business_acceptance": "not_verified",
                              "release_authorization": "not_verified"},
                "status": "malformed" if any(i["malformed"] for i in issues) else "blocked" if issues else "can_review",
                "next_action": "blocked" if issues else "can_review", "blockers": blockers,
                "blocker_count": len(issues), "diagnostics_truncated": len(issues) > len(blockers),
                "document_coverage": coverage, "unresolved_feedback": unresolved,
                "declared_records": declared,
                "declared_evidence": evidence_projection, "declared_approvals": decision_projection,
                "ai_decision_context": {"basis": "declared_metadata_and_local_structure",
                    "gaps": sorted({i["code"] for i in issues}),
                    "recommendation": "แก้ blocker และส่งให้ผู้มีอำนาจตรวจ" if issues else "ส่งให้ผู้มีอำนาจตรวจหลักฐานและสิทธิ์จริง",
                    "allowed_action": "recommend_only", "automatic_approval": False}}
    try:
        ensure_path_boundary(pack_root)
    except PackError:
        issue("pack_root_boundary", malformed=True)
        return result()
    if not isinstance(pack, dict) or json_compatibility_errors(pack):
        issue("pack_not_strict_json_mapping", malformed=True)
        return result()
    if (pack.get("contract") != PACK_CONTRACT or type(pack.get("standard_version")) is not int
            or pack["standard_version"] != standard["version"]):
        issue("pack_contract_version", malformed=True)
    if not isinstance(pack.get("pack_revision"), str):
        issue("pack_revision_type", malformed=True)
    elif not text(pack["pack_revision"]):
        issue("pack_revision_missing")
    work = pack.get("work")
    if not isinstance(work, dict):
        issue("work_mapping_missing", malformed=True)
        return result()
    for field in ("id", "title", "outcome", "scope", "owner", "risk", "profile_reason"):
        value = work.get(field)
        if not isinstance(value, str):
            issue("work_field_type", malformed=True)
        elif field != "profile_reason" and (not text(value) or PLACEHOLDER.search(value)):
            issue("work_identity_scope_owner_incomplete")
    if not isinstance(work.get("kind"), str) or work["kind"] not in KINDS:
        issue("work_kind_unknown", malformed=True)
    profiles = work.get("profiles")
    if not strings(profiles) or not set(profiles).issubset(standard["profiles"]):
        issue("profiles_unknown_or_duplicate", malformed=True)
        profiles = []
    if type(work.get("client_facing")) is not bool:
        issue("client_facing_type", malformed=True)
    if work.get("client_facing") and "client-delivery" not in profiles:
        issue("client_delivery_profile_required")
    if profiles and (not text(work.get("profile_reason")) or PLACEHOLDER.search(work["profile_reason"])):
        issue("profile_reason_missing")
    required = CORE | CONTROLS | {i for name in profiles for i in standard["profiles"][name]}
    documents = pack.get("documents")
    if not isinstance(documents, list) or len(documents) > MAX_RECORDS:
        issue("documents_list_invalid", malformed=True)
        return result()
    document_index = {}
    def enum(value: Any, allowed: set) -> bool:
        return isinstance(value, str) and value in allowed
    for row in documents:
        if not isinstance(row, dict) or not enum(row.get("doc_type"), known):
            issue("document_type_unknown", malformed=True)
            continue
        doc = row["doc_type"]
        if doc in document_index:
            issue("duplicate_document_type", doc=doc, malformed=True)
        document_index[doc] = row
        if not enum(row.get("disposition"), {"create", "reuse", "not_applicable"}):
            issue("disposition_unknown", doc=doc, malformed=True)
        if not enum(row.get("status"), {"draft", "in_review", "approved", "superseded"}):
            issue("document_status_unknown", doc=doc, malformed=True)
        for field in ("owner", "artifact_version", "artifact_sha256", "decision_ref", "reason", "artifact_path", "artifact_ref"):
            if not isinstance(row.get(field, ""), str):
                issue("document_field_type", doc=doc, malformed=True)
        for field in ("artifact_version", "decision_ref"):
            if text(row.get(field)) and not identifier(row[field]):
                issue("document_version_ref_invalid", doc=doc, malformed=True)
        if not references(row.get("evidence_refs")):
            issue("document_evidence_refs_type", doc=doc, malformed=True)
    for doc in sorted(required - document_index.keys()):
        issue("required_document_unaccounted", doc=doc)
    if set(document_index) - required:
        issue("document_profile_not_selected", malformed=True)
    indexes = {}
    for name in ("evidence", "decisions", "feedback"):
        rows = pack.get(name)
        index = {}
        indexes[name] = index
        if not isinstance(rows, list) or len(rows) > MAX_RECORDS:
            issue("record_list_invalid", malformed=True)
            continue
        declared[name] = len(rows)
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", row["id"])):
                issue("record_id_invalid", malformed=True)
                continue
            if row["id"] in index:
                issue("record_id_duplicate", malformed=True)
            index[row["id"]] = row
            doc = row.get("doc_type")
            if not enum(doc, set(document_index)):
                issue("record_document_unknown", malformed=True)
                continue
            version = row.get("artifact_version")
            if not isinstance(version, str) or (text(version) and not identifier(version)):
                issue("record_version_type_or_format", doc=doc, malformed=True)
            if not text(version) or version != document_index[doc].get("artifact_version"):
                issue("record_artifact_version_stale", doc=doc)
            if name == "evidence":
                if "artifact_ref" in row:
                    if not isinstance(row["artifact_ref"], str):
                        issue("evidence_artifact_ref_type", doc=doc, malformed=True)
                if not enum(row.get("result"), {"passed", "failed", "not_run", "unavailable"}):
                    issue("evidence_result_unknown", doc=doc, malformed=True)
                elif row["result"] != "passed":
                    issue("evidence_not_passed", doc=doc)
                if not text(row.get("summary")) or len(row["summary"]) > 1000 or PLACEHOLDER.search(row["summary"]):
                    issue("evidence_summary_missing_or_unbounded", doc=doc)
                if bool(text(row.get("path"))) == bool(text(row.get("source_ref"))):
                    issue("evidence_source_ambiguous", doc=doc)
                elif text(row.get("path")):
                    try:
                        path = safe_local(pack_root, row["path"])
                        with path.open("rb") as handle:
                            handle.read(1)
                    except (PackError, OSError):
                        issue("evidence_local_path_boundary_or_missing", doc=doc)
                elif not stable_ref(row.get("source_ref")):
                    issue("evidence_external_ref_invalid", doc=doc)
                evidence_projection.append({"id": row["id"], "doc_type": doc,
                    "artifact_version": version if identifier(version) else "invalid",
                    "result": row.get("result") if enum(row.get("result"), {"passed", "failed", "not_run", "unavailable"}) else "invalid",
                    "source_verification": "local_file" if text(row.get("path")) else "external_declared_not_fetched"})
            elif name == "decisions":
                if not enum(row.get("kind"), {"approval", "reuse", "applicability", "waiver", "change", "feedback_resolution"}):
                    issue("decision_kind_unknown", doc=doc, malformed=True)
                if not enum(row.get("decision"), {"pending", "approved", "rejected"}):
                    issue("decision_state_unknown", doc=doc, malformed=True)
                elif row["decision"] != "approved":
                    issue("decision_pending_or_rejected", doc=doc)
                for field in ("authority", "reason"):
                    if not text(row.get(field)) or PLACEHOLDER.search(row[field]):
                        issue("decision_authority_reason_missing", doc=doc)
                for field in ("work_id", "work_scope"):
                    if not isinstance(row.get(field), str):
                        issue("decision_work_binding_type_or_missing", doc=doc, malformed=True)
                if row.get("kind") == "reuse" and not isinstance(row.get("artifact_ref"), str):
                    issue("reuse_decision_artifact_ref_type_or_missing", doc=doc, malformed=True)
                if row.get("kind") == "feedback_resolution":
                    targets = row.get("feedback_ids")
                    if not references(targets) or not targets or len(targets) > MAX_RECORDS:
                        issue("resolution_feedback_ids_invalid", doc=doc, malformed=True)
                if not references(row.get("evidence_refs")):
                    issue("decision_evidence_refs_type", doc=doc, malformed=True)
                decision_projection.append({"id": row["id"], "doc_type": doc,
                    "artifact_version": version if identifier(version) else "invalid",
                    "decision": row.get("decision") if enum(row.get("decision"), {"pending", "approved", "rejected"}) else "invalid",
                    "authority_declared": text(row.get("authority")), "authority_verified": False,
                    "evidence_refs": row["evidence_refs"] if references(row.get("evidence_refs")) else []})
            else:
                if not enum(row.get("type"), {"clarification", "change", "defect", "risk"}):
                    issue("feedback_type_unknown", doc=doc, malformed=True)
                if not enum(row.get("status"), {"new", "triaged", "in_progress", "ready_for_retest", "resolved", "rejected", "deferred", "duplicate"}):
                    issue("feedback_status_unknown", doc=doc, malformed=True)
                if not text(row.get("owner")):
                    issue("feedback_owner_missing", doc=doc)
                if not references(row.get("evidence_refs")):
                    issue("feedback_evidence_refs_type", doc=doc, malformed=True)
    for record in indexes["decisions"].values():
        if (record.get("kind") != "feedback_resolution"
                or not enum(record.get("doc_type"), set(document_index))):
            continue
        targets = record.get("feedback_ids")
        if not references(targets) or not targets or len(targets) > MAX_RECORDS:
            continue
        for target_id in targets:
            target = indexes["feedback"].get(target_id)
            if target is None:
                issue("resolution_feedback_unknown", doc=record.get("doc_type"), malformed=True)
            elif (target.get("doc_type") != record.get("doc_type")
                  or target.get("artifact_version") != record.get("artifact_version")):
                issue("resolution_feedback_document_version_mismatch", doc=record.get("doc_type"))
    # Malformed records cannot participate in decisions or leak arbitrary values in a projection.
    if any(i["malformed"] for i in issues):
        return result()
    def evidence_ok(refs: Any, doc: str, version: Any, *, required: bool = True,
                    artifact_ref: str | None = None) -> bool:
        if not references(refs) or (required and not refs):
            return False
        for ref in refs:
            record = indexes["evidence"].get(ref)
            if (not record or record.get("doc_type") != doc or record.get("artifact_version") != version
                    or record.get("result") != "passed"
                    or (artifact_ref is not None and record.get("artifact_ref") != artifact_ref)):
                return False
        return True
    def decision_ok(ref: Any, doc: str, version: Any, kinds: set, *, require_evidence: bool = True,
                    digest: str | None = None, artifact_ref: str | None = None,
                    feedback_id: str | None = None) -> bool:
        if not isinstance(ref, str):
            return False
        record = indexes["decisions"].get(ref)
        return bool(record and record.get("doc_type") == doc and record.get("artifact_version") == version
                    and record.get("kind") in kinds and record.get("decision") == "approved"
                    and text(record.get("authority")) and text(record.get("reason"))
                    and record.get("work_id") == work.get("id") and record.get("work_scope") == work.get("scope")
                    and (digest is None or record.get("artifact_sha256") == digest)
                    and (artifact_ref is None or record.get("artifact_ref") == artifact_ref)
                    and (feedback_id is None or feedback_id in record.get("feedback_ids", []))
                    and evidence_ok(record.get("evidence_refs"), doc, version,
                                    required=require_evidence, artifact_ref=artifact_ref))
    for doc, row in document_index.items():
        before = len(issues)
        version = row.get("artifact_version")
        disposition = row.get("disposition")
        if row.get("status") != "approved":
            issue("document_not_approved", doc=doc)
        if not text(row.get("owner")) or not text(version):
            issue("document_owner_version_missing", doc=doc)
        if doc in CONTROLS and disposition != "create":
            issue("work_control_must_create", doc=doc)
        if disposition == "create":
            digest = row.get("artifact_sha256")
            digest_valid = isinstance(digest, str) and bool(re.fullmatch(r"[0-9a-f]{64}", digest))
            if not digest_valid:
                issue("document_digest_missing_or_invalid", doc=doc)
            try:
                path = safe_local(pack_root, row.get("artifact_path"))
                if path.suffix != ".md":
                    raise PackError("document ต้องเป็น Markdown")
                raw = bounded_bytes(path)
                try:
                    content = raw.decode("utf-8")
                except UnicodeError as exc:
                    raise PackError("document ต้องเป็น UTF-8") from exc
                current_digest = hashlib.sha256(raw).hexdigest()
                if digest != current_digest:
                    issue("document_content_digest_stale", doc=doc)
                metadata, body = split_frontmatter(content)
                for field, expected in (("doc_type", doc), ("work_id", work.get("id")),
                                        ("version", version), ("status", "approved")):
                    if metadata.get(field) != expected:
                        issue("document_frontmatter_identity_version_status", doc=doc)
                if not substantive(body):
                    issue("document_placeholder_or_empty", doc=doc)
            except PackError:
                issue("document_local_path_boundary_or_missing", doc=doc)
            if not digest_valid or not decision_ok(row.get("decision_ref"), doc, version, {"approval"}, digest=digest):
                issue("document_exact_version_approval_missing", doc=doc)
            if not evidence_ok(row.get("evidence_refs"), doc, version):
                issue("document_exact_version_evidence_missing", doc=doc)
        elif disposition == "reuse":
            if not stable_ref(row.get("artifact_ref")):
                issue("reuse_stable_ref_missing", doc=doc)
            if not decision_ok(row.get("decision_ref"), doc, version, {"reuse"}, artifact_ref=row.get("artifact_ref")):
                issue("scoped_reuse_approval_missing", doc=doc)
            if not evidence_ok(row.get("evidence_refs"), doc, version, artifact_ref=row.get("artifact_ref")):
                issue("reuse_exact_version_evidence_missing", doc=doc)
        elif disposition == "not_applicable":
            if not text(row.get("reason")) or PLACEHOLDER.search(row["reason"]):
                issue("applicability_reason_missing", doc=doc)
            kinds = {"waiver"} if work.get("kind") == "full-product" else {"applicability", "waiver"}
            if not decision_ok(row.get("decision_ref"), doc, version, kinds):
                issue("scoped_applicability_approval_missing", doc=doc)
        coverage.append({"doc_type": doc, "disposition": disposition, "status": row.get("status"),
                         "artifact_version": version if isinstance(version, str) else "invalid",
                         "decision_ref": row.get("decision_ref") if isinstance(row.get("decision_ref"), str) else "",
                         "evidence_refs": row.get("evidence_refs", []),
                         "completion_blockers": len(issues) - before,
                         "artifact_verification": "local_file" if disposition == "create" else "declared_only"})
    for record in indexes["decisions"].values():
        doc, version = record.get("doc_type"), record.get("artifact_version")
        if doc in document_index and not evidence_ok(record.get("evidence_refs"), doc, version):
            issue("decision_evidence_missing_or_stale", doc=doc)
    for record in indexes["feedback"].values():
        doc, version = record.get("doc_type"), record.get("artifact_version")
        if doc not in document_index:
            continue
        closed = record.get("status") in {"resolved", "rejected", "deferred", "duplicate"}
        valid = (closed and decision_ok(record.get("decision_ref"), doc, version, {"feedback_resolution"}, feedback_id=record["id"])
                 and evidence_ok(record.get("evidence_refs"), doc, version))
        if not valid:
            # Return only the bounded identifier, never the feedback body.
            unresolved.append(record["id"])
            issue("feedback_unresolved_or_unproved_resolution", doc=doc)
    return result()


def init_pack(target: Path, work_id: str, kind: str, profiles: list[str], client_facing: bool,
              standard: dict, root: Path = ROOT) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", work_id) or kind not in KINDS:
        raise PackError("work_id/kind ไม่ถูกต้อง")
    if len(set(profiles)) != len(profiles) or not set(profiles).issubset(standard["profiles"]):
        raise PackError("profile ไม่รู้จักหรือซ้ำ")
    if client_facing and "client-delivery" not in profiles:
        profiles = profiles + ["client-delivery"]
    if ".." in target.parts or credential_path(target):
        raise PackError("init target ละเมิดขอบเขต")
    for parent in (target, *target.parents):
        if parent.is_symlink():
            raise PackError("init target ห้ามผ่าน symlink")
    if target.exists() or not target.parent.is_dir():
        raise PackError("init ต้องเป็น directory ใหม่และมี parent อยู่แล้ว")
    manifest = draft_manifest(standard, work_id, kind, profiles, client_facing)
    definitions = {row["id"]: row for row in standard["documents"]}
    prepared = []
    for row in manifest["documents"]:
        content = bounded_read(safe_local(root, definitions[row["doc_type"]]["template"]))
        metadata, body = split_frontmatter(content)
        metadata.update({"doc_type": row["doc_type"], "work_id": work_id,
                         "version": row["artifact_version"], "status": "draft"})
        prepared.append((row["artifact_path"], "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
                         + "---\n" + body))
    # Prepare everything before the exclusive creation. Never overwrite another session.
    try:
        target.mkdir()
    except OSError as exc:
        raise PackError("สร้าง init directory ใหม่ไม่ได้") from exc
    try:
        (target / "documents").mkdir()
        for relative, content in prepared:
            with (target / relative).open("x", encoding="utf-8") as handle:
                handle.write(content)
        with (target / "pack-manifest.yaml").open("x", encoding="utf-8") as handle:
            yaml.safe_dump(manifest, handle, allow_unicode=True, sort_keys=False)
    except OSError as exc:
        # Do not delete any directory: a partially created draft must remain inspectable.
        raise PackError("init ไม่สำเร็จ มี draft บางส่วนให้ตรวจ; ไม่ลบหรือ overwrite") from exc
    return target / "pack-manifest.yaml"


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description="ตรวจ document standard/pack แบบ offline โดยไม่อนุมัติแทนคน")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true")
    actions.add_argument("--write", action="store_true")
    actions.add_argument("--pack", type=Path)
    actions.add_argument("--init", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--work-id")
    parser.add_argument("--kind", choices=sorted(KINDS), default="task")
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--client-facing", action="store_true")
    args = parser.parse_args(argv)
    if args.init and not args.work_id:
        parser.error("--init ต้องมี --work-id")
    if not args.init and (args.work_id or args.profile or args.client_facing):
        parser.error("work-id/profile/client-facing ใช้กับ --init เท่านั้น")
    if args.json and (args.write or args.check or args.init):
        parser.error("--json ใช้ export standard หรือ --pack เท่านั้น")
    try:
        standard = read_yaml(safe_local(root, str(STANDARD)))
        errors = validate_standard(standard, root)
        if errors:
            raise PackError("standard validation: " + "; ".join(errors[:20]))
        if args.pack:
            # Reject credential/config components before invoking the manifest reader.
            ensure_path_boundary(args.pack)
            report = pack_report(read_yaml(args.pack), args.pack.parent, standard)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
            else:
                print(f"document pack: {report['status']}; blockers={report['blocker_count']}; authority=not_verified")
            return 2 if report["status"] == "malformed" else 1 if report["blocker_count"] else 0
        if args.init:
            init_pack(args.init, args.work_id, args.kind, args.profile, args.client_facing, standard, root)
            print("สร้าง draft pack แล้ว; ยัง blocked จนกว่าเอกสาร หลักฐาน และการตัดสินใจครบ")
        elif args.json:
            print(json.dumps(standard, ensure_ascii=False, indent=2, allow_nan=False))
        elif args.write:
            from _env import refuse_snapshot_write  # noqa: E402
            refuse_snapshot_write("delivery_document_pack", root)
            destination = safe_local(root, str(MATRIX), exists=False)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(render_matrix(standard, root), encoding="utf-8")
            print("สร้าง document matrix72ขั้นแล้ว")
        else:
            if bounded_read(safe_local(root, str(MATRIX))) != render_matrix(standard, root):
                raise PackError("document matrix drift; ใช้ --write")
            print("delivery document standard: passed; matrix72ขั้น; offline authority=not_verified")
        return 0
    except (PackError, OSError, ValueError, TypeError, RecursionError) as exc:
        # Do not echo YAML parser exceptions, user payloads, filenames or document bodies.
        if args.json:
            report = pack_report(None, args.pack.parent if args.pack else root, {"documents": []})
            print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        else:
            diagnostic = str(exc)[:1500] if isinstance(exc, PackError) else "schema/path/source ไม่ถูกต้อง"
            print("document pack check ไม่ผ่าน: " + diagnostic, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
