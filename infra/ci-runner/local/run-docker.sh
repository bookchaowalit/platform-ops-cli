#!/usr/bin/env bash
# Docker-capable runners for image builds and image scans (label `docker`), on the Fedora homelab
# box under the unprivileged user `ci-docker` with rootless podman. Jobs talk to that user's podman
# socket as /var/run/docker.sock, so a job that breaks out of its containers is `ci-docker`: no root,
# no `docker` group, no access to other users' homes. The normal runners (run.sh, label `railway`)
# have no container engine at all; only jobs that ask for `docker` land here.
#
# Run from the workstation (gh + infisical mint the one-hour registration tokens):
#   ./run-docker.sh up       load the image, register missing runners, (re)start the service
#   ./run-docker.sh down     stop the service
#   ./run-docker.sh status   service state and each runner's GitHub status
#
# One-time host setup (owner, sudo), done 2026-11-10: user ci-docker (nologin, subuid/subgid),
# `loginctl enable-linger ci-docker`, and /etc/sudoers.d/ci-docker =
# `bookchaowalit ALL=(ci-docker) NOPASSWD: ALL` (run as that user only, never as root).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
HOST=${CI_DOCKER_HOST:-bookchaowalit@192.168.152.12}
KEY=${CI_DOCKER_KEY:-$HOME/.ssh/homelab_ed25519}
IMAGE=docker.io/library/ci-runner-local:latest
UNIT=ci-runner-docker
LABELS=self-hosted,linux,x64,docker
RUNNERS=$(grep -v '^\s*#' "$here/runners-docker.txt" | xargs)

# cd /tmp first: sudo -u ci-docker cannot enter this user's home.
remote() { ssh -i "$KEY" -o BatchMode=yes "$HOST" "cd /tmp && $*"; }
# Commands as ci-docker, with its user systemd and podman socket.
AS='sudo -n -u ci-docker env XDG_RUNTIME_DIR=/run/user/$(id -u ci-docker) HOME=/home/ci-docker'

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

load_image() {
  # Build with the host's Docker (same context as run.sh), then copy into ci-docker's podman store.
  tar -czf - -C "$here/.." Dockerfile entrypoint.sh job-cleanup.sh multi-runner.sh start-postgres.sh |
    remote "rm -rf \$HOME/ci-runner-ctx && mkdir -p \$HOME/ci-runner-ctx && tar -xzf - -C \$HOME/ci-runner-ctx &&
      docker build -q -t ci-runner-local \$HOME/ci-runner-ctx >/dev/null &&
      docker save ci-runner-local | $AS podman load -q >/dev/null && echo image loaded"
}

register() {
  local saved name token
  saved=$(remote "$AS bash -c 'mkdir -p ~/runner-config && for f in ~/runner-config/*/.runner; do [ -f \"\$f\" ] && basename \"\$(dirname \"\$f\")\"; done; true'")
  envfile=$(mktemp); chmod 600 "$envfile"
  trap 'rm -f "${envfile:-}"' EXIT
  for spec in $RUNNERS; do
    name=${spec%%=*}
    grep -qx "$name" <<<"$saved" && continue
    token=$(ghx "$(api_path "${spec#*=}")/actions/runners/registration-token" -X POST -q .token)
    [[ -n $token ]] || { echo "$name: no registration token" >&2; exit 1; }
    echo "RUNNER_TOKEN_$(tr 'a-z-' 'A-Z_' <<<"$name")=$token" >>"$envfile"
  done
  if [[ -s $envfile ]]; then
    # Tokens go over SSH stdin into a 0600 file that is deleted right after registration.
    remote "$AS bash -c 'umask 077; cat > ~/.reg.env; podman run --rm --security-opt label=disable \
      -e RUNNERS=\"$RUNNERS\" -e LABELS=$LABELS -e RUNNER_NAME_PREFIX=docker -e REGISTER_ONLY=1 \
      --env-file ~/.reg.env -v ~/runner-config:/runner-config $IMAGE | tail -1; rc=\${PIPESTATUS[0]}; rm -f ~/.reg.env; exit \$rc'" <"$envfile"
  fi
  rm -f "$envfile"
}

install_unit() {
  # Quadlet unit: podman generates ci-runner-docker.service; Restart=always, starts at boot (linger).
  remote "$AS bash -c 'mkdir -p ~/.config/containers/systemd && umask 077 && cat > ~/.config/containers/systemd/$UNIT.env && cat > ~/.config/containers/systemd/$UNIT.container <<EOF
[Unit]
Description=GitHub Actions runners with a container engine (label docker)

[Container]
ContainerName=$UNIT
Image=$IMAGE
EnvironmentFile=%h/.config/containers/systemd/$UNIT.env
Volume=%h/runner-config:/runner-config
Volume=%t/podman/podman.sock:/var/run/docker.sock
SecurityLabelDisable=true
PodmanArgs=--memory 3g --cpus 4

[Service]
Restart=always

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload && systemctl --user restart $UNIT.service && systemctl --user is-active $UNIT.service'" <<EOF
RUNNERS=$RUNNERS
LABELS=$LABELS
RUNNER_NAME_PREFIX=docker
JOB_CLEANUP=off
DISABLE_AUTOMATIC_DEREGISTRATION=true
DOCKER_HOST=unix:///var/run/docker.sock
DOCKER_BUILDKIT=0
EOF
}

install_prune() {
  # Weekly: drop images, build cache and stopped containers unused for 7 days. The runner image is
  # in use by the running container, so it stays.
  remote "$AS bash -c 'mkdir -p ~/.config/systemd/user && cat > ~/.config/systemd/user/podman-ci-prune.service <<EOF
[Unit]
Description=Prune old CI images and build cache (ci-docker)

[Service]
Type=oneshot
ExecStart=/usr/bin/podman system prune --all --force --filter until=168h
EOF
cat > ~/.config/systemd/user/podman-ci-prune.timer <<EOF
[Unit]
Description=Weekly prune of CI images (ci-docker)

[Timer]
OnCalendar=Sun 04:00 Asia/Bangkok
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload && systemctl --user enable --now podman-ci-prune.timer >/dev/null 2>&1 && systemctl --user is-enabled podman-ci-prune.timer'"
}

case ${1:-status} in
  up) load_image; register; install_unit; install_prune ;;
  prune-timer) install_prune ;;
  down) remote "$AS systemctl --user stop $UNIT.service" ;;
  status)
    remote "$AS systemctl --user is-active $UNIT.service" || true
    for spec in $RUNNERS; do
      ghx "$(api_path "${spec#*=}")/actions/runners" \
        -q '.runners[]|select(.name|startswith("docker-"))|"  "+.name+" "+.status+(if .busy then " busy" else "" end)'
    done
    ;;
  *) echo "usage: $0 up|down|status|prune-timer" >&2; exit 2 ;;
esac
