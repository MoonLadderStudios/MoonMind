# Feature Specification: Resolve Proposal Policy and Delivery Records

**Feature Branch**: `311-resolve-proposal-delivery-records`
**Created**: 2026-05-07
**Status**: Draft
**Input**: User description: """
For a single-story Jira preset brief, run moonspec-specify unless an active spec.md already passes the specify gate.
For a broad technical or declarative design, run moonspec-breakdown first, then select the recommended first generated spec unless the issue brief explicitly requires processing all specs.
Preserve Jira issue MM-597 and the original preset brief in spec.md so final verification can compare against them.

Canonical Jira preset brief:

# MM-597 MoonSpec Orchestration Input

## Source

- Jira issue: MM-597
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Resolve proposal policy and delivery records deterministically
- Labels:
  - `moonmind-workflow-mm-f4b2ca74-585d-4fde-b7c8-9c21456c69a8`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-597 from MM project
Summary: Resolve proposal policy and delivery records deterministically
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-597 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-597: Resolve proposal policy and delivery records deterministically

Source Reference
- Source document: `docs/Tasks/TaskProposalSystem.md`
- Source title: Task Proposal System
- Source sections:
  - 4. Proposal Policy
  - 5.1 Delivery model
  - 5.2 Dedup-first delivery
  - 7. Origin, Identity, and Naming
  - 13. Desired Data Model
- Coverage IDs:
  - DESIGN-REQ-010
  - DESIGN-REQ-011
  - DESIGN-REQ-012
  - DESIGN-REQ-013
  - DESIGN-REQ-020

User Story
As a MoonMind operator, I need proposal submission to resolve routing policy, target repositories, deduplication, origin metadata, and durable delivery records before provider-specific issue delivery.

Acceptance Criteria
- Policy resolution preserves explicit candidate values over defaults while enforcing allowlists, capacity limits, severity gates, and tag gates.
- Project proposals keep the triggering repository and MoonMind run-quality proposals rewrite to the configured MoonMind repository only when category/severity/tag gates pass.
- Dedup searches local open delivery records and provider metadata before creating a new delivery target.
- Existing open duplicates update or link to the existing issue path instead of creating duplicate reviewer-facing records.
- Delivery records include the canonical field set or an explicitly documented subset with provider-specific metadata separated from canonical fields.
- Origin metadata uses `origin.source = workflow`, `origin.id = workflow_id`, and snake_case keys.

Requirements
- Implement deterministic policy resolution at proposal submission time.
- Persist delivery records as the audit/idempotency source.
- Provide repository-aware deduplication before provider issue creation.

Relevant Implementation Notes
- Preserve MM-597 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Treat `docs/Tasks/TaskProposalSystem.md` as the source design reference for proposal policy, delivery records, dedup-first delivery, origin identity, and the desired data model.
- Resolve proposal policy at proposal submission time by merging global defaults with `task.proposalPolicy`, preserving explicit candidate values over defaults, enforcing capacity and gate constraints, resolving the delivery provider and destination, verifying operator allowlists, and persisting the resolved delivery decision for auditability.
- Keep project-targeted proposals on the triggering repository, while MoonMind-targeted run-quality proposals rewrite to the configured MoonMind repository only after category, severity, and tag gates pass.
- Use repository-aware deduplication based on canonical repository target and normalized proposal title before creating provider issues.
- Search local open delivery records and provider metadata for matching dedup targets before issue creation; update, link, or comment on an open duplicate instead of creating a duplicate reviewer-facing issue.
- Persist proposal delivery records as the audit and idempotency model, including canonical identity, provider, external issue, repository, dedup, status, title, summary, category, tags, priority, task snapshot, origin, delivery, sync, promotion, decision, and timestamp fields where supported by the implementation slice.
- Keep provider-specific metadata separate from canonical delivery-record fields when it does not belong in the canonical field set.
- Normalize workflow-origin metadata to `origin.source = "workflow"`, `origin.id = workflow_id`, and snake_case metadata keys such as `workflow_id`, `temporal_run_id`, `trigger_repo`, `starting_branch`, `working_branch`, `trigger_job_id`, `trigger_step_id`, and `signal`.
"""

## Classification

Input classification: single-story runtime feature request. The Jira brief selects one independently testable proposal-submission behavior story from `docs/Tasks/TaskProposalSystem.md`; it does not require `moonspec-breakdown` before MoonSpec orchestration.

Resume decision: no existing Moon Spec feature directory or checked-in spec artifact matched `MM-597` under `specs/`, so `Specify` is the first incomplete stage.

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Deterministic Proposal Delivery

**Summary**: As a MoonMind operator, I want proposal submission to resolve policy, target repositories, deduplication, origin metadata, and delivery records deterministically before provider-specific issue delivery.

**Goal**: Proposal delivery decisions are explainable, idempotent, repository-aware, and auditable before MoonMind creates or updates any external tracker issue.

**Independent Test**: Can be fully tested by submitting representative proposal candidates with explicit and default policy values, project and MoonMind targets, duplicate and non-duplicate delivery identities, workflow-origin metadata, and provider-specific metadata, then verifying the resolved delivery decision, delivery record, dedup behavior, and origin fields before external issue delivery.

**Acceptance Scenarios**:

1. **Given** a proposal candidate contains explicit routing values and operator defaults also exist, **When** proposal submission resolves policy, **Then** explicit candidate values are preserved unless an allowlist, capacity limit, severity gate, or tag gate rejects them.
2. **Given** a project-targeted proposal is submitted, **When** the delivery target is resolved, **Then** the triggering repository remains the canonical repository target and the external tracker destination is derived from allowed policy.
3. **Given** a MoonMind run-quality proposal is submitted, **When** category, severity, and tag gates pass, **Then** the repository target is rewritten to the configured MoonMind repository and the delivery decision records that routing.
4. **Given** a proposal matches a local open delivery record or provider metadata for the same destination and dedup hash, **When** submission runs, **Then** MoonMind updates, links, or comments on the existing external issue path instead of creating a duplicate reviewer-facing record.
5. **Given** no open duplicate exists, **When** submission runs, **Then** MoonMind creates or prepares exactly one durable proposal delivery record with the canonical audit and idempotency fields supported by this slice.
6. **Given** provider-specific fields are needed for GitHub or Jira delivery, **When** the delivery record is stored, **Then** provider-specific metadata remains separate from canonical delivery-record fields.
7. **Given** a proposal originates from a workflow, **When** origin metadata is normalized, **Then** the result uses `origin.source = "workflow"`, `origin.id = workflow_id`, and snake_case metadata keys.

### Edge Cases

- Explicit policy selects a delivery provider or repository that is not allowed by operator policy.
- Candidate capacity exceeds per-target project or MoonMind proposal limits.
- A run-quality proposal has insufficient severity or missing required tags for MoonMind routing.
- Local delivery records and provider metadata disagree about duplicate state.
- A duplicate external issue exists but is closed, promoted, dismissed, or otherwise no longer open.
- Origin metadata contains older or non-canonical keys.
- Provider-specific metadata contains fields that resemble canonical delivery-record fields.

## Assumptions

- External issue creation and update remain trusted provider-side effects that occur after deterministic policy, deduplication, origin, and delivery-record preparation.
- Operator allowlists, proposal defaults, MoonMind repository target, repository-to-tracker bindings, and provider configuration already exist or can be represented by current configuration surfaces.
- This story covers proposal submission policy and delivery-record behavior, not proposal candidate generation or human promotion flow.

## Source Design Requirements

- **DESIGN-REQ-001**: Source section 4 requires proposal routing to follow global defaults plus per-task overrides, while preserving skill selection from the candidate payload or inherited semantics rather than recomputing it from proposal policy. Scope: in scope. Mapped requirements: FR-001, FR-002.
- **DESIGN-REQ-002**: Source section 4.2 defines `task.proposalPolicy` controls for targets, per-target caps, MoonMind severity floor, default runtime, delivery provider, GitHub delivery fields, and Jira delivery fields. Scope: in scope. Mapped requirements: FR-001, FR-002, FR-003.
- **DESIGN-REQ-003**: Source section 4.3 requires policy resolution at proposal submission time to merge defaults with task policy, preserve explicit values, enforce capacity and gates, resolve provider and destination, verify allowlists, and persist the resolved target and delivery decision. Scope: in scope. Mapped requirements: FR-001, FR-002, FR-003, FR-004, FR-005.
- **DESIGN-REQ-004**: Source sections 4.4 and 4.5 require project proposals to keep the triggering repository and MoonMind-targeted run-quality proposals to rewrite to the configured MoonMind repository only when category, severity, and tag gates pass. Scope: in scope. Mapped requirements: FR-004, FR-005.
- **DESIGN-REQ-005**: Source section 5.1 requires each submitted proposal to create or update a delivery record that acts as the audit and idempotency record with canonical provider, issue, repository, dedup, status, summary, category, priority, task snapshot, origin, delivery, sync, promotion, and decision fields. Scope: in scope. Mapped requirements: FR-006, FR-007.
- **DESIGN-REQ-006**: Source section 5.2 requires deduplication before external issue creation, using canonical repository target and normalized title, local open delivery records, and provider metadata; matching open issues are updated instead of duplicated. Scope: in scope. Mapped requirements: FR-008, FR-009, FR-010.
- **DESIGN-REQ-007**: Source section 7 requires workflow-originated proposals to use `origin.source = "workflow"`, `origin.id = workflow_id`, and snake_case metadata keys consistently across workflow payloads, stored records, API responses, issue rendering, and docs. Scope: in scope. Mapped requirements: FR-011, FR-012.
- **DESIGN-REQ-008**: Source section 13 requires provider-specific metadata to stay separate when it does not belong in the canonical proposal delivery record field set. Scope: in scope. Mapped requirements: FR-006, FR-007, FR-013.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Proposal submission MUST resolve proposal policy deterministically at submission time before provider-specific issue delivery.
- **FR-002**: Policy resolution MUST merge global defaults with per-task proposal policy while preserving explicit candidate values over defaults.
- **FR-003**: Policy resolution MUST enforce destination allowlists, per-target capacity limits, MoonMind severity gates, and approved tag gates before allowing delivery.
- **FR-004**: Project-targeted proposals MUST retain the triggering repository as the canonical repository target.
- **FR-005**: MoonMind-targeted run-quality proposals MUST rewrite to the configured MoonMind repository only when category, severity, and tag gates pass.
- **FR-006**: Proposal submission MUST create or update a durable proposal delivery record before external issue delivery is considered complete.
- **FR-007**: Delivery records MUST include the canonical audit and idempotency field set supported by this implementation slice, including provider, external issue identity, repository, dedup identity, status, title, summary, category, tags, priority, task snapshot or reference, origin identity, delivery/sync timestamps, promotion linkage, decision fields, and created/updated timestamps.
- **FR-008**: Proposal deduplication MUST compute a dedup identity from the canonical repository target and normalized proposal title before creating a new external issue.
- **FR-009**: Proposal deduplication MUST search local open delivery records and available provider metadata for an existing open reviewer-facing issue before creating a new one.
- **FR-010**: When an open duplicate exists, proposal submission MUST update, link, or comment on the existing issue path instead of creating a duplicate reviewer-facing record.
- **FR-011**: Workflow-originated proposals MUST normalize origin identity to `origin.source = "workflow"` and `origin.id = workflow_id`.
- **FR-012**: Origin metadata MUST use snake_case keys consistently for workflow, run, repository, branch, job, step, and signal metadata.
- **FR-013**: Provider-specific metadata MUST be stored separately from canonical delivery-record fields when it does not belong in the canonical field set.
- **FR-014**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-597` and the canonical Jira preset brief for traceability.

### Key Entities

- **Resolved Proposal Policy**: The normalized policy decision that identifies the allowed proposal target, delivery provider, destination, capacity and gate outcomes, and any defaulted values.
- **Proposal Delivery Record**: The durable audit and idempotency record connecting a proposal task snapshot to a provider destination, dedup identity, external issue identity, origin, status, and decision lifecycle.
- **Dedup Identity**: The repository-aware identity derived from canonical repository target and normalized proposal title, used to find existing open proposal records or provider issues.
- **Workflow Origin Metadata**: The canonical source identity and snake_case metadata describing the workflow that generated the proposal.
- **Provider-Specific Metadata**: GitHub or Jira fields needed for delivery that are not part of the canonical delivery-record field set.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests cover at least one explicit-over-default policy case, one allowlist rejection, one capacity or gate rejection, and one successful defaulted delivery decision.
- **SC-002**: Tests cover both project-targeted repository preservation and MoonMind-targeted run-quality repository rewriting after category, severity, and tag gates pass.
- **SC-003**: Tests cover local-record deduplication, provider-metadata deduplication, and no-duplicate creation paths.
- **SC-004**: Tests verify open duplicates are updated, linked, or commented on instead of creating duplicate reviewer-facing records.
- **SC-005**: Tests or verification evidence confirm delivery records include the canonical field subset chosen for this implementation and keep provider-specific metadata separate.
- **SC-006**: Tests verify workflow-origin proposals use `origin.source = "workflow"`, `origin.id = workflow_id`, and snake_case metadata keys.
- **SC-007**: Verification evidence preserves `MM-597`, the canonical Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-008.
