# Research: Orchestrator Task Runtime Upgrade

## Scope Resolution

- `spec.md` has no remaining `NEEDS CLARIFICATION` markers.
- Selected orchestration mode is **runtime** (not docs-only), so design choices below prioritize production code changes plus test validation.

## Decision 1: Keep API-level compatibility while migrating user-facing naming to tasks
- **Decision**: Keep `/orchestrator/runs*` operational, add `/orchestrator/tasks*` aliases, and return both `taskId` and `runId` during transition.
- **Rationale**: Existing clients and scripts continue to work while the dashboard and new contracts move to task terminology.
- **Alternatives considered**:
  - Immediate hard cut from `/runs` to `/tasks`: rejected due to breaking-change blast radius.
  - Long-term dual semantics with no canonical migration: rejected because it prolongs ambiguity and maintenance cost.

## Decision 2: Keep unified list/detail as dashboard composition with a shared row contract
- **Decision**: Continue to build `/tasks/list` by fan-out to queue + orchestrator sources and normalize both into one `UnifiedTaskRow` contract in dashboard runtime code.
- **Rationale**: Existing dashboard architecture already supports source fan-out and normalized rendering with minimal backend churn.
- **Alternatives considered**:
  - New server-side aggregator endpoint: deferred to avoid adding another API surface during this migration.
  - Keep separate orchestrator pages: rejected by DOC-REQ-003/006.

## Decision 3: Use deterministic source resolution for `/tasks/:taskId`
- **Decision**: Respect explicit `?source=` when present; otherwise probe queue first, then orchestrator.
- **Rationale**: Handles overlapping ids without breaking old links and keeps resolution behavior explicit and testable.
- **Alternatives considered**:
  - Probe both in parallel and choose first response: rejected because race timing can cause nondeterministic behavior.

## Decision 4: Orchestrator authoring uses steps + skills while preserving queue capability fields
- **Decision**: Runtime `orchestrator` submit mode must require explicit step skill IDs, support per-step args, and keep queue capability inputs available in shared submit UX.
- **Rationale**: Delivers authoring parity and avoids hiding operator controls purely due to runtime selection.
- **Alternatives considered**:
  - Keep single instruction/skill payload: rejected by DOC-REQ-002/009/011.
  - Fork orchestrator into a separate form: rejected because it reintroduces split UX.

## Decision 5: Group skill discovery by runtime domain
- **Decision**: `/api/tasks/skills` returns grouped catalogs (`worker`, `orchestrator`) with legacy compatibility payload.
- **Rationale**: Submit UI can present runtime-appropriate skill options without adding another endpoint.
- **Alternatives considered**:
  - Separate skill endpoints per runtime: rejected as unnecessary API sprawl.

## Decision 6: Move to canonical task/step persistence with explicit migration plan
- **Decision**: Treat task/step models as canonical for orchestrator runtime and plan migration from legacy run-only semantics.
- **Rationale**: Arbitrary `N` steps and stable step IDs/attempt tracking are hard to represent with fixed enum-only state rows.
- **Alternatives considered**:
  - Keep fixed enum plan-step model and emulate custom steps in JSON: rejected for poor queryability and weak integrity guarantees.

## Decision 7: Keep both queue job types during rollout
- **Decision**: Worker and dispatch layers accept `orchestrator_task` and `orchestrator_run` payloads during transition.
- **Rationale**: Reduces rollout risk and enables incremental migration.
- **Alternatives considered**:
  - Single cutover to `orchestrator_task` only: rejected because in-flight and retry flows may still emit legacy payloads.

## Decision 8: Use state-sink fallback with artifact-backed snapshots as degraded-mode durability
- **Decision**: Runtime executor records state through `OrchestratorStateSink` abstraction (`DbStateSink` primary, `ArtifactStateSink` fallback).
- **Rationale**: In-flight execution can continue when DB writes fail, while retaining reconciliation evidence.
- **Alternatives considered**:
  - Fail-fast on any persistence error: rejected by DOC-REQ-004/015/016.
  - Artifact-only persistence always: rejected because DB-first remains required for normal observability and query flows.

## Decision 9: Add explicit reconciliation for DB and queue terminal updates after outages
- **Decision**: Plan dedicated reconciliation for artifact snapshots and delayed queue terminal transitions once connectivity returns.
- **Rationale**: Current fallback captures data, but reconciliation guarantees are required to complete degraded-mode behavior.
- **Alternatives considered**:
  - Best-effort logging only without replay: rejected because terminal consistency can be lost.

## Decision 10: Runtime-vs-docs mode alignment is a hard gate in this feature
- **Decision**: Keep runtime mode selected across planning and implementation and validate scope via `validate-implementation-scope.sh --mode runtime` plus `./tools/test_unit.sh`.
- **Rationale**: Task objective explicitly forbids docs-only completion.
- **Alternatives considered**:
  - Documentation-only milestone acceptance: rejected by DOC-REQ-019.
