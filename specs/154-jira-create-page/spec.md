# Feature Specification: Jira Create Page Integration

**Feature Branch**: `154-jira-create-page`
**Created**: 2026-04-11
**Status**: Draft
**Input**: User description: "Implement the Jira Create-page integration for MoonMind in runtime mode. Treat docs/UI/CreatePage.md as the canonical desired-state design. Jira must plug into the existing Create page authoring surfaces, use MoonMind-owned browser operations through the trusted Jira boundary, expose rollout-gated runtime config, support browsing Jira project/board/column/issue detail, import Jira text into preset or step instructions, preserve preset reapply semantics and template-bound step edit behavior, keep Jira failures local and non-blocking, include provenance/session preferences, and deliver production runtime code changes plus validation tests."

## Source Document Requirements

The feature is backed by `docs/UI/CreatePage.md`. These source requirements are implementation-agnostic summaries of the desired-state contract and each maps to at least one functional requirement below.

| Requirement ID | Source Citation | Requirement Summary | Functional Requirement Mapping |
| --- | --- | --- | --- |
| DOC-REQ-001 | `docs/UI/CreatePage.md` §1, lines 9-21 | The Create page is the single task-composition surface and Jira story text may be imported into either step instructions or preset initial instructions. | FR-013, FR-014, FR-020, FR-022 |
| DOC-REQ-002 | `docs/UI/CreatePage.md` §3, lines 38-54 | Jira is only an external instruction source; manual entry remains first-class; browser clients must call MoonMind only; imported Jira text is a one-time copy, not a live sync. | FR-005, FR-027, FR-030, FR-032 |
| DOC-REQ-003 | `docs/UI/CreatePage.md` §6, lines 111-127 | The Jira browser is not a top-level Create page section; it is one shared secondary instruction-source surface invoked from Steps and Task Presets. | FR-013, FR-014, FR-015 |
| DOC-REQ-004 | `docs/UI/CreatePage.md` §7.3, lines 160-168 | Importing Jira text into a template-bound step counts as a manual instruction edit and detaches template instruction identity when the text diverges. | FR-023, FR-026 |
| DOC-REQ-005 | `docs/UI/CreatePage.md` §8.3-§8.4, lines 202-224 | Preset initial instructions are the preset-owned objective source; changing them, including through Jira import, must mark applied presets dirty without silently overwriting expanded steps. | FR-021, FR-024, FR-025 |
| DOC-REQ-006 | `docs/UI/CreatePage.md` §14, lines 325-358 | Jira browser entry points may render only when runtime config explicitly enables Jira integration, and browser clients must not embed Jira credentials or Jira-domain knowledge beyond documented responses. | FR-001, FR-002, FR-003, FR-004, FR-005 |
| DOC-REQ-007 | `docs/UI/CreatePage.md` §15.1-§15.2, lines 362-426 | MoonMind must provide Create-page-ready board columns and issue lists, resolving board configuration and status-to-column mapping server-side while keeping empty columns renderable. | FR-007, FR-009, FR-010, FR-011 |
| DOC-REQ-008 | `docs/UI/CreatePage.md` §15.3, lines 428-455 | Issue detail must provide normalized story text and target-specific recommended import text so the browser does not parse Jira rich-text formats. | FR-012 |
| DOC-REQ-009 | `docs/UI/CreatePage.md` §16-§17, lines 459-516 | One shared Jira browser must preserve draft state, preselect the opening target, allow target visibility or switching, and require explicit import before writing. | FR-015, FR-016, FR-017, FR-018 |
| DOC-REQ-010 | `docs/UI/CreatePage.md` §18-§19, lines 517-570 | Jira import must support target-aware import modes plus explicit replace and append actions, with append preserving existing text and adding separation. | FR-018, FR-019, FR-020, FR-022 |
| DOC-REQ-011 | `docs/UI/CreatePage.md` §20, lines 574-601 | Jira provenance is advisory field-level UI metadata; absence of submitted provenance must not block task creation. | FR-027, FR-028, FR-029, FR-030 |
| DOC-REQ-012 | `docs/UI/CreatePage.md` §21-§22, lines 606-643 | Jira failures and empty states must remain local and additive, while the existing Create page submission flow, objective resolution, and task type remain unchanged. | FR-030, FR-031, FR-032, FR-033 |
| DOC-REQ-013 | `docs/UI/CreatePage.md` §23, lines 647-659 | Jira browser controls and field affordances must meet the same accessibility expectations as the rest of Mission Control. | FR-036 |
| DOC-REQ-014 | `docs/UI/CreatePage.md` §24, lines 661-675 | Test coverage must include hidden entry points, board/column behavior, issue selection, import targets, template detachment, preset reapply signaling, failure isolation, and unchanged submission behavior. | FR-035 |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Discover Jira Capability Safely (Priority: P1)

An operator can enable or disable the Create-page Jira browser independently from trusted backend Jira tooling, and the Create page only exposes Jira controls when the UI rollout is enabled.

**Why this priority**: This protects existing Create page behavior and lets operators roll out browser-facing Jira access without changing trusted agent tool availability.

**Independent Test**: Generate the dashboard boot payload with the Jira UI rollout disabled and enabled, then verify Jira sources and UI capability metadata are absent when disabled and complete when enabled.

**Acceptance Scenarios**:

1. **Given** Jira trusted tooling is configured but the Create-page Jira rollout is disabled, **When** a user opens the Create page, **Then** Jira browser entry points are not available and existing manual task creation behavior is unchanged.
2. **Given** the Create-page Jira rollout is enabled, **When** the Create page receives runtime configuration, **Then** it includes Jira browser source locations and Jira integration defaults for project, board, and session memory.
3. **Given** a default Jira project or board is configured, **When** a user opens the Jira browser, **Then** the browser can start from those defaults without requiring the operator to expose credentials to the browser.

---

### User Story 2 - Browse Jira Stories From Task Creation (Priority: P1)

A task author can open one shared Jira browser from either the preset objective field or a step instructions field, navigate available Jira projects, boards, board columns, and issue details, then inspect the story before importing anything.

**Why this priority**: Browsing and previewing are the core user journey that makes Jira useful as an instruction source while preserving the Create page as the primary task-composition surface.

**Independent Test**: With Jira browser capability enabled and a configured connection, open the browser from preset and step contexts, navigate project to board to column to issue, and verify the selected issue detail is shown without changing task fields.

**Acceptance Scenarios**:

1. **Given** a user is editing preset initial instructions, **When** they open the Jira browser from that field, **Then** the browser opens with the preset target selected.
2. **Given** a user is editing a specific step, **When** they open the Jira browser from that step, **Then** the browser opens with that step as the selected target.
3. **Given** a board has ordered columns and issues in multiple statuses, **When** the user selects the board, **Then** columns appear in board order and issues are visible under the correct column.
4. **Given** a user selects an issue, **When** the issue detail loads, **Then** the user can review normalized summary, description, acceptance criteria, and recommended import text before importing.

---

### User Story 3 - Import Jira Text Into the Right Authoring Target (Priority: P1)

A task author can copy normalized Jira story text into either the preset Feature Request / Initial Instructions field or one selected step's Instructions field using replace or append behavior and a chosen import mode.

**Why this priority**: Importing text into existing authoring fields delivers the main value without creating a separate Jira task model or changing task submission semantics.

**Independent Test**: Import the same Jira issue into a preset target and a step target using replace and append modes, then verify only the intended field changes and task objective precedence remains correct.

**Acceptance Scenarios**:

1. **Given** a preset target is selected and the user chooses Replace, **When** they import a Jira execution brief, **Then** the preset initial instructions are replaced and become the preferred task objective source.
2. **Given** a preset target contains existing text and the user chooses Append, **When** they import Jira acceptance criteria, **Then** the imported text is appended without removing the existing text.
3. **Given** a step target is selected, **When** the user imports Jira text, **Then** only that step's instructions change.
4. **Given** a step originated from a preset template, **When** Jira import changes that step's instructions, **Then** the step is treated as manually customized.

---

### User Story 4 - Preserve Preset and Failure Safety (Priority: P2)

A task author receives clear signals when Jira import affects preset inputs or template-bound steps, and Jira outages do not block manual task creation.

**Why this priority**: Preset expansion and manual task creation are existing Create page behaviors that must remain predictable as Jira is added.

**Independent Test**: Apply a preset, import Jira into preset instructions, verify the user is prompted to reapply explicitly, then simulate Jira browser failures and verify manual editing and task creation remain available.

**Acceptance Scenarios**:

1. **Given** a preset has already been applied, **When** Jira import changes preset initial instructions, **Then** already-expanded steps remain unchanged and the page shows that the preset needs explicit reapply.
2. **Given** a template-bound step is selected as the import target, **When** the user starts an import, **Then** the page warns that the step will become manually customized while still allowing the import.
3. **Given** Jira browser loading fails, **When** the user continues editing task steps manually, **Then** manual editing and task creation remain usable.
4. **Given** an issue was imported, **When** the user views the edited field, **Then** a compact Jira issue provenance marker is visible near the affected field.

### Edge Cases

- Jira UI rollout is disabled while trusted backend Jira tooling is enabled.
- Jira connection verification fails or credentials are missing, expired, or denied.
- A user has no allowed Jira projects or selects a project denied by policy.
- A board has no columns, no issues, or issue statuses that do not map cleanly to visible columns.
- Issue detail lacks description, acceptance criteria, or either recommended import text.
- A user switches targets while the shared Jira browser is open.
- A user imports into a field that already contains text and chooses append.
- A user imports into preset instructions after applying a preset.
- A user imports into a template-bound step whose current text still matches the template.
- Browser session memory is disabled or contains a project or board that is no longer available.
- Jira browser operations fail while the user is otherwise ready to submit a manual task.

### Assumptions

- `docs/UI/CreatePage.md` remains the canonical desired-state contract for the Create page during this feature.
- Jira is an optional instruction source for task authoring, not a replacement task type or execution substrate.
- Existing manual step authoring, preset application, dependency selection, scheduling, and task submission behavior must remain usable when Jira is disabled or unavailable.
- Existing trusted Jira configuration, policy, and secret handling are the required security boundary for browser-facing Jira data.
- Jira provenance is local Create-page state for the initial delivery and is not persisted into submitted task payloads.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST keep Create-page Jira browser exposure controlled by a UI-specific rollout setting that is separate from trusted Jira tool enablement. (DOC-REQ-006)
- **FR-002**: System MUST omit Jira browser sources and Jira integration metadata from the Create-page boot payload when the Jira UI rollout is disabled. (DOC-REQ-006)
- **FR-003**: System MUST include Jira browser source locations and Jira integration metadata in the Create-page boot payload when the Jira UI rollout is enabled. (DOC-REQ-006)
- **FR-004**: System MUST surface configured default Jira project, default Jira board, and session-memory preference values to the Create page when Jira UI is enabled. (DOC-REQ-006)
- **FR-005**: System MUST ensure browser clients interact only with MoonMind-owned Jira browser operations and never receive raw Jira credentials. (DOC-REQ-002, DOC-REQ-006)
- **FR-006**: System MUST verify the configured Jira connection through the trusted MoonMind Jira boundary before presenting Jira data as available.
- **FR-007**: System MUST provide project, board, board-column, board-issue, and issue-detail browsing capabilities through the trusted Jira boundary. (DOC-REQ-007)
- **FR-008**: System MUST enforce configured Jira project allowlists and Jira policy decisions on all browser-facing Jira read operations.
- **FR-009**: System MUST normalize Jira board configuration into stable column records that preserve board order. (DOC-REQ-007)
- **FR-010**: System MUST map Jira issues to board columns using board status mappings and provide safe handling for statuses that cannot be mapped. (DOC-REQ-007)
- **FR-011**: System MUST return normalized issue summaries suitable for list display without exposing unnecessary raw Jira response data. (DOC-REQ-007)
- **FR-012**: System MUST return normalized issue detail containing description text, acceptance criteria text, recommended preset-instruction import text, and recommended step-instruction import text. (DOC-REQ-008)
- **FR-013**: Users MUST be able to open one shared Jira browser surface from the preset Feature Request / Initial Instructions field. (DOC-REQ-001, DOC-REQ-003)
- **FR-014**: Users MUST be able to open the same shared Jira browser surface from any step Instructions field. (DOC-REQ-001, DOC-REQ-003)
- **FR-015**: System MUST keep only one Jira browser surface open at a time and preserve the current import target context. (DOC-REQ-003, DOC-REQ-009)
- **FR-016**: Users MUST be able to navigate from project to board to column to issue detail before importing text. (DOC-REQ-009)
- **FR-017**: Users MUST be able to choose an import target of either preset instructions or one specific step's instructions. (DOC-REQ-009)
- **FR-018**: Users MUST be able to choose replace or append behavior before applying an import. (DOC-REQ-009, DOC-REQ-010)
- **FR-019**: Users MUST be able to import at least four normalized text modes: preset brief, execution brief, description only, and acceptance criteria only. (DOC-REQ-010)
- **FR-020**: System MUST write preset-target imports into the preset Feature Request / Initial Instructions field. (DOC-REQ-001, DOC-REQ-010)
- **FR-021**: System MUST preserve objective precedence so non-empty preset initial instructions remain preferred over primary step instructions for task objective resolution. (DOC-REQ-005)
- **FR-022**: System MUST write step-target imports only into the selected step's Instructions field. (DOC-REQ-001, DOC-REQ-010)
- **FR-023**: System MUST treat Jira import into a template-bound step as a manual instruction edit, including detaching template identity when the imported text diverges from template instructions. (DOC-REQ-004)
- **FR-024**: System MUST show a non-blocking reapply-needed message when Jira import changes preset instructions after a preset has already been applied. (DOC-REQ-005)
- **FR-025**: System MUST NOT silently rewrite already-expanded preset steps when preset instructions change through Jira import. (DOC-REQ-005)
- **FR-026**: System MUST warn before importing into a template-bound step that the step will become manually customized while still allowing the import. (DOC-REQ-004)
- **FR-027**: System MUST track local Jira import provenance for issue key, board, import mode, and target type. (DOC-REQ-002, DOC-REQ-011)
- **FR-028**: System MUST show a compact Jira issue marker near a field or section changed by Jira import. (DOC-REQ-011)
- **FR-029**: System MUST remember the last selected Jira project and board for the browser session only when session memory is enabled. (DOC-REQ-011)
- **FR-030**: System MUST keep the task submission contract unchanged for the initial delivery and MUST NOT require Jira provenance in the submitted task payload. (DOC-REQ-002, DOC-REQ-011, DOC-REQ-012)
- **FR-031**: System MUST keep Jira loading and request failures local to the Jira browser surface. (DOC-REQ-012)
- **FR-032**: System MUST keep manual step editing, preset editing, and task creation usable when Jira is unavailable. (DOC-REQ-002, DOC-REQ-012)
- **FR-033**: System MUST only block an import action while the user is actively waiting on that import, and MUST NOT disable general task creation because Jira failed. (DOC-REQ-012)
- **FR-034**: Required deliverables MUST include production runtime code changes, not docs/spec-only changes.
- **FR-035**: Required deliverables MUST include validation tests covering the runtime configuration, trusted Jira browser path, normalization behavior, policy/secret safety, Create-page browsing, import behavior, preset safety, and Jira failure isolation. (DOC-REQ-014)
- **FR-036**: System MUST make Jira browser open, close, target, column navigation, issue selection, and import actions keyboard accessible with clear target context and predictable focus behavior after import. (DOC-REQ-013)

### Key Entities *(include if feature involves data)*

- **Jira Integration Runtime Capability**: The Create-page capability metadata that declares whether Jira browser UI is enabled, where browser operations are reached, and which project or board defaults apply.
- **Jira Connection Verification**: The result of checking the configured trusted Jira connection and policy boundary before browser data is presented.
- **Jira Project**: A Jira project visible to the current MoonMind configuration and allowed by policy.
- **Jira Board**: A Jira board associated with a project and used as the browsing context for columns and issues.
- **Jira Column**: A normalized board column with stable identity, display name, order, and status mapping.
- **Jira Issue Summary**: A normalized issue row for board-column browsing, including enough information to identify and choose a story.
- **Jira Issue Detail**: A normalized issue preview that includes description, acceptance criteria, and recommended import text variants.
- **Jira Import Target**: The Create-page destination selected for import, either preset initial instructions or a specific step's instructions.
- **Jira Import Provenance**: Local UI state describing the imported issue key, board, import mode, and target type.
- **Preset Reapply Signal**: A non-blocking state indicating preset inputs changed after a preset was applied and require explicit reapply before generated steps are updated.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With Jira UI disabled, the Create page boot payload contains no Jira browser contract and existing Create page task submission tests continue to pass.
- **SC-002**: With Jira UI enabled, a user can navigate from project to board to column to issue detail in one browser session without exposing Jira credentials to the browser.
- **SC-003**: A user can import Jira text into preset instructions or any selected step in under 30 seconds after selecting an issue detail.
- **SC-004**: Replace and append imports update only the selected target in all covered preset and step scenarios.
- **SC-005**: Importing into preset instructions after preset application never changes already-expanded steps unless the user explicitly reapplies the preset.
- **SC-006**: Importing into a template-bound step is recorded as a manual customization in all covered tests.
- **SC-007**: Jira browser failures do not prevent a user from manually editing a task and submitting it.
- **SC-008**: Automated validation covers runtime configuration, browser operation behavior, issue normalization, policy denial, secret redaction, Create-page navigation, import behavior, preset reapply messaging, accessibility expectations, and Jira failure isolation.
