#!/usr/bin/env bash
# Run several GitHub Actions runners in one container to share its base memory.
# RUNNERS is a space-separated list of name=url entries, where url is a repository
# (https://github.com/<owner>/<repo>) or an organization (https://github.com/<org>).
# A runner registers once with RUNNER_TOKEN_<NAME> (name upper-cased, - becomes _);
# its registration files are then kept on the volume and reused on restart.
set -euo pipefail
export RUNNER_ALLOW_RUNASROOT=1

CONFIG_ROOT=${CONFIG_ROOT:-/runner-config}
LABELS=${LABELS:-self-hosted,linux,x64,railway}
mkdir -p /opt/runners

for spec in ${RUNNERS:?set RUNNERS to name=url entries}; do
  name=${spec%%=*}
  url=${spec#*=}
  dir=/opt/runners/$name
  saved=$CONFIG_ROOT/$name
  # Hard links share the runner binaries; config writes new files, so copies stay separate.
  rm -rf "$dir"
  cp -al /actions-runner "$dir"
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
  (cd "$dir" && exec ./run.sh) &
  echo "$name: started for $url"
done

# If any runner stops, exit so Railway restarts the whole container.
wait -n
echo "a runner exited; restarting" >&2
exit 1
