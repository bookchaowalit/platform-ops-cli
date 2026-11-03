# 01 — Business process (As-Is / To-Be)

platform-ops-cli (ชื่อเดิมใน README: `ops-toolkit`) **ไม่ใช่ service ที่ deploy** (`PRODUCT.md` §Operating Context) เป็น monorepo ของ Python scripts + owner CLI (`infra/cli.ts`) + Task adapters ที่เจ้าของและ AI agent เรียกจาก terminal และมี 3 workflows ที่รันบน GitHub Actions บน GitHub Actions ของ repo นี้เอง
งานหลักในวันนี้แบ่งได้ 5 กลุ่ม: (1) daily workflows ที่อ่าน/เขียน cell APIs (tasks, goals, habits, diary …), (2) org hub registry sync, (3) Book Dev Linear dispatch (AI build → review → PR), (4) CI runner hosting (image + failover watchdog), (5) offline tests และ contract guards (`task check`)

## As-Is (จากโค้ดวันนี้)

```mermaid
flowchart LR
  subgraph Owner["Owner terminal / AI agent"]
    T["task ... / ./empire / infra/cli.ts"]
  end
  subgraph Daily["Daily workflows (local)"]
    D1["diary, diary:summary"]
    D2["tasks, decision, life:checkin, status"]
    D3["integrations:* read-only probes"]
  end
  subgraph Hosted["Hosted on GitHub Actions (this repo)"]
    H1["Org hub sync 09:00 BKK, dispatched by jobs queue"]
    H2["CI runner failover every 10 min"]
    H3["CI on push / PR"]
  end
  subgraph Timers["Local systemd user timers"]
    L1["book-dev-dispatch 08-22h every 2h"]
    L2["tracker-sync-pilot every 15 min (template)"]
  end
  Cells["empire-platform cell APIs"]
  KS["knowledge-api document store"]
  Hubs["org hub repos + bookchaowalit-empire"]
  JW["platform-jobs-worker"]
  Rail["Railway ci-runners / runner-multi"]
  Lin["Linear (read-only)"]
  PR["Pull request on target repo"]

  T --> D1 & D2 & D3
  D1 --> KS
  D1 --> Cells
  D2 --> Cells
  JW -->|"workflow_dispatch daily"| H1
  H1 -->|"commit + push projections"| Hubs
  H2 -->|"deploymentRestart / deploymentStop"| Rail
  L1 --> Lin
  L1 -->|"Claude build, Codex review"| PR
  T -->|"task check"| Ev["docs:check guards + run_all.py tests"]
```

Sources: `README.md`, `PRODUCT.md`, `Taskfile.yml`, commit `5fdb2d9`, `infra/tasks/operations.yml`, `infra/tasks/portfolio.yml`, `.github/workflows/org-hub-sync.yml`, `.github/workflows/ci-runner-failover.yml`, `.github/workflows/ci.yml`, `infra/scripts/workflows/install_book_dev_dispatch_timer.py`, `infra/templates/tracker-sync/tracker-sync-pilot.timer`, `infra/scripts/workflows/daily_diary.py`, `infra/scripts/utils/knowledge_store.py`, `docs/operations/book-dev-linear-dispatch.md`

### As-Is: daily diary (กระบวนการที่ทำบ่อยที่สุด)

```mermaid
flowchart TD
  A["Agent ends a conversation"] --> B["task diary -- --record-conversation"]
  B --> C{"client.env has EMPIRE_KNOWLEDGE_API and SOLO_WEB_PUBLIC_ORIGIN?"}
  C -->|"yes, SOLO_DIARY_STORE not off"| D["Mint 60 s RS256 JWT with owner key files"]
  D --> E["PUT diary/entries/YYYY/MM/DD in knowledge-api with if_version"]
  C -->|"no"| F["Write local copy under SOLO_KNOWLEDGE_ROOT/diary/entries"]
  E --> F
  F --> G["Daily note step reads Tasks API with reporting-reader token"]
  G -->|"token missing"| H["Step fails, owner action open"]
```

Sources: `infra/scripts/workflows/daily_diary.py`, `infra/scripts/utils/knowledge_store.py`, `README.md` §Diary, `PRODUCT.md` §Capabilities, ops record: hosted-document-store (2026-11-03)

## To-Be

repo นี้ไม่มี `specs/` หรือ `docs/UPGRADE-PLAN.md` To-Be ที่มีในเอกสาร repo มีเพียง:

- `PRODUCT.md` §Measures: `task test:strict` บน `main` ต้องมี skip/partial = 0 นอกกรณี sibling-checkout ที่ระบุไว้, ทุก release ของ 4 core targets ต้องมี quality evidence (ทั้งสอง task ยังไม่มีใน Taskfile ดู [03](03-user-flows.md)), diary ต้องบันทึกทุกวันทำงานเมื่อ `reporting-reader` ถูกตั้ง [ต้องยืนยัน]
- `docs/operations/book-dev-linear-dispatch.md`: timer จะเริ่มส่งงานได้จริงเมื่อมี `LINEAR_API_KEY` (read-only) ใน Infisical `/linear`
- `infra/ci-runner/README.md`: failover จะ switch จริงเมื่อเจ้าของตั้ง `RAILWAY_TOKEN` ใน environment `ci-runner-failover`
- `docs/superpowers/specs/2026-10-14-tracker-sync.md`: tracker sync pilot เปิดใช้เมื่อ credential preflight ผ่าน

## Open questions

- PRODUCT.md บอก "Out of scope: hosted schedulers" แต่ repo มี scheduled workflows 2 ตัว (org hub sync, CI runner failover): ควรแก้ PRODUCT.md หรือย้ายงาน
- To-Be ระดับ product (เช่น จะย้าย daily workflows ขึ้น hosted หรือไม่) ไม่มีในเอกสาร repo
