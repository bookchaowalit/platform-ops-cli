# 06 — C4 Container

"Container" ในที่นี้คือหน่วยที่รันแยกกันจริง: CLI entrypoints บนเครื่องเจ้าของ, scheduled jobs บน GitHub Actions, local timers และ CI runner image ที่ build จาก `infra/ci-runner/`

```mermaid
C4Container
  title platform-ops-cli - Containers
  Person(owner, "Owner / AI agent")
  System_Boundary(ops, "platform-ops-cli") {
    Container(facade, "empire + Taskfile", "Python 3, Task 3.53.1", "Routing facade and task adapters in infra/tasks")
    Container(cli, "Owner CLI", "TypeScript via tsx", "infra/cli.ts: tasks, goals, skills, habits, revenue, delivery")
    Container(scripts, "Python scripts", "Python 3.12 stdlib + PyYAML, PyJWT", "infra/scripts: utils, platform, sync, workflows, learning, security")
    Container(tests, "Offline test suite", "Python", "infra/tests/run_all.py with SQLite store doubles")
    Container(hubjob, "Org hub sync job", "GitHub Actions ubuntu-24.04", "org-hub-sync.yml workflow_dispatch from jobs queue")
    Container(failjob, "CI runner failover job", "GitHub Actions ubuntu-24.04", "ci-runner-failover.yml every 10 min")
    Container(timer, "Local user timers", "systemd --user", "book-dev-dispatch; tracker-sync-pilot template; org-hub timer disabled")
    Container(img, "CI runner image", "myoung34/github-runner ubuntu-noble + PostgreSQL", "32 runner slots, multi-runner.sh, job-cleanup.sh")
    ContainerDb(state, "Local runtime state", "JSON / Markdown files", "data/runtime/agent-loop/book-dev, .runtime/automation-tokens, .runtime/tracker-sync-pilot")
  }
  System_Ext(cells, "Cell APIs", "empire-platform")
  System_Ext(ks, "knowledge-api")
  System_Ext(mcp, "platform-mcp-aggregator checkout")
  System_Ext(gh, "GitHub")
  System_Ext(rail, "Railway runner-multi")
  System_Ext(local, "ci-runner-local container", "Owner machine Docker")
  System_Ext(lin, "Linear")
  System_Ext(jobs, "platform-jobs-worker", "Cloudflare Queues jobs: cron 0 2 * * * dispatches org-hub-sync")

  Rel(owner, facade, "./empire, task")
  Rel(facade, scripts, "run-python3.sh")
  Rel(facade, cli, "task tasks")
  Rel(cli, mcp, "imports src/*.ts")
  Rel(cli, cells, "HTTPS")
  Rel(scripts, cells, "HTTPS + JWT")
  Rel(scripts, ks, "HTTPS + JWT")
  Rel(scripts, state, "read / write")
  Rel(jobs, hubjob, "workflow_dispatch")
  Rel(hubjob, scripts, "org_hub_registry.py")
  Rel(hubjob, gh, "clone, commit, push")
  Rel(failjob, scripts, "ci_runner_failover.py")
  Rel(failjob, rail, "GraphQL")
  Rel(failjob, gh, "list self-hosted runners")
  Rel(timer, scripts, "book_dev_linear_cycle.py")
  Rel(timer, lin, "read issues")
  Rel(img, rail, "railway up -s runner-multi")
  Rel(img, local, "local/run.sh up")
```

Sources: `empire`, `Taskfile.yml`, `.task-version`, `infra/cli.ts`, `infra/package.json`, `infra/requirements-dev.txt`, `infra/tests/run_all.py`, `infra/tests/support/`, `.github/workflows/org-hub-sync.yml` (comment on `on:`), commit `5fdb2d9`, `.github/workflows/ci-runner-failover.yml`, `infra/scripts/workflows/install_book_dev_dispatch_timer.py`, `infra/templates/tracker-sync/`, `infra/scripts/sync/install_org_hub_timer.py`, `infra/ci-runner/Dockerfile`, `infra/ci-runner/README.md`, `infra/ci-runner/local/run.sh`, `infra/scripts/utils/task_store.py` (`TOKEN_DIR`), ops record: org-hub-sync-hosted (2026-11-03), ops record: messaging-layer-adr-0008 (2026-11-03)

## Open questions

- ไม่มีไฟล์ระบุ dependency Python ที่ runtime ใช้ (มีแค่ `requirements-dev.txt` และ `requirements-telegram.txt`): ควรมี lock สำหรับ scripts ที่รันบน timer หรือไม่
