# 03 — User flows

repo นี้เป็น CLI จึงไม่มี pages/routes flow ต่อ role คือเส้นทางคำสั่ง: `./empire <words>` → `task <name>` → Python script / `infra/cli.ts`
`empire` เป็น routing อย่างเดียว (longest-prefix match ใน `ROUTES`) ไม่มี business logic

## Flow 1 — Owner: คำสั่งผ่าน facade

```mermaid
flowchart TD
  A["./empire status / doctor / diary daily ..."] --> B{"resolve_route longest prefix"}
  B -->|"no match"| X["print help, exit nonzero"]
  B -->|"match"| C["build argv: task NAME -- ARGS (no shell)"]
  C --> D{"find_task: SOLO_EMPIRE_TASK, PATH, ~/.local/bin, ~/go/bin"}
  D -->|"not found"| Y["error: Task not installed"]
  D --> E["Task runs adapter from infra/tasks/*.yml"]
  E --> F["run-python3.sh picks Linux python3 (.venv wins)"]
  F --> G["script under infra/scripts/*"]
```

Sources: `empire`, `Taskfile.yml`, `infra/tasks/*.yml`, `infra/scripts/setup/run-python3.sh`, `infra/README.md`

## Flow 2 — Owner: owner CLI tasks

```mermaid
flowchart TD
  A["task tasks -- add / done / update / list"] --> B["infra/cli.ts"]
  B --> C["mcp-modules.ts loads src/*.ts from SOLO_MCP_ROOT or ../platform-mcp-aggregator"]
  C --> D["openTaskStore / openCellStore('owner-cli')"]
  D --> E{"client.env: SOLO_EMPIRE_TASKS_BACKEND=api and EMPIRE_TASKS_API"}
  E -->|"missing or file mode too open"| F["ClientConfigError"]
  E --> G["HTTPS call to Tasks API /v1/tasks"]
  G --> H["print table / JSON"]
```

Sources: `infra/cli.ts`, `infra/mcp-modules.ts`, `infra/scripts/utils/task_store.py`, `infra/scripts/utils/tasks_backend.py`

## Flow 3 — Owner: local verification gate (ที่มีจริงใน Taskfile)

```mermaid
flowchart TD
  A["task check"] --> B["task docs:check"]
  B --> B1["rnd, evidence, step, role-tasks, portfolio contexts, evaluation, engagements strict, contracts"]
  B1 -->|"sibling architecture / knowledge checkout absent"| S["guard skips or fails visibly"]
  A --> C["task test"]
  C --> D["infra/tests/run_all.py: each *_test.py in own interpreter"]
  D --> E["EMPIRE_CLIENT_CONFIG=/nonexistent, SOLO_DIARY_STORE=off, EMPIRE_KNOWLEDGE_API empty"]
  E --> F{"any nonzero exit?"}
  F -->|"yes"| X["exit 1"]
  F -->|"no"| G["exit 0 (skips counted as pass = partial)"]
```

Sources: `Taskfile.yml` (`check`, `docs:check`, `test`), `infra/tests/run_all.py`

**ข้อค้นพบ:** `README.md` §Quality gate, `PRODUCT.md` และ `docs/core-quality.md` อธิบาย `task test:strict` และ `task quality:core` (evidence `.runtime/quality/core-latest.json`) แต่ใน `Taskfile.yml` และ `infra/tasks/*.yml` ไม่มี task ทั้งสองชื่อ และไม่มีสคริปต์ใดใน repo ที่เขียน `core-latest.json` README ยังบอกว่า `task check` ใช้ `test:strict` ขณะที่ `Taskfile.yml` เรียก `task: test` จึงวาดเฉพาะ flow ที่มีจริง

## Flow 4 — Owner: org hub sync (manual / hosted)

```mermaid
flowchart LR
  A["task hubs:sync -- --index ... --checkouts-root ..."] --> B{"mode"}
  B -->|"default"| P["preview, no writes"]
  B -->|"--check"| Q["exit 1 on drift"]
  B -->|"--apply"| R["write + commit scoped projection files"]
  R -->|"--publish"| S["git push to hub"]
  B -->|"blocker"| X["exit 2: dirty checkout, remote mismatch, unknown org ..."]
```

Sources: `docs/operations/org-hub-registry-sync.md`, `infra/scripts/sync/org_hub_registry.py`, `.github/workflows/org-hub-sync.yml`

Flow ของ Book Dev dispatch (timer) อยู่ใน [10-sequences.md](10-sequences.md) และตารางขั้นตอนเดิมใน [`docs/operations/book-dev-linear-dispatch.md`](../operations/book-dev-linear-dispatch.md) (reused)

## Open questions

- `task quality:core` / `task test:strict` หายไปตอน split จาก hub (2026-10-11) หรืออยู่ repo อื่น: ต้องคืน task หรือแก้ README/PRODUCT.md/`docs/core-quality.md`
- `task tasks -- sync|push|pull` ใน `infra/cli.ts` เรียก `go run` ใน `infra/go/task-sync` ซึ่งไม่มีใน repo: flow นี้ใช้ไม่ได้ในวันนี้ ควรลบหรือชี้ไป repo อื่น
