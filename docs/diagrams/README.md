# Core 22 SDLC diagram pack — platform-ops-cli

ชุดภาพ Core 22 ตาม SDLC Diagram Catalog v2 วาดจากโค้ดและ config ใน repo นี้ (ณ `origin/main` `5fdb2d9`, 2026-11-03)
repo นี้ (README เรียก `ops-toolkit`) เป็น **monorepo ของเครื่องมือ ops**: Python scripts, owner CLI `infra/cli.ts`, Task adapters, platform registries, offline tests และ source ของ CI runner image ไม่มี hosted runtime ของตัวเอง งานที่รันเองคือ GitHub Actions 3 workflows (CI, Org hub sync ที่ถูก dispatch จาก jobs queue, CI runner failover) และ local systemd timers (Book Dev dispatch, tracker sync pilot)

ทุกไฟล์มี `Sources:` ใต้ภาพ และ "Open questions" สำหรับสิ่งที่ยืนยันจากโค้ดไม่ได้

| # | Item | Status | File | Sources read |
|---|---|---|---|---|
| 01 | Business process As-Is / To-Be | done (To-Be จาก PRODUCT.md / ops docs เท่านั้น) | [01-business-process.md](01-business-process.md) | `README.md`, `PRODUCT.md`, `Taskfile.yml`, workflows, `daily_diary.py`, `knowledge_store.py` |
| 02 | Use cases | done | [02-use-cases.md](02-use-cases.md) | `PRODUCT.md`, `infra/cli.ts`, `task_store.py`, `cell_store.py`, workflows, timers |
| 03 | User flows | done (CLI flows) + reused | [03-user-flows.md](03-user-flows.md), → [`docs/operations/book-dev-linear-dispatch.md`](../operations/book-dev-linear-dispatch.md) | `empire`, `Taskfile.yml`, `infra/cli.ts`, `run_all.py`, `org-hub-registry-sync.md` |
| 04 | Traceability (RTM) | done (temporary IDs R-01..R-15) | [04-traceability.md](04-traceability.md) | `PRODUCT.md`, `docs/superpowers/specs/*`, `docs/operations/*`, `infra/tests/` |
| 05 | C4 Context | done | [05-c4-context.md](05-c4-context.md) | store clients, `infra/scripts/sync/*`, `platform/*`, workflows |
| 06 | C4 Container | done | [06-c4-container.md](06-c4-container.md) | `empire`, `infra/cli.ts`, workflows, timer installers, `infra/ci-runner/*` |
| 07 | C4 Component | done | [07-c4-component.md](07-c4-component.md) | `infra/README.md`, `infra/scripts/utils/*`, `infra/cli.ts`, `infra/mcp-modules.ts` |
| 08 | ADRs | done (6) | [08-adr/](08-adr/) | git log, workflows, `infra/ci-runner/README.md`, `task_store.py`, Book Dev scripts, central ADR-0001 |
| 09 | ERD | done (client view of remote resources + local state files; no own DB) | [09-erd.md](09-erd.md) | store clients, `sqlite_tasks_schema.sql`, Book Dev / tracker / hub writers |
| 10 | Sequences | done (4) | [10-sequences.md](10-sequences.md) | `daily_diary.py`, `knowledge_store.py`, `book_dev_*`, `org_hub_registry.py`, `ci_runner_failover.py` |
| 11 | State machines | done (5) | [11-state-machines.md](11-state-machines.md) | tasks DDL double, tracker spec, `book_dev_ai_dispatch.py`, `book_dev_linear_cycle.py`, `ci_runner_failover.py` |
| 12 | API & integrations | done (outbound only; no endpoints exposed) | [12-api-integration.md](12-api-integration.md) | store clients, `infra/scripts/sync/*`, `platform/*` |
| 13 | Threat model | done | [13-threat-model.md](13-threat-model.md) | `task_store.py`, `secret_resolver.py`, dispatch guard, workflows, `infra/ci-runner/*` |
| 14 | Backup & DR | done (no backup job; RTO/RPO not defined) | [14-backup-dr.md](14-backup-dr.md) | `infra/ci-runner/README.md`, `tasks_backend.py`, Book Dev doc, tracker spec, `Makefile` |
| 15 | CI/CD pipeline | done | [15-cicd-pipeline.md](15-cicd-pipeline.md) | `.github/workflows/*.yml`, platform-ci-workflows @ `87dc5e1c`, `gh run list` |
| 16 | Deployment | done (not deployed: checkout, Actions, timers, runner image) | [16-deployment.md](16-deployment.md) | `PRODUCT.md`, timer installers, `infra/ci-runner/*`, workflows |
| 17 | Test coverage matrix | done | [17-test-coverage-matrix.md](17-test-coverage-matrix.md) | `infra/tests/**` (127 files), `run_all.py`, `ci.yml` |
| 18 | Git & release workflow | done | [18-git-release-workflow.md](18-git-release-workflow.md) | `git log`, `gh pr list`, branch protection API |
| 19 | Service dependency map | done | [19-service-dependency-map.md](19-service-dependency-map.md) | entrypoints, store clients, workflows |
| 20 | Observability | done (no Sentry / PostHog / heartbeat of its own) | [20-observability.md](20-observability.md) | workflows, `alerts.py`, `ci_runner_failover.py`, `monitor_health.py`, `gh run list` |
| 21 | Incident response | done (assembled from real channels; no runbook in repo) | [21-incident-response.md](21-incident-response.md) | workflows, `infra/ci-runner/README.md`, ops docs, `README.md` §Quality gate |
| 22 | DORA | done (5/5 computed with proxies, ~18-day window) | [22-dora.md](22-dora.md) | `gh run list` (ci.yml main push), `git log` |

## ADR list

- [ADR-0001](08-adr/ADR-0001-standalone-ops-repo-with-sibling-resolution.md) แยก ops tooling เป็น repo เดี่ยว หา sibling ผ่าน `SOLO_*_ROOT`
- [ADR-0002](08-adr/ADR-0002-cells-over-http-with-automation-tokens.md) Cell APIs + automation token ต่อหน้าที่ (SQLite retired)
- [ADR-0003](08-adr/ADR-0003-hosted-schedules-on-public-repo-actions.md) งาน schedule บน GitHub-hosted Actions, env secret main-only (org hub sync ย้ายไป jobs queue dispatch)
- [ADR-0004](08-adr/ADR-0004-private-ci-local-primary-railway-fallback.md) Private CI: local primary, Railway runner-multi fallback
- [ADR-0005](08-adr/ADR-0005-book-dev-build-review-pr-gate.md) Book Dev: Claude build → Codex review → PR
- [ADR-0006](08-adr/ADR-0006-offline-tests-shared-ci-workflows.md) Offline per-file tests + shared CI/security workflows pinned by SHA

## Findings worth the owner's attention

- `task quality:core` และ `task test:strict` ที่ `README.md`, `PRODUCT.md`, `docs/core-quality.md` อธิบาย **ไม่มีใน Taskfile** และไม่มี script ที่เขียน `core-latest.json`; `task check` เรียก `test` ไม่ใช่ `test:strict` (ดู 03, 04)
- `infra/cli.ts tasks sync|push|pull` เรียก `go run` ใน `infra/go/task-sync` ที่ไม่มีใน repo (ดู 03, 07)
- README บอก "No CI workflow is configured" แต่มี `ci.yml`; PRODUCT.md บอก hosted schedulers อยู่นอก scope แต่มี 2 scheduled jobs (ดู 01, ADR-0003, ADR-0006)
- `docs/operations/org-hub-registry-sync.md` ยังเขียนว่า workflow ใช้ cron `0 2 * * *` ขณะที่ `5fdb2d9` เปลี่ยนเป็น dispatch จาก platform-jobs-worker (ดู 15)
- `ci-runner-failover.yml` cron ทุก 10 นาที มี scheduled run เพียง 1 ครั้งในวันแรก และ no-op จนกว่าเจ้าของจะตั้ง `RAILWAY_TOKEN` (ดู 15, 20)
- `main` ไม่มี branch protection: 5 / 53 push runs ล้มบน `main` โดย scheduled jobs ใช้ `main` ทันที (ดู 18, 22)
- `ORG_HUB_SYNC_TOKEN` ยังเป็น classic PAT สิทธิ์กว้าง (ดู 13)
- Diary upload: ถ้าได้ 409 ครบ 3 ครั้ง loop จบเงียบ ๆ โดยไม่มี warning (ดู 10)
- Central ADR-0001 (architecture-docs) ยังเขียน "never the local machine" ขัดกับ local-primary 2026-11-03 (ดู ADR-0004)

Validate: `node ~/work/core22/tools/validate.mjs docs/diagrams/*.md docs/diagrams/08-adr/*.md`
