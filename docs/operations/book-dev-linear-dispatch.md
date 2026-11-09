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
Repo ปลายทางไม่มี `.claude/settings.json` และ hook ของ hub ไม่โหลดใน worktree dispatcher จึงส่ง `infra/scripts/workflows/book_dev_dispatch_guard.py` เป็น PreToolUse hook ผ่าน `--settings` ทุกครั้งที่เรียก Claude
- ทุก flow: ห้ามแตะ path ของ secret (`.env`, key, `~/.ssh`, `~/.config` …) และตัวแปร env ที่ชื่อเป็น credential (`$..._TOKEN`, `printenv`), ห้ามเขียนไฟล์นอก working directory ทั้งผ่าน Edit/Write และผ่าน shell (`>`, `tee`, `cp`, `mv`, `dd`; ยกเว้น `/dev/*`), ห้าม `sudo`, `git reset --hard`, `git clean`, force push, `rm -r` นอก working directory และการ dump env
- คำสั่ง shell ถูกแยก token ตาม quote แล้วตัดตาม `;`/`&&`/`|` และตรวจข้อความใน `bash -c`, `eval`, `$(...)` ด้วย
- Flow สร้าง–รีวิว (policy `branch`) ห้ามเพิ่ม: `git push`/`switch`/`checkout <branch>`/`merge`/`tag`/`worktree`, CLI `gh` ทุกคำสั่ง (รวมแบบอ่าน), CLI deploy/cloud/secret (`vercel`, `railway`, `wrangler`, `infisical` …), `npm publish`/`docker push`, curl/wget ที่ส่งข้อมูลออก และการเขียนไฟล์คำสั่ง agent
- Policy `branch` อนุญาต git เฉพาะ subcommand ในรายการ (add, commit, diff, log, stash, rebase …) ห้าม alias, `git -c`, `git config`, ตัวแปร `GIT_CONFIG*`/`GIT_SSH*`/`GIT_DIR` … และ option ที่สั่งรันโปรแกรมอื่น (`rebase --exec`, `--upload-pack`, `--ext-diff`) เพื่อไม่ให้เปลี่ยน hook, remote หรือ credential ที่ dispatcher จะใช้ตอน push (dispatcher เองก็ push ด้วย `-c core.hooksPath=/dev/null`)
- Claude builder รันโดยตัดตัวแปร env ที่ชื่อเป็น credential ออก (`*_TOKEN`, `*_API_KEY`, `*AUTH*`, `*_DSN`, `SSH_AUTH_SOCK` และ prefix ของผู้ให้บริการ เช่น `GH_`, `VERCEL_`, `RAILWAY_`, `AWS_` …; ใช้ denylist เพราะ build ต้องใช้ env ของ toolchain) เหลือแค่ login ของ Claude เอง
- ทดสอบสดแล้ว 2026-11-02: push, `gh` และการเขียน `AGENTS.md` ถูกบล็อก ส่วน `git status` และการเขียนไฟล์ปกติผ่าน
ข้อจำกัดที่เหลือ: guard ตรวจจากข้อความคำสั่ง ไม่ใช่ sandbox ระดับ OS (security review รอบหลัง ๆ เจอเพียงรูปแบบ option ที่แปลกขึ้นเรื่อย ๆ ถ้าต้องการขอบเขตแข็งจริงต้องรัน builder ใน OS sandbox เช่น bubblewrap หรือ sandbox ของ Claude Code โดยไม่มี credential ใน HOME) โปรแกรมที่ตั้งใจหลบ (เช่น สคริปต์ที่ builder เขียนเองแล้วสั่งรัน) ยังเลี่ยงได้ ด่านสุดท้ายจึงเป็นเจ้าของที่อ่าน diff ใน PR ก่อน merge
รายงานรีวิวทุกรอบอยู่ที่ `dispatch/<stamp>/codex-review-<n>.last-message.md` ส่วน `report.json` มี `flow` (branch, worktree, verdict รายรอบ, URL ของ PR)

### Builder แบบ Codex (Luna) และการเลือกโมเดลแยกตามบทบาท (spec 2026-11-09)

ใช้โควตา ChatGPT ที่มีอยู่แล้วเป็น worker ราคาถูก (reviewer ใช้ Sol ไม่ใช้ Astra เพราะ Astra กินโควตามากกว่าหลายเท่า, 2026-11-09): `--providers codex --reviewer codex --builder-model gpt-6-luna --reviewer-model gpt-6.1-sol`
(หรือ env `BOOK_DEV_DISPATCH_PROVIDERS=codex`, `BOOK_DEV_BUILDER_MODEL`, `BOOK_DEV_REVIEWER_MODEL`)

ข้อกำหนด:
- Builder `codex` รันใน OS sandbox ของ Codex (`--sandbox workspace-write`, `-c sandbox_workspace_write.network_access=false`) จึงเขียนได้แค่ใน worktree และออก network ไม่ได้ (push, `gh`, deploy ไม่ได้ทั้งที่ไม่มี guard hook) และรันโดยตัดตัวแปร env ที่ชื่อเป็น credential ออกเหมือน Claude
- Codex ทั้ง builder และ reviewer รันด้วย `--ignore-user-config`, `mcp_servers={}`, `web_search="disabled"` และปิด feature `apps`, `plugins`, `browser_use`, `computer_use` … เพราะ MCP/connector ทำงานนอก sandbox (เปิด PR หรือ deploy ได้ก่อนผ่านรีวิว) และ query ค้นเว็บพาข้อมูลออกได้ (ทดสอบสด 2026-11-09: เหลือแค่ shell และ tool ภายใน)
- dispatcher ต้องเจอ `codex` ที่ติดตั้งใน PATH จริง ไม่ใช้ `npx` fallback ใน flow นี้ (npx จะหา `node_modules/.bin/codex` จาก tree ของ builder)
- ก่อนรีวิวทุกรอบ dispatcher สแกน **ทุก commit** ใน `base..head` (เพราะ push ส่งประวัติทั้งหมด token ที่เพิ่มแล้วลบทีหลังก็ยังหลุด) หาไฟล์ credential (`.env`, `*.pem`, `id_rsa` …) และ token/private key ในเนื้อไฟล์แบบ bytes ทั้งไฟล์ (รองรับ binary และชื่อไฟล์ทุกแบบ) ถ้าเจอ หรือสแกนไม่สำเร็จ/ไฟล์ใหญ่เกิน 20 MB/เกิน 200 commit จะหยุดเป็น `review_blocked` (`flow.secret_findings` บอกแค่ path ไม่บอกค่า) ใช้กับ builder ทุกตัว ไฟล์ที่มี token อยู่ก่อนแล้วและถูกแก้ก็จะถูกหยุดด้วย (ตั้งใจให้ผิดฝั่งปลอดภัย)
- ข้อจำกัดที่เหลือ: sandbox ของ Codex จำกัดการเขียนและ network แต่ไม่จำกัดการอ่าน builder จึงยังอ่านไฟล์ใน HOME แล้วส่งเข้า context ของโมเดลได้ การสแกน diff กันได้แค่การ commit ออกมา
- builder แต่ละตัวเก็บเฉพาะตัวแปร auth ของตัวเองหลังการตัด env (`codex`: `OPENAI_API_KEY`, `CODEX_API_KEY`; `claude`: token ของ Claude)
- sandbox เขียน gitdir ของ worktree (อยู่ใน `.git/` ของ repo หลัก) ไม่ได้ builder จึง **ไม่ commit เอง** ให้แก้ไฟล์ทิ้งไว้ แล้ว dispatcher `git add -A` + commit ให้หลังแต่ละรอบ (ปิด hook ด้วย `-c core.hooksPath=/dev/null`, ข้อความ commit `<label>: <ชื่องาน>`) จากนั้นเข้ารีวิวตามเดิม
- builder กับ reviewer เป็น provider เดียวกันได้เฉพาะ `codex` และต้องระบุโมเดลทั้งสองฝั่งที่ไม่ซ้ำกัน (`flow.same_family = true` ใน report) reviewer ยังรันใน checkout แยกแบบ read-only ด้วย context ใหม่ ถ้าเป็นงานเสี่ยง (auth, เงิน, ข้อมูลส่วนตัว) ให้ใช้ builder `claude` หรือให้ Claude รีวิวซ้ำก่อน merge
- `--builder-model` ส่งเป็น `-m` ให้ Codex และ `--model` ให้ Claude; `--reviewer-model` ส่งเป็น `-m` ให้ Codex reviewer ถ้าไม่ระบุใช้ค่าเริ่มต้นของ CLI
- builder `grok` และ Antigravity (`agy`) ยังไม่รองรับใน flow นี้: ไม่มี guard hook และ `agy --sandbox` ไม่ได้ระบุว่ากัน network/การเขียนนอก workspace

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
BOOK_DEV_DISPATCH_PROVIDERS=codex        # ค่าบนเครื่องเจ้าของตั้งแต่ 2026-11-09 (เดิม claude)
BOOK_DEV_DISPATCH_REVIEWER=codex
BOOK_DEV_BUILDER_MODEL=gpt-6-luna
BOOK_DEV_REVIEWER_MODEL=gpt-6.1-sol
BOOK_DEV_DISPATCH_MODE=implement
INFISICAL_CLI_ENABLED=1                  # ให้ timer ดึง LINEAR_API_KEY จาก Infisical /linear ผ่าน CLI ที่ login แล้ว
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
