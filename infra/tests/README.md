# ops-toolkit tests

Offline test scripts grouped by the boundary they protect. Every `*_test.py` is a stand-alone script
(plain asserts or `unittest`); `run_all.py` runs them all, each in its own interpreter with the production
Tasks API disabled (`EMPIRE_CLIENT_CONFIG=/nonexistent/...`).

```bash
task test                              # python3 infra/tests/run_all.py
python3 infra/tests/run_all.py --only quality/ --jobs 4
python3 infra/tests/quality/smoke_test.py   # one script
```

```text
tests/
├── agent/        # Book Dev loop, dispatch, domain loop, unused-file report
├── data_lake/    # job consumer contract
├── decisions/    # decision records, briefs and context
├── diary/        # daily diary and summaries
├── engagements/  # engagement review and notification contracts
├── integrations/ # provider read-only and sync boundaries
├── learning/ media/ rnd/ seo/ security/ discovery/ finance/
├── platform/     # cloud/PaaS registries, usage, workspace paths
├── quality/      # smoke, regression, automation and contract guard checks
├── tasks/        # task-store readers/writers over the SQLite store doubles
├── support/      # SQLite store doubles, legacy DDL, siblings.py (sibling checkout lookup)
└── fixtures/     # sanitized fixtures
```

Tests that need a sibling repository (`architecture-docs`, `knowledge-base`, `agent-skills`, ...) look it up
through `support/siblings.py` (`SOLO_<NAME>_ROOT` or `../<repo>`) and print `SKIP` / `skipped` when it is absent,
so a fresh clone passes with `task test`. No test reads a registry-hub checkout.

The agent-harness hook tests and the registry guard tests live in `bookchaowalit/bookchaowalit-empire`;
skill governance tests live in `bookchaowalit/platform-agent-skills`; the mcp-aggregator tests (including the three that drive
`infra/cli.ts` from this repository) live in `bookchaowalit/platform-mcp-aggregator`.
