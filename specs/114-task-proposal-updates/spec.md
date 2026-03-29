# Feature Specification: Task Proposal System Plan Phase 1 and Phase 3 Implementation

**Feature Branch**: `114-task-proposal-updates`  
**Created**: 2026-03-28  
**Status**: Draft  
**Input**: User description: "Fully implement Phase 1 and Phase 3 of docs/tmp/015-TaskProposalSystemPlan.md"

## Source Document Requirements

*Note: As instructed by speckit-specify for document-backed intents, these requirements are extracted from `docs/tmp/015-TaskProposalSystemPlan.md`.*

### Phase 1: Contract Alignment

- **DOC-REQ-001**: Add `defaultRuntime` to `TaskProposalPolicy` and validate it against supported task runtimes.
- **DOC-REQ-002**: Standardize proposal payloads on the Temporal submit contract used by `/api/executions`.
- **DOC-REQ-003**: Remove `agent_runtime` as the documented proposal payload tool type.
- **DOC-REQ-004**: Preserve raw `task.proposalPolicy` in run `initialParameters` instead of flattened `proposalTargets`/`proposalMaxItems`/`proposalDefaultRuntime`.
- **DOC-REQ-005**: Normalize origin metadata to snake_case and `origin.source = "workflow"`.
- **DOC-REQ-006**: Update proposal API schemas to expose promotion linkage and returned execution metadata cleanly.

### Phase 3: Proposal Stage Hardening

- **DOC-REQ-007**: Enforce the workflow-level global proposal enable switch in the run workflow or submit path.
- **DOC-REQ-008**: Stop relying on flattened `proposalTargets`, `proposalMaxItems`, and `proposalDefaultRuntime` as the primary proposal-stage contract.
- **DOC-REQ-009**: Resolve proposal defaults plus per-task overrides inside the proposal stage or `proposal.submit`.
- **DOC-REQ-010**: Stamp `defaultRuntime` onto candidate payloads only when the candidate omits a runtime.
- **DOC-REQ-011**: Ensure finish summary data records requested, generated, submitted, and error outcomes consistently.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Maintain execution history correctly on proposal generation (Priority: P1)

The system accurately routes global `TaskProposalPolicy` overrides when submitting a run, effectively determining what features to propose without losing runtime target tracking.

**Why this priority**: Correct API payloads allow integration layers/mission control to understand exactly what generated tasks and where they belong.

**Independent Test**: Can be fully tested by creating a new `MoonMind.Run` payload that dictates strict policy requirements, reviewing the proposal summary, and ensuring the final state correctly propagates policies exactly into the proposal activity inputs.

**Acceptance Scenarios**:
1. **Given** a run initialized with a specific `TaskProposalPolicy` containing `defaultRuntime`, **When** the workflow enters the proposal stage, **Then** it correctly resolves those policies rather than parsing flat override parameters.
2. **Given** global proposal generation disablement, **When** a workflow runs with task proposal generation enabled, **Then** proposal execution is circumvented entirely.

### User Story 2 - Complete API representations for Proposing Tasks (Priority: P2)

Operators interacting with the API can see exact lifecycle connections representing whether a task successfully spawned a new `MoonMind.Run` or stalled.

**Why this priority**: Enables precise UI mapping of the generated task linkages internally within dashboards.

**Independent Test**: Can be tested by polling the API for execution outcomes once the task proposal is generated.

**Acceptance Scenarios**:
1. **Given** an API request to poll a finished proposal, **When** examining the outcome, **Then** the promotion identifier mapped exactly indicates the promoted `MoonMind.Run`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST process `TaskProposalPolicy` structures retaining an explicit `defaultRuntime` parameter validated strictly against system supported runtimes. (Maps to **DOC-REQ-001**)
- **FR-002**: System MUST standardize proposed candidate payloads with matching Temporal schemas for parity with `/api/executions`. (Maps to **DOC-REQ-002**)
- **FR-003**: System MUST eliminate any residual schema footprint of `agent_runtime` acting as a tool. (Maps to **DOC-REQ-003**)
- **FR-004**: System MUST strictly relay canonical JSON values defining `task.proposalPolicy` into run `initialParameters` instead of destructured keys. (Maps to **DOC-REQ-004**, **DOC-REQ-008**)
- **FR-005**: System MUST assign metadata origin elements formatted strictly as `snake_case` specifically noting `origin.source = "workflow"`. (Maps to **DOC-REQ-005**)
- **FR-006**: System MUST supply the related generated `execution_id` corresponding with promoted proposals upon fetch. (Maps to **DOC-REQ-006**)
- **FR-007**: System MUST abide by global configuration gates intercepting proposals uniformly across the whole operational queue. (Maps to **DOC-REQ-007**)
- **FR-008**: System MUST negotiate hierarchy differences between global proposal configurations against local per-task specifications accurately within the proposal run state. (Maps to **DOC-REQ-009**)
- **FR-009**: System MUST enforce an implicit assignment evaluating `defaultRuntime` into any proposed task attempting to submit lacking explicit run configuration. (Maps to **DOC-REQ-010**)
- **FR-010**: System MUST document run statistics spanning request issuance, internal candidate resolution, and submit tracking uniformly aligned on the overall finish summary. (Maps to **DOC-REQ-011**)

### Key Entities

- **TaskProposalPolicy**: Determines how proposals are derived and constraints placed on target configurations.
- **TaskProposal**: Candidate payloads surfaced from the proposal execution phase retaining source properties directly linking execution origins. 
- **MoonMind.Run Execution Summary**: Aggregated status tracker encapsulating statistics around proposal counts and execution outcomes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of generated task proposals retain `origin.source = "workflow"` and utilize strict `snake_case` definitions.
- **SC-002**: Workflows submitted with proposal toggle deactivated generate 0 proposal steps and complete immediately upon main task finalization.
- **SC-003**: Proposal polling queries successfully emit JSON payload responses describing the subsequent workflow id linked via promotion.
- **SC-004**: All workflow tests (`tests/integration`) correctly process standard `TaskProposalPolicy` schema logic yielding backward compatible passing regression coverage.
