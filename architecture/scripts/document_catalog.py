#!/usr/bin/env python3
"""Build and verify the repository-wide Markdown document catalog.

The catalog is a generated navigation projection. Markdown files remain the
source of truth; this script only extracts safe metadata and relative paths.
It deliberately excludes private/generated data, dependency trees, and nested
repository boundaries.

Usage:
    python3 scripts/document_catalog.py --write
    python3 scripts/document_catalog.py --check
    python3 scripts/document_catalog.py --federated OUT.json --source knowledge-base=../knowledge-base

``--federated`` adds other document repositories to this one for the hosted
knowledge API. Their paths are prefixed with the source name (which also
becomes their root area) and the same exclusions apply inside each source.
The result is written only to OUT; the committed projections stay single-root.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError:  # pragma: no cover - the repository normally provides PyYAML
    yaml = None


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import PROJECT_ROOT as ROOT  # noqa: E402  (the document tree, SPEC-001)
MARKDOWN_OUTPUT = ROOT / "docs" / "DOCUMENT-CATALOG.md"
JSON_OUTPUT = ROOT / "data" / "indexes" / "document-catalog.json"
SCHEMA_VERSION = "solo-empire.document-catalog.v1"

# These are not durable documentation surfaces. Boundary README files remain
# eligible so the catalog still points to the contract for a private area.
EXCLUDED_PREFIXES = (
    "data/generated/",
    "data/raw/",
    "data/lake/",
    "data/lake-local/",
    "data/backups/",
    "data/runtime/",
    "projects/product/engineering/book-dev/",
    "engagements/",
    "decisions/records/",
)
PRIVATE_PREFIXES = (
    "clients/",
    "diary/entries/",
    "finance/accounts/",
    "finance/assets/",
    "finance/budgets/",
    "finance/cash-flow/",
    "finance/expense/",
    "finance/inbox/",
    "finance/income/",
    "finance/insurance/",
    "finance/invoices/",
    "finance/investments/",
    "finance/liabilities/",
    "finance/reconciliation/",
    "finance/tax/",
)
# Any path segment with one of these names is never catalogued, at any depth.
PRIVATE_PART_NAMES = {".git", ".ssh", ".aws", "credentials", "secrets", "private", ".env"}
SKIP_DIR_NAMES = {
    ".git",
    ".claude",
    ".codex",
    ".github",
    ".impeccable",
    ".next",
    ".qoder",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
FRONTMATTER_FIELDS = {"title", "aliases", "tags", "created", "updated", "description", "parent", "related", "status", "canonical"}
LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
HEADING_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def rel(path: Path, root: Path = ROOT) -> str:
    return path.relative_to(root).as_posix()


def is_boundary_readme(path_str: str) -> bool:
    folded = path_str.casefold()
    return folded.endswith("/readme.md") or folded == "readme.md"


def is_excluded_path(path_str: str) -> bool:
    """Apply the privacy boundary to a path relative to its own source root.

    Mirrors ``safe_path()`` in platform-knowledge-api, which only sees the
    namespaced path, so a federated source must be filtered here first.
    """

    path_str = path_str.casefold()
    parts = path_str.split("/")
    if any(part in PRIVATE_PART_NAMES or part.startswith(".env.") for part in parts):
        return True
    if any(path_str.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return True
    if any(path_str.startswith(prefix) for prefix in PRIVATE_PREFIXES):
        return not is_boundary_readme(path_str)
    return False


def is_nested_repository_path(path: Path, root: Path = ROOT) -> bool:
    """Return True when a candidate belongs to an independent Git boundary."""

    current = path.parent
    while current != root and root in current.parents:
        if (current / ".git").exists():
            return True
        current = current.parent
    return False


def iter_candidate_paths(root: Path = ROOT) -> Iterable[Path]:
    """Yield first-party Markdown files from Git's tracked/unignored view."""

    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "*.md",
                "*.mdx",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        candidates = [root / line for line in result.stdout.splitlines() if line.strip()]
    except (FileNotFoundError, subprocess.CalledProcessError):
        candidates = []
        for directory, dirnames, filenames in os.walk(root):
            directory_path = Path(directory)
            if directory_path != root and (directory_path / ".git").exists():
                dirnames[:] = []
                continue
            dirnames[:] = sorted(
                name for name in dirnames if name not in SKIP_DIR_NAMES and not name.startswith(".")
            )
            candidates.extend(
                directory_path / name
                for name in filenames
                if Path(name).suffix.lower() in {".md", ".mdx"}
            )

    seen: set[str] = set()
    real_root = root.resolve()
    for path in sorted(candidates, key=lambda item: rel(item, root)):
        # A symlink anywhere on the path (the file or a directory above it) would bring in a file
        # outside the source, or a private file under a public name: the resolved path must be
        # the lexical one.
        if not path.is_file() or path.resolve() != real_root / path.relative_to(root):
            continue
        path_str = rel(path, root)
        if path_str in seen or is_excluded_path(path_str) or is_nested_repository_path(path, root):
            continue
        seen.add(path_str)
        yield path


def _fallback_value(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    if value.startswith("[") and value.endswith("]"):
        return [part.strip().strip("\"'") for part in value[1:-1].split(",") if part.strip()]
    return value.strip("\"'")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], bool]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, False
    closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if closing is None:
        return {}, False
    raw = "\n".join(lines[1:closing])
    if yaml is not None:
        try:
            parsed = yaml.safe_load(raw)
        except yaml.YAMLError:
            parsed = None
        if isinstance(parsed, dict):
            return {key: value for key, value in parsed.items() if key in FRONTMATTER_FIELDS}, True
    parsed_fallback: dict[str, Any] = {}
    for line in raw.splitlines():
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", line)
        if match and match.group(1) in FRONTMATTER_FIELDS:
            parsed_fallback[match.group(1)] = _fallback_value(match.group(2))
    return parsed_fallback, True


def as_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(as_string(item) for item in value if as_string(item))
    return str(value).strip()


def as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple)) else [value]
    return [as_string(item) for item in values if as_string(item)]


def infer_status(path_str: str, metadata: dict[str, Any]) -> str:
    explicit = as_string(metadata.get("status")).lower()
    if explicit:
        return explicit
    parts = set(path_str.lower().split("/"))
    if "generated" in parts or path_str.startswith("data/indexes/"):
        return "generated"
    if "template" in parts or path_str.startswith("templates/"):
        return "template"
    if "evidence" in parts:
        return "evidence"
    if "archive" in parts or path_str.startswith(("plans/", "sessions/")):
        return "historical"
    if path_str.startswith("dashboard/"):
        return "current"
    return "unclassified"


def infer_document_type(path_str: str, metadata: dict[str, Any]) -> str:
    # Leading slash so the directory checks also match a top-level directory of a federated source.
    path_lower = "/" + path_str.lower()
    tags = {tag.lower() for tag in as_string_list(metadata.get("tags"))}
    name = Path(path_str).name.lower()
    if name in {"readme.md", "index.md"} or tags & {"index", "moc", "manifest"}:
        return "index"
    if "/architecture/" in path_lower or "architecture" in tags:
        return "architecture"
    if "/governance/" in path_lower or "policy" in tags:
        return "policy"
    if "/lifecycle/" in path_lower or "lifecycle" in tags:
        return "lifecycle"
    if "/references/" in path_lower or "reference" in tags:
        return "reference"
    if "/runbook" in path_lower or "runbook" in tags:
        return "runbook"
    if "/evidence/" in path_lower or infer_status(path_str, metadata) == "evidence":
        return "evidence"
    if "template" in path_lower or "template" in tags:
        return "template"
    if path_lower.startswith("/dashboard/") or "dashboard" in tags:
        return "dashboard"
    return "document"


def title_for(path: Path, text: str, metadata: dict[str, Any]) -> str:
    title = as_string(metadata.get("title"))
    if title:
        return title
    heading = HEADING_PATTERN.search(text)
    if heading:
        return heading.group(1).strip()
    return path.stem.replace("-", " ").replace("_", " ").strip().title()


def clean_text(value: Any, limit: int = 180) -> str:
    text = " ".join(as_string(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def namespace_reference(source_path: str, reference: str, documents: frozenset[str], prefix: str) -> str:
    """Resolve a parent/related reference inside its source, then namespace it.

    Document-relative references win over source-root ones, as in the
    knowledge API. Only the source's catalogued ``documents`` count, so an
    ignored, private or nested-repository file never wins over a real target.
    A reference that names none of them, or escapes the source, is dropped so
    it can never point at a document of another source.
    """

    value = reference.strip().strip("<>")
    if not prefix or value.startswith(("http://", "https://", "mailto:")):
        return value
    value = value.split("#", 1)[0].split("?", 1)[0].strip().replace("\\", "/")
    if not value or value.startswith("/"):
        return ""
    names = [value] if value.endswith((".md", ".mdx")) else [f"{value}.md", f"{value}.mdx"]
    base = posixpath.dirname(source_path)
    for candidate in [posixpath.join(base, name) for name in names] + names:
        candidate = posixpath.normpath(candidate)
        if candidate in documents:
            return prefix + candidate
    return ""


def extract_document(
    path: Path, root: Path = ROOT, prefix: str = "", documents: frozenset[str] = frozenset()
) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    metadata, has_frontmatter = parse_frontmatter(text)
    # Classify on the path inside its own source; the prefix only names that source.
    source_path = rel(path, root)
    path_str = prefix + source_path
    references = (namespace_reference(source_path, item, documents, prefix) for item in as_string_list(metadata.get("related")))
    related = [ref for ref in references if ref]
    links = [url for url in LINK_PATTERN.findall(text) if not url.startswith(("http://", "https://", "mailto:"))]
    root_area = path_str.split("/", 1)[0] if "/" in path_str else "(root)"
    return {
        "path": path_str,
        "title": title_for(path, text, metadata),
        "description": clean_text(metadata.get("description")),
        "root_area": root_area,
        "document_type": infer_document_type(source_path, metadata),
        "status": infer_status(source_path, metadata),
        "created": as_string(metadata.get("created")),
        "updated": as_string(metadata.get("updated")),
        "parent": namespace_reference(source_path, as_string(metadata.get("parent")), documents, prefix),
        "canonical": as_string(metadata.get("canonical")),
        "related": related,
        "aliases": as_string_list(metadata.get("aliases")),
        "tags": as_string_list(metadata.get("tags")),
        "has_frontmatter": has_frontmatter,
        "internal_link_count": len(links),
    }


def build_catalog() -> dict[str, Any]:
    documents = [extract_document(path) for path in iter_candidate_paths()]
    documents.sort(key=lambda item: item["path"])
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "scripts/document_catalog.py",
        "scope": {
            "included": "First-party .md and .mdx files visible to Git and not inside nested repositories.",
            "excluded": [
                "data/generated/",
                "data/raw/",
                "data/lake/",
                "data/lake-local/",
                "data/backups/",
                "data/runtime/",
                "private diary/finance/client content except boundary README files",
                "nested Git repositories",
            ],
        },
        "summary": {
            "documents": len(documents),
            "by_root_area": dict(sorted(Counter(item["root_area"] for item in documents).items())),
            "by_document_type": dict(sorted(Counter(item["document_type"] for item in documents).items())),
            "by_status": dict(sorted(Counter(item["status"] for item in documents).items())),
            "with_frontmatter": sum(1 for item in documents if item["has_frontmatter"]),
            "without_frontmatter": sum(1 for item in documents if not item["has_frontmatter"]),
        },
        "documents": documents,
    }


SOURCE_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_source(value: str) -> tuple[str, Path]:
    name, sep, location = value.partition("=")
    if not sep or not SOURCE_NAME.match(name) or not location:
        raise argparse.ArgumentTypeError(f"expected NAME=PATH with a kebab-case name, got {value!r}")
    root = Path(location).expanduser().resolve()
    if not root.is_dir():
        raise argparse.ArgumentTypeError(f"source {name!r} is not a directory: {root}")
    return name, root


def build_federated_catalog(sources: list[tuple[str, Path]]) -> dict[str, Any]:
    """This repository's catalog plus each extra source under a ``<name>/`` path prefix."""

    catalog = build_catalog()
    normalize_generated_catalog_entry(catalog)
    taken = {item["root_area"] for item in catalog["documents"]}
    names = [name for name, _ in sources]
    for name, root in sources:
        if name in taken or names.count(name) > 1:
            raise ValueError(f"source name {name!r} collides with an existing root area or another source")
        paths = list(iter_candidate_paths(root))
        eligible = frozenset(rel(path, root) for path in paths)
        catalog["documents"].extend(extract_document(path, root, f"{name}/", eligible) for path in paths)
    documents = sorted(catalog["documents"], key=lambda item: item["path"])
    catalog["documents"] = documents
    catalog["scope"]["included"] += " Federated sources: " + ", ".join(names) + "."
    catalog["summary"] = {
        "documents": len(documents),
        "by_root_area": dict(sorted(Counter(item["root_area"] for item in documents).items())),
        "by_document_type": dict(sorted(Counter(item["document_type"] for item in documents).items())),
        "by_status": dict(sorted(Counter(item["status"] for item in documents).items())),
        "with_frontmatter": sum(1 for item in documents if item["has_frontmatter"]),
        "without_frontmatter": sum(1 for item in documents if not item["has_frontmatter"]),
    }
    return catalog


def markdown_escape(value: Any) -> str:
    return clean_text(value).replace("|", "\\|") or "—"


def document_link(path_str: str) -> str:
    target = os.path.relpath(ROOT / path_str, MARKDOWN_OUTPUT.parent).replace(os.sep, "/")
    if not target.startswith("."):
        target = f"./{target}"
    return f"[{path_str}]({target})"


def render_markdown(catalog: dict[str, Any]) -> str:
    summary = catalog["summary"]
    lines = [
        "---",
        "title: Unified Document Catalog",
        "aliases: [Document Index, Repository Document Map]",
        "tags: [documentation, index, catalog, generated]",
        "created: 2026-08-20",
        "updated: 2026-08-20",
        "description: Generated repository-wide catalog of first-party Markdown documents and their navigation metadata.",
        "status: generated",
        "generated: true",
        "parent: docs/README.md",
        "related: [docs/references/AI-REFERENCE-MANIFEST.md, docs/WORKSPACE-STRUCTURE.md, docs/WORKSPACE-LOCATION-MAP.md]",
        "---",
        "",
        "# Unified Document Catalog",
        "",
        "> Generated navigation projection. The documents listed here remain the source of truth; this file must not be edited manually.",
        ">",
        "> Regenerate with `task docs:catalog` and verify drift with `task docs:catalog:check`.",
        "",
        "## How to use",
        "",
        "- Use this page for a human-readable path/title/type/status lookup.",
        "- Use [`data/indexes/document-catalog.json`](../data/indexes/document-catalog.json) for machine filtering by `status`, `root_area`, `document_type`, tags, parent, and related files.",
        "- Use [`docs/README.md`](README.md) and [`docs/references/AI-REFERENCE-MANIFEST.md`](references/AI-REFERENCE-MANIFEST.md) for source-of-truth and reading-order rules.",
        "- Generated, private, raw, backup, and nested-repository content is intentionally excluded from this catalog.",
        "",
        "## Summary",
        "",
        f"- Documents indexed: **{summary['documents']}**",
        f"- With frontmatter: **{summary['with_frontmatter']}**",
        f"- Without frontmatter: **{summary['without_frontmatter']}**",
        "",
        "### By root area",
        "",
        "| Root area | Documents |",
        "|---|---:|",
    ]
    for area, count in summary["by_root_area"].items():
        lines.append(f"| `{area}` | {count} |")
    lines.extend([
        "",
        "### By status",
        "",
        "| Status | Documents |",
        "|---|---:|",
    ])
    for status, count in summary["by_status"].items():
        lines.append(f"| `{status}` | {count} |")
    lines.extend([
        "",
        "## Document lookup",
        "",
        "| Path | Title | Type | Status | Updated | Parent |",
        "|---|---|---|---|---|---|",
    ])
    for item in catalog["documents"]:
        lines.append(
            "| "
            + " | ".join(
                (
                    document_link(item["path"]),
                    markdown_escape(item["title"]),
                    f"`{markdown_escape(item['document_type'])}`",
                    f"`{markdown_escape(item['status'])}`",
                    markdown_escape(item["updated"]),
                    markdown_escape(item["parent"]),
                )
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def normalize_generated_catalog_entry(catalog: dict[str, Any]) -> None:
    """Avoid recording stale self-link metadata from the prior catalog file."""

    catalog_path = rel(MARKDOWN_OUTPUT)
    entry = next((item for item in catalog["documents"] if item["path"] == catalog_path), None)
    if entry is not None:
        entry["internal_link_count"] = len(LINK_PATTERN.findall(render_markdown(catalog)))


def write_outputs(catalog: dict[str, Any]) -> None:
    MARKDOWN_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    MARKDOWN_OUTPUT.write_text(render_markdown(catalog), encoding="utf-8")
    JSON_OUTPUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_outputs(catalog: dict[str, Any]) -> int:
    expected_markdown = render_markdown(catalog)
    expected_json = json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    errors: list[str] = []
    if not MARKDOWN_OUTPUT.is_file():
        errors.append(f"missing {rel(MARKDOWN_OUTPUT)}")
    elif MARKDOWN_OUTPUT.read_text(encoding="utf-8") != expected_markdown:
        errors.append(f"drift detected in {rel(MARKDOWN_OUTPUT)}")
    if not JSON_OUTPUT.is_file():
        errors.append(f"missing {rel(JSON_OUTPUT)}")
    elif JSON_OUTPUT.read_text(encoding="utf-8") != expected_json:
        errors.append(f"drift detected in {rel(JSON_OUTPUT)}")
    if errors:
        for error in errors:
            print(f"  FAIL: {error}")
        print("Run: task docs:catalog")
        return 1
    print(f"  OK: {summary_line(catalog)}")
    return 0


def summary_line(catalog: dict[str, Any]) -> str:
    return f"{catalog['summary']['documents']} documents indexed; generated outputs are current"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="write both catalog projections")
    group.add_argument("--check", action="store_true", help="fail when either projection is missing or stale")
    group.add_argument("--federated", type=Path, metavar="OUT", help="write a JSON catalog that also covers --source repositories")
    parser.add_argument("--source", type=parse_source, action="append", default=[], metavar="NAME=PATH", help="extra document repository for --federated")
    args = parser.parse_args()

    if args.federated:
        if not args.source:
            parser.error("--federated needs at least one --source")
        try:
            catalog = build_federated_catalog(args.source)
        except ValueError as exc:
            parser.error(str(exc))
        args.federated.parent.mkdir(parents=True, exist_ok=True)
        args.federated.write_text(json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"  OK: wrote {args.federated} ({catalog['summary']['documents']} documents, {len(args.source)} extra sources)")
        return 0
    if args.source:
        parser.error("--source only applies to --federated")

    catalog = build_catalog()
    normalize_generated_catalog_entry(catalog)
    if args.write:
        write_outputs(catalog)
        print(f"  OK: wrote {rel(MARKDOWN_OUTPUT)}")
        print(f"  OK: wrote {rel(JSON_OUTPUT)}")
        print(f"  OK: {summary_line(catalog)}")
        return 0
    return check_outputs(catalog)


if __name__ == "__main__":
    raise SystemExit(main())
