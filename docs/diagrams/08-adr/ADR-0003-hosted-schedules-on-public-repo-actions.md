# ADR-0003 — งาน schedule ของ ops รันบน GitHub-hosted Actions ของ repo public นี้ (env secret เฉพาะ `main`)

- **Status:** accepted (retro-documented)
- **วันที่ตัดสินใจ:** 2026-10-28 (commit `249d3d4` org hub sync), 2026-11-03 (commit `7eed15f` CI runner failover)

## Context

นโยบายเจ้าของ: งาน schedule ห้ามพึ่งเครื่อง laptop (ops record: org-hub-sync-hosted, ci-hosting-policy) repo นี้ public จึงใช้ GitHub-hosted runner ได้โดยไม่เสีย minutes แต่ log ก็ public ด้วย

## Decision

- `org-hub-sync.yml` (09:00 BKK) และ `ci-runner-failover.yml` (`*/10 * * * *`) รันบน `ubuntu-24.04`
- **อัปเดต 2026-11-03 (commit `5fdb2d9`):** `org-hub-sync.yml` เลิกใช้ `schedule` ของ GitHub แล้ว เหลือ `workflow_dispatch` ที่ถูกเรียกโดย job `org-hub-sync` ของ platform-jobs-worker (cron `0 2 * * *` UTC, ADR-0008 messaging layer) เหตุผลตาม commit: GitHub cron เริ่มช้าและไม่ retry ส่วน jobs queue retry แล้ว park ใน `jobs-dlq` ที่ uptime Worker แจ้งเตือน `ci-runner-failover.yml` ยังใช้ `schedule`
- ทั้งสอง job มี guard `github.repository == 'bookchaowalit/platform-ops-cli' && github.ref == 'refs/heads/main'` และใช้ GitHub environment (`org-hub-sync`, `ci-runner-failover`) ที่อนุญาตเฉพาะ `main`
- `permissions: contents: read`, checkout `persist-credentials: false`, `concurrency` กันรันซ้อน
- Log พิมพ์เฉพาะสถานะต่อ target; รายงานเต็มไม่ถูก upload
- Local timer `bookchaowalit-org-hub-sync.timer` ถูกปิด (installer ยังอยู่)

## Consequences

- GitHub schedule อาจเลื่อนหรือข้ามรอบ (เหตุผลที่ย้าย org hub sync ไป jobs queue): `ci-runner-failover.yml` มี scheduled run เพียง 1 ครั้ง (2026-11-03 12:07 UTC) นับจากเพิ่ม workflow ตาม `gh run list` (ดู [20](../20-observability.md))
- GitHub ปิด schedule เมื่อ repo ไม่มี commit 60 วัน
- `ORG_HUB_SYNC_TOKEN` เป็น classic PAT สิทธิ์กว้าง; การลดสิทธิ์เหลือ `repo` + `read:org` ยังเป็นงานของเจ้าของ
- ขัดกับ `PRODUCT.md` ที่เขียนว่า hosted schedulers อยู่นอก scope

## Evidence

`.github/workflows/org-hub-sync.yml`, `.github/workflows/ci-runner-failover.yml`, `docs/operations/org-hub-registry-sync.md` §ตัวตั้งเวลาแบบ hosted, `git log` `249d3d4` `7eed15f` `5fdb2d9`, `gh run list`, ops record: org-hub-sync-hosted (2026-11-03)
