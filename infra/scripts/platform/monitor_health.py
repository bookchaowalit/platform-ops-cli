#!/usr/bin/env python3
"""Read-only central monitoring projection. No providers, secrets, or writes.

Only bounded loopback health GETs, aggregate SQLite queries, registered metadata
and the existing redacted scraping health report are consumed. Inventory and
record counts never establish runtime health or deployment readiness.
"""
from __future__ import annotations

import argparse
import http.client
import io
import json
import os
import re
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from _env import ARCHITECTURE_ROOT, PROJECT_ROOT, REGISTRY_DIR, repo_file, sibling_repo_root  # noqa: E402
from cell_store import CellStoreError, open_cell_store  # noqa: E402
from notifications_store import NotificationsStoreError, open_notifications_store  # noqa: E402
from task_store import ClientConfigError  # noqa: E402

SYSTEM_SOURCE = "repository-catalog/registries/system-repositories.yaml"
CLOUD_SOURCE = "infra/platform/cloud-providers.yaml"
PRODUCT_SOURCE = "systems/architecture/data-product-contracts-v4.yaml"
SCRAPING_SOURCE = "data/generated/briefings/scraping-collection-health.json"
MAX_BYTES = 2_000_000
MAX_TARGETS = 128
MAX_PROBES = 40
TIMEOUT = 0.75
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
HEALTH_PATHS = {"/health", "/healthz", "/api/v1/health"}
# Compatibility targets for packages predating service.yaml, from their READMEs.
LEGACY_TARGETS = {"cloud-api": (3440, "/health"), "production-hub": (3460, "/api/v1/health")}
SERVICE_SYSTEMS = {"knowledge-api": "knowledge",
                   "cloud-api": "cloud", "accounts-api": "profile-identity",
                   "integrations-api": "integrations", "saas-catalog-api": "assets-catalog",
                   "awesome-api": "knowledge", "production-hub": "projects"}


def timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def item(ident, label, category, source, *, now, status="unknown", coverage="missing",
         observed_at=None, metrics=None, message="ยังไม่มีหลักฐานตรวจสุขภาพ",
         next_action="เชื่อมสัญญาณตรวจสุขภาพของระบบนี้", detail_href=None, max_age=86400):
    observed = timestamp(observed_at)
    age = (now - observed).total_seconds() if observed else None
    freshness = "unknown"
    if age is not None and age >= -300:
        freshness = "stale" if age > max_age else "fresh"
    elif observed:
        observed = None
    if freshness == "stale" and status != "error":
        status = "warning"
        next_action = "ตรวจแหล่งข้อมูลและสร้างหลักฐานตรวจสุขภาพล่าสุด"
    if status == "healthy" and freshness == "unknown":
        status = "unknown"
    return {"id": ident, "label": label, "category": category, "status": status,
            "coverage": coverage, "freshness": freshness,
            "observed_at": observed.isoformat() if observed else None, "source": source,
            "metrics": metrics or {}, "message": message, "next_action": next_action,
            "detail_href": detail_href}


def _locate(root, relative):
    """(file, containing root). The default root serves registry/architecture files from the repositories
    that own them now (SOLO_REGISTRY_DIR, SOLO_ARCHITECTURE_ROOT); any other root is used verbatim."""
    if Path(root) == PROJECT_ROOT:
        head = Path(relative).parts[0] if Path(relative).parts else ""
        if head == "repository-catalog":
            return repo_file(relative), (REGISTRY_DIR or Path("/nonexistent-solo-registry"))
        if head in {"docs", "systems", "operations", "templates"}:
            return repo_file(relative), ARCHITECTURE_ROOT
    return root / relative, root


def read_mapping(root, relative, *, json_format=False):
    path, base = _locate(root, relative)
    try:
        # Never follow a report/registry symlink into another data boundary.
        if path.is_symlink() or not path.resolve().is_relative_to(base.resolve()) or path.stat().st_size > MAX_BYTES:
            return None
        content = path.read_text(encoding="utf-8")
        data = json.loads(content) if json_format else yaml.safe_load(content)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, UnicodeError, yaml.YAMLError, RecursionError):
        return None


def read_inventory(root, relative, key, *, require_name=False):
    data = read_mapping(root, relative)
    entries = data.get(key) if data else None
    if not isinstance(entries, list) or not entries:
        return [], False
    valid = len(entries) <= MAX_TARGETS
    seen, result = set(), []
    for entry in entries[:MAX_TARGETS]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not SAFE_ID.fullmatch(entry["id"]) or entry["id"] in seen:
            valid = False
            continue
        if require_name and (not isinstance(entry.get("name"), str) or not entry["name"].strip()):
            valid = False
            continue
        seen.add(entry["id"])
        result.append(entry)
    return result, valid


def probe_health(port, path):
    if type(port) is not int or not 1024 <= port <= 65535 or path not in HEALTH_PATHS:
        return {"status": "unknown", "message": "ไม่มี health endpoint ที่อนุญาต"}
    try:
        # A total wall-clock budget covers headers and body, even when a broken
        # server trickles bytes. Direct fixed-loopback sockets ignore proxies
        # and cannot follow redirects or send ambient credentials.
        deadline = time.monotonic() + TIMEOUT
        with socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT) as connection:
            request = f"GET {path} HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nAccept: application/json\r\nConnection: close\r\n\r\n"
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            connection.sendall(request.encode("ascii"))
            raw = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                connection.settimeout(remaining)
                chunk = connection.recv(4096)
                if not chunk:
                    break
                raw.extend(chunk)
                boundary = raw.find(b"\r\n\r\n")
                if len(raw) > 65536 + 8192 or (boundary < 0 and len(raw) > 8192) or boundary > 8192:
                    return {"status": "error", "message": "health response เกินขนาดที่อนุญาต"}
            class BufferedSocket:
                def makefile(self, *args, **kwargs):
                    return io.BytesIO(raw)
            response = http.client.HTTPResponse(BufferedSocket())
            response.begin()
            if response.status != 200:
                status = "unknown" if response.status in {401, 403} else "error"
                return {"status": status, "message": f"health endpoint ตอบ HTTP {response.status}"}
            # Read the declared body in full so truncated Content-Length or
            # chunked responses cannot masquerade as a successful observation.
            body = response.read()
            if len(body) > 65536:
                return {"status": "error", "message": "health response เกินขนาดที่อนุญาต"}
            payload = json.loads(body)
            state = payload.get("status") if isinstance(payload, dict) else None
            if not isinstance(state, str):
                return {"status": "unknown", "message": "รูปแบบ health status ไม่รองรับ"}
            if state in {"ok", "healthy", "ready"}:
                return {"status": "healthy", "message": "health endpoint ตอบปกติบนเครื่องนี้"}
            if state in {"error", "failed", "unhealthy", "degraded"}:
                return {"status": "error", "message": "health endpoint รายงานปัญหา"}
            return {"status": "unknown", "message": "ตอบกลับแล้ว แต่รูปแบบสุขภาพยังไม่รองรับ"}
    except (OSError, ValueError, RecursionError, http.client.HTTPException, TimeoutError):
        return {"status": "unavailable", "message": "เข้าถึง health endpoint บนเครื่องนี้ไม่ได้"}


def notifications_check(now):
    """Seven-day delivery aggregates read through the notifications cell (not the retired SQLite table)."""
    ident, label, category = "notifications", "การส่งแจ้งเตือน", "notifications"
    make = lambda *args, **kwargs: item(*args, "Notifications API aggregate", now=now, **kwargs)
    try:
        since = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        entries = open_notifications_store("reporting-reader").notifications(since=since)
    except (NotificationsStoreError, ClientConfigError):
        return make(ident, label, category, status="unavailable", message="อ่านบริการแจ้งเตือนไม่ได้")
    delivered_at = [e["delivered_at"] for e in entries if e.get("delivered_at")]
    metrics = {"total": len(entries), "failed": sum(bool(e.get("delivery_error")) for e in entries),
               "delivered": len(delivered_at)}
    return make(ident, label, category, status="error" if metrics["failed"] else "unknown", coverage="observed",
                observed_at=max(delivered_at) if delivered_at else None, metrics=metrics,
                message="สรุปประวัติในฐานข้อมูล; ไม่ยืนยันว่า worker, provider หรือ backup กู้คืนได้",
                next_action="ตรวจรายการผิดพลาดและยืนยันการรันล่าสุด" if metrics["failed"] else "ตรวจ heartbeat และหลักฐานการทำงานจริงของระบบ")


def cron_check(now):
    """Active scheduler jobs read through the automation cell (not the retired SQLite table)."""
    ident, label, category = "cron", "งานอัตโนมัติ / scheduler", "automation"
    make = lambda *args, **kwargs: item(*args, "Automation API aggregate", now=now, **kwargs)
    try:
        jobs = open_cell_store("automation", "reporting-reader").list("cron-jobs", is_active=True)
    except (CellStoreError, ClientConfigError):
        return make(ident, label, category, status="unavailable", message="อ่านบริการ automation ไม่ได้")
    ran = [j["last_run_at"] for j in jobs if j.get("last_run_at")]
    metrics = {"total": len(jobs), "failed": sum(j.get("last_status") in ("failed", "error") for j in jobs)}
    return make(ident, label, category, status="error" if metrics["failed"] else "unknown", coverage="observed",
                observed_at=max(ran, key=lambda value: timestamp(value) or datetime.min.replace(tzinfo=timezone.utc)) if ran else None,
                metrics=metrics,
                message="สรุปประวัติในฐานข้อมูล; ไม่ยืนยันว่า worker, provider หรือ backup กู้คืนได้",
                next_action="ตรวจรายการผิดพลาดและยืนยันการรันล่าสุด" if metrics["failed"] else "ตรวจ heartbeat และหลักฐานการทำงานจริงของระบบ")


def build_report(root=ROOT, *, now=None, probe=True):
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    checks = []
    if probe:  # the notifications and automation cells are remote: --offline reports them as not probed
        checks.append(cron_check(now))
        checks.append(notifications_check(now))
    else:
        checks.append(item("cron", "งานอัตโนมัติ / scheduler", "automation", "Automation API aggregate", now=now,
                           status="unknown", message="offline: ไม่ได้อ่านบริการ automation"))
        checks.append(item("notifications", "การส่งแจ้งเตือน", "notifications", "Notifications API aggregate", now=now,
                           status="unknown", message="offline: ไม่ได้อ่านบริการแจ้งเตือน"))
    systems_raw, valid_systems = read_inventory(root, SYSTEM_SOURCE, "systems", require_name=True)
    if not valid_systems:
        checks.append(item("system-registry", "รายการระบบทั้งหมด", "coverage", SYSTEM_SOURCE, now=now, status="unavailable", message="อ่านรายการระบบไม่ได้; จำนวนระบบอาจไม่ครบ"))
    targets = []
    service_root = root / "services"
    if service_root.is_dir() and not service_root.is_symlink():
        for directory in sorted(service_root.iterdir())[:MAX_TARGETS]:
            if not directory.is_dir() or directory.is_symlink() or not SAFE_ID.fullmatch(directory.name):
                continue
            source = f"services/{directory.name}/service.yaml"
            manifest = read_mapping(root, source)
            fallback = LEGACY_TARGETS.get(directory.name)
            if manifest:
                port, path = manifest.get("port"), manifest.get("health")
            elif fallback and not (directory / "service.yaml").exists():
                port, path = fallback
                source = f"services/{directory.name}/README.md"
            else:
                port, path = None, None
            targets.append((f"service:{directory.name}", directory.name, "services", source, port, path))
    products, valid_products = read_inventory(root, PRODUCT_SOURCE, "products")
    for product in products:
        targets.append((f'data-product:{product["id"]}', product["id"], "data-products", PRODUCT_SOURCE, product.get("port"), "/healthz"))
    if not valid_products:
        checks.append(item("product-registry", "Data product inventory", "coverage", PRODUCT_SOURCE, now=now, status="unavailable", message="อ่าน data product registry ไม่ได้"))

    def target_check(target):
        ident, label, category, source, port, path = target
        valid = type(port) is int and 1024 <= port <= 65535 and isinstance(path, str) and path in HEALTH_PATHS
        if not valid or not probe:
            return item(ident, label, category, source, now=now, message="ยังไม่มี health target ที่รองรับ" if not valid else "ปิดการตรวจ loopback ในรอบนี้", next_action="กำหนด service.yaml health/port หรือเปิดบริการบนเครื่องนี้")
        result = probe_health(port, path)
        return item(ident, label, category, source, now=now, status=result["status"], coverage="observed", observed_at=now, metrics={"port": port}, message=result["message"], next_action="ตรวจบริการและ health endpoint ตามพอร์ตที่ขึ้นทะเบียน")
    with ThreadPoolExecutor(max_workers=8) as pool:
        checks.extend(pool.map(target_check, targets[:MAX_PROBES]))
    for ident, label, category, source, _, _ in targets[MAX_PROBES:]:
        checks.append(item(ident, label, category, source, now=now, message="ยังไม่ตรวจ: เกินงบจำนวน probes ต่อรอบ", next_action="จัดกลุ่มหรือปรับงบการตรวจร่วมกับ timeout ของ API"))

    providers, valid_providers = read_inventory(root, CLOUD_SOURCE, "providers", require_name=True)
    for provider in providers:
        checks.append(item(f'cloud:{provider["id"]}', provider["name"], "cloud", CLOUD_SOURCE, now=now, message="มีใน provider registry; ยังไม่มี live observation ใน monitor นี้", next_action="ใช้ cloud health workflow ที่ได้รับอนุมัติเพื่อเชื่อมหลักฐาน provider", detail_href="/cloud"))
    if not valid_providers:
        checks.append(item("cloud-registry", "Cloud provider inventory", "coverage", CLOUD_SOURCE, now=now, status="unavailable", message="อ่าน cloud provider registry ไม่ได้"))
    scraping = read_mapping(root, SCRAPING_SOURCE, json_format=True)
    required_metrics = {"required_products", "end_to_end_ready", "gap_count", "validation_error_count"}
    valid_scraping = bool(scraping and scraping.get("contract") == "solo-empire.collection-health.v1" and isinstance(scraping.get("summary"), dict) and timestamp(scraping.get("generated_at")) and isinstance(scraping.get("status"), str) and scraping["status"] in {"ok", "attention", "error"})
    if valid_scraping:
        values = scraping["summary"]
        valid_scraping = all(type(values.get(k)) is int and values[k] >= 0 for k in required_metrics)
        valid_scraping = valid_scraping and values["required_products"] > 0 and values["end_to_end_ready"] <= values["required_products"]
        if scraping.get("status") == "ok":
            valid_scraping = valid_scraping and values["end_to_end_ready"] == values["required_products"] and values["gap_count"] == 0 and values["validation_error_count"] == 0
    if valid_scraping:
        metrics = {k: v for k, v in scraping["summary"].items() if k in {"required_products", "end_to_end_ready", "gap_count", "validation_error_count"} and type(v) is int and v >= 0}
        status = {"ok": "healthy", "attention": "warning", "error": "error"}.get(scraping.get("status"), "unknown")
        checks.append(item("scraping", "Scraping / lake / collection readiness", "data-products", SCRAPING_SOURCE, now=now, status=status, coverage="observed", observed_at=scraping["generated_at"], metrics=metrics, message="ใช้รายงาน collection-health เดิม; ไม่ติดตั้ง scheduler", next_action="ตรวจ coverage gaps และรัน scraping:monitor เพื่ออัปเดตหลักฐาน"))
    else:
        checks.append(item("scraping", "Scraping / lake / collection readiness", "data-products", SCRAPING_SOURCE, now=now, status="unavailable", message="รายงาน collection-health หายหรือรูปแบบไม่รองรับ", next_action="รัน scraping:monitor เพื่อสร้างรายงานที่ตรวจสอบแล้ว"))
    tools, valid_tools = read_inventory(sibling_repo_root("content-production"), "catalog/production-tools.yaml", "tools", require_name=True)
    if valid_tools and all(isinstance(tool.get("owning_repo"), str) and tool["owning_repo"] for tool in tools):
        missing = 0
        try:
            checkouts = (read_mapping(root, "repository-catalog/registries/runtime-boundaries.yaml") or {}).get("repositories") or {}
        except (OSError, yaml.YAMLError):
            checkouts = {}
        for tool in tools:
            # owning_repo is bookchaowalit/<repo>; runtime-boundaries says where it is checked out here.
            ref = tool["owning_repo"]
            entry = checkouts.get(ref.split("/", 1)[1]) if ref.startswith("bookchaowalit/") else None
            owner_rel = entry.get("path", ref) if isinstance(entry, dict) else ref
            default_root = Path(root) == PROJECT_ROOT
            owner = repo_file(owner_rel) if default_root else root / owner_rel
            try:
                escapes = ".." in Path(owner_rel).parts if default_root else not owner.resolve().is_relative_to(root.resolve())
                missing += int(escapes or not owner.is_dir())
            except (OSError, RuntimeError, ValueError):
                missing += 1
        checks.append(item("production-tools", "Production tools / owning checkouts", "delivery", "book-production-platform:catalog/production-tools.yaml", now=now, status="error" if missing else "unknown", coverage="observed", observed_at=now, metrics={"tools": len(tools), "missing_checkouts": missing}, message="ตรวจเฉพาะการมี checkout ตาม catalog; ไม่อ่านเนื้อหาหรือสั่งรัน tool", next_action="คืน checkout ที่ขาดตามขอบเขตเจ้าของ และตรวจสัญญา tool" if missing else "ยืนยัน runtime และ acceptance gate ของแต่ละ tool"))
    else:
        checks.append(item("production-tools", "Production tools / owning checkouts", "delivery", "book-production-platform:catalog/production-tools.yaml", now=now, status="unavailable", message="อ่าน production tools catalog ไม่ได้"))
    for ident, label, category, source in [
        ("delivery", "CI/CD / deployments", "delivery", "repository-catalog/registries/system-repositories.yaml"),
        ("self-hosted", "Self-hosted runtime / containers", "self-hosted", "infra/scripts/platform/self_hosted_health.py"),
        ("observability", "Metrics / logs / traces", "coverage", SYSTEM_SOURCE),
    ]:
        checks.append(item(ident, label, category, source, now=now, message="มี workflow หรือขอบเขตระบบ; ยังไม่มี runtime evidence ที่เชื่อมกับ monitor นี้", next_action="เชื่อมรายงานหรือ heartbeat จากเจ้าของระบบ"))
    mapping = {"operating": ["cron"], "communications": ["notifications"],
               "workflow-platform": ["cron"], "data-products": ["scraping"], "developer-platform": ["delivery"],
               "analytics-observability": ["observability"], "projects": ["production-tools"]}
    systems = []
    by_id = {c["id"]: c for c in checks}
    for system in systems_raw:
        ident = system["id"]
        linked = [f"domain:{ident}"] + mapping.get(ident, [])
        linked += [f"service:{name}" for name, owner in SERVICE_SYSTEMS.items() if owner == ident]
        if ident == "cloud":
            linked += [c["id"] for c in checks if c["category"] == "cloud"]
        if ident == "data-products":
            linked += [c["id"] for c in checks if c["id"].startswith("data-product:")]
        linked = [i for i in linked if i in by_id]
        systems.append({"id": ident, "label": system["name"], "coverage": "observed" if any(by_id[i]["coverage"] == "observed" for i in linked) else "missing", "check_ids": linked})
    summary = {"total": len(checks), "healthy": 0, "attention": 0, "unavailable": 0, "unknown": 0,
               "stale": 0, "coverage_observed": 0, "coverage_missing": 0}
    for check in checks:
        summary["attention" if check["status"] in {"warning", "error"} else check["status"]] += 1
        summary[f'coverage_{check["coverage"]}'] += 1
        summary["stale"] += int(check["freshness"] == "stale")
    return {"schema_version": "solo-empire.monitor.v1", "generated_at": now.isoformat(), "mode": "local-read-only",
            "summary": summary, "checks": checks, "systems": systems,
            "limits": {"remote_probes": False, "alert_delivery": False, "refresh_seconds": 30}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Skip even loopback health GETs")
    args = parser.parse_args()
    report = build_report(probe=not args.offline)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
