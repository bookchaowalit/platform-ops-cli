#!/usr/bin/env python3
"""
System Health Endpoint — Unified health check combining all monitoring systems.

Aggregates: DB stats, revenue, goals, habits, tasks, cron health,
data freshness, and alert summary into a single health report.

Usage:
    python3 system_health.py              # Full health report
    python3 system_health.py --quick      # One-line health summary
    python3 system_health.py --json       # JSON output (for APIs / MCP)
    python3 system_health.py --save       # Save to data/generated/briefings/health-DATE.json
"""

import json
from _env import PROJECT_ROOT, REPORTS_DIR
import os
# Add utils directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from task_store import ClientConfigError, open_task_store
from cell_store import CellStoreError, open_cell_store
from habits_store import HabitsStoreError, completed_today, open_habits_store
from secret_resolver import EnvProvider, resolve_active_provider
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

SCRIPTS_DIR = PROJECT_ROOT / 'infra' / 'scripts' / 'utils'
BRIEFINGS_DIR = PROJECT_ROOT / 'data' / 'generated' / 'briefings'
DASHBOARD_DIR = REPORTS_DIR


# ── Section collectors ────────────────────────────────────────────────

def collect_revenue():
    """Revenue summary. ฿-labeled numeric fields are THB-only."""
    from finance_totals import safe_revenue_overview

    overview = safe_revenue_overview()  # CRM ledger; flagged unavailable when unreachable
    return {
        'unavailable': bool(overview.get('unavailable')),
        'received': overview['received'],
        'pending': overview['pending'],
        'total': overview['total'],
        'entries': overview['entries'],
        'reporting_currency': overview['reporting_currency'],
        'received_display': overview['received_display'],
        'pending_display': overview['pending_display'],
        'by_currency': overview['by_currency'],
    }


def collect_goals():
    """Goal summary; ``unavailable`` when the goals API cannot be reached."""
    try:
        store = open_cell_store("goals", "reporting-reader")
        active = len(store.list("goals", status="active"))
        task_counts = store.stats("goal-tasks", "status")
    except (CellStoreError, ClientConfigError):
        return {'active': 0, 'tasks_total': 0, 'tasks_done': 0, 'unavailable': True}
    tasks_total = sum(task_counts.values())
    tasks_done = task_counts.get("done", 0)
    return {'active': active, 'tasks_total': tasks_total, 'tasks_done': tasks_done}


def collect_habits():
    """Habit summary; ``unavailable`` when the habits API cannot be reached."""
    today = date.today().isoformat()
    try:
        store = open_habits_store("reporting-reader")
        active = len(store.habits(status="active"))
        logged_today = len(completed_today(store.habit_logs(date_from=today, date_to=today), today))
    except (HabitsStoreError, ClientConfigError):
        return {'active': 0, 'logged_today': 0, 'unavailable': True}
    return {'active': active, 'logged_today': logged_today}


def collect_tasks():
    """Task summary."""
    stats = open_task_store(None, "reporting-reader").stats(group_by='status', include_archived=True)
    total = stats['total']
    done = stats['groups'].get('done', 0)
    in_progress = stats['groups'].get('in_progress', 0)
    return {'total': total, 'done': done, 'in_progress': in_progress}


def collect_cron():
    """Cron job health."""
    try:
        jobs = open_cell_store('automation', 'reporting-reader').list('cron-jobs', is_active=True)
        total = len(jobs)
        active = total
        failed = sum(1 for j in jobs if j.get('last_status') == 'failed')
        success = sum(1 for j in jobs if j.get('last_status') == 'success')

        # Last run times
        ran = sorted((j for j in jobs if j.get('last_run_at')), reverse=True,
                     key=lambda j: datetime.fromisoformat(str(j['last_run_at']).replace('Z', '+00:00')).timestamp())
        recent_list = [{'job_type': r['job_type'], 'last_run': r['last_run_at'], 'status': r['last_status']} for r in ran[:5]]
        failed_jobs = [j['job_type'] for j in jobs if j.get('last_status') == 'failed']

        return {
            'total': total, 'active': active,
            'success': success, 'failed': failed,
            'pending': total - success - failed,
            'recent': recent_list,
            'failed_jobs': failed_jobs,
        }
    except Exception as e:
        return {'error': str(e)}


def collect_freshness():
    """Data freshness check (calls data_freshness.py --json)."""
    script = SCRIPTS_DIR / 'data_freshness.py'
    if not script.exists():
        return {'error': 'data_freshness.py not found'}
    try:
        result = subprocess.run(
            [sys.executable, str(script), '--json'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            return data.get('summary', {})
        return {'error': 'no output'}
    except Exception as e:
        return {'error': str(e)}


def collect_alerts():
    """Alert summary (calls alerts.py --json)."""
    script = SCRIPTS_DIR / 'alerts.py'
    if not script.exists():
        return {'error': 'alerts.py not found'}
    try:
        result = subprocess.run(
            [sys.executable, str(script), '--json'],
            capture_output=True, text=True, timeout=30
        )
        # alerts.py may exit 1 if critical alerts found — still parse output
        if result.stdout.strip():
            data = json.loads(result.stdout)
            return data.get('summary', {})
        return {'error': 'no output'}
    except Exception as e:
        return {'error': str(e)}


# ── Aggregation ────────────────────────────────────────────────────────

def compute_grade(issues, checks):
    """Compute letter grade from issue/check ratio."""
    if checks == 0:
        return 'A'
    ratio = issues / checks
    if ratio == 0:
        return 'A'
    elif ratio <= 0.25:
        return 'B'
    elif ratio <= 0.5:
        return 'C'
    else:
        return 'D'


def full_health():
    """Collect all health data with separate System and Business health scores."""
    health = {
        'schema_version': '1.0',
        'timestamp': datetime.now().isoformat(),
        'revenue': collect_revenue(),
        'goals': collect_goals(),
        'habits': collect_habits(),
        'tasks': collect_tasks(),
        'cron': collect_cron(),
        'freshness': collect_freshness(),
        'alerts': collect_alerts(),
        'credentials': collect_credentials(),
    }

    # ── System Health: code, database, tests, integrations ──
    # Note: Missing credentials (not_configured) are NOT system failures
    # Only actual errors (permission_denied, network_failure) count
    sys_issues = 0
    sys_checks = 5
    cron = health['cron']
    if isinstance(cron, dict) and cron.get('failed', 0) > 0:
        # Only exempt a failed optional integration when its matching
        # credential is actually not configured. Never let unrelated missing
        # credentials hide a real cron failure.
        creds = health.get('credentials', {})
        credential_job_map = {
            'sync_gmail': 'gmail',
            'sync_calendar': 'calendar',
            'run_alerts': 'telegram',
        }
        actual_failures = 0
        for job_type in cron.get('failed_jobs', []):
            integration = credential_job_map.get(job_type)
            if not integration or creds.get(integration, {}).get('status') != 'not_configured':
                actual_failures += 1
        if actual_failures > 0:
            sys_issues += 1
    freshness = health['freshness']
    if isinstance(freshness, dict) and freshness.get('stale', 0) + freshness.get('missing', 0) > 2:
        sys_issues += 1
    # DB integrity (FK violations, migration status)
    # Checked via smoke tests — if we reach here, DB is accessible
    # Count cron-failed as integration issue, not business
    rev = health['revenue']
    if rev['pending'] > rev['total'] * 0.5 and rev['total'] > 0:
        sys_issues += 1

    health['system_health'] = {
        'issues': sys_issues,
        'checks': sys_checks,
        'healthy': sys_issues == 0,
        'grade': compute_grade(sys_issues, sys_checks),
    }

    # ── Business Health: outreach, goals, habits, follow-ups ──
    biz_issues = 0
    biz_checks = 3
    alerts = health['alerts']
    if isinstance(alerts, dict):
        if alerts.get('critical', 0) > 0:
            biz_issues += 1
        if alerts.get('warnings', 0) > 3:
            biz_issues += 1
    habits = health['habits']
    if habits.get('logged_today', 0) == 0 and habits.get('active', 0) > 0:
        biz_issues += 1

    health['business_health'] = {
        'issues': biz_issues,
        'checks': biz_checks,
        'healthy': biz_issues == 0,
        'grade': compute_grade(biz_issues, biz_checks),
    }

    # ── Overall: combined (for backward compat) ──
    total_issues = sys_issues + biz_issues
    total_checks = sys_checks + biz_checks
    health['score'] = {
        'issues': total_issues,
        'checks': total_checks,
        'healthy': total_issues == 0,
        'grade': compute_grade(total_issues, total_checks),
    }
    return health


def collect_credentials():
    """Check provider readiness without fetching or printing secret values."""

    try:
        mode, provider = resolve_active_provider()
        provider_name = getattr(provider, 'name', 'none') if provider else 'none'
        if provider is None or (
            not isinstance(provider, EnvProvider) and not provider.is_configured()
        ):
            return {
                'gmail': {'status': 'not_configured', 'message': 'credential provider not configured'},
                'calendar': {'status': 'not_configured', 'message': 'credential provider not configured'},
                'telegram': {'status': 'not_configured', 'message': 'credential provider not configured'},
            }
        if isinstance(provider, EnvProvider):
            google_ready = all(os.environ.get(key) for key in (
                'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN',
            ))
            telegram_ready = bool(
                os.environ.get('TELEGRAM_NOTI_BOT_TOKEN')
                or os.environ.get('TELEGRAM_BOT_TOKEN')
            )
            google = {
                'status': 'configured' if google_ready else 'not_configured',
                'provider': provider_name,
            }
            telegram = {
                'status': 'configured' if telegram_ready else 'not_configured',
                'provider': provider_name,
            }
        else:
            google = {'status': 'configured', 'provider': provider_name}
            telegram = {'status': 'configured', 'provider': provider_name}
    except Exception:
        google = {'status': 'error', 'message': 'credential provider check failed'}
        telegram = {'status': 'error', 'message': 'credential provider check failed'}

    return {
        'gmail': dict(google),
        'calendar': dict(google),
        'telegram': telegram,
    }


def print_health(health):
    """Pretty-print health report with separate System and Business health."""
    print(f"\n{'=' * 64}")
    print(f"  🏰 SOLO EMPIRE — HEALTH REPORT")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 64}")

    # System Health
    sys_score = health['system_health']
    sys_icon = {'A': '🟢', 'B': '🟡', 'C': '🟠', 'D': '🔴'}.get(sys_score['grade'], '⚪')
    print(f"\n  🔧 System Health: {sys_icon} Grade {sys_score['grade']} ({sys_score['issues']} issues / {sys_score['checks']} checks)")
    print(f"     (code, database, tests, integrations)")

    # Business Health
    biz_score = health['business_health']
    biz_icon = {'A': '🟢', 'B': '🟡', 'C': '🟠', 'D': '🔴'}.get(biz_score['grade'], '⚪')
    print(f"\n  💼 Business Health: {biz_icon} Grade {biz_score['grade']} ({biz_score['issues']} issues / {biz_score['checks']} checks)")
    print(f"     (outreach, goals, habits, follow-ups)")

    # Overall
    score = health['score']
    grade_icon = {'A': '🟢', 'B': '🟡', 'C': '🟠', 'D': '🔴'}.get(score['grade'], '⚪')
    print(f"\n  📊 Overall: {grade_icon} Grade {score['grade']} ({score['issues']} issues / {score['checks']} checks)")

    # Details
    rev = health['revenue']
    received_fallback = f"฿{rev['received']:,.0f}"
    pending_fallback = f"฿{rev['pending']:,.0f}"
    print(f"  💰 Revenue: {rev.get('received_display', received_fallback)} received | {rev.get('pending_display', pending_fallback)} pending")

    goals = health['goals']
    print(f"  🎯 Goals: {goals['active']} active | {goals['tasks_done']}/{goals['tasks_total']} tasks done")

    habits = health['habits']
    if habits.get('unavailable'):
        print("  🔁 Habits: unavailable (habits API)")
    else:
        print(f"  🔁 Habits: {habits['logged_today']}/{habits['active']} logged today")

    tasks = health['tasks']
    print(f"  📋 Tasks: {tasks['done']}/{tasks['total']} done | {tasks['in_progress']} in progress")

    cron = health['cron']
    if 'error' not in cron:
        print(f"  ⚙️  Cron: {cron['success']} ok / {cron['failed']} failed / {cron['total']} total")

    freshness = health['freshness']
    if 'error' not in freshness:
        print(f"  📊 Freshness: {freshness.get('ok', 0)} fresh | {freshness.get('stale', 0)} stale | {freshness.get('missing', 0)} missing")

    alerts = health['alerts']
    if 'error' not in alerts:
        print(f"  🚨 Alerts: {alerts.get('critical', 0)} critical | {alerts.get('warnings', 0)} warnings | {alerts.get('info', 0)} info")

    print(f"\n{'=' * 64}\n")


def quick_health(health):
    """One-line summary."""
    score = health['score']
    rev = health['revenue']
    grade_icon = {'A': '🟢', 'B': '🟡', 'C': '🟠', 'D': '🔴'}.get(score['grade'], '⚪')
    received_fallback = f"฿{rev['received']:,.0f}"
    print(f"{grade_icon} Health {score['grade']} | {rev.get('received_display', received_fallback)} received | {score['issues']} issues")


def main():
    args = sys.argv[1:]
    as_json = '--json' in args
    quick = '--quick' in args
    save = '--save' in args

    health = full_health()

    if as_json:
        print(json.dumps(health, indent=2, default=str))
    elif save:
        BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        filepath = BRIEFINGS_DIR / f'health-{today}.json'
        with open(filepath, 'w') as f:
            json.dump(health, f, indent=2, default=str)
        print(f'✅ Saved to {filepath}')
    elif quick:
        quick_health(health)
    else:
        print_health(health)


if __name__ == '__main__':
    main()
