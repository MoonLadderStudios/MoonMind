# Feature Specification: Create Page Repository Dropdown

**Feature Branch**: `204-repository-dropdown`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**:

```text
Use the Jira preset brief for MM-393 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

**Canonical Jira Brief**: `spec.md` (Input)

## Original Jira Preset Brief

Jira issue: MM-393 from MM project
Summary: We should retrieve a list of git repos that you've added + ones that are available using your credentials if we can detect that
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-393 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-393: We should retrieve a list of git repos that you've added + ones that are available using your credentials if we can detect that

We should retrieve a list of git repos that you've added + ones that are available using your credentials if we can detect that and show it as a dropdown in the Create Page UI

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## Classification

- Input class: single-story feature request.
- Mode: runtime.
- Source design treatment: `docs/UI/CreatePage.md` defines the existing Create Page runtime contract and is used as supporting source requirements, not as a docs-only target.
- Resume decision: no existing `MM-393` spec artifacts were present, so the workflow starts at Specify.

## User Story - Create Page Repository Dropdown

**Summary**: As a task author, I want the Create page repository field to offer known and credential-visible repositories as selectable options so that I can target a run without manually typing an owner/repo value.

**Goal**: Task authors can choose a repository from a dropdown populated by MoonMind configuration and any detectable GitHub repositories available through configured credentials, while still retaining manual entry when repository discovery is unavailable.

**Independent Test**: Can be fully tested by opening `/tasks/new` with configured repository options and a mocked repository-discovery response, selecting a repository from the dropdown, submitting a task, and verifying the submitted payload uses the selected owner/repo value while manual repository entry still works when discovery fails.

**Acceptance Scenarios**:

1. **Given** MoonMind has a default repository or configured repository list, **When** the Create page renders, **Then** the repository field offers those repositories as selectable dropdown options.
2. **Given** MoonMind can detect additional repositories through configured GitHub credentials, **When** the Create page repository options load, **Then** those repositories are available in the same dropdown without exposing credential material to the browser.
3. **Given** a task author chooses a repository option, **When** they submit the task, **Then** the selected repository is sent as the task repository value.
4. **Given** repository discovery is unavailable or returns no options, **When** the Create page renders, **Then** manual repository entry remains available and existing repository validation still applies.
5. **Given** repository options include duplicates or invalid repository strings, **When** options are prepared for the browser, **Then** duplicates are removed and invalid values are not offered as selectable options.

### Edge Cases

- GitHub integration is disabled.
- No GitHub credentials are configured.
- GitHub credentials are configured but repository listing fails or times out.
- Configured repository values include whitespace, duplicates, unsupported URL formats, or invalid owner/repo strings.
- The configured default repository is not present in the discovered repository list.
- The task author types a valid repository that is not present in the dropdown.

## Assumptions

- The Create page repository value remains an editable text field with dropdown suggestions rather than a closed select, because users may need to enter repositories that are not detectable.
- Configured repositories include the workflow default repository and comma-delimited `GITHUB_REPOS` values when present.
- Credential-visible repository discovery is best-effort; failures must not block task authoring or submit.
- Browser clients receive only repository names and metadata needed for display, never GitHub tokens or secret refs.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: `docs/UI/CreatePage.md`, sections 3-6): The Create page MUST remain a MoonMind-native task authoring surface whose browser actions go through MoonMind REST APIs and whose execution context includes the repository field. Scope: in scope. Maps to FR-001, FR-002, FR-006, FR-009.
- **DESIGN-REQ-002** (Source: `docs/UI/CreatePage.md`, sections 5 and 6): Runtime, provider, model, effort, repository, branches, and publish mode are part of the execution context draft model. Scope: in scope. Maps to FR-003, FR-007.
- **DESIGN-REQ-003** (Source: `docs/UI/CreatePage.md`, sections 16-17): Optional integration failures MUST leave manual authoring available and validation feedback associated with the affected target. Scope: in scope. Maps to FR-004, FR-008.
- **DESIGN-REQ-004** (Source: MM-393 Jira brief): The Create page repository control SHOULD retrieve repositories the user has added plus repositories available through credentials when detectable and show them as dropdown options. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-005, FR-006.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose repository options to the Create page using MoonMind-owned API/configuration surfaces.
- **FR-002**: System MUST include configured repositories in repository options, including the workflow default repository and configured repository list values when present.
- **FR-003**: System MUST attempt to include credential-visible GitHub repositories when GitHub integration and resolvable credentials are available.
- **FR-004**: Repository option discovery failures MUST NOT prevent the Create page from rendering or block manual repository entry.
- **FR-005**: Repository options MUST be normalized to selectable owner/repo values and MUST exclude invalid or credential-bearing values.
- **FR-006**: Repository option data exposed to the browser MUST NOT include raw tokens, secret refs, authorization headers, cookies, or credential-bearing clone URLs.
- **FR-007**: The Create page repository field MUST render repository options as dropdown suggestions while preserving free-form manual entry.
- **FR-008**: Existing repository validation MUST continue to reject missing or invalid repository values at submit time.
- **FR-009**: Selecting a repository option MUST update the submitted task repository value without changing unrelated draft fields.
- **FR-010**: System MUST preserve Jira issue key MM-393 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Key Entities

- **Repository Option**: A selectable repository candidate with an owner/repo value, display label, and source classification such as configured default, configured list, or GitHub credentials.
- **Repository Discovery Result**: The best-effort set of repository options returned to the Create page, including any non-secret warning state needed for graceful degradation.
- **Create Page Draft**: Browser state containing the selected or manually entered repository and the rest of the task execution context.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Backend unit coverage verifies configured default and configured-list repositories appear in normalized repository options.
- **SC-002**: Backend unit coverage verifies credential-visible GitHub repositories are included when the GitHub API returns accessible repositories.
- **SC-003**: Backend unit coverage verifies invalid, duplicate, and credential-bearing repository strings are excluded from repository options.
- **SC-004**: Frontend tests verify the repository field exposes dropdown suggestions while allowing manual owner/repo entry.
- **SC-005**: Frontend tests verify selecting a repository option submits that repository value.
- **SC-006**: Frontend or backend tests verify repository discovery failure does not block Create page rendering or manual repository submission.
- **SC-007**: Verification evidence preserves MM-393 as the source Jira issue for the feature.
