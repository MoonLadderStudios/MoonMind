# Feature Specification: Temporal Type-Safety Gates

**Feature Branch**: `176-temporal-type-gates`  
**Created**: 2026-04-15  
**Status**: Draft  
**Input**:

```text
Jira issue: MM-331 from TOOL board
Summary: MM-316: Add Temporal type-safety compatibility, replay, and review gates
Issue type: Story
Current Jira status: Backlog
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-331 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

User Story
As a reviewer of Temporal changes, I need compatibility rules, replay/in-flight tests, static analysis, and anti-pattern checks to fail unsafe type-safety migrations before they can break running workflows or reintroduce raw dictionary contracts.

Source Document
docs/Temporal/TemporalTypeSafety.md

Source Sections
- 3.5 Compatibility outranks tidiness; 10 Compatibility and evolution rules
- 11 Testing and tooling requirements
- 12 Approved escape hatches
- 13 Anti-patterns

Coverage IDs
- DESIGN-REQ-005
- DESIGN-REQ-018
- DESIGN-REQ-019
- DESIGN-REQ-020

Story Metadata
- Story ID: STORY-005
- Short name: temporal-type-gates
- Breakdown JSON: docs/tmp/story-breakdowns/mm-316-breakdown-docs-temporal-temporaltypesafet-c8c0a38c/stories.json

Acceptance Criteria
- Compatibility-sensitive workflow, message, activity, or Continue-As-New changes include replay/in-flight regression coverage or explicit versioned cutover notes.
- Static analysis, lint, or targeted tests catch raw dict activity payloads and public raw dict handlers in covered Temporal modules.
- Review gates reject unnecessary Any leaks, provider-shaped top-level workflow-facing activity results, nested raw bytes, and large conversational state in workflow history where practical.
- Escape hatches are documented as transitional and bounded.
- Additive-first evolution is the default and unsafe non-additive changes require an explicit migration plan.

Requirements
- Preserve replay and in-flight safety during contract evolution.
- Test schemas, Temporal boundary round trips, replay/in-flight compatibility, and static analysis coverage.
- Block documented anti-patterns and constrain approved escape hatches.

Independent Test
Run tests that intentionally exercise disallowed raw dict activity calls, public raw dict handlers, unknown provider-shaped status leakage, and an in-flight/replay compatibility fixture, then verify the checks fail or pass exactly as policy requires.

Dependencies
- STORY-001
- STORY-002
- STORY-003
- STORY-004

Out of Scope
- Implementing the boundary model inventory itself.
- Changing Temporal task queue topology or retry policy semantics.
- Using compatibility aliases that change billing-relevant or execution semantics.

Source Design Coverage
- DESIGN-REQ-005: Owns additive-first compatibility, replay safety, and migration planning.
- DESIGN-REQ-018: Owns the four-layer testing and tooling gate.
- DESIGN-REQ-019: Owns escape-hatch documentation and transitional status.
- DESIGN-REQ-020: Owns anti-pattern blocking and removal.

Needs Clarification
- None

Notes
Without enforcement, the target-state models can drift back to raw dicts, unsafe Any, generic messages, and replay-breaking changes.
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Temporal Type-Safety Gates

**Summary**: As a reviewer of Temporal changes, I want compatibility evidence, replay or in-flight safety checks, and anti-pattern gates to catch unsafe type-safety migrations before they can affect running workflows.

**Goal**: Reviewers can rely on repeatable safety gates to confirm Temporal contract changes preserve replay compatibility, keep escape hatches bounded, and prevent raw dictionary contracts or provider-shaped payloads from returning to workflow-facing boundaries.

**Independent Test**: Submit representative safe and unsafe Temporal contract changes to the review gates, including raw dictionary activity calls, public raw dictionary handlers, provider-shaped status leakage, nested raw bytes, large workflow-history state, bounded escape hatches, and replay or in-flight compatibility evidence. The unsafe cases must be rejected with actionable reasons and the safe compatibility-reviewed cases must pass.

**Acceptance Scenarios**:

1. **Given** a compatibility-sensitive workflow, message, activity, or Continue-As-New contract change, **When** it lacks replay or in-flight regression coverage and lacks explicit versioned cutover notes, **Then** the review gates reject it with the missing safety evidence identified.
2. **Given** a contract change evolves an existing Temporal boundary additively, **When** it includes the required compatibility evidence, **Then** the review gates allow it without requiring a non-additive migration plan.
3. **Given** a change introduces a raw dictionary activity payload, a public raw dictionary handler, a generic action envelope, or a provider-shaped top-level workflow-facing result, **When** the review gates evaluate the change, **Then** they reject the unsafe contract shape and identify the violated rule.
4. **Given** a change introduces nested raw bytes or large conversational state into workflow history, **When** the review gates evaluate the change, **Then** they reject it unless the payload is represented through an intentional compact reference or approved serialized boundary.
5. **Given** a retained compatibility escape hatch is necessary for live or replayed histories, **When** the review gates evaluate it, **Then** it is accepted only if it is documented as transitional, bounded, and justified by compatibility needs.

### Edge Cases

- Non-additive contract changes must be rejected unless they include an explicit migration or cutover plan that protects running histories.
- Additive enum or status expansion must be treated as safe only when callers and handlers tolerate unknown future values.
- Compatibility logic must remain at public Temporal boundaries and cannot become hidden business logic.
- Escape hatches cannot change execution semantics, billing-relevant values, or workflow routing behavior.
- Large or binary payload findings must distinguish intentional compact references from bodies stored directly in workflow history.

## Assumptions

- The active story is Jira issue MM-331 from the TOOL board, mapped to STORY-005 and short name `temporal-type-gates`.
- The existing breakdown handoff in `docs/tmp/story-breakdowns/breakdown-docs-temporal-temporaltypesafety-md-in-9e0bd9a2/stories.json` is the available source for the referenced MM-316 breakdown.
- Existing specs for STORY-002, STORY-003, and STORY-004 may satisfy some dependencies, while no matching STORY-001 spec was found during targeted inspection.
- The review gates may combine automated checks and documented reviewer evidence, as long as the observable outcome is deterministic pass or fail with actionable reasons.

## Source Design Requirements

- **DESIGN-REQ-005**: Source `docs/Temporal/TemporalTypeSafety.md` sections 3.5 and 10 require compatibility to outrank model tidiness, additive-first evolution to be preferred, and unsafe non-additive workflow-visible changes to include deployment safety, compatibility handling, replay testing, or an explicit cutover plan. Scope: in scope. Maps to FR-001, FR-002, FR-003.
- **DESIGN-REQ-018**: Source `docs/Temporal/TemporalTypeSafety.md` section 11 requires schema tests, Temporal boundary round-trip tests, replay or in-flight compatibility tests where compatibility is a concern, and static analysis that catches raw dictionary payloads, untyped leaks, or provider-shaped workflow-facing data. Scope: in scope. Maps to FR-001, FR-004, FR-005, FR-006.
- **DESIGN-REQ-019**: Source `docs/Temporal/TemporalTypeSafety.md` section 12 allows escape hatches only when they are explicit, transitional, bounded, and justified by compatibility needs. Scope: in scope. Maps to FR-007.
- **DESIGN-REQ-020**: Source `docs/Temporal/TemporalTypeSafety.md` section 13 discourages raw dictionary activity payloads, public raw dictionary handlers, generic action envelopes, nested raw bytes, provider-specific top-level activity results, unnecessary `Any`, and large conversational workflow-history state. Scope: in scope. Maps to FR-004, FR-005, FR-006.

## Requirements

### Functional Requirements

- **FR-001**: Compatibility-sensitive Temporal contract changes MUST include replay or in-flight regression evidence, or explicit versioned cutover notes, before the review gates can pass them.
- **FR-002**: Temporal contract evolution MUST default to additive changes, and non-additive changes MUST be rejected unless they include a migration or cutover plan that preserves running workflow safety.
- **FR-003**: Review outcomes MUST distinguish safe additive evolution from unsafe field removal, field renaming, semantic field changes, replay-visible message ordering changes, and in-place activity or workflow type changes.
- **FR-004**: Review gates MUST reject raw dictionary activity payloads, public raw dictionary workflow handlers, generic action envelopes for new public controls, and provider-shaped top-level workflow-facing activity results.
- **FR-005**: Review gates MUST reject unnecessary untyped values where a closed model or status is expected, including unknown provider-shaped statuses that would leak into workflow-facing contracts.
- **FR-006**: Review gates MUST reject nested raw bytes and large conversational state stored directly in workflow history unless the payload is represented through an intentional compact reference or approved serialized boundary.
- **FR-007**: Compatibility escape hatches MUST be accepted only when documented as transitional, bounded to the public boundary, and justified by replay or in-flight compatibility needs.
- **FR-008**: Every gate failure MUST produce an actionable reason that identifies the violated compatibility, testing, escape-hatch, or anti-pattern rule.

### Key Entities

- **Compatibility Evidence**: Reviewer-visible proof that a Temporal contract change preserves live or replayed workflow safety through regression coverage, replay or in-flight fixtures, or explicit cutover notes.
- **Review Gate Finding**: A pass or fail result for one compatibility, testing, escape-hatch, or anti-pattern rule, including the violated rule and actionable remediation when failing.
- **Escape Hatch Justification**: A bounded record explaining why a temporary compatibility shape is retained and how it is constrained to the public boundary.
- **Temporal Anti-Pattern Case**: A representative unsafe contract shape, such as raw dictionary payloads, public raw dictionary handlers, provider-shaped workflow-facing data, nested raw bytes, or large workflow-history state.

## Success Criteria

- **SC-001**: 100% of compatibility-sensitive test fixtures without replay, in-flight, or cutover evidence fail the review gates with a missing-evidence reason.
- **SC-002**: 100% of representative anti-pattern fixtures listed in the source brief fail with a rule-specific reason.
- **SC-003**: 100% of retained escape-hatch fixtures without transitional, bounded, compatibility-focused justification fail the review gates.
- **SC-004**: At least one safe additive compatibility fixture passes while at least one unsafe non-additive fixture fails, proving the gates distinguish allowed evolution from unsafe migration.
- **SC-005**: Final verification can trace every in-scope source design requirement and Jira issue MM-331 from TOOL board to at least one functional requirement and acceptance scenario.
