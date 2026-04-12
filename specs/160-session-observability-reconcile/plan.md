# Implementation Plan: Managed Session Observability and Reconcile

**Branch**: `160-session-observability-reconcile` | **Date**: 2026-04-12 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `specs/160-session-observability-reconcile/spec.md`

## Summary

Add runtime-owned observability and recurring recovery for the Codex managed session plane. The implementation will enrich `MoonMind.AgentSession` and its parent session start path with bounded operator metadata, add readable activity summaries for session controls, preserve strict separation between workflow processing and Docker/runtime activity work, and provide a Temporal Schedule target that invokes managed-session reconciliation with a bounded outcome.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Temporal Python SDK, Pydantic managed-session schemas, MoonMind Temporal activity catalog/worker topology, Docker-backed managed-session controller, pytest  
**Storage**: Temporal workflow history/visibility metadata for bounded operator fields; JSON-backed `ManagedSessionStore` as operational recovery index; artifact refs remain the durable continuity evidence  
**Testing**: Focused pytest coverage for managed-session workflow visibility, activity routing/summaries, worker registration, schedule wiring, and activity/controller boundary; final verification via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`  
**Target Platform**: Docker/Compose-hosted MoonMind Temporal workers and task-scoped managed Codex session containers  
**Project Type**: Backend runtime/workflow observability and operational recovery  
**Performance Goals**: Visibility updates must stay bounded and reference-sized; recurring reconcile results must remain compact even when many records are inspected; runtime activity routing must not add workflow-worker Docker privileges  
**Constraints**: Runtime mode only; deliver production runtime code and validation tests; no docs/spec-only completion; do not put prompts, transcripts, scrollback, raw logs, credentials, or secrets in workflow visibility, schedule metadata, or activity summaries; keep Docker/runtime work on the agent-runtime activity boundary  
**Scale/Scope**: One task-scoped Codex managed session per task; no cross-task session reuse, frontend UI redesign, multi-runtime session-plane expansion, standalone image path, or canonical-doc migration in this feature

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan adds orchestration metadata and recovery around Codex sessions without reimplementing Codex behavior.
- **II. One-Click Agent Deployment**: PASS. The recurring reconcile path uses existing Temporal worker and schedule capabilities; no new external service or secret is required.
- **III. Avoid Vendor Lock-In**: PASS. Codex-specific managed-session visibility remains isolated to the Codex session plane and existing runtime/controller boundaries.
- **IV. Own Your Data**: PASS. Operator metadata, artifacts, and recovery records remain in MoonMind-controlled Temporal/artifact/store surfaces.
- **V. Skills Are First-Class and Easy to Add**: PASS. Agent skill discovery, resolution, and materialization paths are not changed.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The work strengthens existing workflow/activity contracts instead of creating a parallel observability stack.
- **VII. Powerful Runtime Configurability**: PASS. Queue names, worker topology, schedule cadence, and runtime behavior remain controlled by existing Temporal settings and schedule configuration.
- **VIII. Modular and Extensible Architecture**: PASS. Changes are scoped to managed-session workflow, agent-run launch path, activity catalog/runtime, worker registration, client schedule helper, and tests.
- **IX. Resilient by Default**: PASS. Recurring reconcile and bounded degraded-state visibility improve unattended recovery and diagnostics.
- **X. Facilitate Continuous Improvement**: PASS. Operators get structured phase and continuity metadata to answer what happened without raw internals.
- **XI. Spec-Driven Development**: PASS. This plan derives from the active spec and preserves runtime deliverable/test requirements.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Planning remains under `specs/`; no canonical docs are turned into rollout logs.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. No compatibility alias, deprecated activity name, or fallback contract is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/160-session-observability-reconcile/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── managed-session-observability-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
└── workflows/
    └── temporal/
        ├── activity_catalog.py
        ├── activity_runtime.py
        ├── client.py
        ├── worker_entrypoint.py
        ├── worker_runtime.py
        ├── workers.py
        └── workflows/
            ├── agent_run.py
            ├── agent_session.py
            ├── managed_session_reconcile.py
            └── run.py

tests/
└── unit/
    └── workflows/
        └── temporal/
            ├── test_agent_runtime_activities.py
            ├── test_client_schedules.py
            ├── test_temporal_worker_runtime.py
            ├── test_temporal_workers.py
            └── workflows/
                ├── test_agent_session.py
                └── test_run_codex_sessions.py
```

**Structure Decision**: Use the existing backend Temporal workflow/activity/client/worker topology and unit-test layout. This feature does not add a frontend surface, new service, new storage engine, or canonical documentation migration.

## Phase 0: Research

Research decisions are captured in [research.md](./research.md).

Key decisions:

1. Use Temporal workflow UI metadata and indexed visibility fields only for bounded operator state.
2. Keep Search Attributes to the six requested fields and treat all prompts/logs/transcripts/secrets as forbidden visibility data.
3. Generate activity summaries at the workflow scheduling boundary so history is readable without inspecting payloads.
4. Preserve runtime/Docker work on the `agent_runtime` activity fleet and keep workflow workers privilege-light.
5. Model recurring recovery as a Temporal Schedule target workflow that delegates side effects to an agent-runtime activity.

## Phase 1: Design

Design outputs:

- [data-model.md](./data-model.md)
- [contracts/managed-session-observability-contract.md](./contracts/managed-session-observability-contract.md)
- [quickstart.md](./quickstart.md)

Implementation surfaces:

1. Update `MoonMind.AgentSession` to set current details and upsert bounded Search Attributes at session start and each major transition.
2. Update the task-scoped session start path in `MoonMind.Run` to provide static summary/details and initial bounded Search Attributes when creating the child session workflow.
3. Add readable activity summaries for launch and session control activities without including instructions, logs, or credentials.
4. Add `agent_runtime.reconcile_managed_sessions` to the activity catalog/runtime binding on the agent-runtime fleet.
5. Add a `MoonMind.ManagedSessionReconcile` workflow and register it with workflow workers.
6. Add/update Temporal client schedule wiring to create or update the recurring managed-session reconcile schedule.
7. Add focused tests across workflow visibility, schedule wiring, activity routing, worker registration, and bounded reconcile output.

## Post-Design Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The design observes and recovers managed sessions through MoonMind orchestration boundaries only.
- **II. One-Click Agent Deployment**: PASS. No deployment prerequisite or mandatory external service is added.
- **III. Avoid Vendor Lock-In**: PASS. Codex-specific session-plane metadata stays isolated to existing Codex managed-session surfaces.
- **IV. Own Your Data**: PASS. Visibility fields and recovery records stay in operator-controlled Temporal/artifact/store systems.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill runtime paths are untouched.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The design adds small, explicit workflow/activity/schedule contracts.
- **VII. Powerful Runtime Configurability**: PASS. Existing queue topology and schedule configuration remain authoritative.
- **VIII. Modular and Extensible Architecture**: PASS. Runtime observability and reconcile logic remain in existing Temporal modules.
- **IX. Resilient by Default**: PASS. Bounded degraded-state metadata and recurring reconcile directly improve recovery.
- **X. Facilitate Continuous Improvement**: PASS. Operators get compact evidence for troubleshooting and later hardening.
- **XI. Spec-Driven Development**: PASS. Design artifacts map to the active spec requirements and success criteria.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. No canonical docs are modified for rollout tracking.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. No legacy alias or compatibility wrapper is added.

## Complexity Tracking

No constitution violations. No additional complexity exceptions required.
