# Feature Specification: Proposal Candidate Validation

**Feature Branch**: `310-proposal-candidate-validation`
**Created**: 2026-05-07
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-596 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-596 MoonSpec Orchestration Input

## Source

- Jira issue: MM-596
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Generate and validate proposal candidates from run evidence
- Trusted fetch tool: `jira.get_issue`
- Canonical source: synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-596 from MM project
Summary: Generate and validate proposal candidates from run evidence
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-596 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-596: Generate and validate proposal candidates from run evidence

Source Reference
Source Document: docs/Tasks/TaskProposalSystem.md
Source Title: Task Proposal System
Source Sections:
- 3.4 Proposal generation
- 3.5 Proposal submission and delivery
- 6. Canonical Proposal Payload Contract
- 12. Worker-Boundary Architecture
Coverage IDs:
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-017
- DESIGN-REQ-018
- DESIGN-REQ-019
- DESIGN-REQ-032

User Story
As a MoonMind operator, I need the proposals stage to generate candidate follow-up tasks from run evidence without side effects, then validate candidates before any delivery action is attempted.

Acceptance Criteria
- Generation activities do not commit, push, create issues, create tasks, or mutate proposal delivery records.
- Generated taskCreateRequest payloads validate against the canonical /api/executions task contract.
- tool.type=skill is accepted for executable tools and tool.type=agent_runtime is rejected.
- Explicit material skill selectors are preserved by reference or selector, not embedded as skill bodies or runtime materialization state.
- Reliable authoredPresets and steps[].source provenance are preserved when present; absent provenance is not fabricated.
- Activity/task-queue boundaries keep LLM-capable generation separate from trusted submission and delivery side effects.

Requirements
- Implement side-effect-free candidate generation from durable run evidence.
- Validate generated candidates at the submission boundary before delivery.
- Reject unsafe, malformed, or semantically ambiguous candidates with visible redacted errors.

Implementation Notes
- Treat generation and validation as separate from trusted submission and delivery side effects.
- Preserve explicit material skill selectors by reference or selector only; do not embed skill bodies or runtime materialization state in generated payloads.
- Preserve reliable authoredPresets and steps[].source provenance when present, and do not fabricate provenance when absent.
- Keep proposal generation behind LLM-capable activity/task-queue boundaries and delivery behind trusted side-effecting activity boundaries.
- Validate generated taskCreateRequest payloads against the canonical /api/executions task contract before any delivery action.

Needs Clarification
- None from the trusted Jira response beyond the brief above.
"""

## Classification

- Input type: Single-story runtime feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable proposal-generation story.
- Selected mode: Runtime implementation workflow.
- Source design: `docs/Tasks/TaskProposalSystem.md` is treated as runtime source requirements.
- Source design path input: `.`.
- Resume decision: No existing checked-in Moon Spec artifacts for `MM-596` were found under `specs/`; specification is the first incomplete stage.
- Multi-spec ordering: Not applicable for `MM-596` because the trusted Jira preset brief defines one independently testable story.

## User Story - Generate Validated Proposal Candidates

**Summary**: As a MoonMind operator, I need the proposals stage to generate candidate follow-up tasks from run evidence without side effects, then validate candidates before any delivery action is attempted.

**Goal**: Proposal generation produces reviewable follow-up task candidates from durable run evidence while preserving proposal intent, skill selectors, and provenance safely enough for the trusted submission boundary to validate or reject each candidate before any external delivery side effect occurs.

**Independent Test**: Run a proposal-generation scenario from durable run evidence with proposals enabled, verify generation emits candidate task payloads without mutating repositories, task queues, proposal delivery records, or external trackers, then submit those candidates through the validation boundary and confirm valid `tool.type=skill` payloads pass while `tool.type=agent_runtime`, malformed skill selectors, fabricated provenance, and semantically ambiguous payloads are rejected with visible redacted errors.

**Acceptance Scenarios**:

1. **Given** a proposal-capable run has durable evidence and proposal generation is enabled, **When** the proposals stage generates candidates, **Then** candidate generation produces proposed follow-up tasks without commits, pushes, issue creation, task creation, proposal delivery-record mutation, or other delivery side effects.
2. **Given** generated candidates contain `taskCreateRequest` payloads, **When** they reach the submission boundary, **Then** each payload is validated against the canonical task creation contract before any storage or tracker delivery is attempted.
3. **Given** a generated candidate uses an executable tool selector, **When** the submission boundary validates it, **Then** `tool.type=skill` is accepted and `tool.type=agent_runtime` is rejected with a visible redacted validation error.
4. **Given** generated candidates include explicit material skill selectors, **When** they are stored or delivered for review, **Then** selectors are preserved by reference or selector and no skill bodies or runtime materialization state are embedded in the candidate payload.
5. **Given** parent run evidence contains reliable authored preset or step source provenance, **When** proposal candidates are generated, **Then** reliable provenance is preserved; **and Given** the evidence lacks reliable provenance, **When** candidates are generated, **Then** authored preset and step source provenance are not fabricated.
6. **Given** generated candidates are ready for delivery, **When** trusted proposal submission runs, **Then** LLM-capable generation remains separated from trusted submission and delivery side effects by an explicit activity/task-queue boundary.
7. **Given** MoonSpec artifacts and delivery metadata are produced for this story, **When** traceability is reviewed, **Then** Jira issue key `MM-596` and the original Jira preset brief remain visible in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Edge Cases

- Proposal generation receives incomplete or partially unavailable run artifacts.
- Generated candidate payloads contain unsupported tool types, malformed skill-selector shapes, or missing required task fields.
- Generated candidates contain large logs, artifact bodies, or diagnostics that must remain behind references.
- Parent run evidence contains no reliable authored preset or step source provenance.
- Candidate validation fails after generation succeeds but before any delivery record or external tracker issue is created.
- Proposal generation is requested while global proposal generation is disabled.

## Assumptions

- Existing global and task-level proposal enablement rules from the Task Proposal System remain the authority for whether the proposals stage runs.
- Human review and external tracker delivery remain out of scope for this story except where needed to prove candidate validation occurs before delivery side effects.
- The existing canonical `/api/executions` task contract is the validation target for generated `taskCreateRequest` payloads.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Maps To |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-007 | `docs/Tasks/TaskProposalSystem.md` §3.4 Proposal generation | Proposal generation must inspect durable run evidence, treat inputs/logs as untrusted, use references for large context, redact unsafe output, and avoid side effects. | In scope | FR-001, FR-002, FR-007 |
| DESIGN-REQ-008 | `docs/Tasks/TaskProposalSystem.md` §3.5 Proposal submission and delivery | Proposal submission must validate candidates, resolve policy, normalize origin metadata, preserve explicit skill intent, reject malformed skill fields, compute deduplication, and perform delivery side effects only after validation. | In scope | FR-002, FR-003, FR-004, FR-008 |
| DESIGN-REQ-017 | `docs/Tasks/TaskProposalSystem.md` §6 Canonical Proposal Payload Contract | Stored proposal candidates must use the task-shaped contract accepted by Temporal submission through `/api/executions`. | In scope | FR-002, FR-003 |
| DESIGN-REQ-018 | `docs/Tasks/TaskProposalSystem.md` §6 Canonical Proposal Payload Contract | Executable tool selectors in proposal payloads must use `tool.type=skill` and must reject `tool.type=agent_runtime`. | In scope | FR-003 |
| DESIGN-REQ-019 | `docs/Tasks/TaskProposalSystem.md` §6 Canonical Proposal Payload Contract and Agent Skill System reference | Explicit `task.skills` and `step.skills` selectors must preserve skill intent without embedding skill bodies or runtime materialization state. | In scope | FR-004 |
| DESIGN-REQ-032 | `docs/Tasks/TaskProposalSystem.md` §12 Worker-Boundary Architecture | LLM-capable proposal generation must remain separated from trusted submission, storage, webhook, promotion, and external delivery side effects by activity/task-queue boundaries. | In scope | FR-001, FR-008 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST generate proposal candidates only from durable run evidence and normalized run outcomes, treating all run inputs, logs, and diagnostics as untrusted input.
- **FR-002**: The system MUST validate every generated candidate's `taskCreateRequest` payload against the canonical task creation contract before storage, delivery-record mutation, external issue creation, or task execution can occur.
- **FR-003**: The system MUST accept executable tool selectors with `tool.type=skill` and reject candidate payloads using `tool.type=agent_runtime` with a visible redacted validation error.
- **FR-004**: The system MUST preserve explicit material skill selectors by reference or selector in generated candidate payloads and MUST NOT embed skill bodies or runtime materialization state.
- **FR-005**: The system MUST preserve reliable `authoredPresets` and `steps[].source` provenance when the parent run provides trustworthy provenance evidence.
- **FR-006**: The system MUST NOT fabricate `authoredPresets`, binding IDs, include paths, preset-derived labels, or `steps[].source` provenance when reliable evidence is absent.
- **FR-007**: Proposal candidate generation MUST NOT perform commits, pushes, issue creation, task creation, proposal delivery-record mutation, or other delivery side effects.
- **FR-008**: Proposal candidate generation MUST run behind an LLM-capable generation boundary, while submission validation and delivery side effects MUST remain behind trusted control-plane or integration-capable boundaries.
- **FR-009**: The system MUST reject unsafe, malformed, or semantically ambiguous proposal candidates with operator-visible redacted errors and without applying delivery side effects for rejected candidates.
- **FR-010**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this story MUST preserve Jira issue key `MM-596` and this canonical Jira preset brief for traceability.

### Key Entities

- **Proposal Candidate**: A generated follow-up task suggestion derived from durable run evidence; includes title, summary, category, signal, tags, and a canonical `taskCreateRequest` payload or reference.
- **Task Create Request**: The executable task-shaped payload that must validate against the same contract accepted by task submission before storage or delivery side effects.
- **Skill Selector**: A material task-level or step-level skill reference that preserves execution intent without embedding active skill bodies or runtime materialization state.
- **Provenance Metadata**: Authored preset and step source evidence that is preserved only when reliable parent-run evidence exists.
- **Validation Error**: A redacted operator-visible reason explaining why a candidate was rejected before delivery side effects.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In validation coverage, 100% of proposal generation scenarios perform zero repository, task-creation, proposal-delivery, or external-tracker side effects.
- **SC-002**: 100% of generated candidates in test scenarios pass through canonical task payload validation before any delivery side effect is allowed.
- **SC-003**: Validation coverage demonstrates at least one accepted `tool.type=skill` candidate and at least one rejected `tool.type=agent_runtime` candidate.
- **SC-004**: Validation coverage demonstrates that explicit skill selectors are preserved without embedding skill bodies or runtime materialization state.
- **SC-005**: Validation coverage demonstrates both preservation of reliable authored preset or step provenance and non-fabrication when provenance evidence is absent.
- **SC-006**: Boundary verification demonstrates generation and trusted submission/delivery execute through distinct capability boundaries.
- **SC-007**: Traceability review confirms `MM-596`, the canonical Jira preset brief, and DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, and DESIGN-REQ-032 remain visible in MoonSpec artifacts and final implementation evidence.
