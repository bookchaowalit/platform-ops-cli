#!/usr/bin/env bash
# RUNNERS set: several runners in one container. Otherwise the image's single-runner entrypoint.
if [[ -n ${RUNNERS:-} ]]; then
  exec /usr/local/bin/multi-runner
fi
exec /entrypoint.sh "$@"
