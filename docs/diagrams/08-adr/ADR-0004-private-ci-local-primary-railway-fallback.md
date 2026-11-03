# ADR-0004 — Private-repo CI: local Docker runner เป็นหลัก, Railway runner-multi เป็น fallback อัตโนมัติ

- **Status:** accepted (retro-documented)
- **วันที่ตัดสินใจ:** 2026-10-16 Railway runner (commit `152d5cc`), 2026-10-23 รวมเป็น `runner-multi`, 2026-11-03 local primary (commit `7eed15f`), 32 slots (commit `24dffcf`)

## Context

Private repos ห้ามใช้ GitHub-hosted minutes (central [ADR-0001 private CI Railway runner](https://github.com/bookchaowalit/architecture-docs/blob/main/docs/architecture/decisions/ADR-0001-private-ci-railway-runner.md)) Railway `runner-multi` ราว $14/เดือน ส่วนใหญ่เป็น memory ตอน idle เจ้าของตัดสินใจ 2026-11-03 ให้เครื่องตัวเองเป็นหลัก

## Decision

- Image เดียว (`infra/ci-runner/Dockerfile`, base `myoung34/github-runner:ubuntu-noble` + PostgreSQL, `ARG RUNNER_SLOTS=32`) ใช้ทั้งสอง host
- Local: `infra/ci-runner/local/run.sh up` รัน container `ci-runner-local` (runners `local-<name>`, labels `self-hosted,linux,x64,railway,local`, 8 GB / 6 CPU, ไม่มี Docker socket / home mount)
- Railway: service `runner-multi` ใน project `ci-runners` เก็บ registration `railway-<name>` บน volume `/runner-config`
- Watchdog `ci_runner_failover.py`: local offline เกิน grace 180 s และ Railway stopped → `deploymentRestart` + Telegram; local online และ Railway running และไม่มี job → `deploymentStop`
- `local/runners.txt` กับ Railway `RUNNERS` ต้องลำดับตรงกัน (slot = ตำแหน่ง)

## Consequences

- Watchdog no-op (warning) จนกว่าเจ้าของตั้ง `RAILWAY_TOKEN` (project token สร้างได้จาก dashboard เท่านั้น)
- ขณะทั้งสอง host online ทั้งคู่รับ job (GitHub ไม่มี runner priority)
- Trust model: runner ใน container เดียวกันอ่าน registration กันได้ ยอมรับได้เพราะทุก repo มีเจ้าของเป็น collaborator เดียว; repo ลูกค้าห้ามเข้าร่วม
- Central ADR-0001 ระบุ "never the local machine" ซึ่งถูกเปลี่ยนโดยการตัดสินใจ 2026-11-03 แต่ central ADR ยังไม่ถูก supersede

## Evidence

`infra/ci-runner/README.md`, `infra/ci-runner/Dockerfile`, `infra/ci-runner/multi-runner.sh`, `infra/ci-runner/job-cleanup.sh`, `infra/ci-runner/local/run.sh`, `infra/ci-runner/local/runners.txt`, `infra/scripts/platform/ci_runner_failover.py`, `infra/tests/platform/ci_runner_failover_test.py`, `.github/workflows/ci-runner-failover.yml`, ops record: ci-hosting-policy (2026-11-03)
