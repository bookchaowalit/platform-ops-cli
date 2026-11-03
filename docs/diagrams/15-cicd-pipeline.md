# 15 — CI/CD pipeline

3 workflows ใน `.github/workflows/` ไม่มี CD job (repo ไม่ deploy) ภาพ runner image ที่ build ด้วยมือแยกไว้ที่ [16](16-deployment.md)

```mermaid
flowchart TB
  subgraph Triggers
    P["push main"]
    PR["pull_request"]
    WD["workflow_dispatch"]
    C1["workflow_dispatch from platform-jobs-worker, cron 0 2 * * * UTC"]
    C2["schedule */10 * * * *"]
  end

  subgraph CI["ci.yml (concurrency ci-ref, cancel in progress)"]
    IP["infra-python: ci-python.yml @87dc5e1c v1.1.1"]
    IN["infra-node: ci-node.yml @87dc5e1c"]
    SE["security: security.yml @87dc5e1c"]
  end
  subgraph IPsteps["ci-python steps"]
    a1["checkout, uv + Python 3.12, install deps"]
    a2["Ruff lint (format-check off)"]
    a3["typecheck"]
    a4["test-command: install Task 3.53.1, sha256 check, run_all.py --jobs 4"]
    a5["coverage gate (opt-in)"]
  end
  subgraph INsteps["ci-node steps"]
    b1["checkout, Node 22, npm ci"]
    b2["lint, typecheck (test-command empty)"]
  end
  subgraph SEsteps["security jobs"]
    s1["gitleaks"]
    s2["semgrep"]
    s3["osv-scanner"]
    s4["trivy-fs"]
  end

  subgraph HUB["org-hub-sync.yml job sync (env org-hub-sync, main only)"]
    h1["checkout, token check, gh auth setup-git"]
    h2["clone bookchaowalit-empire"]
    h3["org_hub_registry.py --clone-missing --apply --publish"]
    h4["per-target summary, exit code"]
  end
  subgraph FO["ci-runner-failover.yml job failover (env ci-runner-failover, main only)"]
    f1["checkout"]
    f2["ci_runner_failover.py (dry-run on manual default)"]
  end

  P --> CI
  PR --> CI
  WD --> CI
  IP --> IPsteps
  IN --> INsteps
  SE --> SEsteps
  a1 --> a2 --> a3 --> a4 --> a5
  b1 --> b2
  C1 --> HUB
  WD --> HUB
  h1 --> h2 --> h3 --> h4
  C2 --> FO
  WD --> FO
  f1 --> f2
```

| Workflow | Trigger | Runner | Secrets | ผลล่าสุด 90 วัน (`gh run list`) |
|---|---|---|---|---|
| `ci.yml` | push main, PR, dispatch | GitHub-hosted (reusable default `ubuntu-24.04`) | — | 59 runs (ทุก branch): 52 success, 6 failure, 1 cancelled (เริ่ม 2026-10-17) |
| `org-hub-sync.yml` | `workflow_dispatch` only (jobs queue 02:00 UTC ตั้งแต่ `5fdb2d9`; ก่อนหน้านั้น `schedule`) | `ubuntu-24.04` | `ORG_HUB_SYNC_TOKEN` | 8 runs: 8 success (เริ่ม 2026-10-28) |
| `ci-runner-failover.yml` | */10, dispatch | `ubuntu-24.04` | `RAILWAY_TOKEN`, `CI_FAILOVER_GH_TOKEN`, `TELEGRAM_*` | 2 runs: 1 dispatch + 1 schedule (เริ่ม 2026-11-03) |

ไม่มี `needs:` ระหว่าง jobs ใน `ci.yml`: ทั้ง 3 jobs รันขนานกัน

Sources: `.github/workflows/ci.yml`, `.github/workflows/org-hub-sync.yml`, commit `5fdb2d9`, `.github/workflows/ci-runner-failover.yml`, `.task-version`, platform-ci-workflows `.github/workflows/ci-python.yml`, `ci-node.yml`, `security.yml` @ `87dc5e1c`, `gh run list --repo bookchaowalit/platform-ops-cli --limit 300` (2026-11-03)

## Open questions

- Failover cron ทุก 10 นาที แต่มี scheduled run เพียง 1 ครั้งในวันแรก: GitHub ไม่รับประกันความถี่ของ schedule สั้น ๆ ควรเช็คต่อหรือย้ายไปที่อื่น
- `ci.yml` ไม่มี branch protection / required checks ให้ตรวจจากใน repo
