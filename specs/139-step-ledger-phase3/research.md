# Research: Step Ledger Phase 3

## Decision 1: Use Temporal workflow queries as the primary source for progress and step reads

- **Decision**: Read `progress` and the latest-run step ledger from `MoonMind.Run` workflow queries rather than reconstructing them from the execution projection or artifacts.
- **Rationale**: The canonical docs make the workflow-owned ledger the source of truth, and queries remain available after completion without growing event history.
- **Alternatives considered**:
  - **Rebuild progress from projection + memo**: rejected because the projection does not own the full step state and would drift from the workflow contract.
  - **Read artifacts to synthesize progress**: rejected because it is slower, non-authoritative, and violates the bounded-detail goal.

## Decision 2: Keep `/api/executions/{workflowId}` cheap and route full step detail to `/steps`

- **Decision**: Add `progress` to execution detail, but keep the full step ledger on `GET /api/executions/{workflowId}/steps`.
- **Rationale**: This matches the normative contract and keeps the common polling path bounded.
- **Alternatives considered**:
  - **Inline `steps` on execution detail**: rejected because it would bloat every detail refresh and weaken the separation between summary and detail reads.

## Decision 3: Fail fast for unsupported workflow types on `/steps`

- **Decision**: Restrict `/api/executions/{workflowId}/steps` to `MoonMind.Run` and return a validation error for other workflow types.
- **Rationale**: Only `MoonMind.Run` owns the canonical step-ledger query contract today; silent fallback would fabricate semantics for workflows that do not expose the query.
- **Alternatives considered**:
  - **Return empty step lists for unsupported workflows**: rejected because it would look like authoritative state and hide contract gaps.

## Decision 4: Extend the compatibility payload with `stepsHref` instead of inlining rows

- **Decision**: Add `stepsHref` to `ExecutionModel` and task-dashboard runtime config so task-oriented consumers can discover the steps route explicitly.
- **Rationale**: The compatibility layer should remain task-oriented and bounded while still advertising the canonical step-detail read.
- **Alternatives considered**:
  - **Have the client derive the steps URL locally**: rejected because the compatibility doc explicitly calls for `stepsHref` and server-owned route metadata.
