#!/usr/bin/env python3
"""Validate and render the offline product-delivery definition, without runtime writes."""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
DEFINITION = Path("systems/architecture/product-delivery-workflow.yaml")
GENERATED = Path("docs/lifecycle/PRODUCT-DELIVERY-STEPS.md")
CONTRACT = "solo-empire.product-delivery-definition.v1"
REQUIRED_INTERFACE_IDS = frozenset({
    "http-api", "mcp-local", "mcp-hosted", "cli", "web", "mobile-web",
    "native-mobile", "desktop-ide", "design-tools", "storybook", "client-review",
    "notifications",
})
REQUIRED_VIEW_FIELDS = frozenset({
    "workflow_id", "definition_version", "instance_revision", "project_ref", "feature_ref",
    "active_step_ids", "role_assignments", "step_state", "blockers", "tool_requirements",
    "tool_runs", "artifact_refs", "gate_state", "feedback_summary", "allowed_actions",
    "updated_at", "freshness", "capability_status",
})
LANE_COUNTS = {"product": 8, "ba": 10, "sa": 10, "ux": 12,
               "dev": 10, "qa": 12, "release": 6, "operate": 4}
REQUIRED_ROLE_COVERAGE = {
    "product": {"product-manager"}, "ba": {"business-analyst"},
    "sa": {"solution-architect"}, "ux": {"ux-ui-designer"},
    "dev": {"frontend-engineer", "backend-engineer"},
    "qa": {"qa-test-engineer"},
}
SOURCE_FIELDS = ("lifecycle", "roles", "role_work_packages", "uiux_flow")


class DefinitionError(ValueError):
    """An invalid definition or repository-local source."""


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate mapping keys instead of silently replacing a definition."""


def _unique_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise DefinitionError("YAML mapping key ต้องเป็น scalar") from exc
        if duplicate:
            raise DefinitionError(f"YAML mapping key ซ้ำ: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                               _unique_mapping)


def local_file(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DefinitionError("path ต้องเป็นข้อความที่ไม่ว่าง")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise DefinitionError(f"path ต้องอยู่ภายใน repository: {value}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise DefinitionError(f"ไม่พบไฟล์ภายใน repository: {value}")
    return path


def read_yaml(path: Path) -> Any:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise DefinitionError(f"อ่าน YAML ไม่สำเร็จ: {path.name}: {exc}") from exc


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _strings(value: Any, *, nonempty: bool = True) -> bool:
    return (isinstance(value, list) and (bool(value) or not nonempty)
            and all(_text(item) for item in value))


def json_compatibility_errors(data: Any) -> list[str]:
    """Validate every value, including optional metadata, before any export."""
    errors = []
    ancestors = set()

    def visit(value: Any, path: str) -> None:
        if value is None or type(value) in (str, int, bool):
            return
        if type(value) is float:
            if not math.isfinite(value):
                errors.append(f"{path}: JSON ห้ามใช้ NaN หรือ Infinity")
            return
        if not isinstance(value, (dict, list)):
            errors.append(f"{path}: JSON ไม่รองรับชนิด {type(value).__name__}")
            return
        identity = id(value)
        if identity in ancestors:
            errors.append(f"{path}: JSON ห้ามอ้างอิงแบบวนซ้ำ")
            return
        ancestors.add(identity)
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    errors.append(f"{path}: JSON mapping key ต้องเป็น string")
                else:
                    visit(child, f"{path}.{key}")
        else:
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")
        ancestors.remove(identity)

    try:
        visit(data, "definition")
    except RecursionError:
        errors.append("definition: JSON ซ้อนระดับลึกเกินไป")
    return errors


def _uiux_step_ids(content: str) -> set[str]:
    """Read stable Markdown step headings, excluding fenced code examples."""
    identifiers = set()
    fence = None
    for line in content.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if marker:
            delimiter, tail = marker.groups()
            if fence is None:
                fence = (delimiter[0], len(delimiter))
            elif delimiter[0] == fence[0] and len(delimiter) >= fence[1] and not tail.strip():
                fence = None
            continue
        if fence is not None:
            continue
        heading = re.fullmatch(r" {0,3}###[ \t]+(S[0-9]{2})[ \t]+—[ \t]+\S.*", line)
        if heading:
            identifiers.add(heading[1])
    return identifiers


def source_context(data: dict, root: Path) -> tuple[dict, set, dict, set, list[str]]:
    """Load only declared repository-local registries; return bounded diagnostics."""
    errors = []
    documents = {}
    sources = data.get("source_catalog")
    if not isinstance(sources, dict):
        return {}, set(), {}, set(), ["source_catalog ต้องเป็น mapping"]
    for field in SOURCE_FIELDS:
        try:
            path = local_file(root, sources.get(field))
            if field == "uiux_flow":
                documents[field] = path.read_text(encoding="utf-8")
            else:
                documents[field] = read_yaml(path)
        except (DefinitionError, OSError, UnicodeError) as exc:
            errors.append(f"source_catalog.{field}: {exc}")
    uiux_ids = _uiux_step_ids(documents.get("uiux_flow", ""))
    if "uiux_flow" in documents and not uiux_ids:
        errors.append("source_catalog.uiux_flow: ไม่พบ stable heading รูปแบบ ### S01 — title")
    roles = {}
    stages = set()
    packages = {}
    for source, field, target in (("roles", "roles", roles),
                                  ("lifecycle", "stages", stages)):
        doc = documents.get(source)
        entries = doc.get(field) if isinstance(doc, dict) else None
        if not isinstance(entries, list):
            errors.append(f"source_catalog.{source}: ไม่พบ {field}[]")
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not _text(entry.get("id")):
                errors.append(f"source_catalog.{source}: {field}[].id ไม่ถูกต้อง")
                continue
            identifier = entry["id"]
            if identifier in target:
                errors.append(f"source_catalog.{source}: ID ซ้ำ {identifier}")
            elif isinstance(target, set):
                target.add(identifier)
            else:
                target[identifier] = entry.get("name", identifier)
    doc = documents.get("role_work_packages")
    packs = doc.get("packs") if isinstance(doc, dict) else None
    if not isinstance(packs, dict):
        errors.append("source_catalog.role_work_packages: ไม่พบ packs mapping")
    else:
        for pack in packs.values():
            pack_stages = pack.get("stages") if isinstance(pack, dict) else None
            if not isinstance(pack_stages, dict):
                errors.append("source_catalog.role_work_packages: stages ต้องเป็น mapping")
                continue
            for stage in pack_stages.values():
                if not isinstance(stage, dict):
                    errors.append("source_catalog.role_work_packages: stage ต้องเป็น mapping")
                    continue
                parent = stage.get("parent_step_id")
                work = stage.get("work_packages")
                if not _text(parent) or not isinstance(work, dict):
                    errors.append("source_catalog.role_work_packages: parent_step_id/work_packages ไม่ถูกต้อง")
                    continue
                for role_id, package in work.items():
                    if isinstance(package, dict) and package.get("role_id") == role_id:
                        packages[f"{parent}@{role_id}"] = role_id
    return roles, stages, packages, uiux_ids, errors


def validate_definition(data: Any, root: Path = ROOT) -> list[str]:
    """Validate full static definitions; feedback targets are not DAG edges."""
    if not isinstance(data, dict):
        return ["definition ต้องเป็น mapping"]
    compatibility_errors = json_compatibility_errors(data)
    if compatibility_errors:
        return compatibility_errors
    roles, stages, packages, uiux_ids, errors = source_context(data, root)
    for field, expected in (("contract", CONTRACT), ("status", "design-ready"),
                            ("runtime_status", "not-integrated")):
        if data.get(field) != expected:
            errors.append(f"{field} ต้องเป็น {expected}")
    if type(data.get("version")) is not int or data["version"] != 1:
        errors.append("version ต้องเป็น integer 1")

    def text_field(item: dict, field: str, where: str) -> None:
        if not _text(item.get(field)):
            errors.append(f"{where}.{field} ต้องเป็นข้อความที่ไม่ว่าง")

    def list_field(item: dict, field: str, where: str, nonempty: bool = True) -> list:
        value = item.get(field)
        if not _strings(value, nonempty=nonempty):
            errors.append(f"{where}.{field} ต้องเป็นรายการข้อความ" + ("ที่ไม่ว่าง" if nonempty else ""))
            return []
        if len(value) != len(set(value)):
            errors.append(f"{where}.{field} มีค่าซ้ำ")
        return value

    def reference(value: Any, allowed: Any, where: str) -> None:
        if not _text(value) or value not in allowed:
            errors.append(f"{where}: reference ไม่รู้จัก {value!r}")

    def index(field: str) -> dict:
        entries = data.get(field)
        result = {}
        if not isinstance(entries, list) or not entries:
            errors.append(f"{field} ต้องเป็นรายการที่ไม่ว่าง")
            return result
        for offset, entry in enumerate(entries):
            if not isinstance(entry, dict) or not _text(entry.get("id")):
                errors.append(f"{field}[{offset}].id ต้องเป็นข้อความที่ไม่ว่าง")
                continue
            identifier = entry["id"]
            if identifier in result:
                errors.append(f"{field}: ID ซ้ำ {identifier}")
            else:
                result[identifier] = entry
        return result

    tools = data.get("tools")
    if not isinstance(tools, dict) or not tools:
        errors.append("tools ต้องเป็น mapping ที่ไม่ว่าง")
        tools = {}
    for identifier, tool in tools.items():
        if not _text(identifier) or not isinstance(tool, dict):
            errors.append("tools: ID และ definition ไม่ถูกต้อง")
            continue
        for field in ("name", "purpose"):
            text_field(tool, field, f"tools.{identifier}")

    lanes, interfaces, routes, steps = (index(field) for field in
                                      ("lanes", "interfaces", "feedback_routes", "steps"))
    if set(lanes) != set(LANE_COUNTS):
        errors.append("lanes ต้องมี product, ba, sa, ux, dev, qa, release, operate ครบและไม่มี lane อื่น")
    for identifier, lane in lanes.items():
        where = f"lanes.{identifier}"
        text_field(lane, "name", where)
        for role in list_field(lane, "role_ids", where):
            reference(role, roles, f"{where}.role_ids")
        for template in list_field(lane, "templates", where):
            try:
                local_file(root, template)
            except DefinitionError as exc:
                errors.append(f"{where}.templates: {exc}")
    for identifier in sorted(REQUIRED_INTERFACE_IDS - interfaces.keys()):
        errors.append(f"interfaces: ขาด consumer ที่จำเป็นสำหรับ v1: {identifier}")
    for identifier, interface in interfaces.items():
        where = f"interfaces.{identifier}"
        for field in ("name", "kind", "owner_repository"):
            text_field(interface, field, where)
        reference(interface.get("readiness"),
                  {"task-source-present", "projection-needed", "unverified"}, f"{where}.readiness")
        view_fields = list_field(interface, "view_fields", where)
        for field in sorted(REQUIRED_VIEW_FIELDS - set(view_fields)):
            errors.append(f"{where}.view_fields: ขาด shared field ที่จำเป็นสำหรับ v1: {field}")
    for identifier, route in routes.items():
        where = f"feedback_routes.{identifier}"
        reference(route.get("owner_role"), roles, f"{where}.owner_role")
        for target in list_field(route, "target_steps", where):
            reference(target, steps, f"{where}.target_steps")
        if type(route.get("requires_new_baseline")) is not bool:
            errors.append(f"{where}.requires_new_baseline ต้องเป็น boolean")
        text_field(route, "retest", where)

    counts = dict.fromkeys(LANE_COUNTS, 0)
    owners = {lane: set() for lane in LANE_COUNTS}
    dependencies = {}
    for identifier, step in steps.items():
        where = f"steps.{identifier}"
        for field in ("title", "artifact_location", "approval_authority", "client_output"):
            text_field(step, field, where)
        lane_id = step.get("lane")
        reference(lane_id, lanes, f"{where}.lane")
        role_id = step.get("owner_role")
        reference(role_id, roles, f"{where}.owner_role")
        if _text(lane_id) and lane_id in counts:
            counts[lane_id] += 1
            if _text(role_id):
                owners[lane_id].add(role_id)
            lane_roles = lanes.get(lane_id, {}).get("role_ids")
            if _strings(lane_roles) and role_id not in lane_roles:
                errors.append(f"{where}.owner_role ไม่อยู่ใน lane.role_ids")
        for role in list_field(step, "reviewer_roles", where):
            reference(role, roles, f"{where}.reviewer_roles")
        reference(step.get("lifecycle_stage_id"), stages, f"{where}.lifecycle_stage_id")
        parent = step.get("parent_role_package")
        if parent is not None:
            reference(parent, packages, f"{where}.parent_role_package")
            if _text(parent) and parent in packages and packages[parent] != role_id:
                errors.append(f"{where}.parent_role_package ไม่ตรง owner_role")
        for field in ("primary_tools", "supporting_tools"):
            for tool in list_field(step, field, where, nonempty=field == "primary_tools"):
                reference(tool, tools, f"{where}.{field}")
        for field in ("inputs", "actions", "outputs", "acceptance"):
            list_field(step, field, where)
        if type(step.get("approval_required")) is not bool:
            errors.append(f"{where}.approval_required ต้องเป็น boolean")
        dependencies[identifier] = list_field(step, "depends_on", where, nonempty=False)
        for dependency in dependencies[identifier]:
            reference(dependency, steps, f"{where}.depends_on")
            if dependency == identifier:
                errors.append(f"{where}.depends_on อ้างตัวเอง")
        for route_id in list_field(step, "feedback_routes", where, nonempty=False):
            reference(route_id, routes, f"{where}.feedback_routes")
        for ref in list_field(step, "uiux_step_refs", where, nonempty=False):
            if not re.fullmatch(r"S(?:0[1-9]|1[0-9]|2[0-4])", ref) or ref not in uiux_ids:
                errors.append(f"{where}.uiux_step_refs: ไม่พบ heading {ref!r} ใน source_catalog.uiux_flow")
        for field in ("status", "actual_status", "instance_id", "task_id"):
            if field in step:
                errors.append(f"{where}.{field}: definition ห้ามเก็บสถานะหรือ instance ของงานจริง")
    for lane, expected in LANE_COUNTS.items():
        if counts[lane] != expected:
            errors.append(f"lanes.{lane}: ต้องมี {expected} steps พบ {counts[lane]}")
        for role in REQUIRED_ROLE_COVERAGE.get(lane, set()) - owners[lane]:
            errors.append(f"lanes.{lane}: ขาด owner coverage ของ {role}")

    active, visited = set(), set()

    def visit(identifier: str) -> None:
        if identifier in active:
            errors.append(f"steps.depends_on: พบ cycle ที่ {identifier}")
            return
        if identifier in visited:
            return
        active.add(identifier)
        for dependency in dependencies.get(identifier, []):
            if dependency in steps:
                visit(dependency)
        active.remove(identifier)
        visited.add(identifier)

    for identifier in steps:
        visit(identifier)
    return errors


def role_view(data: dict, role: str | None) -> dict:
    """Keep shared references; partial views may depend on steps outside the view."""
    result = copy.deepcopy(data)
    if role:
        result["steps"] = [step for step in result["steps"]
                           if role == step["owner_role"] or role in step["reviewer_roles"]]
        result["view"] = {"role_id": role, "scope": "partial",
                          "dependency_scope": "full-definition"}
    return result


def render_markdown(data: dict, root: Path = ROOT, role: str | None = None) -> str:
    roles, _, _, _, _ = source_context(data, root)
    view = role_view(data, role)
    lines = ["---", "title: Product Delivery — รายละเอียดขั้นตอนทุกบทบาท",
             "tags: [product-delivery, lifecycle, roles, workflow]",
             "created: 2026-10-13", "updated: 2026-10-13", "status: generated",
             "generated: true", "parent: docs/lifecycle/README.md", "---", "",
             "# Product Delivery — รายละเอียดขั้นตอนทุกบทบาท", "",
             "เอกสารนี้เป็น definition และเครื่องมือที่แนะนำสำหรับการส่งมอบงานเท่านั้น "
             "ไม่เก็บสถานะหรือ instance ของงานจริง การเชื่อม runtime ติดตามแยกต่างหาก "
             "(`runtime_status: not-integrated`)", "",
             "แหล่งนิยาม: [product-delivery-workflow.yaml](../../systems/architecture/product-delivery-workflow.yaml) "
             "· [ภาพรวม flow](./PRODUCT-DELIVERY-FLOW.md) "
             "· [UI/UX flow](./UIUX-DESIGN-DELIVERY-FLOW.md)", ""]
    if role:
        lines += [f"มุมมองบางส่วนของบทบาท `{role}` ({roles[role]}): "
                  "รวมขั้นที่เป็น owner หรือ reviewer; dependencies อาจอยู่ใน definition ฉบับเต็ม", ""]
    lines += [f"จำนวนขั้นในมุมมองนี้: {len(view['steps'])}", ""]
    routes = {route["id"]: route for route in data["feedback_routes"]}

    def items(label: str, values: list[str]) -> None:
        lines.extend([f"**{label}**", ""])
        lines.extend([f"- {value}" for value in values] if values else ["- ไม่มี"])
        lines.append("")

    def tools(tool_ids: list[str]) -> list[str]:
        return [f"{data['tools'][tool]['name']} (`{tool}`) — {data['tools'][tool]['purpose']}"
                for tool in tool_ids]

    for lane in data["lanes"]:
        lane_steps = [step for step in view["steps"] if step["lane"] == lane["id"]]
        if not lane_steps:
            continue
        lines.extend([f"## {lane['name']} (`{lane['id']}`) — {len(lane_steps)} ขั้น", ""])
        for step in lane_steps:
            lines.extend([f"### {step['id']} — {step['title']}", "",
                          f"ผู้รับผิดชอบ: {roles[step['owner_role']]} (`{step['owner_role']}`)", "",
                          "ผู้ทบทวน: " + ", ".join(f"{roles[r]} (`{r}`)" for r in step["reviewer_roles"]), "",
                          f"Lifecycle stage: `{step['lifecycle_stage_id']}`", "",
                          f"Parent role package: `{step.get('parent_role_package', 'ไม่ระบุ')}`", ""])
            if data.get("refinement_standard"):
                lines.extend([f"Refine/check: [คำถาม หลักฐาน และเงื่อนไขย้อนแก้ของ {step['id']}]"
                              f"(./PRODUCT-DELIVERY-REFINEMENT-STEPS.md#{step['id'].lower()})", ""])
            items("เครื่องมือหลัก", tools(step["primary_tools"]))
            items("เครื่องมือสนับสนุน", tools(step["supporting_tools"]))
            for label, field in (("ข้อมูลนำเข้า", "inputs"), ("การดำเนินงาน", "actions"),
                                 ("ผลลัพธ์", "outputs")):
                items(label, step[field])
            lines.extend([f"ตำแหน่ง artifact: {step['artifact_location']}", ""])
            items("เกณฑ์ยอมรับ", step["acceptance"])
            approval = "ต้องอนุมัติ" if step["approval_required"] else "ไม่ต้องอนุมัติเพิ่ม"
            lines.extend([f"การอนุมัติ: {approval}; authority: {step['approval_authority']}", ""])
            items("Dependencies", [f"`{ref}`" for ref in step["depends_on"]])
            feedback = []
            for identifier in step["feedback_routes"]:
                route = routes[identifier]
                baseline = "ต้องสร้าง baseline ใหม่" if route["requires_new_baseline"] else "ทบทวน baseline ปัจจุบัน"
                feedback.append(f"`{identifier}` — owner: {roles[route['owner_role']]} "
                                f"(`{route['owner_role']}`); กลับไป: {', '.join(route['target_steps'])}; "
                                f"{baseline}; retest: {route['retest']}")
            items("Feedback และการทดสอบซ้ำ", feedback)
            lines.extend([f"สิ่งที่ส่งให้ลูกค้า: {step['client_output']}", ""])
            items("อ้างอิงขั้น UI/UX", [f"`{ref}`" for ref in step["uiux_step_refs"]])
    return "\n".join(lines).rstrip() + "\n"


def check_generated(data: dict, root: Path = ROOT) -> list[str]:
    expected = render_markdown(data, root)
    try:
        actual = (root / GENERATED).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return [f"ไม่พบหรืออ่านเอกสาร generated ไม่สำเร็จ: {GENERATED}; ใช้ --write"]
    if actual != expected:
        return [f"เอกสาร generated drift: {GENERATED}; ใช้ --write"]
    return []


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description="ตรวจ definition และสร้างเอกสาร Product Delivery แบบ offline")
    modes = parser.add_mutually_exclusive_group()
    for mode in ("check", "json", "markdown", "write"):
        modes.add_argument(f"--{mode}", action="store_true")
    parser.add_argument("--role", help="registered role ID สำหรับมุมมอง owner/reviewer")
    args = parser.parse_args(argv)
    if args.role and not (args.json or args.markdown):
        parser.error("--role ใช้ได้เฉพาะ --json หรือ --markdown; --write ต้องสร้างฉบับเต็ม")
    try:
        data = read_yaml(local_file(root, str(DEFINITION)))
        errors = validate_definition(data, root)
        if errors:
            raise DefinitionError("\n".join(errors))
        roles, _, _, _, _ = source_context(data, root)
        if args.role and args.role not in roles:
            raise DefinitionError(f"--role: reference ไม่รู้จัก {args.role!r}")
        if args.json:
            try:
                payload = json.dumps(role_view(data, args.role), ensure_ascii=False,
                                     indent=2, allow_nan=False)
            except (TypeError, ValueError, OverflowError, RecursionError) as exc:
                raise DefinitionError(f"สร้าง JSON ไม่สำเร็จ: {exc}") from exc
            print(payload)
        elif args.markdown:
            print(render_markdown(data, root, args.role), end="")
        elif args.write:
            target = root / GENERATED
            # The only write target is the canonical repository-local generated document.
            if not target.parent.resolve().is_relative_to(root.resolve()) or target.is_symlink():
                raise DefinitionError("generated path ต้องอยู่ภายใน repository และห้ามเป็น symlink")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(render_markdown(data, root), encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise DefinitionError(f"เขียน {GENERATED} ไม่สำเร็จ: {exc}") from exc
            print(f"เขียน {GENERATED} แล้ว ({len(data['steps'])} steps)")
        else:
            drift = check_generated(data, root)
            if drift:
                raise DefinitionError("\n".join(drift))
            print(f"Product Delivery definition ผ่านการตรวจ ({len(data['steps'])} steps); generated ตรงกับ source")
    except DefinitionError as exc:
        print(f"Product Delivery ตรวจไม่ผ่าน:\n{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
