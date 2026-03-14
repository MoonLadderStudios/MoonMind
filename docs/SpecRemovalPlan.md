# Spec Removal Plan (Completed Codebase Migration)

## Purpose

This document formerly outlined how to remove legacy `SPEC`-style naming from documentation and specs. It has since been executed and expanded to encompass the entire codebase (including environment variables, settings, Python models, API contracts, and database migrations).

## Scope

1. `docs/` markdown files (completed)
2. `specs/` markdown files and contract assets (completed)
3. Python codebase, API definitions, environment configuration, database models, and Alembic migrations (completed).

## Hard Rules for this migration pass

1. Canonical naming must be used consistently after the edit pass.
2. No new aliasing language (e.g., “old name/new alias”) should be introduced in operational docs; only one canonical term is used.
3. Legacy names are preserved only in a dedicated appendix when needed for historical traceability.
4. Any schema/API/contract names changed in docs must map to explicit canonical decisions and migration follow-up actions.
5. This plan must remain confined to planning assets under `docs/` and `specs/`.

## Current legacy footprint (for planning, not implementation)

1. Legacy surface command used for discovery: 

```
rg -l "SPEC_WORKFLOW_|spec_workflow|/api/spec-automation|spec-automation|Speckit|SpecWorkflow|spec_workflows|/api/workflows/speckit|SPEC_AUTOMATION_" docs specs --glob '*.md'
```

2. Current footprint counts:

`docs`: 10 files

`specs`: 66 files

3. API contract and schema assets identified for this pass:

`specs/002-document-speckit-automation/contracts/workflow.openapi.yaml`

`specs/001-celery-chain-workflow/contracts/workflow.openapi.yaml`

`specs/005-orchestrator-architecture/contracts/orchestrator.openapi.yaml` (artifact path references)

## Canonical naming target (to replace legacy terms)

1. Config/env naming: `SPEC_WORKFLOW_*` -> `WORKFLOW_*`
2. Settings path naming: `spec_workflow` -> `workflow`
3. API contract naming: `/api/spec-automation/*` and `/api/workflows/speckit/*` -> `/api/workflows/*` where it represents the same canonical run surface.
4. Data model naming in docs/contracts: `SpecWorkflow*` -> `Workflow*`
5. Metrics prefixes: `moonmind.spec_workflow` -> `moonmind.workflow`
6. Artifact location naming: `var/artifacts/spec_workflows` -> `var/artifacts/workflow_runs` or `var/artifacts/workflows` depending on folder conventions in the target document.

## Workstreams and sequencing

1. Discovery and freeze
1. Lock the legacy token map above and freeze the canonical replacements.
1. Capture a full inventory by file for review before any edits.

2. High-signal docs update pass
1. Update strategy/architecture/runbook docs where naming appears in operational guidance and troubleshooting behavior.
1. Update quickstarts so all command examples and API snippets use canonical names.
1. Remove legacy aliases from explanatory text and table headings.

3. Spec artifact pass
1. Update spec `spec.md`, `plan.md`, `tasks.md`, `research.md`, and `contracts/*` in the same canonical style.
1. Update contract references to operation IDs and schema names where required for consistency.
1. Normalize all `Task`/`Run` naming in these documents to canonical workflow models.

4. Verification pass
1. Re-run discovery to confirm legacy tokens are removed except where intentionally held in the historical appendix.
1. Confirm no new `SPEC_*` config/API/DB path wording appears in any changed file.
1. Produce a delta report in the spec runbook or plan entry.

## Required canonical updates by surface type

1. Environment and settings surface
1. Replace every `SPEC_WORKFLOW_CODEX_QUEUE` reference with `WORKFLOW_CODEX_QUEUE`.
1. Replace every `SPEC_WORKFLOW_TEST_MODE` reference with `WORKFLOW_TEST_MODE`.
1. Replace every `SPEC_WORKFLOW_ALLOWED_SKILLS`, `SPEC_WORKFLOW_DEFAULT_SKILL`, `SPEC_WORKFLOW_USE_SKILLS` with `WORKFLOW_ALLOWED_SKILLS`, `WORKFLOW_DEFAULT_SKILL`, `WORKFLOW_USE_SKILLS`.
1. Replace every `SPEC_WORKFLOW_ARTIFACT_ROOT` with `WORKFLOW_ARTIFACT_ROOT`.
1. Replace every `SPEC_WORKFLOW_METRICS_*` with `WORKFLOW_METRICS_*` and deprecate `spec_automation.*` references where they appear.

2. API contract surface
1. Replace legacy route families with canonical workflow routes in API docs.
1. Replace `/api/spec-automation/*` endpoint references with `/api/workflows/*`.
1. Replace `/api/workflows/speckit/*` endpoint references with canonical `/api/workflows/*`.
1. Rename contract schema names where they currently include `SpecWorkflow*` to `Workflow*`.
1. Update operation IDs from `createWorkflowRun`, `listWorkflowRuns`, etc. to canonical workflow equivalents.

3. Data model and schema surface
1. Replace `spec_workflow_runs` and `spec_workflow_task_states` mentions with canonical workflow equivalents in docs and plans.
1. Replace references to `spec_workflow.*` settings references with `workflow.*`.
1. Replace `spec_input` or similar legacy identifiers in contract examples with neutral terms if they are not domain-required by an external spec.

4. Runtime/observability wording
1. Replace `moonmind.spec_workflow` metric namespace with `moonmind.workflow`.
1. Replace artifact roots described as `var/artifacts/spec_workflows` with `var/artifacts/workflow_runs` or `var/artifacts/workflows` per document’s naming schema.
1. Remove legacy “compatibility route” prose from operational docs, except the dedicated migration log.

## Per-directory file list for implementation planning

### `docs`

`docs/CodexCliWorkers.md`

`docs/LiveTaskHandoff.md`

`docs/LlamaIndexManifestSystem.md`

`docs/MemoryArchitecture.md`

`docs/OrchestratorArchitecture.md`

`docs/SpecKitAutomation.md`

`docs/SpecKitAutomationInstructions.md`

`docs/TaskQueueSystem.md`

`docs/TasksJira.md`

`docs/TasksStepSystem.md`

`docs/ops-runbook.md`

### `specs/001-celery-chain-workflow`

`specs/001-celery-chain-workflow/data-model.md`

`specs/001-celery-chain-workflow/plan.md`

`specs/001-celery-chain-workflow/quickstart.md`

`specs/001-celery-chain-workflow/research.md`

`specs/001-celery-chain-workflow/spec.md`

`specs/001-celery-chain-workflow/tasks.md`

`specs/001-celery-chain-workflow/contracts/workflow.openapi.yaml`

### `specs/002-document-speckit-automation`

`specs/002-document-speckit-automation/AGENTS.md`

`specs/002-document-speckit-automation/plan.md`

`specs/002-document-speckit-automation/quickstart.md`

`specs/002-document-speckit-automation/spec.md`

`specs/002-document-speckit-automation/tasks.md`

`specs/002-document-speckit-automation/contracts/workflow.openapi.yaml`

### `specs/003-celery-oauth-volumes`

`specs/003-celery-oauth-volumes/quickstart.md`

`specs/003-celery-oauth-volumes/tasks.md`

### `specs/005-orchestrator-architecture`

`specs/005-orchestrator-architecture/data-model.md`

`specs/005-orchestrator-architecture/plan.md`

`specs/005-orchestrator-architecture/quickstart.md`

`specs/005-orchestrator-architecture/research.md`

`specs/005-orchestrator-architecture/spec.md`

`specs/005-orchestrator-architecture/tasks.md`

`specs/005-orchestrator-architecture/contracts/orchestrator.openapi.yaml`

### `specs/007-scalable-codex-worker`

`specs/007-scalable-codex-worker/checklists/requirements.md`

`specs/007-scalable-codex-worker/data-model.md`

`specs/007-scalable-codex-worker/plan.md`

`specs/007-scalable-codex-worker/quickstart.md`

`specs/007-scalable-codex-worker/research.md`

`specs/007-scalable-codex-worker/spec.md`

`specs/007-scalable-codex-worker/tasks.md`

### `specs/008-gemini-cli-worker`

`specs/008-gemini-cli-worker/tasks.md`

### `specs/009-agent-queue-mvp`

`specs/009-agent-queue-mvp/research.md`

### `specs/011-remote-worker-daemon`

`specs/011-remote-worker-daemon/contracts/codex-worker-runtime-contract.md`

`specs/011-remote-worker-daemon/contracts/requirements-traceability.md`

`specs/011-remote-worker-daemon/data-model.md`

`specs/011-remote-worker-daemon/plan.md`

`specs/011-remote-worker-daemon/quickstart.md`

`specs/011-remote-worker-daemon/research.md`

`specs/011-remote-worker-daemon/spec.md`

`specs/011-remote-worker-daemon/tasks.md`

### `specs/015-skills-workflow`

`specs/015-skills-workflow/contracts/compose-fast-path.md`

`specs/015-skills-workflow/data-model.md`

`specs/015-skills-workflow/plan.md`

`specs/015-skills-workflow/research.md`

`specs/015-skills-workflow/spec.md`

`specs/015-skills-workflow/tasks.md`

### `specs/016-shared-agent-skills`

`specs/016-shared-agent-skills/contracts/shared-skills-workspace-contract.md`

`specs/016-shared-agent-skills/quickstart.md`

### `specs/018-unified-cli-queue`

`specs/018-unified-cli-queue/contracts/worker-runtime-contract.md`

`specs/018-unified-cli-queue/contracts/requirements-traceability.md`

`specs/018-unified-cli-queue/plan.md`

`specs/018-unified-cli-queue/research.md`

`specs/018-unified-cli-queue/spec.md`

`specs/018-unified-cli-queue/tasks.md`

### `specs/031-manifest-phase0`

`specs/031-manifest-phase0/plan.md`

`specs/031-manifest-phase0/tasks.md`

### `specs/034-task-proposal-update`

`specs/034-task-proposal-update/plan.md`

`specs/034-task-proposal-update/research.md`

`specs/034-task-proposal-update/tasks.md`

### `specs/034-worker-self-heal`

`specs/034-worker-self-heal/quickstart.md`

`specs/034-worker-self-heal/research.md`

### `specs/036-isolate-speckit-references`

`specs/036-isolate-speckit-references/contracts/skill-adapter-contract.md`

`specs/036-isolate-speckit-references/contracts/workflow-runs-api.md`

`specs/036-isolate-speckit-references/data-model.md`

`specs/036-isolate-speckit-references/plan.md`

`specs/036-isolate-speckit-references/quickstart.md`

`specs/036-isolate-speckit-references/research.md`

`specs/036-isolate-speckit-references/spec.md`

`specs/036-isolate-speckit-references/tasks.md`

### `specs/037-tasks-image-phase1`

`specs/037-tasks-image-phase1/data-model.md`

`specs/037-tasks-image-phase1/plan.md`

`specs/037-tasks-image-phase1/tasks.md`

### `specs/038-claude-runtime-gate`

`specs/038-claude-runtime-gate/contracts/task_dashboard_config.md`

`specs/038-claude-runtime-gate/data-model.md`

`specs/038-claude-runtime-gate/plan.md`

`specs/038-claude-runtime-gate/research.md`

`specs/038-claude-runtime-gate/tasks.md`

## Deliverable checklist

1. `docs/SpecRemovalPlan.md` created in this branch.
2. All references in the listed files are rephrased to canonical terms.
3. A verification report is attached to the spec/plan describing residual legacy references only allowed in explicitly labeled historical sections.
4. No changes to code files, config files, or runtime assets are required for this planning-only pass.

## Acceptance criteria for this planning pass

1. A follow-up grep within `docs/` and `specs/` finds no unintentional legacy surface usage outside the migration exception zone.
2. Every API contract/documented endpoint uses the canonical workflow route naming.
3. All `SPEC_WORKFLOW_*` and `spec_workflow*` naming in docs/specs is replaced according to the canonical mapping, except for historical trace sections.
4. The plan includes a complete list of any intentionally retained legacy mentions for later execution work.

## Verification report (2026-02-24)

### Legacy-token verification

- Executed scan command:
  - `rg -l "SPEC_WORKFLOW_|SPEC_AUTOMATION_|/api/spec-automation|/api/workflows/speckit|SpecWorkflow|spec_workflow|spec_workflows|spec-automation|spec_automation|moonmind\\.spec_workflow|var/artifacts/spec_workflows" docs specs --glob '*.md' --glob '*.yaml' --glob '*.yml' -g '!specs/040-spec-removal/**'`
- Result: intentional legacy-token references remain in `docs/SpecRemovalPlan.md` and `specs/040-spec-removal/*` as migration context.
- Result count outside intentional exception files: `0`.

### Historical references retained

- `docs/SpecRemovalPlan.md` retains legacy terms solely as migration context and cannot be interpreted as runtime contract expectations.
- `specs/040-spec-removal/*` retains legacy terms intentionally to describe source/target mappings, acceptance checks, and traceability for this migration feature.

## Verification report (2026-03-14 - Codebase Execution)

The full codebase migration has been completed.
- Renamed all `/api/spec-automation/*` routes to `/api/workflows/*`.
- Renamed `SpecWorkflowRuns` to `WorkflowRuns` and generated Alembic migrations using `op.rename_table()`.
- Replaced all configuration variables (`SPEC_WORKFLOW_CODEX_MODEL` -> `MOONMIND_CODEX_MODEL`, `SPEC_WORKFLOW_*` -> `WORKFLOW_*`).
- The entire Docker unit test suite passes after the renaming.
