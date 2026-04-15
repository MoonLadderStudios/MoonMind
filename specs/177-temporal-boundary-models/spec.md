# Feature Specification: Temporal Boundary Models

**Feature Branch**: `177-temporal-boundary-models`  
**Created**: 2026-04-15  
**Status**: Draft  
**Input**:

```text
Jira issue: MM-327 from TOOL board
Summary: MM-316: Inventory and model Temporal public boundaries
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-327 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-327: MM-316: Inventory and model Temporal public boundaries

User Story
As a MoonMind maintainer, I need every public Temporal boundary inventoried and represented by named Pydantic v2 request/response models so workflow, activity, message, query, and continuation payloads are reviewable serialized contracts instead of ad hoc dictionaries.

Source Document
docs/Temporal/TemporalTypeSafety.md

Source Sections
- 1 Purpose; 3.1 Boundary types are real contracts
- 3.2 One structured argument per boundary; 4.1 Request/response models
- 3.3 Pydantic v2; 4.2 Model configuration
- 4.1 Request/response models; 14 Canonical implementation anchors
- 4.3 No anonymous JSON islands; 4.4 No generic type variables
- Header; 14 Canonical implementation anchors

Coverage IDs
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-021

Story Metadata
- Story ID: STORY-001
- Short name: temporal-boundary-models
- Breakdown JSON: docs/tmp/story-breakdowns/mm-316-breakdown-docs-temporal-temporaltypesafet-c8c0a38c/stories.json

Acceptance Criteria
- Every inventoried public Temporal boundary has an owning named request model and, where applicable, named response or snapshot model.
- Boundary models use Pydantic v2 and reject unknown fields by default unless a documented escape hatch applies.
- Known business, idempotency, billing, routing, and operator-visible fields are named model fields, not hidden in parameters/options/payload JSON blobs.
- Approved schema homes and implementation anchors are used or a precise domain schema module rationale is documented.
- No activity or workflow type string is renamed as part of the modeling work.

Requirements
- Model workflow, activity, message, query, and continuation payloads as serialized contracts.
- Default to one structured request argument and one structured response model for public Temporal entrypoints.
- Use strict Pydantic v2 configuration and concrete collection element types.
- Keep canonical docs desired-state-only while any implementation tracking remains in docs/tmp.

Independent Test
Run focused schema tests that instantiate representative boundary models, verify aliases and normalization, reject unknown fields, exercise enum/literal validation, and confirm no unresolved generic or raw dict model fields are introduced for known workflow-control data.

Dependencies
- None

Out of Scope
- Changing Temporal activity or workflow type names.
- Implementing data converter rollout or workflow call-site migration beyond what is needed to compile schema references.
- Creating specs directories or implementation task lists.

Source Design Coverage
- DESIGN-REQ-001: Defines the public Temporal boundary inventory as contracts.
- DESIGN-REQ-002: Owns the one-request/one-response model target shape.
- DESIGN-REQ-003: Owns Pydantic v2 model defaults and validation policy.
- DESIGN-REQ-007: Places models in approved schema homes and anchors.
- DESIGN-REQ-008: Prevents anonymous JSON islands and unresolved generics.
- DESIGN-REQ-021: Keeps implementation tracking out of canonical docs.

Needs Clarification
- None

Notes
This establishes the canonical contract surface and schema homes that all later type-safety enforcement depends on.
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Temporal Boundary Contract Inventory

**Summary**: As a MoonMind maintainer, I want a deterministic inventory of public Temporal boundaries and their named typed contracts so that workflow and activity payload shapes are reviewable before broader type-safety migration work proceeds.

**Goal**: Maintainers can inspect and test one canonical boundary inventory that identifies each covered public workflow, message, query, continuation, and activity contract by stable Temporal name, owning model, schema home, and any explicitly documented compatibility escape hatch.

**Independent Test**: Run focused boundary-inventory schema tests that instantiate the inventory entries, verify model aliases and strict validation, reject unknown fields for new contract models, prove no covered boundary is represented only by an ad hoc dictionary, and confirm Temporal activity/workflow type names remain unchanged.

**Acceptance Scenarios**:

1. **Given** the Temporal boundary inventory is loaded, **When** a maintainer inspects covered activity and workflow boundaries, **Then** every entry includes a stable boundary kind, Temporal name, request model, optional response or snapshot model, schema home, and source requirement coverage.
2. **Given** a covered boundary has no typed response or uses a compatibility shape, **When** the inventory is validated, **Then** the entry must include an explicit rationale rather than silently treating the boundary as fully modeled.
3. **Given** a boundary contract model is instantiated with extra unknown fields or blank identifiers, **When** validation runs, **Then** the model rejects the invalid payload unless the field is an explicitly bounded metadata escape hatch.
4. **Given** the activity catalog and workflow message names already exist, **When** the boundary inventory is compared against those names, **Then** this story does not rename or remove any existing activity, workflow, signal, update, or query type.
5. **Given** implementation-tracking notes are needed for unmigrated or compatibility-sensitive surfaces, **When** the inventory documents them, **Then** the notes live under `docs/tmp/` and canonical docs remain desired-state only.

### Edge Cases

- Boundaries that are intentionally bytes contracts must identify the explicit binary model or serializer instead of embedding raw bytes in a JSON-shaped payload.
- Bounded metadata bags may remain only as annotated metadata and cannot hide workflow-control fields.
- Existing compatibility shims may be listed only when their boundary status and rationale are explicit.
- The referenced Jira breakdown path may be stale; the current repository handoff path is `docs/tmp/story-breakdowns/breakdown-docs-temporal-temporaltypesafety-md-in-9e0bd9a2/stories.json`.

## Assumptions

- This story establishes the reviewable boundary inventory and representative typed models needed before broad call-site migration, not the full conversion of every Temporal caller.
- Existing public Temporal names are compatibility-sensitive and must remain unchanged.
- The current repository handoff path supersedes the stale generated breakdown path in the Jira brief for source inspection only; the original Jira brief remains preserved above.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/Temporal/TemporalTypeSafety.md` sections 1 and 3.1 require public Temporal workflow, activity, message, query, and continuation boundaries to be explicit reviewable contracts. Scope: in scope. Maps to FR-001 and FR-002.
- **DESIGN-REQ-002**: Source sections 3.2 and 4.1 require one structured request model per boundary and named response or snapshot models where structured data is returned. Scope: in scope. Maps to FR-002 and FR-003.
- **DESIGN-REQ-003**: Source sections 3.3 and 4.2 require Pydantic v2 boundary models with strict defaults, stable aliases, nonblank identifier normalization, and enum/literal validation where applicable. Scope: in scope. Maps to FR-004.
- **DESIGN-REQ-007**: Source sections 4.1 and 14 define approved schema homes and implementation anchors for Temporal boundary models and review surfaces. Scope: in scope. Maps to FR-005.
- **DESIGN-REQ-008**: Source sections 4.3 and 4.4 prohibit anonymous JSON islands and unresolved generics for known workflow-control data. Scope: in scope. Maps to FR-006.
- **DESIGN-REQ-021**: Source header and section 14 require canonical docs to remain desired-state-only while implementation tracking stays under `docs/tmp/`. Scope: in scope. Maps to FR-007.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a deterministic inventory of covered public Temporal boundaries with stable Temporal names, boundary kinds, schema homes, and source design coverage.
- **FR-002**: Each inventory entry MUST identify an owning named request model for the covered boundary.
- **FR-003**: Each inventory entry that returns structured data MUST identify a named response or snapshot model, or include an explicit typed-contract rationale when no response model applies yet.
- **FR-004**: New or updated boundary contract models MUST use Pydantic v2, stable aliases, nonblank normalization for identifiers, strict extra-field rejection by default, and enum/literal validation for closed values.
- **FR-005**: Inventory entries MUST place request, response, and snapshot models in approved schema homes or state a precise domain schema module rationale.
- **FR-006**: Covered workflow-control data MUST be represented by named fields instead of anonymous `parameters`, `options`, or `payload` blobs unless the inventory marks a bounded compatibility escape hatch.
- **FR-007**: Implementation tracking for incomplete or compatibility-sensitive boundary migration MUST be written under `docs/tmp/` and MUST NOT turn canonical `docs/Temporal/TemporalTypeSafety.md` into a migration checklist.
- **FR-008**: Existing Temporal activity, workflow, signal, update, query, and Continue-As-New type names MUST remain unchanged by this story.

### Key Entities

- **Temporal Boundary Entry**: A reviewable record for one public Temporal contract, including boundary kind, stable name, model ownership, schema home, coverage IDs, and compatibility status.
- **Boundary Contract Model**: A named Pydantic v2 request, response, or snapshot model that defines the serialized payload shape for a boundary.
- **Compatibility Escape Hatch**: An explicitly documented temporary boundary allowance with rationale and source coverage.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Boundary-inventory tests verify at least one covered workflow input, workflow message/query contract, activity input/output contract, and Continue-As-New state contract.
- **SC-002**: Schema tests demonstrate strict extra-field rejection and identifier normalization for new boundary inventory models and representative contract references.
- **SC-003**: A regression test confirms covered Temporal activity and workflow/message names in the inventory match the existing catalog or workflow constants and are not renamed.
- **SC-004**: A docs/tmp implementation tracker records incomplete or compatibility-sensitive boundary modeling work without modifying canonical desired-state documentation.
