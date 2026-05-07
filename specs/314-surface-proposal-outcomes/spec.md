# Feature Specification: Surface Proposal Outcomes

**Feature Branch**: `314-surface-proposal-outcomes`
**Created**: 2026-05-07
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-600 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-600 MoonSpec Orchestration Input

## Source

- Jira issue: MM-600
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Surface proposal outcomes in summaries and Mission Control
- Priority: Medium
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or any non-empty recommended/preset custom field; potentially related custom fields `Implementation plan`, `Backout plan`, and `Test plan` were present but empty.
- Trusted response artifact: `/work/agent_jobs/mm:0c314657-113e-4d5e-951f-7149150d8b9e/artifacts/moonspec-inputs/MM-600-trusted-jira-get-issue.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-600 from MM project
Summary: Surface proposal outcomes in summaries and Mission Control
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-600 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-600: Surface proposal outcomes in summaries and Mission Control

Source Reference
Source Document: docs/Tasks/TaskProposalSystem.md
Source Title: Task Proposal System
Source Sections:
- 3.6 Finish summary integration
- 10. Observability and UI Contract
- 14. Acceptance Criteria
Coverage IDs:
- DESIGN-REQ-009
- DESIGN-REQ-028
- DESIGN-REQ-029
- DESIGN-REQ-030

As a MoonMind operator, I need proposal generation, delivery, dedup, failure, and promotion outcomes visible in finish summaries, reports, APIs, execution detail, and Mission Control without creating a new primary proposal queue.

Acceptance Criteria
- Finish summaries and reports/run_summary.json include requested/generated/submitted/delivered counts, provider failures, redacted validation errors, issue links, and dedup updates.
- Execution detail and Mission Control show provider, external key, delivery status, last sync timestamp, dedup new-or-updated status, compact task summary, and promotion result links.
- mm_state=proposals is visible and mapped to running for dashboard compatibility.
- Malformed candidates are skipped with visible redacted errors and do not promote or silently drop semantically important fields.
- External delivery failures are retried idempotently and visible in summaries, delivery records, and operator diagnostics.
- No standalone proposal queue page becomes the normal review path.

Requirements
- Publish proposal-stage observability across run summaries, APIs, and Mission Control.
- Represent partial success and redacted failures clearly.
- Preserve external tracker review as the primary workflow.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
"""

## User Story - Proposal Outcome Visibility

**Summary**: As a MoonMind operator, I want proposal generation, delivery, deduplication, failure, and promotion outcomes visible in run summaries and Mission Control so I can understand proposal-stage results without using a separate proposal queue.

**Goal**: Operators can inspect one completed or in-progress proposal-capable run and see whether proposals were requested, generated, submitted, delivered, deduplicated, failed validation or delivery, and later promoted, with external tracker links available for human review.

**Independent Test**: Can be fully tested by running or simulating one proposal-capable task with generated, delivered, duplicate, malformed, delivery-failed, and promoted proposal outcomes, then verifying the finish summary, exported run summary, execution detail, API-visible state, and Mission Control surfaces expose the expected redacted outcome data while review remains anchored in GitHub or Jira.

**Acceptance Scenarios**:

1. **Given** a run requests proposal generation and proposals are generated and delivered, **When** an operator opens the run finish summary or exported run summary, **Then** the operator sees requested, generated, submitted, and delivered counts plus external tracker links.
2. **Given** generated proposals include duplicates of existing external tracker issues, **When** the run summary and execution detail are inspected, **Then** the operator sees which proposals updated or linked to existing issues instead of appearing as new deliveries.
3. **Given** proposal generation or delivery encounters provider failures or malformed candidates, **When** the operator reviews summaries, delivery records, execution detail, or Mission Control, **Then** redacted validation and delivery errors are visible and no malformed candidate is promoted or silently changed into a different task.
4. **Given** a run is in the proposal generation or delivery stage, **When** API consumers or Mission Control read its state, **Then** `mm_state=proposals` is visible and dashboard compatibility treats it as a running state.
5. **Given** a proposal has an external tracker record and is later approved for promotion, **When** the operator views execution detail or Mission Control, **Then** provider, external key, delivery status, last sync timestamp, dedup status, compact task summary, and promotion result links are visible.
6. **Given** an operator needs to review proposal work, **When** the normal review path is used, **Then** GitHub or Jira remains the primary review surface and no standalone MoonMind proposal queue page becomes required.

### Edge Cases

- Proposal generation is requested but produces zero candidates.
- Proposal generation is disabled globally or not requested for the run.
- A delivery provider partially succeeds, producing delivered proposals and provider-specific failures in the same run.
- A candidate is malformed, contains invalid skill-selection meaning, or omits required task meaning.
- A retry attempts to deliver the same proposal after a previous partial failure.
- A delivered proposal is attached to an existing dedup target rather than creating a new issue.
- A proposal is promoted after the source run has completed.

## Assumptions

- Existing GitHub and Jira tracker issues remain the primary human review destinations for proposal triage.
- Operator-visible errors should be redacted while still preserving enough context to diagnose the proposal-stage outcome.
- Dashboard compatibility can continue to classify `proposals` as running while still exposing the more specific underlying state.

## Source Design Requirements

- **DESIGN-REQ-009**: Source `docs/Tasks/TaskProposalSystem.md` section 3.6. Finish summaries and exported run summaries must record whether proposal generation was requested, generated candidate count, submitted and delivered counts, provider-specific delivery failures, redacted validation errors, external GitHub or Jira issue links, and dedup updates. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, and FR-005.
- **DESIGN-REQ-028**: Source `docs/Tasks/TaskProposalSystem.md` sections 10.1 and 10.2. Proposal state and delivery visibility must be exposed across API, execution detail, and Mission Control, including `mm_state=proposals`, dashboard compatibility mapping to running, proposal counts and errors, external issue links, provider, external key, delivery status, last sync timestamp, dedup new-or-updated status, compact task summary, and promotion result links. Scope: in scope. Maps to FR-006, FR-007, FR-008, and FR-009.
- **DESIGN-REQ-029**: Source `docs/Tasks/TaskProposalSystem.md` sections 10.4 and 14. Proposal generation, submission, and delivery failures must be represented as retry-safe, redacted, operator-visible partial outcomes; malformed candidates must be skipped and must not be promoted or silently altered in ways that change execution meaning. Scope: in scope. Maps to FR-003, FR-004, FR-010, and FR-011.
- **DESIGN-REQ-030**: Source `docs/Tasks/TaskProposalSystem.md` sections 10.3 and 14. GitHub Issues and Jira issues must remain the normal proposal review surfaces, while MoonMind may show delivery status and links but must not require or introduce a standalone proposal queue page as the primary review path. Scope: in scope. Maps to FR-012.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST indicate in run finish summaries whether proposal generation was requested for the run.
- **FR-002**: System MUST expose generated candidate, submitted proposal, and delivered proposal counts in both the finish summary and exported run summary.
- **FR-003**: System MUST include provider-specific delivery failures and redacted validation errors in proposal-stage summaries when they occur.
- **FR-004**: System MUST include external GitHub Issue or Jira issue links for delivered proposals in summary and detail surfaces.
- **FR-005**: System MUST identify dedup updates where a new proposal candidate was attached to or updated an existing external issue.
- **FR-006**: System MUST expose `mm_state=proposals` while proposal generation, submission, or delivery is in progress.
- **FR-007**: System MUST map proposal-stage runs to the running dashboard compatibility category while preserving the specific proposal state for operators.
- **FR-008**: System MUST show proposal delivery details in execution detail and Mission Control, including provider, external key, delivery status, last sync timestamp, and whether delivery created a new issue or updated an existing one.
- **FR-009**: System MUST show a compact task summary for delivered proposals, including runtime, repository, publish mode, priority, attempt policy, skill context, and preset provenance when those values are present in the stored proposal.
- **FR-010**: System MUST skip malformed or semantically invalid proposal candidates with redacted visible errors rather than promoting them.
- **FR-011**: System MUST make external delivery failures retry-safe and visible in summaries, delivery records, and operator diagnostics.
- **FR-012**: System MUST preserve GitHub Issues or Jira issues as the primary proposal review workflow and MUST NOT require a standalone MoonMind proposal queue page for normal review.
- **FR-013**: System MUST show promotion result links after approved proposals are promoted.
- **FR-014**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-600` and this original Jira preset brief for traceability.

### Key Entities

- **Proposal Outcome Summary**: Operator-visible summary for one run's proposal-stage request, generation, submission, delivery, dedup, validation, and failure outcomes.
- **Proposal Delivery Record**: Review and audit representation of one delivered or attempted proposal, including provider, external key, delivery status, sync timing, dedup status, errors, and links.
- **Proposal Candidate Error**: Redacted validation or provider error explaining why a candidate was skipped, failed delivery, or requires operator attention.
- **Promotion Result**: Linkage from an approved external proposal review item to the resulting promoted work outcome.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a proposal-capable run with generated and delivered proposals, 100% of requested, generated, submitted, and delivered proposal counts are visible in finish summary output and the exported run summary.
- **SC-002**: For delivered proposals, 100% of external GitHub or Jira issue links visible in execution detail are also represented in proposal-stage summary data.
- **SC-003**: For malformed candidates in validation coverage, 100% are skipped with redacted visible errors and 0 are promoted.
- **SC-004**: For provider delivery failure coverage, 100% of failures are visible in summaries, delivery records, and operator diagnostics without exposing secret values.
- **SC-005**: During proposal-stage execution, API-visible state includes `mm_state=proposals` and dashboard compatibility categorizes the run as running in 100% of covered cases.
- **SC-006**: Mission Control and execution detail show provider, external key, delivery status, last sync timestamp, dedup status, compact task summary, and promotion result links for 100% of covered delivered-or-promoted proposals where those values exist.
- **SC-007**: Verification confirms no standalone MoonMind proposal queue page is required for normal proposal review.
- **SC-008**: Traceability evidence preserves `MM-600`, the original Jira preset brief, and DESIGN-REQ-009, DESIGN-REQ-028, DESIGN-REQ-029, and DESIGN-REQ-030 in MoonSpec artifacts.
