#!/usr/bin/env python3
"""Client of the CRM ledger (revenue, invoices, clients) on the owner's income-pipeline CRM.

Revenue, invoices and clients left the control plane on 2026-10-11 for the CRM
(bookchaowalit/sales-crm, instance income-pipeline). Readers and the ledger CLI
use this client instead of SQL:

    ledger = open_crm_ledger()
    rows, totals = ledger.revenue(since="2026-10-01")       # totals: {"THB": 1500.0}
    ledger.add_revenue({...})                               # idempotent on idempotency_key

The URL comes from ``EMPIRE_CRM_LEDGER_API`` (environment or the owner-only
client file ``~/.config/empire-platform/client.env``); the bearer token is the
CRM's owner token, from ``EMPIRE_CRM_LEDGER_TOKEN`` or the owner-only file
``~/.config/solo-crm/income.token``. https only (http on loopback); redirects
are never followed; the token is never printed.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from task_store import ClientConfigError, _NoRedirect, automation_token, client_config_path, read_client_file, validate_api_url

URL_KEY = "EMPIRE_CRM_LEDGER_API"
TOKEN_KEY = "EMPIRE_CRM_LEDGER_TOKEN"
TOKEN_FILE = Path.home() / ".config" / "solo-crm" / "income.token"
MAX_LIMIT = 500
# Behind the edge gateway only RS256 platform tokens pass, so the static CRM token cannot be used
# there; the owner-cli automation token (audience crm-income-api, allowed by crm-api) is used instead.
GATEWAY_HOST = "gateway.bookchaowalit.com"
TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9._~+/=-]{16,8192}$")  # the ledger API's page cap; totals always cover every matching row


class LedgerError(RuntimeError):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(f"CRM ledger {status}: {reason}")
        self.status = status
        self.reason = reason


class CrmLedger:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    def __repr__(self) -> str:
        return f"CrmLedger({self.base_url!r})"

    def _call(self, method: str, path: str, body: Any = None, params: dict | None = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        query = f"?{urllib.parse.urlencode(clean)}" if clean else ""
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/crm/ledger{path}{query}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            reason = "error"
            try:  # the body may be cut short, not JSON, or carry no error object
                detail = json.loads(error.read()).get("error")
                if isinstance(detail, dict):
                    reason = str(detail.get("code") or detail.get("message") or reason)
            except Exception:  # noqa: BLE001 - any failure here keeps the generic reason
                pass
            raise LedgerError(error.code, reason) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as error:
            # Never chain or echo the error: a malformed header error can carry the Authorization value.
            raise LedgerError(503, f"unavailable:{type(error).__name__}") from None

    def revenue(self, *, since: str | None = None, until: str | None = None, source_type: str | None = None,
                client_id: int | None = None, received: bool | None = None, limit: int = 500
                ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        """(rows newest first, totals per currency) for the filters."""
        out = self._call("GET", "/revenue", params={
            "since": since, "until": until, "source_type": source_type, "client_id": client_id, "limit": limit,
            "received": None if received is None else str(bool(received)).lower()} | {"limit": min(limit, MAX_LIMIT)})
        return out["items"], out["totals"]

    def add_revenue(self, fields: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        out = self._call("POST", "/revenue", fields)
        return out["outcome"], out["revenue"]

    def revenue_all(self, *, since: str | None = None, until: str | None = None, source_type: str | None = None,
                    client_id: int | None = None, received: bool | None = None
                    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        """Every matching row (all pages, newest first) and the totals per currency."""
        rows: list[dict[str, Any]] = []
        totals: dict[str, float] = {}
        while True:
            out = self._call("GET", "/revenue", params={
                "since": since, "until": until, "source_type": source_type, "client_id": client_id,
                "received": None if received is None else str(bool(received)).lower(),
                "limit": MAX_LIMIT, "offset": len(rows)})
            rows.extend(out["items"])
            totals = out["totals"]
            if len(out["items"]) < MAX_LIMIT:
                return rows, totals

    def update_revenue(self, record_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return self._call("PATCH", f"/revenue/{int(record_id)}", fields)["revenue"]

    def invoices(self, *, status: str | None = None, client_id: int | None = None, limit: int = 500
                 ) -> list[dict[str, Any]]:
        return self._call("GET", "/invoices", params={"status": status, "client_id": client_id,
                                                       "limit": min(limit, MAX_LIMIT)})["items"]

    def add_invoice(self, fields: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        out = self._call("POST", "/invoices", fields)
        return out["outcome"], out["invoice"]

    def update_invoice(self, record_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return self._call("PATCH", f"/invoices/{int(record_id)}", fields)["invoice"]

    def clients(self, *, status: str | None = None, q: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        return self._call("GET", "/clients", params={"status": status, "q": q, "limit": min(limit, MAX_LIMIT)})["items"]

    def add_client(self, fields: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        out = self._call("POST", "/clients", fields)
        return out["outcome"], out["client"]


def ledger_url(environ: Any = None) -> str:
    environ = os.environ if environ is None else environ
    value = (environ.get(URL_KEY) or "").strip() or read_client_file(client_config_path(environ)).get(URL_KEY, "")
    if not value:
        raise ClientConfigError(f"the CRM ledger is not configured: set {URL_KEY} (env or client.env)")
    return validate_api_url(value)


def ledger_token(environ: Any = None, url: str | None = None) -> str:
    environ = os.environ if environ is None else environ
    if url and urllib.parse.urlsplit(url).hostname == GATEWAY_HOST:
        try:
            token = automation_token("owner-cli")
        except RuntimeError as exc:
            raise ClientConfigError(f"the CRM ledger goes through the gateway: {exc}") from None
    elif environ.get(TOKEN_KEY):
        token = environ[TOKEN_KEY].strip()
    elif TOKEN_FILE.is_file():
        if TOKEN_FILE.stat().st_mode & 0o077:
            raise ClientConfigError(f"{TOKEN_FILE} must be readable by its owner only (chmod 600)")
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    else:
        raise ClientConfigError(f"no CRM ledger token: set {TOKEN_KEY} or create {TOKEN_FILE}")
    if not TOKEN_SHAPE.fullmatch(token):
        raise ClientConfigError("the CRM ledger token has an unexpected format")  # never echo it
    return token


def open_crm_ledger() -> CrmLedger:
    url = ledger_url()
    return CrmLedger(url, ledger_token(url=url))


def total_thb(totals: dict[str, float]) -> float:
    """The THB total of a totals map (other currencies are reported separately, never converted here)."""
    return float(totals.get("THB", 0.0))
