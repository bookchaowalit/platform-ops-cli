"""Path helpers for the architecture-docs document tooling.

PROJECT_ROOT is the root of this repository (architecture-docs). Sibling
repositories are resolved without any dependency on the solo-empire registry
repository: an explicit ``SOLO_<NAME>_ROOT`` environment variable wins, otherwise
the sibling is looked up next to this checkout (``../<repo>``). A missing sibling
makes the guards that need it skip with a clear WARN (set
``SOLO_REQUIRE_SIBLINGS=1`` to turn the skip into a failure).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The tools (scripts/, tests/, design-floor/, messaging/) live in platform-ops-cli/architecture;
# the documents they check live in the hosted store space architecture-docs (SPEC-001, knowledge
# store plans/architecture-docs-store/). PROJECT_ROOT is the document tree, not this folder.
TOOLS_ROOT = Path(__file__).resolve().parent.parent


def main_checkout(repo_root: Path) -> Path:
    """The main clone of ``repo_root``: itself, or for a linked worktree (``.git`` is a file
    ``gitdir: <main>/.git/worktrees/<name>``) the clone it belongs to."""
    marker = repo_root / ".git"
    if marker.is_file():
        text = marker.read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("gitdir:"):
            gitdir = Path(text[len("gitdir:"):].strip())
            if not gitdir.is_absolute():  # git worktree add --relative-paths
                gitdir = (marker.parent / gitdir).resolve()
            if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
                return gitdir.parent.parent.parent
    return repo_root


def architecture_root(environ=os.environ, tools_root: Path = TOOLS_ROOT) -> Path:
    """The architecture document tree.

    Order: SOLO_ARCHITECTURE_ROOT (a store snapshot or a checkout) -> the tools folder itself when
    it still sits inside the document tree (legacy layout, docs/ next to scripts/) -> the
    architecture-docs sibling of the platform-ops-cli clone (``../architecture-docs``) -> the
    empire clone folder, where ops-cli is under public/ and the documents under private/ -> the local
    store snapshot (SOLO_ARCHITECTURE_SNAPSHOT, default ~/.cache/solo-empire/architecture-docs, known
    by its .snapshot.json stamp; SPEC-001 phase 2). A linked worktree resolves from its main clone.
    """
    override = (environ.get("SOLO_ARCHITECTURE_ROOT") or "").strip()
    if override:
        # Absolute but not resolved: path guards (delivery_context.ensure_path_boundary) must
        # still see any "..", symlink or denied component of the override.
        chosen = Path.cwd() / Path(override).expanduser()
        if (chosen / ".snapshot.json").is_file():
            _warn_if_stale(chosen)
        return chosen
    if (tools_root / "docs").is_dir():
        return tools_root
    repo = main_checkout(tools_root.parent)
    sibling = repo.parent / "architecture-docs"
    if sibling.is_dir():
        return sibling
    private = repo.parent.parent / "private" / "architecture-docs"
    if private.is_dir():
        return private
    # Store snapshot (SPEC-001 phase 2), resolved: refreshes swap the folder symlink atomically.
    snapshot_override = (environ.get("SOLO_ARCHITECTURE_SNAPSHOT") or "").strip()
    snapshot = Path(snapshot_override).expanduser() if snapshot_override else Path.home() / ".cache" / "solo-empire" / "architecture-docs"
    if (snapshot / ".snapshot.json").is_file():
        _warn_if_stale(snapshot)
        return snapshot.resolve()
    return private


def _warn_if_stale(snapshot: Path, max_age_hours: float = 24) -> None:
    """Keep working on an old snapshot, but say so on stderr (SPEC-001: stale after 24 h)."""
    import json
    import time
    try:
        pulled = float(json.loads((snapshot / ".snapshot.json").read_text(encoding="utf-8"))["pulled_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return
    age = (time.time() - pulled) / 3600
    if age > max_age_hours:
        print(f"WARN: architecture snapshot {snapshot} is {age:.0f} h old; refresh it with the hub's "
              "knowledge_snapshot.py --space architecture-docs --refresh", file=sys.stderr)


# A relative SOLO_*_ROOT override means "from where the command was called". Child processes that
# run in another folder (cwd=PROJECT_ROOT) must see the same folders, so pass them on resolved.
for _name, _value in list(os.environ.items()):
    if _name.startswith("SOLO_") and _name.endswith("_ROOT") and _value.strip() and not os.path.isabs(os.path.expanduser(_value.strip())):
        # Absolute but not resolved: path guards (delivery_context.ensure_path_boundary) must still
        # see any "..", symlink or denied component the caller wrote.
        os.environ[_name] = str(Path.cwd() / Path(_value.strip()).expanduser())

PROJECT_ROOT = architecture_root()

# A missing or empty document root is refused by the guards that could otherwise pass on nothing
# (link_check_guard, document_catalog); other scripts fail on the first file they cannot read, and
# commands that take their own paths (--catalog, --root, --help) still work without a default root.

# A store snapshot (SPEC-001) is read-only: a refresh replaces it, and Git stays the source until
# the phase 3 cutover. Its parent is a cache folder, so sibling checkouts are looked up where the
# architecture-docs clone would be (the owner's private/ folder of this clone's layout).
IS_SNAPSHOT = (PROJECT_ROOT / ".snapshot.json").is_file()
_DOCS_PARENT = (main_checkout(TOOLS_ROOT.parent).parent.parent / "private") if IS_SNAPSHOT else PROJECT_ROOT.parent


def refuse_snapshot_write(tool: str, root: Path | None = None) -> None:
    """Stop a generator before it writes into a store snapshot (the next refresh would drop it).
    ``root`` is the tree the generator is about to write (default: the document root)."""
    target = Path(root) if root is not None else PROJECT_ROOT
    if (target / ".snapshot.json").is_file():
        raise SystemExit(
            f"{tool}: {target} is a read-only store snapshot; write in an architecture-docs "
            "checkout (SOLO_ARCHITECTURE_ROOT) - Git is the source until the SPEC-001 phase 3 cutover"
        )


# Workspace documentation (projects/, roles/, learning/, quiz/, goals/ ...) lives in
# bookchaowalit/knowledge-base; override with SOLO_KNOWLEDGE_ROOT.
KNOWLEDGE_ROOT = Path(os.environ.get(
    "SOLO_KNOWLEDGE_ROOT", str(_DOCS_PARENT / "knowledge-base")
)).expanduser()
KNOWLEDGE_BASE = KNOWLEDGE_ROOT / "knowledge"

# Top-level folders that live in the knowledge-base repository.
KNOWLEDGE_DIRS = frozenset({
    "projects", "knowledge", "roles", "learning", "quiz", "routines", "goals",
    "habits", "decisions", "tasks", "sessions", "documentation-assets",
})

# The sibling parent folder (default: the folder that holds the document checkout).
SIBLINGS_ROOT = _DOCS_PARENT


# Repositories renamed without the solo- prefix whose SOLO_<NAME>_ROOT override
# name must not change: new repo name -> the short name used before the rename.
LEGACY_ENV_SHORT = {
    "business-catalog": "CATALOG", "agent-skills": "SKILLS",
    "architecture-docs": "ARCHITECTURE", "mcp-aggregator": "MCP", "ops-toolkit": "OPS",
    "time-tracking": "TIME", "projects-cell": "PROJECTS", "operating-cycles": "OPERATING",
    "learning-cell": "LEARNING", "health-metrics": "HEALTH", "content-cell": "CONTENT",
    "automation-cell": "AUTOMATION", "knowledge-base": "KNOWLEDGE", "owner-web": "WEB",
    "platform-api-archive": "PLATFORM_API", "data-lake-core": "EMPIRE_DATA_LAKE",
    "media-assets": "MEDIA", "insights-dashboard": "EMPIRE_INSIGHTS",
    "backstage-portal": "EMPIRE_BACKSTAGE",
    # Domain-first folder names (2026-10-13) keep the override names used before.
    "ai-grounded-assistant": "BOOK_GROUNDED_AI_ASSISTANT",
    "ai-rag-demo-worker": "RAG_DEMO_CF",
    "content-audio-mastering-cli": "BOOK_AUDIO_PRODUCTION",
    "content-graphics-cli": "BOOK_GRAPHICS_PRODUCTION",
    "content-media-worker": "MEDIA_WORKER",
    "content-production": "BOOK_PRODUCTION_PLATFORM",
    "data-catalog-api": "BOOK_DATA_API", "data-lake-pipelines": "DATA_LAKE",
    "data-migration-workbench": "BOOK_MIGRATION_VALIDATION_WORKBENCH",
    "data-mlops-pipeline": "MLOPS", "devtools-awesome-atlas": "AWESOME_ATLAS",
    "devtools-booklang-compiler": "BOOK_LANGUAGE_TOOL",
    "finance-accounts-api": "FINANCE_ACCOUNTS", "finance-budgets-api": "BUDGETS",
    "finance-business-assets-api": "BUSINESS_ASSETS",
    "finance-cash-flow-api": "CASH_FLOW", "finance-investments-api": "INVESTMENTS",
    "finance-ledger-core": "BOOK_FINANCE_CORE",
    "finance-liabilities-api": "LIABILITIES", "finance-portfolio-web": "BOOK_PORTFOLIO",
    "finance-project-profit": "BOOK_PROJECT_PROFIT",
    "finance-reconciliations-api": "RECONCILIATIONS",
    "finance-subscription-cli": "SUBSCRIPTION_INTELLIGENCE",
    "finance-subscriptions-api": "SUBSCRIPTIONS", "finance-tax-api": "TAX",
    "finance-transactions-api": "TRANSACTIONS", "life-content-api": "CONTENT",
    "life-dashboard-web": "WEB", "life-decisions-api": "DECISIONS",
    "life-diary-api": "DIARY", "life-goals-api": "GOALS", "life-habits-api": "HABITS",
    "life-health-api": "HEALTH", "life-learning-api": "LEARNING",
    "life-operating-cycles-api": "OPERATING", "life-opportunities-api": "OPPORTUNITIES",
    "life-projects-api": "PROJECTS", "life-roles-skills-api": "ROLES_SKILLS",
    "life-time-api": "TIME", "market-defi-yield-data": "BOOK_DEFI_DATA",
    "market-insights-web": "EMPIRE_INSIGHTS",
    "market-insights-worker": "INSIGHTS_WORKER",
    "marketing-seo-ranking-data": "BOOK_SEO_DATA",
    "opportunity-benefit-data": "BOOK_BENEFIT_DATA",
    "opportunity-bounty-data": "BOOK_BOUNTY_DATA",
    "opportunity-competition-data": "BOOK_COMPETITION_DATA",
    "opportunity-creator-program-data": "BOOK_CREATOR_PROGRAM_DATA",
    "opportunity-deal-data": "BOOK_DEAL_DATA",
    "opportunity-freebie-data": "BOOK_FREEBIE_DATA",
    "opportunity-grant-data": "BOOK_GRANT_DATA",
    "opportunity-intelligence-data": "BOOK_OPPORTUNITY_INTELLIGENCE",
    "opportunity-learning-program-data": "BOOK_LEARNING_PROGRAM_DATA",
    "opportunity-mobility-program-data": "BOOK_MOBILITY_PROGRAM_DATA",
    "opportunity-paid-study-data": "BOOK_PAID_STUDY_DATA",
    "opportunity-partnership-data": "BOOK_PARTNERSHIP_DATA",
    "opportunity-procurement-data": "BOOK_PROCUREMENT_DATA",
    "opportunity-scholarship-data": "BOOK_SCHOLARSHIP_DATA",
    "opportunity-startup-program-data": "BOOK_STARTUP_PROGRAM_DATA",
    "platform-accounts-api": "ACCOUNTS", "platform-agent-skills": "SKILLS",
    "platform-ai-gateway-api": "AI_GATEWAY", "platform-ai-usage-api": "AI_USAGE",
    "platform-auth-sdk": "SERVICE_AUTH", "platform-automation-api": "AUTOMATION",
    "platform-cloud-api": "CLOUD_API", "platform-developer-portal": "EMPIRE_BACKSTAGE",
    "platform-empire": "EMPIRE_PLATFORM", "platform-event-hub-api": "EVENT_HUB",
    "platform-homelab-infra": "HOMELAB", "platform-infra-kit": "PLATFORM_KIT",
    "platform-intake-api": "INTAKE", "platform-integrations-api": "INTEGRATIONS",
    "platform-knowledge-api": "KNOWLEDGE_API",
    "platform-legacy-api-archive": "PLATFORM_API", "platform-mcp-aggregator": "MCP",
    "platform-mcp-remote-worker": "MCP_REMOTE",
    "platform-notifications-api": "NOTIFICATIONS", "platform-ops-cli": "OPS",
    "platform-saas-catalog-api": "SAAS_CATALOG",
    "platform-uptime-worker": "CLOUD_MONITOR",
    "platform-webhooks-worker": "HOOKS_WORKER",
    "realestate-lead-extension": "BROWSER_EXTENSION", "sales-crm": "CRM",
    "tech-ai-tools-data": "BOOK_AI_TOOLS_DATA",
    "travel-flight-price-data": "BOOK_FLIGHT_DATA", "trust-provenchain": "PROVENCHAIN",
    "workplace-nodal": "ORGANIZATION_ENVIRONMENT",
}


def sibling_repo_root(repo: str) -> Path:
    """Checkout root of `bookchaowalit/<repo>` (override: SOLO_<NAME>_ROOT)."""
    name = repo.split("/")[-1]
    short = LEGACY_ENV_SHORT.get(name) or (name[len("solo-"):] if name.startswith("solo-") else name)
    override = os.environ.get(f"SOLO_{short.upper().replace('-', '_')}_ROOT")
    return Path(override).expanduser() if override else SIBLINGS_ROOT / name


def sibling_ref_file(ref: str) -> Path:
    """Resolve a `bookchaowalit/<repo>:<path>` reference to a path in the sibling checkout.

    The returned path may not exist (sibling absent); check `.exists()` before reading.
    """
    repo, sep, rel_path = ref.partition(":")
    if not sep or not rel_path:
        raise ValueError(f"expected 'bookchaowalit/<repo>:<path>', got {ref!r}")
    return sibling_repo_root(repo) / rel_path


# The production tools catalog is owned by bookchaowalit/content-production.
PRODUCTION_TOOLS_REF = "bookchaowalit/content-production:catalog/production-tools.yaml"


def production_tools_catalog() -> Path:
    """Catalog path in the book-production-platform checkout (override: SOLO_BOOK_PRODUCTION_PLATFORM_ROOT)."""
    return sibling_ref_file(PRODUCTION_TOOLS_REF)


def repo_file(relative) -> Path:
    """Resolve a repository-relative path, redirecting knowledge-base folders to that checkout."""
    rel = Path(relative)
    if rel.parts and rel.parts[0] in KNOWLEDGE_DIRS:
        return KNOWLEDGE_ROOT / rel
    return PROJECT_ROOT / rel


def repo_rel(path) -> str:
    """Repository-relative POSIX path; knowledge-base content maps to its original relative path."""
    path = Path(path)
    for root in (KNOWLEDGE_ROOT, PROJECT_ROOT):
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            continue
    raise ValueError(f"{path} is outside the repository")


def knowledge_checkout_missing() -> bool:
    """True when the knowledge-base checkout is absent."""
    return not KNOWLEDGE_ROOT.is_dir()


def skip_without_knowledge(guard: str) -> None:
    """Exit 0 with a WARN when knowledge-base is absent (fail if SOLO_REQUIRE_SIBLINGS=1)."""
    if knowledge_checkout_missing() and os.environ.get("SOLO_REQUIRE_SIBLINGS") != "1":
        print(f"WARN: {guard} skipped: knowledge-base checkout not found at {KNOWLEDGE_ROOT}")
        raise SystemExit(0)


# Top-level hub paths that now belong to other repositories (ops-toolkit, agent-skills,
# mcp-aggregator, the registry hub, ...). Evidence references into them cannot be verified from
# this checkout, so guards treat them as external instead of failing.
EXTERNAL_TOP_LEVEL = frozenset({
    "infra", "skills", "mcp", "dashboard", "repository-catalog", "ventures", "empire",
    "integrations", "opportunities", "catalog", "diary", "engagements", "clients",
    ".github", ".claude", ".codex", ".gemini", ".qoder", ".mcp.json", "AGENTS.md",
    "CLAUDE.md", "Makefile", "LICENSE", "accounts", ".gitleaks.toml", ".gitattributes",
    ".task-version", "README.md", "Taskfile.yml",
})
_BOOK_DEV = ("projects", "product", "engineering", "book-dev")


def is_external_ref(relative) -> bool:
    """True when a repository-relative evidence path points into another repository."""
    parts = Path(str(relative)).parts
    if not parts:
        return False
    if parts[: len(_BOOK_DEV)] == _BOOK_DEV:
        return True
    # knowledge-base folders are resolved by repo_file(); they are not external here.
    return parts[0] in EXTERNAL_TOP_LEVEL and not (len(parts) == 1 and parts[0] == "README.md" and (PROJECT_ROOT / "README.md").exists())
