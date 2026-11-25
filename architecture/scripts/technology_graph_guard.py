#!/usr/bin/env python3
"""Validate the technology taxonomy and Solo Empire implementation graph.

The guard is intentionally provider-neutral and metadata-only. It checks that
the implementation graph uses the canonical taxonomy vocabulary, that graph
references resolve, and that evidence paths stay inside an allowed repository
documentation boundary. It never reads or prints private payloads.

Usage::

    task architecture:technology-graph:check
    task architecture:technology-graph:check -- --json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PureWindowsPath
from typing import Any

import yaml

from _env import KNOWLEDGE_DIRS, KNOWLEDGE_ROOT, PROJECT_ROOT, is_external_ref


TAXONOMY_RELATIVE_PATH = Path("systems/architecture/technology-taxonomy.yaml")
GRAPH_RELATIVE_PATH = Path("systems/architecture/solo-empire-technology-graph.yaml")

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")

NODE_REQUIRED_FIELDS = (
    "id",
    "kind",
    "name",
    "status",
    "layer",
    "domain",
    "capability",
    "owner_boundary",
    "interfaces",
    "flows",
    "cross_cutting",
    "evidence",
)

REFERENCE_FIELDS = {
    "domain": "domains",
    "capability": "capability_families",
    "interfaces": "interface_families",
    "cross_cutting": "cross_cutting",
}

SENSITIVE_COMPONENTS = {
    ".git",
    ".aws",
    "credential",
    "credentials",
    "cookies",
    "secret",
    "secrets",
    "tokens",
}
SENSITIVE_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "token.json",
}
SENSITIVE_PREFIXES = (
    "data/generated/",
    "data/lake/",
    "data/raw/",
    "data/runtime/",
    "diary/entries/",
    "finance/accounts/",
    "finance/assets/",
)


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _label_path(root: Path, path: Path) -> str:
    return _relative_path(root, path)


def _load_yaml(path: Path, root: Path, errors: list[str], label: str) -> Any:
    if not path.is_file():
        errors.append(f"{label}: missing file {_label_path(root, path)}")
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = ""
        if mark is not None:
            location = f" at line {mark.line + 1}, column {mark.column + 1}"
        errors.append(f"{label}: invalid YAML{location}")
    except OSError:
        errors.append(f"{label}: file cannot be read")
    return {}


def _mapping(value: Any, errors: list[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{label}: must be a mapping")
        return {}
    return value


def _list(value: Any, errors: list[str], label: str, *, required: bool = False) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{label}: must be a list")
        return []
    if required and not value:
        errors.append(f"{label}: must not be empty")
    return value


def _required(mapping: dict[str, Any], fields: tuple[str, ...], label: str, errors: list[str]) -> None:
    for field in fields:
        value = mapping.get(field)
        if value is None or value == "":
            errors.append(f"{label}: missing required field {field!r}")


def _check_id(value: Any, label: str, errors: list[str]) -> bool:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        errors.append(f"{label}: invalid id")
        return False
    return True


def _registry_ids(
    value: Any,
    label: str,
    errors: list[str],
    *,
    require_entries: bool = True,
) -> set[str]:
    entries = _list(value, errors, label, required=require_entries)
    ids: set[str] = set()
    for index, entry in enumerate(entries):
        entry_label = f"{label}[{index}]"
        if isinstance(entry, str):
            entry_id = entry
        elif isinstance(entry, dict):
            entry_id = entry.get("id")
            if entry.get("name") in (None, ""):
                errors.append(f"{entry_label}: missing required field 'name'")
        else:
            errors.append(f"{entry_label}: must be a string or mapping")
            continue
        if not _check_id(entry_id, entry_label, errors):
            continue
        if entry_id in ids:
            errors.append(f"{entry_label}: duplicate id {entry_id!r}")
        ids.add(entry_id)
    return ids


def _string_set(value: Any, label: str, errors: list[str], *, required: bool = True) -> set[str]:
    entries = _list(value, errors, label, required=required)
    values: set[str] = set()
    for index, entry in enumerate(entries):
        entry_label = f"{label}[{index}]"
        if not isinstance(entry, str) or not entry.strip():
            errors.append(f"{entry_label}: must be a non-empty string")
            continue
        if entry in values:
            errors.append(f"{entry_label}: duplicate value {entry!r}")
        values.add(entry)
    return values


def _safe_evidence_path(
    root: Path,
    value: Any,
    label: str,
    errors: list[str],
    *,
    require_file: bool = False,
    base: Path | None = None,
) -> None:
    """Check a repository-relative evidence path without exposing unsafe names."""

    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}: evidence path must be a non-empty string")
        return

    raw = value.strip()
    path = Path(raw)
    parts = path.parts
    lowered_raw = raw.lower()
    lowered_parts = {part.lower() for part in parts}
    lowered_filename = path.name.lower()
    windows_path = PureWindowsPath(raw)
    unsafe = (
        "\\" in raw
        or path.is_absolute()
        or windows_path.drive
        or windows_path.is_absolute()
        or not parts
        or any(part in {".", ".."} for part in parts)
        or bool(lowered_parts & SENSITIVE_COMPONENTS)
        or lowered_filename in SENSITIVE_FILENAMES
        or lowered_filename.endswith((".pem", ".key", ".p12", ".pfx"))
        or any(lowered_raw.startswith(prefix) for prefix in SENSITIVE_PREFIXES)
    )
    if unsafe:
        errors.append(f"{label}: path is outside the allowed evidence boundary")
        return

    candidate = (base or root) / path
    root_resolved = root.resolve()
    if base is None and root_resolved == PROJECT_ROOT.resolve() and is_external_ref(path.as_posix()):
        # Evidence in another repository (moved out of the hub): not verifiable from here.
        return
    if (
        base is None
        and root_resolved == PROJECT_ROOT.resolve()
        and parts[0] in KNOWLEDGE_DIRS
        and parts[:4] != ("projects", "product", "engineering", "book-dev")
    ):
        # Moved workspace docs live in the knowledge-base checkout.
        candidate = KNOWLEDGE_ROOT / path
        root_resolved = KNOWLEDGE_ROOT.resolve()
    try:
        candidate.resolve(strict=False).relative_to(root_resolved)
    except ValueError:
        errors.append(f"{label}: path is outside the repository boundary")
        return

    if require_file:
        exists = candidate.is_file()
    else:
        exists = candidate.exists()
    if not exists:
        errors.append(f"{label}: referenced path does not exist: {raw}")


def _check_reference_list(
    mapping: dict[str, Any],
    field: str,
    allowed: set[str],
    label: str,
    errors: list[str],
) -> None:
    values = _list(mapping.get(field), errors, f"{label}.{field}", required=True)
    seen: set[str] = set()
    for index, value in enumerate(values):
        value_label = f"{label}.{field}[{index}]"
        if not isinstance(value, str) or not value:
            errors.append(f"{value_label}: must be a non-empty taxonomy id")
            continue
        if value in seen:
            errors.append(f"{value_label}: duplicate reference {value!r}")
        seen.add(value)
        if value not in allowed:
            errors.append(f"{value_label}: unknown taxonomy id {value!r}")


def _validate_taxonomy(document: dict[str, Any], root: Path, errors: list[str]) -> dict[str, set[str]]:
    _required(
        document,
        (
            "version",
            "kind",
            "status",
            "canonical_markdown",
            "node_kinds",
            "layers",
            "domains",
            "capability_families",
            "interface_families",
            "cross_cutting",
            "flow_types",
            "edge_types",
            "node_record",
        ),
        "taxonomy",
        errors,
    )
    if document.get("kind") != "technology-taxonomy":
        errors.append("taxonomy: kind must be technology-taxonomy")
    if document.get("status") != "current":
        errors.append("taxonomy: status must be current")
    canonical = document.get("canonical_markdown")
    _safe_evidence_path(
        root,
        canonical,
        "taxonomy.canonical_markdown",
        errors,
        require_file=True,
        base=root / TAXONOMY_RELATIVE_PATH.parent,
    )

    registries = {
        "node_kinds": _string_set(document.get("node_kinds"), "taxonomy.node_kinds", errors),
        "layers": _registry_ids(document.get("layers"), "taxonomy.layers", errors),
        "domains": _registry_ids(document.get("domains"), "taxonomy.domains", errors),
        "capability_families": _registry_ids(
            document.get("capability_families"), "taxonomy.capability_families", errors
        ),
        "interface_families": _registry_ids(
            document.get("interface_families"), "taxonomy.interface_families", errors
        ),
        "cross_cutting": _registry_ids(
            document.get("cross_cutting"), "taxonomy.cross_cutting", errors
        ),
        "flow_types": _string_set(document.get("flow_types"), "taxonomy.flow_types", errors),
        "edge_types": _string_set(document.get("edge_types"), "taxonomy.edge_types", errors),
    }

    node_record = _mapping(document.get("node_record"), errors, "taxonomy.node_record")
    required_fields = _string_set(
        node_record.get("required_fields"),
        "taxonomy.node_record.required_fields",
        errors,
    )
    if not set(("id", "kind", "domain", "capability", "layer", "owner_boundary", "interfaces", "flows", "cross_cutting", "status")).issubset(required_fields):
        errors.append("taxonomy.node_record.required_fields: missing canonical node fields")
    return registries


def _validate_evidence_list(
    mapping: dict[str, Any],
    label: str,
    root: Path,
    errors: list[str],
) -> None:
    evidence = _list(mapping.get("evidence"), errors, f"{label}.evidence", required=True)
    for index, path in enumerate(evidence):
        _safe_evidence_path(root, path, f"{label}.evidence[{index}]", errors)


def _validate_graph(
    document: dict[str, Any],
    taxonomy: dict[str, set[str]],
    root: Path,
    errors: list[str],
    warnings: list[str],
) -> dict[str, int]:
    _required(
        document,
        (
            "version",
            "kind",
            "status",
            "graph_id",
            "taxonomy",
            "canonical_markdown",
            "as_of",
            "status_values",
            "node_schema",
            "nodes",
            "edges",
            "flows",
            "boundary_rules",
            "known_gaps",
        ),
        "graph",
        errors,
    )
    if document.get("kind") != "technology-graph":
        errors.append("graph: kind must be technology-graph")
    if document.get("status") != "current":
        errors.append("graph: status must be current")
    _check_id(document.get("graph_id"), "graph.graph_id", errors)
    if document.get("taxonomy") != TAXONOMY_RELATIVE_PATH.name:
        errors.append("graph.taxonomy: must point to the canonical technology taxonomy")
    _safe_evidence_path(
        root,
        document.get("canonical_markdown"),
        "graph.canonical_markdown",
        errors,
        require_file=True,
        base=root / GRAPH_RELATIVE_PATH.parent,
    )

    statuses = _string_set(document.get("status_values"), "graph.status_values", errors)
    if "current" not in statuses:
        errors.append("graph.status_values: must include 'current'")

    node_schema = _mapping(document.get("node_schema"), errors, "graph.node_schema")
    schema_fields = _string_set(
        node_schema.get("required_fields"),
        "graph.node_schema.required_fields",
        errors,
    )
    missing_schema_fields = [field for field in NODE_REQUIRED_FIELDS if field not in schema_fields]
    if missing_schema_fields:
        errors.append("graph.node_schema.required_fields: missing canonical node fields")

    nodes = _list(document.get("nodes"), errors, "graph.nodes", required=True)
    node_ids: set[str] = set()
    node_by_id: dict[str, dict[str, Any]] = {}
    for index, node_value in enumerate(nodes):
        label = f"graph.nodes[{index}]"
        node = _mapping(node_value, errors, label)
        if not node:
            continue
        _required(node, NODE_REQUIRED_FIELDS, label, errors)
        node_id = node.get("id")
        valid_id = _check_id(node_id, f"{label}.id", errors)
        if valid_id and node_id in node_ids:
            errors.append(f"{label}: duplicate node id {node_id!r}")
        if valid_id:
            node_ids.add(node_id)
            node_by_id[node_id] = node
        if node.get("kind") not in taxonomy["node_kinds"]:
            errors.append(f"{label}.kind: unknown taxonomy node kind")
        if not isinstance(node.get("name"), str) or not node.get("name").strip():
            errors.append(f"{label}.name: must be a non-empty string")
        if node.get("status") not in statuses:
            errors.append(f"{label}.status: unknown graph status")
        if node.get("layer") not in taxonomy["layers"]:
            errors.append(f"{label}.layer: unknown taxonomy layer")
        for field, registry in REFERENCE_FIELDS.items():
            _check_reference_list(node, field, taxonomy[registry], label, errors)
        _check_reference_list(node, "flows", taxonomy["flow_types"], label, errors)
        owner_boundary = node.get("owner_boundary")
        if not isinstance(owner_boundary, str) or not owner_boundary.strip():
            errors.append(f"{label}.owner_boundary: must be a non-empty string")
        _validate_evidence_list(node, label, root, errors)

    edges = _list(document.get("edges"), errors, "graph.edges")
    edge_keys: set[tuple[str, str, str, str, str]] = set()
    degree: dict[str, int] = {node_id: 0 for node_id in node_ids}
    for index, edge_value in enumerate(edges):
        label = f"graph.edges[{index}]"
        edge = _mapping(edge_value, errors, label)
        if not edge:
            continue
        source = edge.get("source")
        target = edge.get("target")
        edge_type = edge.get("type")
        if source not in node_ids:
            errors.append(f"{label}.source: unknown node reference")
        if target not in node_ids:
            errors.append(f"{label}.target: unknown node reference")
        if edge_type not in taxonomy["edge_types"]:
            errors.append(f"{label}.type: unknown taxonomy edge type")
        interface = edge.get("interface", "")
        if interface and interface not in taxonomy["interface_families"]:
            errors.append(f"{label}.interface: unknown taxonomy interface")
        flow = edge.get("flow", "")
        if flow and flow not in taxonomy["flow_types"]:
            errors.append(f"{label}.flow: unknown taxonomy flow type")
        key = (str(source), str(target), str(edge_type), str(interface), str(flow))
        if key in edge_keys:
            errors.append(f"{label}: duplicate edge")
        edge_keys.add(key)
        if source in degree:
            degree[source] += 1
        if target in degree:
            degree[target] += 1

    flows = _list(document.get("flows"), errors, "graph.flows")
    flow_ids: set[str] = set()
    for index, flow_value in enumerate(flows):
        label = f"graph.flows[{index}]"
        flow = _mapping(flow_value, errors, label)
        if not flow:
            continue
        _required(flow, ("id", "name", "flow_type", "interfaces", "steps", "evidence"), label, errors)
        flow_id = flow.get("id")
        if _check_id(flow_id, f"{label}.id", errors):
            if flow_id in flow_ids:
                errors.append(f"{label}: duplicate flow id {flow_id!r}")
            flow_ids.add(flow_id)
        if not isinstance(flow.get("name"), str) or not flow.get("name").strip():
            errors.append(f"{label}.name: must be a non-empty string")
        if flow.get("flow_type") not in taxonomy["flow_types"]:
            errors.append(f"{label}.flow_type: unknown taxonomy flow type")
        _check_reference_list(flow, "interfaces", taxonomy["interface_families"], label, errors)
        steps = _list(flow.get("steps"), errors, f"{label}.steps", required=True)
        for step_index, step_value in enumerate(steps):
            step_label = f"{label}.steps[{step_index}]"
            step = _mapping(step_value, errors, step_label)
            if not step:
                continue
            _required(step, ("source", "target", "edge"), step_label, errors)
            if step.get("source") not in node_ids:
                errors.append(f"{step_label}.source: unknown node reference")
            if step.get("target") not in node_ids:
                errors.append(f"{step_label}.target: unknown node reference")
            if step.get("edge") not in taxonomy["edge_types"]:
                errors.append(f"{step_label}.edge: unknown taxonomy edge type")
        _validate_evidence_list(flow, label, root, errors)

    _validate_rule_list(document.get("boundary_rules"), "graph.boundary_rules", root, errors)
    gaps = _list(document.get("known_gaps"), errors, "graph.known_gaps")
    gap_ids: set[str] = set()
    for index, gap_value in enumerate(gaps):
        label = f"graph.known_gaps[{index}]"
        gap = _mapping(gap_value, errors, label)
        if not gap:
            continue
        _required(gap, ("id", "status", "statement", "evidence"), label, errors)
        gap_id = gap.get("id")
        if _check_id(gap_id, f"{label}.id", errors):
            if gap_id in gap_ids:
                errors.append(f"{label}: duplicate gap id {gap_id!r}")
            gap_ids.add(gap_id)
        if gap.get("status") not in statuses:
            errors.append(f"{label}.status: unknown graph status")
        if not isinstance(gap.get("statement"), str) or not gap.get("statement").strip():
            errors.append(f"{label}.statement: must be a non-empty string")
        _validate_evidence_list(gap, label, root, errors)

    isolated = sorted(
        node_id
        for node_id, count in degree.items()
        if count == 0
        and node_by_id.get(node_id, {}).get("status") not in {"lab-only", "approved-lane", "deferred"}
    )
    if isolated:
        warnings.append("isolated active nodes: " + ", ".join(isolated))

    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "flows": len(flows),
        "boundary_rules": len(document.get("boundary_rules", []))
        if isinstance(document.get("boundary_rules"), list)
        else 0,
        "known_gaps": len(gaps),
        "isolated_active_nodes": len(isolated),
    }


def _validate_rule_list(
    value: Any,
    label: str,
    root: Path,
    errors: list[str],
) -> None:
    rules = _list(value, errors, label)
    ids: set[str] = set()
    for index, rule_value in enumerate(rules):
        rule_label = f"{label}[{index}]"
        rule = _mapping(rule_value, errors, rule_label)
        if not rule:
            continue
        _required(rule, ("id", "rule", "evidence"), rule_label, errors)
        rule_id = rule.get("id")
        if _check_id(rule_id, f"{rule_label}.id", errors):
            if rule_id in ids:
                errors.append(f"{rule_label}: duplicate id {rule_id!r}")
            ids.add(rule_id)
        if not isinstance(rule.get("rule"), str) or not rule.get("rule").strip():
            errors.append(f"{rule_label}.rule: must be a non-empty string")
        _validate_evidence_list(rule, rule_label, root, errors)


def validate(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return a deterministic validation report for one repository boundary."""

    root = Path(root).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    taxonomy_path = root / TAXONOMY_RELATIVE_PATH
    graph_path = root / GRAPH_RELATIVE_PATH

    taxonomy_document = _mapping(
        _load_yaml(taxonomy_path, root, errors, "taxonomy"),
        errors,
        "taxonomy",
    )
    taxonomy = _validate_taxonomy(taxonomy_document, root, errors) if taxonomy_document else {}

    graph_document = _mapping(
        _load_yaml(graph_path, root, errors, "graph"),
        errors,
        "graph",
    )
    counts = {"nodes": 0, "edges": 0, "flows": 0, "boundary_rules": 0, "known_gaps": 0, "isolated_active_nodes": 0}
    if graph_document and taxonomy:
        counts = _validate_graph(graph_document, taxonomy, root, errors, warnings)

    return {
        "status": "ok" if not errors else "fail",
        "taxonomy": _label_path(root, taxonomy_path),
        "graph": _label_path(root, graph_path),
        "counts": counts,
        "errors": errors,
        "warnings": warnings,
        "safety": {
            "payloads_read": False,
            "secret_values_read": False,
            "evidence_scope": "repository-relative documentation and bounded metadata paths",
        },
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="run the validation guard")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    if not args.check:
        parser.error("--check is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = validate()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] == "ok" else 1

    print("=" * 70)
    print("Solo Empire — Technology Graph Guard")
    print("=" * 70)
    print(f"  taxonomy: {report['taxonomy']}")
    print(f"  graph: {report['graph']}")
    for key in ("nodes", "edges", "flows", "boundary_rules", "known_gaps"):
        print(f"  {key}: {report['counts'][key]}")
    for warning in report["warnings"]:
        print(f"  WARN: {warning}")
    for error in report["errors"]:
        print(f"  FAIL: {error}")
    if report["errors"]:
        print(f"Result: FAIL ({len(report['errors'])} errors)")
        return 1
    print("  evidence paths: repository-relative and present")
    print("  taxonomy references: valid")
    print("  edge and flow references: valid")
    print("Result: OK")
    return 0


if __name__ == "__main__":
    from _env import skip_without_knowledge

    skip_without_knowledge("technology_graph_guard.py")
    raise SystemExit(main())
