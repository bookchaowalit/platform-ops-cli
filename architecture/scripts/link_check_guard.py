#!/usr/bin/env python3
"""link-check-guard.py — Verify internal links in docs point to existing files.

Scans markdown files for relative links and checks if the target exists.

Usage:
    python3 scripts/link_check_guard.py
    make link:check
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import KNOWLEDGE_DIRS, KNOWLEDGE_ROOT, PROJECT_ROOT, knowledge_checkout_missing  # noqa: E402

ROOT = str(PROJECT_ROOT)

# Top-level paths that used to live in the solo-empire hub and now belong to other
# repositories (ops-toolkit, agent-skills, mcp-aggregator, the registry hub, ...). A relative link
# into one of them cannot be verified from this checkout: it is counted as external
# and never fails the guard.
EXTERNAL_TOP_LEVEL = frozenset({
    "infra", "skills", "mcp", "dashboard", "repository-catalog", "ventures", "empire",
    "integrations", "opportunities", "catalog", "diary", "engagements", "clients",
    ".github", ".claude", ".codex", ".gemini", ".qoder", ".mcp.json",
    "AGENTS.md", "CLAUDE.md", "Makefile", "LICENSE", "accounts", ".gitleaks.toml",
    ".gitattributes", ".task-version",
})

# Pattern to find markdown links: [text](path)
LINK_PATTERN = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')

# Skip external links, anchors, and mailto
SKIP_PREFIXES = ('http://', 'https://', 'mailto:', '#', 'ftp://', 'file:///', 'file://')

# Directories to skip when scanning
SKIP_DIRS = {
    'node_modules', '__pycache__', '.git', '.venv', '.android-sdk',
    '.gemini', '.qwen', '.claude', '.codex', '.npm-cache', '.wrangler',
    '.vercel-tmp', '.next', '.terraform', 'dist', 'build', '.pytest_cache',
}

# Placeholder link text that isn't a real link
PLACEHOLDER_LINKS = {'url', 'URL', 'path'}
PORTABLE_CI = os.environ.get('SOLO_REQUIRE_SIBLINGS') != '1'


def extract_links(filepath):
    """Extract all internal links from a markdown file."""
    links = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return links

    for match in LINK_PATTERN.finditer(content):
        url = match.group(2).strip()
        # Skip external links
        if any(url.startswith(p) for p in SKIP_PREFIXES):
            continue
        # Skip pure anchors
        if url.startswith('#') and '(' not in url:
            continue
        # Remove anchor part for file existence check
        file_part = url.split('#')[0]
        if not file_part or file_part.startswith('/'):
            continue
        # Skip placeholder text
        if file_part in PLACEHOLDER_LINKS:
            continue
        links.append((match.start(), url, file_part))
    return links


def resolve_link(doc_path, file_part):
    """Resolve a relative link to an absolute path."""
    doc_dir = os.path.dirname(doc_path)
    resolved = os.path.normpath(os.path.join(doc_dir, file_part))
    return resolved


BOOK_DEV_PREFIX = ('projects', 'product', 'engineering', 'book-dev')
BOOK_DEV_CHECKOUT_ROOTS = frozenset({'github', 'gitlab', 'local'})


def is_ignored_runtime_path(path):
    """Return whether a missing target belongs to a local-only checkout.

    Covers gitignored ``engagements/`` and the nested Book Dev checkouts under
    ``projects/product/engineering/book-dev/{github,gitlab,local}/<repo>``
    (mirrors ``.gitignore``). Tracked boundary markers such as
    ``book-dev/README.md`` and the ``.gitkeep`` files are not ignored.
    """
    relative = os.path.relpath(path, ROOT)
    if relative == 'engagements' or relative.startswith('engagements' + os.sep):
        return True
    parts = tuple(relative.split(os.sep))
    return (
        len(parts) > len(BOOK_DEV_PREFIX) + 1
        and parts[:len(BOOK_DEV_PREFIX)] == BOOK_DEV_PREFIX
        and parts[len(BOOK_DEV_PREFIX)] in BOOK_DEV_CHECKOUT_ROOTS
        and parts[len(BOOK_DEV_PREFIX) + 1] != '.gitkeep'
    )


def target_exists(resolved):
    """Existence check; moved workspace docs resolve against the knowledge-base checkout."""
    if os.path.exists(resolved):
        return True
    relative = os.path.relpath(resolved, ROOT)
    parts = tuple(relative.split(os.sep))
    if parts[0] in KNOWLEDGE_DIRS and parts[:len(BOOK_DEV_PREFIX)] != BOOK_DEV_PREFIX:
        return os.path.exists(os.path.join(str(KNOWLEDGE_ROOT), relative))
    return False


def is_other_repo_path(resolved):
    """True when a link target lives in another repository (moved or sibling)."""
    relative = os.path.relpath(resolved, ROOT)
    if relative.startswith('..'):
        return True
    top = relative.split(os.sep)[0]
    if top in EXTERNAL_TOP_LEVEL:
        return True
    parts = tuple(relative.split(os.sep))
    if parts[:len(BOOK_DEV_PREFIX)] == BOOK_DEV_PREFIX:
        return True
    # Moved workspace docs: checked against knowledge-base when it is present.
    if top in KNOWLEDGE_DIRS and knowledge_checkout_missing() and os.environ.get('SOLO_REQUIRE_SIBLINGS') != '1':
        return True
    return False


def check_links():
    """Check all internal links in docs."""
    errors = []
    warnings = []
    checked = 0
    external = 0

    files_to_scan = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith('node_modules.')
        ]
        for fn in filenames:
            if fn.endswith('.md'):
                files_to_scan.append(os.path.join(dirpath, fn))

    for doc_path in files_to_scan:
        links = extract_links(doc_path)
        for _, url, file_part in links:
            resolved = resolve_link(doc_path, file_part)
            checked += 1
            if is_other_repo_path(resolved):
                external += 1
                continue
            if not target_exists(resolved):
                rel_doc = os.path.relpath(doc_path, ROOT)
                message = f"  DEFERRED: {rel_doc} -> {url} (ignored local-only checkout)"
                if PORTABLE_CI and is_ignored_runtime_path(resolved):
                    warnings.append(message)
                else:
                    errors.append(f"  BROKEN: {rel_doc} -> {url}")

    return errors, warnings, checked, external, len(files_to_scan)


def main():
    print("=" * 60)
    print("  Solo Empire — Link Check Guard")
    print("=" * 60)
    print()

    errors, warnings, checked, external, documents = check_links()

    if errors:
        print(f"BROKEN LINKS ({len(errors)}):")
        for e in errors:
            print(e)
        print()

    if warnings:
        for w in warnings:
            print(w)
        print()

    print(f"  Checked {checked} internal links ({external} point into other repositories and are not verified here)")

    if not errors and not warnings:
        print("  All internal links are valid.")
        print()

    empty = documents == 0
    if empty:
        print(f"  FAIL: no Markdown documents under {ROOT}: wrong or empty document root")
    status = "FAIL" if errors or empty else "OK"
    print(f"  Result: {status} ({len(errors)} broken, {len(warnings)} warnings)")
    print("=" * 60)

    return 1 if errors or empty else 0


if __name__ == "__main__":
    sys.exit(main())
