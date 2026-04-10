# Feature Specification: Step Ledger Phase 3

**Feature Branch**: `139-step-ledger-phase3`  
**Created**: 2026-04-08  
**Status**: Draft  
**Input**: User description: "Implement Phase 3 using test-driven development of the step-ledger rollout plan. Expose bounded execution progress and latest-run step-ledger reads through the executions API and compatibility layer, keeping Mission Control UI pivot work out of scope."

## Source Document Requirements

Source: `docs/Temporal/StepLedgerAndProgressModel.md`, `docs/Api/ExecutionsApiContract.md`, `docs/Temporal/TaskExecutionCompatibilityModel.md`, `docs/Temporal/RunHistoryAndRerunSemantics.md`, `docs/tmp/remaining-work/Api-ExecutionsApiContract.md`, `docs/tmp/remaining-work/Temporal-TaskExecutionCompatibilityModel.md`

| ID | Source Section | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `StepLedgerAndProgressModel` §4, §5 | Execution detail must expose bounded latest-run `progress`, and `GET /api/executions/{workflowId}/steps` must return the latest/current run only. |
| DOC-REQ-002 | `StepLedgerAndProgressModel` §6, §7 | The API step-ledger payload must expose the frozen workflow-owned row contract unchanged, including attempts, checks, refs, and artifact slots. |
| DOC-REQ-003 | `ExecutionsApiContract` §8.1, §8.2 | `ExecutionModel` must include `progress`, and the detail payload must remain cheap while the full step ledger is a separate read. |
| DOC-REQ-004 | `ExecutionsApiContract` §11 | `/api/executions/{workflowId}/steps` must be a first-class route returning latest-run step rows in camelCase. |
| DOC-REQ-005 | `TaskExecutionCompatibilityModel` §8.2, remaining work | Temporal-backed task detail compatibility payloads must surface bounded `progress` and a `stepsHref` pointing at `/api/executions/{workflowId}/steps`. |
| DOC-REQ-006 | `RunHistoryAndRerunSemantics` §5-§7 | `workflowId` remains the durable detail handle while `runId` rotates across Continue-As-New; default detail and step reads must follow the latest run only. |
| DOC-REQ-007 | `Api-ExecutionsApiContract` remaining work | Add backend/API tests covering step status vocabulary, attempt identity, and latest-run behavior across Continue-As-New. |
| DOC-REQ-008 | `TaskExecutionCompatibilityModel` §7, remaining work | Task-oriented compatibility detail must keep `taskId == workflowId` while loading step detail separately instead of exposing raw Temporal history. |

## User Scenarios & Testing

### User Story 1 - Execution detail exposes bounded progress (Priority: P1)

A polling client needs execution detail to include compact latest-run progress so the detail page can refresh cheaply without loading the full step ledger on every poll.

**Why this priority**: Phase 3 is the API contract phase; `progress` is the required bounded summary that unblocks the compatibility and UI layers.

**Independent Test**: Can be tested by describing an execution whose workflow query returns progress and asserting the API returns `progress` in the canonical camelCase shape without requiring artifact hydration.

**Acceptance Scenarios**:

1. **Given** a `MoonMind.Run` execution with workflow-owned progress state, **When** a caller requests `GET /api/executions/{workflowId}`, **Then** the response includes bounded `progress` matching the canonical status counters and current step title.
2. **Given** a non-run workflow type such as `MoonMind.ManifestIngest`, **When** the caller requests execution detail, **Then** the route does not fabricate step-ledger progress and may return `null`.
3. **Given** the workflow query is unavailable or the workflow is missing, **When** the detail route still resolves through the projection, **Then** the API degrades safely without fabricating progress data.

---

### User Story 2 - Step ledger is available through `/api/executions/{workflowId}/steps` (Priority: P1)

A backend or UI consumer needs one explicit route that returns the latest/current run step ledger without parsing generic execution detail or Temporal history.

**Why this priority**: This is the main Phase 3 contract surface required by the rollout plan and the docs.

**Independent Test**: Can be tested by querying the new route against a mocked `MoonMind.Run` workflow and asserting the response returns the frozen row contract in the same order and shape as the workflow query.

**Acceptance Scenarios**:

1. **Given** a live `MoonMind.Run` workflow, **When** a caller requests `GET /api/executions/{workflowId}/steps`, **Then** the route returns the workflow query result with `workflowId`, `runId`, `runScope: "latest"`, and the ordered rows.
2. **Given** the same logical execution has Continued-As-New and rotated `runId`, **When** the route is called by `workflowId`, **Then** it returns the latest run’s step rows only and preserves attempt identity inside that run.
3. **Given** a caller does not own the execution, **When** they request the steps route, **Then** the route follows the same execution ownership semantics as execution detail.

---

### User Story 3 - Compatibility task detail surfaces `stepsHref` and progress (Priority: P2)

A task-oriented Mission Control detail consumer needs the compatibility payload to advertise where step detail lives without converting the whole detail response into raw workflow state.

**Why this priority**: The current task detail remains task-oriented, so the compatibility adapter must point clients at the new API surface before the UI pivot phase.

**Independent Test**: Can be tested by serializing a Temporal-backed execution and asserting the compatibility detail includes `progress` plus `stepsHref` while `taskId == workflowId` remains unchanged.

**Acceptance Scenarios**:

1. **Given** a Temporal-backed detail payload, **When** it is serialized for task-oriented clients, **Then** it includes `stepsHref: /api/executions/{workflowId}/steps`.
2. **Given** a rerun or Continue-As-New rotates `runId`, **When** the compatibility payload is fetched again, **Then** `taskId` and `stepsHref` remain anchored on `workflowId`.
3. **Given** the full step ledger is not yet fetched, **When** the compatibility detail is returned, **Then** the payload still remains bounded and does not inline the step rows.

### Edge Cases

- What happens when the workflow query returns `runId` newer than the projection row? The API should trust the workflow query for step/progress payloads while keeping the detail route anchored on the same `workflowId`.
- What happens when the execution is not a `MoonMind.Run` workflow? The steps route should fail fast rather than fabricating a step ledger for unsupported workflow types.
- What happens when the workflow query errors but the projection read succeeds? The detail route should remain readable and leave `progress` unset instead of inventing fallback values.
- What happens when the execution completed earlier? Query-backed progress and step-ledger reads must still be available after completion.
- What happens when a step row contains `taskRunId` but the top-level detail also has `taskRunId`? Both may coexist; the top-level field remains a coarse binding while step rows remain the canonical per-step drilldown keys.

## Requirements

### Functional Requirements

- **FR-001**: System MUST populate `ExecutionModel.progress` for `MoonMind.Run` execution detail from the workflow-owned progress query using the canonical bounded `ExecutionProgressModel`. Mappings: DOC-REQ-001, DOC-REQ-003.
- **FR-002**: System MUST add `GET /api/executions/{workflowId}/steps` returning the latest-run `StepLedgerSnapshotModel` for `MoonMind.Run` workflows without reshaping the frozen row contract. Mappings: DOC-REQ-001, DOC-REQ-002, DOC-REQ-004.
- **FR-003**: System MUST keep execution detail bounded by returning `progress` only and leaving the full step ledger to the separate steps route. Mappings: DOC-REQ-003, DOC-REQ-008.
- **FR-004**: System MUST preserve v1 latest-run semantics across Continue-As-New: the detail route and the steps route remain keyed by `workflowId` and reflect the latest/current `runId`. Mappings: DOC-REQ-001, DOC-REQ-006, DOC-REQ-007.
- **FR-005**: System MUST extend the task-oriented compatibility detail payload with `stepsHref` and bounded `progress` while preserving `taskId == workflowId`. Mappings: DOC-REQ-005, DOC-REQ-008.
- **FR-006**: System MUST update the OpenAPI schema and generated TypeScript client so downstream clients see the new `progress` and step-ledger route contract. Mappings: DOC-REQ-003, DOC-REQ-004, DOC-REQ-005.
- **FR-007**: System MUST add production runtime code changes plus validation tests for execution detail, steps route, and compatibility serialization; documentation-only work is insufficient. Mappings: DOC-REQ-001, DOC-REQ-004, DOC-REQ-007.

### Key Entities

- **ExecutionProgressQueryResult**: Canonical bounded progress payload returned by `MoonMind.Run.get_progress`.
- **ExecutionStepLedgerResponse**: API response shape for `/api/executions/{workflowId}/steps`, identical to `StepLedgerSnapshotModel`.
- **ExecutionCompatibilityLinks**: Bounded compatibility metadata on execution detail including `detailHref` and `stepsHref`.
- **LatestRunRead**: Detail/read behavior anchored on `workflowId` while surfacing the current/latest `runId`.

## Success Criteria

### Measurable Outcomes

- **SC-001**: API/router tests prove `GET /api/executions/{workflowId}` returns canonical bounded `progress` for `MoonMind.Run` executions.
- **SC-002**: API/router and contract tests prove `GET /api/executions/{workflowId}/steps` returns latest-run step rows with attempts, refs, and artifact slots in the frozen contract shape.
- **SC-003**: Compatibility tests prove serialized Temporal-backed task detail includes `stepsHref` and preserves `taskId == workflowId` across rerun/Continue-As-New semantics.
- **SC-004**: OpenAPI regeneration updates the checked-in TypeScript client with `progress` and the new steps route.
- **SC-005**: Final verification passes through targeted API/router/workflow tests and `./tools/test_unit.sh`.

## Assumptions

- Phase 3 stops at the API and compatibility layer; making the Mission Control Steps panel the primary task-detail surface remains Phase 4 work.
- Optional degraded-read projections such as `execution_step_projection` are not required if query-backed reads keep detail polling cheap and stable.
- The workflow-owned query contracts from Phase 1 and the evidence wiring from Phase 2 are already the authoritative source for latest-run step/progress data.
