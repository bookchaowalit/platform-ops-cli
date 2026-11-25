#!/usr/bin/env python3
"""Check the local architecture catalog and optional provider readiness.

The default is local-first: when no provider URL is configured this command
validates ``catalog-info.yaml`` and performs no network call. When a provider
URL is explicitly configured, the command performs one bounded JSON ``GET``
probe per provider. It only sends an explicitly configured runtime token for
OpenMetadata, never follows redirects, writes to providers, stores response
bodies, or prints response payloads.

Examples::

    task architecture:providers:check
    task architecture:providers:check -- --offline --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import PROJECT_ROOT as DEFAULT_REPO_ROOT  # noqa: E402  (the document tree, SPEC-001)
MAX_URL_LENGTH = 300
MAX_RESPONSE_BYTES = 256 * 1024
PROBE_TIMEOUT_SECONDS = 1.5
ARCHIMATE_MODEL_RELATIVE_PATH = "systems/architecture/models/solo-empire-system-context.archimate"
ARCHIMATE_NAMESPACE = "http://www.archimatetool.com/archimate"
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"

PROVIDERS: dict[str, dict[str, Any]] = {
    "backstage": {
        "label": "Backstage",
        "env_names": ("ARCHITECTURE_BACKSTAGE_CATALOG_URL",),
        "path": "entities/by-query?limit=1&fields=kind,metadata.name",
    },
    "openmetadata": {
        "label": "OpenMetadata",
        "env_names": (
            "ARCHITECTURE_OPENMETADATA_API_URL",
            "METADATA_CATALOG_URL",
        ),
        "token_env_names": ("ARCHITECTURE_OPENMETADATA_API_TOKEN",),
        "path": "v1/tables?limit=1&offset=0",
    },
}


class NoRedirectHandler(HTTPRedirectHandler):
    """Prevent a configured probe from following an unvalidated redirect."""

    def redirect_request(self, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        return None


def _safe_scalar(value: str) -> str:
    value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def _top_level_field(document: str, field: str) -> str:
    pattern = re.compile(rf"^{re.escape(field)}:\s*(.*?)\s*$", re.MULTILINE)
    match = pattern.search(document)
    return _safe_scalar(match.group(1)) if match else ""


def _nested_field(document: str, section: str, field: str) -> str:
    lines = document.splitlines()
    inside = False
    for line in lines:
        if re.fullmatch(rf"{re.escape(section)}:\s*", line):
            inside = True
            continue
        if inside and line and not line.startswith((" ", "\t")):
            break
        if inside:
            match = re.match(rf"^[ \t]+{re.escape(field)}:\s*(.*?)\s*$", line)
            if match:
                return _safe_scalar(match.group(1))
    return ""


def validate_catalog_descriptor(path: Path) -> dict[str, Any]:
    """Validate the small Backstage descriptor contract without PyYAML."""

    if not path.is_file():
        return {
            "state": "invalid",
            "path": "catalog-info.yaml",
            "documents": 0,
            "valid_documents": 0,
            "system_count": 0,
            "component_count": 0,
            "errors": ["ไม่พบ catalog-info.yaml"],
        }

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {
            "state": "invalid",
            "path": "catalog-info.yaml",
            "documents": 0,
            "valid_documents": 0,
            "system_count": 0,
            "component_count": 0,
            "errors": ["อ่าน catalog-info.yaml ไม่ได้"],
        }

    documents = [
        item
        for item in re.split(r"(?m)^\s*---\s*$", text)
        if item.strip() and not all(
            not line.strip() or line.lstrip().startswith("#")
            for line in item.splitlines()
        )
    ]
    errors: list[str] = []
    valid_documents = 0
    system_count = 0
    component_count = 0
    resource_count = 0

    for index, document in enumerate(documents, start=1):
        api_version = _top_level_field(document, "apiVersion")
        kind = _top_level_field(document, "kind")
        name = _nested_field(document, "metadata", "name")
        owner = _nested_field(document, "spec", "owner")
        document_errors: list[str] = []
        if api_version != "backstage.io/v1alpha1":
            document_errors.append("apiVersion ต้องเป็น backstage.io/v1alpha1")
        if not kind:
            document_errors.append("ขาด kind")
        if not name or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name):
            document_errors.append("metadata.name ไม่ถูกต้องหรือหายไป")
        if kind in {"System", "Component", "Resource"} and not owner:
            document_errors.append("spec.owner หายไป")
        if kind == "Component":
            if not _nested_field(document, "spec", "type"):
                document_errors.append("Component ต้องมี spec.type")
            if not _nested_field(document, "spec", "lifecycle"):
                document_errors.append("Component ต้องมี spec.lifecycle")
        if kind == "Resource" and not _nested_field(document, "spec", "type"):
            document_errors.append("Resource ต้องมี spec.type")

        if document_errors:
            errors.extend(f"document {index}: {item}" for item in document_errors)
            continue

        valid_documents += 1
        if kind == "System":
            system_count += 1
        elif kind == "Component":
            component_count += 1
        elif kind == "Resource":
            resource_count += 1

    if not documents:
        errors.append("catalog-info.yaml ไม่มีเอกสารที่อ่านได้")

    return {
        "state": "valid" if not errors else "invalid",
        "path": "catalog-info.yaml",
        "documents": len(documents),
        "valid_documents": valid_documents,
        "system_count": system_count,
        "component_count": component_count,
        "resource_count": resource_count,
        "errors": errors,
    }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def validate_archimate_model(path: Path) -> dict[str, Any]:
    """Validate the checked-in ArchiMate model without requiring Archi."""

    base = {
        "path": ARCHIMATE_MODEL_RELATIVE_PATH,
        "element_count": 0,
        "relationship_count": 0,
        "view_count": 0,
        "errors": [],
    }
    if not path.is_file():
        return {**base, "state": "invalid", "errors": ["ไม่พบ ArchiMate model"]}

    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {
            **base,
            "state": "invalid",
            "errors": ["อ่าน ArchiMate model เป็น XML ไม่ได้"],
        }

    errors: list[str] = []
    if _local_name(root.tag) != "model" or not root.tag.startswith(f"{{{ARCHIMATE_NAMESPACE}}}"):
        errors.append("root ต้องเป็น archimate:model")
    if not root.attrib.get("version"):
        errors.append("model ต้องมี version")

    elements = [node for node in root.iter() if _local_name(node.tag) == "element"]
    relationships = [
        node
        for node in elements
        if node.attrib.get(XSI_TYPE, "").endswith("Relationship")
    ]
    views = [
        node
        for node in elements
        if node.attrib.get(XSI_TYPE) == "archimate:ArchimateDiagramModel"
    ]
    ids = {node.attrib.get("id") for node in elements if node.attrib.get("id")}
    relationship_ids = {
        node.attrib.get("id") for node in relationships if node.attrib.get("id")
    }
    for relationship in relationships:
        if relationship.attrib.get("source") not in ids:
            errors.append("relationship มี source ที่อ้างอิงไม่ได้")
        if relationship.attrib.get("target") not in ids:
            errors.append("relationship มี target ที่อ้างอิงไม่ได้")
    for child in root.iter():
        referenced_element = child.attrib.get("archimateElement")
        if referenced_element and referenced_element not in ids:
            errors.append("diagram object มี archimateElement ที่อ้างอิงไม่ได้")
        referenced_relationship = child.attrib.get("archimateRelationship")
        if referenced_relationship and referenced_relationship not in relationship_ids:
            errors.append("diagram connection มี relationship ที่อ้างอิงไม่ได้")

    if not elements:
        errors.append("model ต้องมี element")
    if not relationships:
        errors.append("model ต้องมี relationship")
    if not views:
        errors.append("model ต้องมี ArchimateDiagramModel")

    return {
        **base,
        "state": "valid" if not errors else "invalid",
        "element_count": len(elements),
        "relationship_count": len(relationships),
        "view_count": len(views),
        "errors": sorted(set(errors)),
    }


def safe_provider_url(raw: str | None) -> dict[str, str]:
    """Return a redacted URL representation or a validation error."""

    value = (raw or "").strip()
    if not value:
        return {"state": "disabled", "detail": "ไม่ได้ตั้งค่า URL"}
    if len(value) > MAX_URL_LENGTH:
        return {"state": "invalid", "detail": "URL ยาวเกินขอบเขตที่อนุญาต"}

    try:
        parsed = urlparse(value)
        _ = parsed.port
        hostname = parsed.hostname
    except ValueError:
        return {"state": "invalid", "detail": "URL ไม่ถูกต้อง"}

    if parsed.scheme not in {"http", "https"} or not hostname:
        return {"state": "invalid", "detail": "อนุญาตเฉพาะ URL แบบ http หรือ https"}
    if parsed.username or parsed.password:
        return {"state": "invalid", "detail": "URL ต้องไม่มี userinfo หรือ credential"}
    if parsed.query or parsed.fragment or parsed.params:
        return {"state": "invalid", "detail": "URL ต้องไม่มี query, fragment หรือ params"}

    path = parsed.path.rstrip("/")
    normalized = urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    return {
        "state": "configured",
        "value": normalized,
        "display": normalized,
    }


def safe_provider_token(raw: str | None) -> dict[str, str]:
    """Validate an opt-in runtime token without echoing its value."""

    value = (raw or "").strip()
    if not value:
        return {"state": "disabled"}
    if len(value) > 8192:
        return {"state": "invalid", "detail": "token ยาวเกินขอบเขตที่อนุญาต"}
    if any(character.isspace() for character in value):
        return {"state": "invalid", "detail": "token ต้องไม่มี whitespace"}
    return {"state": "configured"}


def _asset_count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("items", "entities", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
    return 0


def probe_endpoint(
    url: str,
    *,
    offline: bool = False,
    token: str | None = None,
    opener: Any | None = None,
) -> dict[str, Any]:
    """Probe a JSON endpoint with one bounded GET and no redirect."""

    if offline:
        return {
            "state": "skipped",
            "detail": "offline mode: ไม่ทำ network probe",
        }

    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "solo-empire-architecture-preflight/1",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    client = opener or build_opener(NoRedirectHandler())
    try:
        with client.open(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                return {
                    "state": "invalid",
                    "status_code": getattr(response, "status", 200),
                    "detail": "provider response ใหญ่เกินขอบเขตที่อนุญาต",
                }
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {
                    "state": "invalid",
                    "status_code": getattr(response, "status", 200),
                    "detail": "provider ตอบกลับไม่ใช่ JSON",
                }
            return {
                "state": "connected",
                "status_code": getattr(response, "status", 200),
                "asset_count": _asset_count(payload),
                "detail": "อ่าน JSON ด้วย GET สำเร็จ; ไม่เก็บ response body",
            }
    except HTTPError as error:
        if error.code in {401, 403}:
            state = "unauthorized"
            detail = (
                "provider ปฏิเสธการอ่าน (401/403) หลังส่ง runtime token"
                if token
                else "provider ปฏิเสธการอ่าน (401/403) โดยไม่มีการส่ง credential"
            )
        else:
            state = "unavailable"
            detail = f"provider ตอบ HTTP {error.code}"
        return {"state": state, "status_code": error.code, "detail": detail}
    except (TimeoutError, OSError, URLError):
        return {
            "state": "unavailable",
            "status_code": 0,
            "detail": "เชื่อมต่อ provider ไม่ได้หรือหมดเวลา",
        }


def _provider_report(
    provider_id: str,
    config: Mapping[str, Any],
    env: Mapping[str, str],
    *,
    offline: bool,
    opener: Any | None = None,
) -> dict[str, Any]:
    env_name = next(
        (name for name in config["env_names"] if (env.get(name) or "").strip()),
        None,
    )
    token_env_name = next(
        (name for name in config.get("token_env_names", ()) if (env.get(name) or "").strip()),
        None,
    )
    token_value = env.get(token_env_name, "") if token_env_name else ""
    token = safe_provider_token(token_value)
    base = safe_provider_url(env.get(env_name, "") if env_name else "")
    base_state = base.get("state")
    result: dict[str, Any] = {
        "id": provider_id,
        "label": config["label"],
        "environment": env_name,
        "state": base_state,
        "read_only": True,
        "credential_environment": token_env_name,
        "credential_configured": token.get("state") == "configured",
        "credentials_sent": False,
    }
    if base_state == "disabled":
        result["detail"] = (
            "ไม่ได้ตั้งค่า provider URL; ใช้ local repository registry ต่อไป"
        )
        return result
    if base_state == "invalid":
        result["detail"] = base["detail"]
        return result
    if token.get("state") == "invalid":
        result.update({"state": "invalid", "detail": token["detail"]})
        return result

    result["endpoint"] = base["display"]
    if offline:
        result.update(
            {
                "state": "skipped",
                "detail": "offline mode: ข้าม network probe",
            }
        )
        return result

    endpoint = f"{base['value'].rstrip('/')}/{config['path']}"
    runtime_token = token_value if token.get("state") == "configured" else None
    result.update(
        probe_endpoint(endpoint, token=runtime_token, opener=opener)
    )
    result["credentials_sent"] = bool(runtime_token) and not offline
    return result


def build_report(
    repo_root: Path = DEFAULT_REPO_ROOT,
    *,
    env: Mapping[str, str] | None = None,
    offline: bool = False,
    opener: Any | None = None,
) -> dict[str, Any]:
    """Build a redacted readiness report for local and optional providers."""

    runtime_env = env if env is not None else os.environ
    descriptor = validate_catalog_descriptor(repo_root / "catalog-info.yaml")
    archimate_model = validate_archimate_model(
        repo_root / ARCHIMATE_MODEL_RELATIVE_PATH
    )
    providers = [
        _provider_report(
            provider_id,
            config,
            runtime_env,
            offline=offline,
            opener=opener,
        )
        for provider_id, config in PROVIDERS.items()
    ]

    errors = list(descriptor["errors"])
    errors.extend(f"ArchiMate model: {item}" for item in archimate_model["errors"])
    provider_errors = [
        f"{item['label']}: {item['detail']}"
        for item in providers
        if item["state"] == "invalid"
    ]
    errors.extend(provider_errors)
    degraded = [
        item
        for item in providers
        if item["state"] in {"unavailable", "unauthorized"}
    ]
    if errors:
        status = "error"
    elif degraded:
        status = "degraded"
    else:
        status = "ready"

    configured_count = sum(
        1 for item in providers if item["state"] not in {"disabled", "skipped"}
    )
    return {
        "status": status,
        "mode": "offline"
        if offline
        else ("local-only" if not configured_count else "read-only-probe"),
        "catalog_descriptor": descriptor,
        "archimate_model": archimate_model,
        "providers": providers,
        "safety": {
            "network": "none" if offline or not configured_count else "bounded GET only",
            "writes": False,
            "credentials_sent": any(item.get("credentials_sent", False) for item in providers),
            "response_persisted": False,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "timeout_seconds": PROBE_TIMEOUT_SECONDS,
        },
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="Repository root containing catalog-info.yaml",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Validate configuration but never make a network request",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _print_report(report: Mapping[str, Any]) -> None:
    print("Solo Empire — Architecture Provider Readiness")
    print(f"  status: {report['status']}")
    print(f"  mode: {report['mode']}")
    descriptor = report["catalog_descriptor"]
    print(
        "  catalog-info.yaml: "
        f"{descriptor['state']} ({descriptor['valid_documents']}/{descriptor['documents']} documents)"
    )
    model = report["archimate_model"]
    print(
        "  ArchiMate model: "
        f"{model['state']} ({model['element_count']} elements, "
        f"{model['relationship_count']} relationships, {model['view_count']} views)"
    )
    for provider in report["providers"]:
        endpoint = f" endpoint={provider['endpoint']}" if provider.get("endpoint") else ""
        print(
            f"  {provider['label']}: {provider['state']}"
            f"{endpoint} — {provider['detail']}"
        )
    safety = report["safety"]
    print(
        "  safety: "
        f"network={safety['network']}, writes={safety['writes']}, "
        f"credentials_sent={safety['credentials_sent']}"
    )
    for error in report["errors"]:
        print(f"  error: {error}", file=sys.stderr)


def main() -> int:
    args = build_parser().parse_args()
    report = build_report(args.repo_root, offline=args.offline)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        _print_report(report)
    return 0 if report["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
