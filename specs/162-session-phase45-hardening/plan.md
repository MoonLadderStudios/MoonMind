# Implementation Plan: Codex Managed Session Phase 4/5 Hardening

**Branch**: `162-session-phase45-hardening` | **Date**: 2026-04-12 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `/specs/162-session-phase45-hardening/spec.md`

## Summary

Implement the remaining runtime work for Codex managed session Phase 4 and Phase 5 without duplicating behavior that is already complete. The plan hardens bounded operator visibility, control-history readability, runtime worker separation, recurring reconciliation, lifecycle integration coverage, Continue-As-New carry-forward validation, and replay safety. The implementation remains runtime-mode work: production code changes plus validation tests are required, and docs-only completion is invalid.

## Technical Context

**Language/Version**: Python 3.12; TypeScript only if existing dashboard/test plumbing needs generated visibility validation updates  
**Primary Dependencies**: Temporal Python SDK, Pydantic managed-session schemas, FastAPI/runtime settings, Docker-backed managed-session controller and supervisor, pytest, Temporal test environment/replayer  
**Storage**: Temporal workflow history and visibility metadata; JSON-backed managed session store as operational recovery index; artifact references for operator/audit continuity; container-local state as disposable runtime cache  
**Testing**: `./tools/test_unit.sh`, focused pytest targets, Temporal time-skipping integration tests where CI-safe, replay tests for representative histories, hermetic `integration_ci` only where the test taxonomy allows it  
**Target Platform**: Docker/Compose-hosted MoonMind services and Temporal worker fleets running Codex task-scoped managed session containers  
**Project Type**: Backend runtime/workflow reliability and observability hardening  
**Performance Goals**: Operator metadata and reconcile outcomes remain bounded and reference-sized; recurring reconcile stays compact under many stale records; workflow history growth remains bounded by Continue-As-New  
**Constraints**: Runtime mode only; implement only missing behavior; no prompts, transcripts, scrollback, raw logs, credentials, or secrets in indexed visibility, workflow metadata, schedule metadata, activity summaries, or replay fixtures; runtime/container side effects stay on the runtime activity boundary; provider verification remains outside required CI unless explicitly enabled  
**Scale/Scope**: One Codex task-scoped managed session per task; no standalone image path; no multi-runtime managed session expansion; no frontend redesign unless required to validate bounded operator presentation

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The feature observes and controls Codex through MoonMind's orchestration boundary without reimplementing Codex behavior.
- **II. One-Click Agent Deployment**: PASS. The plan uses existing Docker/Compose, Temporal worker, and local test paths with no new mandatory external service.
- **III. Avoid Vendor Lock-In**: PASS. Codex-specific managed session work stays isolated to the managed-session runtime/controller surfaces and does not alter core orchestration contracts for other agents.
- **IV. Own Your Data**: PASS. Visibility, recovery records, and artifacts remain in MoonMind-controlled Temporal/store/artifact surfaces.
- **V. Skills Are First-Class and Easy to Add**: PASS. Agent skill discovery and materialization are not changed.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The plan strengthens workflow/activity/reconcile/test contracts rather than adding cognitive scaffolding.
- **VII. Powerful Runtime Configurability**: PASS. Schedule cadence, worker topology, and runtime mode remain configuration-driven or existing runtime parameters.
- **VIII. Modular and Extensible Architecture**: PASS. Work is scoped to established workflow, activity catalog/runtime, controller/supervisor, client schedule, worker topology, and tests.
- **IX. Resilient by Default**: PASS. Recurring reconcile, idempotency/race coverage, cleanup validation, and replay testing improve unattended recovery.
- **X. Facilitate Continuous Improvement**: PASS. Operators get clearer bounded state and test evidence for future workflow changes.
- **XI. Spec-Driven Development**: PASS. Spec, plan, and downstream tasks trace the runtime requirements.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Implementation planning stays under `specs/`; no canonical docs become rollout checklists.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The plan avoids introducing compatibility aliases and requires old superseded internal behavior to be removed when replacements are complete.

## Project Structure

### Documentation (this feature)

```text
specs/162-session-phase45-hardening/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
├── contracts/
│   └── managed-session-phase45-contract.md
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
        ├── runtime/
        │   ├── managed_session_controller.py
        │   ├── managed_session_store.py
        │   └── managed_session_supervisor.py
        └── workflows/
            ├── agent_run.py
            ├── agent_session.py
            ├── managed_session_reconcile.py
            └── run.py

tests/
├── integration/
│   ├── services/temporal/
│   └── temporal/
└── unit/
    ├── services/temporal/runtime/
    └── workflows/temporal/
```

**Structure Decision**: Use the existing MoonMind Temporal runtime layout. The feature is not a new service or UI project; it extends established managed-session workflow, runtime controller/supervisor, worker topology, schedule helper, and test surfaces.

## Phase 0: Research

Research decisions are captured in [research.md](./research.md).

Key decisions:

1. Preserve bounded visibility as the operator surface and keep secrets/unbounded content out of indexed metadata, summaries, schedules, and replay fixtures.
2. Route runtime/container side effects through the existing runtime activity boundary.
3. Use recurring scheduled reconciliation for durable recovery and bounded sweeper outcomes.
4. Treat lifecycle integration and replay validation as required deployment-safety evidence for workflow-shape changes.
5. Implement only behavior that is not already complete, using existing tests to prove completed behavior rather than duplicating it.

## Phase 1: Design

Design outputs:

- [data-model.md](./data-model.md)
- [contracts/managed-session-phase45-contract.md](./contracts/managed-session-phase45-contract.md)
- [quickstart.md](./quickstart.md)

Implementation surfaces:

1. Audit existing Phase 4/5 behavior and mark already-complete behavior as verification-only.
2. Add or correct runtime workflow visibility updates where major transitions are missing or unsafe.
3. Add or correct bounded control summaries for launch, send, interrupt, clear, cancel, steer, and terminate operations.
4. Preserve runtime/container activity routing on the runtime worker fleet.
5. Add or complete scheduled reconcile workflow/client wiring and bounded reconcile outcome normalization.
6. Add integration tests for lifecycle, clear invariants, interrupt, cancel, terminate cleanup, restart/reconcile, race/idempotency, and Continue-As-New carry-forward.
7. Add replay tests or replay-fixture gates for representative managed session histories affected by workflow-shape changes.

## Post-Design Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. Design keeps Codex behavior behind runtime/controller boundaries.
- **II. One-Click Agent Deployment**: PASS. No new required deployment primitive is introduced.
- **III. Avoid Vendor Lock-In**: PASS. Codex-specific behavior remains isolated.
- **IV. Own Your Data**: PASS. Artifacts, Temporal metadata, and local recovery records stay operator-controlled.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill runtime contracts are untouched.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The design adds testable contracts and avoids parallel abstractions.
- **VII. Powerful Runtime Configurability**: PASS. Reconcile cadence and worker routing remain configurable through existing runtime settings and Temporal schedule helpers.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay in existing modules with boundary tests.
- **IX. Resilient by Default**: PASS. Cleanup, race, idempotency, reconcile, Continue-As-New, and replay coverage strengthen recovery.
- **X. Facilitate Continuous Improvement**: PASS. Bounded visibility and test artifacts improve incident review.
- **XI. Spec-Driven Development**: PASS. Design maps directly to the feature spec.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. No canonical doc migration backlog is added.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. Superseded internal behavior should be removed in the same implementation task.

## Complexity Tracking

No constitution violations. No additional complexity exceptions required.
