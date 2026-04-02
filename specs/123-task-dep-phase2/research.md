# Research: Task Dependencies Phase 2 - MoonMind.Run Dependency Gate

## Decision 1: Use Temporal external workflow handles for prerequisite waiting

- **Decision**: Wait on dependencies with `workflow.get_external_workflow_handle(dep_id)` and `await asyncio.gather(*(handle.result() ...))`.
- **Rationale**: This matches the canonical design in `docs/Tasks/TaskDependencies.md` and avoids introducing polling activities or extra persistence just to observe prerequisite completion.
- **Alternatives considered**:
  - Poll execution state through activities: rejected because it duplicates Temporal orchestration semantics and adds avoidable latency/history churn.
  - Signal-based prerequisite completion fan-out: rejected because it requires broader contract changes and more moving parts than Phase 2 needs.

## Decision 2: Guard the dependency gate with a replay-stable patch id

- **Decision**: Use `workflow.patched("dependency-gate-v1")` around the new pre-planning behavior.
- **Rationale**: Existing histories currently transition straight from `initializing` to `planning`. The patch guard preserves that path for unpatched histories while allowing new runs to use the dependency gate safely.
- **Alternatives considered**:
  - Replace behavior without patching: rejected because it risks replay divergence for in-flight runs.
  - Introduce a second workflow type/version: rejected because the change is local and Temporal patching already solves the replay problem.

## Decision 3: Keep dependency IDs in memo, not new search attributes

- **Decision**: Expose dependency IDs through workflow memo and use existing search attributes only for state transitions (`mm_state`, `mm_updated_at`, existing owner/repo fields).
- **Rationale**: `docs/Temporal/VisibilityAndUiQueryModel.md` defines a tight v1 search-attribute set and explicitly warns against ad hoc additions. Phase 2 needs state visibility, not new query surfaces.
- **Alternatives considered**:
  - Add `mm_dependency_ids` search attribute: rejected because it would expand the visibility contract ahead of the planned Phase 3/4 work.
  - Skip dependency metadata entirely: rejected because the phase plan explicitly calls for dependency IDs in workflow metadata.

## Decision 4: Reuse existing waiting metadata contract

- **Decision**: Set `waitingReason` to `dependency_wait` via the existing `_waiting_reason` memo/search update path.
- **Rationale**: The visibility model already defines `dependency_wait` as an allowed waiting reason, so the implementation can integrate with current list/detail semantics without inventing a new field.
- **Alternatives considered**:
  - Leave `waiting_reason` unset: rejected because blocked runs would be less diagnosable in UI and tests.
  - Add a dependency-specific status alias: rejected by the pre-release compatibility policy.

## Decision 5: Prefer workflow-boundary unit tests over isolated helper-only tests

- **Decision**: Add tests around `MoonMindRunWorkflow.run()` orchestration, plus any focused helper tests only where needed.
- **Rationale**: The AGENTS guidance requires boundary coverage for workflow contract changes, including compatibility coverage for in-flight histories and degraded provider/runtime inputs.
- **Alternatives considered**:
  - Unit-test only a private helper: rejected because it would miss the replay/cancel/state-transition contract at the workflow boundary.
