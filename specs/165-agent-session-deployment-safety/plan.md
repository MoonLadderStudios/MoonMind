# Implementation Plan: Agent Session Deployment Safety

**Branch**: `165-agent-session-deployment-safety` | **Date**: 2026-04-13 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/165-agent-session-deployment-safety/spec.md`

## Summary

Finish the remaining runtime work needed to make Codex managed session workflow changes safe to operate and deploy. The implementation is runtime-mode work: production workflow, runtime/controller, worker deployment, reconciliation, observability, and validation-test changes are required deliverables; docs/spec-only changes are insufficient. The delayed standalone-image path remains out of scope.

The current repository already contains substantial Phase 3-5 surfaces, including typed `AgentSessionWorkflow` updates, runtime steering/termination hooks, workflow handler hardening, bounded visibility metadata, managed-session reconcile workflow, and worker deployment configuration. This plan treats those as implementation candidates to verify, preserve, and close any gaps against the spec rather than reimplementing completed behavior blindly.

Traceability status: this feature has no `DOC-REQ-*` identifiers. No `contracts/requirements-traceability.md` artifact is required for this spec, and implementation traceability is maintained through FR-to-task coverage in `tasks.md` and `speckit_analyze_report.md`.

## Technical Context

**Language/Version**: Python 3.12 for Temporal workflows, activities, runtime services, and tests; TypeScript only if an affected operator/API surface needs payload or visibility-field handling.
**Primary Dependencies**: Temporal Python SDK, pytest, Pydantic managed-session schemas, existing Codex managed runtime/controller/supervisor/store modules, Docker-backed managed session activities, OpenTelemetry/logging integration, existing Temporal client/schedule helpers.
**Storage**: Temporal workflow history and visibility metadata; artifact refs for operator/audit truth; JSON-backed `ManagedSessionStore` as the operational recovery index; container-local state as disposable runtime cache only.
**Testing**: `./tools/test_unit.sh` for required unit verification; targeted pytest during iteration; Temporal workflow replay tests; local Temporal/time-skipping integration tests where needed; `./tools/test_integration.sh` only for affected hermetic integration-ci seams.
**Target Platform**: MoonMind backend/runtime workers in Docker Compose with Temporal service, managed Codex runtime containers, and separated workflow/activity task queues.
**Project Type**: Backend/runtime workflow reliability and deployment-safety feature.
**Performance Goals**: No leaked managed-session containers after terminate; no unbounded prompts/logs/transcripts in visibility or summaries; Continue-As-New prevents unbounded workflow history growth; reconcile remains bounded over stale/degraded records.
**Constraints**: Runtime mode is mandatory; delayed standalone-image delivery is excluded; heavy side effects stay outside workflow code; secrets and unbounded provider output must not enter indexed visibility, summaries, telemetry dimensions, or replay fixtures; incompatible workflow evolution requires Worker Versioning, patching, or explicit cutover.
**Scale/Scope**: One task-scoped Codex managed session per task run; no cross-task session reuse, generic runtime marketplace, or Claude/Gemini managed session plane expansion.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Temporal-First Orchestration**: PASS. Session lifecycle, reconciliation, deployment safety, and replay gates remain Temporal workflow/activity concerns.
- **II. Declarative Desired State**: PASS. The feature defines desired managed-session control and deployment behavior, then reconciles runtime state through controller/supervisor/store boundaries.
- **III. Durable State Over Ephemeral Memory**: PASS. Operator/audit state is artifacts plus bounded workflow metadata; the managed session record is a recovery index; container-local state is disposable.
- **IV. Strict Runtime Boundaries**: PASS. Runtime/container effects stay in activities/controllers, not workflow code.
- **V. Operator Visibility Without Secret Leakage**: PASS. Visibility, summaries, telemetry, schedules, and replay fixtures are restricted to bounded identifiers and refs.
- **VI. Testable Workflow Boundaries**: PASS. Required work includes workflow-boundary, runtime/controller, replay, race/idempotency, and reconciliation validation.
- **VII. Idempotent Side Effects**: PASS. Launch, clear, interrupt, cancel, steer, and terminate are planned as retry-safe or deduplicated at the side-effect boundary.
- **VIII. Safe Evolution of Durable Code**: PASS. Worker Versioning, patching, replay gates, and cutover playbooks are first-class deliverables.
- **IX. Minimal Surface Area**: PASS. The scope is Codex managed sessions only, with standalone-image delivery excluded.
- **X. No Hidden Compatibility Layers for Internal Contracts**: PASS. Legacy or bridge behavior must be scoped to replay/versioned cutover needs, not indefinite production aliases.
- **XI. Runtime Deliverables Over Paper Compliance**: PASS. Production code changes and validation tests are required.
- **XII. Canonical Docs vs Temporary Migration Notes**: PASS. Any runbook/cutover notes support the runtime change and do not replace implementation.
- **XIII. Pre-Release Compatibility Policy**: PASS. Incompatible internal contract changes use explicit versioning/cutover rather than silent compatibility transforms.

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

1. Verify the existing managed-session implementation against FR-001 through FR-027 before adding code. Mark already-compliant behavior in tasks as verification/test work instead of duplicating runtime logic.
2. Confirm the production mutation surface is typed workflow Updates for request/response controls and Signals only for fire-and-forget state propagation.
3. Confirm terminate/cancel/clear/interrupt/steer behavior is implemented at the workflow boundary and at the runtime/controller side-effect boundary.
4. Confirm handler locking, readiness waits, handler drain, and Continue-As-New carry-forward happen in workflow-safe code paths.
5. Confirm bounded metadata, Search Attributes, activity summaries, schedule metadata, telemetry dimensions, and replay fixtures exclude prompts, transcripts, scrollback, raw logs, credentials, and secrets.
6. Confirm Worker Versioning, patching/cutover guidance, and replay tests are present for incompatible workflow-shape changes.

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

1. **Gap audit**: Compare current `agent_session.py`, managed runtime/controller, worker runtime, client schedules, replay tests, and lifecycle tests against each functional requirement.
2. **Control API parity**: Ensure production mutators are typed Updates with validators or deterministic pre-mutation rejection, and only state propagation remains signal-based.
3. **Lifecycle semantics**: Ensure terminate waits for runtime cleanup and supervision finalization, while cancel stops active work without destroying the session.
4. **Runtime side-effect safety**: Make retryable activity/controller boundaries idempotent, classify permanent failures explicitly, and add heartbeat/cancellation handling for meaningful blocking work.
5. **Long-lived workflow hardening**: Preserve lock-protected mutators, readiness gates, handler drain, Continue-As-New from the main workflow path, and bounded carry-forward state.
6. **Operational separation**: Keep artifact/controller/supervisor publication as the production operator/audit path; use the session store for recovery/reconcile; treat container-local helpers as fallback-only.
7. **Observability/reconcile**: Maintain bounded UI/current details, Search Attributes, summaries, telemetry correlation, worker/task-queue separation, and scheduled reconcile.
8. **Deployment safety**: Require Worker Versioning, patching or explicit versioned cutover, replay validation, and cutover playbooks before incompatible workflow-shape rollout.
9. **Validation**: Add or update workflow-boundary, controller/runtime, integration/reconcile, idempotency/race, Continue-As-New, and replay tests. Use credential-free validation for required gates.

## Post-Design Constitution Check

- **Temporal and runtime boundaries** remain intact: workflow code carries compact state and delegates side effects to activities/controllers.
- **Durable-state and operator-truth separation** is explicit in the model and contract.
- **Security constraints** are testable through bounded metadata and forbidden-content checks.
- **Deployment safety** is covered through Worker Versioning or versioned cutover plus replay gates.
- **Runtime-mode deliverables** are enforced by the contract and quickstart validation; docs-only completion cannot satisfy FR-001.

No new complexity violations are introduced.

## Complexity Tracking

No constitution violations require justification.
