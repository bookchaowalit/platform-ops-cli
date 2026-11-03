# 18 — Git & release workflow

"Production" ของ repo นี้คือ `main`: scheduled jobs บน Actions รันจาก `main` ทุกรอบ และ checkout ของเจ้าของ `git pull` จาก `main` ไม่มี tag หรือ release
ในทางปฏิบัติ งานส่วนใหญ่ commit ตรงเข้า `main`: 98 commits ใน 90 วัน มี PR เพียง 4 (#1–#4) และ merge commit 1 (PR #3) `main` ไม่มี branch protection หรือ ruleset (`gh api .../branches/main/protection` → 404, rulesets ว่าง)

```mermaid
gitGraph
  commit id: "2a88aca split from hub"
  commit id: "152d5cc Railway runner"
  commit id: "8493570 PR 1"
  commit id: "3512069 PR 2 squash"
  branch fix-telegram-noti-first
  checkout fix-telegram-noti-first
  commit id: "book_noti_bot"
  checkout main
  merge fix-telegram-noti-first id: "1406fae PR 3 merge"
  commit id: "249d3d4 hosted org hub sync"
  commit id: "c1b4836 build-review"
  commit id: "7eed15f local runners"
  commit id: "24dffcf PR 4 squash"
  commit id: "07db1a4 registry"
  commit id: "5fdb2d9 HEAD jobs queue"
```

```mermaid
flowchart LR
  A["Edit in owner clone or agent worktree"] --> B{"via PR?"}
  B -->|"most changes"| C["git push origin main"]
  B -->|"PR #1-#4"| D["PR: ci.yml runs on pull_request"]
  D --> E["merge locally then push"]
  E --> C
  C --> F["ci.yml on push main (not a gate: no protection)"]
  C --> G["Next scheduled run uses new main: org hub sync, failover"]
  C --> H["Owner pulls checkout; timers use it on next tick"]
  C --> I["Runner image change: manual railway up / run.sh up"]
```

กติกาจาก memory ของเจ้าของ: merge ใน local clone แล้ว push (ไม่ใช้ `gh pr merge`) และงาน AI (Book Dev) ต้องผ่าน PR ที่เจ้าของ merge เอง (ADR-0005) ส่วนการแก้ repo นี้เองไม่มีกติกาบังคับใน repo

Sources: `git log origin/main --since=90.days` (98 commits), `git log --merges`, `gh pr list --state all`, `gh api repos/bookchaowalit/platform-ops-cli/branches/main/protection`, `gh api .../rulesets`, `.github/workflows/*.yml`, `infra/ci-runner/README.md`, [08-adr/ADR-0005](08-adr/ADR-0005-book-dev-build-review-pr-gate.md)

## Open questions

- ควรเปิด branch protection / required check `CI` บน `main` หรือไม่ เพราะ scheduled jobs ใช้ `main` ทันที (5 จาก 53 push runs บน main ล้ม, ดู [22](22-dora.md))
