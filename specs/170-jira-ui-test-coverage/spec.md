# Feature Specification: Jira UI Test Coverage

**Feature Branch**: `170-jira-ui-test-coverage`  
**Created**: 2026-04-13  
**Status**: Draft  
**Input**: User description: "Implement Phase 9 using test-driven development of the Jira UI plan. Treat docs/UI/CreatePage.md as the canonical desired-state design for the Create page Jira browser and import experience. Add robust validation coverage for the Jira Create-page integration across frontend runtime behavior, backend browser endpoints, Jira browser service normalization, runtime config exposure, policy denial, secret/redaction safety, and failure isolation. Frontend coverage must include Jira controls hidden when disabled, browser opening from preset and step targets, board columns loading in order, column switching updating visible issues, issue selection loading preview, replace and append import into preset instructions, replace import into a selected step only, template-bound step identity detachment after import, preset reapply-needed signaling after applied preset import, and Jira fetch failure remaining local while manual creation stays usable. Backend coverage must include router tests for Jira browser endpoints, service tests for board/column grouping, issue-detail normalization, policy-denied behavior, secret/redaction regression coverage on the browser API path, and runtime config tests for gated Jira config exposure. Existing Create page behavior and task submission contracts must remain unchanged. Selected mode: runtime. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary | Functional Requirement Mapping |
| --- | --- | --- | --- |
| DOC-REQ-001 | `docs/UI/CreatePage.md` section 14, "Jira integration: runtime config contract" | Jira entry points may appear only when runtime config explicitly enables the integration, and browser-visible Jira URLs must remain MoonMind-owned API endpoints without credentials. | FR-001, FR-002, FR-014 |
| DOC-REQ-002 | `docs/UI/CreatePage.md` section 15.1, "Column contract" | Board columns must be resolved by MoonMind from board configuration, mapped from Jira statuses server-side, and rendered by clients in board order. | FR-005, FR-010 |
| DOC-REQ-003 | `docs/UI/CreatePage.md` section 15.2, "Issue-list contract" | Issue lists must support board browsing by column, keep empty columns renderable, and avoid browser-side inference from raw issue status text. | FR-005, FR-006, FR-010 |
| DOC-REQ-004 | `docs/UI/CreatePage.md` section 15.3, "Issue-detail contract" | Issue detail must provide normalized story text and target-specific recommended import text so clients do not parse Jira rich text. | FR-007, FR-011 |
| DOC-REQ-005 | `docs/UI/CreatePage.md` sections 16-17, "Shared browser surface" and "Target model" | One shared Jira browser must open from preset or step targets, show the selected target, and avoid mutating drafts until explicit import. | FR-003, FR-004, FR-006, FR-008 |
| DOC-REQ-006 | `docs/UI/CreatePage.md` sections 18-19, "Import modes" and "Write semantics" | Jira import must support target-aware modes plus explicit replace and append actions into preset or step instruction targets. | FR-008, FR-009 |
| DOC-REQ-007 | `docs/UI/CreatePage.md` section 19.2, "Step-instructions target" | Importing into a template-bound step must detach template instruction identity when imported text differs from template instructions. | FR-009, FR-012 |
| DOC-REQ-008 | `docs/UI/CreatePage.md` section 19.1, "Preset-objective target" | Importing into preset objective text must not rewrite the step list and must mark an already-applied preset as needing reapply. | FR-008, FR-013 |
| DOC-REQ-009 | `docs/UI/CreatePage.md` sections 20 and 22, "Provenance" and "Submission invariants" | Jira import provenance is advisory, create must remain compatible with the existing task payload shape, and Jira must not introduce a separate task type or endpoint. | FR-015, FR-016 |
| DOC-REQ-010 | `docs/UI/CreatePage.md` section 21, "Failure and empty-state rules" | Jira failures and empty states must remain local to the Jira browser and must not block manual authoring or task creation. | FR-017, FR-018 |
| DOC-REQ-011 | `docs/UI/CreatePage.md` section 24, "Testing requirements" | Validation must cover disabled entry points, board ordering, column switching, issue selection, imports, template detachment, preset reapply signaling, failure isolation, and unchanged submission behavior. | FR-001, FR-003, FR-004, FR-006, FR-008, FR-009, FR-012, FR-013, FR-016, FR-018, FR-020 |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Protect Jira Rollout Boundaries (Priority: P1)

An operator can rely on the Create page exposing Jira controls only when the Create-page Jira integration is intentionally enabled, while the boot payload continues to keep Jira browser endpoints and defaults inside the MoonMind runtime configuration boundary.

**Why this priority**: Jira is optional and security-sensitive. The first independently valuable slice is proving the feature cannot accidentally appear because trusted backend tooling exists.

**Independent Test**: Generate runtime configuration with Jira disabled and enabled, then verify hidden or present Jira browser capability, MoonMind-owned endpoint locations, and configured defaults.

**Acceptance Scenarios**:

1. **Given** Jira backend tooling may be configured but the Create-page Jira integration is disabled, **When** the Create page receives runtime configuration, **Then** no Jira browser sources or Jira integration metadata are exposed.
2. **Given** the Create-page Jira integration is enabled, **When** runtime configuration is built, **Then** browser endpoints, default project, default board, and session-memory preference are present and use MoonMind-owned API paths.
3. **Given** the Create page renders without an enabled Jira integration, **When** a user edits a task manually, **Then** Jira controls are hidden and normal manual task creation remains available.

---

### User Story 2 - Validate Jira Browsing Behavior (Priority: P1)

A task author can open the shared Jira browser from either preset instructions or a step, browse a board by ordered columns, switch columns, and preview a selected issue without changing any authored text until an explicit import action occurs.

**Why this priority**: Browsing and previewing are the foundation for the Jira import workflow and must not corrupt existing task drafts.

**Independent Test**: Render the Create page with Jira enabled and sample browser responses, open the browser from both target types, navigate board columns and issues, and verify issue preview appears while draft fields remain unchanged.

**Acceptance Scenarios**:

1. **Given** Jira is enabled, **When** the user opens Jira from preset instructions, **Then** one browser surface opens and identifies preset instructions as the selected target.
2. **Given** Jira is enabled, **When** the user opens Jira from a specific step, **Then** the same browser surface opens and identifies that step as the selected target.
3. **Given** a board has multiple ordered columns and issues, **When** the browser loads the board and the user switches columns, **Then** columns remain in board order and the visible issue list changes to the selected column.
4. **Given** a user selects an issue, **When** the issue detail loads, **Then** normalized description, acceptance criteria, and import preview content are visible without changing preset or step instruction fields.

---

### User Story 3 - Validate Jira Import Semantics (Priority: P1)

A task author can explicitly import selected Jira story text into either preset instructions or exactly one selected step using replace or append behavior, while preserving objective precedence, preset reapply semantics, and template-bound step safety.

**Why this priority**: The main user value of Jira integration is copying normalized story text into existing Create page authoring surfaces without creating a parallel task model.

**Independent Test**: Select a Jira issue and exercise replace and append actions against preset and step targets, then inspect the edited fields, template identity behavior, and submitted task shape.

**Acceptance Scenarios**:

1. **Given** preset instructions are the selected target, **When** the user replaces target text from a Jira issue, **Then** only the preset instruction field changes and it becomes the preferred objective source.
2. **Given** preset instructions already contain text, **When** the user appends Jira text, **Then** the existing text remains and imported text is added after a clear separator.
3. **Given** a step is the selected target, **When** the user replaces target text, **Then** only that selected step changes.
4. **Given** a template-bound step is selected, **When** Jira import changes its instructions, **Then** the step is treated as manually customized and no longer claims matching template instruction identity.
5. **Given** a preset has already been applied, **When** Jira import changes preset instructions, **Then** existing expanded steps remain unchanged and the page signals that explicit reapply is needed.

---

### User Story 4 - Validate Trusted Backend Browser Path (Priority: P2)

An operator can trust that browser-facing Jira operations use MoonMind's server-side Jira boundary, normalize data into Create-page read models, enforce policy decisions, and sanitize failure responses before they reach the browser.

**Why this priority**: The UI must not become a credential path or policy bypass, and backend normalization is what keeps the browser simple and safe.

**Independent Test**: Exercise the browser-facing connection, project, board, column, issue-list, and issue-detail operations with allowed, denied, empty, malformed, and failure scenarios.

**Acceptance Scenarios**:

1. **Given** a configured Jira connection, **When** the browser verifies connection or requests projects, boards, columns, issues, or issue detail, **Then** each operation returns normalized Create-page-ready data.
2. **Given** a project or board request violates configured policy, **When** the browser path handles the request, **Then** the request is denied before unauthorized Jira data is exposed.
3. **Given** Jira returns board configuration and issues with mapped and unmapped statuses, **When** the browser service normalizes them, **Then** ordered columns, column issue buckets, empty buckets, and unmapped items are represented safely.
4. **Given** Jira detail contains rich text or acceptance criteria, **When** issue detail is normalized, **Then** plain description, acceptance criteria, and recommended import text are available.
5. **Given** a Jira browser failure contains secret-like or trace-like content, **When** the browser API returns an error, **Then** the response is structured and safe for the browser.

---

### User Story 5 - Keep Jira Failure Additive (Priority: P2)

A task author can continue manual task creation when Jira browser requests fail, and Jira failure state never changes the existing task submission contract.

**Why this priority**: Jira is an instruction source, not a prerequisite for creating tasks.

**Independent Test**: Simulate project, board, column, issue-list, and issue-detail failures, then verify errors remain inside the Jira browser while manual editing and task submission still work.

**Acceptance Scenarios**:

1. **Given** Jira project or board loading fails, **When** the user keeps editing manually, **Then** step and preset fields remain editable and the Create action remains available for valid manual input.
2. **Given** issue detail loading fails after an issue is selected, **When** the browser shows the error, **Then** no import actions mutate preset or step text.
3. **Given** Jira failed earlier in the session, **When** the user submits a manual task, **Then** the existing create flow and payload shape are used without Jira provenance or failure fields.

### Edge Cases

- Jira trusted tooling is enabled while Create-page Jira UI is disabled.
- Runtime configuration contains incomplete or non-MoonMind Jira browser endpoint templates.
- Configured default project or board is missing, stale, or no longer allowed.
- Jira returns no projects, no boards for a project, no columns for a board, or no issues in an active column.
- Jira issue status cannot be mapped to a known board column.
- Jira issue detail lacks description, acceptance criteria, recommended preset text, or recommended step text.
- User selects an issue preview but never presses an import action.
- User appends Jira text into an empty target field.
- User imports into a target step that was removed while the browser was open.
- User imports into preset instructions after preset application.
- Jira browser error details include credential-like, token-like, or trace-like text.
- Jira endpoint failures happen after the user has already manually authored valid task content.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Validation MUST prove Jira browser controls are hidden when Create-page Jira integration is disabled. (Maps: DOC-REQ-001, DOC-REQ-011)
- **FR-002**: Validation MUST prove runtime configuration exposes Jira browser sources and integration defaults only when the Create-page Jira integration is enabled. (Maps: DOC-REQ-001)
- **FR-003**: Validation MUST prove the Jira browser opens from the preset instruction target and displays that target context. (Maps: DOC-REQ-005, DOC-REQ-011)
- **FR-004**: Validation MUST prove the Jira browser opens from a selected step instruction target and displays that step context. (Maps: DOC-REQ-005, DOC-REQ-011)
- **FR-005**: Validation MUST prove board columns are loaded in board order and issue grouping is based on MoonMind-provided column mapping. (Maps: DOC-REQ-002, DOC-REQ-003)
- **FR-006**: Validation MUST prove switching active board columns changes the visible issue list without showing unrelated column issues. (Maps: DOC-REQ-003, DOC-REQ-005, DOC-REQ-011)
- **FR-007**: Validation MUST prove selecting an issue loads normalized preview content, including description, acceptance criteria, and recommended import text where available. (Maps: DOC-REQ-004)
- **FR-008**: Validation MUST prove selecting an issue does not mutate draft fields until an explicit replace or append action is taken. (Maps: DOC-REQ-005, DOC-REQ-006, DOC-REQ-008, DOC-REQ-011)
- **FR-009**: Validation MUST prove replace and append imports update only the selected preset or step target using the selected import mode. (Maps: DOC-REQ-006, DOC-REQ-007, DOC-REQ-011)
- **FR-010**: Validation MUST prove service-level board and issue normalization preserves ordered columns, empty columns, mapped issue buckets, and safe handling for unmapped statuses. (Maps: DOC-REQ-002, DOC-REQ-003)
- **FR-011**: Validation MUST prove issue-detail normalization returns plain text and target-specific recommended import text without requiring browser-side rich-text parsing. (Maps: DOC-REQ-004)
- **FR-012**: Validation MUST prove Jira import into a template-bound step detaches template instruction identity when imported instructions diverge. (Maps: DOC-REQ-007, DOC-REQ-011)
- **FR-013**: Validation MUST prove Jira import into preset instructions after preset application surfaces reapply-needed signaling and does not silently rewrite expanded steps. (Maps: DOC-REQ-008, DOC-REQ-011)
- **FR-014**: Validation MUST prove browser-facing Jira endpoint templates remain MoonMind-owned paths and do not expose browser credential paths. (Maps: DOC-REQ-001)
- **FR-015**: Validation MUST prove Jira provenance remains advisory and is not required for task creation. (Maps: DOC-REQ-009)
- **FR-016**: Validation MUST prove task submission still uses the existing task creation contract and excludes Jira-specific task types, create endpoints, and required payload fields. (Maps: DOC-REQ-009, DOC-REQ-011)
- **FR-017**: Validation MUST prove backend Jira browser failures are structured and sanitized so raw credentials, secret-like values, and traces do not reach browser-facing responses. (Maps: DOC-REQ-010)
- **FR-018**: Validation MUST prove frontend Jira failures remain local to the browser and do not block manual step editing, preset editing, or valid manual task creation. (Maps: DOC-REQ-010, DOC-REQ-011)
- **FR-019**: Runtime deliverables MUST include production runtime code changes where coverage reveals behavior gaps; docs-only or spec-only changes are insufficient for this feature.
- **FR-020**: Runtime deliverables MUST include validation tests covering frontend Create-page behavior, backend Jira browser endpoints, service normalization, runtime configuration, policy denial, secret/redaction safety, failure isolation, and unchanged submission behavior. (Maps: DOC-REQ-011)

### Key Entities *(include if feature involves data)*

- **Jira Runtime Capability**: The runtime-provided Create-page metadata that controls whether Jira browser controls appear and where browser operations are reached.
- **Jira Browser Target**: The currently selected import destination, either preset instructions or one specific step instruction field.
- **Jira Board Browser Model**: The normalized project, board, column, issue-list, and issue-detail data consumed by the Create page.
- **Jira Import Action**: An explicit copy operation that writes selected Jira text into the chosen target using replace or append semantics.
- **Template-Bound Step Identity**: The marker that a preset-expanded step still matches its template instructions and must be removed when import customizes the step.
- **Jira Failure Response**: A browser-facing error response with stable safe details and no secret-like or provider trace content.
- **Manual Task Draft**: Existing Create page state for preset instructions, steps, runtime controls, dependencies, scheduling, and submission that must remain usable with Jira disabled or failing.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Runtime configuration validation covers both Jira disabled and enabled states, including endpoint ownership and default project or board values.
- **SC-002**: Frontend validation covers all required Jira browser user journeys: hidden disabled controls, preset open, step open, ordered columns, column switching, issue preview, explicit import, preset reapply signaling, template detachment, and local failure handling.
- **SC-003**: Backend validation covers all browser endpoint categories: connection verification, projects, boards, columns, board issues, and issue detail.
- **SC-004**: Service validation covers board column ordering, status-to-column issue grouping, empty renderable states, unmapped issue handling, issue-detail text normalization, policy denial, and safe request validation.
- **SC-005**: Secret and redaction regression validation confirms browser-facing Jira failures never include raw credentials, secret-like tokens, authorization material, private-key text, or stack traces.
- **SC-006**: Manual task creation remains demonstrably usable after Jira browser failures, and submitted tasks retain the existing create endpoint and payload shape.
- **SC-007**: The final implementation includes runtime code or test changes plus passing relevant frontend and backend validation suites.

### Assumptions

- Jira remains an optional instruction source for Create page authoring, not a separate task type or execution substrate.
- The Create page continues to consume server-provided runtime configuration and MoonMind-owned browser APIs.
- Tests may use representative Jira data and policy outcomes rather than live Jira credentials.
- Provider verification against live Jira credentials is outside this phase unless separately requested.
