# Research: Step Ledger Phase 4

## Decision 1: Treat `/api/executions/{workflowId}/steps` as the primary task-detail dataset

- **Decision**: Load execution detail first for top-level metadata, then load the latest-run step ledger as the primary task-detail execution surface.
- **Rationale**: The canonical docs explicitly position step ledger state as detail-page evidence and require the Steps section to appear above Timeline and generic Artifacts.
- **Alternatives considered**:
  - **Keep the current observability-first page and add Steps below it**: rejected because it preserves the wrong information hierarchy and leaves the new API underused.
  - **Inline steps into execution detail polling only**: rejected because the full ledger belongs on its own read path and would bloat common polling.

## Decision 2: Scope observability fetches to expanded rows only

- **Decision**: Use row expansion to trigger `/api/task-runs/{taskRunId}/observability-summary`, `/logs/*`, and `/diagnostics` requests only for steps that expose `taskRunId`.
- **Rationale**: This keeps the default page cheap and matches the Task Runs API identity model, where `taskRunId` is the observability handle for a specific managed run bound to a step.
- **Alternatives considered**:
  - **Fetch observability for the first step automatically**: rejected because many rows may have no binding yet and eager observability calls recreate the old whole-page model.

## Decision 3: Preserve expanded-row state by logical step id

- **Decision**: Track expanded step rows by `logicalStepId` so polling and latest-run refreshes do not collapse or remount the operator’s current focus unnecessarily.
- **Rationale**: The step-ledger contract requires clients not to infer identity from array position alone, and latest-run polling can update row order/state over time.
- **Alternatives considered**:
  - **Track expansion by row index**: rejected because it is fragile across rerenders and violates the stable-identity guidance.

## Decision 4: Keep generic execution-wide Artifacts and Timeline as secondary surfaces

- **Decision**: Retain the existing Timeline and Artifacts sections, but move them below Steps and make them clearly secondary to step-scoped detail.
- **Rationale**: This delivers the requested product pivot without deleting useful secondary evidence surfaces that still matter for debugging and coarse execution history.
- **Alternatives considered**:
  - **Delete Timeline/Artifacts entirely**: rejected because Phase 4 is a priority shift, not a removal phase.
