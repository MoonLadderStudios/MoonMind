# Feature Specification: Normalize Proposal Intent in Temporal Submissions

**Feature Branch**: `309-normalize-proposal-intent`
**Created**: 2026-05-06
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-595 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-595 MoonSpec Orchestration Input

## Source

- Jira issue: MM-595
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Normalize proposal intent in Temporal submissions
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-595 from MM project
Summary: Normalize proposal intent in Temporal submissions
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/Tasks/TaskProposalSystem.md
Source Title: Task Proposal System
Source Sections:
- 3.1 Submit-time contract
- 3.1.1 Canonical submission-path normalization
- 3.2 Run states
Coverage IDs:
- DESIGN-REQ-003
- DESIGN-REQ-004
- DESIGN-REQ-005
- DESIGN-REQ-006

As a MoonMind operator, I need every task creation surface to persist proposal intent in the canonical nested task payload so proposal behavior is deterministic across API submissions, schedules, promotions, and Codex managed sessions.

Acceptance Criteria
- New submission paths write proposal opt-in and policy only to the canonical nested task payload.
- Codex managed-session originated task creation does not rely on root-level flags, turn metadata, container environment, or adapter-local state for durable proposal intent.
- The workflow enters proposals only when global settings and initialParameters.task.proposeTasks are both enabled.
- Replay/in-flight compatibility reads are isolated and covered by a boundary test.
- The proposals state vocabulary is reflected consistently in workflow payloads, API responses, UI mapping, finish summaries, and documentation references touched by the change.

Requirements
- Persist canonical nested proposal fields for all new task submission surfaces.
- Keep compatibility reads from becoming new write contracts.
- Provide workflow-boundary coverage for proposals state gating.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-595 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

## Classification

Input classification: single-story runtime feature request. The Jira brief selects one independently testable task-submission contract story from `docs/Tasks/TaskProposalSystem.md`; it does not require `moonspec-breakdown`.

Resume decision: no existing Moon Spec feature directory or checked-in spec artifact matched `MM-595` under `specs/`, so `Specify` is the first incomplete stage.

## User Story - Canonical Proposal Intent

**Summary**: As a MoonMind operator, I need every task creation surface to persist proposal intent in the canonical nested task payload so proposal behavior is deterministic across API submissions, schedules, promotions, and Codex managed sessions.

**Goal**: Proposal-capable task submissions preserve opt-in and routing policy in one durable task-shaped contract, enter the proposal stage only under the documented global and task-level gates, and expose proposal lifecycle state consistently across operator-facing surfaces.

**Independent Test**: Submit representative task creation requests through each supported creation surface and verify the stored run input, proposal-stage gate decision, compatibility handling, and reported lifecycle state without relying on root-level flags, runtime-local metadata, or environment-derived proposal intent.

**Acceptance Scenarios**:

1. **Given** a new API task submission enables proposal generation, **When** the run input is persisted, **Then** proposal opt-in and policy are stored only under the canonical nested task payload.
2. **Given** a scheduled task or proposal promotion carries proposal policy, **When** it creates a new run, **Then** the resulting durable task payload uses the same canonical nested proposal fields as ordinary API submission.
3. **Given** a Codex managed session creates a MoonMind task with proposal intent, **When** the task is submitted, **Then** proposal behavior is determined from the canonical nested task payload rather than root-level flags, turn metadata, container environment, or adapter-local state.
4. **Given** global proposal generation is disabled, **When** a submitted task enables proposal generation at the task level, **Then** the workflow does not enter the proposals stage.
5. **Given** global proposal generation is enabled and the submitted task's canonical nested opt-in is enabled, **When** the workflow reaches proposal gating, **Then** it enters the proposals stage.
6. **Given** an in-flight or replayed workflow contains an older proposal-intent shape, **When** the workflow evaluates proposal behavior, **Then** compatibility reads remain isolated and do not become write contracts for new submissions.
7. **Given** a proposal-capable run changes state, **When** workflow payloads, API responses, UI mappings, finish summaries, or touched documentation references expose that state, **Then** they use the same proposals state vocabulary.

### Edge Cases

- A request contains both canonical nested proposal fields and stale root-level proposal flags.
- Proposal policy is present without explicit task-level proposal opt-in.
- A Codex managed session includes proposal metadata in a runtime-local location but omits the canonical nested task field.
- A replayed run contains only older proposal-intent fields.
- Proposal stage reporting reaches one surface before another and could drift in status vocabulary.

## Assumptions

- The global proposal-generation switch remains a separate operator setting and must be enabled in addition to task-level opt-in.
- Compatibility reads for already-running or replayed workflows are allowed only where they preserve existing executions without creating new submission write paths.

## Source Design Requirements

- **DESIGN-REQ-003**: `docs/Tasks/TaskProposalSystem.md` section 3.1 requires proposal opt-in and proposal policy values to be preserved as part of the durable run contract in `initialParameters`. Scope: in scope. Mapped requirements: FR-001, FR-002.
- **DESIGN-REQ-004**: `docs/Tasks/TaskProposalSystem.md` section 3.1.1 requires all new task submission paths to normalize proposal intent into the same canonical nested task payload before a run starts. Scope: in scope. Mapped requirements: FR-001, FR-003.
- **DESIGN-REQ-005**: `docs/Tasks/TaskProposalSystem.md` section 3.1.1 forbids non-canonical locations from acting as the durable write contract for new work and limits older-shape reads to replay and in-flight compatibility. Scope: in scope. Mapped requirements: FR-004, FR-005.
- **DESIGN-REQ-006**: `docs/Tasks/TaskProposalSystem.md` section 3.2 requires proposal-capable lifecycle vocabulary, including `proposals`, to be used consistently across workflow state, API responses, Mission Control mapping, finish summaries, external updates, and touched documentation. Scope: in scope. Mapped requirements: FR-006.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: New task submission surfaces MUST persist proposal opt-in and proposal policy only in the canonical nested task payload used to start a run.
- **FR-002**: The stored run input MUST preserve the raw task proposal policy object so proposal generation, delivery, and promotion can be explained from durable run data.
- **FR-003**: API submissions, schedules, proposal promotions, Codex managed-session task creation, and future task-creation surfaces MUST normalize proposal intent into the same canonical task-shaped contract before workflow execution begins.
- **FR-004**: New submissions MUST NOT use root-level proposal flags, turn metadata, session binding metadata, container environment, or adapter-local state as durable proposal-intent write contracts.
- **FR-005**: Workflow compatibility reads for older proposal-intent shapes MUST be isolated from new submission writes and covered by workflow-boundary evidence.
- **FR-006**: Proposal-capable run state vocabulary MUST be consistent across workflow payloads, API responses, Mission Control status mapping, finish summaries, and any documentation references touched by the change.
- **FR-007**: The workflow MUST enter the proposals stage only when global proposal generation is enabled and the submitted task's canonical nested proposal opt-in is enabled.
- **FR-008**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-595` and this canonical Jira preset brief for traceability.

### Key Entities

- **Canonical Task Proposal Intent**: The durable task-shaped proposal opt-in and policy values used by the run to decide proposal behavior.
- **Proposal Policy**: The task-provided routing and generation policy preserved in the run input and resolved later for delivery decisions.
- **Proposal-Capable Run State**: The lifecycle state representation exposed to operators while proposal-capable workflows execute and finish.
- **Compatibility Proposal Intent Read**: A read-only interpretation of older payload shapes used only to preserve replay or in-flight workflow behavior.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: New API, schedule, promotion, and Codex managed-session submissions with proposal intent all persist the same canonical nested proposal fields in stored run input.
- **SC-002**: A workflow-boundary test proves proposal stage entry requires both global enablement and canonical nested task opt-in.
- **SC-003**: A compatibility test proves older proposal-intent shapes can be read for replay or in-flight behavior without becoming new write output.
- **SC-004**: No new submission path writes durable proposal opt-in or policy to root-level flags, turn metadata, runtime environment, or adapter-local state.
- **SC-005**: Workflow payload, API response, UI status mapping, finish summary, and touched documentation references use the same proposals state vocabulary.
- **SC-006**: Verification evidence preserves `MM-595`, the canonical Jira preset brief, and DESIGN-REQ-003 through DESIGN-REQ-006.
