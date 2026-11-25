#!/usr/bin/env python3
"""Check committed source-policy inventory without approving or collecting data."""
from __future__ import annotations

import sys
import argparse
import json
import re
import subprocess
from datetime import date
from pathlib import Path, PurePosixPath

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
SCHEMA_VERSION = "solo-empire.source-product-coverage.v1"
COVERAGE_PATH = "systems/ai-data/scraping-data-coverage.yaml"
POLICY_FIELDS = ("permission_status", "permission_basis", "reviewed_at")


def _policy_path(value):
    return isinstance(value, str) and re.fullmatch(
        r"src/[a-z][a-z0-9_]*/source(?:-[a-z0-9-]+)?\.json", value
    ) is not None


def load_inventory(root=ROOT):
    config = yaml.safe_load((root / COVERAGE_PATH).read_text(encoding="utf-8"))
    value = config["source_product_inventory"]["path"]
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError("inventory path must stay inside the owning repository")
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("inventory target must stay inside the owning repository")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate(data):
    errors = []
    if not isinstance(data, dict):
        return ["inventory must be a mapping"]
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported source-product inventory schema")
    products = data.get("products")
    if not isinstance(products, list):
        return errors + ["products must be a list"]
    if not products or data.get("expected_product_count") != len(products):
        errors.append("product count differs from the declared inventory")
    seen_products = set()
    seen_repositories = set()
    for product in products:
        if not isinstance(product, dict):
            errors.append("product must be a mapping")
            continue
        product_id = product.get("id")
        if not isinstance(product_id, str) or not re.fullmatch(r"book-[a-z-]+-data", product_id):
            errors.append("invalid product id")
        elif product_id in seen_products:
            errors.append(f"duplicate product: {product_id}")
        else:
            seen_products.add(product_id)
        repository = product.get("repository")
        if not isinstance(repository, str) or not re.fullmatch(r"bookchaowalit/opportunity-[a-z-]+-data", repository):
            errors.append(f"{product_id}: invalid owning repository")
        elif repository in seen_repositories:
            errors.append(f"{product_id}: duplicate owning repository")
        else:
            seen_repositories.add(repository)
        if not re.fullmatch(r"[0-9a-f]{40}", str(product.get("commit", ""))):
            errors.append(f"{product_id}: source commit must be immutable")
        if product.get("manifest_path") != "sources.json":
            errors.append(f"{product_id}: source manifest must be sources.json")
        sources = product.get("sources")
        if not isinstance(sources, list):
            errors.append(f"{product_id}: sources must be a list")
            continue
        if not sources or product.get("expected_source_count") != len(sources):
            errors.append(f"{product_id}: source count differs from the declared inventory")
        seen_sources = set()
        for source in sources:
            if not isinstance(source, dict):
                errors.append(f"{product_id}: source must be a mapping")
                continue
            source_id = source.get("id")
            if not isinstance(source_id, str) or not re.fullmatch(r"[a-z0-9-]+", source_id):
                errors.append(f"{product_id}: invalid source id")
            elif source_id in seen_sources:
                errors.append(f"{product_id}: duplicate source {source_id}")
            else:
                seen_sources.add(source_id)
            if not _policy_path(source.get("policy_path")):
                errors.append(f"{product_id}/{source_id}: unsafe or missing policy path")
            for field in POLICY_FIELDS:
                if not isinstance(source.get(field), str) or not source[field].strip():
                    errors.append(f"{product_id}/{source_id}: missing {field}")
            try:
                value = source.get("reviewed_at")
                if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                    raise ValueError
            except ValueError:
                errors.append(f"{product_id}/{source_id}: invalid review date")
    return errors


def build_report(data):
    errors = validate(data)
    if errors:
        raise ValueError("; ".join(errors))
    products = []
    for product in data["products"]:
        sources = [dict(source, evidence_path=(
            f"{product['repository']}@{product['commit']}:{source['policy_path']}"
        )) for source in product["sources"]]
        products.append({**product, "sources": sources})
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_kind": "committed_policy_metadata",
        "registry_authorizes_collection": False,
        "runtime_readiness": "not_assessed",
        "product_count": len(products),
        "source_count": sum(len(product["sources"]) for product in products),
        "products": products,
    }


def _git_json(repository, revision, path):
    result = subprocess.run(["git", "show", f"{revision}:{path}"], cwd=repository,
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def verify_sources(data, source_root):
    """Compare declared policy metadata with immutable and current owner configs."""
    errors = []
    for product in data["products"]:
        repository = source_root / product["repository"].split("/")[1]
        for revision in (product["commit"], "HEAD"):
            try:
                manifest = _git_json(repository, revision, "sources.json")
                if set(manifest["enabled_sources"]) != {s["id"] for s in product["sources"]}:
                    errors.append(f"{product['id']}@{revision}: source manifest drift")
                for source in product["sources"]:
                    policy = _git_json(repository, revision, source["policy_path"])
                    expected = {"permission_status": policy["permission_status"],
                                "permission_basis": policy["permission_evidence"],
                                "reviewed_at": policy["reviewed_at"]}
                    if policy["id"] != source["id"] or any(source[k] != v for k, v in expected.items()):
                        errors.append(f"{product['id']}/{source['id']}@{revision}: source policy drift")
            except (OSError, subprocess.CalledProcessError, KeyError, TypeError, ValueError):
                errors.append(f"{product['id']}@{revision}: source metadata unavailable or invalid")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print metadata and immutable evidence paths")
    parser.add_argument("--source-root", type=Path, help="Explicit folder containing the owning source checkouts")
    args = parser.parse_args(argv)
    try:
        data = load_inventory()
        errors = validate(data)
        if not errors and args.source_root:
            errors.extend(verify_sources(data, args.source_root))
        if errors:
            print(json.dumps({"status": "failed", "errors": errors}))
            return 1
        report = build_report(data)
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        print("Source-product coverage configuration could not be read or validated")
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Source-product coverage: {report['product_count']} products, {report['source_count']} sources; policy metadata valid")
        print("Collection, schedule, freshness and legal acceptance are not assessed by this registry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
