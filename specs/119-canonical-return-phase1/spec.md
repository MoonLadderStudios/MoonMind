# Feature Specification: canonical-return-phase1

**Feature Branch**: `119-canonical-return-phase1`
**Created**: 2026-03-31
**Status**: Draft
**Input**: User description: "Fully implement Phase 1 of docs/Temporal/WorkflowTypeCatalogAndLifecycle.md using test-driven development."

## Source Document Requirements

- **DOC-REQ-001**: **Source:** Phase 1 Objectives. **Requirement:** The activity boundary must be the only place allowed to normalize or reject runtime/provider payloads.
- **DOC-REQ-002**: **Source:** Phase 1 Tasks. **Requirement:** Provide shared contract validation helpers for canonicalizing handles, statuses, and results, and for raising UnsupportedStatus.
- **DOC-REQ-003**: **Source:** Phase 1 Tasks. **Requirement:** Provide shared activity-family contract enforcement helpers (external provider, managed runtime).
- **DOC-REQ-004**: **Source:** Phase 1 Tasks. **Requirement:** Standardize metadata usage: `providerStatus`, `normalizedStatus`, `externalUrl`, mapping provider-specific extras strictly into `metadata`.
- **DOC-REQ-005**: **Source:** Phase 1 Tasks. **Requirement:** Prohibit provider-shaped top-level fields (`external_id`, `tracking_ref`, `provider_status`, raw status block) from crossing the workflow boundary.
- **DOC-REQ-006**: **Source:** Phase 1 Tasks. **Requirement:** Add tests that assert malformed activity return shapes fail validation at the boundary, not inside the workflow.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Activity Boundary Validation Helper Enforcement (Priority: P1)

Developers interacting with agent activity return boundaries require robust helpers to ensure payloads are fully normalized to MoonMind canonical formats without needing downstream validation in the workflow code.

**Why this priority**: It is the foundation for enforcing canonical data structures before they reach the workflow layer, eliminating workflow-side repair necessity.

**Independent Test**: Can be fully tested by creating mock activities that attempt to return raw provider data shapes or malformed data, and ensuring that the newly introduced helpers correctly convert, reject, or filter the items according to Canonical schemas.

**Acceptance Scenarios**:

1. **Given** an activity receives data with non-canonical fields, **When** the new validation helper is utilized, **Then** all unexpected provider-shaped fields are stripped or embedded under `metadata`.
2. **Given** an activity returns an unknown status state, **When** evaluated via the contract-enforcement helper, **Then** an UnsupportedStatus exception is explicitly raised at the boundary.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST strictly enforce canonical return signatures for all external and internally-managed activity returns (Mappings: DOC-REQ-001, DOC-REQ-005).
- **FR-002**: System MUST implement `build_canonical_start_handle`, `build_canonical_status`, and `build_canonical_result` validation helpers (Mappings: DOC-REQ-002).
- **FR-003**: System MUST implement `raise_unsupported_status` helper for unregistered provider and runtime states (Mappings: DOC-REQ-002).
- **FR-004**: System MUST implement external provider and managed runtime specific contract-enforcement abstractions if beneficial for encapsulation (Mappings: DOC-REQ-003).
- **FR-005**: System MUST validate `providerStatus`, `normalizedStatus`, `externalUrl`, and any ad-hoc data as cleanly compartmentalized metadata mapping (Mappings: DOC-REQ-004).
- **FR-006**: System MUST supply failing test cases simulating invalid runtime payload shapes asserting the validation layer traps the failure prior to workflow evaluation (Mappings: DOC-REQ-006).

### Key Entities

- **AgentRunHandle**: Canonical reference pointer returned continuously throughout the agent run sequence.
- **AgentRunStatus**: Canonical payload providing deterministic snapshot properties regarding task run progress.
- **AgentRunResult**: Final canonical output.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of the functional helpers listed in Phase 1 plan are implemented and fully unit-tested independently of live provider integrations.
- **SC-002**: Existent workflow pipelines should remain unaffected but malformed payload attempts intentionally fail at the new enforcement boundary tests.
