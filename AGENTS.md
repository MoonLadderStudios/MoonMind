# Agent Instructions

## Read Documentation

Read relevant documents in the following order before implementing tasks:

1. **Constitution:** `.specify/memory/constitution.md` for non-negotiable principles and constraints
2. **Standards:** Code style and guidance in `README.md`
3. **Spec:** `specs/<feature-id>/spec.md`, then `plan.md`, then `tasks.md`
4. **Docs:** `docs/*.md` as needed for system architecture (see **Documentation: canonical vs tmp** below).
   - Start here for Agent Skills: `docs/Tasks/AgentSkillSystem.md`
   - For Executable Tools: `docs/Tasks/SkillAndPlanContracts.md`
   - For Runtime boundaries: `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
5. **Migration / implementation backlog (when relevant):** `docs/tmp/remaining-work/` and `docs/tmp/PlansOverview.md` for plan-shaped or in-flight work tied to canonical docs.

## Agent Skill System Terminology
- Executable `tool.type = "skill"` contracts are **not** the same thing as agent instruction bundles (skill sets) under `.agents/skills`.
- For agent instruction bundles and snapshot logic, the canonical design is in `docs/Tasks/AgentSkillSystem.md`.
- For executable tool contracts, the canonical design is in `docs/Tasks/SkillAndPlanContracts.md`.

## When Modifying the Agent Skill System
When writing code that interacts with skills:
- Read `docs/Tasks/AgentSkillSystem.md`.
- Keep `.agents/skills` as the canonical active path.
- Keep `.agents/skills/local` as a local-only overlay.
- Do not mutate checked-in skill folders in place.
- Keep large skill content out of workflow history (use refs).
- Add workflow/activity or adapter-boundary tests.

## Documentation: canonical vs `docs/tmp`

- **Canonical docs** (`docs/` except `docs/tmp/`): describe **declarative desired state** — architecture, contracts, operator-visible behavior, target semantics. Avoid making phased migration or implementation checklists the main story in these files.
- **Migration and implementation notes** belong under **`docs/tmp/`** (e.g. per-doc trackers in `docs/tmp/remaining-work/`, indexes in `docs/tmp/PlansOverview.md`) so they can be **removed when the work completes** without rewriting the canonical spec.
- Align with **Constitution principle XII** in `.specify/memory/constitution.md`.

## Spec Numbering

When creating a new spec folder/feature ID:

- ✅ **DO** scan `specs/` and find the highest numeric prefix across all directories matching `<number>-<name>`.
- ✅ **DO** assign the next number globally (`max + 1`), regardless of short-name/topic.
- ✅ **DO** keep branch/feature/spec numbering aligned to that global next number.
- ❌ **DON'T** reset numbering to `001` for a new short-name if higher numbered specs already exist.

## Testing Instructions

### Test Taxonomy

MoonMind uses a four-tier test model that separates hermetic CI from credentialed provider checks:

| Tier | Marker(s) | Required on PR? | Runner |
|------|-----------|-----------------|--------|
| **Unit** | `asyncio` (as needed) | Yes | `./tools/test_unit.sh` |
| **Hermetic Integration CI** | `integration` + `integration_ci` | Yes | `./tools/test_integration.sh` |
| **Provider Verification** | `provider_verification` + `jules` + `requires_credentials` | No (manual/nightly) | `./tools/test_jules_provider.sh` |
| **Local-only Integration** | `integration` without `integration_ci` | No | local dev only |

- **Hermetic Integration Tests** — compose-backed, local-dependencies-only, no external credentials required.
  These are marked with `@pytest.mark.integration_ci` and are run by the required CI pipeline.
  Use `./tools/test_integration.sh` (Bash) or `tools/test-integration.ps1` (PowerShell) to run them locally.

  The required integration_ci suite focuses on the highest-risk seams:
  - **Artifacts**: create/upload/list, auth/preview, lifecycle cleanup, authorization boundaries
  - **Worker topology**: activity family routing, task queue assignment, sandbox execution
  - **Live logs**: SSE publisher/subscriber, performance at volume, managed runtime streaming
  - **Compose foundation**: service topology, namespace bootstrapping, visibility schema rehearsal
  - **Startup seeding**: profiles, managed secrets, task templates

- **Provider Verification Tests** — real third-party provider checks using real credentials.
  These are **not** required for merge and are excluded from the required CI pipeline.
  They are marked with `@pytest.mark.provider_verification` (and often `@pytest.mark.jules` / `@pytest.mark.requires_credentials`).
  Use `./tools/test_jules_provider.sh` (Bash) or `tools/test-provider.ps1` (PowerShell) to run them locally.

- **Temporal workflow boundary tests with time-skipping** (`tests/integration/temporal/test_execution_rescheduling.py`, `tests/integration/temporal/test_interventions_temporal.py`, `tests/integration/workflows/temporal/**`) are **not** marked `integration_ci` because they consistently exceed CI timeout thresholds under the Temporal test server. They remain valuable for local dev verification.

Note: Jules **unit** tests (`tests/unit/jules/`, `tests/unit/workflows/temporal/test_jules_activities.py`, etc.) remain in the required unit suite — only Jules *provider verification* tests are excluded from required CI.

### Running Tests

- **Unit Tests**: Always use `./tools/test_unit.sh` for final unit-test verification. In MoonMind-managed agent containers, unit tests are expected to run locally inside the current container. Do not use `./tools/test_unit_docker.sh` or nested Docker for normal managed-agent verification.
- **Managed-Agent Local Test Mode**: Managed-agent worker environments should run with `MOONMIND_FORCE_LOCAL_TESTS=1`. The WSL Docker fallback applies to human local WSL development only, not to containerized worker sessions.
- **Frontend Test Prereqs**: Frontend unit tests require local Node/npm and repo JS dependencies from `package-lock.json`. `./tools/test_unit.sh` should prepare these automatically when dashboard tests are enabled. If `node_modules` is missing or stale relative to `package-lock.json`, the script runs `npm ci --no-fund --no-audit` before executing `npm run ui:test`.
- **Targeted Test Runs**: Positional args to `./tools/test_unit.sh` filter Python tests only. They do not target a Vitest file. For focused frontend iteration, use `npm run ui:test -- <path>` after local JS deps are prepared, or use `./tools/test_unit.sh --ui-args <path>` to route Vitest targets through the test runner. Before finalizing, rerun `./tools/test_unit.sh` for the full suite.
- **No Docker Assumption in Agent Jobs**: Do not assume the Docker socket is available inside MoonMind-managed agent workspaces when running unit tests.
- **Hermetic Integration Tests**: Run compose-backed, no-credentials-required tests marked with `integration_ci`. Use `./tools/test_integration.sh` (Bash) or `tools/test-integration.ps1` (PowerShell). Under the hood: `docker compose -f docker-compose.test.yaml run --rm pytest bash -lc "pytest tests/integration -m 'integration_ci' -q --tb=short"`.
- **Provider Verification**: Run live external-provider tests that require real credentials. Use `./tools/test_jules_provider.sh` (Bash) or `tools/test-provider.ps1` (PowerShell). These scripts fail fast if `JULES_API_KEY` is not set.
- **Workflow Boundary Coverage**: Any change to Temporal workflows, activity signatures, signal/update names, serialized payload shapes, status normalization, or adapter-to-workflow contracts MUST add or update tests at the workflow boundary, not just isolated unit tests. At minimum:
  - cover the real invocation shape used by the worker binding or Temporal activity wrapper,
  - cover one compatibility case for the previous payload/history shape when runs may already be in flight,
  - cover degraded provider input such as unknown, blank, or newly introduced status values.
- **Replay / In-Flight Safety**: If a change can affect already-running workflows or persisted payloads, add a compatibility or replay-style regression test, or document why in-flight compatibility is impossible and how the cutover is made safe.
- **Agent Skill System Coverage**: Changes to agent-skill selection, snapshot resolution, runtime materialization, or adapter-visible skill paths must include tests covering the real workflow/activity or adapter boundary. If the change affects already-running workflow payloads, include in-flight compatibility coverage or explicit cutover notes.
- **Skill Architectural Boundaries**: Source loading, resolution, manifest generation, and materialization belong strictly at activity/service boundaries. Workflow code should carry immutable refs and compact metadata only. Large skill content must not be embedded in workflow payloads.

## Agent Job Storage Locations
- Agent jobs are executed in a per-run workspace directory named with the job UUID.
- In a Docker worker container, look under `/work/agent_jobs/<job_id>/`.
- Per-job artifacts for those runs are under `/work/agent_jobs/<job_id>/artifacts`, and the checked-out repo is at `/work/agent_jobs/<job_id>/repo`.
- To inspect a run from the host, use the Docker volume directly:
  `docker run --rm -v agent_workspaces:/work/agent_jobs -it -v /tmp:/host_tmp alpine sh -lc 'ls /work/agent_jobs/<job_id> | head'`.
- In the repository code/docs path, durable workflow artifacts for workflow automation are typically written to `var/artifacts/<scope>/<run_id>` (for example `var/artifacts/spec_workflows/<run_id>`).

## Troubleshooting Temporal Workflow Runs

When asked to check on a workflow, follow this procedure in order:

1. **Describe the parent workflow** (always use `--namespace default`):
   ```
   docker exec moonmind-temporal-1 temporal workflow describe \
     --namespace default --workflow-id "<workflow-id>"
   ```
   Check: Status, StartTime, StateTransitionCount, HistoryLength, Pending Activities, Pending Child Workflows.

2. **If the parent has pending child workflows**, describe each child:
   ```
   docker exec moonmind-temporal-1 temporal workflow describe \
     --namespace default --workflow-id "<child-workflow-id>"
   ```

3. **Inspect recent history** of whichever workflow is actively executing (the deepest pending child):
   ```
   docker exec moonmind-temporal-1 temporal workflow show \
     --namespace default --workflow-id "<workflow-id>" | tail -30
   ```
   A healthy agent poll loop looks like: `ActivityTaskScheduled → Started → Completed → TimerStarted → TimerFired` repeating on ~10s intervals. If the last event is an `ActivityTaskScheduled` with no `Started` for minutes, the worker may be down or the task queue starved.

4. **List workflows** when the ID is unknown or to find related runs:
   ```
   docker exec moonmind-temporal-1 temporal workflow list \
     --namespace default --query "mm_state = 'executing'"
   ```

5. **If it is not an obvious Temporal scheduling failure**, keep going: diagnose the **agent runtime and environment**. Clear Temporal-side issues include pending activities that never start (worker down or queue starved), repeated `WorkflowTaskFailed` / stuck workflow tasks, or wrong namespace. When the parent or child **completed** but returned a failed business outcome (for example `execution_error`, “process exited with code …”), or `ActivityTaskFailed` with an application error from the activity, treat that as **agent/tooling/environment** until proven otherwise. Follow the evidence using whatever is available: **artifact content** (workflow `diagnosticsRef` / `outputRefs`, `artifact.read`, UI artifact downloads, `var/artifacts/...`, `/work/agent_jobs/<job_id>/...`), **container and worker logs**, and **database rows** (e.g. `agent_jobs` and related projection tables) until the root cause is identified.

Key diagnostics:
- **Pending Activities > 0 with no progress**: worker may be down — check `docker ps` and worker logs.
- **TimerStarted as last event**: workflow is sleeping between poll cycles — normal, wait for it to fire.
- **ActivityTaskFailed / WorkflowTaskFailed**: read the failure details in the event history JSON output (`--output json`).
- **"workflow not found"**: always retry with `--namespace default` — most workflows run in the `default` namespace.
- **Child workflow `COMPLETED` but result carries `failureClass` / non-zero exit summary**: Temporal executed successfully; inspect agent stdout/diagnostics artifacts and worker logs, not only workflow history.

## Tool Execution Guardrails
- **Strict Verification of Tool Results**: Never hallucinate success or fabricate data when a tool execution fails. If a tool (e.g., `read_file`, `run_shell_command`) returns an error such as 'File not found', you must correctly identify the failure and take appropriate remediating action instead of silently bypassing it.

## Tool Usage
- **Heredocs in `run_shell_command`**: Explicitly forbid the use of bare heredocs (e.g. `<< 'EOF' > file.md`) in `run_shell_command`. You MUST use `cat << 'EOF' > file.md` or the `write_file` tool to prevent Bash parsing errors and subsequent artifact gaps.

## Security Guardrails
- Never post or commit raw credentials (tokens, API keys, passwords, private keys, cookies, auth headers, session IDs).
- Never paste full `docker compose` output, `.env` files, or environment/config dumps into PR comments. Summarize and redact.
- Before posting any PR/issue/review comment, scan the outgoing text for secret-like patterns (`ghp_`, `github_pat_`, `AIza`, `ATATT`, `AKIA`, private key blocks, `token=`/`password=` assignments) and block posting on any match.
- If secrets are observed in comments, logs, or commits: stop, redact/delete the exposed content when possible, and rotate affected credentials immediately.
- Repo and local skill sources are potentially *untrusted input*. Implementations must respect deployment policy on whether those sources are allowed and must not silently assume repo/local skills are always enabled.

## Compatibility Policy
- MoonMind is a **pre-release project** (see Constitution Principle XIII). Do NOT introduce compatibility aliases, translation layers, or backward-compat wrappers for internal contracts. When a pattern is superseded, **remove the old version entirely** in the same change.
- When refactoring an activity name, model, or interface: grep the entire codebase, update every caller, test, mock, and doc reference, and delete the old artifact. Partial migrations are not acceptable.
- Never introduce compatibility transforms that change execution semantics or billing-relevant values (for example model identifiers, effort values, queue semantics, or publish behavior).
- Prefer fail-fast behavior for unsupported runtime input values over hidden fallback behavior.
- For Codex execution specifically, `codex.model` and `codex.effort` inputs must be passed through exactly as provided. Unsupported values must fail through normal CLI/API validation.
- For Temporal-facing contracts specifically, treat workflow/activity/update/signal payload shapes as compatibility-sensitive. Signature or schema changes MUST preserve worker-bound invocation compatibility for in-flight runs, or be versioned with an explicit migration/cutover plan.

## Shared Agent Skills Runtime
- **Target-State Model**: MoonMind resolves and materializes one per-run active skill set, exposing it to agents through adapter boundaries.
- **Canonical Active Path**: `.agents/skills` is the canonical runtime-visible path. It contains the **resolved active snapshot** for the run, not a mutable source-of-truth folder.
- **Immutable Source Protection**: Do not rewrite checked-in skill folders in place as part of runtime setup. Generate or project the active skill set separately, then expose it through the canonical active path.
- **Local Overlay Source**: `.agents/skills/local` is a valid *local-only input/overlay path*. It is **not** the authoritative durable storage model for MoonMind-managed skills and should not be treated as the canonical source of truth.
- **Adapter Mappings**: `skills_active` (or its equivalent run-scoped active directory) contains the **resolved immutable active skill set for the run**. Adapters traditionally map `.agents/skills -> ../skills_active` or `.gemini/skills -> ../skills_active` to link workflows to the snapshot. Checked-in repo skills and local-only skills are merely *inputs* to this resolution.
- **Environment Targeting**: Prefer configuring `WORKFLOW_SKILLS_WORKSPACE_ROOT` and `WORKFLOW_SKILLS_CACHE_ROOT` to point to writable paths intended specifically for storing resolved active skill snapshots and related runtime materialization artifacts (these mounts are not arbitrary mutable replacements for the canonical design).

## Active Technologies
- Python 3.12 + Pydantic v2, Temporal Python SDK, pytest, existing MoonMind Temporal workflow test helpers (176-temporal-type-gates)
- No new persistent storage; review findings are produced as deterministic validation output and test evidence (176-temporal-type-gates)
- Python 3.12 + YAML seed templates + Pydantic v2, FastAPI/MCP tool registry, `httpx`, existing Jira integration service, Temporal story-output tool handlers, task preset catalog (177-jira-chain-blockers)
- No new persistent storage; deterministic outputs carry issue mappings and link results (177-jira-chain-blockers)
- Python 3.12 + Temporal Python SDK, Pydantic v2, existing MoonMind workflow/activity catalog, existing GitHub/Jira trusted integration surfaces, pr-resolver skill (179-merge-gate)
- Existing Temporal workflow history, Search Attributes, Memo, and existing execution/projection records; no new persistent database tables planned (179-merge-gate)
- Python 3.12 + Pydantic v2, existing MoonMind schema validation helpers (185-claude-policy-envelope)
- No new persistent storage; this story defines compact runtime contracts and deterministic outputs that can later be persisted by the managed-session store (185-claude-policy-envelope)
- No new persistent storage; this story defines compact runtime contracts and deterministic outputs that can later be persisted by the managed-session store or export sinks (191-claude-governance-telemetry)
- Python 3.12 + Pydantic v2, Temporal Python SDK, pytest, existing OAuth provider registry, existing OAuth session workflow/activity catalog, existing terminal bridge runtime helpers (192-oauth-runner-bootstrap-pty)
- Existing OAuth session database row and workflow/activity payloads only; no new persistent storage (192-oauth-runner-bootstrap-pty)
- Python 3.12; TypeScript/React for existing Create page tests if frontend behavior changes + Pydantic v2, FastAPI, SQLAlchemy async session fixtures, existing Temporal execution router/service, React/Vitest test harness (195-targeted-image-attachment-submission)
- Existing Temporal execution records and artifact-backed original task input snapshots; no new persistent tables (195-targeted-image-attachment-submission)
- TypeScript/React for Mission Control UI, Python 3.12 for FastAPI route tests + React, FastAPI, existing boot payload helpers, existing task dashboard router, Vitest, pytest (195-canonical-create-page-shell)
- Python 3.12; TypeScript/React for existing Create-page behavior + FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal artifact service, existing React Create page (195-enforce-image-artifact-policy)
- Existing Temporal artifact metadata tables and configured artifact store; no new persistent storage (195-enforce-image-artifact-policy)
- Python 3.12; TypeScript/React for Mission Control Create-page behavior + FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal artifact service, React, Vitest, existing task editing helpers (196-preserve-attachment-bindings)
- Existing Temporal artifact metadata tables and original task input snapshot artifacts; no new persistent storage (196-preserve-attachment-bindings)
- Python 3.12; TypeScript/React + FastAPI dashboard runtime config helpers, Pydantic settings, `httpx`, React, Vitest, pytest (203-repository-dropdown)
- No new persistent storage; options are derived from configuration and best-effort GitHub API responses at runtime (203-repository-dropdown)
- Python 3.12 + Pydantic v2, Temporal Python SDK, existing MoonMind Jira tool service, existing artifact and workflow activity catalogs (205-post-merge-jira-completion)
- Existing Temporal workflow history, memo/search attributes, and artifact outputs; no new persistent database tables planned (205-post-merge-jira-completion)
- Python 3.12 with Pydantic v2 models, SQLAlchemy async ORM, Temporal Python SDK activity boundaries + Pydantic v2, SQLAlchemy async session fixtures, Temporal activity wrappers, existing agent-skill resolver/materializer services (206-agent-skill-catalog-source-policy)
- Existing agent skill tables and artifact-backed version content; no new persistent tables planned (206-agent-skill-catalog-source-policy)
- TypeScript/React for Mission Control UI; CSS for shared Mission Control styling; Python 3.12 remains present but is not expected in this story + React, TanStack Query, existing Create page entrypoint, existing Mission Control stylesheet, Vitest and Testing Library (210-liquid-glass-panel)
- No new persistent storage; existing task draft and submission payload state only (210-liquid-glass-panel)
- TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected in this story + React, TanStack Query, existing Settings entrypoint, Vitest, Testing Library (226-route-claude-auth-actions)
- No new persistent storage; uses existing provider profile row metadata and optional command/readiness data (226-route-claude-auth-actions)
- Python 3.12 + FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK (226-canonical-remediation-submissions)
- Existing SQLAlchemy/Alembic database with `execution_remediation_links` already present (226-canonical-remediation-submissions)
- Python 3.12 + SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK service boundaries, existing Temporal artifact service, existing remediation context/action services (232-remediation-lifecycle-audit)
- Existing Temporal execution records, `execution_remediation_links`, Temporal artifact metadata/content store, and existing execution memo/search/projection paths; no new persistent database table planned unless audit events cannot reuse an existing control-event mechanism (232-remediation-lifecycle-audit)
- TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected in this story + React, Vitest, Testing Library, existing Mission Control stylesheet, existing entrypoint render tests (244-shimmer-sweep-status-pill)
- Python 3.12 + FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, pytest, existing OAuth session/terminal bridge/runtime-launch services (245-claude-oauth-guardrails)
- Existing OAuth session rows, provider-profile rows, managed-session diagnostics, artifact metadata, and workflow history; no new persistent tables planned (245-claude-oauth-guardrails)
- Python 3.12 and TypeScript/React for existing Mission Control verification surfaces + Pydantic v2, Temporal Python SDK, SQLAlchemy async ORM, existing temporal artifact service/helpers, React, Vitest (245-publish-report-bundles)
- Existing temporal artifact metadata tables and configured artifact store; no new persistent storage (245-publish-report-bundles)

## Recent Changes
- 176-temporal-type-gates: Added Python 3.12 + Pydantic v2, Temporal Python SDK, pytest, existing MoonMind Temporal workflow test helpers
