# Implementation Plan: Worker Self-Heal System (Phase 1 Alignment)

**Branch**: `034-worker-self-heal` | **Date**: 2026-03-02 | **Spec**: `specs/034-worker-self-heal/spec.md`  
**Input**: Feature specification from `/specs/034-worker-self-heal/spec.md`

## Summary

Align feature `034-worker-self-heal` to MoonMind's current phased strategy by treating worker-side in-step self-heal as the active runtime scope (Phase 1), while explicitly deferring hard-reset replay, retry-context envelope activation, and operator recovery controls to later phases. Runtime-vs-doc behavior remains aligned with orchestration intent: this feature requires production runtime implementation plus validation tests, not docs-only output.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: `moonmind.agents.codex_worker` runtime, Celery worker runtime, StatsD client instrumentation, existing queue/job contracts, pytest  
**Storage**: Filesystem artifacts under `var/artifacts/agent_jobs/<run_id>/state/{steps,self_heal}/`; existing queue persistence in PostgreSQL/RabbitMQ unchanged  
**Testing**: `./tools/test_unit.sh` (required CI/local gate)  
**Target Platform**: Linux worker runtime (local + Docker Compose services)  
**Project Type**: Backend worker runtime behavior and telemetry update  
**Performance Goals**: Bounded attempt loops per step; timeout/no-progress detection with no unbounded retry behavior; no additional queue round-trips for in-step retries  
**Constraints**: Preserve existing pause/takeover/cancel semantics, preserve queue retry ownership, redact secrets in signatures/events/artifacts, avoid API/schema breaking changes in Phase 1  
**Scale/Scope**: Codex worker execution path and related tests/docs for Phase 1 only; hard reset replay, retry-context envelope activation, and operator recovery APIs remain deferred

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. One-Click Deployment with Smart Defaults**: PASS. No new required services; self-heal budgets have defaults and fail-fast behavior remains explicit.
- **II. Powerful Runtime Configurability**: PASS. Self-heal behavior is controlled by runtime env configuration (`STEP_*`, `JOB_SELF_HEAL_MAX_RESETS`) with deterministic precedence.
- **III. Modular and Extensible Architecture**: PASS. Self-heal logic is isolated in `self_heal.py` and consumed through worker execution boundaries.
- **IV. Avoid Exclusive Proprietary Vendor Lock-In**: PASS. Strategy and artifacts are portable JSON/event contracts; only codex runtime path is currently activated by phase choice, not by architecture lock-in.
- **V. Self-Healing by Default**: PASS. Retryable classifications, bounded retries, deterministic exhaustion semantics, and resumable artifacts are explicit.
- **VI. Facilitate Continuous Improvement**: PASS. Structured events/metrics and `run_quality_reason` improve failure triage and future improvements backlog quality.
- **VII. Spec-Driven Development Is the Source of Truth**: PASS. `spec.md`, `plan.md`, and `tasks.md` are synchronized to phased runtime scope.
- **VIII. Skills Are First-Class and Easy to Add**: PASS. No skill contract regression; worker self-heal updates do not alter skill packaging/discovery behavior.

**Gate Status**: PASS.

## Project Structure

### Documentation (this feature)

```text
specs/034-worker-self-heal/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── task-run-recovery.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── agents/codex_worker/
│   ├── self_heal.py
│   ├── metrics.py
│   ├── worker.py
│   └── __init__.py
└── workflows/agent_queue/storage.py

celery_worker/
└── speckit_worker.py

tests/
├── unit/agents/codex_worker/
│   ├── test_self_heal.py
│   ├── test_metrics.py
│   └── test_worker.py
└── unit/workflows/agent_queue/test_artifact_storage.py
```

**Structure Decision**: Keep the implementation in the existing codex worker runtime path and shared artifact/metrics modules. Avoid new services or API surfaces in Phase 1; capture deferred Phase 2/3 surfaces only in design docs/contracts.

## Phase 0: Research Outcomes

Research outputs are captured in `specs/034-worker-self-heal/research.md` and resolve planning unknowns:

1. Adopt phased scope (Phase 1 runtime delivery now; Phases 2/3 deferred).
2. Place self-heal attempt loop directly in codex step execution path.
3. Detect stuck behavior via wall timeout, idle timeout, and no-progress signature/diff tracking.
4. Escalate retryable exhaustion through structured `run_quality_reason` to queue retries.
5. Persist attempt/checkpoint artifacts now to support replay/operator controls later.
6. Preserve strict redaction while avoiding over-redaction of short diagnostic tokens.

## Phase 1: Design Outputs

- `data-model.md`: defines `StepCheckpointState`, `SelfHealAttemptArtifact`, retry metadata, in-memory attempt state, and metrics tag schema.
- `contracts/task-run-recovery.openapi.yaml`: documents active Phase 1 control surface (pause/resume/takeover) and explicitly defers recovery action APIs.
- `contracts/requirements-traceability.md`: maps each `DOC-REQ-001..013` to FRs, runtime surfaces, and validation strategy.
- `quickstart.md`: deterministic smoke checks for retry recovery, retryable exhaustion, deterministic fail-fast behavior, control compatibility, and required unit test gate.

`spec.md` includes `DOC-REQ-*` entries and all are mapped in `contracts/requirements-traceability.md` with planned/implemented validation; no unmapped requirement remains.

## Post-Design Constitution Re-check

- Phase 1 design keeps runtime behavior configurable and bounded, with explicit operator-observable events/metrics/artifacts.
- Deferred Phase 2/3 items are documented as explicit non-goals for this phase, preventing hidden scope expansion.
- Runtime deliverable requirement (production code + tests) is preserved in both `spec.md` and `tasks.md`.

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| None | N/A | N/A |
