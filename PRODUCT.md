# Product

<!-- impeccable:product-schema 1 -->

## Platform

cli (Python scripts and the TypeScript owner CLI `infra/cli.ts`)

## Users

- The Solo Empire owner, from a terminal: `task ...`, the `empire` wrapper and `infra/cli.ts`
  (tasks, `task diary`, CRM ledger, platform registries, integrations, portfolio and learning
  workflows).
- AI agents working for the owner: the hub's harness hooks and `platform-mcp-aggregator` shell
  out to these scripts (`SOLO_OPS_ROOT`), and agents record the conversation diary through
  `task diary -- --record-conversation`.

## Product Purpose

The operations toolbox behind the daily workflows: the scripts and clients that read and write
the cells, keep the registries honest, and gate releases with evidence (`task quality:core`).

Success: every daily workflow runs from one command without hand-edited state, and a release
candidate of the core systems is either backed by revision-bound evidence or blocked.

## Positioning

Plain scripts with offline tests instead of a hosted operations service: clients call the cell
APIs with automation tokens, nothing depends on the `solo-empire` hub, and sibling repositories
are found through `SOLO_*_ROOT` variables or `../<repository>`.

## Operating Context

- Not deployed: runs on the owner's machine and in CI. Tests run offline with
  `EMPIRE_CLIENT_CONFIG=/nonexistent`; tests that need a sibling checkout skip with a visible
  `SKIP` (a partial result, not a pass).
- CI: shared `ci-python`, `ci-node` and security workflows.
- Split from the hub on 2026-10-11 by copy: history before that date stays in
  `bookchaowalit/solo-empire`.

## Capabilities and Constraints

- `task test`, `task test:strict` (fails on skips, partial runs, timeouts), `task docs:check`,
  `task quality:core` for `solo-ops`, `solo-mcp`, `empire-platform` and `solo-web` with JSON
  evidence in `.runtime/quality/`.
- The quality runner installs nothing, deploys nothing and makes no live API or credential
  lookups.
- `task diary` never stores raw prompts, responses, secrets or private transcripts; it needs the
  `reporting-reader` token, which is not set yet (owner action).
- Out of scope: hosted schedulers (crons run on Railway or GitHub Actions in their own repos).

## Measures

- `task test:strict` result on `main`: skips and partials (target: 0 outside documented
  sibling-checkout cases).
- Core quality gate evidence present for every release of the four core targets.
- Diary entries recorded per working day once `reporting-reader` is set [ต้องยืนยัน].
