#!/usr/bin/env python3
"""Smoke tests for the generated unified document catalog."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from _env import PROJECT_ROOT as REPO_ROOT  # noqa: E402  (the document tree, SPEC-001)
SCRIPT = TOOLS / "scripts" / "document_catalog.py"
CATALOG = REPO_ROOT / "data" / "indexes" / "document-catalog.json"
sys.path.insert(0, str(SCRIPT.parent))
import document_catalog


def run_catalog_check() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_catalog_is_current() -> bool:
    result = run_catalog_check()
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        return False
    return True


def test_catalog_contract() -> bool:
    try:
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"invalid catalog JSON: {exc}")
        return False

    if payload.get("schema_version") != "solo-empire.document-catalog.v1":
        print("unexpected catalog schema_version")
        return False
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        print("catalog documents must be a non-empty list")
        return False
    paths = [item.get("path") for item in documents if isinstance(item, dict)]
    if len(paths) != len(set(paths)):
        print("catalog contains duplicate document paths")
        return False
    forbidden = ("data/generated/", "data/raw/", "data/runtime/", "diary/entries/")
    if any(
        isinstance(path, str)
        and (
            (path.startswith(forbidden) and not path.endswith("/README.md"))
            or (path.startswith("clients/") and not path.endswith("/README.md"))
        )
        for path in paths
    ):
        print("catalog contains an excluded generated/private path")
        return False
    missing = [path for path in paths if isinstance(path, str) and not (REPO_ROOT / path).is_file()]
    if missing:
        print(f"catalog contains missing paths: {missing[:3]}")
        return False
    return True


def test_generated_catalog_self_link_count_is_stable() -> bool:
    catalog = {
        "summary": {
            "documents": 1,
            "with_frontmatter": 0,
            "without_frontmatter": 1,
            "by_root_area": {"docs": 1},
            "by_status": {"generated": 1},
        },
        "documents": [
            {
                "path": "docs/DOCUMENT-CATALOG.md",
                "title": "Unified Document Catalog",
                "document_type": "index",
                "status": "generated",
                "updated": "",
                "parent": "",
                "internal_link_count": 0,
            }
        ],
    }

    document_catalog.normalize_generated_catalog_entry(catalog)
    first_count = catalog["documents"][0]["internal_link_count"]
    document_catalog.normalize_generated_catalog_entry(catalog)
    expected_count = len(document_catalog.LINK_PATTERN.findall(document_catalog.render_markdown(catalog)))
    if first_count != expected_count or catalog["documents"][0]["internal_link_count"] != first_count:
        print("generated catalog self-link count is not stable")
        return False
    return True


def test_federated_catalog_prefixes_sources_and_keeps_private_paths_out() -> bool:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "kb"
        (source / "notes").mkdir(parents=True)
        (source / "diary" / "entries").mkdir(parents=True)
        (source / "notes" / "career.md").write_text("---\ntitle: Career\ntags: [career]\n---\n# Career\n", encoding="utf-8")
        (source / "diary" / "entries" / "2026-11-03.md").write_text("# private\n", encoding="utf-8")
        (source / "diary" / "README.md").write_text("# Diary boundary\n", encoding="utf-8")
        (source / "plans").mkdir()
        (source / "plans" / "old.md").write_text("# Old plan\n", encoding="utf-8")
        (source / "runbooks").mkdir()
        (source / "runbooks" / "restore.md").write_text("# Restore\n", encoding="utf-8")
        catalog = document_catalog.build_federated_catalog([("knowledge-base", source)])
        out = Path(tmp) / "federated.json"
        cli = subprocess.run(
            [sys.executable, str(SCRIPT), "--federated", str(out), "--source", f"knowledge-base={source}"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
        collision = subprocess.run(
            [sys.executable, str(SCRIPT), "--federated", str(out), "--source", f"docs={source}"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
    extra = {item["path"]: item for item in catalog["documents"] if item["path"].startswith("knowledge-base/")}
    own = [item for item in catalog["documents"] if not item["path"].startswith("knowledge-base/")]
    checks = {
        "source document is prefixed": extra.get("knowledge-base/notes/career.md", {}).get("title") == "Career",
        "source root area is its name": extra.get("knowledge-base/notes/career.md", {}).get("root_area") == "knowledge-base",
        "private diary entry is excluded": "knowledge-base/diary/entries/2026-11-03.md" not in extra,
        "boundary README stays": "knowledge-base/diary/README.md" in extra,
        "top-level source directory keeps its type": extra.get("knowledge-base/runbooks/restore.md", {}).get("document_type") == "runbook",
        "classified on the source path": extra.get("knowledge-base/plans/old.md", {}).get("status")
        == document_catalog.infer_status("plans/old.md", {}),
        "own documents keep their paths": len(own) == len(document_catalog.build_catalog()["documents"]),
        "summary counts everything": catalog["summary"]["documents"] == len(catalog["documents"]),
        "cli writes the file": cli.returncode == 0,
        "name colliding with a root area is refused": collision.returncode != 0,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("federated catalog checks failed: " + ", ".join(failed))
        print(cli.stdout, cli.stderr)
        return False
    return True


def test_federated_catalog_applies_full_privacy_boundary_to_source_paths() -> bool:
    import tempfile

    private = (
        "clients/acme/brief.md",
        "engagements/acme/notes.md",
        "decisions/records/2026-11-03.md",
        "finance/inbox/receipt.md",
        "notes/secrets/token.md",
        "notes/Private/plan.md",
        ".env.local/notes.md",
    )
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "kb"
        for relative in private + ("clients/README.md", "notes/public.md"):
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("---\ntitle: Secret title\ndescription: Secret description\n---\n", encoding="utf-8")
        catalog = document_catalog.build_federated_catalog([("knowledge-base", source)])
    paths = {item["path"] for item in catalog["documents"]}
    leaked = [relative for relative in private if f"knowledge-base/{relative}" in paths]
    checks = {
        "no private source path is catalogued": not leaked,
        "client boundary README stays": "knowledge-base/clients/README.md" in paths,
        "public source document stays": "knowledge-base/notes/public.md" in paths,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("federated privacy checks failed: " + ", ".join(failed) + f" (leaked: {leaked})")
        return False
    return True


def test_federated_catalog_namespaces_parent_and_related() -> bool:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "kb"
        (source / "notes" / "deep").mkdir(parents=True)
        (source / "clients").mkdir()
        (source / "README.md").write_text("# KB\n", encoding="utf-8")
        (source / "notes" / "README.md").write_text("# Notes\n", encoding="utf-8")
        (source / "notes" / "sibling.md").write_text("# Sibling\n", encoding="utf-8")
        (source / "clients" / "acme.md").write_text("# Acme\n", encoding="utf-8")
        (source / "notes" / "deep" / "guide.mdx").write_text("# Guide\n", encoding="utf-8")
        (source / "notes" / "deep" / "asset").write_text("not a document\n", encoding="utf-8")
        (source / "notes" / "deep" / "child.md").write_text(
            "---\n"
            "parent: README.md\n"
            "related: [../sibling.md, notes/README.md#top, sibling, guide, asset, ../../clients/acme.md, ../../../outside.md, https://example.com/x, docs/README.md]\n"
            "---\n# Child\n",
            encoding="utf-8",
        )
        (source / "notes" / "rooted.md").write_text("---\nparent: README.md\nrelated: [notes/sibling.md]\n---\n# Rooted\n", encoding="utf-8")
        catalog = document_catalog.build_federated_catalog([("knowledge-base", source)])
    by_path = {item["path"]: item for item in catalog["documents"]}
    child = by_path.get("knowledge-base/notes/deep/child.md", {})
    rooted = by_path.get("knowledge-base/notes/rooted.md", {})
    checks = {
        "source-root parent is namespaced": child.get("parent") == "knowledge-base/README.md",
        "document-relative parent wins over the source root": rooted.get("parent") == "knowledge-base/notes/README.md",
        "related resolve inside the source": child.get("related")
        == [
            "knowledge-base/notes/sibling.md",
            "knowledge-base/notes/README.md",
            "knowledge-base/notes/deep/guide.mdx",
            "https://example.com/x",
        ],
        "source-root related is namespaced": rooted.get("related") == ["knowledge-base/notes/sibling.md"],
        "own catalog references are untouched": by_path.get("docs/DOCUMENT-CATALOG.md", {}).get("parent") == "docs/README.md",
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("federated reference checks failed: " + ", ".join(failed))
        print(child.get("parent"), child.get("related"), rooted.get("parent"), rooted.get("related"))
        return False
    return True


def test_federated_references_skip_files_outside_the_catalog() -> bool:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "kb"
        (source / "notes" / "vendor").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        (source / ".gitignore").write_text("notes/README.md\n", encoding="utf-8")
        (source / "README.md").write_text("# KB\n", encoding="utf-8")
        (source / "notes" / "README.md").write_text("# Ignored\n", encoding="utf-8")
        (source / "notes" / "vendor" / ".git").mkdir()
        (source / "notes" / "vendor" / "guide.md").write_text("# Nested repo\n", encoding="utf-8")
        (source / "notes" / "child.md").write_text(
            "---\nparent: README.md\nrelated: [vendor/guide.md]\n---\n# Child\n", encoding="utf-8"
        )
        catalog = document_catalog.build_federated_catalog([("knowledge-base", source)])
    by_path = {item["path"]: item for item in catalog["documents"]}
    child = by_path.get("knowledge-base/notes/child.md", {})
    checks = {
        "ignored file is not catalogued": "knowledge-base/notes/README.md" not in by_path,
        "parent falls back to the catalogued source root": child.get("parent") == "knowledge-base/README.md",
        "nested-repository document is not a related target": child.get("related") == [],
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("federated eligible-reference checks failed: " + ", ".join(failed))
        print(child.get("parent"), child.get("related"))
        return False
    return True


def test_federated_catalog_skips_symlinked_documents() -> bool:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        outside = Path(tmp) / "outside" / "secret.md"
        outside.parent.mkdir(parents=True)
        outside.write_text("---\ntitle: Outside secret\n---\n", encoding="utf-8")
        source = Path(tmp) / "kb"
        (source / "clients" / "acme").mkdir(parents=True)
        (source / "notes").mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        (source / "clients" / "acme" / "brief.md").write_text("---\ntitle: Client secret\n---\n", encoding="utf-8")
        (source / "notes" / "public.md").write_text("# Public\n", encoding="utf-8")
        (source / "notes" / "outside.md").symlink_to(outside)
        (source / "notes" / "alias.md").symlink_to(Path("..") / "clients" / "acme" / "brief.md")
        subprocess.run(["git", "-C", str(source), "add", "-A"], check=True)
        catalog = document_catalog.build_federated_catalog([("knowledge-base", source)])
    by_path = {item["path"]: item for item in catalog["documents"]}
    titles = {item["title"] for item in catalog["documents"]}
    checks = {
        "symlink to a file outside the source is skipped": "knowledge-base/notes/outside.md" not in by_path,
        "symlink to a private file inside the source is skipped": "knowledge-base/notes/alias.md" not in by_path,
        "no symlink target title leaks": not titles & {"Outside secret", "Client secret"},
        "regular document stays": "knowledge-base/notes/public.md" in by_path,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("federated symlink checks failed: " + ", ".join(failed))
        return False
    return True


def test_federated_catalog_skips_documents_under_a_symlinked_directory() -> bool:
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "kb"
        (source / "clients" / "acme").mkdir(parents=True)
        (source / "notes").mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        (source / "clients" / "acme" / "brief.md").write_text("---\ntitle: Client secret\n---\n", encoding="utf-8")
        (source / "notes" / "brief.md").write_text("# Public brief\n", encoding="utf-8")
        (source / "notes" / "public.md").write_text("# Public\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(source), "add", "-A"], check=True)
        # The index still lists notes/brief.md, but notes/ is now a link into the private tree.
        shutil.rmtree(source / "notes")
        (source / "notes").symlink_to(Path("clients") / "acme")
        (source / "docs").mkdir()
        (source / "docs" / "public.md").write_text("# Docs\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(source), "add", "docs/public.md"], check=True)
        catalog = document_catalog.build_federated_catalog([("knowledge-base", source)])
    by_path = {item["path"]: item for item in catalog["documents"]}
    titles = {item["title"] for item in catalog["documents"]}
    checks = {
        "file reached through a symlinked directory is skipped": "knowledge-base/notes/brief.md" not in by_path,
        "no private title leaks through the directory link": "Client secret" not in titles,
        "regular document stays": "knowledge-base/docs/public.md" in by_path,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print("federated symlinked-directory checks failed: " + ", ".join(failed))
        return False
    return True


def main() -> int:
    tests = [
        test_catalog_is_current,
        test_catalog_contract,
        test_generated_catalog_self_link_count_is_stable,
        test_federated_catalog_prefixes_sources_and_keeps_private_paths_out,
        test_federated_catalog_applies_full_privacy_boundary_to_source_paths,
        test_federated_catalog_namespaces_parent_and_related,
        test_federated_references_skip_files_outside_the_catalog,
        test_federated_catalog_skips_symlinked_documents,
        test_federated_catalog_skips_documents_under_a_symlinked_directory,
    ]
    passed = failed = 0
    for test in tests:
        print(f"[{'RUN':>4}] {test.__name__}")
        try:
            ok = test()
        except Exception as exc:  # pragma: no cover - test harness output
            print(f"  error: {exc}")
            ok = False
        if ok:
            passed += 1
            print("  PASS")
        else:
            failed += 1
            print("  FAIL")
    print(f"Results: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
