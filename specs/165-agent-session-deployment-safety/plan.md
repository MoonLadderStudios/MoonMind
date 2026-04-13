# Implementation Plan: Agent Session Deployment Safety

**Branch**: `165-agent-session-deployment-safety` | **Date**: 2026-04-13 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/165-agent-session-deployment-safety/spec.md`

## Summary

Finish the remaining runtime work needed to make Codex managed session workflow changes safe to operate and deploy. The implementation is runtime-mode work: production workflow, runtime/controller, worker deployment, reconciliation, observability, and validation-test changes are required deliverables; docs/spec-only changes are insufficient. The delayed standalone-image path remains out of scope, and the implementation process must stay test-driven.

The current repository already contains substantial Phase 3-5 surfaces, including typed `AgentSessionWorkflow` updates, runtime steering/termination hooks, workflow handler hardening, bounded visibility metadata, managed-session reconcile workflow, and worker deployment configuration. This plan treats those as implementation candidates to verify, preserve, and close any gaps against the spec rather than reimplementing completed behavior blindly.

Traceability status: this feature has no `DOC-REQ-*` identifiers. No `contracts/requirements-traceability.md` artifact is required for this spec, and implementation traceability is maintained through FR-to-task coverage in `tasks.md` and `speckit_analyze_report.md`.

## Technical Context

**Language/Version**: Python 3.12 for Temporal workflows, activities, runtime services, and tests; TypeScript only if an affected operator/API surface needs payload or visibility-field handling.
**Primary Dependencies**: Temporal Python SDK, pytest, Pydantic managed-session schemas, existing Codex managed runtime/controller/supervisor/store modules, Docker-backed managed session activities, OpenTelemetry/logging integration, existing Temporal client/schedule helpers.
**Storage**: Temporal workflow history and visibility metadata; artifact refs for operator/audit truth; JSON-backed `ManagedSessionStore` as the operational recovery index; container-local state as disposable runtime cache only.
**Testing**: Test-driven development is required. Add or update targeted pytest/workflow replay/integration coverage before relying on production runtime changes as complete; use `./tools/test_unit.sh` for required unit verification; use `./tools/test_integration.sh` only for affected hermetic integration-ci seams.
**Target Platform**: MoonMind backend/runtime workers in Docker Compose with Temporal service, managed Codex runtime containers, and separated workflow/activity task queues.
**Project Type**: Backend/runtime workflow reliability and deployment-safety feature.
**Performance Goals**: No leaked managed-session containers after terminate; no unbounded prompts/logs/transcripts in visibility or summaries; Continue-As-New prevents unbounded workflow history growth; reconcile remains bounded over stale/degraded records.
**Constraints**: Runtime mode is mandatory; delayed standalone-image delivery is excluded; heavy side effects stay outside workflow code; secrets and unbounded provider output must not enter indexed visibility, summaries, telemetry dimensions, or replay fixtures; incompatible workflow evolution requires Worker Versioning, patching, or explicit cutover; docs-only completion is not acceptable.
**Scale/Scope**: One task-scoped Codex managed session per task run; no cross-task session reuse, generic runtime marketplace, or Claude/Gemini managed session plane expansion.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. Codex remains an orchestrated managed runtime behind workflow/activity/controller boundaries; MoonMind does not recreate agent cognition.
- **II. One-Click Agent Deployment**: PASS. The rollout preserves Docker Compose/local-first operation and does not introduce mandatory external cloud dependencies or standalone-image scope.
- **III. Avoid Vendor Lock-In**: PASS. Codex-specific behavior stays behind managed-runtime adapter/controller surfaces, and the plan does not make core orchestration Codex-only.
- **IV. Own Your Data**: PASS. Artifacts, bounded workflow metadata, and managed-session recovery records remain operator-controlled data surfaces.
- **V. Skills Are First-Class and Easy to Add**: PASS. This feature does not alter executable tool contracts or agent instruction bundles, and it preserves runtime-neutral workflow boundaries where applicable.
- **VI. The Bittersweet Lesson**: PASS. Test-driven validation and replay gates keep the scaffolding replaceable while preserving objective evidence.
- **VII. Powerful Runtime Configurability**: PASS. Worker Versioning, task queues, and runtime behavior remain controlled by explicit configuration and observable metadata.
- **VIII. Modular and Extensible Architecture**: PASS. Changes are scoped to workflow, activity, runtime/controller, worker, and validation modules with clear contracts.
- **IX. Resilient by Default**: PASS. The plan requires idempotent side effects, deterministic workflow behavior, failure classification, replay coverage, and lifecycle cleanup.
- **X. Facilitate Continuous Improvement**: PASS. The feature preserves structured terminal outcomes, diagnostics, artifact refs, and validation evidence for future improvement.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Spec, plan, tasks, contracts, and validation gates are maintained before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Long-lived docs remain declarative while cutover/playbook material stays under `docs/tmp/`.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. Legacy bridge behavior is scoped to replay/cutover needs with removal expectations rather than indefinite compatibility aliases.

No complexity violations are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/165-agent-session-deployment-safety/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
├── contracts/
│   └── agent-session-deployment-safety.md
└── tasks.md                # Generated by speckit-tasks, not this plan step
```

### Runtime Source Code

```text
moonmind/schemas/
└── managed_session_models.py

moonmind/workflows/temporal/
├── activity_catalog.py
├── activity_runtime.py
├── client.py
├── worker_runtime.py
├── workers.py
├── runtime/
│   ├── codex_session_runtime.py
│   ├── managed_session_controller.py
│   ├── managed_session_store.py
│   └── managed_session_supervisor.py
└── workflows/
    ├── agent_session.py
    ├── managed_session_reconcile.py
    └── run.py
```

### Validation Source Code

```text
tests/unit/workflows/temporal/
├── test_agent_runtime_activities.py
├── test_agent_session_replayer.py
├── test_client_schedules.py
├── test_temporal_worker_runtime.py
└── workflows/
    ├── test_agent_session.py
    └── test_run_codex_sessions.py

tests/unit/services/temporal/runtime/
├── test_codex_session_runtime.py
└── test_managed_session_controller.py

tests/integration/services/temporal/workflows/
└── test_agent_session_lifecycle.py
```

**Structure Decision**: Use the existing backend/runtime workflow layout. This is not a new app or service; implementation should close gaps inside the current Temporal workflow, activity, controller, runtime, client, worker, and test modules.

## Phase 0: Research

1. Verify the existing managed-session implementation against FR-001 through FR-028 before adding code. Mark already-compliant behavior in tasks as verification/test work instead of duplicating runtime logic.
2. Confirm the production mutation surface is typed workflow Updates for request/response controls and Signals only for fire-and-forget state propagation.
3. Confirm terminate/cancel/clear/interrupt/steer behavior is implemented at the workflow boundary and at the runtime/controller side-effect boundary.
4. Confirm handler locking, readiness waits, handler drain, and Continue-As-New carry-forward happen in workflow-safe code paths.
5. Confirm bounded metadata, Search Attributes, activity summaries, schedule metadata, telemetry dimensions, and replay fixtures exclude prompts, transcripts, scrollback, raw logs, credentials, and secrets.
6. Confirm Worker Versioning, patching/cutover guidance, and replay tests are present for incompatible workflow-shape changes.
7. Confirm validation is added or updated before production runtime changes are treated as complete.

**Output**: [research.md](./research.md)

## Phase 1: Design

1. Model the managed-session entities that cross durable workflow, runtime side-effect, recovery, and operator/audit boundaries.
2. Define the workflow/update/activity/reconcile/deployment contract for the production control plane.
3. Provide a quickstart focused on gap auditing and validation commands for runtime implementation.

**Outputs**:

- [data-model.md](./data-model.md)
- [contracts/agent-session-deployment-safety.md](./contracts/agent-session-deployment-safety.md)
- [quickstart.md](./quickstart.md)

## Implementation Approach

1. **TDD first**: Add or update the smallest workflow, runtime, controller, replay, or integration regression that proves the missing or changed behavior before treating the production change as complete.
2. **Gap audit**: Compare current `agent_session.py`, managed runtime/controller, worker runtime, client schedules, replay tests, and lifecycle tests against each functional requirement.
3. **Control API parity**: Ensure production mutators are typed Updates with validators or deterministic pre-mutation rejection, and only state propagation remains signal-based.
4. **Lifecycle semantics**: Ensure terminate waits for runtime cleanup and supervision finalization, while cancel stops active work without destroying the session.
5. **Runtime side-effect safety**: Make retryable activity/controller boundaries idempotent, classify permanent failures explicitly, and add heartbeat/cancellation handling for meaningful blocking work.
6. **Long-lived workflow hardening**: Preserve lock-protected mutators, readiness gates, handler drain, Continue-As-New from the main workflow path, and bounded carry-forward state.
7. **Operational separation**: Keep artifact/controller/supervisor publication as the production operator/audit path; use the session store for recovery/reconcile; treat container-local helpers as fallback-only.
8. **Observability/reconcile**: Maintain bounded UI/current details, Search Attributes, summaries, telemetry correlation, worker/task-queue separation, and scheduled reconcile.
9. **Deployment safety**: Require Worker Versioning, patching or explicit versioned cutover, replay validation, and cutover playbooks before incompatible workflow-shape rollout.
10. **Validation**: Run credential-free validation for required gates and broaden to hermetic integration only when affected seams require it.

## Post-Design Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The design continues to orchestrate Codex through managed runtime boundaries.
- **II. One-Click Agent Deployment**: PASS. No new mandatory deployment dependency or standalone-image path is introduced.
- **III. Avoid Vendor Lock-In**: PASS. Codex-specific details remain adapter/controller-bound and do not alter core orchestration contracts.
- **IV. Own Your Data**: PASS. Operator/audit truth and recovery indexes remain artifact and local runtime-control surfaces.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill/tool terminology remains unchanged and outside this feature's runtime scope.
- **VI. The Bittersweet Lesson**: PASS. TDD, replay, and cutover gates provide durable evidence for replaceable runtime scaffolding.
- **VII. Powerful Runtime Configurability**: PASS. Runtime mode, worker routing, and versioning remain explicit configuration surfaces.
- **VIII. Modular and Extensible Architecture**: PASS. The data model and contract keep workflow, activity, runtime, controller, and worker responsibilities separated.
- **IX. Resilient by Default**: PASS. Retry safety, deterministic workflow boundaries, failure classification, and replay validation are required.
- **X. Facilitate Continuous Improvement**: PASS. Bounded diagnostics and completion evidence are preserved through artifacts and validation outputs.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Runtime implementation remains traceable to spec, plan, contracts, and tasks.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical docs and tmp cutover notes remain separated.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. Obsolete bridges are planned for removal after replay/cutover conditions are satisfied.

No new complexity violations are introduced.

## Complexity Tracking

No constitution violations require justification.
