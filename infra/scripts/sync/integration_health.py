#!/usr/bin/env python3
"""
Integration Health Check — Test all external API connections and report status.

Checks:
  - Credential availability (env vars)
  - API connectivity (lightweight requests)
  - Data freshness (last update timestamps from the skills and habits cells)
  - Overall integration health summary

Usage:
    python3 infra/scripts/sync/integration_health.py            # Full health check
    python3 infra/scripts/sync/integration_health.py --json     # JSON output
    python3 infra/scripts/sync/integration_health.py --quick    # Quick status only
    python3 infra/scripts/sync/integration_health.py --offline  # Credentials only; no network
"""

import base64
import os
import re
# Add scripts directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'utils'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'platform'))
from paas_health import check_paas_provider
import json
import sys
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from _env import (
    get_cloudflare_token,
    get_env,
    get_google_creds,
    get_strava_token,
    get_binance_th_creds,
)
from _http import get_access_token


def _safe_error(exc):
    """Return a non-sensitive error class for health output."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    return type(exc).__name__


def _get_json(url, headers=None):
    """Make a bounded read-only JSON request without returning its body."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read())


def check_google_calendar():
    """Check Google Calendar API connectivity."""
    result = {"name": "Google Calendar", "icon": "📅", "status": "unknown"}
    try:
        client_id, client_secret, refresh_token = get_google_creds()
    except ValueError:
        result["status"] = "not_configured"
        result["error"] = "Missing GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN"
        return result

    try:
        data = urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }).encode()
        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
        token = resp.get("access_token")
        if token:
            _get_json(
                "https://www.googleapis.com/calendar/v3/calendars/primary?fields=id",
                {"Authorization": f"Bearer {token}"},
            )
            result["status"] = "healthy"
            result["detail"] = "Calendar API reachable"
        else:
            result["status"] = "error"
            result["error"] = "No access token returned"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_gmail():
    """Check Gmail API (uses same OAuth as Calendar)."""
    result = {"name": "Gmail", "icon": "📧", "status": "unknown"}
    try:
        client_id, client_secret, refresh_token = get_google_creds()
        token = get_access_token(client_id, client_secret, refresh_token)
        _get_json(
            "https://gmail.googleapis.com/gmail/v1/users/me/labels?fields=labels(id)",
            {"Authorization": f"Bearer {token}"},
        )
        result["status"] = "healthy"
        result["detail"] = "Gmail API reachable"
    except ValueError:
        result["status"] = "not_configured"
        result["error"] = "Missing Google OAuth credentials"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_google_drive():
    """Check Google Drive API read access using the shared OAuth grant."""
    result = {"name": "Google Drive", "icon": "📁", "status": "unknown"}
    try:
        client_id, client_secret, refresh_token = get_google_creds()
        token = get_access_token(client_id, client_secret, refresh_token)
        _get_json(
            "https://www.googleapis.com/drive/v3/files?pageSize=1&fields=files(id)",
            {"Authorization": f"Bearer {token}"},
        )
        result["status"] = "healthy"
        result["detail"] = "Drive API reachable"
    except ValueError:
        result["status"] = "not_configured"
        result["error"] = "Missing Google OAuth credentials"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_github():
    """Check GitHub API identity access without reading repository contents."""
    result = {"name": "GitHub", "icon": "🐙", "status": "unknown"}
    token = get_env("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not token:
        result["status"] = "not_configured"
        result["error"] = "Missing GITHUB_PERSONAL_ACCESS_TOKEN"
        return result
    try:
        _get_json(
            "https://api.github.com/user",
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        result["status"] = "healthy"
        result["detail"] = "GitHub API reachable"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_cloudflare():
    """Check Cloudflare API token validity without changing zones or records."""
    result = {"name": "Cloudflare", "icon": "☁️", "status": "unknown"}
    token = get_cloudflare_token()
    if not token:
        result["status"] = "not_configured"
        result["error"] = "Missing CLOUDFLARE_API_TOKEN"
        return result
    try:
        _get_json(
            "https://api.cloudflare.com/client/v4/user/tokens/verify",
            {"Authorization": f"Bearer {token}"},
        )
        result["status"] = "healthy"
        result["detail"] = "Cloudflare token verified"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_vercel():
    """Check Vercel project-list access without deployment or mutation."""
    result = {"name": "Vercel", "icon": "▲", "status": "unknown"}
    token = get_env("VERCEL_TOKEN")
    if not token:
        result["status"] = "not_configured"
        result["error"] = "Missing VERCEL_TOKEN"
        return result
    try:
        _get_json(
            "https://api.vercel.com/v9/projects?limit=1",
            {"Authorization": f"Bearer {token}"},
        )
        result["status"] = "healthy"
        result["detail"] = "Vercel API reachable"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_render():
    """Check Render service API access without changing services."""
    return check_paas_provider("render", live=True)


def check_railway():
    """Check Railway GraphQL account access without mutating resources."""
    return check_paas_provider("railway", live=True)


def check_fly_io():
    """Check Fly.io Machines API access without changing apps or machines."""
    return check_paas_provider("fly-io", live=True)


def check_heroku():
    """Check Heroku Platform API account access without changing apps."""
    return check_paas_provider("heroku", live=True)


def check_telegram():
    """Check the alert bot (book_noti_bot) with Telegram getMe (read-only)."""
    result = {"name": "Telegram", "icon": "✈️", "status": "unknown"}
    token = get_env("TELEGRAM_NOTI_BOT_TOKEN") or get_env("TELEGRAM_BOT_TOKEN")
    if not token:
        result["status"] = "not_configured"
        result["error"] = "Missing Telegram bot token"
        return result
    try:
        _get_json(f"https://api.telegram.org/bot{token}/getMe")
        result["status"] = "healthy"
        result["detail"] = "Telegram Bot API reachable"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_line():
    """Check LINE Official Account API access without sending a message."""
    result = {"name": "LINE Official Account", "icon": "💬", "status": "unknown"}
    token = get_env("LINE_SOLO_EMPIRE_CHANNEL_ACCESS_TOKEN")
    webhook_secret = get_env("LINE_SOLO_EMPIRE_WEBHOOK_SECRET")
    if not token or not webhook_secret:
        result["status"] = "not_configured"
        missing = []
        if not token:
            missing.append("LINE_SOLO_EMPIRE_CHANNEL_ACCESS_TOKEN")
        if not webhook_secret:
            missing.append("LINE_SOLO_EMPIRE_WEBHOOK_SECRET")
        result["error"] = f"Missing {'/'.join(missing)}"
        return result

    try:
        _get_json(
            "https://api.line.me/v2/bot/info",
            {"Authorization": f"Bearer {token}"},
        )
        result["status"] = "healthy"
        result["detail"] = "LINE Bot API reachable; webhook secret configured"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_stripe():
    """Check Stripe account access without creating or changing a payment."""
    result = {"name": "Stripe", "icon": "💳", "status": "unknown"}
    secret_key = get_env("STRIPE_SECRET_KEY")
    webhook_secret = get_env("STRIPE_WEBHOOK_SECRET")
    if not secret_key or not webhook_secret:
        result["status"] = "not_configured"
        missing = []
        if not secret_key:
            missing.append("STRIPE_SECRET_KEY")
        if not webhook_secret:
            missing.append("STRIPE_WEBHOOK_SECRET")
        result["error"] = f"Missing {'/'.join(missing)}"
        return result

    try:
        basic = base64.b64encode(f"{secret_key}:".encode("utf-8")).decode("ascii")
        _get_json(
            "https://api.stripe.com/v1/balance",
            {"Authorization": f"Basic {basic}"},
        )
        result["status"] = "healthy"
        result["detail"] = "Stripe Balance API reachable; webhook secret configured"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def _sentry_base_url():
    """Return a validated Sentry base URL without embedding credentials."""
    base_url = get_env("SENTRY_BASE_URL") or "https://sentry.io"
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("SENTRY_BASE_URL must be an http(s) URL without credentials")
    return base_url.rstrip("/")


def check_sentry():
    """Check Sentry organization API access without reading event payloads."""
    result = {"name": "Sentry", "icon": "🛡️", "status": "unknown"}
    token = get_env("SENTRY_AUTH_TOKEN")
    org_slug = get_env("SENTRY_ORG_SLUG")
    if not token or not org_slug:
        result["status"] = "not_configured"
        missing = []
        if not token:
            missing.append("SENTRY_AUTH_TOKEN")
        if not org_slug:
            missing.append("SENTRY_ORG_SLUG")
        result["error"] = f"Missing {'/'.join(missing)}"
        return result

    try:
        org = urllib.parse.quote(str(org_slug).strip(), safe="")
        _get_json(
            f"{_sentry_base_url()}/api/0/organizations/{org}/",
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        result["status"] = "healthy"
        result["detail"] = "Sentry organization API reachable"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def _posthog_host():
    """Return a validated PostHog private API host."""
    host = get_env("POSTHOG_HOST") or "https://us.posthog.com"
    parsed = urllib.parse.urlparse(host)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("POSTHOG_HOST must be an http(s) URL without credentials")
    return host.rstrip("/")


def check_posthog():
    """Check PostHog saved-insight read access without capturing or mutating data."""
    result = {"name": "PostHog", "icon": "📊", "status": "unknown"}
    try:
        api_key = get_env("POSTHOG_API_KEY")
        project_id = get_env("POSTHOG_PROJECT_ID")
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
        return result
    if not api_key or not project_id:
        result["status"] = "not_configured"
        missing = []
        if not api_key:
            missing.append("POSTHOG_API_KEY")
        if not project_id:
            missing.append("POSTHOG_PROJECT_ID")
        result["error"] = f"Missing {'/'.join(missing)}"
        return result

    try:
        project = urllib.parse.quote(str(project_id).strip(), safe="")
        query = urllib.parse.urlencode({"format": "json", "limit": "1"})
        _get_json(
            f"{_posthog_host()}/api/projects/{project}/insights/?{query}",
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
        result["status"] = "healthy"
        result["detail"] = "PostHog saved-insight API reachable"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_slack():
    """Check Slack authentication without reading or sending messages."""
    result = {"name": "Slack", "icon": "💬", "status": "unknown"}
    try:
        token = get_env("SLACK_BOT_TOKEN")
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
        return result
    if not token:
        result["status"] = "not_configured"
        result["error"] = "Missing SLACK_BOT_TOKEN"
        return result

    try:
        request = urllib.request.Request(
            "https://slack.com/api/auth.test",
            data=b"",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        payload = json.loads(urllib.request.urlopen(request, timeout=15).read().decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            error = payload.get("error") if isinstance(payload, dict) else None
            result["status"] = "error"
            result["error_class"] = f"slack_{error}" if isinstance(error, str) and error else "slack_api_error"
            return result
        result["status"] = "healthy"
        result["detail"] = "Slack auth.test reachable"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_notion():
    """Check Notion search authentication without reading page content."""
    result = {"name": "Notion", "icon": "⬛", "status": "unknown"}
    try:
        token = get_env("NOTION_BOT_TOKEN")
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
        return result
    if not token:
        result["status"] = "not_configured"
        result["error"] = "Missing NOTION_BOT_TOKEN"
        return result

    try:
        request = urllib.request.Request(
            "https://api.notion.com/v1/search",
            data=json.dumps({"page_size": 1}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Notion-Version": os.environ.get("NOTION_VERSION", "2026-03-11"),
            },
            method="POST",
        )
        payload = json.loads(urllib.request.urlopen(request, timeout=15).read().decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("object") == "error":
            result["status"] = "error"
            result["error_class"] = "notion_api_error"
            return result
        result["status"] = "healthy"
        result["detail"] = "Notion search API reachable"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_asana():
    """Check Asana workspace metadata access without reading or mutating tasks."""
    result = {"name": "Asana", "icon": "🟣", "status": "unknown"}
    try:
        token = get_env("ASANA_PERSONAL_ACCESS_TOKEN")
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
        return result
    if not token:
        result["status"] = "not_configured"
        result["error"] = "Missing ASANA_PERSONAL_ACCESS_TOKEN"
        return result

    try:
        query = urllib.parse.urlencode({"limit": "1"})
        request = urllib.request.Request(
            f"https://app.asana.com/api/1.0/workspaces?{query}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="GET",
        )
        payload = json.loads(urllib.request.urlopen(request, timeout=15).read().decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            result["status"] = "error"
            result["error_class"] = "asana_api_error"
            return result
        result["status"] = "healthy"
        result["detail"] = "Asana workspace API reachable"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_linear():
    """Check Linear GraphQL authentication without reading issue content."""
    result = {"name": "Linear", "icon": "📐", "status": "unknown"}
    try:
        token = get_env("LINEAR_API_KEY")
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
        return result
    if not token:
        result["status"] = "not_configured"
        result["error"] = "Missing LINEAR_API_KEY"
        return result

    try:
        request = urllib.request.Request(
            "https://api.linear.app/graphql",
            data=json.dumps({"query": "query Viewer { viewer { id } }"}).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Authorization": token,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        payload = json.loads(urllib.request.urlopen(request, timeout=15).read().decode("utf-8"))
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("data"), dict)
            or not isinstance(payload["data"].get("viewer"), dict)
        ):
            result["status"] = "error"
            result["error_class"] = "linear_graphql_error"
            return result
        result["status"] = "healthy"
        result["detail"] = "Linear GraphQL API reachable"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_discord():
    """Check Discord bot identity without reading guild content."""
    result = {"name": "Discord", "icon": "🎮", "status": "unknown"}
    try:
        token = get_env("DISCORD_BOT_TOKEN")
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
        return result
    if not token:
        result["status"] = "not_configured"
        result["error"] = "Missing DISCORD_BOT_TOKEN"
        return result

    auth_token = str(token).strip()
    if not auth_token.lower().startswith(("bot ", "bearer ")):
        auth_token = f"Bot {auth_token}"
    try:
        request = urllib.request.Request(
            "https://discord.com/api/v10/users/@me",
            headers={
                "Accept": "application/json",
                "Authorization": auth_token,
                "User-Agent": "solo-empire-discord-readonly/1.0",
            },
            method="GET",
        )
        payload = json.loads(urllib.request.urlopen(request, timeout=15).read().decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
            result["status"] = "error"
            result["error_class"] = "discord_api_error"
            return result
        result["status"] = "healthy"
        result["detail"] = "Discord bot identity API reachable"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_whatsapp_business():
    """Check WhatsApp Business Account metadata access without reading messages."""
    result = {"name": "WhatsApp Business", "icon": "🟢", "status": "unknown"}
    try:
        token = get_env("WHATSAPP_CLOUD_API_TOKEN")
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
        return result
    waba_id = os.environ.get("WHATSAPP_BUSINESS_ACCOUNT_ID", "").strip()
    missing = []
    if not token:
        missing.append("WHATSAPP_CLOUD_API_TOKEN")
    if not waba_id:
        missing.append("WHATSAPP_BUSINESS_ACCOUNT_ID")
    if missing:
        result["status"] = "not_configured"
        result["error"] = f"Missing {'/'.join(missing)}"
        return result

    version = os.environ.get("WHATSAPP_GRAPH_API_VERSION", "v25.0").strip()
    if not re.fullmatch(r"v\d+\.\d+", version):
        result["status"] = "error"
        result["error_class"] = "invalid_graph_api_version"
        return result
    try:
        query = urllib.parse.urlencode({"fields": "id"})
        request = urllib.request.Request(
            f"https://graph.facebook.com/{version}/{urllib.parse.quote(waba_id, safe='')}?{query}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "solo-empire-whatsapp-business-readonly/1.0",
            },
            method="GET",
        )
        payload = json.loads(urllib.request.urlopen(request, timeout=15).read().decode("utf-8"))
        if not isinstance(payload, dict) or isinstance(payload.get("error"), dict) or not payload.get("id"):
            result["status"] = "error"
            result["error_class"] = "meta_graph_error"
            return result
        result["status"] = "healthy"
        result["detail"] = "WhatsApp Business Account API reachable"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_microsoft_graph():
    """Check delegated Microsoft Graph /me metadata access only."""
    result = {"name": "Microsoft Graph", "icon": "🔷", "status": "unknown"}
    try:
        token = get_env("MICROSOFT_GRAPH_ACCESS_TOKEN")
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
        return result
    if not token:
        result["status"] = "not_configured"
        result["error"] = "Missing MICROSOFT_GRAPH_ACCESS_TOKEN"
        return result

    try:
        query = urllib.parse.urlencode({"$select": "id"})
        request = urllib.request.Request(
            f"https://graph.microsoft.com/v1.0/me?{query}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "solo-empire-microsoft-graph-readonly/1.0",
            },
            method="GET",
        )
        payload = json.loads(urllib.request.urlopen(request, timeout=15).read().decode("utf-8"))
        if not isinstance(payload, dict) or isinstance(payload.get("error"), dict) or not payload.get("id"):
            result["status"] = "error"
            result["error_class"] = "microsoft_graph_error"
            return result
        result["status"] = "healthy"
        result["detail"] = "Microsoft Graph /me API reachable"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_zoom():
    """Check authenticated Zoom user metadata access without reading meetings."""
    result = {"name": "Zoom", "icon": "📹", "status": "unknown"}
    try:
        token = get_env("ZOOM_ACCESS_TOKEN")
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
        return result
    if not token:
        result["status"] = "not_configured"
        result["error"] = "Missing ZOOM_ACCESS_TOKEN"
        return result

    try:
        auth_token = str(token).strip()
        if auth_token.lower().startswith("bearer "):
            auth_token = auth_token[7:].strip()
        request = urllib.request.Request(
            "https://api.zoom.us/v2/users/me",
            headers={
                "Accept": "application/json",
                "Authorization": auth_token,
                "User-Agent": "solo-empire-zoom-readonly/1.0",
            },
            method="GET",
        )
        payload = json.loads(urllib.request.urlopen(request, timeout=15).read().decode("utf-8"))
        if not isinstance(payload, dict) or isinstance(payload.get("error"), dict) or not payload.get("id"):
            result["status"] = "error"
            result["error_class"] = "zoom_api_error"
            return result
        result["status"] = "healthy"
        result["detail"] = "Zoom /users/me API reachable"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_monday():
    """Check monday.com board metadata access without reading board items."""
    result = {"name": "monday.com", "icon": "🟨", "status": "unknown"}
    try:
        token = get_env("MONDAY_API_TOKEN")
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
        return result
    if not token:
        result["status"] = "not_configured"
        result["error"] = "Missing MONDAY_API_TOKEN"
        return result

    try:
        query = "query { boards(limit: 1) { id } }"
        request_body = json.dumps({"query": query}).encode("utf-8")
        auth_token = str(token).strip()
        if not auth_token.lower().startswith("bearer "):
            auth_token = f"Bearer {auth_token}"
        request = urllib.request.Request(
            "https://api.monday.com/v2",
            data=request_body,
            headers={
                "Accept": "application/json",
                "Authorization": auth_token,
                "Content-Type": "application/json",
                "User-Agent": "solo-empire-monday-readonly/1.0",
            },
            method="POST",
        )
        payload = json.loads(urllib.request.urlopen(request, timeout=15).read().decode("utf-8"))
        boards = payload.get("data", {}).get("boards") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("errors") or not isinstance(boards, list):
            result["status"] = "error"
            result["error_class"] = "monday_graphql_error"
            return result
        result["status"] = "healthy"
        result["detail"] = "monday.com boards API reachable"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def _credential_only(name, icon, getter, missing_message):
    """Report credential availability without making a network request."""
    result = {"name": name, "icon": icon, "status": "unknown"}
    try:
        getter()
        result["status"] = "configured"
        result["detail"] = "Credentials available (offline check)"
    except ValueError:
        result["status"] = "not_configured"
        result["error"] = missing_message
    except Exception as exc:
        result["status"] = "error"
        result["error_class"] = _safe_error(exc)
    return result


def _require_cloudflare_token():
    token = get_cloudflare_token()
    if not token:
        raise ValueError("CLOUDFLARE_API_TOKEN missing")
    return token


def _require_line_credentials():
    """Require both credentials needed for LINE API and webhook ingress."""
    get_env("LINE_SOLO_EMPIRE_CHANNEL_ACCESS_TOKEN", required=True)
    get_env("LINE_SOLO_EMPIRE_WEBHOOK_SECRET", required=True)


def _require_stripe_credentials():
    """Require both credentials needed for Stripe API and webhook ingress."""
    get_env("STRIPE_SECRET_KEY", required=True)
    get_env("STRIPE_WEBHOOK_SECRET", required=True)


def _require_sentry_credentials():
    """Require the Sentry token and organization slug for read-only access."""
    get_env("SENTRY_AUTH_TOKEN", required=True)
    get_env("SENTRY_ORG_SLUG", required=True)


def _require_posthog_credentials():
    """Require the PostHog API key and project ID for read-only analytics."""
    get_env("POSTHOG_API_KEY", required=True)
    get_env("POSTHOG_PROJECT_ID", required=True)


def _require_slack_credentials():
    """Require the Slack bot token for read-only workspace access."""
    get_env("SLACK_BOT_TOKEN", required=True)


def _require_notion_credentials():
    """Require the Notion bot token for read-only search access."""
    get_env("NOTION_BOT_TOKEN", required=True)


def _require_asana_credentials():
    """Require the Asana PAT for read-only workspace/project access."""
    get_env("ASANA_PERSONAL_ACCESS_TOKEN", required=True)


def _require_linear_credentials():
    """Require the Linear personal API key for read-only GraphQL access."""
    get_env("LINEAR_API_KEY", required=True)


def _require_discord_credentials():
    """Require the Discord bot token for read-only guild access."""
    get_env("DISCORD_BOT_TOKEN", required=True)


def _require_whatsapp_business_credentials():
    """Require the WABA token and identifier for read-only metadata access."""
    get_env("WHATSAPP_CLOUD_API_TOKEN", required=True)
    if not os.environ.get("WHATSAPP_BUSINESS_ACCOUNT_ID", "").strip():
        raise ValueError("WHATSAPP_BUSINESS_ACCOUNT_ID missing")


def _require_microsoft_graph_credentials():
    """Require the delegated Microsoft Graph access token."""
    get_env("MICROSOFT_GRAPH_ACCESS_TOKEN", required=True)


def _require_zoom_credentials():
    """Require the Zoom read-only access token."""
    get_env("ZOOM_ACCESS_TOKEN", required=True)


def _require_monday_credentials():
    """Require the monday.com boards read token."""
    get_env("MONDAY_API_TOKEN", required=True)


def check_leantime():
    """Check Leantime MCP endpoint."""
    result = {"name": "Leantime", "icon": "📋", "status": "unknown"}
    mcp_url = "https://mcp.project.cocofriday.com/mcp"
    try:
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": "leantime_list_tickets", "arguments": {"limit": 1}}
        }).encode()
        req = urllib.request.Request(mcp_url, data=payload, headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        })
        resp = urllib.request.urlopen(req, timeout=15)
        body = json.loads(resp.read().decode())
        if "result" in body:
            result["status"] = "healthy"
            result["detail"] = "MCP endpoint responding"
        else:
            result["status"] = "degraded"
            result["detail"] = "MCP responded but unexpected format"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_todoist():
    """Check Todoist API connectivity."""
    result = {"name": "Todoist", "icon": "✅", "status": "unknown"}
    token = get_env("TODOIST_API_TOKEN")
    if not token:
        result["status"] = "not_configured"
        result["error"] = "Missing TODOIST_API_TOKEN"
        return result

    try:
        req = urllib.request.Request(
            "https://api.todoist.com/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"}
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        result["status"] = "healthy"
        result["detail"] = f"{len(data)} projects accessible"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_strava():
    """Check Strava API connectivity."""
    result = {"name": "Strava", "icon": "🏃", "status": "unknown"}
    token = get_strava_token()
    if not token:
        result["status"] = "not_configured"
        result["error"] = "Missing STRAVA_ACCESS_TOKEN"
        return result

    try:
        req = urllib.request.Request(
            "https://www.strava.com/api/v3/athlete",
            headers={"Authorization": f"Bearer {token}"}
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        result["status"] = "healthy"
        result["detail"] = f"Connected as {data.get('firstname', '')} {data.get('lastname', '')}"
    except Exception as e:
        result["status"] = "error"
        result["error_class"] = _safe_error(e)
    return result


def check_binance_th():
    """Check Binance TH API connectivity."""
    result = {"name": "Binance TH", "icon": "📈", "status": "unknown"}
    api_key, api_secret = get_binance_th_creds()
    if not api_key or not api_secret:
        result["status"] = "not_configured"
        result["error"] = "Missing BINANCE_TH_API_KEY/SECRET"
        return result

    try:
        # Public endpoint — no auth needed
        req = urllib.request.Request(
            "https://api.binance.th/api/v3/ping"
        )
        urllib.request.urlopen(req, timeout=10)
        result["status"] = "healthy"
        result["detail"] = "API reachable (credentials available for signed requests)"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)[:100]
    return result


def check_db_freshness(conn=None):
    """Check data freshness for each cell-backed domain (the local database is retired)."""
    results = {}

    # Skills live in the roles-skills cell, not the local database.
    try:
        from cell_store import open_cell_store
        stamps = [str(sk["updated_at"])[:19] for sk in open_cell_store("roles-skills", "reporting-reader").list("skills")
                  if sk.get("updated_at")]
        last_update = max(stamps, default=None)  # ISO-8601 'YYYY-MM-DDTHH:MM:SS' prefix, as _is_stale parses
        results["skills"] = {"last_sync": last_update, "stale": _is_stale(last_update)}
    except Exception:
        results["skills"] = {"last_sync": None, "stale": True}

    # Habits live in the habits cell, not the local database.
    try:
        from habits_store import open_habits_store
        stamps = [h.get("updated_at") for h in open_habits_store("reporting-reader").habits()]
        last_update = max((str(x) for x in stamps if x), default=None)
        results["habits"] = {"last_sync": last_update, "stale": _is_stale(last_update)}
    except Exception:
        results["habits"] = {"last_sync": None, "stale": True}

    return results


def _is_stale(date_str, threshold_days=7):
    """Check if a date string is older than threshold."""
    if not date_str:
        return True
    try:
        # Handle various date formats
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                dt = datetime.strptime(date_str[:26], fmt)
                return (datetime.now() - dt).days > threshold_days
            except ValueError:
                continue
        return True
    except Exception:
        return True


def run_health_check(json_output=False, offline=False):
    """Run full health check and return results."""
    # API checks
    if offline:
        checks = [
            _credential_only(
                "Google Calendar", "📅", get_google_creds,
                "Missing Google OAuth credentials",
            ),
            _credential_only(
                "Gmail", "📧", get_google_creds,
                "Missing Google OAuth credentials",
            ),
            _credential_only(
                "Google Drive", "📁", get_google_creds,
                "Missing Google OAuth credentials",
            ),
            _credential_only(
                "GitHub", "🐙",
                lambda: get_env("GITHUB_PERSONAL_ACCESS_TOKEN", required=True),
                "Missing GITHUB_PERSONAL_ACCESS_TOKEN",
            ),
            _credential_only(
                "Cloudflare", "☁️",
                _require_cloudflare_token,
                "Missing CLOUDFLARE_API_TOKEN",
            ),
            _credential_only(
                "Vercel", "▲",
                lambda: get_env("VERCEL_TOKEN", required=True),
                "Missing VERCEL_TOKEN",
            ),
            check_paas_provider("render", live=False),
            check_paas_provider("railway", live=False),
            check_paas_provider("fly-io", live=False),
            check_paas_provider("heroku", live=False),
            _credential_only(
                "Telegram", "✈️",
                lambda: get_env("TELEGRAM_NOTI_BOT_TOKEN") or get_env("TELEGRAM_BOT_TOKEN", required=True),
                "Missing Telegram bot token",
            ),
            _credential_only(
                "LINE Official Account", "💬",
                _require_line_credentials,
                "Missing LINE channel access token/webhook secret",
            ),
            _credential_only(
                "Stripe", "💳",
                _require_stripe_credentials,
                "Missing Stripe secret key/webhook secret",
            ),
            _credential_only(
                "Sentry", "🛡️",
                _require_sentry_credentials,
                "Missing Sentry auth token/organization slug",
            ),
            _credential_only(
                "PostHog", "📊",
                _require_posthog_credentials,
                "Missing PostHog API key/project ID",
            ),
            _credential_only(
                "Slack", "💬",
                _require_slack_credentials,
                "Missing Slack bot token",
            ),
            _credential_only(
                "Notion", "⬛",
                _require_notion_credentials,
                "Missing Notion bot token",
            ),
            _credential_only(
                "Asana", "🟣",
                _require_asana_credentials,
                "Missing Asana personal access token",
            ),
            _credential_only(
                "Linear", "📐",
                _require_linear_credentials,
                "Missing Linear API key",
            ),
            _credential_only(
                "Discord", "🎮",
                _require_discord_credentials,
                "Missing Discord bot token",
            ),
            _credential_only(
                "WhatsApp Business", "🟢",
                _require_whatsapp_business_credentials,
                "Missing WhatsApp Cloud API token/WABA ID",
            ),
            _credential_only(
                "Microsoft Graph", "🔷",
                _require_microsoft_graph_credentials,
                "Missing Microsoft Graph access token",
            ),
            _credential_only(
                "Zoom", "📹",
                _require_zoom_credentials,
                "Missing Zoom access token",
            ),
            _credential_only(
                "monday.com", "🟨",
                _require_monday_credentials,
                "Missing monday.com API token",
            ),
            {"name": "Leantime", "icon": "📋", "status": "offline"},
            _credential_only(
                "Todoist", "✅",
                lambda: get_env("TODOIST_API_TOKEN", required=True),
                "Missing TODOIST_API_TOKEN",
            ),
        ]
    else:
        checks = [
            check_google_calendar(),
            check_gmail(),
            check_google_drive(),
            check_github(),
            check_cloudflare(),
            check_vercel(),
            check_render(),
            check_railway(),
            check_fly_io(),
            check_heroku(),
            check_telegram(),
            check_line(),
            check_stripe(),
            check_sentry(),
            check_posthog(),
            check_slack(),
            check_notion(),
            check_asana(),
            check_linear(),
            check_discord(),
            check_whatsapp_business(),
            check_microsoft_graph(),
            check_zoom(),
            check_monday(),
            check_leantime(),
            check_todoist(),
            # Strava and Binance TH are intentionally paused. Their historical
            # records and check functions remain available for a future re-enable,
            # but they must not load credentials or report as active integrations.
        ]

    # DB freshness
    freshness = check_db_freshness()

    # Summary
    healthy = sum(1 for c in checks if c["status"] == "healthy")
    configured = sum(1 for c in checks if c["status"] == "configured")
    not_configured = sum(1 for c in checks if c["status"] == "not_configured")
    errors = sum(1 for c in checks if c["status"] == "error")

    summary = {
        "timestamp": datetime.now().isoformat(),
        "total_integrations": len(checks),
        "healthy": healthy,
        "configured": configured,
        "not_configured": not_configured,
        "errors": errors,
        "integrations": checks,
        "data_freshness": freshness,
    }

    if json_output:
        print(json.dumps(summary, indent=2, default=str))
        return summary

    # Pretty print
    print(f"\n{'='*60}")
    print(f"  🔍 Integration Health Check")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    print(f"\n  📡 API Connections:")
    for c in checks:
        icon = {"healthy": "✅", "not_configured": "⚠️", "error": "❌", "degraded": "🟡"}.get(c["status"], "❓")
        line = f"  {icon} {c['icon']} {c['name']}"
        if c.get("detail"):
            line += f" — {c['detail']}"
        elif c.get("error"):
            line += f" — {c['error']}"
        print(line)

    print(f"\n  📊 Data Freshness:")
    for name, info in freshness.items():
        last = info["last_sync"] or "never"
        stale_flag = " ⚠️ STALE" if info["stale"] else ""
        print(f"    {name}: {last}{stale_flag}")

    print(f"\n  📈 Summary: {healthy} healthy, {configured} configured, {not_configured} not configured, {errors} errors")
    print()

    return summary


def main():
    args = sys.argv[1:]
    json_output = "--json" in args
    run_health_check(json_output=json_output, offline="--offline" in args)


if __name__ == "__main__":
    main()
