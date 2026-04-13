# Feature Specification: Jira Create Browser

**Feature Branch**: `161-jira-create-browser`  
**Created**: 2026-04-12  
**Status**: Draft  
**Input**: User description: "Implement Phase 4 using test-driven development of the Jira UI plan. Treat docs/UI/CreatePage.md as the desired-state contract for the Create page Jira browsing experience. Add frontend Jira state, data hooks, and one shared browser surface in the Create page so users can open a \"Browse Jira story\" modal or drawer from either the preset Feature Request / Initial Instructions field or from any step Instructions field. The browser must be runtime-config gated, use MoonMind-owned Jira browser endpoints exposed by runtime config, and allow navigation from project to board to board columns to issue list to issue detail without importing text yet. It must preserve existing Create page behavior when Jira UI is disabled, keep one browser surface open at a time, preselect the correct target when opened from preset or step context, and keep manual task creation usable when Jira data loading fails. Add frontend types for JiraIntegrationConfig, JiraProject, JiraBoard, JiraColumn, JiraIssueSummary, JiraIssueDetail, and JiraImportTarget. Add state for selected project, selected board, active column, issue list by column, selected issue, browser open/closed, current import target, replace/append preference, and loading/error states. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

The source contract is `docs/UI/CreatePage.md`. The following requirements are extracted for the Phase 4 browser-shell scope and are mapped to functional requirements below.

| ID | Source | Requirement Summary | Functional Requirement Mapping |
| --- | --- | --- | --- |
| DOC-REQ-001 | `docs/UI/CreatePage.md` section 14, "Jira integration: runtime config contract" | The Create page may expose Jira browser entry points only when runtime configuration explicitly enables Jira integration; `sources.jira` alone is insufficient. | FR-001, FR-002 |
| DOC-REQ-002 | `docs/UI/CreatePage.md` section 14, "Jira integration: runtime config contract" | Browser clients must call MoonMind-owned Jira endpoints and must not embed Jira credentials or Jira-domain knowledge beyond documented response shapes. | FR-008 |
| DOC-REQ-003 | `docs/UI/CreatePage.md` section 15.1, "Column contract" | Jira columns are board-specific, column mapping is resolved by MoonMind, and the browser renders columns in board order. | FR-009 |
| DOC-REQ-004 | `docs/UI/CreatePage.md` section 15.2, "Issue-list contract" | The issue-list browser model must support issues grouped by active board column, including empty columns, without browser-side status inference. | FR-009, FR-010 |
| DOC-REQ-005 | `docs/UI/CreatePage.md` section 15.3, "Issue-detail contract" | Issue detail must be normalized for preview, including description and acceptance criteria text, and the browser consumes normalized text rather than Jira rich text. | FR-011 |
| DOC-REQ-006 | `docs/UI/CreatePage.md` section 16, "Jira integration: shared browser surface" | Jira browsing is one shared secondary instruction-source surface that opens from a target field, preserves the rest of the draft, and shows target, project, board, columns, issue list, and preview. | FR-003, FR-004, FR-005, FR-012 |
| DOC-REQ-007 | `docs/UI/CreatePage.md` section 16, "Jira integration: shared browser surface" | Selecting a Jira issue must not write into the draft automatically. | FR-012 |
| DOC-REQ-008 | `docs/UI/CreatePage.md` section 17, "Jira integration: target model" | Jira import targeting must include the preset objective field and any step instruction field, with the browser displaying the selected target explicitly. | FR-003, FR-004, FR-005 |

Out-of-scope source requirements for later Jira phases: text import execution, import modes, import actions, preset reapply behavior, provenance display, and session memory. This Phase 4 specification preserves their contracts by not performing text import or task payload changes.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Open Jira Browser From Preset Instructions (Priority: P1)

An operator composing a preset-oriented task can open a shared Jira story browser from the `Feature Request / Initial Instructions` field and see that the preset field is the selected destination for a future import.

**Why this priority**: Preset objective text is the primary target for Jira stories that should drive a preset workflow, and this story establishes the shared browser surface without changing task submission semantics.

**Independent Test**: Enable Jira browser capability in the runtime configuration, open the Create page, activate the Jira browser from the preset instruction field, and verify that a single browser surface opens with the preset instructions as the target.

**Acceptance Scenarios**:

1. **Given** Jira browser capability is enabled, **When** the operator opens Jira browsing from the preset instructions field, **Then** one Jira browser surface opens and identifies `Feature Request / Initial Instructions` as the current target.
2. **Given** Jira browser capability is disabled, **When** the operator views the Create page, **Then** Jira browser controls are not visible and the existing preset controls behave as before.

---

### User Story 2 - Open Jira Browser From Step Instructions (Priority: P1)

An operator composing a manual or multi-step task can open the same Jira story browser from any step's `Instructions` field and see that the selected step is the target for a future import.

**Why this priority**: Step instructions are the canonical authored execution units, so Jira browsing must plug into each step without creating a separate task model.

**Independent Test**: Enable Jira browser capability, open the browser from a step's instruction field, and verify that the browser opens with that step selected as the current target while the step draft remains unchanged.

**Acceptance Scenarios**:

1. **Given** a Create page draft has one or more steps, **When** the operator opens Jira browsing from a step, **Then** the shared browser opens with that exact step as the selected target.
2. **Given** the browser is already open for one target, **When** the operator opens it from another target, **Then** the same shared browser surface is reused and reflects the new target.

---

### User Story 3 - Browse Jira Board Stories (Priority: P2)

An operator can browse Jira content through MoonMind-owned endpoints by selecting a project, selecting a board, switching board columns in board order, viewing issues for the active column, and selecting an issue to preview normalized story details.

**Why this priority**: The feature is valuable only if operators can navigate from the Create page to the right Jira story without leaving MoonMind or exposing Jira credentials to the browser.

**Independent Test**: Provide runtime-configured Jira endpoints with sample projects, boards, columns, issue lists, and issue detail responses; then verify project-to-board-to-column-to-issue navigation and issue preview rendering.

**Acceptance Scenarios**:

1. **Given** Jira projects and boards are available, **When** the browser opens, **Then** the operator can select or use configured defaults for project and board.
2. **Given** a board has ordered columns, **When** the board loads, **Then** columns render in the returned order with their issue counts.
3. **Given** the operator switches columns, **When** a different column becomes active, **Then** only that column's issue list is shown.
4. **Given** the operator selects an issue, **When** issue detail loads, **Then** the browser displays the issue key, summary, description text, and acceptance criteria text when available.

---

### User Story 4 - Keep Manual Task Creation Independent (Priority: P2)

An operator can continue editing and creating manual tasks even if Jira browser data fails to load.

**Why this priority**: Jira is an additive instruction source; it must not block the Create page's core manual authoring path.

**Independent Test**: Simulate failures from Jira browser data sources and verify errors remain inside the Jira browser while manual preset, step, and submit controls remain usable.

**Acceptance Scenarios**:

1. **Given** Jira project, board, column, issue-list, or issue-detail loading fails, **When** the operator is using the Jira browser, **Then** the failure is shown locally within the browser.
2. **Given** Jira browsing fails, **When** the operator edits manual instructions or submits a valid non-Jira task, **Then** Jira failure state does not block those actions.

### Edge Cases

- Jira browser capability is disabled while trusted Jira tooling remains enabled for non-browser use.
- Runtime configuration exposes incomplete Jira browser endpoints.
- Configured default project or board is absent from the returned data.
- A selected project has no boards.
- A selected board has no columns or has empty columns.
- An issue list contains columns with no issues.
- Issue detail loading fails after a summary was selected.
- The operator opens the browser from a target after a previous target was selected.
- Existing Create page fields already contain draft text before the browser opens.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Create page MUST show Jira browser entry controls only when runtime configuration explicitly enables the Jira browser capability and provides MoonMind-owned Jira browser sources. Maps to DOC-REQ-001.
- **FR-002**: When Jira browser capability is disabled, the Create page MUST preserve existing step editing, preset editing, dependency selection, runtime configuration, scheduling, and submission behavior. Maps to DOC-REQ-001.
- **FR-003**: Users MUST be able to open a single shared `Browse Jira story` browser surface from the preset `Feature Request / Initial Instructions` field. Maps to DOC-REQ-006 and DOC-REQ-008.
- **FR-004**: Users MUST be able to open the same shared `Browse Jira story` browser surface from any step `Instructions` field. Maps to DOC-REQ-006 and DOC-REQ-008.
- **FR-005**: The browser MUST identify the active import target as either the preset instruction field or the specific step instruction field that opened it. Maps to DOC-REQ-006 and DOC-REQ-008.
- **FR-006**: The browser MUST maintain state for selected project, selected board, active column, issue list by column, selected issue, open or closed status, current import target, replace or append preference, and loading and error states.
- **FR-007**: The implementation MUST use explicit client-side representations for Jira integration configuration, Jira projects, Jira boards, Jira columns, Jira issue summaries, Jira issue detail, and Jira import targets.
- **FR-008**: The browser MUST fetch Jira projects, boards, columns, board issues, and issue detail only through MoonMind-owned endpoints supplied by runtime configuration. Maps to DOC-REQ-002.
- **FR-009**: The browser MUST render board columns in the order returned by MoonMind and MUST not infer column membership from raw issue status text. Maps to DOC-REQ-003 and DOC-REQ-004.
- **FR-010**: The browser MUST show issues for the active column and update the visible issue list when the active column changes. Maps to DOC-REQ-004.
- **FR-011**: The browser MUST load and display normalized issue detail only after the user explicitly selects an issue. Maps to DOC-REQ-005.
- **FR-012**: Selecting an issue MUST NOT modify preset instructions, step instructions, task objective, task title, task submission payload, or already-expanded preset steps in this phase. Maps to DOC-REQ-006 and DOC-REQ-007.
- **FR-013**: The browser MUST expose replace-or-append preference state without performing text import in this phase.
- **FR-014**: Jira browser loading failures MUST remain local to the browser and MUST NOT prevent manual task creation or manual instruction editing.
- **FR-015**: Required deliverables MUST include production runtime behavior changes and validation tests; docs-only or spec-only changes are insufficient for this feature.

### Key Entities *(include if feature involves data)*

- **Jira Integration Configuration**: Runtime-provided capability flag, default project and board preferences, session-memory preference, and MoonMind-owned browser source locations.
- **Jira Project**: A selectable Jira project, identified by a project key and display name.
- **Jira Board**: A selectable board associated with a project and used to load ordered board columns and issues.
- **Jira Column**: A board-specific column with a stable identifier, display name, and optional issue count.
- **Jira Issue Summary**: A compact issue-list item containing issue key, summary, type, status, assignee, and update metadata when available.
- **Jira Issue Detail**: Normalized story content for preview, including issue key, summary, status, description text, acceptance criteria text, and recommended import text when available.
- **Jira Import Target**: The Create page destination selected for a future import, either the preset instruction field or a specific step instruction field.
- **Jira Browser State**: The transient Create page state that tracks browser visibility, selections, loading status, errors, current target, and replace-or-append preference.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With Jira browser capability disabled, 100% of existing Create page unit tests continue to pass and no Jira browser entry controls are visible.
- **SC-002**: With Jira browser capability enabled, users can open the browser from both preset instructions and step instructions in one action each.
- **SC-003**: Users can complete project-to-board-to-column-to-issue-preview navigation using MoonMind-provided sample data in a validation test without editing any task text.
- **SC-004**: Switching board columns updates the visible issue list within one user action and keeps unrelated column issues hidden.
- **SC-005**: Issue selection displays normalized story detail without changing preset instructions, step instructions, objective resolution, or task submission payload.
- **SC-006**: Jira fetch failures are covered by validation tests showing that errors remain local to the browser and manual task creation remains available.
- **SC-007**: Production runtime changes and validation tests are present in the implementation artifacts for this feature.

## Assumptions

- Runtime configuration and MoonMind-owned Jira browser endpoints already exist or are being delivered by prior phases.
- This phase intentionally stops before importing Jira text into preset or step fields.
- Jira browser provenance persistence, session memory, and import execution are deferred to later phases unless separately requested.
- The Create page remains the single task authoring surface; Jira browsing does not create a parallel task model.
