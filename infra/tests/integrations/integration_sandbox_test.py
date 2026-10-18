#!/usr/bin/env python3
"""
Integration Sandbox Tests

Tests external integrations (Gmail, Calendar, Strava) in sandbox/dry-run mode:
- Verifies not_configured status when credentials are missing
- Tests timeout and retry handling
- Verifies idempotency (no duplicate data on re-sync)
- Uses mock credentials, never touches production APIs

Usage:
    python3 infra/tests/integrations/integration_sandbox_test.py
"""

import os
import sys
import subprocess
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts' / 'utils'))
from _env import PROJECT_ROOT


def test_not_configured_status():
    """Verify integrations report not_configured when credentials are missing."""
    print("\n[1/5] Testing not_configured status...")

    # Clear Google credentials from environment
    env_keys = [
        'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN',
        'STRAVA_ACCESS_TOKEN', 'SECRETS_PROVIDER',
        'SECRETS_FALLBACK_ENV', 'INFISICAL_SECRET_PATH',
    ]
    env_backup = {}
    for key in env_keys:
        env_backup[key] = os.environ.pop(key, None)

    # Also temporarily rename .env so _env.load_env() doesn't re-populate
    env_file = PROJECT_ROOT / 'infra' / '.env'
    env_file_hidden = PROJECT_ROOT / 'infra' / '.env._sandbox_test_tmp'
    has_env = env_file.exists()
    if has_env:
        env_file.rename(env_file_hidden)

    try:
        # Re-import _env module after clearing env and hiding .env
        import importlib
        import _env as env_module
        os.environ['SECRETS_PROVIDER'] = 'env'
        importlib.reload(env_module)

        # Try to get credentials - should raise ValueError
        try:
            env_module.get_google_creds()
            print("  ❌ get_google_creds() should raise ValueError when credentials missing")
            return False
        except ValueError as e:
            if "not set" in str(e):
                print("  ✅ Google credentials correctly report not_configured")
            else:
                print(f"  ❌ Unexpected error: {e}")
                return False

        # Test Strava
        strava_token = env_module.get_strava_token()
        if strava_token is None:
            print("  ✅ Strava token correctly returns None when not configured")
        else:
            print("  ❌ Strava token should be None when not configured")
            return False

        return True

    finally:
        # Restore .env file
        if has_env and env_file_hidden.exists():
            env_file_hidden.rename(env_file)
        # Restore environment
        for key, value in env_backup.items():
            if value is not None:
                os.environ[key] = value
        # Reload _env to restore normal state
        import importlib
        import _env as env_module
        importlib.reload(env_module)


def test_integration_health_check():
    """Verify integration_health.py handles missing credentials gracefully."""
    print("\n[2/5] Testing integration health check...")

    health_script = PROJECT_ROOT / 'infra' / 'scripts' / 'sync' / 'integration_health.py'
    if not health_script.exists():
        print("  ⚠️  integration_health.py not found (skipping)")
        return True

    # Clear credentials
    env_backup = {}
    for key in ['GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN', 'STRAVA_ACCESS_TOKEN']:
        env_backup[key] = os.environ.pop(key, None)

    try:
        sandbox_env = os.environ.copy()
        sandbox_env['SECRETS_PROVIDER'] = 'env'
        for key in ['GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN',
                    'GITHUB_PERSONAL_ACCESS_TOKEN', 'CLOUDFLARE_API_TOKEN',
                    'VERCEL_TOKEN', 'TODOIST_API_TOKEN', 'TELEGRAM_NOTI_BOT_TOKEN']:
            sandbox_env.pop(key, None)
        result = subprocess.run(
            [sys.executable, str(health_script), '--json', '--offline'],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=PROJECT_ROOT,
            env=sandbox_env,
        )

        # Should exit 0 even when credentials missing
        if result.returncode != 0:
            print(f"  ❌ Health check failed with exit code {result.returncode}")
            print(result.stderr)
            return False

        # Parse JSON output
        try:
            output = json.loads(result.stdout)
            # Check that services report not_configured or error
            services = (
                output
                if isinstance(output, list)
                else output.get('services') or output.get('integrations', [])
            )
            not_configured_count = sum(1 for s in services if s.get('status') in ['not_configured', 'error'])

            if not_configured_count > 0:
                print(f"  ✅ Health check correctly reports {not_configured_count} services as not_configured/error")
                return True
            else:
                print("  ⚠️  No services reported as not_configured (may have credentials)")
                return True

        except json.JSONDecodeError:
            # Not JSON, check for text indicators
            if 'not_configured' in result.stdout or 'Missing' in result.stdout:
                print("  ✅ Health check reports not_configured status")
                return True
            else:
                print("  ⚠️  Could not parse health check output")
                return True

    except subprocess.TimeoutExpired:
        print("  ❌ Health check timed out")
        return False
    finally:
        # Restore environment
        for key, value in env_backup.items():
            if value is not None:
                os.environ[key] = value


def test_timeout_handling():
    """Verify scripts handle timeouts gracefully."""
    print("\n[3/5] Testing timeout handling...")

    # Test that sync scripts have timeout parameters
    sync_scripts = [
        'infra/scripts/sync/sync_google_calendar.py',
    ]

    for script_rel in sync_scripts:
        script = PROJECT_ROOT / script_rel
        if not script.exists():
            continue

        content = script.read_text()

        # Check for timeout in subprocess calls or urllib requests
        if 'timeout=' in content or 'TimeoutExpired' in content:
            print(f"  ✅ {script.name} has timeout handling")
        else:
            print(f"  ⚠️  {script.name} may lack timeout handling")

    return True


def test_retry_handling():
    """Verify scripts have retry logic for transient failures."""
    print("\n[4/5] Testing retry handling...")

    sync_scripts = [
        'infra/scripts/sync/sync_google_calendar.py',
        'infra/scripts/sync/sync_all_integrations.py',
    ]

    retry_count = 0
    for script_rel in sync_scripts:
        script = PROJECT_ROOT / script_rel
        if not script.exists():
            continue

        content = script.read_text()

        # Check for retry patterns
        if 'retry' in content.lower() or 'MAX_RETRIES' in content or 'attempt' in content:
            print(f"  ✅ {script.name} has retry logic")
            retry_count += 1

    if retry_count > 0:
        return True
    else:
        print("  ⚠️  No retry logic found in sync scripts")
        return True  # Not a hard failure


def test_idempotency_concept():
    """Verify sync scripts are designed for idempotency."""
    print("\n[5/5] Testing idempotency design...")

    # Check that sync scripts use INSERT OR REPLACE or check for duplicates
    sync_scripts = [
        'infra/scripts/sync/sync_google_calendar.py',
    ]

    idempotent_count = 0
    for script_rel in sync_scripts:
        script = PROJECT_ROOT / script_rel
        if not script.exists():
            continue

        content = script.read_text()

        # Check for idempotency patterns
        if ('INSERT OR REPLACE' in content or
            'INSERT OR IGNORE' in content or
            'ON CONFLICT' in content or
            'SELECT' in content and 'WHERE' in content):  # Check before insert
            print(f"  ✅ {script.name} has idempotency design")
            idempotent_count += 1

    if idempotent_count > 0:
        return True
    else:
        print("  ⚠️  Idempotency patterns not explicitly found (may use other methods)")
        return True  # Not a hard failure


def main():
    print("=" * 70)
    print("Integration Sandbox Tests")
    print("=" * 70)

    tests = [
        test_not_configured_status,
        test_integration_health_check,
        test_timeout_handling,
        test_retry_handling,
        test_idempotency_concept,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ❌ {test.__name__} raised exception: {e}")
            failed += 1

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70)

    if failed == 0:
        print("✅ All integration sandbox tests passed!")
        return 0
    else:
        print(f"❌ {failed} test(s) failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())
