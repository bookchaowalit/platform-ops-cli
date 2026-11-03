# 21 — Incident response

repo นี้ไม่มี runbook incident หรือ postmortem template ของตัวเอง flow ด้านล่างประกอบจากช่องแจ้งเตือนที่มีจริง (GitHub failure email, Telegram, exit codes) และขั้นตอนกู้ที่เขียนไว้ใน `infra/ci-runner/README.md`, `docs/operations/org-hub-registry-sync.md` และ `docs/operations/book-dev-linear-dispatch.md`
หลักการแก้ defect มาจาก `README.md` §Quality gate: บันทึก flow/preconditions → เขียน regression ที่ล้มก่อน → แก้ที่สาเหตุ → รัน journey ที่กระทบ ห้ามเพิ่ม retry/exclusion เพื่อให้เขียว

```mermaid
flowchart TD
  subgraph Alert["Alert"]
    A1["GitHub email: Org hub sync failed (blocked target exit 2 / token missing)"]
    A2["GitHub email: CI failed on main"]
    A3["Telegram: runner-multi started / stopped"]
    A4["Failover job error: runner-multi has no SUCCESS deployment"]
    A5["Owner notices: Book Dev report status failed / review_blocked"]
  end
  A1 --> T1["Read step summary: repository + status code"]
  A2 --> T2["gh run view --log-failed"]
  A3 --> T3["./infra/ci-runner/local/run.sh status"]
  A4 --> T4["Redeploy runner-multi by hand"]
  A5 --> T5["Open dispatch/stamp/report.json and review logs"]

  T1 --> F1{"Code"}
  F1 -->|"dirty checkout, branch or remote mismatch"| X1["Fix hub repo, rerun gh workflow run org-hub-sync.yml"]
  F1 -->|"unregistered org / permission change"| X2["Update umbrella registry/hubs.json"]
  T2 --> X3["Failing regression first, fix cause, push main"]
  T3 --> F3{"local runner state"}
  F3 -->|"offline, registration deleted"| X4["Replace /runner-config or local config, new RUNNER_TOKEN, redeploy"]
  F3 -->|"session already exists ~2 min"| X5["Wait, normal after restart"]
  T5 --> F5{"status"}
  F5 -->|"review_blocked"| X6["Owner reads worktree diff, decides; git worktree remove"]
  F5 -->|"failed / timeout"| X7["Fix cause; delete ledger entry to retry"]

  X1 & X2 & X3 & X4 & X6 & X7 --> V["Verify: next run green / runners online / PR opened"]
  V --> PM["Postmortem: not defined in repo (commit message + docs update in practice)"]
```

| Severity / ช่อง | ใครถูกแจ้ง | อยู่ที่ |
|---|---|---|
| Actions job failure | Owner (GitHub email) | GitHub notification settings |
| CI host switch | Owner (Telegram book_noti_bot) | `ci_runner_failover.py` `notify` |
| Book Dev / tracker sync | ไม่มี push alert, ดู report/journal บนเครื่อง | `data/runtime/...`, `journalctl --user` |

Sources: `.github/workflows/org-hub-sync.yml`, `.github/workflows/ci-runner-failover.yml`, `infra/scripts/platform/ci_runner_failover.py`, `infra/ci-runner/README.md` (§Registration and credentials, §Several runners), `docs/operations/org-hub-registry-sync.md`, `docs/operations/book-dev-linear-dispatch.md`, `README.md` §Quality gate, ops record: ci-hosting-policy (2026-11-03)

## Open questions

- Severity levels, เวลาตอบสนองที่คาดหวัง และ postmortem template ยังไม่กำหนด
- Incident ของงาน ops ควรสร้าง task ใน Tasks API อัตโนมัติ (แบบ backlog-sync ใน platform-empire) หรือไม่
