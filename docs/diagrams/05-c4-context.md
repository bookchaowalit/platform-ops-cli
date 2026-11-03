# 05 — C4 Context

platform-ops-cli เป็นกล่องเครื่องมือของเจ้าของ: ไม่มี server ของตัวเอง ทำงานเป็น client ของ cell APIs และ provider APIs และมีงาน schedule บน GitHub Actions

```mermaid
C4Context
  title platform-ops-cli - System Context
  Person(owner, "Owner", "Runs task / empire / infra/cli.ts from a terminal")
  Person(agent, "AI agents", "Claude Code, Codex: record diary, run scripts via SOLO_OPS_ROOT")
  System(ops, "platform-ops-cli", "Python scripts, owner CLI, Task adapters, registries, offline tests, CI runner image")
  System_Ext(cells, "empire-platform cell APIs", "Tasks, goals, projects, roles-skills, habits, decisions, notifications, automation, operating, intake")
  System_Ext(ks, "platform-knowledge-api", "Hosted document store, diary entries")
  System_Ext(crm, "sales-crm income-pipeline", "CRM ledger: revenue, invoices, clients")
  System_Ext(mcp, "platform-mcp-aggregator", "Sibling checkout: shared TS modules for infra/cli.ts")
  System_Ext(arch, "architecture-docs / knowledge-base", "Sibling checkouts read by contract guards")
  System_Ext(gh, "GitHub", "API, Actions, hub repos, PRs")
  System_Ext(rail, "Railway ci-runners", "runner-multi fallback service")
  System_Ext(jobs, "platform-jobs-worker", "Jobs queue: dispatches org-hub-sync.yml daily")
  System_Ext(lin, "Linear", "ai-dispatch labelled issues, read-only")
  System_Ext(trk, "Trello / Jira", "Tracker sync pilot targets")
  System_Ext(inf, "Infisical", "Secret provider via CLI or REST")
  System_Ext(tg, "Telegram Bot API", "Alerts and failover notices")
  System_Ext(saas, "Read-only SaaS probes", "Asana, Slack, Notion, Sentry, PostHog, Discord, Zoom, monday, MS Graph, WhatsApp")
  System_Ext(cloud, "Cloud provider APIs", "Cloudflare, Vercel, GitHub Actions usage, Neon, RunPod")
  System_Ext(google, "Google APIs / Todoist / Strava", "Gmail, Calendar, Sheets; tasks and activity sync scripts")

  Rel(owner, ops, "Runs commands")
  Rel(agent, ops, "Shells out")
  Rel(ops, cells, "HTTPS + automation JWT")
  Rel(ops, ks, "GET/PUT document, 60 s RS256 JWT")
  Rel(ops, crm, "HTTPS + owner token")
  Rel(ops, mcp, "Imports src/*.ts")
  Rel(ops, arch, "Reads files")
  Rel(ops, gh, "gh CLI / REST, push projections, open PRs")
  Rel(ops, rail, "GraphQL deploymentRestart / Stop")
  Rel(jobs, gh, "workflow_dispatch org-hub-sync.yml")
  Rel(ops, lin, "GraphQL read")
  Rel(ops, trk, "REST, allowlisted writes")
  Rel(ops, inf, "Resolve secrets")
  Rel(ops, tg, "sendMessage")
  Rel(ops, saas, "Read metadata")
  Rel(ops, cloud, "Read usage / status, DNS plan")
  Rel(ops, google, "OAuth refresh, API calls")
```

Sources: `README.md`, `PRODUCT.md`, `.github/workflows/org-hub-sync.yml`, commit `5fdb2d9`, `infra/scripts/utils/task_store.py`, `infra/scripts/utils/cell_store.py`, `infra/scripts/utils/knowledge_store.py`, `infra/scripts/utils/crm_ledger.py`, `infra/mcp-modules.ts`, `infra/scripts/sync/*.py` (provider origins), `infra/scripts/platform/ci_runner_failover.py`, `infra/scripts/sync/tracker_sync_http.py`, `infra/scripts/utils/secret_resolver.py`, `infra/scripts/utils/alerts.py`, `infra/scripts/platform/cloudflare_dns.py`, `infra/scripts/platform/vercel_billing.py`, `infra/scripts/platform/github_actions_usage.py`, `infra/scripts/platform/neon_projects.py`, `infra/scripts/utils/_http.py`

## Open questions

- Scripts กลุ่ม Gmail / Todoist / Strava / Google Calendar ยังใช้งานจริงหรือเป็น legacy จาก hub (ไม่มี timer หรือ workflow ที่เรียก)
