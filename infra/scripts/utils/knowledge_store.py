"""Client for the hosted knowledge document store (platform-knowledge-api SPEC-002).

The diary and other tools keep their documents there instead of in a Git checkout.
Configuration is not secret and comes from the environment or the owner-only
``~/.config/empire-platform/client.env``::

    EMPIRE_KNOWLEDGE_API=https://...      the knowledge API
    SOLO_WEB_PUBLIC_ORIGIN=https://...    the JWT issuer (Solo Web's public origin)
    SOLO_DIARY_STORE=off                  optional: keep the diary local only

Each call mints a 60-second RS256 token with the owner key files that the knowledge
MCP uses (``~/.config/solo-web/service-private.pem``, ``owner-subjects.txt``). Tokens,
bodies and error text are never printed; failures carry only a status code.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

AUDIENCE = "knowledge-api"
SCOPES = {"read": "knowledge:read", "write": "knowledge:write"}
HOME_CONFIG = Path.home() / ".config" / "solo-web"


class StoreUnavailable(RuntimeError):
    """The store could not be used; the message is a short status, never upstream text."""


class StoreConflict(RuntimeError):
    """The document changed since the version that was read (HTTP 409)."""


def _client_file(environ: Any) -> dict[str, str]:
    override = (environ.get("EMPIRE_CLIENT_CONFIG") or "").strip()
    path = Path(override).expanduser() if override else Path.home() / ".config" / "empire-platform" / "client.env"
    try:
        if path.stat().st_mode & 0o077:
            raise StoreUnavailable("client.env must be chmod 600")
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("'\"")
    return values


def _https_origin(value: str, name: str) -> str:
    parts = urllib.parse.urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise StoreUnavailable(f"{name} must be an https URL without credentials")
    return value.rstrip("/")


def settings(environ: Any = None) -> tuple[str, str] | None:
    """(api_url, issuer) when the store is configured and enabled, else None."""
    environ = os.environ if environ is None else environ
    file_values = _client_file(environ)
    switch = (environ.get("SOLO_DIARY_STORE") or file_values.get("SOLO_DIARY_STORE") or "").strip().lower()
    if switch in {"off", "0", "false"}:
        return None
    api = (environ.get("EMPIRE_KNOWLEDGE_API") or "").strip() or file_values.get("EMPIRE_KNOWLEDGE_API", "")
    issuer = (environ.get("SOLO_WEB_PUBLIC_ORIGIN") or "").strip() or file_values.get("SOLO_WEB_PUBLIC_ORIGIN", "")
    if not api or not issuer:
        return None
    return _https_origin(api, "EMPIRE_KNOWLEDGE_API"), _https_origin(issuer, "SOLO_WEB_PUBLIC_ORIGIN")


def _owner_file(path: Path) -> str:
    if path.stat().st_mode & 0o077:
        raise StoreUnavailable("owner key files must be chmod 600")
    return path.read_text(encoding="utf-8").strip()


def mint(issuer: str, scope: str, environ: Any = None) -> str:
    try:
        import jwt  # PyJWT + cryptography (infra/requirements-dev.txt), as the API verifies
    except ImportError:
        raise StoreUnavailable("PyJWT[crypto] is not installed") from None

    environ = os.environ if environ is None else environ
    try:
        key = _owner_file(Path(environ.get("SOLO_WEB_SERVICE_KEY_FILE") or HOME_CONFIG / "service-private.pem").expanduser())
        subjects = [
            line.strip()
            for line in _owner_file(
                Path(environ.get("SOLO_WEB_OWNER_SUBJECTS_FILE") or HOME_CONFIG / "owner-subjects.txt").expanduser()
            ).splitlines()
            if line.strip()
        ]
    except OSError:
        raise StoreUnavailable("owner key files are missing") from None
    if not subjects:
        raise StoreUnavailable("owner subjects file is empty")
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": AUDIENCE,
        "sub": subjects[0],
        "tenant_id": "owner",
        "scope": scope,
        "iat": now,
        "nbf": now,
        "exp": now + 60,
        "jti": str(uuid.uuid4()),
    }
    try:
        return jwt.encode(claims, key, algorithm="RS256")
    except Exception:  # noqa: BLE001 - a bad key must not surface its text
        raise StoreUnavailable("owner key is not usable") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # the token stays on the API host
        return None


class KnowledgeStore:
    """One space of the store: get and put documents by path."""

    def __init__(self, api: str, issuer: str, *, space: str = "knowledge-base", timeout: float = 30) -> None:
        self.api, self.issuer, self.space, self.timeout = api, issuer, space, timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _call(self, method: str, path: str, *, scope: str, body: dict[str, Any] | None = None) -> dict[str, Any] | None:
        query = urllib.parse.urlencode({"space": self.space, "path": path})
        request = urllib.request.Request(
            f"{self.api}/api/v1/store/document?{query}",
            method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={
                "Authorization": f"Bearer {mint(self.issuer, SCOPES[scope])}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                result: dict[str, Any] = json.loads(response.read())
                return result
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == "GET":
                return None
            if exc.code == 409:
                raise StoreConflict() from None
            raise StoreUnavailable(str(exc.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise StoreUnavailable("unreachable") from None

    def get(self, path: str) -> tuple[bytes, int] | None:
        result = self._call("GET", path, scope="read")
        if result is None:
            return None
        document = result["document"]
        content = (
            str(document["content"]).encode("utf-8")
            if document["encoding"] == "utf-8"
            else base64.b64decode(document["content_base64"])
        )
        return content, int(document["version"])

    def put(self, path: str, content: bytes, *, if_version: int | None) -> int:
        body: dict[str, Any] = {"content_base64": base64.b64encode(content).decode("ascii")}
        if if_version is not None:
            body["if_version"] = if_version
        result = self._call("PUT", path, scope="write", body=body)
        assert result is not None
        return int(result["document"]["version"])


def open_store(environ: Any = None) -> KnowledgeStore | None:
    configured = settings(environ)
    return KnowledgeStore(*configured) if configured else None
