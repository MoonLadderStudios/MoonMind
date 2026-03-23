# Orchestrator Removal Plan

## 1. Overview
The orchestrator component (`mm-orchestrator`) is no longer required for the project. Restarting and upgrading MoonMind does not need to happen from within MoonMind anymore. This plan outlines the necessary steps to safely remove the orchestrator and all its associated dependencies, endpoints, database models, and workflows from the codebase.

### 1.1 Spec & Prior Work

Spec `087-orchestrator-removal` tracked the initial bulk removal. The work covered in that spec (compose services, API router, `moonmind/workflows/orchestrator/` package, ORM models, Alembic migration, CI workflows, primary test and doc removals) has been completed and merged. This document captures the **remaining residual references** discovered via a full-repo grep.

### 1.2 Categorisation

Remaining references fall into three categories:

| Category | Description |
|---|---|
| **Active code** | Python/JS that still imports, configures, or branches on orchestrator concepts — must be removed or refactored |
| **Config & templates** | Settings, env templates, and TOML sections that provision orchestrator env vars — must be pruned |
| **Comments & docs** | Doc files, comments, and string literals that mention orchestrator — update or remove |

---

## 2. Active Code to Remove or Refactor

### 2.1 `moonmind/workflows/automation/orchestrator.py` (DELETE)

The entire 157-line module still exists. It defines `TriggeredWorkflow`, `WorkflowConflictError`, `WorkflowRetryError`, `trigger_workflow_run()`, and `retry_workflow_run()`. These are now superseded by Temporal workflow execution.

- **Delete** the file.
- **Update** `moonmind/workflows/__init__.py` to remove the import block (lines 7–13) and the corresponding `__all__` entries (`TriggeredWorkflow`, `WorkflowConflictError`, `WorkflowRetryError`, `retry_workflow_run`, `trigger_workflow_run`).
- **Grep-fix** any downstream imports of these symbols from `moonmind.workflows` or `moonmind.workflows.automation.orchestrator`. As of the audit, the only consumer is `moonmind/workflows/__init__.py` itself.

### 2.2 `moonmind/workflows/__init__.py` — orphaned docstrings (lines 39–43)

After a prior removal pass, lines 39–43 contain orphaned docstrings with no associated function definition. Remove these dead lines.

### 2.3 `moonmind/workflows/task_proposals/models.py` — `ORCHESTRATOR` enum value

`TaskProposalOriginSource.ORCHESTRATOR = "orchestrator"` (line 45). This is a DB-backed enum with a corresponding value in the `taskproposaloriginsource` PostgreSQL enum type.

- **Action**: Remove the Python enum member, then add a new Alembic migration to drop the `'orchestrator'` label from the PostgreSQL enum type (or leave it as a dead label if the migration complexity is not worth it — decide at implementation time).
- **Risk**: If any existing `task_proposals` rows have `origin_source = 'orchestrator'`, the migration must handle them first (update to `'workflow'` or `'manual'`).

### 2.4 `api_service/api/routers/task_dashboard.py` — blocked IDs & skill bucket

| Line | Reference | Action |
|---|---|---|
| 59 | `_BLOCKED_TOP_LEVEL_TASK_IDS` includes `"orchestrator"` | Remove `"orchestrator"` from the set |
| 289–293 | `# Empty orchestrator bucket ...` + `"orchestrator": []` in skills response | Remove the `"orchestrator"` key and the comment |

### 2.5 `api_service/static/task_dashboard/dashboard.js` — route matching (lines 9760–9761)

```js
const orchestratorDetailMatch = normalizedRoute.match(
  /^\/tasks\/orchestrator\/([^/]+)$/,
);
```

Remove the `orchestratorDetailMatch` variable, its associated conditional block, and any downstream branches that reference it.

### 2.6 `moonmind/config/settings.py` — `orchestrator_docker_host` field (lines 1789–1794)

```python
orchestrator_docker_host: Optional[str] = Field(
    None,
    env="ORCHESTRATOR_DOCKER_HOST",
    ...
)
```

Remove this field from `AppSettings`. If `DOCKER_HOST` is still needed elsewhere, it should use the worker-level `DOCKER_HOST` env var directly, not the orchestrator-prefixed one.

### 2.7 `tests/task_dashboard/` — orchestrator fixtures and test data

| File | Lines | What |
|---|---|---|
| `__fixtures__/task_rows.js` | 47, 55 | `createOrchestratorRow` factory and export |
| `test_task_layouts.js` | 22, 432 | Import + usage of `createOrchestratorRow` |
| `test_submit_runtime.js` | 156, 403–411 | `orchestrator: "/orchestrator/tasks"` route, `orchestratorPriority` test data, `resolveQueueSubmitPriorityForRuntime("orchestrator", ...)` |

Remove orchestrator test data, fixtures, and assertions.

### 2.8 `tests/unit/api/routers/test_task_dashboard.py`

| Line | What | Action |
|---|---|---|
| 61 | `assert not _is_allowed_path("orchestrator/run-1")` | Remove (the blocked-ID set entry it tests is being removed) |
| 183 | `"orchestrator": []` in expected response | Remove |
| 271–289 | `test_task_resolution_rejects_orchestrator_source_hint()` | Delete entire test |

### 2.9 `specs/062-task-execution-compatibility/contracts/task-execution-compatibility.openapi.yaml`

Lines 178, 186, 274, 419 include `orchestrator` in `enum` arrays. Remove `orchestrator` from these enum definitions.

---

## 3. Configuration & Environment Templates

### 3.1 `config.toml` — `[orchestrator]` section (lines 54–57)

```toml
[orchestrator]
artifact_root = "var/artifacts/workflows"
statsd_host = ""
statsd_port = 8125
```

**Delete** the entire `[orchestrator]` section.

### 3.2 `.env-template`

| Line(s) | Content | Action |
|---|---|---|
| 11 | `ORCHESTRATOR_DOCKER_HOST="unix:///var/run/docker.sock"` | Delete |
| 116–123 | `# Orchestrator mirror overrides ...` block with `MOONMIND_ORCHESTRATOR_*` vars | Delete entire block |

### 3.3 `.env.vllm-template`

| Line(s) | Content | Action |
|---|---|---|
| 30–34 | `# Orchestrator service defaults` + `ORCHESTRATOR_ARTIFACT_ROOT`, `ORCHESTRATOR_STATSD_HOST`, `ORCHESTRATOR_STATSD_PORT`, `ORCHESTRATOR_DOCKER_HOST` | Delete entire block |

### 3.4 `pyproject.toml` — pytest marker (line 105)

```
"integration: marks integration tests that exercise orchestrator workflows.",
```

Remove or reword to remove orchestrator mention (e.g., `"integration: marks integration tests that exercise workflows."`).

---

## 4. Documentation & Comment Cleanup

### 4.1 Files requiring substantive edits

These docs reference orchestrator as an active component and need text updates:

| File | Nature of reference |
|---|---|
| `docs/Api/ExecutionsApiContract.md` (line 44) | Mentions `legacy /orchestrator/* compatibility routes` |
| `docs/ManagedAgents/DockerOutOfDocker.md` (line 152) | References `ORCHESTRATOR_DOCKER_HOST` env var |
| `docs/ManagedAgents/ManagedAgentsGit.md` (line 82) | "Temporal orchestrator" — clarify wording |
| `docs/Temporal/LiveTaskManagement.md` | Orchestrator as task source |
| `docs/Temporal/TemporalPlatformFoundation.md` | Orchestrator architecture references |
| `docs/Temporal/TaskExecutionCompatibilityModel.md` | Orchestrator compatibility branches |
| `docs/Temporal/ActivityCatalogAndWorkerTopology.md` | Orchestrator topology |
| `docs/Temporal/RunHistoryAndRerunSemantics.md` | Orchestrator reruns |
| `docs/Temporal/SourceOfTruthAndProjectionModel.md` | Orchestrator projection |
| `docs/Temporal/RoutingPolicy.md` | Orchestrator routing |
| `docs/Temporal/VisibilityAndUiQueryModel.md` | Orchestrator UI queries |
| `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` | Orchestrator execution |
| `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` | Orchestrator lifecycle |
| `docs/Temporal/ops-runbook.md` | Orchestrator ops |
| `docs/Tasks/TaskArchitecture.md` | Orchestrator task architecture |
| `docs/Tasks/SkillAndPlanEvolution.md` | Orchestrator skill evolution |
| `docs/UI/MissionControlArchitecture.md` | Orchestrator UI arch |
| `docs/UI/MissionControlStyleGuide.md` | Orchestrator UI style |
| `docs/Memory/MemoryResearch.md` | Orchestrator mention |
| `docs/Memory/MemoryArchitecture.md` | Orchestrator mention |
| `docs/Rag/ManifestIngestDesign.md` | Orchestrator mention |
| `docs/LegacyDocsReview.md` | Orchestrator mention |
| `docs/Troubleshooting/TemporalWorkerExecutionIssues.md` | Orchestrator troubleshooting |
| `docs/pre-commit-integration.md` (lines 51, 77) | Example output referencing deleted test file |
| `docs/ExternalAgents/JulesTemporalExternalEventContract.md` (line 541) | "Celery/orchestrator paths" |

### 4.2 `docs/tmp/` sibling docs

| File | Lines | Action |
|---|---|---|
| `docs/tmp/SharedManagedAgentAbstractions.md` | Orchestrator mentions | Update |
| `docs/tmp/Roadmap.md` | Orchestrator mentions | Update |
| `docs/tmp/SingleSubstrateMigration.md` | Multiple tables referencing orchestrator as legacy | Update to reflect removal is complete |

### 4.3 Comment-only mentions (lower priority)

| File | Line | Content | Action |
|---|---|---|---|
| `moonmind/utils/logging.py` | 56 | `"can be applied to orchestrator"` | Reword comment |
| `moonmind/workflows/tasks/routing.py` | 52 | `"The legacy queue and orchestrator"` | Reword comment |
| `moonmind/workflows/automation/workspace.py` | 273 | `"orchestrator code can export them"` | Reword comment |
| `moonmind/workflows/adapters/openclaw_agent_adapter.py` | 34 | `"by the MoonMind orchestrator"` | Reword to `"by MoonMind"` |
| `moonmind/manifest/evaluation.py` | 158 | Section heading `# Orchestrator` | Rename to `# Evaluation runner` or similar |
| `api_service/Dockerfile` | 6 | `"the API, orchestrator, and Temporal workers"` | Remove `orchestrator,` from comment |
| `package.json` | 4 | Description mentions `"Temporal-based orchestrator"` | Reword to remove orchestrator reference |
| `README.md` | 39 | Reference to "an orchestrator" | Evaluate and reword |

### 4.4 Files to NOT touch (historical / migration records)

These files mention orchestrator but should be left as-is:

- `api_service/migrations/versions/c1d2e3f4a5b6_drop_orchestrator_tables.py` — the migration that dropped orchestrator tables; historical record.
- `api_service/migrations/versions/594fc88de6eb_initial_clean_migration.py` — initial migration; historical record.
- `tests/unit/orchestrator_removal/test_doc_req_coverage.py` — guard tests that validate orchestrator was removed; these are the safety net.

### 4.5 Uses of "orchestrator" that mean something different

The word "orchestrator" is also used generically in some docs (e.g., the OAuth session workflow is described as an "orchestrator workflow", the pr-resolver skill is called a "master orchestrator"). These are legitimate uses of the word in a non-`mm-orchestrator` context and should **not** be changed:

- `moonmind/workflows/temporal/workflows/oauth_session.py` — `"OAuth session orchestrator workflow"` (describes the workflow pattern)
- `moonmind/workflows/temporal/runtime/providers/base.py` — `"session orchestrator"` (refers to the OAuth session workflow)
- `moonmind/workflows/temporal/runtime/strategies/base.py` — `"Session orchestrator"` (refers to the OAuth session workflow)
- `docs/ManagedAgents/UniversalTmateOAuth.md` — numerous references to "orchestrator" meaning the OAuth session workflow
- `docs/ManagedAgents/SkillGithubPrResolver.md` — `"Master orchestrator"` (describes pr-resolver's role)

---

## 5. Database Migration

The primary Alembic migration (`c1d2e3f4a5b6`) that drops orchestrator tables has already been created and merged. Additional migration work:

- **Optional**: Add a migration to remove `'orchestrator'` from the `taskproposaloriginsource` PostgreSQL enum type if the `TaskProposalOriginSource.ORCHESTRATOR` Python enum member is removed (§2.3). This requires `ALTER TYPE ... DROP VALUE` which is only supported in PostgreSQL 13+.

---

## 6. Post-Removal Validation

- Run `./tools/test_unit.sh` and verify all existing DOC-REQ guard tests pass (`tests/unit/orchestrator_removal/test_doc_req_coverage.py`).
- Run `grep -ri 'orchestrator' --include='*.py' moonmind/ api_service/` and confirm only:
  - Migration files (historical)
  - Guard tests
  - Generic "orchestrator" uses (OAuth session, pr-resolver)
  - Comment-only references (if any remain intentionally)
- Start the application stack via `docker compose up -d` and verify that no `mm-orchestrator` service is present.
- Verify `dashboard.js` no longer matches `/tasks/orchestrator/` routes by loading the dashboard and confirming no orchestrator UI is rendered.

---

## 7. Implementation Order

1. **Phase A — Active code** (§2): Delete `orchestrator.py`, clean `__init__.py`, remove enum value, clean dashboard router/JS, clean settings, clean test fixtures and specs.
2. **Phase B — Config** (§3): Prune `config.toml`, `.env*-template`, `pyproject.toml`.
3. **Phase C — Docs** (§4): Substantive doc edits, comment rewording, description updates.
4. **Phase D — Migration** (§5, optional): Enum cleanup migration if proceeding.
5. **Phase E — Validate** (§6): Full test suite + grep verification.
