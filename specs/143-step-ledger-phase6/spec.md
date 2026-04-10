# Feature Specification: Step Ledger Phase 6

**Feature Branch**: `143-step-ledger-phase6`  
**Created**: 2026-04-09  
**Status**: Draft  
**Input**: User description: "Implement Phase 6 using test-driven development of the step-ledger rollout plan. Harden latest-run semantics, degraded reads, Continue-As-New behavior, and rollout cleanup so task detail remains truthful and bounded across reruns and projection drift."

## Source Document Requirements

Source: `docs/Temporal/StepLedgerAndProgressModel.md`, `docs/Temporal/RunHistoryAndRerunSemantics.md`, `docs/Temporal/SourceOfTruthAndProjectionModel.md`, `docs/Temporal/WorkflowArtifactSystemDesign.md`, `docs/UI/MissionControlArchitecture.md`, `docs/tmp/remaining-work/*step-ledger rollout trackers`

| ID | Source Section | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `StepLedgerAndProgressModel` §4-§10 | Default task detail and `/api/executions/{workflowId}/steps` must stay latest-run-only, with attempt identity scoped by `(workflowId, runId, logicalStepId, attempt)`. |
| DOC-REQ-002 | `RunHistoryAndRerunSemantics` §5-§8 | `workflowId` stays stable across rerun and Continue-As-New while `runId` rotates, and task-oriented detail should continue following the same logical execution. |
| DOC-REQ-003 | `SourceOfTruthAndProjectionModel` §5-§9 | Temporal remains authoritative for latest `runId`; projection-backed reads may degrade or repair, but must not invent stale latest-run truth. |
| DOC-REQ-004 | `WorkflowArtifactSystemDesign` §1, §5.2 | Generic artifact browsing must remain run-scoped and artifact-backed, not reconstructed from workflow state or mixed across run boundaries. |
| DOC-REQ-005 | `MissionControlArchitecture` §9.2 | Task detail should keep the Steps surface primary while secondary evidence panels still follow the latest/current run. |
| DOC-REQ-006 | `tmp/remaining-work` step-ledger trackers | API, workflow, artifact, UI, architecture, and run-history rollout trackers should be retired once implementation and verification land. |

## User Scenarios & Testing

### User Story 1 - Execution detail follows the workflow's latest run during projection lag (Priority: P1)

An operator opens a task detail page during Continue-As-New or rerun churn and expects the visible run identity to match the workflow's latest run, not a stale projection row.

**Why this priority**: If detail reads show an old `runId` while the step ledger is already on the new run, the page becomes internally inconsistent.

**Independent Test**: Simulate a stale projection-backed execution detail record plus a workflow progress query that reports a newer `runId`, then assert the execution detail response and task-detail page follow the queried latest run.

**Acceptance Scenarios**:

1. **Given** execution detail is loaded from a stale projection row, **When** the progress query reports a newer latest run, **Then** the response uses the queried `runId` while keeping the bounded `progress` payload unchanged.
2. **Given** the Steps query reports a newer latest run than the initial detail payload, **When** Mission Control loads secondary run-scoped artifacts, **Then** those reads use the latest run rather than the stale run.

---

### User Story 2 - Latest-run semantics remain truthful through degraded reads and repair (Priority: P1)

An operator or API caller hits degraded-mode execution reads and expects truthful latest-run behavior rather than fabricated or mixed historical state.

**Why this priority**: Phase 6 exists to harden the system around failure modes after the earlier feature phases shipped.

**Independent Test**: Exercise public and unit-level reads with projection fallback, orphaned-row repair, and rerun/Continue-As-New transitions, then assert latest-run-only behavior and honest degraded responses remain intact.

**Acceptance Scenarios**:

1. **Given** Temporal sync fails but a projection fallback exists, **When** execution detail is requested, **Then** the API degrades honestly and still keeps `workflowId` anchored to one logical execution.
2. **Given** an orphaned projection row is repaired from canonical state, **When** the task detail is read again, **Then** only the latest run is surfaced and old run identities are not mixed into the default view.

---

### User Story 3 - Step-ledger rollout trackers are retired after hardening lands (Priority: P2)

An engineer reading canonical docs should no longer see already-finished step-ledger rollout work lingering in `docs/tmp/remaining-work`.

**Why this priority**: Constitution Principle XII requires finishing migration work by deleting or retiring completed rollout notes.

**Independent Test**: Review the owning tmp tracker files after implementation and confirm the step-ledger rollout bullets covered by Phases 1-6 are removed.

**Acceptance Scenarios**:

1. **Given** Phase 6 verification is complete, **When** the tmp tracker files are inspected, **Then** the step-ledger rollout bullets for API, workflow, artifact, UI, architecture, and run-history hardening are gone.

### Edge Cases

- What happens when the progress query is unavailable? Detail reads remain honest, keep `progress` as `null`, and do not fabricate a newer `runId`.
- What happens when the Steps query returns a newer run after the initial detail fetch? The task-detail page should align secondary latest-run evidence with the step-ledger run.
- What happens when the projection row is stale but still authoritative enough to authorize ownership? The router may use it for access checks, but latest-run display state should prefer workflow query truth when available.

## Requirements

### Functional Requirements

- **FR-001**: System MUST reconcile execution-detail `runId` to the workflow's latest queried run when bounded latest-run query data is available, without exposing extra non-contract fields in `progress`. Mappings: DOC-REQ-001, DOC-REQ-002, DOC-REQ-003.
- **FR-002**: System MUST keep secondary task-detail evidence reads aligned to the latest/current run once the step ledger is loaded. Mappings: DOC-REQ-001, DOC-REQ-004, DOC-REQ-005.
- **FR-003**: System MUST preserve honest degraded-read behavior for projection fallback and repair flows, keeping `workflowId` stable and latest-run-only semantics intact. Mappings: DOC-REQ-002, DOC-REQ-003.
- **FR-004**: System MUST add automated coverage for rerun / Continue-As-New latest-run semantics at the workflow-boundary, router/contract, and Mission Control levels where applicable. Mappings: DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-005.
- **FR-005**: System MUST retire the completed step-ledger rollout bullets from the tmp tracking docs once the hardening work is verified. Mappings: DOC-REQ-006.
- **FR-006**: System MUST implement production runtime code changes plus validation tests in this phase; docs-only cleanup is insufficient. Mappings: DOC-REQ-001 through DOC-REQ-005.

### Key Entities

- **LatestRunExecutionRead**: The execution-detail payload after reconciling bounded progress-query latest-run truth with the projection-backed base record.
- **RunScopedArtifactRead**: Task-detail artifact fetch behavior keyed to the latest/current run rather than a stale detail snapshot.
- **ProjectionRepairReadMode**: Truthful degraded/read-repair behavior for detail reads when Temporal sync or projection state drifts.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Unit and contract tests prove execution detail prefers the queried latest `runId` during projection lag while keeping `progress` bounded.
- **SC-002**: Browser tests prove secondary artifact reads on task detail follow the latest run exposed by the step ledger.
- **SC-003**: Existing repair/degraded-read tests continue passing with no mixed-run regressions.
- **SC-004**: `pytest tests/unit/api/routers/test_executions.py -q`, `pytest tests/contract/test_temporal_execution_api.py -q`, `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`, and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx tests/unit/api/routers/test_executions.py tests/contract/test_temporal_execution_api.py` pass.

## Assumptions

- Phase 6 hardens the latest-run read path and rollout cleanup; it does not introduce a new historical-runs UI.
- Projection repair behavior already exists and is being tightened, not redesigned.
- The bounded `progress` API contract remains unchanged externally even if the workflow/router exchange carries extra internal reconciliation metadata.
