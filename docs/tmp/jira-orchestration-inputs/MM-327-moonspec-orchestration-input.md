# MM-327 MoonSpec Orchestration Input

## Source

- Jira issue: MM-327
- Board scope: TOOL
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: MM-316: Inventory and model Temporal public boundaries
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`
- Repository-local source note: the Jira brief references `docs/tmp/story-breakdowns/mm-316-breakdown-docs-temporal-temporaltypesafet-c8c0a38c/stories.json`; this checkout contains the equivalent handoff at `docs/tmp/story-breakdowns/breakdown-docs-temporal-temporaltypesafety-md-in-9e0bd9a2/stories.json`

## Canonical MoonSpec Feature Request

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
