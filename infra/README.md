# infra

Tooling owned by ops-toolkit (see the repository README for the overview, sibling variables and checks).

```text
infra/
  cli.ts                owner CLI; shared modules come from the mcp-aggregator sibling (mcp-modules.ts)
  mcp-modules.ts        loader for ../platform-mcp-aggregator/src/*.ts (override SOLO_MCP_ROOT)
  scripts/
    utils/              _env.py (path resolver), guards, stores, reports
    platform/           cloud/PaaS/self-hosted registries, probes, status
    sync/               integration clients (read-only unless a script says otherwise)
    workflows/          daily diary, briefings, Book Dev loop
    learning/ quality/ security/ setup/ docpack/ discovery/ cli/ github/ db/
  tests/                offline tests (`python3 infra/tests/run_all.py`), SQLite store doubles in tests/support
  tasks/*.yml           Task adapters included by the root Taskfile.yml
  platform/             registries owned by ops (cloud-providers, domains, building blocks, ...)
  scaffolds/            platform repository scaffold
```

Commands: `task --list`. Python entrypoints run through `scripts/setup/run-python3.sh`
(or `run-python3.py`), which picks a real Linux `python3` (a local `.venv` wins).

## Removed or moved on 2026-10-11

* Dashboard pages and the scripts that only generated them (`knowledge_atlas`, `domain_scanner`,
  `data_freshness`, `doc_number_guard`, `root_structure_guard`, `operating_model_guard`,
  `role_system_coverage_guard`): retired; `owner-web` replaces the dashboards. Remaining report writers
  (`ai_cycle`, `role_task_*`, `portfolio_*`, `goal_portfolio_mapping`) write to `data/generated/reports`
  (override `SOLO_REPORTS_DIR`).
* `mcp-server/` and `mcp/`: the `mcp-aggregator` repository. `skill_governance`: the `agent-skills` repository.
* `matrix_template_guard`, `architecture_provider_readiness`: the `architecture-docs` repository.
* `income_streams` and `ventures/`: the `business-catalog` repository.
* Registry guards (`repository_catalog_guard`, `repo_roadmap_*`, `system_repository_guard`,
  `module_boundary_guard`, `platform_repository_guard`, `book_dev_structure_guard`,
  `workspace_boundary_guard`, `systems_status`) and the agent-harness hook scripts live in
  `bookchaowalit/bookchaowalit-empire` (moved from solo-empire on 2026-11-12), next to the registry they validate.
