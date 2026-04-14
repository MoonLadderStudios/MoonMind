# Feature Specification: Jira Create-Page Rollout Hardening

**Feature Branch**: `171-jira-rollout-hardening`  
**Created**: 2026-04-14  
**Status**: Draft  
**Input**: User description: "Implement Phase 10 and any unfinished work using test-driven development with the Jira UI plan for MoonMind's Create page. The fastest safe path is to add MoonMind-owned Jira browser endpoints, publish them through Create-page runtime config, add one shared Jira story browser UI, support imports into either preset instructions or a selected step, add explicit preset-reapply semantics, and harden with focused tests. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Safely Browse Jira While Creating a Task (Priority: P1)

An operator composing a new task can open a Jira story browser from the Create page, browse available Jira projects, boards, columns, and stories, preview a selected story, and continue manual task authoring even if Jira is disabled or unavailable.

**Why this priority**: Jira browsing is the foundation for every import path, and it must be additive so task creation remains reliable.

**Independent Test**: Enable the Jira Create-page rollout, open the browser from a task field, navigate from project to board to column to story preview, then repeat with Jira disabled and with Jira returning an error to confirm manual authoring still works.

**Acceptance Scenarios**:

1. **Given** Jira Create-page integration is disabled, **When** an operator opens the Create page, **Then** no Jira entry points are visible and manual task creation behaves unchanged.
2. **Given** Jira Create-page integration is enabled and Jira data is available, **When** an operator opens the Jira browser, selects a project, board, column, and story, **Then** the story preview displays normalized summary, description, acceptance criteria, and import options.
3. **Given** Jira returns an error or empty result, **When** the operator uses the Jira browser, **Then** the error or empty state remains local to the browser and the operator can continue editing and submit a manual task.

---

### User Story 2 - Import Jira Text Into the Correct Task Field (Priority: P1)

An operator can explicitly import selected Jira story text into either the preset objective field or any step instruction field, choosing whether to replace or append the imported text.

**Why this priority**: The core user value is reusing Jira story context without creating a separate task model or changing task submission semantics.

**Independent Test**: Import the same Jira story into the preset objective and into a secondary step using both replace and append actions, then verify only the selected target changes and task submission remains compatible with the existing task payload.

**Acceptance Scenarios**:

1. **Given** a Jira story preview is selected and the preset objective is the target, **When** the operator chooses replace, **Then** the preset objective text is replaced with the selected import text and the task objective resolves from that field.
2. **Given** a Jira story preview is selected and a step instruction field is the target, **When** the operator chooses replace, **Then** only that step's instructions are replaced.
3. **Given** any target already has text, **When** the operator chooses append, **Then** the existing text is preserved and the imported Jira text is added with a clear separator.
4. **Given** a template-derived step is targeted, **When** Jira text is imported into that step, **Then** the step is treated as manually customized and no longer relies on template instruction identity.

---

### User Story 3 - Understand Preset Reapply and Import Provenance (Priority: P2)

An operator can see when a Jira import affects preset inputs that have already been applied, and can see lightweight provenance showing which Jira issue was copied into a field.

**Why this priority**: Preset expansion is explicit; operators need clear feedback without hidden rewrites of existing steps.

**Independent Test**: Apply a preset, import Jira text into the preset objective, verify a reapply-needed message appears without mutating existing steps, and verify imported fields show a compact Jira issue chip.

**Acceptance Scenarios**:

1. **Given** a preset has already been applied, **When** Jira import changes the preset objective field, **Then** the Create page shows that the preset should be reapplied and does not silently rewrite already-expanded steps.
2. **Given** Jira text has been imported into a field, **When** the field is displayed, **Then** a compact provenance chip identifies the Jira issue.
3. **Given** Jira text has been imported into a field, **When** the operator reopens the Jira browser from that field, **Then** the prior issue context is preferred when still available.
4. **Given** an imported field is manually edited or removed, **When** the field is displayed, **Then** stale Jira provenance is removed.

---

### User Story 4 - Roll Out Jira UI Without Weakening Trusted Boundaries (Priority: P2)

An operator can enable the Jira Create-page browser independently from backend Jira tooling, and browser clients only interact with MoonMind-owned endpoints that enforce policy and never expose raw Jira credentials.

**Why this priority**: Jira credentials and project policy are trusted server-side concerns; the UI must not create a new credential path.

**Independent Test**: Enable backend Jira tooling while the Create-page Jira rollout is disabled, verify no Jira UI appears, then enable the UI rollout and verify runtime config exposes only MoonMind-owned endpoint templates and policy-denied Jira requests fail safely.

**Acceptance Scenarios**:

1. **Given** trusted Jira tooling is enabled but Create-page Jira integration is disabled, **When** the Create page loads, **Then** Jira browser controls remain hidden.
2. **Given** Create-page Jira integration is enabled, **When** the page boots, **Then** it receives Jira capability flags and MoonMind-owned endpoint templates.
3. **Given** a Jira project is outside the configured policy boundary, **When** the operator attempts to browse it, **Then** the request is denied without exposing secrets or raw provider details.

### Edge Cases

- Jira is not configured, credentials are missing, or a managed secret cannot be resolved.
- The configured default project or board no longer exists.
- A board has no columns, no issues in a selected column, or issues whose statuses do not map to a board column.
- Jira returns rich text, plain text, blank descriptions, or acceptance criteria in a separate custom field.
- A selected issue cannot be fetched after appearing in an issue list.
- A user imports Jira text into an empty field, a populated field, a primary step, a secondary step, or a template-derived step.
- The browser storage used for remembering the last project or board is unavailable.
- The task draft is submitted after a Jira browser failure.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose Jira browser entry points on the Create page only when a Create-page Jira rollout flag is enabled.
- **FR-002**: The system MUST keep Create-page Jira rollout separate from trusted backend Jira tooling enablement.
- **FR-003**: The system MUST publish Jira UI capability flags, default project and board values, session-memory preference, and MoonMind-owned endpoint templates through the Create-page boot configuration when enabled.
- **FR-004**: The browser client MUST NOT call Jira directly or receive raw Jira credentials.
- **FR-005**: The system MUST provide read-only Jira browsing capabilities for connection verification, projects, project boards, board columns, board issues, and issue detail.
- **FR-006**: Jira browser requests MUST enforce configured Jira policy boundaries, including project allowlists.
- **FR-007**: Jira browser failures MUST be returned as structured, safe errors that do not expose secrets, authorization material, stack traces, or raw provider-sensitive details.
- **FR-008**: The system MUST normalize Jira board configuration into ordered Create-page column objects.
- **FR-009**: The system MUST group board issues by server-resolved column mapping rather than requiring the browser to infer column membership from raw status text.
- **FR-010**: The system MUST return normalized issue summaries suitable for list display.
- **FR-011**: The system MUST return normalized issue detail containing description text, acceptance criteria text, and recommended import text for preset and step targets.
- **FR-012**: The Create page MUST render one shared Jira browser surface that can be opened from the preset objective field or from any step instructions field.
- **FR-013**: Opening the Jira browser from a field MUST preselect that field as the import target.
- **FR-014**: Selecting a Jira issue MUST NOT mutate task draft fields until the operator explicitly chooses an import action.
- **FR-015**: The Jira browser MUST support import modes for preset brief, execution brief, description only, and acceptance criteria only.
- **FR-016**: The Jira browser MUST support explicit replace and append actions for the selected target.
- **FR-017**: Importing into the preset target MUST update the preset objective field without directly rewriting the step list.
- **FR-018**: Importing into a step target MUST update only the selected step instructions.
- **FR-019**: Importing into a template-derived step MUST count as a manual instruction edit and detach template instruction identity when the imported text differs.
- **FR-020**: If Jira import changes preset objective text after a preset has already been applied, the system MUST show a reapply-needed message and preserve existing expanded steps until the operator explicitly reapplies the preset.
- **FR-021**: The Create page MUST retain lightweight field-level Jira provenance for imported fields and display a compact issue identifier near the edited field.
- **FR-022**: Reopening the Jira browser from an imported field SHOULD prefer the prior issue context when still available.
- **FR-023**: The Create page MAY remember the last selected Jira project and board for the current browser session when enabled by configuration.
- **FR-024**: Jira failures MUST NOT block manual preset editing, manual step editing, or task submission unless the operator is actively waiting for a Jira import action to complete.
- **FR-025**: The task submission payload MUST remain compatible with the existing Create-page task submission contract unless a later accepted requirement introduces submitted Jira provenance.
- **FR-026**: Required deliverables MUST include production runtime code changes, not docs/spec-only changes.
- **FR-027**: Required deliverables MUST include validation tests covering runtime configuration, trusted Jira browsing, UI browsing, import behavior, failure handling, provenance behavior, and unchanged task submission semantics.

### Key Entities *(include if feature involves data)*

- **Jira Integration Configuration**: Create-page capability state, endpoint templates, default project and board values, and session-memory preference exposed at page boot.
- **Jira Project**: A Jira project available for browsing, identified by project key and display name.
- **Jira Board**: A board associated with a project, identified by board id, name, type, and project key.
- **Jira Column**: A board-specific ordered column with a stable id, display name, issue count, and server-resolved status mapping.
- **Jira Issue Summary**: A list-display representation containing issue key, summary, type, status, assignee, update time, and mapped column.
- **Jira Issue Detail**: A preview/import representation containing issue identity, summary, status, column, normalized description, normalized acceptance criteria, and recommended import text.
- **Jira Import Target**: A selected Create-page destination, either the preset objective field or one step instruction field.
- **Jira Import Provenance**: Advisory local metadata identifying the imported issue, board, column when available, import mode, and target type.

### Assumptions

- The MVP reads Jira story content into the Create page; it does not create or mutate Jira issues.
- Jira provenance remains local UI metadata and is not submitted with the task payload unless a future requirement introduces a downstream consumer.
- Session memory for the last selected Jira project and board is best-effort and limited to the current browser session.
- Manual task creation remains the primary fallback whenever Jira is disabled, unavailable, empty, or policy-denied.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With Jira integration enabled and configured, an operator can browse from project to board to column to issue preview and import into a selected target in one Create-page session.
- **SC-002**: With Jira integration disabled, existing Create-page manual task creation scenarios remain unchanged and Jira controls are absent.
- **SC-003**: 100% of Jira browser endpoint responses exposed to the UI are MoonMind-owned paths and contain no raw credential material.
- **SC-004**: Jira unavailable, empty, and policy-denied states are visible inside the Jira browser without preventing manual task submission.
- **SC-005**: Importing into preset and step targets is covered by automated tests for replace and append behavior.
- **SC-006**: Preset reapply behavior is covered by automated tests proving already-expanded steps are not silently rewritten.
- **SC-007**: Template-derived step import behavior is covered by automated tests proving manual customization detaches template instruction identity.
- **SC-008**: Task submission after Jira import or Jira failure remains compatible with the existing task submission payload shape.
- **SC-009**: Runtime configuration, backend browser normalization, policy-denied handling, secret redaction, UI browsing, provenance, and failure paths are covered by validation tests.
