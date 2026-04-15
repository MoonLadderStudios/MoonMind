# Feature Specification: Typed Temporal Activity Calls

**Feature Branch**: `172-typed-activity-calls`
**Created**: 2026-04-15
**Status**: Draft
**Input**: User description: "MM-328: Enforce typed Temporal payload conversion and activity calls

User Story
As a MoonMind workflow maintainer, I need Temporal clients, workers, and workflow activity call sites to share typed payload conversion and typed execution helpers so model annotations match the serialized wire shape and provider-specific data cannot leak into workflow histories.
Source Document
docs/Temporal/TemporalTypeSafety.md
Source Sections
- 3.4 The data converter is part of the contract
- 3.6 Determinism remains the workflow rule
- 5 Activities
- 5.4 Provider-specific data stops at the activity boundary
- 5.5 Compatibility shims are narrow and temporary
Coverage IDs
- DESIGN-REQ-004
- DESIGN-REQ-006
- DESIGN-REQ-009
- DESIGN-REQ-010
- DESIGN-REQ-011
Story Metadata
- Story ID: STORY-002
- Short name: typed-activity-calls
- Breakdown JSON: docs/tmp/story-breakdowns/mm-316-breakdown-docs-temporal-temporaltypesafet-c8c0a38c/stories.json

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the request is "Implement Docs/<path>.md", treat it as runtime intent and use the document as source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes plus validation tests.

## User Story - Enforce Typed Activity Calls

**Summary**: As a MoonMind workflow maintainer, I want Temporal clients, workers, and activity call sites to share typed payload conversion and typed execution helpers so workflow histories carry canonical MoonMind contracts instead of ad hoc provider-shaped dictionaries.

**Goal**: Temporal-facing code validates activity inputs and structured outputs through shared typed contracts, while nondeterministic provider work remains inside activities and provider-specific payloads are normalized before workflows observe them.

**Independent Test**: Run Temporal boundary and workflow unit tests that pass typed activity request models through the shared payload converter and typed execution helper, then assert workflows receive canonical MoonMind result models rather than provider-shaped dictionaries.

**Acceptance Scenarios**:

1. **Given** a Temporal client or worker is created, **When** it connects to Temporal, **Then** it uses the shared MoonMind Pydantic-aware data converter policy.
2. **Given** a workflow calls a migrated activity, **When** it constructs the activity argument, **Then** the argument is a named typed request model and the call goes through the typed execution facade.
3. **Given** an external provider activity returns provider-shaped status or result data internally, **When** the workflow receives the activity response, **Then** the workflow-facing value is a canonical MoonMind activity/result model or is immediately validated into one.
4. **Given** a legacy dict-shaped payload still reaches a public activity boundary for in-flight compatibility, **When** the activity starts processing it, **Then** it validates the dict into the canonical request model before business logic runs.

### Edge Cases

- Legacy aliases such as `external_id` or `run_id` may appear at activity entry; they must be accepted only by boundary validation and serialized from canonical models afterward.
- Unknown fields in new typed activity request models must fail validation rather than silently entering workflow history.
- Provider status/result dictionaries must not be returned directly from migrated activities to workflow logic.
- Data converter configuration must be shared through one importable contract so clients and workers cannot drift.

## Assumptions

- This story targets representative high-risk managed and external agent runtime activity calls rather than every remaining historical Temporal boundary in one change.
- Existing stable activity type strings are preserved.
- Compatibility handling for already-running histories remains at public activity edges only.

## Source Design Requirements

- **DESIGN-REQ-004**: Source `docs/Temporal/TemporalTypeSafety.md` section 3.4. Temporal clients and workers must share a Pydantic-aware payload conversion policy, with legacy JSON-shaped boundaries immediately validating into canonical models. Scope: in scope. Mapped to FR-001, FR-002.
- **DESIGN-REQ-006**: Source `docs/Temporal/TemporalTypeSafety.md` section 3.6. Type-safety changes must not move network, filesystem, provider inspection, clocks, subprocesses, or mutable external reads into workflow code. Scope: in scope. Mapped to FR-007.
- **DESIGN-REQ-009**: Source `docs/Temporal/TemporalTypeSafety.md` section 5 and 5.3. Activities expose single typed request models and named structured return models, while workflow call sites construct typed requests through typed execution facades. Scope: in scope. Mapped to FR-003, FR-004, FR-005.
- **DESIGN-REQ-010**: Source `docs/Temporal/TemporalTypeSafety.md` section 5.4. Provider-specific payloads terminate inside activities and workflow-facing values use MoonMind canonical contracts. Scope: in scope. Mapped to FR-006.
- **DESIGN-REQ-011**: Source `docs/Temporal/TemporalTypeSafety.md` section 5.5. Legacy dict acceptance is narrow, temporary, boundary-only, and immediately validates into canonical models. Scope: in scope. Mapped to FR-002, FR-008.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Temporal client construction MUST use a shared MoonMind Pydantic-aware data converter contract for Temporal-facing calls.
- **FR-002**: Temporal worker construction MUST receive the same shared data converter policy through the client used to run workers.
- **FR-003**: Migrated activity inputs MUST use named Pydantic v2 request models with unknown fields rejected by default.
- **FR-004**: Migrated workflow activity call sites MUST construct typed request models instead of inline raw dictionaries.
- **FR-005**: Migrated workflow activity call sites MUST use the typed execution facade for activity execution.
- **FR-006**: Migrated provider or runtime activities MUST return canonical MoonMind response models or validate returned dictionaries into canonical MoonMind response models before workflow logic consumes them.
- **FR-007**: Workflow code MUST keep provider inspection, environment reads, filesystem I/O, subprocesses, network calls, and other nondeterministic work inside activities.
- **FR-008**: Any retained legacy dict-shaped activity acceptance MUST exist only at the public boundary and validate into the canonical request model before business logic runs.

### Key Entities

- **Shared Temporal Data Converter**: The importable MoonMind contract used by clients and workers to serialize and deserialize Temporal payloads.
- **Typed Activity Request**: A named Pydantic v2 model representing a single public activity argument.
- **Typed Execution Facade**: The workflow-side helper that wraps Temporal activity execution with overloads for known activity request and response types.
- **Canonical Agent Runtime Result**: MoonMind-owned response models such as `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult` that workflows may safely store in history.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit coverage proves Temporal clients use the shared MoonMind data converter contract.
- **SC-002**: Unit coverage proves migrated activity request models reject unknown fields and accept only documented legacy aliases at the public boundary.
- **SC-003**: Workflow unit coverage proves external and managed runtime polling/fetch/cancel calls are made with typed request model instances.
- **SC-004**: Temporal boundary coverage proves a typed activity request can round-trip through a real Temporal test worker and return a typed canonical model.
- **SC-005**: Targeted tests for migrated modules pass through the repo test runner.
