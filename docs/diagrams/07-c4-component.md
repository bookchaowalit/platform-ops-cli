# 07 — C4 Component (Python scripts container)

Component หลักภายใน `infra/scripts/` (โฟลเดอร์ตาม `infra/README.md`) และ module กลางที่ทุก script ใช้ร่วม

```mermaid
C4Component
  title platform-ops-cli - Components of infra/scripts
  Container_Boundary(scripts, "infra/scripts") {
    Component(env, "_env.py / paths.py", "Python", "PROJECT_ROOT, sibling_repo_root, repo_file, get_env, nominal DB_PATH")
    Component(sec, "secret_resolver.py", "Python", "SECRETS_PROVIDER env / infisical / vault / auto, values in memory only")
    Component(http, "_http.py", "Python", "api_request with retry on 429/5xx, Google OAuth refresh with safe error codes")
    Component(ts, "task_store.py", "Python", "Tasks API client, client.env switch, automation token per caller")
    Component(cs, "cell_store.py", "Python", "Generic cell client /v1/<plural> list/get/create/update/delete/stats")
    Component(oth, "habits / decision / notifications stores, crm_ledger.py", "Python", "Per-cell and CRM clients")
    Component(kst, "knowledge_store.py", "Python", "knowledge-api /api/v1/store/document, 60 s RS256 JWT")
    Component(wf, "workflows/*", "Python", "daily_diary, diary_summary, briefings, decisions, weekly review")
    Component(bd, "workflows/book_dev_*", "Python", "loop, linear_cycle, ai_dispatch, dispatch_guard, execution_hook")
    Component(sync, "sync/*", "Python", "read-only SaaS probes, org_hub_registry, tracker_sync, Google / Todoist / Strava")
    Component(plat, "platform/*", "Python", "cloud registries, usage, DNS, PaaS, self-hosted, ci_runner_failover, runpod")
    Component(guard, "utils/*_guard.py, docpack/*", "Python", "contract guards for docs:check, docpack build")
    Component(alerts, "alerts.py / system_health.py", "Python", "health checks, Telegram delivery")
  }
  System_Ext(cells, "Cell APIs")
  System_Ext(ks, "knowledge-api")
  System_Ext(inf, "Infisical / Vault")
  System_Ext(ext, "Provider APIs")

  Rel(wf, ts, "uses")
  Rel(wf, cs, "uses")
  Rel(wf, kst, "diary put")
  Rel(bd, ts, "reads open tasks")
  Rel(bd, sync, "linear_readonly")
  Rel(ts, cells, "HTTPS")
  Rel(cs, cells, "HTTPS")
  Rel(oth, cells, "HTTPS")
  Rel(kst, ks, "HTTPS")
  Rel(sync, http, "uses")
  Rel(plat, sec, "credentials")
  Rel(sync, sec, "credentials")
  Rel(sec, inf, "CLI / REST")
  Rel(http, ext, "HTTPS")
  Rel(alerts, ext, "Telegram sendMessage")
  Rel(guard, env, "sibling roots")
```

Sources: `infra/README.md`, `infra/scripts/utils/_env.py`, `infra/scripts/utils/paths.py`, `infra/scripts/utils/secret_resolver.py`, `infra/scripts/utils/_http.py`, `infra/scripts/utils/task_store.py`, `infra/scripts/utils/cell_store.py`, `infra/scripts/utils/habits_store.py`, `infra/scripts/utils/decision_store.py`, `infra/scripts/utils/notifications_store.py`, `infra/scripts/utils/crm_ledger.py`, `infra/scripts/utils/knowledge_store.py`, `infra/scripts/workflows/`, `infra/scripts/sync/`, `infra/scripts/platform/`, `infra/scripts/utils/alerts.py`, `Taskfile.yml` (`docs:check`)

## Owner CLI (`infra/cli.ts`)

```mermaid
flowchart LR
  CLI["infra/cli.ts commands: delivery, dashboard, skills, progress, goals, revenue, projects, habits, health, audit, review, tasks"]
  MM["mcp-modules.ts"]
  subgraph MCP["../platform-mcp-aggregator/src"]
    M1["task-state, task-store"]
    M2["habits-store, cell-store"]
    M3["crm-ledger, portfolio-reads"]
    M4["delivery-context"]
  end
  CLI --> MM --> M1 & M2 & M3 & M4
  CLI -->|"tasks sync/push/pull"| GO["go run infra/go/task-sync (directory missing)"]
  CLI -->|"progress"| PY["run-python3.sh portfolio_context_seed.py, portfolio_progress.py"]
```

Sources: `infra/cli.ts` (`case` labels, `await mcp(...)`, `runTaskSync`, `runPortfolioProgress`), `infra/mcp-modules.ts`, `README.md` §Delivery definitions

## Open questions

- `runTaskSync` ชี้ไป `infra/go/task-sync` ที่ไม่มีใน repo (ดู [03](03-user-flows.md))
