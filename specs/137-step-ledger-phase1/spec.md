# Feature Specification: Step Ledger Phase 1

**Feature Branch**: `137-step-ledger-phase1`  
**Created**: 2026-04-07  
**Status**: Draft  
**Input**: User description: "Implement Phases 0 and 1 of the step ledger and progress rollout using test-driven development where feasible. Freeze the v1 execution progress and step-ledger contract, then implement workflow-owned deterministic step ledger state and workflow queries in MoonMind.Run without shipping the API or UI phases yet."

## Source Document Requirements

Source: `docs/Temporal/StepLedgerAndProgressModel.md`, `docs/tmp/remaining-work/Temporal-WorkflowTypeCatalogAndLifecycle.md`, `docs/tmp/remaining-work/Api-ExecutionsApiContract.md`

| ID | Source Section | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `StepLedgerAndProgressModel` §3.1 | Planned step identity, order, title, tool, and dependencies must come from resolved plan-node metadata, not from logs or ad hoc counters. |
| DOC-REQ-002 | `StepLedgerAndProgressModel` §3.2 | `MoonMind.Run` must own compact step status, attempt, waiting state, check state, and current refs as workflow state. |
| DOC-REQ-003 | `StepLedgerAndProgressModel` §3.3 | Large outputs, logs, diagnostics, provider payloads, and long review bodies must stay out of workflow state. |
| DOC-REQ-004 | `StepLedgerAndProgressModel` §4 | Task detail and default step reads are anchored on `workflowId` and return latest/current run state only. |
| DOC-REQ-005 | `StepLedgerAndProgressModel` §5 | Execution detail progress must be a bounded summary object derivable from workflow state without artifact hydration. |
| DOC-REQ-006 | `StepLedgerAndProgressModel` §6 | `MoonMind.Run` must expose a query-safe step-ledger response that includes `workflowId`, `runId`, `runScope`, and `steps`. |
| DOC-REQ-007 | `StepLedgerAndProgressModel` §7 | Each step row must include stable identity, current attempt, timestamps, bounded summary/error, checks, refs, and artifact slots. |
| DOC-REQ-008 | `StepLedgerAndProgressModel` §8 | The canonical v1 step status vocabulary is `pending`, `ready`, `running`, `awaiting_external`, `reviewing`, `succeeded`, `failed`, `skipped`, `canceled`. |
| DOC-REQ-009 | `StepLedgerAndProgressModel` §9 | `checks[]` is the canonical structured check/review surface on a step row. |
| DOC-REQ-010 | `StepLedgerAndProgressModel` §10 | Step attempts are scoped by `(workflowId, runId, logicalStepId, attempt)` and do not merge across run IDs. |
| DOC-REQ-011 | `StepLedgerAndProgressModel` §12 | Step rows, attempts, and checks must stay out of Search Attributes and Memo; only compact user-visible summaries may be mirrored there. |
| DOC-REQ-012 | `Temporal-WorkflowTypeCatalogAndLifecycle` remaining work | Lifecycle coverage must include plan resolved, step ready, step started, step reviewing, step succeeded, step failed, and step canceled transitions. |
| DOC-REQ-013 | `Api-ExecutionsApiContract` remaining work | The phase-0 contract freeze must include a bounded execution `progress` payload and a step-ledger response shape that later API routes can expose unchanged. |

## User Scenarios & Testing

### User Story 1 - Freeze the v1 step-ledger contract (Priority: P1)

A platform developer needs one canonical, testable step-ledger and progress contract so workflow, API, and UI work can converge on the same field names, statuses, and bounded payload rules.

**Why this priority**: Phase 1 implementation is risky without a frozen contract; otherwise backend and frontend will diverge and later phases will rework state shapes.

**Independent Test**: Can be tested by validating golden fixtures for `progress` and representative step rows against the new workflow-owned contract models without starting the API or UI layers.

**Acceptance Scenarios**:

1. **Given** a resolved plan with two executable nodes, **When** the workflow creates its initial ledger state, **Then** each row uses plan-node `id`, `title`, `tool`, and dependencies rather than derived display text from logs.
2. **Given** a representative retrying step, child-runtime step, and reviewed step, **When** the contract fixtures are validated, **Then** they expose the frozen v1 field names, status vocabulary, checks shape, refs shape, and artifact slot names.
3. **Given** a workflow step mutation, **When** the workflow mirrors operator-visible state into Memo or Search Attributes, **Then** it does not embed full step rows, attempts, or checks there.

---

### User Story 2 - Track deterministic workflow-owned step state (Priority: P1)

An operator monitoring a run needs `MoonMind.Run` to own the live step ledger so the workflow can report deterministic step status, attempts, waiting state, and bounded progress during execution and after completion.

**Why this priority**: This is the core Phase 1 implementation deliverable and the prerequisite for later API and UI rollout.

**Independent Test**: Can be tested by executing the workflow against a resolved plan and asserting deterministic transitions through `pending`, `ready`, `running`, `awaiting_external`, `reviewing`, and terminal states.

**Acceptance Scenarios**:

1. **Given** a resolved linear plan, **When** execution begins, **Then** the workflow marks only dependency-free steps as `ready` and leaves blocked steps as `pending`.
2. **Given** a step begins execution, **When** the workflow dispatches the step, **Then** the ledger records `running`, increments the run-scoped attempt number, sets `startedAt`, and updates bounded progress.
3. **Given** a step enters external wait or review processing, **When** the workflow transitions state, **Then** the ledger records `awaiting_external` or `reviewing` without storing large observability payloads in workflow state.
4. **Given** a step succeeds, fails, is skipped, or is canceled, **When** the workflow updates the ledger, **Then** the row ends in the canonical terminal status and the workflow summary/progress remain deterministic.

---

### User Story 3 - Query the latest-run ledger and progress (Priority: P2)

A control-plane consumer needs workflow queries that return the current/latest run ledger and bounded progress without reading Temporal history, artifacts, or Search Attributes.

**Why this priority**: Query surfaces are the contract that later API work depends on, and they must be safe during execution and after completion.

**Independent Test**: Can be tested by querying the workflow while it is executing and after it finishes, verifying the same latest-run ledger remains available with stable `workflowId` and `runId`.

**Acceptance Scenarios**:

1. **Given** a running workflow, **When** a caller queries step ledger state, **Then** the workflow returns `workflowId`, `runId`, `runScope: "latest"`, and the current rows without reading artifacts.
2. **Given** a completed workflow, **When** a caller queries progress or the step ledger, **Then** the same bounded progress and latest-run ledger remain available.
3. **Given** a run with multiple attempts for one logical step, **When** a caller reads the latest-run ledger, **Then** the row shows the current/latest attempt for that `runId` and does not merge prior run history.

### Edge Cases

- What happens when the plan contains dependency edges? The workflow must keep blocked nodes in `pending` and move them to `ready` only after upstream logical step completion is recorded.
- What happens when a step fails before returning outputs? The row must still end in `failed` with a bounded `lastError` summary and no large error body in workflow state.
- What happens when the workflow is canceled while a step is active? The current step must end in `canceled`, later nodes remain non-terminal, and query results must stay readable after workflow completion.
- What happens when a step has no checks or refs yet? The ledger must return empty/default structured values rather than omitting the fields.
- What happens when the workflow continues to update generic summary strings? Those summary updates must not become the source of truth for per-step identity or status.

## Requirements

### Functional Requirements

- **FR-001**: System MUST define a canonical v1 step-ledger contract and bounded `progress` contract for execution detail, including golden JSON fixtures for progress and representative step rows. Mappings: DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-013.
- **FR-002**: System MUST initialize workflow-owned ledger rows from resolved plan-node metadata (`id`, `title`, `tool`, dependency data) after plan resolution. Mappings: DOC-REQ-001, DOC-REQ-002.
- **FR-003**: System MUST track step lifecycle transitions in `MoonMind.Run` across `pending`, `ready`, `running`, `awaiting_external`, `reviewing`, `succeeded`, `failed`, `skipped`, and `canceled`. Mappings: DOC-REQ-002, DOC-REQ-008, DOC-REQ-012.
- **FR-004**: System MUST track run-scoped attempt numbers and timestamps per logical step without merging attempt history across run IDs. Mappings: DOC-REQ-010.
- **FR-005**: System MUST expose workflow queries for full latest-run step-ledger state and bounded progress while the workflow is running and after it completes. Mappings: DOC-REQ-004, DOC-REQ-005, DOC-REQ-006.
- **FR-006**: System MUST keep logs, diagnostics, provider dumps, and other large evidence out of workflow state, storing only bounded summaries, refs, and artifact-slot placeholders in the ledger. Mappings: DOC-REQ-003, DOC-REQ-007.
- **FR-007**: System MUST keep Memo and Search Attribute updates compact and user-visible, and MUST NOT store full step rows, attempts, or checks in either surface. Mappings: DOC-REQ-011.
- **FR-008**: System MUST provide structured default values for `checks`, `refs`, and `artifacts` so later API and UI phases can consume a stable schema immediately. Mappings: DOC-REQ-007, DOC-REQ-009.
- **FR-009**: System MUST add workflow-boundary tests covering plan resolved, step ready, step started, waiting, reviewing, succeeded, failed, skipped, canceled, and post-completion query behavior. Mappings: DOC-REQ-012.
- **FR-010**: System MUST implement production runtime code changes and validation tests in this phase; documentation-only changes are insufficient. Mappings: DOC-REQ-002, DOC-REQ-005, DOC-REQ-009.

### Key Entities

- **ExecutionProgress**: Bounded execution-level summary of step counts by canonical status plus the current step title and last update timestamp.
- **StepLedgerSnapshot**: Query response containing `workflowId`, `runId`, `runScope`, and ordered latest-run step rows.
- **StepLedgerRow**: Compact workflow-owned representation of one logical step's current/latest attempt for the active run.
- **StepLedgerRefs**: Structured parent/child linkage fields reserved for `childWorkflowId`, `childRunId`, and `taskRunId`.
- **StepLedgerArtifacts**: Structured semantic artifact slots reserved for summary, primary output, logs, diagnostics, and provider snapshot refs.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Golden fixture tests validate the canonical `progress` object and representative step rows for retrying, child-runtime, and reviewed steps.
- **SC-002**: Workflow-boundary tests cover plan resolved, step ready, step started, waiting, reviewing, succeeded, failed, skipped, and canceled transitions.
- **SC-003**: Step-ledger and progress queries return deterministic latest-run data during execution and after completion without reading artifacts.
- **SC-004**: Memo/Search Attribute tests prove only compact summaries are mirrored there and full step rows are not.
- **SC-005**: Final verification passes through `./tools/test_unit.sh`.

## Assumptions

- Phase 0 and Phase 1 stop at workflow-owned state and queries; `/api/executions/{workflowId}/steps`, `ExecutionModel.progress`, and Mission Control UI changes remain explicitly out of scope for this change.
- Existing plan artifacts already contain stable node IDs, titles, tool descriptors, and dependency edges sufficient to seed the ledger.
- Child workflow refs and artifact slots may remain empty/default in this phase as long as their schema is frozen and query-safe.
