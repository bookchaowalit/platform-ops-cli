#!/usr/bin/env bash
# Job-completed hook (ACTIONS_RUNNER_HOOK_JOB_COMPLETED) for runner-multi. Railway bills page cache
# as memory and nothing reclaims it in an idle container, so after the last running job finishes,
# delete the dependency caches the jobs wrote and stop the test database. The next job downloads
# again (inbound traffic is free). Skipped while another job in this container is still running.
# The runner runs hooks with `bash -e -o pipefail`, so no step here may fail the job.
if (( $(pgrep -c -x Runner.Worker || true) > 1 )); then
  exit 0
fi
rm -rf /root/.cache /root/.npm /root/.local /opt/hostedtoolcache/* 2>/dev/null || true
pg_ctlcluster 17 main stop >/dev/null 2>&1 || true
exit 0
