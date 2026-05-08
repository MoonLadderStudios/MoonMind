# Feature Specification: Process Verified Tracker Decisions

**Feature Branch**: `313-process-tracker-decisions`
**Created**: 2026-05-07
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-599 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-599 MoonSpec Orchestration Input

## Source

- Jira issue: MM-599
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Process verified tracker decisions and promote approved proposals
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-599 from MM project
Summary: Process verified tracker decisions and promote approved proposals
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-599 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-599: Process verified tracker decisions and promote approved proposals

Source Reference
Source Document: docs/Tasks/TaskProposalSystem.md
Source Title: Task Proposal System
Source Sections:
- 8. Review, Promotion, and Execution
- 9.2 Webhook endpoints
- 9.3 Admin and recovery APIs

Coverage IDs:
- DESIGN-REQ-002
- DESIGN-REQ-021
- DESIGN-REQ-022
- DESIGN-REQ-023
- DESIGN-REQ-024
- DESIGN-REQ-025
- DESIGN-REQ-026
- DESIGN-REQ-031

As a reviewer, I need approved external tracker actions to promote proposals into new MoonMind.Run executions while dismiss, defer, reprioritize, and revision actions update proposal state without executing arbitrary tracker content.

Acceptance Criteria
- Webhook handlers verify provider signatures or shared secrets before processing decisions.
- Provider event IDs make webhook decision handling idempotent.
- Actor permission checks block unauthorized promotion, dismissal, deferral, reprioritization, and revision requests.
- Promotion ignores edited issue body text and Jira ADF, uses only the stored snapshot plus bounded controls, and creates a new MoonMind.Run through the canonical Temporal-backed create path.
- Promotion preserves explicit skill selectors, authoredPresets, and steps[].source provenance unless a validated proposal revision changed them.
- Dismissal and deferral record actor, provider event identity, note/reason, timestamp, and external issue state without starting execution.
- Disabled or unsupported runtime overrides fail validation before a workflow is created.

Requirements
- Implement verified provider decision ingestion.
- Bridge approved proposals to Temporal execution from stored snapshots.
- Record non-executing decisions and expose recovery surfaces.
"""

Input classification: single-story runtime feature request. The Jira brief selects one independently testable proposal-review behavior story from `docs/Tasks/TaskProposalSystem.md`; it does not require `moonspec-breakdown` before MoonSpec orchestration.

Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-599` under `specs/`, so `Specify` is the first incomplete stage.

## User Story - Process Verified Tracker Decisions

**Summary**: As a reviewer, I want verified external tracker decisions to either promote approved proposals into new MoonMind runs or record non-executing decisions, so MoonMind executes only trusted stored proposal snapshots and keeps review state auditable.

**Goal**: External review actions are accepted only after provider verification and actor authorization, then produce the correct proposal outcome without treating tracker-edited content as executable instructions.

**Independent Test**: Can be fully tested by submitting representative approved, dismissed, deferred, reprioritized, revision-requested, duplicate, unauthorized, and invalid-runtime tracker decisions for an existing proposal delivery and confirming the resulting proposal state, run creation behavior, audit details, and external issue updates.

**Acceptance Scenarios**:

1. **Given** an open delivered proposal with a stored proposal snapshot and an authorized reviewer, **When** a verified approval decision is received with bounded promotion controls, **Then** MoonMind creates exactly one new MoonMind.Run from the stored snapshot, applies only validated bounded controls, preserves explicit skill and preset provenance, records the promoted run identifier, and updates the external issue with promotion metadata.
2. **Given** an external tracker issue whose body or structured description was edited after delivery, **When** an authorized approval decision is processed, **Then** MoonMind ignores the edited tracker content as executable input and promotes only the stored proposal snapshot or a previously validated proposal revision.
3. **Given** an open delivered proposal, **When** a verified authorized dismissal, deferral, reprioritization, or revision-request decision is received, **Then** MoonMind records the actor, provider event identity, note or reason, timestamp, requested state, and external issue state without creating a new run.
4. **Given** a duplicate provider event for a decision that was already processed, **When** MoonMind receives the event again, **Then** the existing recorded outcome is reused and no second run or duplicate state transition is created.
5. **Given** an unverified provider event or an actor without permission for the requested decision, **When** MoonMind processes the event, **Then** the decision is rejected, the proposal remains unpromoted, no run is created, and the rejected outcome is recorded without exposing secrets.
6. **Given** an approval decision with a disabled or unsupported runtime override, **When** MoonMind validates the promotion controls, **Then** validation fails before any workflow is created and the proposal remains available for a corrected decision or recovery action.
7. **Given** an operator recovery request for a proposal delivery, **When** the request is authorized and targets a supported recovery action, **Then** MoonMind exposes enough delivery and decision state to inspect, redeliver, synchronize, or promote without bypassing decision validation.

### Edge Cases

- Provider payloads with missing, blank, malformed, or previously seen provider event identifiers must not create ambiguous or duplicate outcomes.
- Approval decisions for proposals that are already promoted, dismissed, deferred, or superseded by a validated revision must not create an additional run.
- Promotion controls that attempt to replace the full task payload or inject arbitrary tracker body content must be rejected.
- Stored proposal snapshots with invalid executable step types, invalid skill selectors, incompatible skill/runtime selections, or missing required task fields must fail validation before run creation.
- External issue update failures after an internal decision is recorded must leave a recoverable state with enough evidence for retry or synchronization.
- Provider errors and rejection records must avoid logging provider secrets, signatures, shared secrets, tokens, or raw credential material.

## Assumptions

- Existing MoonMind proposal delivery records and stored proposal snapshots are the source of truth for promoted execution content.
- Existing configured provider destinations define which external tracker actions and actors are authorized for proposal review decisions.
- Recovery APIs are operator-facing controls for inspection and repair, not the normal reviewer workflow.

## Source Design Requirements

- **DESIGN-REQ-002** (`docs/Tasks/TaskProposalSystem.md` §8): Proposal creation and proposal execution remain separate; work starts only through a verified promotion decision. Scope: in scope. Mapped requirements: FR-001, FR-006, FR-014.
- **DESIGN-REQ-021** (`docs/Tasks/TaskProposalSystem.md` §8.2): Canonical reviewer actions are promote, dismiss, defer, reprioritize, and request revision, mapped from provider-native events. Scope: in scope. Mapped requirements: FR-002, FR-007.
- **DESIGN-REQ-022** (`docs/Tasks/TaskProposalSystem.md` §9.2): Provider-originated decision handling verifies signatures or shared secrets before processing. Scope: in scope. Mapped requirements: FR-003, FR-004.
- **DESIGN-REQ-023** (`docs/Tasks/TaskProposalSystem.md` §8.3, §8.6, §9.2): Promotion loads the stored proposal snapshot, ignores edited external issue content as executable input, and preserves explicit skill and preset provenance unless a validated revision changed it. Scope: in scope. Mapped requirements: FR-008, FR-009, FR-010, FR-011.
- **DESIGN-REQ-024** (`docs/Tasks/TaskProposalSystem.md` §8.4, §9.2): Dismissal, deferral, reprioritization, and revision requests update proposal state and record decision evidence without starting execution. Scope: in scope. Mapped requirements: FR-007, FR-012.
- **DESIGN-REQ-025** (`docs/Tasks/TaskProposalSystem.md` §8.7): Runtime overrides are bounded controls on the promoted execution request, and disabled or unsupported runtime values fail validation before run creation. Scope: in scope. Mapped requirements: FR-013.
- **DESIGN-REQ-026** (`docs/Tasks/TaskProposalSystem.md` §8.8, §9.3): Successful promotion and recovery surfaces expose decision state, created run metadata, external issue state, and warnings needed for operators to inspect or repair delivery outcomes. Scope: in scope. Mapped requirements: FR-015, FR-016.
- **DESIGN-REQ-031** (`docs/Tasks/TaskProposalSystem.md` §9.2, §9.4): Provider adapters enforce destination and action policy, keep credentials inside trusted boundaries, normalize provider decisions, and redact provider errors or secrets. Scope: in scope. Mapped requirements: FR-003, FR-004, FR-005, FR-017.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind MUST keep proposal delivery and proposal execution separate until a verified promotion decision is accepted.
- **FR-002**: MoonMind MUST recognize promote, dismiss, defer, reprioritize, and request-revision as the canonical proposal decision outcomes from external review surfaces.
- **FR-003**: MoonMind MUST verify provider-originated decision authenticity before evaluating the requested decision.
- **FR-004**: MoonMind MUST verify the external actor is authorized to perform the requested proposal decision before changing proposal state.
- **FR-005**: MoonMind MUST reject unverified, unauthorized, or policy-denied provider decisions without creating a new run or exposing sensitive provider information.
- **FR-006**: MoonMind MUST promote an approved proposal by creating exactly one new MoonMind.Run from the stored proposal snapshot after successful validation.
- **FR-007**: MoonMind MUST record dismiss, defer, reprioritize, and request-revision decisions as non-executing proposal outcomes without creating a new run.
- **FR-008**: Promotion MUST ignore edited external issue body text, comments, and structured tracker descriptions as executable replacement input.
- **FR-009**: Promotion MUST use only the stored proposal snapshot plus validated bounded controls unless a validated proposal revision has become the stored source of truth.
- **FR-010**: Promotion MUST preserve explicit agent skill selectors from the stored proposal unless a validated proposal revision changed them.
- **FR-011**: Promotion MUST preserve authored preset metadata and per-step source provenance from the stored proposal when present and valid.
- **FR-012**: Non-executing decisions MUST record actor identity, provider event identity, note or reason when supplied, timestamp, requested state, and external issue state.
- **FR-013**: Disabled, unsupported, or incompatible runtime overrides MUST fail validation before any workflow or run is created.
- **FR-014**: Provider event identity MUST make decision processing idempotent so repeated events cannot create duplicate runs or duplicate terminal outcomes.
- **FR-015**: Successful promotion MUST record the created run identifier and make it available for external issue updates and operator inspection.
- **FR-016**: Operator recovery surfaces MUST expose delivery inspection and recovery actions for redelivery, synchronization, and controlled promotion without bypassing normal validation.
- **FR-017**: Provider errors, rejection records, logs, and external updates MUST redact secrets, credentials, signatures, tokens, and raw authentication material.
- **FR-018**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-599` and this canonical Jira preset brief for traceability.

### Key Entities

- **Proposal Delivery**: A reviewable proposal sent to an external tracker; includes provider destination, external issue identity, current review state, and links to stored proposal evidence.
- **Stored Proposal Snapshot**: The immutable or revisioned task content that MoonMind may promote into a new run after validation.
- **Proposal Decision Event**: A provider-originated reviewer action with provider event identity, actor, requested decision, bounded controls, and verification result.
- **Promotion Controls**: Limited reviewer-supplied values that may affect the promoted run without replacing the stored proposal payload.
- **MoonMind.Run**: The created execution record that represents promoted work from an approved proposal.
- **Recovery Action**: An operator-initiated inspection or repair action for proposal delivery state, external issue synchronization, redelivery, or controlled promotion.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of accepted external tracker decisions have a recorded provider event identity, verified actor, requested decision, timestamp, and resulting proposal state.
- **SC-002**: Replaying the same provider event for an already processed approval creates 0 additional MoonMind.Run records.
- **SC-003**: In validation scenarios where external issue content differs from the stored proposal snapshot, 100% of promoted runs use the stored snapshot content rather than edited tracker text.
- **SC-004**: 100% of unauthorized, unverified, policy-denied, or invalid-runtime decisions are rejected before run creation.
- **SC-005**: 100% of dismiss, defer, reprioritize, and request-revision decisions leave the proposal without a newly created run.
- **SC-006**: A reviewer or operator can identify the promoted run or non-executing decision outcome for a processed proposal delivery from recorded proposal state and external issue metadata.
- **SC-007**: Traceability review confirms `MM-599`, the original Jira preset brief, and all in-scope source design requirements remain preserved across MoonSpec artifacts.
