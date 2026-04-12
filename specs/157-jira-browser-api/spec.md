# Feature Specification: Jira Browser API

**Feature Branch**: `157-jira-browser-api`
**Created**: 2026-04-12
**Status**: Draft
**Input**: User description: "Implement Phase 2 using test-driven development of the Jira UI plan: build the server-side Jira browser API for the MoonMind Create page. Treat docs/UI/CreatePage.md as the canonical desired-state contract. Add MoonMind-owned browser endpoints for connection verification, project listing, project boards, board columns, board issues, and issue detail. Reuse the existing trusted Jira auth, low-level client, redaction, retry, timeout, and project-policy boundary so browser clients never receive or use raw Jira credentials. Normalize Jira board configuration into stable ordered columns, group board issues by column using status mappings, return normalized issue summaries, and return issue detail with descriptionText, acceptanceCriteriaText, recommendedImports.presetInstructions, and recommendedImports.stepInstructions. Keep Jira additive and non-blocking for manual task creation. Runtime mode. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary | Functional Requirement Mapping |
| --- | --- | --- | --- |
| DOC-REQ-001 | `docs/UI/CreatePage.md` §13 | Jira is an external instruction source for Create-page task composition and must not replace task creation, the step editor, presets, or the MoonMind task submission model. | FR-001, FR-014 |
| DOC-REQ-002 | `docs/UI/CreatePage.md` §13 | Users need to browse Jira boards by column and inspect stories before importing story text into existing Create-page fields. | FR-002, FR-005, FR-006, FR-008 |
| DOC-REQ-003 | `docs/UI/CreatePage.md` §14 | Jira Create-page exposure must be explicit in runtime configuration, and browser clients must only use MoonMind-owned operations. | FR-001, FR-003, FR-004 |
| DOC-REQ-004 | `docs/UI/CreatePage.md` §15.1 | MoonMind must resolve Jira board columns from board configuration, translate status-to-column mappings server-side, and preserve board column order. | FR-006, FR-007 |
| DOC-REQ-005 | `docs/UI/CreatePage.md` §15.2 | The board issue browser must group issues by normalized column, avoid browser-side status inference, support optional issue filtering, and keep empty columns renderable. | FR-008, FR-009, FR-010 |
| DOC-REQ-006 | `docs/UI/CreatePage.md` §15.3 | Issue detail responses must provide normalized description text, acceptance criteria text, and target-specific recommended import text so the browser does not parse Jira rich text. | FR-011, FR-012 |
| DOC-REQ-007 | `docs/UI/CreatePage.md` §21-§22 | Jira failures must stay local and additive, and manual task creation must remain usable when Jira is unavailable. | FR-013, FR-014 |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Verify Trusted Jira Availability (Priority: P1)

A task author or operator can verify whether the configured Jira connection is usable for the Create-page browser without exposing Jira credentials to the browser.

**Why this priority**: Browser access is only safe if it goes through MoonMind's trusted Jira boundary. Verification is the first independent proof that the UI can discover Jira availability without creating a direct browser-to-Jira credential path.

**Independent Test**: Configure a trusted Jira binding, call the browser verification operation from the MoonMind API surface, and confirm the response contains only safe connection metadata and no raw credential material.

**Acceptance Scenarios**:

1. **Given** trusted Jira configuration is present and allowed, **When** the browser verification operation is requested, **Then** the system reports that Jira is usable and returns only safe display metadata.
2. **Given** Jira configuration is missing, expired, denied, or disabled for the Create-page browser, **When** verification is requested, **Then** the system returns a structured safe failure and does not expose credential values or raw provider response bodies.

---

### User Story 2 - Browse Projects, Boards, And Columns (Priority: P1)

A task author can browse allowed Jira projects, select a project board, and see the board's columns in the order defined by Jira.

**Why this priority**: The Create page needs board browsing before it can present stories by workflow column, and project policy must be enforced before any project data is shown.

**Independent Test**: With a configured Jira connection and project allowlist, request projects, boards for an allowed project, and columns for a board; verify denied projects are rejected before data is returned and allowed columns keep Jira board order.

**Acceptance Scenarios**:

1. **Given** one or more Jira projects are allowed by policy, **When** the Create-page browser requests projects, **Then** only allowed or visible projects are returned in normalized form.
2. **Given** a user requests boards for an allowed project, **When** boards are available, **Then** the system returns normalized board identifiers, names, project keys, and safe display metadata.
3. **Given** a user requests boards for a denied project, **When** policy does not allow that project, **Then** the system rejects the request before returning Jira project data.
4. **Given** a board has ordered Jira columns and status mappings, **When** the Create-page browser requests columns, **Then** the system returns stable column objects in board order with their server-resolved status mappings.

---

### User Story 3 - Browse Issues By Board Column (Priority: P1)

A task author can view Jira issues grouped into the correct board columns, including empty columns and issues whose statuses cannot be mapped safely.

**Why this priority**: The UI plan depends on column-based browsing, and grouping must be resolved server-side so the browser does not infer Jira workflow semantics from status names.

**Independent Test**: Provide a board configuration with multiple columns and Jira issues in mapped and unmapped statuses; request board issues and verify items are grouped by normalized column with an explicit unmapped bucket.

**Acceptance Scenarios**:

1. **Given** a board contains issues with statuses mapped to board columns, **When** the browser requests board issues, **Then** each issue appears under the matching normalized column.
2. **Given** a board column has no matching issues, **When** board issues are returned, **Then** the empty column remains present and renderable.
3. **Given** an issue has a status not mapped by the board configuration, **When** board issues are returned, **Then** the issue is placed in a safe unmapped group instead of being guessed into a visible column.
4. **Given** a user filters by issue key or summary, **When** a filter is supplied, **Then** matching issues are returned while column structure remains stable.

---

### User Story 4 - Preview Normalized Issue Detail (Priority: P1)

A task author can select a Jira issue and preview normalized story content that is ready to import into either preset instructions or step instructions.

**Why this priority**: The backend must supply Create-page-ready text so the frontend remains simple and avoids parsing Jira rich-text formats or applying inconsistent import recommendations.

**Independent Test**: Request issue detail for an allowed issue with rich-text description and acceptance criteria; verify normalized text fields and recommended import strings are present without raw Jira rich-text payloads.

**Acceptance Scenarios**:

1. **Given** an allowed Jira issue has description content, **When** issue detail is requested, **Then** the system returns normalized plain description text.
2. **Given** an allowed Jira issue has acceptance criteria content, **When** issue detail is requested, **Then** the system returns normalized plain acceptance criteria text.
3. **Given** issue detail is returned, **When** the browser previews import options, **Then** it can use server-provided preset-instruction and step-instruction recommendations.
4. **Given** the issue lacks description or acceptance criteria, **When** issue detail is requested, **Then** the system returns safe empty text fields and still provides usable recommendation strings.

### Edge Cases

- Jira Create-page browser rollout is disabled while trusted backend Jira tooling is enabled.
- Jira configuration is missing, expired, denied, or otherwise unavailable.
- The configured project allowlist is empty, contains one project, or contains multiple projects.
- A request targets a project that is denied by policy.
- A project has no boards or a board has no configured columns.
- A board has empty columns.
- A board issue has a status that is not mapped to any board column.
- Issue list filtering matches no issues.
- Issue detail lacks description, acceptance criteria, issue type, status, assignee, or browser URL metadata.
- Jira returns rich-text content that includes nested paragraphs, headings, lists, or hard breaks.
- Jira provider failures include sensitive text; browser responses must stay structured and sanitized.

### Assumptions

- The Create-page Jira browser remains an optional instruction-source capability, not a replacement task type.
- Existing trusted Jira configuration and project policy remain the required security boundary for all browser data.
- The browser consumes normalized MoonMind responses and does not need direct Jira-domain credentials or rich-text parsing.
- Jira import into preset or step fields is handled by later Create-page UI work; this feature only supplies the trusted browser read model.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST deliver this feature as runtime behavior with production code changes and validation tests, not as docs/spec-only work. (DOC-REQ-001)
- **FR-002**: System MUST provide MoonMind-owned Jira browser operations for connection verification, project listing, project board listing, board column listing, board issue listing, and issue detail lookup. (DOC-REQ-002)
- **FR-003**: System MUST ensure browser clients interact only with MoonMind-owned Jira browser operations and never receive raw Jira credentials. (DOC-REQ-003)
- **FR-004**: System MUST keep Create-page Jira browser exposure controlled separately from trusted backend Jira tool enablement. (DOC-REQ-003)
- **FR-005**: System MUST verify the configured trusted Jira connection before presenting Jira browser data as available. (DOC-REQ-002)
- **FR-006**: System MUST enforce configured Jira project allowlists and policy decisions for every project-scoped browser operation. (DOC-REQ-004)
- **FR-007**: System MUST normalize Jira board configuration into stable column records that preserve Jira board order and include status-to-column mapping metadata. (DOC-REQ-004)
- **FR-008**: System MUST group board issues into normalized columns using server-resolved status mappings rather than browser-side status-name inference. (DOC-REQ-002, DOC-REQ-005)
- **FR-009**: System MUST return empty board columns in issue-list responses so the browser can render them. (DOC-REQ-005)
- **FR-010**: System MUST provide a safe representation for issues whose statuses cannot be mapped to a board column. (DOC-REQ-005)
- **FR-011**: System MUST return normalized issue summaries suitable for list display without exposing unnecessary raw Jira response data. (DOC-REQ-006)
- **FR-012**: System MUST return normalized issue detail with description text, acceptance criteria text, recommended preset-instruction text, and recommended step-instruction text. (DOC-REQ-006)
- **FR-013**: System MUST normalize Jira browser failures into structured safe errors that do not expose credential material or raw provider response bodies. (DOC-REQ-007)
- **FR-014**: System MUST keep manual Create-page task authoring and task creation usable when Jira browser operations fail or return empty states. (DOC-REQ-001, DOC-REQ-007)
- **FR-015**: System MUST include validation tests covering connection verification, project allowlist behavior, board and column normalization, issue grouping, issue detail normalization, policy denial, safe error mapping, and credential redaction regression.

### Key Entities *(include if feature involves data)*

- **Jira Connection Verification**: A safe result indicating whether the trusted Jira binding can serve Create-page browser reads.
- **Jira Project**: A normalized Jira project visible through the configured trust and policy boundary.
- **Jira Board**: A normalized board associated with an allowed project and used as the browsing context for columns and issues.
- **Jira Column**: A stable board-column record with display name, order, issue count, and status mapping metadata.
- **Jira Issue Summary**: A list-row representation of a Jira issue with key, summary, status, assignee display label, update timestamp, and resolved column.
- **Jira Issue Detail**: A preview representation with normalized story text and recommended import text for Create-page authoring targets.
- **Jira Browser Error**: A structured safe failure result that communicates the problem without leaking credentials or raw provider payloads.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A configured and allowed Jira connection can be verified through a MoonMind-owned browser operation without exposing raw Jira credentials.
- **SC-002**: For an allowed project, users can retrieve normalized project boards and board columns in the same order Jira defines them.
- **SC-003**: For a board with mapped, unmapped, and empty-column states, issue browsing returns correct grouped items, an explicit unmapped group, and all empty columns.
- **SC-004**: For an issue with rich-text story content, issue detail returns normalized description text, acceptance criteria text, and both recommended import strings.
- **SC-005**: Denied projects, disabled Jira browser rollout, missing configuration, and provider failures all return structured safe errors without credential material.
- **SC-006**: Automated tests cover all new browser read models, policy boundaries, router responses, normalization behavior, and failure handling required by this specification.
