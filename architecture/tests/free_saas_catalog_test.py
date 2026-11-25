#!/usr/bin/env python3
"""Offline tests for the full Free SaaS catalog projection and importer."""

from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
sys.path.insert(0, str(TOOLS / "scripts"))

from free_saas_catalog import (  # noqa: E402
    DEFAULT_CATALOG,
    DEFAULT_SELECTOR,
    SOURCE_HEADERS,
    build_stack_selector,
    build_projection,
    coverage_report,
    load_catalog,
    recommend_services,
    search_services,
    validate_catalog,
    validate_stack_selector,
)


def _inline_cell(reference: str, value: str) -> str:
    escaped = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<c r="{reference}" t="inlineStr"><is><t>{escaped}</t></is></c>'


def _write_minimal_workbook(path: Path) -> None:
    headers = list(SOURCE_HEADERS)
    values = [
        "Example Service",
        "Hosting",
        "Static",
        "Free plan",
        "100 builds",
        "No",
        "Verify",
        "No",
        "1 GB",
        "1 GB",
        "100k requests",
        "Global",
        "Yes",
        "Yes",
        "High",
        "Low",
        "Verify",
        "Yes",
        "Static host",
        "Docs",
        "Legacy host",
        "Test row",
        "https://example.com",
        "2026-08-30",
    ]
    header_xml = "".join(_inline_cell(f"{chr(65 + i)}1", value) for i, value in enumerate(headers))
    value_xml = "".join(_inline_cell(f"{chr(65 + i)}2", value) for i, value in enumerate(values))
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Catalog" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData><row r=\"1\">{header_xml}</row><row r=\"2\">{value_xml}</row></sheetData>"
        "</worksheet>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


def test_projection_parser_reads_xlsx_xml_and_normalizes_fields() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "catalog.xlsx"
        _write_minimal_workbook(path)
        projection = build_projection(path)
        assert projection["source"]["record_count"] == 1
        assert projection["services"][0]["id"] == "example-service"
        assert projection["services"][0]["official_url"] == "https://example.com"


def test_checked_repository_projection_contains_all_source_rows() -> None:
    catalog = load_catalog(DEFAULT_CATALOG)
    assert validate_catalog(catalog) == []
    assert len(catalog["services"]) == 648
    assert catalog["summary"]["computed"]["categories"] == 29
    assert catalog["source"]["record_count"] == 648
    assert catalog["summary"]["workbook"]["catalog_date"] == "2026-08-30"


def test_search_matches_any_service_field_and_category() -> None:
    catalog = load_catalog(DEFAULT_CATALOG)
    hosting = search_services(catalog["services"], query="static", category="Hosting")
    assert hosting
    assert all(item["category"] == "Hosting" for item in hosting)
    assert any("static" in (item.get("best_use_case") or "").casefold() for item in hosting)


def test_validation_rejects_duplicate_service_ids() -> None:
    catalog = load_catalog(DEFAULT_CATALOG)
    duplicate = json.loads(json.dumps(catalog))
    duplicate["services"].append(dict(duplicate["services"][0]))
    errors = validate_catalog(duplicate)
    assert any("duplicate id" in error for error in errors)


def test_coverage_exposes_every_catalog_service_without_activation() -> None:
    catalog = load_catalog(DEFAULT_CATALOG)
    coverage = coverage_report(catalog)
    assert coverage["candidate_pool"] == 648
    assert coverage["addressable_by_unique_id"] == 648
    assert coverage["official_url_coverage"] == 648
    assert coverage["excluded_from_discovery"] == 0
    assert coverage["all_services_usable_for_discovery"] is True
    assert coverage["starter_profile_count"] == 5
    assert coverage["starter_layer_count"] == 18
    assert coverage["activation"] == {
        "mode": "selection-only",
        "external_accounts_created": False,
        "external_services_provisioned": False,
        "credentials_used": False,
    }


def test_recommendation_ranks_relevance_and_known_quality_deterministically() -> None:
    catalog = load_catalog(DEFAULT_CATALOG)
    recommendations = recommend_services(catalog["services"], "serverless postgresql")
    assert recommendations
    assert recommendations[0]["id"] == "neon"
    assert recommendations[0]["score"] > recommendations[1]["score"]
    assert all(item["matched_fields"] for item in recommendations)
    assert not {"let-s-encrypt", "dead-man-s-snitch"} & {
        item["id"] for item in recommendations
    }


def test_recommendation_enforces_strict_policy_filters() -> None:
    catalog = load_catalog(DEFAULT_CATALOG)
    recommendations = recommend_services(
        catalog["services"],
        "serverless postgresql",
        no_card=True,
        commercial=True,
        max_lock_in="low",
    )
    assert recommendations
    assert recommendations[0]["id"] == "neon"
    assert all(item["credit_card_required"] == "No" for item in recommendations)
    assert all((item["commercial_use"] or "").startswith("Yes") for item in recommendations)
    assert all(item["vendor_lock_in_risk"] == "Low" for item in recommendations)


def test_selector_resolves_every_workbook_option_and_keeps_all_candidates_available() -> None:
    catalog = load_catalog(DEFAULT_CATALOG)
    selector = build_stack_selector(catalog)
    assert validate_stack_selector(selector, catalog) == []
    assert selector["unresolved_options"] == []
    assert selector["coverage"]["candidate_pool"] == 648
    assert selector["coverage"]["available_for_ad_hoc_recommendation"] == 648
    assert selector["default_profile"] == "Typical SaaS"
    assert len(selector["profiles"]) == 5
    assert sum(len(profile["layers"]) for profile in selector["profiles"]) == 18

    typical = next(profile for profile in selector["profiles"] if profile["use_case"] == "Typical SaaS")
    edge = next(layer for layer in typical["layers"] if layer["layer"] == "DNS/CDN/WAF")
    assert edge["options"]["primary"]["resolution_status"] == "bundle"
    assert {service["id"] for service in edge["options"]["primary"]["services"]} == {
        "cloudflare-dns",
        "cloudflare-cdn",
    }


def test_checked_selector_artifact_matches_deterministic_projection() -> None:
    catalog = load_catalog(DEFAULT_CATALOG)
    stored = json.loads(DEFAULT_SELECTOR.read_text(encoding="utf-8"))
    assert stored == build_stack_selector(catalog)
    assert validate_stack_selector(stored, catalog) == []


def test_import_fails_clearly_when_solo_data_lake_checkout_is_absent() -> None:
    import argparse
    import contextlib
    import io
    import os

    from free_saas_catalog import command_import

    previous = os.environ.get("SOLO_DATA_LAKE_ROOT")
    os.environ["SOLO_DATA_LAKE_ROOT"] = "/nonexistent/solo-data-lake"
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            code = command_import(argparse.Namespace(
                input="x.xlsx", output="/tmp/x.json", data_lake_uri="", as_json=False,
            ))
    finally:
        if previous is None:
            del os.environ["SOLO_DATA_LAKE_ROOT"]
        else:
            os.environ["SOLO_DATA_LAKE_ROOT"] = previous
    assert code == 2
    assert "data-lake" in stderr.getvalue() and "SOLO_DATA_LAKE_ROOT" in stderr.getvalue()


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
    print("OK: free SaaS catalog tests passed")
