# 11 — State machines

Entity ที่มี status/lifecycle ในโค้ดของ repo นี้: Task (ผ่าน Tasks API และ tracker sync mapping), Book Dev ticket / dispatch outcome, Linear issue ใน dispatch ledger, org hub target, Railway runner-multi (failover)

## 1. Task status (มุมมอง client + tracker sync mapping)

ค่าที่อนุญาตมาจาก DDL ที่คัดลอกจาก control plane เดิม (`sqlite_tasks_schema.sql`); การ transition จริงบังคับฝั่ง Tasks API (ไม่อยู่ใน repo นี้)

```mermaid
stateDiagram-v2
  [*] --> todo
  todo --> in_progress
  in_progress --> done
  todo --> blocked
  in_progress --> blocked
  blocked --> in_progress
  todo --> cancelled
  in_progress --> cancelled
  done --> [*]
  cancelled --> [*]
  note right of in_progress
    tracker sync: phase WIP maps to Jira In Progress / Trello WIP,
    phase Review maps to Jira In Review / Trello Review
  end note
  note right of blocked
    tracker sync holds and reports blocked / cancelled
  end note
```

Sources: `infra/tests/support/sqlite_tasks_schema.sql` (status CHECK), `docs/superpowers/specs/2026-10-14-tracker-sync.md` (mapping table), `infra/scripts/sync/tracker_sync.py`

## 2. Book Dev dispatch outcome (build-review flow)

```mermaid
stateDiagram-v2
  [*] --> queued: ticket written by loop / linear cycle
  queued --> building: worktree ai/issue-stamp created
  queued --> failed: no HEAD, fetch failed, worktree add failed
  building --> no_changes: no commits and tree untouched
  building --> uncommitted_changes: edits not committed
  building --> reviewing: commits on branch
  reviewing --> review_blocked: branch touches agent instruction files
  reviewing --> fixing: VERDICT BLOCKING and round < 2
  fixing --> reviewing
  reviewing --> review_blocked: still BLOCKING after 2 fix rounds
  reviewing --> failed: branch moved or no longer descends from base
  reviewing --> pr_open: VERDICT PASS, push reviewed SHA, gh pr create
  pr_open --> dispatched
  dispatched --> [*]
  review_blocked --> [*]: worktree kept for owner
  no_changes --> [*]
  uncommitted_changes --> [*]
  failed --> [*]
```

Sources: `infra/scripts/workflows/book_dev_ai_dispatch.py` (returns `failed`, `no_changes`, `uncommitted_changes`, `review_blocked`, `dispatched`; `MAX_FIX_ROUNDS = 2`), `docs/operations/book-dev-linear-dispatch.md` §ผลลัพธ์ต่อ issue

## 3. Linear issue in the dispatch ledger

```mermaid
stateDiagram-v2
  [*] --> candidate: label ai-dispatch, backlog or unstarted
  candidate --> skipped: hold label, title prefix not one checkout
  candidate --> already_dispatched: in state.json dispatched
  candidate --> selected: first by priority, max 1 per run
  selected --> recorded: status not failed
  selected --> retry: status failed and attempts < 3
  retry --> selected: next cycle
  selected --> recorded: failed and attempts reach 3
  recorded --> already_dispatched
  note right of recorded
    delete the dispatched entry to retry an issue
  end note
```

Sources: `infra/scripts/workflows/book_dev_linear_cycle.py` (`MAX_FAILED_ATTEMPTS = 3`, `state["dispatched"]`, `failed_attempts`), `docs/operations/book-dev-linear-dispatch.md`

## 4. Org hub target per run

```mermaid
stateDiagram-v2
  [*] --> checking
  checking --> unchanged: projection equals GitHub metadata
  checking --> updated: drift, commit and push (apply + publish)
  checking --> blocked: dirty checkout, branch or remote mismatch, permission change, unreadable, unregistered org
  unchanged --> [*]
  updated --> [*]
  blocked --> [*]: job exits 2, GitHub emails owner
```

Sources: `infra/scripts/sync/org_hub_registry.py`, `docs/operations/org-hub-registry-sync.md`, `.github/workflows/org-hub-sync.yml`

## 5. Railway runner-multi (watchdog view)

```mermaid
stateDiagram-v2
  [*] --> stopped
  stopped --> stopped: local online, action none
  stopped --> grace: local offline
  grace --> stopped: local back within 180 s
  grace --> running: deploymentRestart + Telegram
  running --> running: local offline, action none
  running --> draining: local online and Railway runner busy, action wait
  draining --> stopped: idle, deploymentStop + Telegram
  running --> stopped: local online and idle, deploymentStop
```

Sources: `infra/scripts/platform/ci_runner_failover.py` (`decide`, `--grace` 180), `infra/ci-runner/README.md` §Local primary

## Open questions

- Transition ที่ Tasks API อนุญาตจริง (เช่น `done` → `todo`) ไม่อยู่ใน repo นี้
