# ops-toolkit

Operations tooling for Solo Empire: the Python scripts behind the daily workflows, the owner CLI
(`infra/cli.ts`), platform registries, integration clients and their offline tests.

This repository was split out of the `solo-empire` registry repository on 2026-10-11. **The files were
copied, not moved with `git subtree`: everything before the split keeps its history in
`bookchaowalit/solo-empire`** (`infra/`, `Makefile`, `empire`, `data/indexes/task-command-ownership.json`,
the former `.github/workflows`). The registry and the agent-harness hooks now live in
`bookchaowalit/bookchaowalit-empire` (moved from solo-empire on 2026-11-12); it is **not** a dependency of this repository.

## Run the checks

```bash
cd ops-toolkit
task test              # every infra/tests/**/*_test.py script, offline (EMPIRE_CLIENT_CONFIG=/nonexistent)
task docs:check        # contract guards that read the sibling architecture/knowledge checkouts
task check             # docs:check + test:strict (skips block)
task install           # npm ci for the owner CLI (tsx, typescript)
task typecheck         # tsc --noEmit for infra/cli.ts (after task install)
task --list            # all other tasks (platform, integrations, learning, portfolio, ...)
```

`task test` passes in a fresh clone: tests that need a sibling repository skip with a clear `SKIP`
line when it is not checked out. The result is `partial`, not a fully verified pass.
No CI workflow is configured (Actions quota); run the commands locally.

## Quality gate (not just a green unit-test count)

```bash
task test:strict                  # blocks skips, partial runs, failures, timeouts and no tests
task quality:core -- --list       # explicit core scope, not every registered product
task quality:core                 # required owning suites + revision-bound JSON evidence
task quality:core -- --only solo-web,empire-platform
task quality:core -- --require-clean   # additionally refuses dirty release candidates
```

The core targets are `solo-ops`, `solo-mcp`, `empire-platform` and `solo-web` (target ids of the core quality gate).
Resolution is `SOLO_OPS_ROOT`, `SOLO_MCP_ROOT`, `SOLO_PLATFORM_ROOT`, `SOLO_CELL_KIT_ROOT`,
`SOLO_WEB_ROOT`, otherwise `../<repository>`. Missing checkouts/dependencies never pass.
The runner installs nothing and performs no deployment or live API/credential lookup.

- Ops runs its entire offline suite in strict mode. Missing ImageMagick/FFmpeg or sibling tests remain visible blockers.
- MCP runs typecheck and its real in-memory MCP/CLI contract tests, with a command/process-tree deadline.
- Platform runs the full suite against disposable local PostgreSQL, including the real HTTP adapter contract with `ops-toolkit`.
- Cell kit runs its full generated-cell contract. Exactly two named resource-specific non-applicable cases are reported explicitly; no dependency skip is waived.
- Web runs `check:release`: typecheck, all unit/script tests, installed Python auth parity, build, and managed local browser journeys. Provide `SERVICE_AUTH_PYTHON` (a test Python with `service-auth` installed) and Playwright's Chromium/system libraries. Tests use synthetic local services, not real owner login.

Evidence defaults to `.runtime/quality/core-latest.json` (ignored). It contains commands,
statuses, explicit skipped cases, timestamp, each HEAD/dirty flag and a checkout fingerprint
before/after. Changing a checkout while testing blocks the result. `--only` is explicitly a
subset, never a full-core pass. Reports contain no raw test logs or environment values.
Older evidence is not reused. The script-runner JSON is machine-only (`--json`); timeout
kills descendants, not just the test leader. Full pytest runs clear ambient `PYTEST_ADDOPTS`
and configured `addopts`; deselected tests do not count as a full pass. A stand-alone
assertion script must execute its assertions and print completion evidence (or use unittest);
a silent empty module cannot pass. The normal `task check` now uses `test:strict`.

**A passing local gate is not release approval.** Hosted real OIDC login, production
post-deploy smoke, business UAT and products outside these five targets are always marked
unverified. Do not deploy merely because `task test` or this gate exits zero. For every
escaped defect: record its flow/preconditions, reproduce with a failing regression first,
fix the cause, then run the affected journey and the owning gate before release. Unknown
or flaky cases stay blocked; never add retries/exclusions merely to make them green.

## Where things are

| Path | What |
|---|---|
| `infra/scripts/` | Python scripts (`utils`, `platform`, `sync`, `workflows`, `learning`, `quality`, `security`, ...) |
| `infra/tests/` | offline tests; `infra/tests/run_all.py` runs them all |
| `infra/tasks/*.yml` | Task adapters (flattened into the root `Taskfile.yml`) |
| `infra/platform/` | cloud-provider, domain and building-block registries owned by ops |
| `infra/cli.ts` | owner CLI (`task tasks`, `task status`); loads shared modules from the `mcp-aggregator` sibling |
| `empire`, `Makefile` | thin facades over Task |

## Sibling repositories (no path back to the hub)

Siblings are looked up as `../<repo>` next to this checkout, or through `SOLO_<NAME>_ROOT`
(the historical name without the `solo-` prefix, upper-cased, dashes to underscores; `ops-toolkit` is still `SOLO_OPS_ROOT`).

| Variable | Default | Used for |
|---|---|---|
| `SOLO_KNOWLEDGE_ROOT` | `../knowledge-base` | diary entries (`diary/entries`), roles, goals, learning, quiz |
| `SOLO_ARCHITECTURE_ROOT` | `../architecture-docs` | docs, systems, operations, templates read by the contract guards |
| `SOLO_SKILLS_ROOT` | `../platform-agent-skills` | skill packages |
| `SOLO_MCP_ROOT` | `../platform-mcp-aggregator` | `infra/cli.ts` imports `src/*.ts` from here (same module instances as the MCP server) |
| `SOLO_REGISTRY_DIR` | *(none)* | the registry repository's `repository-catalog` folder; tools that read `online-systems.yaml`, `runtime-boundaries.yaml`, `docpack-coverage.yaml` need it and skip clearly when unset |
| `SOLO_BOOK_DEV_GITHUB_ROOT` | derived from `.../github/<owner>/private/platform-ops-cli` | the folder that holds the repository clones |
| `SOLO_ACCOUNTS_ROOT`, `SOLO_MEDIA_ROOT`, `SOLO_INTEGRATIONS_ROOT`, ... | `../<repo>` | per-repository lookups |

`infra/scripts/utils/_env.py` is the single resolver (`PROJECT_ROOT` = this checkout, `repo_file()`,
`sibling_repo_root()`); the legacy repository-relative names `docs/`, `systems/`, `projects/`, `skills/`,
`repository-catalog/` and `projects/product/engineering/book-dev/github/...` are translated to the
owning checkout.

## Delivery definitions and role documentation

The read-only `delivery` command shares `src/delivery-context.ts` with the MCP
aggregator. It reads validated sources from `SOLO_ARCHITECTURE_ROOT` or the
`../architecture-docs` sibling; no hub paths, live task stores or production API
are used. Keep the MCP and architecture checkouts updated with this CLI.

```bash
./infra/node_modules/.bin/tsx infra/cli.ts delivery overview --json
./infra/node_modules/.bin/tsx infra/cli.ts delivery role --role product-manager --json
./infra/node_modules/.bin/tsx infra/cli.ts delivery refinement --step BA03 --json
./infra/node_modules/.bin/tsx infra/cli.ts delivery documents --step QA11 --json
```

`overview` maps all registered roles; `role` distinguishes dedicated/family
templates and direct delivery steps. `workflow`, `documents` and `refinement`
allow role/step filters. `artifact --artifact-id <returned-id> --offset 0
--limit 8000 --json` reads a paged registered policy/template artifact. IDs come
from the overview/role context; callers cannot supply a file path or root.
Known role-family policies resolve through `SOLO_KNOWLEDGE_ROOT` or the
knowledge-base sibling. Missing references remain unavailable; private diary,
records, pilot evidence and arbitrary files are not exposed.

Results include source versions/hashes and definition availability. They do not
certify project facts, approvals or live workflow readiness; runtime integration
remains false. Invalid input, missing checkouts and malformed results return
bounded JSON failures with a nonzero exit. The corresponding MCP tool is
`get_delivery_context`; an existing server/client must reload its updated tool
registration before it can expose the tool.

## Organization hub registries

`task hubs:sync -- --index /path/to/bookchaowalit-empire/registry/hubs.json --checkouts-root /path/to/hubs`
previews GitHub metadata projections without writes. Add `--check` to detect drift,
or `--apply --publish` to update and publish only the owning hub's registry files.
`task hubs:timer -- ... --activate` enables a verified local daily 09:00 Bangkok timer.
See [organization hub sync](docs/operations/org-hub-registry-sync.md) for ownership,
recovery and the local-machine scheduling contract. `task test:org-hubs` runs its tests.

## Diary

`task diary -- --record-conversation --topic ... --question-summary ... --outcome-summary ... --functions ...`
writes the private daily diary under `$SOLO_KNOWLEDGE_ROOT/diary/entries/YYYY/MM/DD/` (git-ignored in
knowledge-base, never committed).

## Secrets

Credentials come from `infra/.env` (git-ignored; template: `infra/.env.example`) or the configured secret
provider. Tests never need them: they use placeholders and the SQLite store doubles under `infra/tests/support/`.
