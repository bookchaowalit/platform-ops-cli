#!/usr/bin/env python3
"""Import and query the full Free SaaS Master Catalog workbook.

The workbook is a public reference source.  Importing it keeps the exact XLSX
bytes in the data lake and only then writes a reviewable JSON projection for
local discovery.  It does not create provider accounts, call external APIs,
or provision any SaaS service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import PROJECT_ROOT, sibling_repo_root  # noqa: E402


MASTER_CATALOG_CONTRACT = "solo-empire.free-saas-catalog.v1"
CONTRACT = MASTER_CATALOG_CONTRACT
SELECTOR_CONTRACT = "solo-empire.free-saas-stack-selector.v1"
SOURCE_ID = "free-saas-master-catalog"
DOMAIN = "operations"
DATASET = "free_saas_catalog"
SCHEMA_VERSION = "1"
DEFAULT_SOURCE = PROJECT_ROOT / "systems" / "cloud-platform" / "sources" / "Free-SaaS-Master-Catalog-Expanded-2026.xlsx"
DEFAULT_CATALOG = PROJECT_ROOT / "systems" / "cloud-platform" / "free-saas-master-catalog-2026.json"
DEFAULT_SELECTOR = PROJECT_ROOT / "systems" / "cloud-platform" / "free-saas-stack-selector-2026.json"
DEFAULT_LAKE_URI = os.environ.get(
    "SOLO_EMPIRE_DATA_LAKE_URI",
    os.environ.get("DATA_LAKE_URI", str(PROJECT_ROOT / "data" / "lake")),
)
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

SEARCH_FIELD_WEIGHTS = (
    ("service", 14),
    ("subcategory", 10),
    ("category", 8),
    ("best_use_case", 7),
    ("replaces", 5),
    ("free_tier_summary", 4),
    ("free_limits", 4),
    ("requests_compute", 3),
    ("storage", 3),
    ("bandwidth", 3),
    ("notes", 2),
    ("self_host_alternative", 2),
)

LOCK_IN_LEVELS = {
    "low": 0,
    "low-medium": 1,
    "medium": 2,
    "medium-high": 3,
    "high": 4,
}

# Workbook starter options normally match a service name exactly.  Keep the
# few aliases explicit so selector generation cannot silently pick a fuzzy
# match after a catalog update.  "Cloudflare" is intentionally a two-service
# bundle because the workbook layer names both DNS and CDN, while no standalone
# "Cloudflare" row exists in the catalog.
STARTER_OPTION_SERVICE_IDS = {
    "cloudflare": ("cloudflare-dns", "cloudflare-cdn"),
    "cloudflare kv": ("cloudflare-workers-kv",),
    "firebase auth": ("firebase-authentication",),
}

EMPTY_STARTER_OPTIONS = {"", "-", "—", "n/a", "none"}

SOURCE_HEADERS = (
    "Service",
    "Category",
    "Subcategory",
    "Free Tier Summary",
    "Free Limits",
    "Credit Card Required?",
    "Commercial Use?",
    "Sleeps/Pauses?",
    "Bandwidth",
    "Storage",
    "Requests/Compute",
    "Regions",
    "Custom Domain?",
    "API?",
    "Open Standard / Portability",
    "Vendor Lock-in Risk",
    "Overage / Paid Entry",
    "Free Forever?",
    "Self-host Alternative",
    "Best Use Case",
    "Replaces",
    "Notes",
    "Official URL",
    "Last Verified",
)


def _column_name(reference: str) -> str:
    match = re.match(r"([A-Z]+)", reference)
    if not match:
        raise ValueError(f"invalid cell reference {reference!r}")
    return match.group(1)


def _column_letters(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _header_key(header: str) -> str:
    value = header.strip().lower()
    value = value.replace("&", " and ").replace("/", " ")
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    if not value:
        raise ValueError(f"cannot normalize empty header {header!r}")
    return value


def _slug_id(value: str, row_number: int) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return result or f"service-{row_number}"


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        payload = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(payload)
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    return [
        "".join(text.text or "" for text in item.iter(f"{namespace}t"))
        for item in root
    ]


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    cell_type = cell.attrib.get("t")
    inline = cell.find(f"{namespace}is")
    if inline is not None:
        return "".join(text.text or "" for text in inline.iter(f"{namespace}t"))
    value = cell.find(f"{namespace}v")
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        try:
            return shared_strings[int(value.text)]
        except (IndexError, ValueError) as exc:
            raise ValueError(f"invalid shared string index {value.text!r}") from exc
    if cell_type == "b":
        return "TRUE" if value.text == "1" else "FALSE"
    return value.text


def _sheet_target(archive: zipfile.ZipFile, sheet_name: str) -> str:
    namespace = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "package": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relationship_map = {
        item.attrib["Id"]: item.attrib["Target"]
        for item in relationships.findall("package:Relationship", namespace)
    }
    for sheet in workbook.findall("main:sheets/main:sheet", namespace):
        if sheet.attrib.get("name") != sheet_name:
            continue
        relationship_id = sheet.attrib[f"{{{namespace['rel']}}}id"]
        target = relationship_map[relationship_id].lstrip("/")
        return target if target.startswith("xl/") else str(PurePosixPath("xl") / target)
    raise ValueError(f"workbook is missing sheet {sheet_name!r}")


def _sheet_rows(archive: zipfile.ZipFile, sheet_name: str) -> list[tuple[int, list[str]]]:
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    root = ET.fromstring(archive.read(_sheet_target(archive, sheet_name)))
    shared = _shared_strings(archive)
    rows: list[tuple[int, list[str]]] = []
    for row in root.findall(f".//{namespace}sheetData/{namespace}row"):
        values = {
            _column_name(cell.attrib["r"]): _cell_value(cell, shared)
            for cell in row.findall(f"{namespace}c")
        }
        max_column = max(
            (ord(column[0]) - 64 for column in values if len(column) == 1),
            default=0,
        )
        row_values = [values.get(_column_letters(index), "") for index in range(1, max_column + 1)]
        rows.append((int(row.attrib.get("r", len(rows) + 1)), row_values))
    return rows


def _optional_key_value_sheet(archive: zipfile.ZipFile, sheet_name: str) -> list[dict[str, str | None]]:
    try:
        rows = _sheet_rows(archive, sheet_name)
    except ValueError:
        return []
    if not rows:
        return []
    headers = rows[0][1]
    data_rows = rows[1:]
    # The workbook's How to Use sheet starts with a title row rather than a
    # formal header.  Keep that content as a stable topic/guidance pair.
    if len(headers) >= 2 and headers[0].strip() and not headers[1].strip():
        headers = ["Topic", "Guidance"]
    result: list[dict[str, str | None]] = []
    for _, values in data_rows:
        if not any(value.strip() for value in values):
            continue
        result.append(
            {
                _header_key(header): (values[index].strip() or None) if index < len(values) else None
                for index, header in enumerate(headers)
                if header.strip()
            }
        )
    return result


def _summary_metrics(archive: zipfile.ZipFile) -> dict[str, str | None]:
    """Read the metric/value block below the Summary sheet title row."""

    try:
        rows = _sheet_rows(archive, "Summary")
    except ValueError:
        return {}
    metric_row = next(
        (index for index, (_, values) in enumerate(rows) if values[:2] == ["Metric", "Value"]),
        None,
    )
    if metric_row is None:
        return {}
    metrics: dict[str, str | None] = {}
    for _, values in rows[metric_row + 1 :]:
        if len(values) < 2 or not values[0].strip():
            continue
        key = _header_key(values[0])
        metrics[key] = values[1].strip() or None
        if key == "catalog_date":
            break
    return metrics


def parse_workbook(path: Path) -> tuple[bytes, list[dict[str, Any]], dict[str, Any]]:
    """Read the workbook using the XLSX XML format without third-party readers."""

    raw = path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        rows = _sheet_rows(archive, "Catalog")
        if not rows:
            raise ValueError("Catalog sheet is empty")
        headers = tuple(value.strip() for value in rows[0][1])
        if headers != SOURCE_HEADERS:
            raise ValueError(
                "Catalog headers do not match the expected source contract: "
                f"{headers!r}"
            )

        key_by_index = tuple(_header_key(header) for header in headers)
        services: list[dict[str, Any]] = []
        for row_number, values in rows[1:]:
            if not any(value.strip() for value in values):
                continue
            record = {
                key: (values[index].strip() or None) if index < len(values) else None
                for index, key in enumerate(key_by_index)
            }
            service = record.get("service")
            if not isinstance(service, str) or not service:
                raise ValueError(f"Catalog row {row_number} has no Service value")
            services.append({"id": _slug_id(service, row_number), "source_row": row_number, **record})

        summary_metrics = _summary_metrics(archive)
        return raw, services, {
            "source_headers": list(headers),
            "workbook_summary": summary_metrics,
            "how_to_use": _optional_key_value_sheet(archive, "How to Use"),
            "starter_stacks": _optional_key_value_sheet(archive, "Starter Stacks"),
        }


def validate_services(services: Iterable[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    required = {"id", "source_row", *(_header_key(header) for header in SOURCE_HEADERS)}
    for index, service in enumerate(services, 1):
        label = f"services[{index}]"
        if not isinstance(service, dict):
            errors.append(f"{label}: must be an object")
            continue
        missing = sorted(required - set(service))
        if missing:
            errors.append(f"{label}: missing fields {missing}")
            continue
        service_id = service["id"]
        name = service["service"]
        if not isinstance(service_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", service_id):
            errors.append(f"{label}: invalid id {service_id!r}")
        elif service_id in seen_ids:
            errors.append(f"{label}: duplicate id {service_id!r}")
        else:
            seen_ids.add(service_id)
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}: service must be non-empty")
        elif name.casefold() in seen_names:
            errors.append(f"{label}: duplicate service {name!r}")
        else:
            seen_names.add(name.casefold())
        for field in ("category", "subcategory", "official_url", "last_verified"):
            value = service.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{label}: {field} must be non-empty")
        official_url = service.get("official_url")
        if isinstance(official_url, str) and not official_url.startswith("https://"):
            errors.append(f"{label}: official_url must use https")
        if not isinstance(service.get("source_row"), int) or service["source_row"] < 2:
            errors.append(f"{label}: source_row must be an integer >= 2")
    return errors


def build_projection(path: Path) -> dict[str, Any]:
    raw, services, workbook = parse_workbook(path)
    errors = validate_services(services)
    if errors:
        raise ValueError("invalid Free SaaS catalog:\n" + "\n".join(errors[:20]))

    categories = Counter(service["category"] for service in services)
    subcategories = Counter(
        (service["category"], service["subcategory"])
        for service in services
    )
    summary = workbook["workbook_summary"]
    snapshot_date = summary.get("catalog_date") or max(
        service["last_verified"] for service in services
    )
    return {
        "contract": CONTRACT,
        "version": 1,
        "source": {
            "file_name": path.name,
            "sheet": "Catalog",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "snapshot_date": snapshot_date,
            "record_count": len(services),
        },
        "columns": ["id", "source_row", *(_header_key(header) for header in SOURCE_HEADERS)],
        "summary": {
            "workbook": summary,
            "computed": {
                "services": len(services),
                "categories": len(categories),
                "subcategories": len(subcategories),
                "by_category": [
                    {"category": category, "services": count}
                    for category, count in sorted(categories.items(), key=lambda item: (-item[1], item[0]))
                ],
            },
        },
        "how_to_use": workbook["how_to_use"],
        "starter_stacks": workbook["starter_stacks"],
        "services": services,
    }


def validate_catalog(catalog: Any) -> list[str]:
    if not isinstance(catalog, dict):
        return ["catalog root must be an object"]
    errors: list[str] = []
    if catalog.get("contract") != CONTRACT:
        errors.append(f"contract must be {CONTRACT!r}")
    if catalog.get("version") != 1:
        errors.append("version must be 1")
    source = catalog.get("source")
    if not isinstance(source, dict):
        errors.append("source must be an object")
    elif not isinstance(source.get("record_count"), int):
        errors.append("source.record_count must be an integer")
    columns = catalog.get("columns")
    expected_columns = ["id", "source_row", *(_header_key(header) for header in SOURCE_HEADERS)]
    if columns != expected_columns:
        errors.append("columns do not match the source contract")
    services = catalog.get("services")
    if not isinstance(services, list) or not services:
        errors.append("services must be a non-empty list")
    else:
        errors.extend(validate_services(services))
        if isinstance(source, dict) and source.get("record_count") != len(services):
            errors.append("source.record_count does not match services length")
    summary = catalog.get("summary")
    if not isinstance(summary, dict) or not isinstance(summary.get("computed"), dict):
        errors.append("summary.computed must be an object")
    return errors


def load_catalog(path: Path = DEFAULT_CATALOG) -> dict[str, Any]:
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read catalog {path}: {exc}") from exc
    errors = validate_catalog(catalog)
    if errors:
        raise ValueError("invalid catalog:\n" + "\n".join(errors[:20]))
    return catalog


def search_services(
    services: Iterable[dict[str, Any]],
    query: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    normalized_query = query.casefold().strip() if query else None
    normalized_category = category.casefold().strip() if category else None
    result = []
    for service in services:
        if normalized_category and service["category"].casefold() != normalized_category:
            continue
        if normalized_query:
            haystack = json.dumps(service, ensure_ascii=False).casefold()
            if normalized_query not in haystack:
                continue
        result.append(service)
    return sorted(result, key=lambda item: (item["category"].casefold(), item["service"].casefold()))


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    tokens = re.findall(r"[^\W_]+", str(value).casefold(), flags=re.UNICODE)
    return " ".join(tokens)


def _value_is_uncertain(value: Any) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return True
    return any(
        marker in normalized
        for marker in (
            "verify",
            "varies",
            "vary",
            "depends",
            "check current",
            "see pricing",
            "plan dependent",
            "terms apply",
            "unknown",
        )
    )


def _strict_no_card(service: dict[str, Any]) -> bool:
    return _normalize_text(service.get("credit_card_required")) == "no"


def _explicit_commercial_use(service: dict[str, Any]) -> bool:
    return _normalize_text(service.get("commercial_use")).startswith("yes")


def service_review_flags(service: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    card = _normalize_text(service.get("credit_card_required"))
    if card != "no":
        if "yes" in card or "billing" in card:
            flags.append("credit-card-or-billing-required")
        else:
            flags.append("credit-card-policy-review")

    commercial = _normalize_text(service.get("commercial_use"))
    if not commercial.startswith("yes") or _value_is_uncertain(service.get("commercial_use")):
        flags.append("commercial-terms-review")
    if _value_is_uncertain(service.get("free_limits")):
        flags.append("current-limit-verification-required")
    if _normalize_text(service.get("sleeps_pauses")) != "no":
        flags.append("sleep-pause-policy-review")
    if _normalize_text(service.get("vendor_lock_in_risk")) == "varies":
        flags.append("lock-in-review")
    return flags


def _query_token_matches(query_token: str, field_token: str) -> bool:
    if query_token == field_token:
        return True
    return len(query_token) >= 4 and len(field_token) >= 4 and (
        query_token in field_token or field_token in query_token
    )


def _text_match_score(service: dict[str, Any], query: str) -> tuple[int, list[str]]:
    normalized_query = _normalize_text(query)
    query_tokens = normalized_query.split()
    if not query_tokens:
        raise ValueError("recommend query must contain at least one searchable token")

    score = 0
    matched_fields: list[str] = []
    matched_query_tokens: set[str] = set()
    for field, weight in SEARCH_FIELD_WEIGHTS:
        field_text = _normalize_text(service.get(field))
        if not field_text:
            continue
        field_tokens = field_text.split()
        field_matches = {
            query_token
            for query_token in query_tokens
            if any(_query_token_matches(query_token, field_token) for field_token in field_tokens)
        }
        token_hits = len(field_matches)
        if not token_hits:
            continue
        matched_query_tokens.update(field_matches)
        matched_fields.append(field)
        score += token_hits * weight
        if token_hits == len(query_tokens):
            score += weight * 2
        if normalized_query == field_text:
            score += weight * 4
        elif normalized_query in field_text:
            score += weight * 2
    if len(matched_query_tokens) != len(set(query_tokens)):
        return 0, []
    return score, matched_fields


def _quality_score(service: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    card = _normalize_text(service.get("credit_card_required"))
    if card == "no":
        score += 6
        reasons.append("ระบุว่าไม่ต้องใช้บัตรเครดิต")
    elif card.startswith("no"):
        score += 2
    elif "yes" in card or "billing" in card:
        score -= 6

    commercial = _normalize_text(service.get("commercial_use"))
    if commercial == "yes":
        score += 5
        reasons.append("ระบุว่าใช้เชิงพาณิชย์ได้")
    elif commercial.startswith("yes"):
        score += 2

    portability = _normalize_text(service.get("open_standard_portability"))
    portability_points = {
        "very high": 6,
        "high": 5,
        "medium": 2,
        "low medium": 1,
        "low": -2,
    }.get(portability, 0)
    score += portability_points
    if portability_points >= 5:
        reasons.append("portability สูง")

    lock_in = _normalize_text(service.get("vendor_lock_in_risk")).replace(" ", "-")
    lock_in_points = {
        "low": 5,
        "low-medium": 4,
        "medium": 1,
        "medium-high": -2,
        "high": -5,
    }.get(lock_in, 0)
    score += lock_in_points
    if lock_in_points >= 4:
        reasons.append("ความเสี่ยง vendor lock-in ต่ำ")

    if _normalize_text(service.get("sleeps_pauses")) == "no":
        score += 3
        reasons.append("ระบุว่าไม่ sleep/pause")
    if _normalize_text(service.get("api")) == "yes":
        score += 1
    if not _value_is_uncertain(service.get("free_limits")):
        score += 2
        reasons.append("มีรายละเอียด quota ใน snapshot")
    return score, reasons


def _matches_recommendation_filters(
    service: dict[str, Any],
    *,
    category: str | None = None,
    no_card: bool = False,
    commercial: bool = False,
    max_lock_in: str | None = None,
) -> bool:
    if category and service["category"].casefold() != category.casefold().strip():
        return False
    if no_card and not _strict_no_card(service):
        return False
    if commercial and not _explicit_commercial_use(service):
        return False
    if max_lock_in:
        actual = _normalize_text(service.get("vendor_lock_in_risk")).replace(" ", "-")
        if actual not in LOCK_IN_LEVELS or LOCK_IN_LEVELS[actual] > LOCK_IN_LEVELS[max_lock_in]:
            return False
    return True


def recommend_services(
    services: Iterable[dict[str, Any]],
    query: str,
    *,
    category: str | None = None,
    no_card: bool = False,
    commercial: bool = False,
    max_lock_in: str | None = None,
) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for service in services:
        if not _matches_recommendation_filters(
            service,
            category=category,
            no_card=no_card,
            commercial=commercial,
            max_lock_in=max_lock_in,
        ):
            continue
        match_score, matched_fields = _text_match_score(service, query)
        if not match_score:
            continue
        quality_score, quality_reasons = _quality_score(service)
        recommendations.append(
            {
                "id": service["id"],
                "service": service["service"],
                "category": service["category"],
                "subcategory": service["subcategory"],
                "score": match_score * 3 + quality_score,
                "match_score": match_score,
                "quality_score": quality_score,
                "matched_fields": matched_fields,
                "why": [
                    "ตรงกับข้อมูลใน " + ", ".join(matched_fields),
                    *quality_reasons,
                ],
                "review_flags": service_review_flags(service),
                "free_tier_summary": service.get("free_tier_summary"),
                "free_limits": service.get("free_limits"),
                "credit_card_required": service.get("credit_card_required"),
                "commercial_use": service.get("commercial_use"),
                "vendor_lock_in_risk": service.get("vendor_lock_in_risk"),
                "best_use_case": service.get("best_use_case"),
                "official_url": service["official_url"],
                "last_verified": service["last_verified"],
            }
        )
    return sorted(
        recommendations,
        key=lambda item: (-item["score"], -item["match_score"], item["service"].casefold()),
    )


def coverage_report(catalog: dict[str, Any]) -> dict[str, Any]:
    services = catalog["services"]
    service_ids = {service["id"] for service in services}
    official_urls = sum(
        1
        for service in services
        if isinstance(service.get("official_url"), str)
        and service["official_url"].startswith("https://")
    )
    workbook_summary = catalog["summary"].get("workbook", {})

    def workbook_count(key: str) -> int | None:
        value = workbook_summary.get(key)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    profiles = list(
        dict.fromkeys(
            row.get("use_case")
            for row in catalog.get("starter_stacks", [])
            if isinstance(row.get("use_case"), str) and row["use_case"]
        )
    )
    return {
        "candidate_pool": len(services),
        "addressable_by_unique_id": len(service_ids),
        "excluded_from_discovery": 0,
        "all_services_usable_for_discovery": len(service_ids) == len(services),
        "categories": catalog["summary"]["computed"].get("categories"),
        "subcategories": catalog["summary"]["computed"].get("subcategories"),
        "official_url_coverage": official_urls,
        "strict_no_card_candidates": sum(_strict_no_card(service) for service in services),
        "explicit_commercial_use_candidates": sum(
            _explicit_commercial_use(service) for service in services
        ),
        "starter_profiles": profiles,
        "starter_profile_count": len(profiles),
        "starter_layer_count": len(catalog.get("starter_stacks", [])),
        "verification": {
            "snapshot_date": catalog["source"].get("snapshot_date"),
            "rows_with_specific_quota_detail": workbook_count(
                "rows_with_specific_quota_detail"
            ),
            "rows_requiring_current_limit_verification": workbook_count(
                "rows_requiring_current_limit_verification"
            ),
            "official_terms_review_required_before_activation": True,
        },
        "activation": {
            "mode": "selection-only",
            "external_accounts_created": False,
            "external_services_provisioned": False,
            "credentials_used": False,
        },
    }


def _service_reference(service: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": service["id"],
        "service": service["service"],
        "category": service["category"],
        "subcategory": service["subcategory"],
        "free_tier_summary": service.get("free_tier_summary"),
        "free_limits": service.get("free_limits"),
        "official_url": service["official_url"],
        "last_verified": service["last_verified"],
        "review_flags": service_review_flags(service),
    }


def resolve_starter_option(
    label: str | None,
    services: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    normalized_label = _normalize_text(label)
    if normalized_label in EMPTY_STARTER_OPTIONS:
        return {
            "label": label,
            "resolution_status": "not-applicable",
            "services": [],
        }

    services_by_id = {service["id"]: service for service in services}
    services_by_name = {
        _normalize_text(service["service"]): service
        for service in services
    }
    declared_ids = STARTER_OPTION_SERVICE_IDS.get(normalized_label)
    if declared_ids:
        resolved = [services_by_id[service_id] for service_id in declared_ids if service_id in services_by_id]
        status = "bundle" if len(declared_ids) > 1 else "alias"
        if len(resolved) != len(declared_ids):
            return {
                "label": label,
                "resolution_status": "unresolved",
                "services": [_service_reference(service) for service in resolved],
            }
        return {
            "label": label,
            "resolution_status": status,
            "services": [_service_reference(service) for service in resolved],
        }

    exact = services_by_name.get(normalized_label)
    if exact:
        return {
            "label": label,
            "resolution_status": "exact",
            "services": [_service_reference(exact)],
        }
    return {
        "label": label,
        "resolution_status": "unresolved",
        "services": [],
    }


def build_stack_selector(catalog: dict[str, Any]) -> dict[str, Any]:
    profiles_by_name: dict[str, dict[str, Any]] = {}
    selected_ids: set[str] = set()
    unresolved_options: list[dict[str, str]] = []
    for row in catalog.get("starter_stacks", []):
        use_case = row.get("use_case")
        layer_name = row.get("layer")
        if not isinstance(use_case, str) or not use_case or not isinstance(layer_name, str) or not layer_name:
            continue
        profile = profiles_by_name.setdefault(use_case, {"use_case": use_case, "layers": []})
        options = {
            "primary": resolve_starter_option(row.get("primary_free_option"), catalog["services"]),
            "fallback_1": resolve_starter_option(row.get("fallback_1"), catalog["services"]),
            "fallback_2": resolve_starter_option(row.get("fallback_2"), catalog["services"]),
        }
        for role, option in options.items():
            selected_ids.update(service["id"] for service in option["services"])
            if option["resolution_status"] == "unresolved":
                unresolved_options.append(
                    {
                        "use_case": use_case,
                        "layer": layer_name,
                        "role": role,
                        "label": str(option.get("label") or ""),
                    }
                )
        profile["layers"].append(
            {
                "layer": layer_name,
                "why": row.get("why"),
                "options": options,
            }
        )

    coverage = coverage_report(catalog)
    coverage.update(
        {
            "available_for_ad_hoc_recommendation": len(catalog["services"]),
            "starter_selected_unique_services": len(selected_ids),
            "unselected_but_available_for_recommendation": len(catalog["services"])
            - len(selected_ids),
        }
    )
    return {
        "contract": SELECTOR_CONTRACT,
        "version": 1,
        "source_catalog": {
            "contract": catalog["contract"],
            "sha256": catalog["source"].get("sha256"),
            "snapshot_date": catalog["source"].get("snapshot_date"),
            "record_count": len(catalog["services"]),
        },
        "policy": {
            "candidate_scope": "all-catalog-services",
            "selection_mode": "offline-deterministic",
            "external_activation": "manual-after-official-review",
            "required_before_activation": [
                "verify-current-free-tier-and-quota",
                "verify-commercial-use-terms",
                "verify-card-billing-and-overage-policy",
                "verify-region-privacy-and-data-retention",
                "store-credentials-in-configured-secret-manager",
            ],
        },
        "default_profile": "Typical SaaS",
        "coverage": coverage,
        "profiles": list(profiles_by_name.values()),
        "unresolved_options": unresolved_options,
    }


def validate_stack_selector(selector: Any, catalog: dict[str, Any]) -> list[str]:
    if not isinstance(selector, dict):
        return ["selector root must be an object"]
    errors: list[str] = []
    if selector.get("contract") != SELECTOR_CONTRACT:
        errors.append(f"contract must be {SELECTOR_CONTRACT!r}")
    if selector.get("version") != 1:
        errors.append("version must be 1")

    services_by_id = {service["id"]: service for service in catalog["services"]}
    source = selector.get("source_catalog")
    if not isinstance(source, dict):
        errors.append("source_catalog must be an object")
    elif source.get("record_count") != len(services_by_id):
        errors.append("source_catalog.record_count does not match catalog")

    coverage = selector.get("coverage")
    if not isinstance(coverage, dict):
        errors.append("coverage must be an object")
    else:
        for field in ("candidate_pool", "addressable_by_unique_id", "available_for_ad_hoc_recommendation"):
            if coverage.get(field) != len(services_by_id):
                errors.append(f"coverage.{field} must include all catalog services")
        if coverage.get("excluded_from_discovery") != 0:
            errors.append("coverage.excluded_from_discovery must be 0")
        if coverage.get("all_services_usable_for_discovery") is not True:
            errors.append("coverage.all_services_usable_for_discovery must be true")

    profiles = selector.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        errors.append("profiles must be a non-empty list")
    else:
        profile_names = {
            profile.get("use_case")
            for profile in profiles
            if isinstance(profile, dict)
        }
        if selector.get("default_profile") not in profile_names:
            errors.append("default_profile must reference an available profile")
        layer_count = 0
        for profile_index, profile in enumerate(profiles, 1):
            layers = profile.get("layers") if isinstance(profile, dict) else None
            if not isinstance(layers, list) or not layers:
                errors.append(f"profiles[{profile_index}].layers must be non-empty")
                continue
            layer_count += len(layers)
            for layer_index, layer in enumerate(layers, 1):
                options = layer.get("options") if isinstance(layer, dict) else None
                if not isinstance(options, dict):
                    errors.append(
                        f"profiles[{profile_index}].layers[{layer_index}].options must be an object"
                    )
                    continue
                primary = options.get("primary")
                if not isinstance(primary, dict) or primary.get("resolution_status") in {
                    "unresolved",
                    "not-applicable",
                }:
                    errors.append(
                        f"profiles[{profile_index}].layers[{layer_index}] has no resolved primary"
                    )
                for role, option in options.items():
                    if not isinstance(option, dict):
                        errors.append(
                            f"profiles[{profile_index}].layers[{layer_index}].{role} must be an object"
                        )
                        continue
                    if option.get("resolution_status") == "unresolved":
                        errors.append(
                            f"profiles[{profile_index}].layers[{layer_index}].{role} is unresolved"
                        )
                    option_services = option.get("services")
                    if not isinstance(option_services, list):
                        errors.append(
                            f"profiles[{profile_index}].layers[{layer_index}].{role}.services must be a list"
                        )
                        continue
                    for service in option_services:
                        service_id = service.get("id") if isinstance(service, dict) else None
                        if service_id not in services_by_id:
                            errors.append(
                                f"profiles[{profile_index}].layers[{layer_index}].{role} references unknown service {service_id!r}"
                            )
        if layer_count != len(catalog.get("starter_stacks", [])):
            errors.append("profile layer count does not match catalog starter stacks")

    unresolved = selector.get("unresolved_options")
    if unresolved != []:
        errors.append("unresolved_options must be empty")
    return errors


def _render_recommendations(recommendations: list[dict[str, Any]]) -> str:
    if not recommendations:
        return "ไม่พบ SaaS ที่ตรงกับ query และ policy filter"
    lines: list[str] = []
    for index, recommendation in enumerate(recommendations, 1):
        flags = recommendation["review_flags"] or ["none-from-snapshot"]
        lines.extend(
            [
                f"{index}. {recommendation['service']} — score {recommendation['score']}",
                f"   หมวด: {recommendation['category']} / {recommendation['subcategory']}",
                f"   เหตุผล: {'; '.join(recommendation['why'])}",
                f"   ต้องตรวจ: {', '.join(flags)}",
                f"   Official: {recommendation['official_url']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def _render_stack(profile: dict[str, Any]) -> str:
    lines = [f"Stack: {profile['use_case']}"]
    for layer in profile["layers"]:
        primary = layer["options"]["primary"]
        primary_names = ", ".join(service["service"] for service in primary["services"])
        fallback_names = []
        for role in ("fallback_1", "fallback_2"):
            option = layer["options"][role]
            fallback_names.extend(service["service"] for service in option["services"])
        lines.append(f"- {layer['layer']}: {primary_names}")
        if fallback_names:
            lines.append(f"  สำรอง: {', '.join(fallback_names)}")
        if layer.get("why"):
            lines.append(f"  เหตุผล: {layer['why']}")
    return "\n".join(lines)


def _render_services(services: list[dict[str, Any]]) -> str:
    if not services:
        return "ไม่พบ SaaS ที่ตรงกับเงื่อนไข"
    lines = []
    for service in services:
        lines.extend(
            [
                f"[{service['category']} / {service['subcategory']}] {service['service']}",
                f"Free: {service.get('free_tier_summary') or 'ไม่มีรายละเอียดใน snapshot'}",
                f"Use: {service.get('best_use_case') or 'ไม่ระบุ'}",
                f"Official: {service['official_url']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def _catalog_path(value: str | None) -> Path:
    return Path(value).expanduser() if value else DEFAULT_CATALOG


def _source_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    # Relative to the caller's folder first (tooling tasks run there), then the document tree.
    if path.exists():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()


def command_import(args: argparse.Namespace) -> int:
    # data_lake moved to the bookchaowalit/data-lake-pipelines repository (override: SOLO_DATA_LAKE_ROOT).
    lake_scripts = sibling_repo_root("data-lake-pipelines") / "infra" / "scripts"
    if not (lake_scripts / "data_lake" / "ingest.py").is_file():
        print(
            f"ERROR: data_lake.ingest not found at {lake_scripts}. It moved to the "
            "bookchaowalit/data-lake-pipelines repository; clone it as a sibling checkout "
            "or set SOLO_DATA_LAKE_ROOT to its path.",
            file=sys.stderr,
        )
        return 2
    sys.path.insert(0, str(lake_scripts))
    source_path = _source_path(args.input)
    output_path = Path(args.output).expanduser().resolve()
    try:
        projection_ref = str(output_path.relative_to(PROJECT_ROOT))
    except ValueError:
        projection_ref = output_path.name
    raw, _, _ = parse_workbook(source_path)
    projection = build_projection(source_path)

    # Refuse a read-only store snapshot as either target before anything is written: the lake
    # import below would otherwise land before the projection write is refused.
    from _env import refuse_write_into_snapshot  # noqa: E402
    refuse_write_into_snapshot(output_path, "free_saas_catalog")
    if "://" not in str(args.data_lake_uri):
        refuse_write_into_snapshot(Path(args.data_lake_uri).expanduser().resolve(), "free_saas_catalog")

    # Import the exact XLSX before creating the JSON projection.  This keeps
    # the workbook recoverable and gives the projection a lake lineage record.
    from data_lake.ingest import ingest_payload  # noqa: PLC0415

    lake_result = ingest_payload(
        raw,
        projection["services"],
        source=SOURCE_ID,
        domain=DOMAIN,
        dataset=DATASET,
        data_lake_uri=args.data_lake_uri,
        input_format="xlsx",
        schema_version=SCHEMA_VERSION,
        source_record_id_field="id",
        event_time_field="last_verified",
        content_type=XLSX_CONTENT_TYPE,
        metadata={
            "contract": CONTRACT,
            "source_file_name": source_path.name,
            "source_sheet": "Catalog",
            "projection": projection_ref,
        },
        privacy_class="public",
        retention_class="reference-catalog",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(projection, ensure_ascii=False, indent=2) + "\n"
    unchanged = output_path.is_file() and output_path.read_text(encoding="utf-8") == serialized
    if not unchanged:
        output_path.write_text(serialized, encoding="utf-8")

    result = {
        "status": "success",
        "contract": CONTRACT,
        "projection": projection_ref,
        "projection_written": not unchanged,
        "record_count": len(projection["services"]),
        "lake": {
            "raw_key": lake_result["raw_key"],
            "bronze_key": lake_result["bronze_key"],
            "manifest_key": lake_result["manifest_key"],
            "run_id": lake_result["run_id"],
        },
    }
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"นำเข้า Free SaaS catalog สำเร็จ: {result['record_count']} รายการ")
        print(f"  Projection: {result['projection']}")
        print(f"  Bronze: {result['lake']['bronze_key']}")
        print(f"  Manifest: {result['lake']['manifest_key']}")
    return 0


def command_check(args: argparse.Namespace) -> int:
    catalog = load_catalog(_catalog_path(args.catalog))
    computed = catalog["summary"]["computed"]
    result = {
        "status": "ok",
        "services": len(catalog["services"]),
        "categories": computed.get("categories"),
        "subcategories": computed.get("subcategories"),
    }
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"OK: validated {result['services']} services, "
            f"{result['categories']} categories, {result['subcategories']} subcategories"
        )
    return 0


def command_list(args: argparse.Namespace) -> int:
    catalog = load_catalog(_catalog_path(args.catalog))
    services = search_services(catalog["services"], category=args.category)
    selected = services[args.offset : args.offset + args.limit]
    if args.as_json:
        print(json.dumps({"services": selected}, ensure_ascii=False, indent=2))
    else:
        print(_render_services(selected))
    return 0


def command_search(args: argparse.Namespace) -> int:
    catalog = load_catalog(_catalog_path(args.catalog))
    services = search_services(catalog["services"], query=args.query, category=args.category)
    selected = services[: args.limit]
    if args.as_json:
        print(json.dumps({"query": args.query, "services": selected}, ensure_ascii=False, indent=2))
    else:
        print(_render_services(selected))
    return 0


def command_coverage(args: argparse.Namespace) -> int:
    catalog = load_catalog(_catalog_path(args.catalog))
    result = coverage_report(catalog)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        verification = result["verification"]
        print(
            f"Coverage: {result['addressable_by_unique_id']}/{result['candidate_pool']} "
            "services ใช้ค้นหาและจัดอันดับได้"
        )
        print(
            f"  Categories: {result['categories']} | Subcategories: {result['subcategories']} | "
            f"Official URLs: {result['official_url_coverage']}"
        )
        print(
            f"  Starter stacks: {result['starter_profile_count']} profiles / "
            f"{result['starter_layer_count']} layers"
        )
        print(
            f"  Strict no-card: {result['strict_no_card_candidates']} | "
            f"Explicit commercial use: {result['explicit_commercial_use_candidates']}"
        )
        print(
            "  Quota detail: "
            f"{verification['rows_with_specific_quota_detail']} specific / "
            f"{verification['rows_requiring_current_limit_verification']} ต้องตรวจปัจจุบัน"
        )
        print("  External activation: none (selection-only)")
    return 0


def command_recommend(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise ValueError("--limit must be at least 1")
    catalog = load_catalog(_catalog_path(args.catalog))
    services = catalog["services"]
    eligible_pool = sum(
        _matches_recommendation_filters(
            service,
            category=args.category,
            no_card=args.no_card,
            commercial=args.commercial,
            max_lock_in=args.max_lock_in,
        )
        for service in services
    )
    recommendations = recommend_services(
        services,
        args.query,
        category=args.category,
        no_card=args.no_card,
        commercial=args.commercial,
        max_lock_in=args.max_lock_in,
    )
    selected = recommendations[: args.limit]
    result = {
        "query": args.query,
        "candidate_pool": len(services),
        "eligible_pool_after_filters": eligible_pool,
        "matched_pool": len(recommendations),
        "returned": len(selected),
        "filters": {
            "category": args.category,
            "strict_no_card": args.no_card,
            "explicit_commercial_use": args.commercial,
            "max_lock_in": args.max_lock_in,
        },
        "activation_mode": "selection-only",
        "recommendations": selected,
    }
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"พิจารณา {result['candidate_pool']} รายการ; ผ่าน filter {eligible_pool}; "
            f"ตรง query {result['matched_pool']}"
        )
        print(_render_recommendations(selected))
        print("\nหมายเหตุ: ตรวจ official terms/limits ก่อนเปิดใช้จริงทุกครั้ง")
    return 0


def command_stack(args: argparse.Namespace) -> int:
    catalog = load_catalog(_catalog_path(args.catalog))
    selector = build_stack_selector(catalog)
    errors = validate_stack_selector(selector, catalog)
    if errors:
        raise ValueError("invalid generated selector:\n" + "\n".join(errors[:20]))
    profiles = selector["profiles"]
    if not args.use_case:
        result = {
            "available_profiles": [profile["use_case"] for profile in profiles],
            "candidate_pool": selector["coverage"]["candidate_pool"],
        }
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Available stack profiles:")
            for profile in result["available_profiles"]:
                print(f"- {profile}")
        return 0

    normalized_use_case = _normalize_text(args.use_case)
    profile = next(
        (
            item
            for item in profiles
            if _normalize_text(item["use_case"]) == normalized_use_case
        ),
        None,
    )
    if profile is None:
        available = ", ".join(item["use_case"] for item in profiles)
        raise ValueError(f"unknown stack profile {args.use_case!r}; choose one of: {available}")
    if args.as_json:
        print(
            json.dumps(
                {
                    "candidate_pool": selector["coverage"]["candidate_pool"],
                    "activation_mode": "selection-only",
                    "profile": profile,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(_render_stack(profile))
        print("\nCandidate pool ทั้งหมด 648 รายการยังใช้ recommend เพื่อสลับแต่ละ layer ได้")
        print("ก่อน activate ต้องตรวจ official terms, quota, billing และ privacy")
    return 0


def command_selector(args: argparse.Namespace) -> int:
    catalog = load_catalog(_catalog_path(args.catalog))
    selector = build_stack_selector(catalog)
    errors = validate_stack_selector(selector, catalog)
    if errors:
        raise ValueError("invalid generated selector:\n" + "\n".join(errors[:20]))

    output_path = Path(args.output).expanduser().resolve()
    serialized = json.dumps(selector, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        try:
            current = output_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cannot read selector {output_path}: {exc}") from exc
        if current != serialized:
            raise ValueError(f"selector is stale: {output_path}")
        status = "current"
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        unchanged = output_path.is_file() and output_path.read_text(encoding="utf-8") == serialized
        if not unchanged:
            from _env import refuse_write_into_snapshot  # noqa: E402
            refuse_write_into_snapshot(output_path, "free_saas_catalog")
            output_path.write_text(serialized, encoding="utf-8")
        status = "unchanged" if unchanged else "written"

    try:
        output_ref = str(output_path.relative_to(PROJECT_ROOT))
    except ValueError:
        output_ref = str(output_path)
    result = {
        "status": status,
        "selector": output_ref,
        "candidate_pool": selector["coverage"]["candidate_pool"],
        "profiles": len(selector["profiles"]),
        "layers": sum(len(profile["layers"]) for profile in selector["profiles"]),
        "selected_unique_services": selector["coverage"]["starter_selected_unique_services"],
        "unresolved_options": len(selector["unresolved_options"]),
        "external_activation": False,
    }
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"Selector {status}: {result['selector']} | {result['candidate_pool']} candidates | "
            f"{result['profiles']} profiles / {result['layers']} layers | "
            f"unresolved {result['unresolved_options']}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    import_parser = subparsers.add_parser("import", help="lake-first import an XLSX catalog")
    import_parser.add_argument("--input", default=str(DEFAULT_SOURCE))
    import_parser.add_argument("--output", default=str(DEFAULT_CATALOG))
    import_parser.add_argument("--data-lake-uri", default=DEFAULT_LAKE_URI)
    import_parser.add_argument("--json", action="store_true", dest="as_json")
    import_parser.set_defaults(handler=command_import)

    check = subparsers.add_parser("check", help="validate the JSON projection")
    check.add_argument("--catalog")
    check.add_argument("--json", action="store_true", dest="as_json")
    check.set_defaults(handler=command_check)

    listing = subparsers.add_parser("list", help="list catalog entries")
    listing.add_argument("--catalog")
    listing.add_argument("--category")
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--json", action="store_true", dest="as_json")
    listing.set_defaults(handler=command_list)

    search = subparsers.add_parser("search", help="search names, limits, use cases, and notes")
    search.add_argument("query")
    search.add_argument("--catalog")
    search.add_argument("--category")
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--json", action="store_true", dest="as_json")
    search.set_defaults(handler=command_search)

    coverage = subparsers.add_parser(
        "coverage",
        help="prove discovery coverage across the complete candidate pool",
    )
    coverage.add_argument("--catalog")
    coverage.add_argument("--json", action="store_true", dest="as_json")
    coverage.set_defaults(handler=command_coverage)

    recommend = subparsers.add_parser(
        "recommend",
        help="rank matching services across the complete catalog",
    )
    recommend.add_argument("query")
    recommend.add_argument("--catalog")
    recommend.add_argument("--category")
    recommend.add_argument("--no-card", action="store_true")
    recommend.add_argument("--commercial", action="store_true")
    recommend.add_argument(
        "--max-lock-in",
        choices=tuple(LOCK_IN_LEVELS),
    )
    recommend.add_argument("--limit", type=int, default=10)
    recommend.add_argument("--json", action="store_true", dest="as_json")
    recommend.set_defaults(handler=command_recommend)

    stack = subparsers.add_parser(
        "stack",
        help="show a resolved workbook starter-stack profile",
    )
    stack.add_argument("use_case", nargs="?")
    stack.add_argument("--catalog")
    stack.add_argument("--json", action="store_true", dest="as_json")
    stack.set_defaults(handler=command_stack)

    selector = subparsers.add_parser(
        "selector",
        help="build or verify the deterministic all-catalog stack selector",
    )
    selector.add_argument("--catalog")
    selector.add_argument("--output", default=str(DEFAULT_SELECTOR))
    selector.add_argument("--check", action="store_true")
    selector.add_argument("--json", action="store_true", dest="as_json")
    selector.set_defaults(handler=command_selector)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    try:
        return args.handler(args)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
