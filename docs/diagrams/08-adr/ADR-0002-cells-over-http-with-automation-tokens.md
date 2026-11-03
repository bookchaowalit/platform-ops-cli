# ADR-0002 — อ่าน/เขียนข้อมูลผ่าน cell APIs ด้วย automation token ต่อหน้าที่ (SQLite retired)

- **Status:** accepted (retro-documented)
- **วันที่ตัดสินใจ:** SQLite task backend retired 2026-10-09 (docstring `task_store.py`); ledger ย้ายไป CRM 2026-10-11 (docstring `crm_ledger.py`)

## Context

ก่อนหน้านี้ scripts เขียน SQLite control plane (`operations.db`) โดยตรง ข้อมูล tasks, goals, projects, habits, decisions, notifications ถูกย้ายไปเป็น cell services ใน empire-platform

## Decision

- Writers ทั้งหมดเรียกผ่าน store module เดียวต่อ cell: `task_store.py` (Tasks API), `cell_store.py` (generic `/v1/<plural>`), `habits_store.py`, `decision_store.py`, `notifications_store.py`, `crm_ledger.py`
- URL มาจาก env หรือไฟล์ owner-only `~/.config/empire-platform/client.env` (ปฏิเสธไฟล์ที่ group/world อ่านได้); `SOLO_EMPIRE_TASKS_BACKEND=api` บังคับ, `sqlite` เป็น retirement error
- แต่ละ automation ใช้ token ของตัวเอง (`EMPIRE_AUTOMATION_TOKEN_<NAME>` หรือ `.runtime/automation-tokens/<name>.jwt`) พร้อม correlation ID ต่อรอบ
- https เท่านั้น (http เฉพาะ loopback), ไม่ follow redirect
- SQLite เหลือเฉพาะ test doubles ใน `infra/tests/support/`; `DB_PATH` (`data/runtime/operations.db`) เป็นค่า nominal ไม่มีไฟล์จริง

## Consequences

- Tests offline ได้ด้วย `EMPIRE_CLIENT_CONFIG=/nonexistent` + SQLite doubles
- ทุก workflow ต้องมี token ที่ถูก provision; ตัวที่ยังไม่มี (`reporting-reader` สำหรับ diary note) ทำให้ step ล้ม
- Schema จริงเป็นของ empire-platform ไม่ใช่ repo นี้ (ดู [09](../09-erd.md))

## Evidence

`infra/scripts/utils/task_store.py`, `cell_store.py`, `crm_ledger.py`, `tasks_backend.py`, `_env.py` (`resolve_db_path`), `infra/tests/support/sqlite_*.py`, `infra/tests/tasks/client_config_test.py`, `PRODUCT.md` §Capabilities
