# ADR-0001 — แยก ops tooling เป็น repo เดี่ยว หา sibling ผ่าน `SOLO_*_ROOT` / `../<repo>`

- **Status:** accepted (retro-documented)
- **วันที่ตัดสินใจ:** 2026-10-11 (commit `2a88aca` "operations tooling moves here from the solo-empire hub")

## Context

เดิม scripts, Makefile, `empire` และ workflows อยู่ใน hub `bookchaowalit/solo-empire` ซึ่งเป็น registry repo หลายสิ่งอ้าง path ภายใน hub (`docs/`, `systems/`, `repository-catalog/` …)

## Decision

- คัดลอก (ไม่ใช่ `git subtree`) `infra/`, `Makefile`, `empire`, `data/indexes/task-command-ownership.json` มาไว้ repo นี้; history ก่อน 2026-10-11 อยู่ใน hub
- ไม่มี path ย้อนกลับไป hub: sibling หาได้จาก `SOLO_<NAME>_ROOT` หรือ `../<repo>`; `infra/scripts/utils/_env.py` เป็น resolver เดียว (`sibling_repo_root()`, `repo_file()`) และแปล path แบบ hub เดิมไปยัง checkout เจ้าของ
- Owner CLI import module จาก `platform-mcp-aggregator` (`infra/mcp-modules.ts`) เพื่อใช้ instance เดียวกับ MCP server
- Test ที่ต้องใช้ sibling จะ `SKIP` อย่างชัดเจนแทนการล้ม

## Consequences

- `task test` รันได้ใน clone เปล่า แต่ผลเป็น partial เมื่อไม่มี sibling (README)
- Blame/history ของไฟล์ก่อน split ต้องไปดูที่ hub
- ของที่ไม่ได้ย้ายตามมาด้วย เช่น `infra/go/task-sync` และ task `quality:core` / `test:strict` ที่ README อ้างถึง ไม่มีใน repo (ดู [03](../03-user-flows.md))

## Evidence

`README.md` (บทนำ, §Sibling repositories), `infra/README.md` §Removed or moved, `infra/scripts/utils/_env.py`, `infra/mcp-modules.ts`, `infra/tests/support/siblings.py`, `git log` `2a88aca`
