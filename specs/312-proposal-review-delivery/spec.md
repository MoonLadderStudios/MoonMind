# Feature Specification: Proposal Review Delivery

**Feature Branch**: `312-proposal-review-delivery`
**Created**: 2026-05-07
**Status**: Draft
**Input**: Trusted Jira preset brief for MM-598 from `/work/agent_jobs/mm:615f63f1-adb1-406d-acf8-a3fce1b3a8e1/artifacts/moonspec-orchestration-input-MM-598.md`. Preserve `MM-598` and the original preset brief for final verification.

Preserved source Jira preset brief: `MM-598` from the trusted Jira preset brief handoff, reproduced verbatim in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response plus normalized trusted Jira issue detail `/api/jira/issues/MM-598?projectKey=MM`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-598` under `specs/` or `.specify`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-598 MoonSpec Orchestration Input

## Source

- Jira issue: MM-598
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Deliver proposals to GitHub and Jira review surfaces
- Trusted fetch tool: `jira.get_issue`
- Normalized detail source: `/api/jira/issues/MM-598?projectKey=MM`
- Canonical source: `recommendedImports.presetInstructions` from the normalized trusted Jira issue detail response, with normalized acceptance criteria preserved below for MoonSpec coverage.

## Canonical MoonSpec Feature Request

Jira issue: MM-598 from MM project
Summary: Deliver proposals to GitHub and Jira review surfaces
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-598 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-598: Deliver proposals to GitHub and Jira review surfaces

Source Reference
Source Document: docs/Tasks/TaskProposalSystem.md
Source Title: Task Proposal System
Source Sections:
- 5. External Proposal Delivery
- 9.4 Provider adapters
- 11. Security and Integrity
Coverage IDs:
- DESIGN-REQ-001
- DESIGN-REQ-014
- DESIGN-REQ-015
- DESIGN-REQ-016
- DESIGN-REQ-027
- DESIGN-REQ-031

As a reviewer, I need MoonMind proposals delivered as GitHub Issues or Jira issues with clear review context, canonical commands or workflow states, dedup markers, and safe links back to run evidence.

Acceptance Criteria
- GitHub issues are created or updated with [MoonMind proposal] titles, canonical labels, hidden markers, source links, dedup metadata, and reviewer action instructions.
- Jira issues are created or updated with [MoonMind proposal] summaries, ADF descriptions, labels/custom fields, canonical workflow states, source links, and configured action triggers.
- Issue bodies/descriptions include a clear stored-snapshot notice and never embed raw executable payload replacement instructions.
- Provider adapters enforce repository, organization, Jira site, Jira project, and action allowlists.
- Provider credentials and raw provider errors are never exposed to managed agent environments, logs, comments, or API responses.

Requirements
- Implement provider-specific delivery through adapter boundaries.
- Render review artifacts from stored proposal snapshots and references.
- Enforce delivery idempotency and security controls at the provider boundary.

Implementation Notes
- Preserve MM-598 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tasks/TaskProposalSystem.md` as the source design reference for external proposal delivery, provider adapter behavior, security, and integrity.
- Scope implementation to delivering MoonMind proposals to GitHub Issues and Jira issues through provider-specific adapter boundaries.
- Include clear review context, canonical commands or workflow states, dedup markers or metadata, safe links back to run evidence, and configured action triggers.
- Render issue bodies/descriptions from stored proposal snapshots and references; do not embed raw executable payload replacement instructions.
- Enforce repository, organization, Jira site, Jira project, and action allowlists at provider boundaries.
- Keep provider credentials and raw provider errors out of managed agent environments, logs, comments, and API responses.
```

## User Story - Deliver Proposals To External Review Surfaces

**Summary**: As a reviewer, I want MoonMind proposals delivered to GitHub Issues or Jira issues with clear review context, safe action controls, and evidence links so I can triage, approve, defer, or dismiss follow-up work in the tracker I already use.

**Goal**: Reviewers can use configured GitHub or Jira review surfaces as the authoritative human triage location while MoonMind preserves the stored proposal snapshot as the executable source of truth.

**Independent Test**: Can be validated end-to-end by submitting proposal candidates for one GitHub destination and one Jira destination, then confirming each destination receives a deduplicated external issue with review context, action instructions or workflow states, safe evidence links, and no executable payload replacement in the issue body.

**Acceptance Scenarios**:

1. **Given** a proposal is ready for a GitHub-configured repository, **When** MoonMind delivers the proposal, **Then** a GitHub Issue is created or updated with a `[MoonMind proposal]` title, canonical proposal labels, dedup metadata, source evidence links, reviewer action instructions, and a notice that execution uses the stored MoonMind snapshot.
2. **Given** a proposal is ready for a Jira-configured project, **When** MoonMind delivers the proposal, **Then** a Jira issue is created or updated with a `[MoonMind proposal]` summary, rich review description, configured metadata, canonical workflow state or action triggers, source evidence links, and a stored-snapshot notice.
3. **Given** a matching open proposal already exists for the same provider destination and dedup target, **When** MoonMind processes the repeated proposal, **Then** the existing external issue is updated or linked instead of creating a duplicate reviewer-facing issue.
4. **Given** a reviewer reads the external issue, **When** they inspect available actions, **Then** they can identify the supported promote, dismiss, defer, or reprioritize controls without needing a MoonMind-hosted proposal queue.
5. **Given** tracker content, comments, labels, fields, or descriptions are edited outside MoonMind, **When** promotion or decision handling evaluates the issue, **Then** MoonMind treats tracker text as untrusted and uses only the stored proposal snapshot plus bounded, validated reviewer controls.
6. **Given** a provider destination, organization, repository, site, project, or action is not allowed by policy, **When** delivery or decision handling is attempted, **Then** MoonMind blocks the action with a sanitized operator-visible reason and does not expose provider credentials or raw provider errors.

### Edge Cases

- A matching issue exists in the provider but local delivery state is missing or stale.
- Provider delivery succeeds but one optional metadata field, label, or custom field cannot be applied.
- Provider delivery fails transiently after dedup lookup but before issue creation or update is confirmed.
- A reviewer edits issue text to include replacement task instructions or unsafe commands.
- Provider webhook or sync events are delivered more than once or arrive out of order.
- Evidence links or large artifacts are unavailable, expired, or too large to embed directly.

## Assumptions

- GitHub or Jira delivery is selected by the existing proposal delivery policy and tracker binding for the proposal destination.
- A validated stored proposal snapshot or snapshot reference exists before external delivery begins.
- Review action names and workflow states use the configured provider conventions when a project customizes labels, fields, or workflow transitions.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind MUST create or update exactly one external GitHub Issue or Jira issue for each proposal destination and dedup target.
- **FR-002**: MoonMind MUST maintain a durable delivery record that identifies the provider, external issue, destination, dedup identity, proposal status, source run, evidence references, and stored proposal snapshot or snapshot reference.
- **FR-003**: MoonMind MUST compute and apply repository-aware dedup metadata before creating a new external issue.
- **FR-004**: MoonMind MUST update or link to an existing matching open external issue instead of creating duplicate reviewer-facing issues.
- **FR-005**: GitHub proposal issues MUST include a `[MoonMind proposal]` title, canonical proposal labels or equivalent metadata, a rendered review body, dedup marker, source evidence links, and reviewer action instructions.
- **FR-006**: Jira proposal issues MUST include a `[MoonMind proposal]` summary, structured review description, configured labels or fields, canonical workflow state or action triggers, source evidence links, and related issue links when applicable.
- **FR-007**: External issue content MUST clearly state that MoonMind executes the stored proposal snapshot rather than edited issue text.
- **FR-008**: MoonMind MUST NOT accept arbitrary edited issue body text, Markdown, rich text, labels, fields, or comments as a replacement executable task payload.
- **FR-009**: MoonMind MUST support reviewer promote, dismiss, defer, and priority controls through explicit configured commands, workflow states, fields, labels, or transitions.
- **FR-010**: MoonMind MUST record reviewer decisions with actor identity, provider event identity, decision note or reason when supplied, timestamp, and resulting external issue state.
- **FR-011**: Provider adapters MUST enforce configured repository, organization, Jira site, Jira project, and action allowlists before delivery or decision handling.
- **FR-012**: Provider credentials and webhook secrets MUST remain inside trusted integration boundaries and MUST NOT appear in managed agent environments, external issue text, logs, comments, operator-facing responses, or spec-derived artifacts.
- **FR-013**: Provider errors surfaced to users or operators MUST be sanitized while still identifying the affected provider, issue, destination, and recoverable next action.
- **FR-014**: External issue rendering MUST link to large logs, diagnostics, artifacts, and run evidence by reference rather than embedding large or sensitive payloads directly.
- **FR-015**: MoonMind MUST make proposal delivery status and external issue links visible from existing task run details or finish summaries without requiring a dedicated MoonMind proposal review page.
- **FR-016**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-598` and this original Jira preset brief for final traceability.

### Key Entities

- **Proposal Delivery Record**: The audit and idempotency record for one proposal delivered to an external tracker; includes provider identity, destination, external issue reference, dedup data, status, origin, evidence refs, and snapshot ref.
- **External Proposal Issue**: The GitHub Issue or Jira issue shown to reviewers; contains human-readable review context and bounded action controls but is not the executable proposal payload.
- **Stored Proposal Snapshot**: The validated MoonMind proposal payload or artifact reference used for promotion; remains the executable source of truth.
- **Provider Decision Event**: A normalized reviewer action from a provider command, workflow transition, field, label, or webhook event; includes actor, provider event identity, decision, note, and resulting state.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/Tasks/TaskProposalSystem.md` §5.1 and §14): Each submitted proposal creates or updates a delivery record and exactly one GitHub or Jira issue per provider destination and dedup target. Scope: in scope. Maps to FR-001, FR-002, FR-015.
- **DESIGN-REQ-014** (`docs/Tasks/TaskProposalSystem.md` §5.2): Delivery is dedup-first; matching open provider issues are updated, linked, or annotated rather than duplicated. Scope: in scope. Maps to FR-003, FR-004.
- **DESIGN-REQ-015** (`docs/Tasks/TaskProposalSystem.md` §5.3): GitHub proposal issues include proposal-specific title, labels or equivalent metadata, rendered body, hidden marker, source links, dedup context, and reviewer action instructions. Scope: in scope. Maps to FR-005, FR-007, FR-009, FR-014.
- **DESIGN-REQ-016** (`docs/Tasks/TaskProposalSystem.md` §5.4): Jira proposal issues include proposal-specific summary, structured review description, configured labels or fields, workflow states or approval triggers, source links, and related issue links. Scope: in scope. Maps to FR-006, FR-007, FR-009, FR-014.
- **DESIGN-REQ-027** (`docs/Tasks/TaskProposalSystem.md` §5.5 and §11): Rendered external issue text is a review artifact; MoonMind executes the stored proposal snapshot and never treats edited issue content as replacement executable instructions. Scope: in scope. Maps to FR-007, FR-008, FR-014.
- **DESIGN-REQ-031** (`docs/Tasks/TaskProposalSystem.md` §9.4 and §11): Provider adapters enforce destination and action policy, keep credentials inside trusted integration boundaries, normalize provider decisions, and redact errors or secrets. Scope: in scope. Maps to FR-010, FR-011, FR-012, FR-013.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a repeated proposal with the same destination and dedup target, review surfaces show one open external issue rather than two duplicate issues in 100% of covered delivery tests.
- **SC-002**: GitHub and Jira delivery validation each confirms all required review context elements, action controls, dedup markers, source links, and stored-snapshot notices are present.
- **SC-003**: Promotion and decision validation rejects arbitrary edited issue text as executable payload replacement in 100% of covered safety tests.
- **SC-004**: Provider allowlist and credential-redaction tests confirm blocked destinations or actions return sanitized outcomes without exposing raw credentials or provider secrets.
- **SC-005**: Task run details or finish summaries expose proposal delivery status and external issue links for delivered proposals without adding a dedicated MoonMind proposal queue.
- **SC-006**: Traceability review confirms `MM-598`, the original Jira preset brief, and DESIGN-REQ-001, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-027, and DESIGN-REQ-031 remain preserved across MoonSpec artifacts and final evidence.
