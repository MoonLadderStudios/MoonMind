# Feature Specification: Step Ledger Phase 2

**Feature Branch**: `138-step-ledger-phase2`  
**Created**: 2026-04-08  
**Status**: Draft  
**Input**: User description: "Implement Phase 2 using test-driven development of the step ledger plan. Wire evidence and parent/child refs into the workflow-owned step ledger without bloating workflow history, keeping API and UI phase work out of scope."

## Source Document Requirements

Source: `docs/Temporal/StepLedgerAndProgressModel.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/Temporal/WorkflowArtifactSystemDesign.md`, `docs/tmp/remaining-work/Temporal-WorkflowArtifactSystemDesign.md`, `docs/Tasks/TaskRunsApi.md`

| ID | Source Section | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `StepLedgerAndProgressModel` §11.1 | `MoonMind.Run` step rows must carry bounded `childWorkflowId`, `childRunId`, and `taskRunId` refs rather than inferring lineage from logs. |
| DOC-REQ-002 | `StepLedgerAndProgressModel` §11.2 | The workflow/server side must group step evidence into canonical artifact slots `outputSummary`, `outputPrimary`, `runtimeStdout`, `runtimeStderr`, `runtimeMergedLogs`, `runtimeDiagnostics`, and `providerSnapshot`. |
| DOC-REQ-003 | `StepLedgerAndProgressModel` §11.2 | Step-scoped artifact linkage metadata should include `step_id`, `attempt`, and optional `scope`. |
| DOC-REQ-004 | `StepLedgerAndProgressModel` §3.3, §13 | Logs, diagnostics, provider payloads, and other large evidence must stay out of workflow state and out of Memo/Search Attributes. |
| DOC-REQ-005 | `ManagedAndExternalAgentExecutionModel` §10.1, §10.2 | The parent step ledger should point at observability refs from managed/external agent runs instead of duplicating log bodies, and managed-run metadata should surface stdout/stderr/merged/diagnostics refs. |
| DOC-REQ-006 | `WorkflowArtifactSystemDesign` §5.2, remaining work | Artifact publication should keep durable evidence in artifacts while exposing compact refs, and Phase 2 should standardize step-scoped metadata for those artifacts. |
| DOC-REQ-007 | `TaskRunsApi` §3-§4 | `taskRunId` on a step row is the managed-run observability handle used for task-run log and diagnostics surfaces. |
| DOC-REQ-008 | `StepLedgerAndProgressModel` §12 | Later API/UI phases must be able to consume the workflow-owned step rows unchanged, so Phase 2 must keep the Phase 1 row schema stable while filling the reserved refs/artifact fields. |

## User Scenarios & Testing

### User Story 1 - Parent steps expose child lineage refs (Priority: P1)

A platform developer needs parent step rows to include child workflow and task-run lineage so later API/UI work can expand one step without guessing runtime ancestry from logs.

**Why this priority**: Child lineage is the core Phase 2 prerequisite for observability drilldown and latest-run-only task detail.

**Independent Test**: Can be tested by executing or simulating an `agent_runtime` step and asserting that the step row exposes bounded `childWorkflowId`, `childRunId`, and `taskRunId` fields while leaving log bodies out of workflow state.

**Acceptance Scenarios**:

1. **Given** an `agent_runtime` plan node launches `MoonMind.AgentRun`, **When** the parent step enters its waiting state, **Then** the row captures the child workflow lineage refs without waiting for artifacts or log hydration.
2. **Given** the child workflow completes and exposes managed-run observability metadata, **When** the parent updates the latest step row, **Then** the row includes the canonical `taskRunId` used by task-run observability routes.
3. **Given** child execution fails with diagnostics available, **When** the parent stores evidence refs, **Then** only bounded refs and summaries are stored in workflow state.

---

### User Story 2 - Parent steps expose grouped evidence slots (Priority: P1)

An operator needs one canonical step-evidence grouping so later API/UI phases can expand a step row into logs, diagnostics, and outputs without client-side artifact guessing.

**Why this priority**: Phase 2 is the server-side evidence wiring phase; later API/UI work depends on these slots already being deterministically populated.

**Independent Test**: Can be tested by feeding representative managed-runtime and skill execution results into the workflow and asserting that `artifacts.*` slots are filled deterministically from compact refs.

**Acceptance Scenarios**:

1. **Given** a managed-runtime result includes summary and runtime observability refs, **When** the parent records the result, **Then** `outputSummary`, `outputPrimary`, `runtimeStdout`, `runtimeStderr`, and `runtimeDiagnostics` are grouped into the step row.
2. **Given** a result exposes only generic `outputRefs` plus a diagnostics ref, **When** the parent groups evidence, **Then** it picks deterministic canonical slots without reading artifact bodies.
3. **Given** a step has no ref for one or more evidence types, **When** the row is returned, **Then** the corresponding slots remain present and `null`.

---

### User Story 3 - Published artifacts carry step-scoped metadata (Priority: P2)

A backend consumer needs artifact publication to stamp step identity and attempt metadata so later projection/API layers can group evidence per step without reverse-engineering artifact names.

**Why this priority**: Server-side grouping is more durable when the artifact layer carries explicit step metadata instead of path heuristics.

**Independent Test**: Can be tested by publishing agent-runtime result artifacts with step context and asserting the artifact create metadata includes `step_id`, `attempt`, and `scope`.

**Acceptance Scenarios**:

1. **Given** an `agent_runtime` step publishes summary/result artifacts, **When** the activity writes those artifacts, **Then** the artifact metadata includes `step_id`, `attempt`, and `scope: "step"`.
2. **Given** an already-running retry attempt publishes artifacts, **When** metadata is written, **Then** the stamped attempt number matches the current parent-step attempt.
3. **Given** step context is absent, **When** publication still succeeds, **Then** artifact publishing remains backward-safe and simply omits the optional step metadata.

### Edge Cases

- What happens when a child workflow succeeds but has no managed-run observability record yet? The row should still preserve `childWorkflowId`/`childRunId` and keep `taskRunId`/artifact slots null until a later result carries them.
- What happens when a managed-session-backed step returns session artifacts rather than classic managed-run refs? The parent should still group the available stdout/stderr/diagnostics refs without inventing merged/provider refs.
- What happens when `outputRefs` contains runtime logs and output artifacts together? The grouping logic must prefer explicit metadata refs first and only use deterministic fallback selection for `outputPrimary`.
- What happens when a step retries? The row should expose only the current attempt's refs, and step-scoped artifact metadata must use the current attempt number.
- What happens when a result contains a large nested payload? The workflow must reduce it to bounded summaries and refs before mutating step state.

## Requirements

### Functional Requirements

- **FR-001**: System MUST enrich `MoonMind.AgentRun` results with bounded lineage metadata sufficient for a parent step row to capture `childWorkflowId`, `childRunId`, and `taskRunId`. Mappings: DOC-REQ-001, DOC-REQ-005, DOC-REQ-007.
- **FR-002**: System MUST update `MoonMind.Run` step rows from compact execution-result metadata so the latest row for a step exposes canonical `refs` and `artifacts` fields without reading artifact bodies. Mappings: DOC-REQ-001, DOC-REQ-002, DOC-REQ-004, DOC-REQ-008.
- **FR-003**: System MUST deterministically map managed-run/session refs into `runtimeStdout`, `runtimeStderr`, `runtimeMergedLogs`, and `runtimeDiagnostics`, and map durable summary/output refs into `outputSummary` and `outputPrimary`. Mappings: DOC-REQ-002, DOC-REQ-005, DOC-REQ-006.
- **FR-004**: System MUST keep step rows, Memo, Search Attributes, and workflow payloads free of raw log bodies, diagnostics bodies, and provider dumps. Mappings: DOC-REQ-004, DOC-REQ-005.
- **FR-005**: System MUST stamp step-scoped metadata (`step_id`, `attempt`, optional `scope`) onto published agent-runtime artifacts when step context is available. Mappings: DOC-REQ-003, DOC-REQ-006.
- **FR-006**: System MUST preserve the Phase 1 step-ledger schema unchanged, filling only the previously reserved `refs` and `artifacts` fields. Mappings: DOC-REQ-008.
- **FR-007**: System MUST implement production runtime code changes plus validation tests in this phase; documentation-only work is insufficient. Mappings: DOC-REQ-001, DOC-REQ-002, DOC-REQ-005.

### Key Entities

- **StepLedgerEvidenceContext**: Compact step-scoped context containing `logicalStepId`, current `attempt`, and optional `scope` for artifact publication.
- **StepLedgerRefs**: Parent-visible lineage refs populated from child workflow execution and managed-run observability metadata.
- **StepLedgerArtifacts**: Canonical grouped evidence slots on a latest-run step row.
- **ManagedRunObservabilityMetadata**: Compact runtime metadata exposing `taskRunId` plus stdout/stderr/merged/diagnostics refs without embedding those artifacts in workflow history.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Workflow tests prove an `agent_runtime` step row captures `childWorkflowId`, `childRunId`, and `taskRunId` while keeping log/diagnostic bodies out of workflow state.
- **SC-002**: Workflow tests prove canonical artifact slots are populated from representative managed-runtime and skill execution results using refs only.
- **SC-003**: Activity/runtime tests prove published agent-runtime artifacts receive step-scoped metadata when context is available.
- **SC-004**: Final verification passes through targeted step-ledger/activity tests and `./tools/test_unit.sh`.

## Assumptions

- Phase 2 stops at workflow/runtime evidence wiring; adding `ExecutionModel.progress`, `GET /api/executions/{workflowId}/steps`, and Mission Control step UI work remains out of scope for this change.
- Existing managed-run/session systems already publish durable stdout/stderr/diagnostics artifacts; Phase 2 only needs to surface their refs through the step ledger.
- When a provider-specific snapshot ref does not yet exist, `providerSnapshot` may remain `null` as long as the slot remains stable.
