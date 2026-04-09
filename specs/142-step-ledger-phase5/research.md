# Research: Step Ledger Phase 5

## Decision 1: Reuse the existing step row as the only review state surface

- **Decision**: Approval-policy review mutates the existing step row through `status`, `attempt`, and `checks[]` rather than creating a separate review projection or a workflow-summary-only status.
- **Rationale**: The canonical step-ledger doc already assigns `reviewing` and `checks[]` to the workflow-owned row. Phase 5 should make that contract real.
- **Alternatives considered**:
  - **Store review state only in workflow summary or memo**: rejected because it bypasses the canonical step-ledger contract and is too lossy for the Steps UI.
  - **Create a separate review table/projection first**: rejected because the rollout explicitly says approval policy should be the first concrete producer of `checks[]`, not a parallel state machine.

## Decision 2: Persist full review payloads in JSON artifacts, not workflow state

- **Decision**: After each review verdict, write a compact JSON artifact containing the request, verdict, and issues, then link it from `checks[].artifactRef`.
- **Rationale**: The step-ledger doc explicitly keeps large review bodies out of workflow state and Search Attributes, while still requiring durable evidence.
- **Alternatives considered**:
  - **Inline full feedback/issues into `checks[]`**: rejected because it bloats workflow state and breaks the bounded row contract.

## Decision 3: Keep UI scope narrow and build on the Phase 4 Checks section

- **Decision**: Extend the existing Checks section to show retry counts and review artifact refs instead of adding a dedicated review panel.
- **Rationale**: Phase 4 already established the expanded Steps panel as the primary detail surface with a stable Checks group.
- **Alternatives considered**:
  - **Add a new “Review” section under each step**: rejected because it duplicates the purpose of `checks[]` and dilutes the existing information hierarchy.

## Decision 4: Treat review retries as step-attempt retries on the same logical step

- **Decision**: A failed review reruns the same logical step, increments the step attempt, and accumulates `retryCount` on the approval-policy check row.
- **Rationale**: The canonical step-ledger model makes attempts run-scoped per logical step and expects retries to stay attached to that row.
- **Alternatives considered**:
  - **Record review retries separately from step attempts**: rejected because it makes the row harder to reason about and conflicts with the existing attempt model.
