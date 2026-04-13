# Feature Specification: Jira Provenance Polish

**Feature Branch**: `167-jira-provenance-polish`  
**Created**: 2026-04-13  
**Status**: Draft  
**Input**: User description: "Implement Phase 7 of Jira Create-page integration. Add production runtime behavior for local provenance metadata when Jira story text is imported into preset instructions or step instructions, showing compact Jira issue chips near edited fields or sections. Remember the last selected Jira project and board for the browser session only when runtime config enables rememberLastBoardInSession. Keep Temporal task submission semantics unchanged and do not persist Jira provenance in task payloads. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See Jira Import Origin (Priority: P1)

An operator imports text from a Jira story into either preset instructions or a step's instructions and can immediately see which Jira issue supplied the imported text.

**Why this priority**: The main value of this phase is operator clarity after an import. Without visible provenance, operators may lose track of which Jira story shaped a task draft.

**Independent Test**: Import one Jira story into the preset instructions and one Jira story into a step, then verify each edited area displays a compact `Jira: <issue key>` indicator without changing any unrelated draft fields.

**Acceptance Scenarios**:

1. **Given** Jira browser support is enabled and a Jira issue is selected, **When** the operator imports the issue into preset instructions, **Then** the preset instruction area shows a compact provenance indicator with the imported issue key.
2. **Given** Jira browser support is enabled and a Jira issue is selected, **When** the operator imports the issue into a step's instructions, **Then** only that step shows a compact provenance indicator with the imported issue key.
3. **Given** a field has Jira provenance, **When** the operator manually edits that field after import, **Then** the stale provenance indicator is removed for that field.

---

### User Story 2 - Remember Last Jira Board in Session (Priority: P2)

An operator who repeatedly imports from the same Jira project and board during a browser session does not need to reselect them every time the Create page is refreshed or reopened, when the runtime configuration allows this behavior.

**Why this priority**: Session-only memory removes repeated selection friction while respecting the rollout flag and avoiding durable user preference storage.

**Independent Test**: Enable the session-memory setting, select a project and board in the Jira browser, refresh or remount the page in the same browser session, and verify those selections are restored.

**Acceptance Scenarios**:

1. **Given** Jira browser support and session memory are enabled, **When** the operator selects a Jira project and board, **Then** those selections are remembered for the current browser session.
2. **Given** remembered Jira selections exist in the current browser session, **When** the operator opens the Jira browser again, **Then** the browser uses the remembered project and board instead of falling back to configured defaults.
3. **Given** session memory is disabled by runtime configuration, **When** the operator selects a Jira project and board, **Then** those selections are not remembered for later page loads.

---

### User Story 3 - Keep Task Submission Unchanged (Priority: P3)

An operator can use Jira provenance cues while creating a task, but the submitted task remains the same shape as before this polish phase.

**Why this priority**: Jira provenance is intentionally local Create-page context for this MVP. Preserving the task submission contract avoids coupling Jira metadata into downstream execution before a consumer exists.

**Independent Test**: Import Jira text, create the task, and verify the submission payload includes the imported instruction text but does not include Jira provenance metadata such as issue key, board id, target type, or import mode as separate persisted fields.

**Acceptance Scenarios**:

1. **Given** Jira text has been imported into a draft, **When** the operator submits the task, **Then** the submitted task contains the edited instruction text and no separate Jira provenance metadata.
2. **Given** Jira provenance indicators are visible on the Create page, **When** the task is submitted, **Then** runtime, objective resolution, dependency validation, attachments, scheduling, and publish settings behave as they did before this feature.

### Edge Cases

- If a Jira issue lacks an issue key, the system must not show an empty or misleading provenance indicator.
- If a step with Jira provenance is removed, the system must remove the local provenance associated with that step.
- If the operator clears the selected project or board, the session memory must clear the corresponding remembered value rather than restoring it immediately.
- If browser session storage is unavailable, blocked, or throws an error, the Create page must remain usable and must fall back to normal Jira selection behavior.
- If Jira browser runtime configuration is absent or disabled, no Jira provenance or session-memory controls should be visible or active.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST add production runtime behavior to track local Jira import provenance when Jira text is imported into preset instructions or step instructions.
- **FR-002**: The system MUST record, at minimum, the imported issue key, board id, import mode, and target type in local Create-page state after a successful Jira import.
- **FR-003**: The system MUST show a compact provenance indicator near the edited preset or step instruction area using the format `Jira: <issue key>`.
- **FR-004**: The system MUST keep provenance indicators scoped to the exact target edited by the import; importing into one step must not mark other steps or the preset area.
- **FR-005**: The system MUST remove stale local provenance from a field when an operator manually edits that field after import.
- **FR-006**: The system MUST remember the last selected Jira project and board for only the current browser session when runtime configuration enables session memory.
- **FR-007**: The system MUST NOT remember Jira project or board selections when runtime configuration disables session memory.
- **FR-008**: The system MUST clear remembered Jira selection values when the operator manually clears the corresponding project or board selection.
- **FR-009**: The system MUST keep Jira provenance metadata out of task submission payloads for this MVP while preserving imported instruction text.
- **FR-010**: The system MUST preserve existing Create page task submission semantics, including objective resolution, dependency validation, attachment handling, scheduling, runtime settings, and publish settings.
- **FR-011**: The system MUST remain usable if browser session storage is unavailable or fails.
- **FR-012**: The system MUST include validation tests for provenance display, session-memory behavior, stale provenance clearing, and unchanged submission payload semantics.
- **FR-013**: Required deliverables MUST include production runtime code changes and validation tests; documentation or specification changes alone are not sufficient.

### Key Entities *(include if feature involves data)*

- **Jira Import Provenance**: Local Create-page metadata describing the source and import context for one successful Jira import. Key attributes include issue key, board id, import mode, and target type.
- **Jira Import Target**: The draft field that receives imported Jira text. Targets are either preset instructions or one specific step's instructions.
- **Session Jira Selection**: Browser-session-only memory of the last selected Jira project and board, active only when enabled by runtime configuration.

### Assumptions

- Jira browser endpoints, issue selection, and import actions already exist and remain the source of Jira issue text.
- Runtime configuration already provides a setting that determines whether last board selection should be remembered for the browser session.
- No downstream workflow or task execution consumer currently requires persisted Jira provenance metadata.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In validation tests, importing a Jira issue into preset instructions displays the correct `Jira: <issue key>` indicator for the preset area in one interaction flow.
- **SC-002**: In validation tests, importing a Jira issue into a step displays the correct `Jira: <issue key>` indicator only for that step.
- **SC-003**: In validation tests, remembered Jira project and board selections are restored during the same browser session when enabled by runtime configuration.
- **SC-004**: In validation tests, task submission after Jira import does not include separate Jira provenance metadata fields.
- **SC-005**: Existing Create page behavior remains available with Jira disabled, and existing Create page validation tests continue to pass.
