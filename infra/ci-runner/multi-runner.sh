#!/usr/bin/env bash
# Run several GitHub Actions runners in one container to share its base memory.
# RUNNERS is a space-separated list of name=url entries, where url is a repository
# (https://github.com/<owner>/<repo>) or an organization (https://github.com/<org>).
# A runner registers once with RUNNER_TOKEN_<NAME> (name upper-cased, - becomes _);
# its registration files are then kept on the volume and reused on restart.
set -euo pipefail
export RUNNER_ALLOW_RUNASROOT=1
export ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/usr/local/bin/job-cleanup.sh

CONFIG_ROOT=${CONFIG_ROOT:-/runner-config}
LABELS=${LABELS:-self-hosted,linux,x64,railway}
slot=0

# Register everything first, then drop the registration tokens from the environment so no job
# (all runners share this container) can read them.
specs=${RUNNERS:?set RUNNERS to name=url entries}
for spec in $specs; do
  name=${spec%%=*}
  url=${spec#*=}
  saved=$CONFIG_ROOT/$name
  # Pre-built hard-linked copies (Dockerfile) share the runner binaries; config writes new files.
  slot=$((slot + 1))
  dir=/opt/runners/slot$slot
  if [[ ! -d $dir ]]; then
    echo "$name: no runner slot $slot (the image has 16)" >&2
    exit 1
  fi
  ln -sfn "$dir" "/opt/runners/$name"
  if [[ -f $saved/.runner ]]; then
    cp -p "$saved"/.[!.]* "$dir"/
  else
    var=RUNNER_TOKEN_$(tr 'a-z-' 'A-Z_' <<<"$name")
    token=${!var:-}
    if [[ -z $token ]]; then
      echo "$name: no saved registration and no $var" >&2
      exit 1
    fi
    (cd "$dir" && ./config.sh --unattended --replace --url "$url" --token "$token" \
      --name "railway-$name" --labels "$LABELS" --work "/_work/$name")
    mkdir -p "$saved"
    cp -p "$dir"/.runner "$dir"/.credentials "$dir"/.credentials_rsaparams "$saved"/
    for f in .env .path; do [[ -f $dir/$f ]] && cp -p "$dir/$f" "$saved"/; done
  fi
  chmod 700 "$saved"
done

for var in "${!RUNNER_TOKEN_@}"; do unset "$var"; done

for spec in $specs; do
  name=${spec%%=*}
  (cd "/opt/runners/$name" && exec ./run.sh) &
  echo "$name: started for ${spec#*=}"
done

# If any runner stops, exit so Railway restarts the whole container.
wait -n
echo "a runner exited; restarting" >&2
exit 1
