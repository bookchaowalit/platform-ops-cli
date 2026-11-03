# ADR-0006 — Offline test suite รันต่อไฟล์ + shared CI/security workflows ที่ pin SHA

- **Status:** accepted (retro-documented)
- **วันที่ตัดสินใจ:** 2026-10-17 (PR #2, commit `3512069` "adopt the shared CI and security workflows (DevSecOps Phase 1)")

## Context

Tests เป็น assertion scripts แยกไฟล์ (ไม่ใช่ pytest collection เดียว) และต้องไม่แตะ production API repo public จึงใช้ GitHub-hosted runner ได้

## Decision

- `infra/tests/run_all.py` รันทุก `*_test.py` ใน interpreter แยก พร้อม env ที่ปิด Tasks API และ knowledge store (`EMPIRE_CLIENT_CONFIG=/nonexistent`, `SOLO_DIARY_STORE=off`)
- `ci.yml` เรียก reusable workflows จาก `bookchaowalit/platform-ci-workflows` pin ที่ `87dc5e1c…` (v1.1.1): `ci-python` (working dir `infra`, Python 3.12, `format-check: false`, test-command ติดตั้ง Task 3.53.1 ตรวจ SHA-256 แล้วรัน `run_all.py --jobs 4`), `ci-node` (Node 22, ไม่มี test), `security` (Gitleaks, Semgrep, OSV-Scanner, Trivy fs)
- Actions ทุกตัว pin ด้วย commit SHA; Renovate (`renovate.json`) ดูแลการอัปเดต

## Consequences

- Test ที่ต้อง sibling checkout จะ skip บน CI (ไม่มี sibling) ผล CI จึงยืนยันได้เฉพาะส่วนที่ไม่ต้องใช้ sibling
- Ruff format ปิดไว้จนกว่าจะมี PR reformat แยก (คอมเมนต์ใน `ci.yml`)
- README ยังเขียนว่า "No CI workflow is configured" ซึ่งไม่ตรงกับ `ci.yml`

## Evidence

`.github/workflows/ci.yml`, `.task-version`, `infra/tests/run_all.py`, `infra/ruff.toml`, `.trivyignore.yaml`, `renovate.json`, platform-ci-workflows `ci-python.yml` / `ci-node.yml` / `security.yml` @ `87dc5e1c`, `gh pr list` (#2)
