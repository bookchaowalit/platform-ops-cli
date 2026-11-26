"""
Shared environment loader for Solo Empire Python scripts.
Loads infra/.env and provides computed paths.

Usage in scripts:
    from _env import PROJECT_ROOT, DB_PATH, get_google_creds, get_env
"""

import os
import sys
from pathlib import Path

# ops-toolkit root: _env.py -> utils -> scripts -> infra -> <ops-toolkit checkout>
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
# Sibling repositories live next to this checkout (same parent folder) unless a
# SOLO_<NAME>_ROOT variable points elsewhere. ops-toolkit never depends on the
# solo-empire hub: it has no path back to it.
def main_checkout(repo_root: Path) -> Path:
    """The main clone of repo_root: itself, or for a linked worktree (.git is a file
    "gitdir: <main>/.git/worktrees/<name>") the clone it belongs to."""
    marker = repo_root / ".git"
    if marker.is_file():
        text = marker.read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("gitdir:"):
            gitdir = Path(text[len("gitdir:"):].strip())
            if not gitdir.is_absolute():
                gitdir = (marker.parent / gitdir).resolve()
            if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
                return gitdir.parent.parent.parent
    return repo_root


# From a linked worktree (outside the clone folder) siblings are found beside the main clone.
SIBLINGS_PARENT = main_checkout(PROJECT_ROOT).parent

# Canonical relative path from repository root
DEFAULT_DB_REL = Path("data") / "runtime" / "operations.db"
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
BRIEFINGS_DIR = PROJECT_ROOT / "data" / "generated" / "briefings"
DRAFTS_DIR = BRIEFINGS_DIR / "drafts"
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
# Generated markdown reports (formerly written to the retired dashboard/ folder).
REPORTS_DIR = Path(os.environ.get(
    "SOLO_REPORTS_DIR", str(PROJECT_ROOT / "data" / "generated" / "reports")
))


# Repositories lost their `solo-` prefix on 2026-10-12. The override variables keep their
# historical names: new repository name -> NAME in SOLO_<NAME>_ROOT.
LEGACY_ENV_NAMES = {
    "business-catalog": "CATALOG",
    "agent-skills": "SKILLS",
    "architecture-docs": "ARCHITECTURE",
    "mcp-aggregator": "MCP",
    "ops-toolkit": "OPS",
    "time-tracking": "TIME",
    "roles-skills": "ROLES_SKILLS",
    "projects-cell": "PROJECTS",
    "opportunities": "OPPORTUNITIES",
    "operating-cycles": "OPERATING",
    "learning-cell": "LEARNING",
    "intake": "INTAKE",
    "health-metrics": "HEALTH",
    "diary": "DIARY",
    "content-cell": "CONTENT",
    "automation-cell": "AUTOMATION",
    "accounts": "ACCOUNTS",
    "goals": "GOALS",
    "knowledge-base": "KNOWLEDGE",
    "notifications": "NOTIFICATIONS",
    "integrations": "INTEGRATIONS",
    "saas-catalog": "SAAS_CATALOG",
    "knowledge-api": "KNOWLEDGE_API",
    "ai-gateway": "AI_GATEWAY",
    "crm": "CRM",
    "decisions": "DECISIONS",
    "habits": "HABITS",
    "owner-web": "WEB",
    "hooks-worker": "HOOKS_WORKER",
    "data-lake": "DATA_LAKE",
    "browser-extension": "BROWSER_EXTENSION",
    "platform-api-archive": "PLATFORM_API",
    "ai-usage": "AI_USAGE",
    "mcp-remote": "MCP_REMOTE",
    "cloud-api": "CLOUD_API",
    "platform-kit": "PLATFORM_KIT",
    "homelab": "HOMELAB",
    "mlops": "MLOPS",
    "event-hub": "EVENT_HUB",
    "data-lake-core": "EMPIRE_DATA_LAKE",
    "media-worker": "MEDIA_WORKER",
    "media-assets": "MEDIA",
    "insights-worker": "INSIGHTS_WORKER",
    "service-auth": "SERVICE_AUTH",
    "rag-demo-cf": "RAG_DEMO_CF",
    "cloud-monitor": "CLOUD_MONITOR",
    "insights-dashboard": "EMPIRE_INSIGHTS",
    "backstage-portal": "EMPIRE_BACKSTAGE",
    # Domain-first folder names (2026-10-13) keep the same override variables.
    "content-production": "BOOK_PRODUCTION_PLATFORM",
    "ai-rag-demo-worker": "RAG_DEMO_CF",
    "content-media-worker": "MEDIA_WORKER",
    "data-lake-pipelines": "DATA_LAKE",
    "data-mlops-pipeline": "MLOPS",
    "life-content-api": "CONTENT",
    "life-dashboard-web": "WEB",
    "life-decisions-api": "DECISIONS",
    "life-diary-api": "DIARY",
    "life-goals-api": "GOALS",
    "life-habits-api": "HABITS",
    "life-health-api": "HEALTH",
    "life-learning-api": "LEARNING",
    "life-operating-cycles-api": "OPERATING",
    "life-opportunities-api": "OPPORTUNITIES",
    "life-projects-api": "PROJECTS",
    "life-roles-skills-api": "ROLES_SKILLS",
    "life-time-api": "TIME",
    "market-insights-web": "EMPIRE_INSIGHTS",
    "market-insights-worker": "INSIGHTS_WORKER",
    "platform-accounts-api": "ACCOUNTS",
    "platform-agent-skills": "SKILLS",
    "platform-ai-gateway-api": "AI_GATEWAY",
    "platform-ai-usage-api": "AI_USAGE",
    "platform-auth-sdk": "SERVICE_AUTH",
    "platform-automation-api": "AUTOMATION",
    "platform-cloud-api": "CLOUD_API",
    "platform-developer-portal": "EMPIRE_BACKSTAGE",
    "platform-event-hub-api": "EVENT_HUB",
    "platform-homelab-infra": "HOMELAB",
    "platform-infra-kit": "PLATFORM_KIT",
    "platform-intake-api": "INTAKE",
    "platform-integrations-api": "INTEGRATIONS",
    "platform-knowledge-api": "KNOWLEDGE_API",
    "platform-legacy-api-archive": "PLATFORM_API",
    "platform-mcp-aggregator": "MCP",
    "platform-mcp-remote-worker": "MCP_REMOTE",
    "platform-notifications-api": "NOTIFICATIONS",
    "platform-ops-cli": "OPS",
    "platform-saas-catalog-api": "SAAS_CATALOG",
    "platform-uptime-worker": "CLOUD_MONITOR",
    "platform-webhooks-worker": "HOOKS_WORKER",
    "realestate-lead-extension": "BROWSER_EXTENSION",
    "sales-crm": "CRM",
}


def legacy_env_name(repo: str) -> str:
    """NAME of the SOLO_<NAME>_ROOT override variable for a repository folder name."""
    name = repo.split("/")[-1]
    return LEGACY_ENV_NAMES.get(name) or (name[len("solo-"):] if name.startswith("solo-") else name).upper().replace("-", "_")


def sibling_repo_root(repo: str) -> Path:
    """Checkout root of `bookchaowalit/<repo>` (override: SOLO_<NAME>_ROOT, default ../<repo>)."""
    name = repo.split("/")[-1]
    override = os.environ.get(f"SOLO_{legacy_env_name(name)}_ROOT")
    if override:
        return Path(override).expanduser()
    default = SIBLINGS_PARENT / name
    if default.exists() or SIBLINGS_PARENT.name not in ("public", "private"):
        return default
    # Hub layout github/<owner>/<visibility>/<repo>: a private sibling of a
    # public checkout (or the reverse) sits in the other visibility folder.
    other = SIBLINGS_PARENT.parent / ("private" if SIBLINGS_PARENT.name == "public" else "public") / name
    return other if other.exists() else default


# Workspace documentation (projects/, knowledge/, roles/, learning/, quiz/, goals/ ...)
# lives in bookchaowalit/knowledge-base (SOLO_KNOWLEDGE_ROOT, default ../knowledge-base).
KNOWLEDGE_ROOT = sibling_repo_root("knowledge-base")
KNOWLEDGE_BASE = KNOWLEDGE_ROOT / "knowledge"
# Architecture docs, systems, operations, templates (architecture-docs SPEC-001 phase 2):
# SOLO_ARCHITECTURE_ROOT, else the architecture-docs checkout (../architecture-docs or the other
# visibility folder), else the local snapshot of store space architecture-docs kept by the hub's
# knowledge_snapshot.py --space architecture-docs (SOLO_ARCHITECTURE_SNAPSHOT, default
# ~/.cache/solo-empire/architecture-docs). Git stays the source until the phase 3 cutover.
SNAPSHOT_STAMP = ".snapshot.json"


def architecture_snapshot(environ=os.environ) -> Path:
    override = (environ.get("SOLO_ARCHITECTURE_SNAPSHOT") or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".cache" / "solo-empire" / "architecture-docs"


def architecture_root(environ=os.environ) -> Path:
    if (environ.get("SOLO_ARCHITECTURE_ROOT") or "").strip():
        return sibling_repo_root("architecture-docs")
    checkout = sibling_repo_root("architecture-docs")
    if checkout.is_dir():
        return checkout
    snapshot = architecture_snapshot(environ)
    if not (snapshot / SNAPSHOT_STAMP).is_file():
        return checkout
    warn_if_stale_snapshot(snapshot)
    # Resolved: the snapshot folder is a symlink that refreshes swap atomically.
    return snapshot.resolve()


def warn_if_stale_snapshot(snapshot: Path, max_age_hours: float = 24) -> None:
    """Readers keep working on an old snapshot but say so on stderr (SPEC-001: stale after 24 h)."""
    import json
    import time
    try:
        pulled = float(json.loads((snapshot / SNAPSHOT_STAMP).read_text(encoding="utf-8"))["pulled_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return
    age = (time.time() - pulled) / 3600
    if age > max_age_hours:
        print(f"WARN: architecture snapshot {snapshot} is {age:.0f} h old; refresh it with the hub's "
              "knowledge_snapshot.py --space architecture-docs --refresh", file=sys.stderr)


ARCHITECTURE_ROOT = architecture_root()
# Agent skills: bookchaowalit/platform-agent-skills (SOLO_SKILLS_ROOT, default ../platform-agent-skills).
SKILLS_ROOT = sibling_repo_root("platform-agent-skills")
# Registry of repositories/systems (the `repository-catalog` folder). It is owned by
# the registry repository; there is no default. Tools that need it take SOLO_REGISTRY_DIR
# (or an argument) and skip clearly when it is unset.
REGISTRY_DIR = Path(os.environ["SOLO_REGISTRY_DIR"]).expanduser() if os.environ.get("SOLO_REGISTRY_DIR") else None


def registry_file(relative) -> Path:
    """Path of a file below SOLO_REGISTRY_DIR; raises when the variable is unset."""
    if REGISTRY_DIR is None:
        raise RegistryUnavailable("SOLO_REGISTRY_DIR is not set")
    return REGISTRY_DIR / relative


class RegistryUnavailable(RuntimeError):
    """SOLO_REGISTRY_DIR is unset (the registry is not part of ops-toolkit)."""


def skip_without_registry(tool: str) -> None:
    """Exit 0 with a clear SKIP line when SOLO_REGISTRY_DIR is unset or missing."""
    if REGISTRY_DIR is None or not REGISTRY_DIR.is_dir():
        print(f"SKIP: {tool}: set SOLO_REGISTRY_DIR to the repository-catalog folder")
        raise SystemExit(0)


def book_dev_tree() -> Path | None:
    """The `book-dev` folder (parent of `github/`) that holds the repository clones, or None."""
    github = book_dev_github_root()
    return github.parent if github is not None else None


def book_dev_github_root() -> Path | None:
    """The `.../book-dev/github` folder holding the owner clones (SOLO_BOOK_DEV_GITHUB_ROOT).

    When ops-toolkit is checked out as `<github>/<owner>/<visibility>/ops-toolkit` the folder is
    derived from that layout; otherwise it is None and callers skip.
    """
    override = os.environ.get("SOLO_BOOK_DEV_GITHUB_ROOT")
    if override:
        return Path(override).expanduser()
    parents = PROJECT_ROOT.parents
    if len(parents) >= 3 and parents[0].name in {"private", "public"}:
        return parents[2]
    return None


# The production tools catalog is owned by bookchaowalit/content-production.
PRODUCTION_TOOLS_REF = "bookchaowalit/content-production:catalog/production-tools.yaml"


def production_tools_catalog() -> Path:
    """Catalog path in the content-production checkout (override: SOLO_BOOK_PRODUCTION_PLATFORM_ROOT)."""
    return sibling_ref_file(PRODUCTION_TOOLS_REF)


def sibling_ref_file(ref: str) -> Path:
    """Resolve a `bookchaowalit/<repo>:<path>` reference to a path in the sibling checkout.

    The returned path may not exist (sibling absent); check `.exists()` before reading.
    """
    repo, sep, rel_path = ref.partition(":")
    if not sep or not rel_path:
        raise ValueError(f"expected 'bookchaowalit/<repo>:<path>', got {ref!r}")
    return sibling_repo_root(repo) / rel_path


# ── .env loader ─────────────────────────────────────────────
def load_env():
    """Load infra/.env into os.environ (idempotent)."""
    env_path = PROJECT_ROOT / "infra" / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Don't overwrite existing env vars (allow override from shell)
            if key not in os.environ:
                os.environ[key] = value


def resolve_db_path(env_value: str | None = None) -> Path:
    """
    Resolve the SQLite path the test doubles use.

    The control-plane database was retired on 2026-10-11 (archived outside the
    repository); production code never opens it. Tests point their SQLite
    doubles at a temporary file through SOLO_EMPIRE_DB_PATH.

    Uses SOLO_EMPIRE_DB_PATH when set:
    - absolute path → used as-is (after expanduser)
    - relative path → resolved from PROJECT_ROOT (not CWD)

    Default when unset: <PROJECT_ROOT>/data/runtime/operations.db (nominal; no file is shipped)
    """
    if env_value is None:
        env_value = os.environ.get("SOLO_EMPIRE_DB_PATH")
    if env_value is None or str(env_value).strip() == "":
        return (PROJECT_ROOT / DEFAULT_DB_REL).resolve()

    path = Path(str(env_value).strip()).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()


# Load on import
load_env()

# ── Computed paths ──────────────────────────────────────────
DB_PATH = resolve_db_path()


# ── Secret accessors ────────────────────────────────────────
def _looks_like_secret(key):
    """Identify credential-shaped env names without treating normal config as secrets."""
    name = key.upper()
    markers = (
        "_TOKEN", "_SECRET", "_PASSWORD", "_PASS", "_API_KEY", "_ACCESS_KEY",
        "_PRIVATE_KEY", "_WEBHOOK_URL", "_DATABASE_URL", "_CLIENT_ID", "_CHAT_ID",
    )
    return any(name.endswith(marker) for marker in markers)


def get_env(key, default=None, required=False):
    """Get config; credential-shaped names resolve through the secret provider."""
    if _looks_like_secret(key):
        return get_secret(key, default=default, required=required)
    value = os.environ.get(key, default)
    if required and not value:
        raise ValueError(f"Required env var {key} is not set. Check infra/.env or infra/.env.example")
    return value


def get_secret(key, default=None, required=False):
    """Resolve a secret from the configured runtime provider or env fallback."""
    from secret_resolver import SecretUnavailable, get_secret as resolve_secret

    try:
        value = resolve_secret(key, default=default, required=required)
    except SecretUnavailable:
        if required:
            raise ValueError(
                f"Required env var {key} is not set. Check infra/.env or secret provider configuration"
            ) from None
        return default
    return value


def get_google_creds():
    """Return Google OAuth credentials from Infisical/Vault/env."""
    return (
        get_secret("GOOGLE_CLIENT_ID", required=True),
        get_secret("GOOGLE_CLIENT_SECRET", required=True),
        get_secret("GOOGLE_REFRESH_TOKEN", required=True),
    )


def get_google_alt_creds():
    """Return alternate Google OAuth creds (used by get_google_refresh_token.py utility)."""
    return (
        get_secret("GOOGLE_CLIENT_ID_ALT", required=True),
        get_secret("GOOGLE_CLIENT_SECRET_ALT", required=True),
    )


def get_cloudflare_token():
    """Return Cloudflare API token from environment (or legacy settings.json)."""
    token = get_secret("CLOUDFLARE_API_TOKEN")
    if token:
        return token
    # Fallback: try legacy Qwen settings
    import json
    settings_path = os.path.expanduser("~/.qwen/settings.json")
    if os.path.exists(settings_path):
        with open(settings_path) as f:
            settings = json.load(f)
            return settings.get("env", {}).get("CLOUDFLARE_API_TOKEN")
    return None


def get_strava_token():
    """Return Strava access token from environment."""
    return get_secret("STRAVA_ACCESS_TOKEN")


def get_binance_th_creds():
    """Return (api_key, api_secret) for Binance TH from environment."""
    return (
        get_secret("BINANCE_TH_API_KEY"),
        get_secret("BINANCE_TH_API_SECRET"),
    )


# Top-level folders that moved to the knowledge-base repository (2026-10-11).
KNOWLEDGE_DIRS = frozenset({
    "projects", "knowledge", "roles", "learning", "quiz", "routines", "goals",
    "habits", "decisions", "tasks", "sessions", "documentation-assets", "diary",
})
ARCHITECTURE_DIRS = frozenset({"docs", "systems", "operations", "templates"})
_MISSING_ROOT = Path("/nonexistent-solo-registry")
_BOOK_DEV_PREFIX = ("projects", "product", "engineering", "book-dev", "github")


def repo_file(relative) -> Path:
    """Resolve a legacy repository-relative path to the repository that owns it now.

    knowledge folders -> knowledge-base, docs/systems/operations/templates ->
    architecture-docs, skills -> agent-skills, repository-catalog -> SOLO_REGISTRY_DIR
    (non-existent path when unset), everything else -> this repository.
    """
    rel = Path(relative)
    parts = rel.parts
    if not parts:
        return PROJECT_ROOT
    head = parts[0]
    if parts[: len(_BOOK_DEV_PREFIX)] == _BOOK_DEV_PREFIX:
        # Clones of the Book Dev tree: <github>/<owner>/<visibility>/<repo>/...
        github = book_dev_github_root()
        return (github or _MISSING_ROOT) / Path(*parts[len(_BOOK_DEV_PREFIX):])
    if head in KNOWLEDGE_DIRS:
        return KNOWLEDGE_ROOT / rel
    if head in ARCHITECTURE_DIRS:
        return ARCHITECTURE_ROOT / rel
    if head == "skills":
        return SKILLS_ROOT / Path(*parts[1:])
    if head == "repository-catalog":
        return (REGISTRY_DIR or _MISSING_ROOT) / Path(*parts[1:])
    return PROJECT_ROOT / rel


def repo_rel(path) -> str:
    """Repository-relative POSIX path in the legacy layout (inverse of repo_file)."""
    path = Path(path)
    for root, prefix in (
        (KNOWLEDGE_ROOT, ""), (ARCHITECTURE_ROOT, ""), (SKILLS_ROOT, "skills/"), (PROJECT_ROOT, ""),
    ):
        try:
            return prefix + path.relative_to(root).as_posix()
        except ValueError:
            continue
    github = book_dev_github_root()
    if github is not None:
        try:
            return "/".join((*_BOOK_DEV_PREFIX, path.relative_to(github).as_posix()))
        except ValueError:
            pass
    raise ValueError(f"{path} is outside the repository")


def knowledge_checkout_missing() -> bool:
    """True when the knowledge-base checkout is absent."""
    return not KNOWLEDGE_ROOT.is_dir()


def skip_without_knowledge(guard: str) -> None:
    """Exit 0 with a WARN when knowledge-base is absent AND SOLO_EMPIRE_CI=1.

    CI checks out only this repository (the sibling is private); locally a missing
    checkout is unexpected, so the guard continues and fails on its own.
    """
    if knowledge_checkout_missing() and os.environ.get("SOLO_EMPIRE_CI") == "1":
        print(
            f"WARN: {guard} skipped: knowledge-base checkout not found at "
            f"{KNOWLEDGE_ROOT} (SOLO_EMPIRE_CI=1)"
        )
        raise SystemExit(0)


def skip_without_architecture(tool: str) -> None:
    """Exit 0 with a clear SKIP line when the architecture-docs checkout is absent."""
    if not ARCHITECTURE_ROOT.is_dir():
        print(f"SKIP: {tool}: architecture documents not found at {ARCHITECTURE_ROOT} (set SOLO_ARCHITECTURE_ROOT, or refresh the store snapshot)")
        raise SystemExit(0)


def registry_path(relative) -> Path:
    """Path below SOLO_REGISTRY_DIR (the repository-catalog folder); a non-existent path when unset."""
    return (REGISTRY_DIR or _MISSING_ROOT) / relative


def require_registry_file(relative, tool: str = "") -> Path:
    """Registry file path, or exit 2 with a clear message when SOLO_REGISTRY_DIR is unset/missing it."""
    path = registry_path(relative)
    if not path.is_file():
        label = f"{tool}: " if tool else ""
        raise SystemExit(
            f"{label}registry file {relative} not found; set SOLO_REGISTRY_DIR to the "
            "repository-catalog folder of the registry repository"
        )
    return path


def checkout_for_repo_path(repo_path: str) -> Path:
    """Local clone for a registry `repo.path` (last path component = repository name)."""
    return sibling_repo_root(Path(repo_path).name)


def display_path(path) -> str:
    """repo_rel() that falls back to the plain path when it is outside every known repository."""
    try:
        return repo_rel(path)
    except ValueError:
        return str(path)
