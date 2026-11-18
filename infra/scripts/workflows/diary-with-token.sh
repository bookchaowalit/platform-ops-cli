#!/usr/bin/env bash
# task diary:hosted — run the daily diary with ONLY the reporting-reader automation token.
# The token lives in Infisical /empire-platform (EMPIRE_AUTOMATION_TOKEN_REPORTING_READER, renewed
# weekly by token-rotate); that folder also holds other platform credentials, so it is never
# injected whole. Tracing is off before the token exists, and a failed or empty read stops here
# instead of running the diary without it. An already-set variable is used as is.
set +x
set -euo pipefail
cd "$(dirname "$0")/../.."   # infra/

if [ -z "${EMPIRE_AUTOMATION_TOKEN_REPORTING_READER:-}" ]; then
  : "${INFISICAL_PROJECT_ID:?set INFISICAL_PROJECT_ID (source ~/.config/solo-empire/environment.sh)}"
  if ! token="$(infisical secrets get EMPIRE_AUTOMATION_TOKEN_REPORTING_READER \
      --projectId "$INFISICAL_PROJECT_ID" --env "${INFISICAL_ENVIRONMENT:-dev}" \
      --path /empire-platform --plain --silent 2>/dev/null)"; then
    echo "diary:hosted: could not read reporting-reader from Infisical /empire-platform" >&2
    exit 1
  fi
  if [ -z "$token" ]; then
    echo "diary:hosted: reporting-reader token is empty in Infisical /empire-platform" >&2
    exit 1
  fi
  export EMPIRE_AUTOMATION_TOKEN_REPORTING_READER="$token"
  unset token
fi
exec python3 scripts/setup/run-python3.py scripts/workflows/daily_diary.py "$@"
