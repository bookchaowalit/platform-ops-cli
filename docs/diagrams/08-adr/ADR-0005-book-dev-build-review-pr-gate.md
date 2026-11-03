# ADR-0005 — Book Dev dispatch: Claude สร้างบน worktree branch, Codex รีวิว read-only, ผ่านแล้วเปิด PR (ไม่ merge/deploy เอง)

- **Status:** accepted (retro-documented)
- **วันที่ตัดสินใจ:** 2026-11-01 (commit `c1b4836`), guard hook 2026-11-02 (ops record: book-dev-build-review-flow)

## Context

Flow เดิมให้ provider เดียวทำงานใน checkout แล้ว commit/push/deploy เอง เจ้าของต้องการ cross-family review และ PR gate

## Decision

- `book_dev_linear_cycle.py` อ่าน Linear issue label `ai-dispatch` (read-only), ข้าม hold labels / issue ที่เคยส่ง / title ไม่ match checkout เดียว, ส่งไม่เกิน 1 issue ต่อรอบ, ledger ใน `state.json`, retry `MAX_FAILED_ATTEMPTS = 3`
- `book_dev_ai_dispatch.py --providers claude --reviewer codex`: worktree branch `ai/<issue>-<stamp>` จาก `origin/<branch>`; Codex รีวิว SHA นั้นใน detached checkout ใน temp dir ส่วนตัว (`-c project_doc_max_bytes=0`); `MAX_FIX_ROUNDS = 2`; PASS → push SHA ที่รีวิวแล้ว + `gh pr create`
- Branch ที่แก้ไฟล์คำสั่ง agent (`AGENTS*`, `CLAUDE*`, `.codex/`, `.claude/` …) หยุดเป็น `review_blocked`
- ทุกการเรียก Claude ได้ `book_dev_dispatch_guard.py` เป็น PreToolUse hook ผ่าน `--settings` และตัด env ที่ชื่อเป็น credential

## Consequences

- เจ้าของเป็นด่านสุดท้าย (merge/deploy ด้วยมือ)
- Guard ตรวจข้อความคำสั่ง ไม่ใช่ OS sandbox (residual risk ที่ยอมรับและบันทึกใน `docs/operations/book-dev-linear-dispatch.md`)
- Timer หยุดที่ `not_configured` จนกว่าจะมี `LINEAR_API_KEY` ใน Infisical `/linear`

## Evidence

`infra/scripts/workflows/book_dev_linear_cycle.py`, `book_dev_ai_dispatch.py`, `book_dev_dispatch_guard.py`, `install_book_dev_dispatch_timer.py`, `infra/tests/agent/book_dev_*_test.py`, `docs/operations/book-dev-linear-dispatch.md`, `git log` `c1b4836`
