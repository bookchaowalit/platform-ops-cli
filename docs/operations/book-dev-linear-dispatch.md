# Book Dev: ส่งงาน Linear ให้ AI อัตโนมัติ

สถานะการรันจริงให้ดูจาก timer และ journal บนเครื่องที่ติดตั้ง การผ่าน tests ไม่ได้แปลว่างานถูกส่งแล้ว

## ทำงานอย่างไร

`infra/scripts/workflows/book_dev_linear_cycle.py` รันหนึ่งรอบดังนี้:

1. อ่าน issue ใน Linear ที่มี label `ai-dispatch` และอยู่ในสถานะ backlog/unstarted (อ่านอย่างเดียว ไม่ย้าย ไม่ comment ไม่สร้าง issue)
2. ข้าม issue ที่มี label `รอเจ้าของ` หรือ `ติดเงื่อนไข`, issue ที่เคยส่งแล้ว และ issue ที่ชื่อไม่ขึ้นต้นด้วย `[ชื่อ-repo]` ที่ตรงกับ checkout ในเครื่องได้หนึ่งเดียว
3. เขียน handoff และ execution ticket ลง `data/runtime/agent-loop/book-dev/`
4. ส่ง ticket แรกตามลำดับ priority เข้า flow สร้าง–รีวิว (ค่าเริ่มต้น)

ส่งได้ไม่เกินหนึ่ง issue ต่อรอบ ประวัติอยู่ใน `data/runtime/agent-loop/book-dev/linear/state.json`
ถ้าหา CLI ไม่เจอ issue จะไม่ถูกนับว่าส่งแล้ว ถ้ารันแล้วล้มจะลองใหม่ได้ 3 ครั้ง ลบรายการใน `dispatched` เพื่อให้ส่ง issue นั้นใหม่

## Flow สร้าง–รีวิว (Claude สร้าง → Codex รีวิว → PR)

| ขั้น | ใครทำ | พื้นที่ทำงาน | ผลลัพธ์ |
|---|---|---|---|
| 1. สร้าง | Claude Code (`claude -p`, acceptEdits, tool allowlist) | worktree ใหม่ `data/runtime/agent-loop/book-dev/execution/worktrees/<repo>-<stamp>` บน branch `ai/<issue>-<stamp>` แตกจาก `origin/<branch ที่ checkout อยู่>` หลัง fetch (commit ที่ยังไม่ push ในเครื่องจะไม่ติดเข้า PR) | commit บน branch (ห้าม push/deploy/เปิด PR เอง) |
| 2. รีวิว | Codex (`codex exec --sandbox read-only`) | checkout แยกแบบ detached ที่ commit นั้นพอดี (worktree ต้องสะอาด ไม่มีไฟล์ค้างนอก commit) | รายงาน finding + บรรทัด `VERDICT: PASS` หรือ `VERDICT: BLOCKING` |
| 3. แก้ | Claude Code | worktree เดิม | commit แก้เฉพาะข้อ blocking สูงสุด 2 รอบ แต่ละรอบ Codex รีวิวใหม่ |
| 4. เปิด PR | dispatcher | push SHA ที่รีวิวผ่านเข้า branch แล้ว `gh pr create` | PR ไปยัง branch ที่ checkout อยู่ (ปกติ `main`) แล้วลบ worktree |
| 5. CI | GitHub Actions ของ repo | PR | CI ต้องผ่าน |
| 6. Merge/Deploy | เจ้าของ | – | dispatcher ไม่ merge และไม่ deploy เอง |

ผลลัพธ์ต่อ issue (`status` ใน report): `dispatched` = เปิด PR แล้ว, `review_blocked` = แก้ 2 รอบแล้ว Codex ยังไม่ผ่าน หรือ branch แก้ไฟล์คำสั่ง agent (`AGENTS.md`, `CLAUDE.md`, `.codex/`, `.claude/`, …) ที่ Codex จะโหลดเป็นคำสั่งตัวเอง จึงไม่ส่งรีวิว (เก็บ worktree ไว้ให้เจ้าของตัดสิน),
`no_changes` = Claude ไม่ได้แก้อะไร (ลบ worktree), `uncommitted_changes` = แก้แล้วแต่ไม่ commit,
`failed`/`timeout` = ดู log ใน dispatch directory สามกรณีหลังเก็บ worktree ไว้ถ้ามีงานค้าง (`flow.worktree_kept`) ลบเองด้วย `git worktree remove` เมื่อตัดสินแล้ว
Codex รีวิวด้วย `-c project_doc_max_bytes=0` จึงไม่โหลด `AGENTS.md`/`AGENTS.override.md` จาก commit ของ builder เป็นคำสั่งตัวเอง (ทดสอบจริงแล้ว 2026-11-01)
checkout ที่ใช้รีวิวและไฟล์ผลรีวิวอยู่ใน temp dir ใหม่ (สิทธิ์ 0700) แล้วคัดลอกกลับมาเก็บ
ข้อจำกัดที่เหลือ: Bash ของ builder ไม่ได้อยู่ใน sandbox ระดับ OS ถ้า builder ทิ้ง process ค้างไว้ก็ยังเข้าถึงไฟล์ของ user ได้ ด่านสุดท้ายจึงเป็นเจ้าของที่อ่าน diff ใน PR ก่อน merge
รายงานรีวิวทุกรอบอยู่ที่ `dispatch/<stamp>/codex-review-<n>.last-message.md` ส่วน `report.json` มี `flow` (branch, worktree, verdict รายรอบ, URL ของ PR)

ตั้ง `BOOK_DEV_DISPATCH_REVIEWER=none` เพื่อกลับไปใช้ flow เดิม (provider เดียว ทำงานใน checkout แล้ว commit, push, deploy ตามที่ repo ระบุ โดยไม่ผ่านรีวิว) ซึ่งไม่แนะนำ
Mode `plan` ไม่ผ่านรีวิวเพราะไม่มีการแก้ไฟล์
รันมือ: `task book-dev:dispatch:review -- --dry-run`

## สิ่งที่ต้องเตรียม

- `LINEAR_API_KEY` ใน Infisical path `/linear` (หรือ secret provider ที่ตั้งไว้) สิทธิ์อ่านอย่างเดียวก็พอ
- Codex CLI ที่ login แล้ว (`codex login status`) สำหรับขั้นรีวิว และ `gh` ที่ login แล้วสำหรับเปิด PR
- Claude Code ที่ login แบบใช้งานนอกแอปได้ CLI ของ Claude desktop (`~/.claude/remote/ccd-cli/`) ใช้ token ของแอปเองและตอบ `Not logged in` เมื่อรันจาก timer
  ให้ติดตั้ง Claude Code CLI แล้ว login หรือใส่ `CLAUDE_CODE_OAUTH_TOKEN` ใน env file
- ลำดับการหา CLI: `BOOK_DEV_CLAUDE_CLI`, `PATH`, `~/.local/bin/claude`, `~/.claude/local/claude` แล้วจึงเป็น build ใหม่สุดใน `~/.claude/remote/ccd-cli/`

ตั้งค่าเพิ่มได้ใน `~/.config/book-dev-dispatch.env`:

```sh
BOOK_DEV_LINEAR_LABEL=ai-dispatch
BOOK_DEV_LINEAR_HOLD_LABELS=รอเจ้าของ,ติดเงื่อนไข
BOOK_DEV_DISPATCH_PROVIDERS=claude
BOOK_DEV_DISPATCH_REVIEWER=codex
BOOK_DEV_DISPATCH_MODE=implement
```

## คำสั่ง

```sh
task book-dev:linear:cycle -- --dry-run --json
task book-dev:linear:timer
task book-dev:linear:timer -- --activate
systemctl --user list-timers book-dev-dispatch.timer
journalctl --user -u book-dev-dispatch.service -n 40
systemctl --user disable --now book-dev-dispatch.timer
```

Timer รันทุก 2 ชั่วโมงตั้งแต่ 08:00 ถึง 22:00 (Asia/Bangkok) และไม่เริ่มรอบใหม่ระหว่างที่รอบก่อนยังทำงานอยู่
Service ให้เวลาหนึ่งรอบสูงสุด 3 ชั่วโมง 10 นาที (สร้าง + แก้ 2 + รีวิว 3 ครั้ง ครั้งละ 30 นาที) หลังอัปเดตให้รัน `task book-dev:linear:timer -- --activate --replace`
Timer ทำงานเฉพาะตอนที่เครื่องและ user service เปิดอยู่
