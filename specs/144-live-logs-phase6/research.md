# Research: Live Logs Phase 6 Compatibility and Cleanup

## Decision 1: Frontend should normalize event aliases instead of adding backend compatibility duplicates

- **Decision**: Accept both camelCase and snake_case observability event aliases in the frontend normalization path.
- **Rationale**: The backend already emits canonical camelCase aliases from `RunObservabilityEvent`. Adding duplicate snake_case fields on the backend would create a longer-lived compatibility contract that the pre-release compatibility policy discourages.
- **Alternatives considered**:
  - Add snake_case aliases to backend responses: rejected because it expands the API surface during a rollout slice.
  - Keep frontend snake_case-only parsing: rejected because it fails the mixed-deploy compatibility requirement.

## Decision 2: Empty structured history should trigger merged fallback

- **Decision**: Treat a successful `/observability/events` response with zero rows as fallback-eligible when `/logs/merged` still has compatibility content.
- **Rationale**: Historical runs may have no structured event journal even though the endpoint exists. Showing a blank timeline would hide real artifact-backed observability during rollout.
- **Alternatives considered**:
  - Treat any successful history response as authoritative, even when empty: rejected because it strands older runs.
  - Add backend flags for fallback intent: rejected because the frontend can infer the condition without widening the API contract.

## Decision 3: Rollout eligibility should use both rollout scope and run context

- **Decision**: Compute viewer eligibility from `liveLogsSessionTimelineRollout` plus run context, while using `liveLogsSessionTimelineEnabled` and missing config as safe fallbacks for older boot payloads.
- **Rationale**: Phase 6 requires codex-only versus all-managed rollout control. The current boolean-only gate cannot express that distinction.
- **Alternatives considered**:
  - Continue using the boolean only: rejected because it ignores rollout scope.
  - Move per-run eligibility to the backend: rejected for this slice because the task-detail page already has enough run context and the boot payload already carries rollout scope.
