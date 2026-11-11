#!/usr/bin/env python3
"""CRM ledger token: the owner-cli automation token behind the edge gateway, else the CRM owner token."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra" / "scripts" / "utils"))

import crm_ledger  # noqa: E402
from task_store import ClientConfigError  # noqa: E402

JWT = "eyJhbGciOiJSUzI1NiJ9." + "a" * 900 + "." + "b" * 300  # longer than the old 512-character cap
GATEWAY = "https://gateway.bookchaowalit.com/v1/crm-income"
STATIC = {"EMPIRE_CRM_LEDGER_TOKEN": "static-crm-owner-token-value"}


def main() -> int:
    original = crm_ledger.automation_token
    try:
        crm_ledger.automation_token = lambda name: JWT if name == "owner-cli" else ""
        assert crm_ledger.ledger_token(STATIC, url=GATEWAY) == JWT
        direct = "https://crm-api-production-19fd.up.railway.app"
        assert crm_ledger.ledger_token(STATIC, url=direct) == STATIC["EMPIRE_CRM_LEDGER_TOKEN"]

        def missing(name):
            raise RuntimeError("no token for automation 'owner-cli'")

        crm_ledger.automation_token = missing
        try:
            crm_ledger.ledger_token({}, url=GATEWAY)
            raise AssertionError("a gateway URL without an owner-cli token must fail")
        except ClientConfigError:
            pass

        crm_ledger.automation_token = lambda name: "bad token with spaces"
        try:
            crm_ledger.ledger_token({}, url=GATEWAY)
            raise AssertionError("a malformed token must be refused")
        except ClientConfigError as exc:
            assert "bad token" not in str(exc)
    finally:
        crm_ledger.automation_token = original
    print("✅ crm ledger: owner-cli token behind the gateway, CRM token direct; malformed tokens never echoed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
