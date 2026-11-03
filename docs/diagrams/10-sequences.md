# 10 — Sequences

4 เส้นทางที่สำคัญที่สุด: diary write (ทำทุกวันโดย agent), Book Dev dispatch (เปลี่ยนโค้ดใน repo อื่น), org hub sync (push เข้า hub ทุกวัน), CI runner failover (ควบคุม Railway)

## 1. `task diary -- --record-conversation` → knowledge store

```mermaid
sequenceDiagram
  autonumber
  participant A as AI agent
  participant D as daily_diary.py
  participant K as knowledge_store.py
  participant F as Owner key files ~/.config/solo-web
  participant API as knowledge-api
  participant L as Local diary/entries
  A->>D: --record-conversation --topic --question-summary --outcome-summary --functions
  D->>D: build bounded metadata entry (no raw prompt or response)
  D->>L: write daily note first (dir 0700, file 0600)
  D->>K: open_diary_store() from env or client.env
  alt SOLO_DIARY_STORE=off or config missing
    D-->>A: local copy only
  else store configured
    K->>F: read service-private.pem, owner-subjects.txt (mode checked)
    K->>K: mint RS256 JWT 60 s, aud knowledge-api, scope knowledge:write
    loop up to 3 attempts
      K->>API: GET /api/v1/store/document?space=knowledge-base&path=diary/entries/...
      API-->>K: body + version or 404
      D->>D: re-render note from current summaries (rebuild)
      K->>API: PUT same path with if_version (0 when new)
      alt 409
        API-->>K: StoreConflict, try again
      else ok
        API-->>K: new version, stop
      end
    end
    opt StoreUnavailable
      D-->>A: warning, note kept locally
    end
  end
```

Sources: `infra/scripts/workflows/daily_diary.py` (`write_daily_diary`, `store.put(... if_version=...)`), `infra/scripts/utils/knowledge_store.py` (`AUDIENCE`, `SCOPES`, `/api/v1/store/document`, `StoreConflict`), `README.md` §Diary, commits `d10ae39`, `181bad8`, `3601004`

## 2. Book Dev Linear cycle (build → review → PR)

```mermaid
sequenceDiagram
  autonumber
  participant T as systemd timer book-dev-dispatch
  participant C as book_dev_linear_cycle.py
  participant S as secret_resolver
  participant LN as Linear GraphQL
  participant X as book_dev_ai_dispatch.py
  participant CL as Claude Code builder
  participant G as dispatch_guard hook
  participant CX as Codex reviewer read-only
  participant GH as GitHub
  T->>C: run (08-22h every 2h BKK)
  C->>S: LINEAR_API_KEY
  alt missing
    C-->>T: status not_configured
  else present
    C->>LN: issues with label ai-dispatch, backlog or unstarted
    C->>C: drop hold labels, already dispatched, title prefix not one checkout
    C->>X: queue first ticket (max 1 per run)
    X->>GH: git fetch origin/<branch>
    X->>X: worktree branch ai/<issue>-<stamp>
    X->>CL: claude -p acceptEdits --settings guard, credential env stripped
    CL->>G: PreToolUse checks each command
    CL-->>X: commits on branch
    loop review round 0..2
      X->>CX: codex exec read-only on detached checkout of HEAD, project_doc_max_bytes=0
      CX-->>X: VERDICT PASS or BLOCKING
      opt BLOCKING and round < 2
        X->>CL: fix only blocking findings
      end
    end
    alt PASS
      X->>GH: push reviewed SHA, gh pr create
      X-->>C: dispatched
    else not passed after 2 fixes or agent files changed
      X-->>C: review_blocked, worktree kept
    end
    C->>C: record in state.json ledger (failed retried up to 3)
  end
```

Sources: `infra/scripts/workflows/book_dev_linear_cycle.py`, `infra/scripts/workflows/book_dev_ai_dispatch.py` (build/review loop, `MAX_FIX_ROUNDS`, `open_pull_request`), `infra/scripts/workflows/book_dev_dispatch_guard.py`, `infra/scripts/workflows/install_book_dev_dispatch_timer.py` (`SCHEDULE`), `docs/operations/book-dev-linear-dispatch.md`

## 3. Org hub sync (GitHub Actions)

```mermaid
sequenceDiagram
  autonumber
  participant CR as platform-jobs-worker (cron 0 2 * * * UTC)
  participant J as Actions job sync (env org-hub-sync)
  participant W as org_hub_registry.py
  participant GH as GitHub API and repos
  CR->>J: workflow_dispatch, runs only on main
  Note over CR: failed dispatch retried, then parked in jobs-dlq
  J->>J: check ORG_HUB_SYNC_TOKEN, gh auth setup-git
  J->>GH: clone bookchaowalit-empire to RUNNER_TEMP/hubs
  J->>W: --index registry/hubs.json --clone-missing --apply --publish
  W->>GH: /user must be bookchaowalit
  W->>GH: clone missing hubs (default branch, single branch)
  loop each hub target
    W->>GH: list org or owner repos metadata
    W->>W: verify owner, URL, path, symlink, marker
    alt unchanged
      W->>W: keep snapshot date, no commit
    else drift
      W->>GH: commit projection files, push
    else blocker
      W->>W: target blocked with code
    end
  end
  W-->>J: report.json (exit 2 on blockers)
  J->>J: print per-target status to step summary only
```

Sources: `.github/workflows/org-hub-sync.yml`, commit `5fdb2d9` (message: retry + jobs-dlq), `infra/scripts/sync/org_hub_registry.py`, `docs/operations/org-hub-registry-sync.md`

## 4. CI runner failover tick

```mermaid
sequenceDiagram
  autonumber
  participant CR as cron */10
  participant F as ci_runner_failover.py
  participant RW as Railway GraphQL
  participant GH as GitHub runners API
  participant TG as Telegram
  CR->>F: run (dry-run only on manual dispatch default)
  alt RAILWAY_TOKEN or CI_FAILOVER_GH_TOKEN empty
    F-->>CR: warning, nothing switched, exit 0
  else tokens set
    F->>RW: latest runner-multi deployment
    F->>GH: status of first runner in runners.txt (canary)
    F->>F: decide(local_online, railway_running, railway_busy)
    opt start
      F->>F: sleep grace 180 s, re-check canary
    end
    alt start
      F->>RW: deploymentRestart
      F->>TG: local offline, Railway started
    else stop
      F->>RW: deploymentStop
      F->>TG: local back, Railway stopped
    else wait or none
      F-->>CR: print decision only
    end
  end
```

Sources: `.github/workflows/ci-runner-failover.yml`, `infra/scripts/platform/ci_runner_failover.py` (`decide`, `main`, `notify`), `infra/ci-runner/local/runners.txt`, `infra/ci-runner/README.md`

## Open questions

- Diary: หลัง 409 ครบ 3 ครั้ง loop จบโดยไม่แจ้งเตือน (ไม่มี `else` หลัง `for`) ควรรายงานว่า upload ไม่สำเร็จหรือไม่
