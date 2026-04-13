# Feature Specification: Jira Failure Handling

**Feature Branch**: `169-jira-failure-handling`  
**Created**: 2026-04-13  
**Status**: Draft  
**Input**: User description: "Implement Phase 8 using test-driven development of the Jira UI plan. Treat docs/UI/CreatePage.md as the canonical desired-state design for the Create page Jira browser failure handling. Goal: keep Jira additive, never blocking manual task creation. Backend scope: normalize Jira browser failures into structured MoonMind errors and return safe empty states where appropriate. Frontend scope: if Jira browser endpoints fail, show local inline errors inside the Jira browser panel only; do not disable manual step editing or preset editing; do not let Jira failures affect the Create button unless the user is actively depending on an in-flight Jira import action. Acceptance: the Create page remains fully usable with Jira unavailable, and Jira loading failure does not block manual task creation. Selected mode: runtime. Default to runtime mode and only switch to docs mode when explicitly requested. Preserve all user-provided constraints. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary | Functional Requirement Mapping |
| --- | --- | --- | --- |
| DOC-REQ-001 | `docs/UI/CreatePage.md` section 21, lines 608-616 | Jira must remain additive; disabled or failing Jira must not corrupt the draft, must not block manual authoring, and issue-detail failures must leave draft content untouched. | FR-001, FR-003, FR-004, FR-005, FR-007, FR-008 |
| DOC-REQ-002 | `docs/UI/CreatePage.md` section 21, lines 615-623 | Empty and failed Jira browser states must be rendered explicitly with operator-facing copy that makes manual continuation clear. | FR-002, FR-005, FR-006 |
| DOC-REQ-003 | `docs/UI/CreatePage.md` section 22, lines 629-637 | Create submission must continue through the existing task flow; Jira must only affect authored field content and must not introduce a separate task type or create endpoint. | FR-007, FR-008, FR-009 |
| DOC-REQ-004 | `docs/UI/CreatePage.md` section 24, lines 663-675 | Validation must cover Jira API failures, hidden disabled entry points, unchanged submission behavior, and unchanged objective resolution expectations. | FR-010, FR-011 |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Contain Jira Backend Failures (Priority: P1)

An operator using the Create page receives safe, structured Jira browser failures from MoonMind instead of raw provider errors, traces, or credential-bearing messages.

**Why this priority**: The browser must stay inside MoonMind's trusted boundary, and failures from Jira or the browser service must be understandable without leaking sensitive details.

**Independent Test**: Exercise each Jira browser operation with policy, provider, validation, and unexpected service failures; verify each failure returns a structured MoonMind error and no raw credentials or provider traces.

**Acceptance Scenarios**:

1. **Given** a Jira browser request fails because of a known Jira or policy error, **When** the Create page calls the MoonMind Jira browser endpoint, **Then** the response contains a structured error code, safe message, Jira-browser source, and relevant action context.
2. **Given** a Jira browser request fails unexpectedly inside MoonMind, **When** the endpoint responds, **Then** the response is still structured and uses a safe generic message.
3. **Given** Jira returns no projects, boards, columns, or issues, **When** the browser operation succeeds with no content, **Then** the response supports an explicit empty state rather than a generic failure.

---

### User Story 2 - Keep Jira UI Failures Local (Priority: P1)

A task author can open the Jira browser and see endpoint failures inside that browser surface only, while the rest of the Create page draft remains editable.

**Why this priority**: Jira is an optional instruction source. A Jira outage must not degrade manual task authoring or preset editing.

**Independent Test**: Simulate failed Jira project, board, column, issue-list, and issue-detail loads, then verify each error appears in the Jira browser while step and preset fields remain editable.

**Acceptance Scenarios**:

1. **Given** Jira project loading fails, **When** the user opens the Jira browser, **Then** the browser shows a local inline error with manual-continuation guidance.
2. **Given** Jira board, column, or issue-list loading fails after a project or board selection, **When** the browser remains open, **Then** the failure appears only in the browser panel and does not alter authored step or preset text.
3. **Given** Jira issue-detail loading fails, **When** the user has selected an issue summary, **Then** no import occurs and the current draft remains unchanged.

---

### User Story 3 - Preserve Manual Creation and Submission (Priority: P1)

A task author can close or ignore a failed Jira browser and create a manual task through the existing Create page flow.

**Why this priority**: The Create page's primary value is task creation; Jira failures must not affect the Create button or submission contract unless the user is actively waiting on an import action.

**Independent Test**: Cause a Jira browser request to fail, manually enter valid instructions, submit the task, and verify submission uses the existing task path and payload shape.

**Acceptance Scenarios**:

1. **Given** Jira is unavailable, **When** the user writes manual step instructions, **Then** manual validation and editing continue normally.
2. **Given** Jira loading failed earlier in the same page session, **When** the user submits a valid task, **Then** the Create button remains available and the task uses the existing submission path.
3. **Given** a user starts a Jira import action that depends on selected issue content, **When** that import is actively pending or impossible because content failed to load, **Then** only the import action is blocked; general task creation remains available.

### Edge Cases

- Jira UI rollout is disabled while trusted Jira backend tooling remains enabled.
- Jira credentials are missing, expired, denied, or produce provider errors that include sensitive-looking text.
- Jira browser service raises an unexpected exception outside the normal Jira error model.
- Jira project, board, column, issue-list, or issue-detail endpoints fail independently.
- Jira endpoints return empty project, board, column, or issue lists.
- A selected issue detail fails after issue summaries loaded successfully.
- A user edits preset or step instructions after Jira failure and then submits a task.
- A user has already imported Jira text before a later Jira browser request fails.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST normalize known Jira browser backend failures into structured MoonMind error responses with a stable code and safe operator-facing message. (Maps: DOC-REQ-001)
- **FR-002**: System MUST prevent raw credentials, secret-like values, provider traces, and unsanitized backend exception messages from appearing in browser-facing Jira failure responses. (Maps: DOC-REQ-002)
- **FR-003**: System MUST normalize unexpected Jira browser backend failures into structured safe errors rather than unhandled raw failures. (Maps: DOC-REQ-001)
- **FR-004**: System MUST preserve safe empty-state responses or empty renderable models when Jira browser operations succeed but return no projects, boards, columns, or issues. (Maps: DOC-REQ-001)
- **FR-005**: System MUST show Jira browser endpoint failures as inline messages inside the Jira browser panel only. (Maps: DOC-REQ-001, DOC-REQ-002)
- **FR-006**: Inline Jira failure messages MUST tell the user they can continue creating the task manually. (Maps: DOC-REQ-002)
- **FR-007**: Jira browser failures MUST NOT disable manual step editing, preset objective editing, preset selection, dependency selection, runtime controls, scheduling controls, or the Create button. (Maps: DOC-REQ-001, DOC-REQ-003)
- **FR-008**: Jira issue-detail failures MUST NOT import text, mutate preset instructions, mutate step instructions, change objective resolution, or alter already-authored draft content. (Maps: DOC-REQ-001, DOC-REQ-003)
- **FR-009**: System MUST keep the task submission contract unchanged when Jira is unavailable, including the existing create endpoint and existing objective resolution order. (Maps: DOC-REQ-003)
- **FR-010**: Required deliverables MUST include production runtime code changes, not documentation-only or specification-only changes. (Maps: DOC-REQ-004)
- **FR-011**: Required deliverables MUST include validation tests covering structured backend errors, secret-safe failure responses, local frontend Jira errors, empty states where applicable, and manual task creation after Jira failure. (Maps: DOC-REQ-004)

### Key Entities *(include if feature involves data)*

- **Jira Browser Failure**: A failed Jira browser operation represented by a stable error code, safe message, source, and optional action context.
- **Jira Empty State**: A successful Jira browser response that contains no selectable projects, boards, columns, issues, or issue detail content and can be rendered explicitly.
- **Manual Task Draft**: The Create page draft fields for authored steps, preset instructions, execution settings, dependencies, schedule, and submission state that must remain usable when Jira fails.
- **In-Flight Jira Import**: The short-lived user action of copying selected Jira issue content into a target field; only this action may be blocked by missing or failed Jira issue content.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In validation tests, 100% of known Jira browser backend failures return structured MoonMind error details with no raw credential or secret-like content.
- **SC-002**: In validation tests, unexpected Jira browser backend exceptions return a safe structured failure response rather than an unhandled raw exception.
- **SC-003**: In validation tests, Jira project, board, column, issue-list, and issue-detail failures are displayed inside the Jira browser panel and nowhere else on the Create page.
- **SC-004**: In validation tests, after a Jira loading failure, a user can manually edit instructions and submit a task through the existing Create page flow.
- **SC-005**: In validation tests, failed issue-detail loading does not change preset instructions, step instructions, objective resolution, or submission payload shape.
- **SC-006**: Existing Create page behavior with Jira disabled remains unchanged, including hidden Jira controls and normal manual task creation.

### Assumptions

- Jira browser runtime configuration, browser endpoints, issue preview, and import actions already exist from earlier Jira Create-page phases.
- Jira remains an optional instruction source, not a separate task source, create endpoint, or task type.
- Operator-facing failure text should be concise and local to the browser panel, with more detailed diagnostics retained in logs.
- Runtime mode is required for this feature; documentation updates alone do not satisfy the request.
