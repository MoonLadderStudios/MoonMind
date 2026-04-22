# Feature Specification: Mission Control Page-Specific Task Workflow Composition

**Feature Branch**: `run-jira-orchestrate-for-mm-428-apply-page-composition`  
**Created**: 2026-04-21  
**Status**: Implemented  
**Input**: Trusted Jira preset brief for MM-428 from `docs/tmp/jira-orchestration-inputs/MM-428-moonspec-orchestration-input.md`. Summary: "Apply page-specific composition to task workflows." Source design: `docs/UI/MissionControlDesignSystem.md`, section 11.

## Original Jira Preset Brief

Jira issue: MM-428 from MM project
Summary: Apply page-specific composition to task workflows
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-428 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-428: Apply page-specific composition to task workflows

Source Reference
Source Document: docs/UI/MissionControlDesignSystem.md
Source Title: Mission Control Design System
Source Sections:
- 11. Page-specific composition rules
- 11.1 /tasks/list
- 11.2 /tasks/new
- 11.3 Task detail and evidence-heavy pages
Coverage IDs:
- DESIGN-REQ-014
- DESIGN-REQ-017
- DESIGN-REQ-019
- DESIGN-REQ-020
- DESIGN-REQ-021

User Story
As a Mission Control operator, I want the task list, task creation flow, and task detail/evidence pages to use the documented composition patterns so each workflow has a clear primary surface and readable supporting content.


Acceptance Criteria
- /tasks/list has a compact filter/control deck above a distinct matte table slab.
- /tasks/list uses right-side utility/telemetry placement, visible active filter chips, sticky table header, and pagination/page-size controls attached to the table system.
- /tasks/new uses matte/satin step cards and a bottom floating launch rail as the page hero surface.
- The /tasks/new primary CTA reads as the clear launch/commit action and large textareas remain matte.
- Task detail pages keep summary, facts, steps, evidence, logs, and actions structurally separate and readable.
- Evidence-heavy pages avoid glass effects that compete with dense evidence or logs.

Requirements
- Implement task list page composition.
- Implement create page launch-flow composition.
- Implement task detail/evidence composition.
- Validate route-specific one-hero-effect and matte dense-region rules.

Implementation Notes
- Preserve MM-428 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/UI/MissionControlDesignSystem.md` as the source design reference for page-specific Mission Control task workflow composition.
- Scope implementation to `/tasks/list`, `/tasks/new`, and task detail/evidence-heavy page composition unless related shared UI primitives must be adjusted to satisfy the route-specific behavior.
- Keep dense task workflow surfaces matte/readable and reserve elevated or hero treatment for the documented primary surface on each route.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-428 is blocked by MM-427, whose embedded status is Done.
- Trusted Jira link metadata also shows MM-428 blocks MM-429, which is not a blocker for MM-428 and is ignored for dependency gating.

## Classification

Single-story runtime feature request. The brief contains one independently testable UI composition outcome: the task list, task creation flow, and task detail/evidence pages must each use their documented primary composition pattern while preserving existing task workflow behavior.

## User Story - Page-Specific Task Workflow Composition

**Summary**: As a Mission Control operator, I want the task list, task creation flow, and task detail/evidence pages to use route-specific composition patterns so each workflow has a clear primary surface and readable supporting content.

**Goal**: Task workflow pages use the Mission Control page-composition rules for list scanning, task creation, and evidence review: `/tasks/list` keeps a control deck plus data slab, `/tasks/new` presents a guided launch flow with a floating launch rail, and task detail/evidence views keep summary, facts, steps, evidence, logs, and actions structurally distinct.

**Independent Test**: Render the task list, create page, and a task detail/evidence page with representative data. The story passes when each route exposes the documented composition structure, dense reading/editing regions remain matte/readable, the create page has one primary launch rail effect, and existing task filtering, submission, detail, evidence, log, and action behavior remains unchanged.

**Acceptance Scenarios**:

1. **Given** the task list page renders, **when** filters, utilities, results, and pagination are inspected, **then** it uses a compact control deck above a distinct matte data slab with active filter chips, sticky table header posture, and attached pagination/page-size controls.
2. **Given** the task creation page renders, **when** the prompt, image, skill, preset, and launch controls are inspected, **then** the primary workflow body uses matte/satin step cards and a bottom floating launch rail as the page's single hero premium surface.
3. **Given** the task creation page primary action is visible, **when** the operator reviews the launch controls, **then** the primary CTA reads as the clear launch/commit action and large textareas remain matte and comfortable for sustained editing.
4. **Given** a task detail or evidence-heavy page renders with summary, facts, steps, evidence, logs, and actions, **when** dense content is inspected, **then** those regions remain structurally separate and readable without glass effects competing with evidence density.
5. **Given** existing task workflows are exercised, **when** route-specific composition is present, **then** task-list requests, task submission payloads, task detail actions, evidence/log visibility, and route navigation remain unchanged.

### Edge Cases

- Empty task-list states must preserve the control deck and data slab without a blank or oversized card.
- Long task prompts, repository names, workflow IDs, evidence names, and logs must wrap within matte dense regions instead of expanding the viewport.
- Narrow/mobile layouts must keep task list cards, create-page launch controls, and detail actions reachable without overlapping content.
- The create page must not introduce multiple competing hero glass effects when images, skills, presets, and launch controls are all present.
- Evidence/log pages must remain readable when logs are long, streaming, missing, or in fallback artifact mode.

## Assumptions

- The trusted Jira preset brief for MM-428 is the canonical orchestration input and must be preserved in downstream artifacts and PR metadata.
- The brief points at `docs/UI/MissionControlDesignSystem.md`; section 11 is treated as runtime source requirements.
- Prior MM-426 and MM-427 work may already satisfy parts of `/tasks/list` and shared interaction language, but MM-428 still verifies all three task workflow page families together.
- Backend contracts, task submission payload semantics, Temporal orchestration, and Jira Orchestrate preset behavior are out of scope unless a regression is found while preserving existing behavior.

## Source Design Requirements

- **DESIGN-REQ-014** (`docs/UI/MissionControlDesignSystem.md` section 11.1): `/tasks/list` must use compact filters/utilities, visible active filter chips, sticky table posture, and pagination/page-size controls attached to the table system. Scope: in scope. Mapped to FR-001, FR-002, FR-009.
- **DESIGN-REQ-017** (`docs/UI/MissionControlDesignSystem.md` sections 11.2 and 11.3): Floating or elevated controls may use glass only where elevation improves clarity; dense content inside task workflow pages must remain grounded and crisp. Scope: in scope. Mapped to FR-003, FR-004, FR-006, FR-009.
- **DESIGN-REQ-019** (`docs/UI/MissionControlDesignSystem.md` section 11.1): Task-list table/data composition must remain table-first on desktop, use matte data slabs, and keep table controls visually attached. Scope: in scope. Mapped to FR-001, FR-002, FR-009.
- **DESIGN-REQ-020** (`docs/UI/MissionControlDesignSystem.md` section 11.2): `/tasks/new` must feel like a guided launch flow with matte/satin step cards and a bottom floating launch rail as the page hero surface. Scope: in scope. Mapped to FR-003, FR-004, FR-005, FR-009.
- **DESIGN-REQ-021** (`docs/UI/MissionControlDesignSystem.md` section 11.3): Task detail and evidence-heavy pages must keep summary, facts, steps, evidence, logs, and actions separated and readable. Scope: in scope. Mapped to FR-006, FR-007, FR-008, FR-009.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The task list MUST expose a compact control deck above a distinct matte data slab for dense results.
- **FR-002**: Task-list active filter chips, sticky table header posture, page-size controls, and pagination MUST remain visually attached to the table/data system.
- **FR-003**: The task creation page MUST organize prompt, image, skill, preset, and action areas as a guided launch flow using matte/satin step cards for the primary workflow body.
- **FR-004**: The task creation page MUST expose a bottom floating launch rail as the page's single primary premium/hero surface.
- **FR-005**: The task creation page primary CTA MUST read as the clear launch/commit action and large textareas MUST remain matte/readable.
- **FR-006**: Task detail and evidence-heavy pages MUST keep summary, facts, steps, evidence, logs, and actions in structurally distinct readable regions.
- **FR-007**: Evidence and log regions MUST avoid glass effects that compete with dense evidence or log readability.
- **FR-008**: Route-specific task workflow composition MUST remain responsive for narrow/mobile viewports without overlap or unreachable controls.
- **FR-009**: Existing task-list request behavior, task submission payloads, task detail actions, evidence/log visibility, routing, and navigation MUST remain unchanged.
- **FR-010**: Automated verification MUST cover the task list, task creation, and task detail/evidence page composition structures and unchanged existing behavior.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve MM-428 and the trusted Jira preset brief.

### Key Entities

- **Task List Control Deck**: The compact filter and utility region above task-list results.
- **Task List Data Slab**: The matte dense result region that contains result summary, table/mobile cards, page-size controls, and pagination.
- **Create Page Step Card**: A matte or satin surface for one guided task-launch input area.
- **Floating Launch Rail**: The bottom task creation action surface containing launch/commit controls.
- **Evidence Slab**: A readable dense region for task evidence, artifacts, logs, or execution details.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: UI tests confirm task-list control deck and data slab composition remains present with active filters, sticky header posture, page-size controls, and pagination.
- **SC-002**: UI tests confirm the create page renders matte/satin workflow step cards and one bottom floating launch rail containing the primary launch action.
- **SC-003**: UI tests confirm create-page large textareas use matte/readable styling and the primary CTA is labeled as a launch/commit action.
- **SC-004**: UI tests confirm task detail/evidence-heavy pages expose separate summary, facts, steps, evidence/log, and action regions without dense content using competing glass surfaces.
- **SC-005**: Existing task-list, create-page submission, and task-detail behavior tests continue to pass.
- **SC-006**: Traceability verification confirms MM-428, the trusted Jira preset brief, and DESIGN-REQ-014, DESIGN-REQ-017, DESIGN-REQ-019, DESIGN-REQ-020, and DESIGN-REQ-021 are preserved in MoonSpec artifacts and final evidence.
