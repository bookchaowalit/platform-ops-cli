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
| `runner-multi` | `railway-org-products` | org | `bookchaowalit-products` |
| `runner-multi` | `railway-org-ai` | org | `bookchaowalit-ai` |
| `runner-multi` | `railway-org-website` | org | `bookchaowalit-website` (covers `marketing-linerichmenu-web`) |
| `runner-multi` | `railway-business-catalog` | repo | `bookchaowalit/business-catalog` |
| `runner-multi` | `railway-knowledge-base` | repo | `bookchaowalit/knowledge-base` |
| `runner-multi` | `railway-media-assets` | repo | `bookchaowalit/media-assets` |
| `runner-multi` | `railway-venture-freelance-dev` | repo | `bookchaowalit/venture-freelance-dev` |
| `runner-multi` | `railway-venture-freelance-seo` | repo | `bookchaowalit/venture-freelance-seo` |
| `runner-multi` | `railway-venture-saas` | repo | `bookchaowalit/venture-saas` |
| `runner-multi` | `railway-venture-job-agency` | repo | `bookchaowalit-agency/venture-job-agency` |

On 2026-10-24 seven private docs repositories joined `runner-multi` (repo-scoped, one slot each, image
raised to sixteen slots). They only run `markdownlint-cli2`, so they add little memory. All seven have
the owner as the only collaborator, so they fit the trust model above.

On 2026-10-23 the four one-runner services (`runner-sales-crm`, `runner-book-storybook`,
`runner-azure-migration-design`, `runner-marketing-linerichmenu-web`) moved into `runner-multi` and
were stopped with `railway down` (services and volumes kept). To go back: redeploy the old service
with a fresh `RUNNER_TOKEN` and remove its entry from `RUNNERS`. Only `sales-crm` uses
`start-postgres`; the shared PostgreSQL would need one database per repository before a second
repository starts using it.

`bookchaowalit-website` now uses an org runner. Org-scoped
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
