# Private-repo CI runner (Railway)

Public repositories keep using GitHub-hosted runners. Private repositories run on this
self-hosted runner so they do not consume paid GitHub Actions minutes.

- Image: `myoung34/github-runner` (Ubuntu 24.04) plus PostgreSQL 17 for test databases.
- Host: Railway project `ci-runners`, one service per runner scope.
- Labels: `self-hosted, linux, x64, railway`. Workflows use
  `runs-on: [self-hosted, linux, x64, railway]`.
- Docker is not available inside Railway containers, so `services:` containers do not work.
  Start the database with a step: `run: start-postgres` and connect to
  `postgresql://postgres@127.0.0.1:5432/postgres`.

## Local primary, Railway fallback (since 2026-11-03)

The owner's machine is now the primary host for every runner below (owner decision 2026-11-03,
replacing "never local" for CI: local runs are free, Railway runner-multi cost about $14 a month,
almost all of it idle memory). `local/run.sh up` builds this image and runs the same `RUNNERS`
(`local/runners.txt`, same names and order) in one Docker container named `ci-runner-local`:
runners `local-<name>`, labels `self-hosted,linux,x64,railway,local`, so no workflow changes. The
container has an 8 GB / 6 CPU limit, `--restart unless-stopped`, no Docker socket and no home-directory
mount, so jobs cannot read this machine's credentials. `JOB_CLEANUP=off` keeps dependency caches
(memory is not billed locally). Registrations live in `~/.local/share/ci-runner-local/config`.

**Host moved to Fedora (2026-11-10).** The container now runs on the Fedora homelab box
(`192.168.152.12`, Ryzen 7 4700U, 8 threads, 15 GiB), not on the workstation, so CI keeps
running when the workstation is off or rebooting into Windows. It has a 12 GB / 7 CPU limit and the
volume is mounted with `:Z` (SELinux). Fedora is set up as a server: the sleep, suspend and hibernate
targets are masked, the lid switch is ignored, it boots to `multi-user.target`, and Docker is enabled at
boot. The registrations were moved, not re-created (the same 29 `local-<name>` runner IDs). The old
workstation state is kept, but not used, at `~/.local/share/ci-runner-local.moved-to-fedora-2026-11-10`.
Drive Fedora from the workstation with the header of `local/run.sh`
(`DOCKER_HOST=ssh://bookchaowalit@192.168.152.12 CI_RUNNER_MEMORY=12g CI_RUNNER_CPUS=7 CI_RUNNER_SELINUX=1`,
with `~/.ssh/homelab_ed25519` in `ssh-agent`). Never run the container on two hosts at once.

**Railway stopped (owner decision 2026-11-10):** "use the VM runners, not Railway, to save cost".
`railway down -s runner-multi` was run, so all 29 `railway-*` runners are offline. The volume and
registrations are kept. A CI rerun afterwards ran all 5 architecture-docs jobs on `local-architecture-docs`.
Each repository now has **one** runner, so its jobs run one after another (that run took 12 minutes in a row,
where it ran in parallel before). If Fedora is down, jobs wait in the queue (up to 24 h) until it is back, or
until someone runs `railway redeploy -s runner-multi` by hand. The watchdog below can do that restart on its
own only after the owner sets `RAILWAY_TOKEN`.

**Two runners per repository (2026-11-10):** a second container `ci-runner-local-2` runs the same
`local/runners.txt` as `local2-<name>`, with its own registrations in `~/.local/share/ci-runner-local-2/config`
(`CI_RUNNER_INSTANCE=2 ... ./run.sh up`). Both containers are limited to 6 GB each (12 GB of Fedora's 15)
and 7 CPU each. The architecture-docs CI went from 12 min (all jobs one after another) to 7 min (two at a
time). Adding a runner means running `run.sh up` for both instances.

**Docker-capable runners (2026-11-10):** `local/run-docker.sh` runs `docker-<name>` runners (labels
`self-hosted,linux,x64,docker`, list `local/runners-docker.txt`: the orgs plus private repos with a Dockerfile)
on Fedora under the unprivileged user `ci-docker` with rootless podman, as a podman Quadlet user service
`ci-runner-docker` (3 GB / 4 CPU). Jobs use that user's podman socket as `/var/run/docker.sock`
(`DOCKER_BUILDKIT=0` → podman's build API); short image names resolve to Docker Hub
(`~/.config/containers/registries.conf`), and `podman-ci-prune.timer` prunes images unused for 7 days every
Sunday. Only `platform-ci-workflows/image-scan.yml` targets them. Escape boundary = `ci-docker` (no root, no
`docker` group, home 700). One-time owner setup with sudo is in the script header.
The service runs the image **by ID** (recorded by `run-docker.sh up`), so a job that retags images in the
shared podman store cannot make a restart run its image. It is ordered after `podman.socket`.
Residual risk (accepted, single owner): all 13 runners share one container, so a job can read every
`docker-*` registration in it, and it can use the socket to stop or remove the runner container (DoS).
This is the same multi-runner trade-off as the `local`/`railway` sets. No org runner group allows public
repositories (checked 2026-11-10), so fork PRs cannot reach any of them. The stronger option, one ephemeral
(JIT) runner per job in a fresh container, is not built. Limit: a job's
`docker run -v $PWD:...` mounts the host path, not the runner container's `/_work`, so bind-mounting the
checkout into a container does not work; `docker build` (context upload) does.

Railway runner-multi keeps its `railway-<name>` registrations as the fallback.
`.github/workflows/ci-runner-failover.yml` (every 10 minutes, GitHub-hosted, environment
`ci-runner-failover`) runs `infra/scripts/platform/ci_runner_failover.py`: if the first runner in
`local/runners.txt` is offline for longer than a 3-minute grace period, it restarts runner-multi's deployment
(`deploymentRestart`, runners online in about 65 s, measured 2026-11-03) and sends a Telegram alert. Once
the local runner is back and no `railway-*` runner is busy, it stops runner-multi again (`deploymentStop`,
volume and registrations kept). A job queued while both hosts are down waits for the fallback, because GitHub
queues self-hosted jobs for up to 24 hours. Both hosts take jobs while both are up (GitHub has no runner
priority).

Adding a runner: append to `local/runners.txt` AND to Railway `RUNNERS` (same position), then
`local/run.sh up` and the Railway steps below. The two must not drift, or the fallback misses repositories.

## Registration and credentials

No long-lived GitHub token is stored on Railway. Each service gets a one-hour registration
token (`RUNNER_TOKEN`) created with `gh api -X POST repos/<owner>/<repo>/actions/runners/registration-token`
(or `orgs/<org>/...` for an org-wide runner). The runner's configuration is kept on a Railway
volume mounted at `/runner-config` (`CONFIGURED_ACTIONS_RUNNER_FILES_DIR`), so restarts reuse
the registration. If the volume is lost, create a new registration token and redeploy.

Do not set `EPHEMERAL` at all: the image treats any non-empty value, including `false`, as
ephemeral, so the runner deregisters after one job. Set `DISABLE_AUTOMATIC_DEREGISTRATION=true`.
If GitHub deletes the registration (the runner was offline too long), the volume holds stale
config and the runner logs "registration has been deleted": replace the volume with an empty one,
set a fresh `RUNNER_TOKEN`, and redeploy.

Personal-account repositories (`bookchaowalit/*`) cannot use an organization runner; each needs its
own repo-scoped registration. Organization repositories share one org-scoped registration.

## Several runners in one service (`runner-multi`)

An idle runner container uses about 0.2 GB of memory (0.36 GB on average with jobs), roughly
$4 a month on Railway. To keep the cost down, new runners go into one service instead of one
service each: when `RUNNERS` is set, the image starts `multi-runner.sh`, which runs one runner per
`name=url` entry (`url` is a repository or an organization) in the same container. The image
holds sixteen runner slots hard-linked at build time, so all runners share one set of binaries and
one page cache (linking at runtime would copy every file up into the container layer and cost
about 1 GB of billed memory). Measured 2026-10-23: eight idle runners use 0.17 GB after a restart and about
0.64 GB after jobs, against 0.24 GB idle for one single-runner service.

After each job, `job-cleanup.sh` (the runner's job-completed hook) deletes the dependency caches
(`~/.cache`, `~/.npm`, `~/.local`, the tool cache) and stops the test PostgreSQL when no other job
in the container is running: Railway bills page cache as memory and an idle container never
reclaims it (2.5 GB after one sales-crm run without the hook, 0.64 GB with it). Jobs download their
dependencies again, which makes them slower; inbound traffic is not billed. The runner runs the hook
with `bash -e -o pipefail` and only accepts a `.sh` path, so every step in it must be non-fatal. Registration files are kept per runner under
`/runner-config/<name>` on the volume.

Add a runner (the service restarts and the other runners re-attach from the volume):

```bash
# one-hour registration token from the Infisical GitHub PAT (has admin:org), never printed
infisical run --projectId "$INFISICAL_PROJECT_ID" --env dev --path /github --silent -- bash -c '
  t=$(curl -sf -X POST -H "Authorization: token $GITHUB_PERSONAL_ACCESS_TOKEN" \
      https://api.github.com/repos/<owner>/<repo>/actions/runners/registration-token | jq -r .token)
  npx -y @railway/cli@latest variables -s runner-multi --skip-deploys \
      --set "RUNNERS=<existing entries> <name>=https://github.com/<owner>/<repo>" \
      --set "RUNNER_TOKEN_<NAME>=$t"'
npx -y @railway/cli@latest up --detach -s runner-multi   # from this folder
```

Redeploy only while no job is running: a job queued while the old container is shutting down can
stay queued after the new runners are online (seen 2026-10-23); cancel and re-run it.

Then delete `RUNNER_TOKEN_<NAME>` (it has expired anyway; the script also unsets every
`RUNNER_TOKEN_*` before any runner takes a job).

Trust model (accepted 2026-10-23): runners in one container share a filesystem and run jobs as root,
as the single-runner services already do, so a job could read another runner's registration on the
volume. That is acceptable only because every runner here serves the owner's own private
repositories (no outside contributors, no fork pull requests). A repository with outside
contributors, or a client repository, must not join `runner-multi`: give it its own service. If GitHub removes a registration after
the runner was offline too long, delete `/runner-config/<name>` (or the whole volume) and register
again. Peak memory is the sum of the jobs running at once, so a busy day costs more than idle.

## Services

| Service | Runner | Scope | Target |
| --- | --- | --- | --- |
| `runner-multi` | `railway-architecture-docs` | repo | `bookchaowalit/architecture-docs` |
| `runner-multi` | `railway-realestate-lead-extension` | repo | `bookchaowalit/realestate-lead-extension` |
| `runner-multi` | `railway-sales-crm` | repo | `bookchaowalit/sales-crm` |
| `runner-multi` | `railway-book-storybook` | repo | `bookchaowalit/book-storybook` |
| `runner-multi` | `railway-azure-migration-design` | repo | `bookchaowalit/azure-migration-design` |
| `runner-multi` | `railway-digital-products` | repo | `bookchaowalit/commerce-digital-products-web` |
| `runner-multi` | `railway-org-ai` | org | `bookchaowalit-ai` |
| `runner-multi` | `railway-linerichmenu` | repo | `bookchaowalit/marketing-linerichmenu-web` |
| `runner-multi` | `railway-business-catalog` | repo | `bookchaowalit/business-catalog` |
| `runner-multi` | `railway-knowledge-base` | repo | `bookchaowalit/knowledge-base` |
| `runner-multi` | `railway-media-assets` | repo | `bookchaowalit/media-assets` |
| `runner-multi` | `railway-venture-freelance-dev` | repo | `bookchaowalit/venture-freelance-dev` |
| `runner-multi` | `railway-venture-freelance-seo` | repo | `bookchaowalit/venture-freelance-seo` |
| `runner-multi` | `railway-venture-saas` | repo | `bookchaowalit/venture-saas` |
| `runner-multi` | `railway-venture-job-agency` | repo | `bookchaowalit-agency/venture-job-agency` |
| `runner-multi` | `railway-solo-empire` | repo | `bookchaowalit/solo-empire` (archived 2026-11-13; removed from `local/runners.txt`, drop from Railway `RUNNERS` on its next redeploy) |

On 2026-10-24 `commerce-digital-products-web` and `marketing-linerichmenu-web` moved from their
organizations to the personal account (Vercel Hobby refuses Git links to private organization
repositories). Their organizations had no other repository with CI, so the org runners
`org-products` and `org-website` were replaced in place in `RUNNERS` (same slots) by the repo runners
`digital-products` and `linerichmenu`. The org registrations stay on GitHub, offline, until GitHub
removes them; their saved config under `/runner-config/org-*` is kept, so putting the entries back
restores them.

On 2026-10-24 seven private docs repositories joined `runner-multi` (repo-scoped, one slot each, image
raised to sixteen slots). They only run `markdownlint-cli2`, so they add little memory. All seven have
the owner as the only collaborator, so they fit the trust model above.

On 2026-10-25 the hub `bookchaowalit/solo-empire` took the sixteenth (last) slot for its weekly
`mcp-catalog-refresh` workflow (one small Python job a week; owner is the only collaborator). The image
is now full: the next runner needs more slots in the image or a second service.

On 2026-11-03 the image went to thirty-two slots (`ARG RUNNER_SLOTS`) for thirteen more runners:
org runners `org-ar`, `org-desktop`, `org-game`, `org-products`, `org-startup`, `org-vr`, `org-mobile`,
`org-backend`, `org-agency` and `org-website` (one per organization, so every private repository in it
can run CI, starting with each org hub), and repo runners `personal-hub`, `bookchaowalit-empire` and
`ai-stack` for the personal account. Checked the same day against the trust model: every one of those
organizations has the owner as its only member and no outside collaborators, and the three personal
repositories have the owner as their only collaborator. Idle runners stay small (about 20 MB each).

On 2026-10-23 the four one-runner services (`runner-sales-crm`, `runner-book-storybook`,
`runner-azure-migration-design`, `runner-marketing-linerichmenu-web`) moved into `runner-multi` and
were stopped with `railway down` (services and volumes kept). To go back: redeploy the old service
with a fresh `RUNNER_TOKEN` and remove its entry from `RUNNERS`. Only `sales-crm` uses
`start-postgres`; the shared PostgreSQL would need one database per repository before a second
repository starts using it.

Org-scoped
registration tokens need `admin:org`: the local `gh` token lacks it, the Infisical `/github` PAT has it.
Other organizations with private repositories (`bookchaowalit-freelance` client work, the `*-hub`
repositories) have no CI workflows, so they get no runner until they do.

## Client repositories: no CI

Client repositories do not get CI/CD or a runner (owner decision, 2026-10-17): they are deployed
on the PaaS or our own servers. Their workflows are disabled with `gh workflow disable`, not
deleted, so they can be turned back on with `gh workflow enable`. Disabled on 2026-10-17:

- `bookchaowalit/{ev3-web, ev3-api, ev3-mobile, findfox-frontend, findfox-backend, findfox-mobile,
  tma-moldflow-api, tma-moldflow-web, a2n-web, a2n-api}`

`Happy-Teeth/*` are client repositories as well and get no runner. Their workflows were left enabled
because `happy-teeth-line` runs the production deploy and the hourly toothbrush-reminder cron, and
these jobs use the org's own hosted minutes.
