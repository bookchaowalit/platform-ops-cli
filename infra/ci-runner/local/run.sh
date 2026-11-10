#!/usr/bin/env bash
# Run the runner-multi image on this machine as the primary private-repo CI host. Railway's
# runner-multi stays registered and running as the fallback. Until a watchdog switches it on and
# off automatically, both hosts take jobs and Railway is not stopped.
#   ./run.sh up       build the image, register missing runners, (re)start the container
#   ./run.sh down     stop the container (Railway takes over after the watchdog notices)
#   ./run.sh status   container state and each runner's GitHub status
#
# Since 2026-11-10 the primary host is the Fedora homelab box (192.168.152.12), not the workstation.
# Run this script from the workstation (it needs gh + infisical for registration tokens) against
# Fedora's Docker over SSH:
#   eval "$(ssh-agent -s)"; ssh-add ~/.ssh/homelab_ed25519
#   DOCKER_HOST=ssh://bookchaowalit@192.168.152.12 CI_RUNNER_MEMORY=12g CI_RUNNER_CPUS=7 \
#     CI_RUNNER_SELINUX=1 ./run.sh up
# $HOME/STATE is resolved on the workstation; the user name and home path are the same on Fedora.
# Never start it on two hosts at once: both would use the same registrations.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
IMAGE=ci-runner-local
CONTAINER=ci-runner-local
STATE=${CI_RUNNER_STATE:-$HOME/.local/share/ci-runner-local}
LABELS=self-hosted,linux,x64,railway,local
RUNNERS=$(grep -v '^\s*#' "$here/runners.txt" | xargs)
MEMORY=${CI_RUNNER_MEMORY:-8g}
CPUS=${CI_RUNNER_CPUS:-6}
# SELinux hosts (Fedora) need the volume relabelled for the container.
RW=${CI_RUNNER_SELINUX:+:Z}
RO=:ro${CI_RUNNER_SELINUX:+,Z}

common=(-e RUNNERS="$RUNNERS" -e LABELS="$LABELS" -e RUNNER_NAME_PREFIX=local -e JOB_CLEANUP=off
  -e DISABLE_AUTOMATIC_DEREGISTRATION=true -v "$STATE/config:/runner-config$RW")

# GitHub API call with the right credentials: repo paths via the local gh token, org paths via
# the Infisical /github PAT. Extra arguments go to gh api.
ghx() {
  local path=$1; shift
  if [[ $path == repos/* ]]; then
    gh api "$path" "$@"
  else
    infisical run --projectId "$INFISICAL_PROJECT_ID" --env dev --path /github --silent -- \
      bash -c 'GH_TOKEN=$GITHUB_PERSONAL_ACCESS_TOKEN exec gh api "$@"' _ "$path" "$@"
  fi
}

api_path() {
  local path=${1#https://github.com/}
  [[ $path == */* ]] && echo "repos/$path" || echo "orgs/$path"
}

register() {
  local name saved token
  # Saved registrations are root-owned 0700 inside the volume, so list them from a container.
  saved=$(docker run --rm --entrypoint sh -v "$STATE/config:/c$RO" "$IMAGE" \
    -c 'for f in /c/*/.runner; do [ -f "$f" ] && basename "$(dirname "$f")"; done; true')
  envfile=$(mktemp); chmod 600 "$envfile"
  trap 'rm -f "$envfile"' EXIT
  for spec in $RUNNERS; do
    name=${spec%%=*}
    grep -qx "$name" <<<"$saved" && continue
    # One-hour registration token, never printed.
    token=$(ghx "$(api_path "${spec#*=}")/actions/runners/registration-token" -X POST -q .token)
    [[ -n $token ]] || { echo "$name: no registration token" >&2; exit 1; }
    echo "RUNNER_TOKEN_$(tr 'a-z-' 'A-Z_' <<<"$name")=$token" >>"$envfile"
  done
  if [[ -s $envfile ]]; then
    docker run --rm "${common[@]}" -e REGISTER_ONLY=1 --env-file "$envfile" "$IMAGE"
  fi
  rm -f "$envfile"
}

case ${1:-status} in
  up)
    # On a remote DOCKER_HOST the bind path must already exist there (Docker creates it root-owned otherwise).
    if [[ -z ${DOCKER_HOST:-} ]]; then mkdir -p "$STATE/config"; fi
    docker build -q -t "$IMAGE" "$here/.." >/dev/null
    register
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    # Limits keep a heavy job from starving the desktop; no Docker socket and no home mount, so
    # jobs cannot reach this machine's credentials.
    docker run -d --name "$CONTAINER" --restart unless-stopped --memory "$MEMORY" --cpus "$CPUS" \
      "${common[@]}" "$IMAGE" >/dev/null
    echo "started $CONTAINER: $RUNNERS"
    ;;
  down) docker stop "$CONTAINER" ;;
  status)
    docker ps -a --filter "name=^$CONTAINER\$" --format '{{.Names}} {{.Status}}'
    for spec in $RUNNERS; do
      ghx "$(api_path "${spec#*=}")/actions/runners" \
        -q '.runners[]|"  "+.name+" "+.status+(if .busy then " busy" else "" end)'
    done
    ;;
  *) echo "usage: $0 up|down|status" >&2; exit 2 ;;
esac
