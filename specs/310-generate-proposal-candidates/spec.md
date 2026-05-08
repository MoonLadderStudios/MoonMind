# Feature Specification: Generate and Validate Proposal Candidates

**Feature Branch**: `310-generate-proposal-candidates`
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
- Labels: `moonmind-workflow-mm-f4b2ca74-585d-4fde-b7c8-9c21456c69a8`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

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

Relevant Implementation Notes
- Proposal generation must run in Temporal activities, not workflow code, and must inspect durable run evidence such as artifacts, plan-step outcomes, normalized `AgentRunResult` data, finish summaries, diagnostics, resolved skill snapshot metadata, and reliable preset/source provenance when present.
- Generation activities must treat run inputs and logs as untrusted, use artifact-backed references for large context, avoid commits, pushes, issue creation, task creation, and delivery-record mutations, and redact or exclude secrets and unsafe command output.
- Candidate `taskCreateRequest` payloads must use the canonical `/api/executions` task-shaped contract, including canonical `task.tool` and `step.tool` submit shapes.
- Executable tool selectors must use `tool.type = "skill"`; proposal payloads must reject `tool.type = "agent_runtime"`.
- `task.skills` and `step.skills` selectors may be present only when they follow the canonical Agent Skill System contract; proposal payloads must preserve selectors or refs instead of embedding full skill bodies, mutable `.agents/skills` directory state, runtime materialization outputs, or adapter-local prompt bundles.
- Optional `task.authoredPresets` and `steps[].source` provenance may be preserved only when reliable binding evidence exists; unresolved preset include objects must not be stored as runtime work and absent provenance must not be fabricated.
- Proposal generation must remain separate from trusted submission and delivery side effects; submission validates candidates, resolves policy, normalizes origin metadata, computes deduplication, stores delivery records, and performs configured GitHub or Jira delivery.
- Unsafe, malformed, semantically ambiguous, secret-bearing, or contract-invalid candidates must be rejected with visible redacted errors before delivery is attempted.

## Source Design Coverage

- DESIGN-REQ-007: Proposal generation from durable run evidence is side-effect-free and treats run inputs/logs as untrusted. Source: `docs/Tasks/TaskProposalSystem.md` section 3.4.
- DESIGN-REQ-008: Proposal submission and delivery are separate trusted side-effecting activities that validate candidates before storage or external delivery. Source: `docs/Tasks/TaskProposalSystem.md` section 3.5.
- DESIGN-REQ-017: Stored proposals use the canonical `/api/executions` task-shaped payload contract. Source: `docs/Tasks/TaskProposalSystem.md` section 6.
- DESIGN-REQ-018: Executable tools use `tool.type = "skill"`; `tool.type = "agent_runtime"` is not accepted in proposal payloads. Source: `docs/Tasks/TaskProposalSystem.md` section 6.
- DESIGN-REQ-019: Skill selectors and authored preset provenance are preserved only as compact reliable refs/metadata, not embedded skill bodies or fabricated provenance. Source: `docs/Tasks/TaskProposalSystem.md` sections 3.4 and 6.
- DESIGN-REQ-032: LLM-capable proposal generation and trusted control-plane submission/delivery run across separate worker/activity boundaries. Source: `docs/Tasks/TaskProposalSystem.md` section 12.

## Classification

Input classification: single-story feature request. The Jira brief selects one independently testable runtime behavior story from `docs/Tasks/TaskProposalSystem.md`; it does not require `moonspec-breakdown` before MoonSpec orchestration.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Evidence-Based Proposal Candidates

**Summary**: As a MoonMind operator, I want proposal candidate generation to use durable run evidence and validate candidates before delivery so follow-up work can be proposed without unintended side effects or invalid task payloads.

**Goal**: The proposals stage produces only reviewable, execution-ready follow-up candidates from durable run context, preserves compact intent metadata when reliable, and rejects malformed or unsafe candidates before any delivery action is attempted.

**Independent Test**: Can be fully tested by invoking the proposal generation and submission activities with representative workflow evidence, canonical and invalid task payloads, skill selectors, preset provenance, and a mock proposal service, then asserting generated candidates are side-effect-free and only valid candidates reach submission.

**Acceptance Scenarios**:

1. **Given** a completed run exposes a distinct next-step idea and durable task context, **When** proposal generation runs, **Then** it returns a candidate task payload derived from that evidence and does not create tasks, issues, commits, pushes, or proposal records.
2. **Given** a generated candidate contains a canonical executable tool selector with `tool.type = "skill"`, **When** proposal submission validates it, **Then** the candidate is accepted and can be handed to the proposal service.
3. **Given** a generated candidate contains `tool.type = "agent_runtime"` or another invalid task contract shape, **When** proposal submission validates it, **Then** delivery is skipped and a redacted visible error is returned.
4. **Given** the parent task contains explicit skill selectors or reliable authored preset and step source provenance, **When** a follow-up candidate is generated, **Then** the candidate preserves that intent as compact selectors or provenance metadata without embedding skill bodies or runtime materialization state.
5. **Given** the parent task lacks reliable preset or step source provenance, **When** a follow-up candidate is generated, **Then** no authored preset binding, include path, binding ID, or preset-derived label is fabricated.
6. **Given** proposal generation and proposal submission run through Temporal activities, **When** the worker bindings are inspected, **Then** generation remains on the LLM-capable proposal activity path and submission remains the trusted side-effect boundary.

### Edge Cases

- Candidate generation receives untrusted structured proposal text instead of a scalar next-step idea.
- Candidate task payload omits repository, contains unsafe embedded secret-like text, or fails canonical task validation.
- A default proposal runtime is configured but the candidate already provides a runtime selector.
- Parent task provenance exists only in runtime-local materialization fields rather than canonical task metadata.
- Proposal service is unavailable during submission.

## Assumptions

- "Durable run evidence" for this story means data already passed into `proposal.generate`, including workflow identifiers, original task payload, result/finish-summary fields, diagnostics, skill selector metadata, and preset provenance; fetching additional artifact bodies is out of scope unless already wired through the activity payload.
- Existing proposal service persistence and external tracker delivery remain the trusted side-effecting boundary; this story strengthens generation and pre-delivery validation without adding a new provider adapter.
- Existing Temporal activity topology already separates `proposal.generate` and `proposal.submit`; this story verifies and preserves that boundary.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Proposal generation MUST produce candidates only from durable run evidence provided to the proposal generation activity.
- **FR-002**: Proposal generation MUST NOT commit, push, create issues, create tasks, mutate proposal delivery records, or perform any other delivery side effect.
- **FR-003**: Generated candidates MUST include `taskCreateRequest` payloads that validate against the canonical `/api/executions` task contract before delivery.
- **FR-004**: Proposal validation MUST accept executable tool selectors using `tool.type = "skill"`.
- **FR-005**: Proposal validation MUST reject `tool.type = "agent_runtime"` and other unsupported executable tool shapes before delivery.
- **FR-006**: Proposal generation MUST preserve explicit task or step skill selectors as compact selectors or refs when those selectors materially affect the follow-up task.
- **FR-007**: Proposal generation MUST preserve reliable `authoredPresets` and `steps[].source` provenance when present in the canonical task payload.
- **FR-008**: Proposal generation MUST NOT fabricate authored preset bindings, binding IDs, include paths, step source metadata, or preset-derived labels when reliable provenance is absent.
- **FR-009**: Proposal submission MUST return visible redacted errors for unsafe, malformed, semantically ambiguous, or contract-invalid candidates and skip delivery for those candidates.
- **FR-010**: Proposal generation and submission MUST remain separate activities so LLM-capable generation is distinct from trusted storage or external delivery side effects.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-596` and the canonical Jira preset brief.

### Key Entities

- **Proposal Candidate**: A generated follow-up task proposal containing title, summary, category, tags, signal metadata, and a canonical `taskCreateRequest`.
- **Task Create Request**: The execution-ready task envelope accepted by Temporal submission through `/api/executions`.
- **Skill Selector Metadata**: Compact task or step skill references and selector sets included in the canonical task payload.
- **Preset Provenance Metadata**: Reliable authored preset bindings and step source metadata from the parent task payload.
- **Proposal Submission Result**: Redacted summary of generated, submitted, and rejected candidates.

## Source Design Requirements

- **DESIGN-REQ-001**: Source section 3.4 requires proposal generation to run in Temporal activities and analyze durable run evidence such as artifacts, plan-step outcomes, normalized agent results, finish summaries, diagnostics, skill metadata, and reliable preset provenance. Scope: in scope. Maps to FR-001, FR-010.
- **DESIGN-REQ-002**: Source section 3.4 requires generators to treat run inputs and logs as untrusted, use artifact-backed references for large context, avoid side effects, redact or exclude secrets, and preserve only material skill selectors and reliable provenance. Scope: in scope. Maps to FR-002, FR-006, FR-007, FR-008, FR-009.
- **DESIGN-REQ-003**: Source section 3.5 requires proposal submission to validate candidate entries, skill selectors, policy, routing, origin metadata, deduplication, delivery records, and configured external issue delivery separately from generation. Scope: in scope. Maps to FR-003, FR-009, FR-010.
- **DESIGN-REQ-004**: Source section 6 requires stored proposals to use the same task-shaped contract accepted by Temporal submission through `/api/executions`. Scope: in scope. Maps to FR-003.
- **DESIGN-REQ-005**: Source section 6 requires executable tool selectors to use `tool.type = "skill"` and forbids proposal payloads from using `tool.type = "agent_runtime"`. Scope: in scope. Maps to FR-004, FR-005.
- **DESIGN-REQ-006**: Source section 6 requires proposal payloads to preserve execution intent without embedding full skill bodies, mutable `.agents/skills` state, runtime materialization outputs, or unresolved preset include objects. Scope: in scope. Maps to FR-006, FR-007, FR-008.
- **DESIGN-REQ-007**: Source section 12 requires proposal generation, proposal submission/storage/external delivery, and webhook promotion to remain on distinct worker/activity boundaries. Scope: in scope for generation and submission boundaries; webhook promotion is out of scope because MM-596 targets candidate generation and pre-delivery validation. Maps to FR-010.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Focused unit tests cover valid skill-tool candidates, rejected `agent_runtime` candidates, provenance preservation, and absent-provenance non-fabrication.
- **SC-002**: Focused integration or boundary tests verify proposal generation produces no proposal service calls while submission performs service calls only after candidate validation.
- **SC-003**: Invalid candidate submissions return at least one redacted error and do not increment submitted count.
- **SC-004**: Verification evidence preserves `MM-596`, DESIGN-REQ-001 through DESIGN-REQ-007, and the canonical Jira preset brief.
