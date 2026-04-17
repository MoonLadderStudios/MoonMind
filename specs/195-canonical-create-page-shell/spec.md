# Feature Specification: Canonical Create Page Shell

**Feature Branch**: `195-canonical-create-page-shell`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**:

```text
# MM-376 MoonSpec Orchestration Input

## Source

- Jira issue: MM-376
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Canonical Create Page Shell
- Labels: `moonmind-workflow-mm-5818081f-60f0-45dd-ad16-3f7753de93ae`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-376 from MM project
Summary: Canonical Create Page Shell
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-376 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-376: Canonical Create Page Shell

Short Name
canonical-create-page-shell

User Story
As a task author, I can open the canonical Create page and use one MoonMind-native task composition form whose route, hosting, section order, and API boundaries are consistent across create, edit, and rerun entry points.

Acceptance Criteria
- Given I navigate to `/tasks/new`, then the server-hosted Mission Control UI renders the Create page from the server-provided runtime boot payload.
- Given a compatibility route exists, when I visit it, then it redirects to `/tasks/new` and does not define separate product behavior.
- Given the page renders, then the form sections appear in this order: Header, Steps, Task Presets, Dependencies, Execution context, Execution controls, Schedule, Submit.
- Given any page action occurs, then the browser calls MoonMind REST APIs rather than Jira, object storage, or model providers directly.
- Given optional presets, Jira, or image upload are unavailable, then manual task authoring remains available.

Requirements
- Expose `/tasks/new` as the canonical Create page route.
- Render task creation, edit, and rerun modes through the same task-first composition surface.
- Build runtime configuration server-side and pass it through the boot payload.
- Keep artifact, Jira, provider, and object-storage interactions behind MoonMind API surfaces.
- Preserve the canonical section order and task-first product stance.
- Redirect compatibility aliases to `/tasks/new` without defining separate product behavior.
- Preserve manual task authoring when optional presets, Jira import, or image upload are unavailable.

Independent Test
Create page coverage verifies that `/tasks/new` renders through the server-hosted Mission Control UI with server-provided runtime boot payload, compatibility aliases redirect to `/tasks/new`, the canonical section order is stable, page actions call MoonMind REST APIs only, and manual task authoring remains available when optional integrations are unavailable.

Source Document
- `docs/UI/CreatePage.md`

Source Sections
- 1. Purpose
- 3. Product stance
- 4. Route and hosting model
- 5. Canonical page model
- 19. Summary

Coverage IDs
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-004

Relevant Implementation Notes
- The Create page is the single task-authoring surface for composing manual steps, applying task presets, importing Jira text and allowed images into declared draft targets, selecting dependencies, configuring execution options, and creating, editing, or rerunning task-shaped Temporal executions.
- The Create page is a MoonMind-native task authoring surface, not a generic workflow builder, Jira-native surface, image editor, or binary transport layer.
- Browser clients must call only MoonMind APIs; they must not call Jira, object storage, or model providers directly.
- The canonical route is `/tasks/new`; compatibility aliases may exist only as redirects and must not create separate behavior.
- The page is server-hosted by FastAPI and rendered by the Mission Control React/Vite UI.
- Runtime configuration is generated server-side and passed through the boot payload.
- Representative implementation surfaces are `frontend/src/entrypoints/task-create.tsx` and `api_service/api/routers/task_dashboard_view_model.py`.
- The canonical page model is a single composition form ordered as Header, Steps, Task Presets, Dependencies, Execution context, Execution controls, Schedule, Submit.

Out of Scope
- Turning the Create page into a generic workflow builder.
- Direct browser integrations with Jira, object storage, or model providers.
- Changing task preset semantics beyond preserving the canonical task-first shell.
- Changing image attachment behavior beyond preserving explicit structured input targets.

Verification
- Run focused frontend tests for the Create page entrypoint and routing behavior.
- Verify server-side runtime boot payload generation for `/tasks/new`.
- Verify compatibility route redirects to `/tasks/new`.
- Verify Create page actions use MoonMind REST APIs only.
- Run `./tools/test_unit.sh` before completion when implementation changes are made.

Needs Clarification
- None
```

**Implementation Intent**: Runtime implementation. Required deliverables include product behavior checks, production shell structure where missing, and validation tests.

## User Story - Canonical Create Page Shell

**Summary**: As a task author, I want `/tasks/new` to render one MoonMind-native task composition form so that create, edit, and rerun entry points use the same route, hosting model, section order, and MoonMind API boundaries.

**Goal**: Task authors can rely on `/tasks/new` as the canonical Create page with a stable task-first shell, server-provided runtime configuration, redirect-only compatibility aliases, and manual authoring that continues to work when optional integrations are unavailable.

**Independent Test**: Render and submit the Create page with optional integrations disabled and enabled. The story passes when `/tasks/new` receives the server boot payload, compatibility aliases redirect to `/tasks/new`, the canonical section order is exposed as Header, Steps, Task Presets, Dependencies, Execution context, Execution controls, Schedule, Submit, submission uses MoonMind REST endpoints only, and manual task authoring remains available without presets, Jira, or image upload.

**Acceptance Scenarios**:

1. **Given** a task author navigates to `/tasks/new`, **when** the server responds, **then** the Mission Control React shell is rendered with a boot payload that contains Create page runtime configuration.
2. **Given** a compatibility create route exists, **when** a task author visits it, **then** it redirects to `/tasks/new` and does not render separate Create page behavior.
3. **Given** the Create page renders, **when** the form shell is inspected, **then** its canonical sections are exposed in this order: Header, Steps, Task Presets, Dependencies, Execution context, Execution controls, Schedule, Submit.
4. **Given** a task author submits a manually authored task, **when** the browser performs network requests, **then** task creation, artifact, Jira, provider profile, and template interactions use MoonMind REST endpoints rather than direct Jira, object-storage, or model-provider calls.
5. **Given** optional presets, Jira integration, and image upload are unavailable, **when** the Create page renders, **then** manual task authoring and task submission remain available.
6. **Given** edit or rerun mode opens through `/tasks/new`, **when** the draft loads, **then** it uses the same Create page composition surface rather than a separate edit or rerun page.

### Edge Cases

- Runtime configuration omits optional template catalog settings.
- Runtime configuration omits Jira integration settings.
- Runtime configuration disables attachment policy or omits artifact upload affordances.
- Compatibility aliases include a trailing slash or malformed path.
- The page is opened in edit or rerun mode with query parameters.

## Assumptions

- The existing FastAPI task dashboard route remains the server-hosted entry point for Mission Control pages.
- Existing Create page controls may be grouped into explicit canonical shell sections without changing their task submission semantics.
- Browser provider-profile and template catalog fetches are MoonMind REST calls and are allowed page actions.

## Source Design Requirements

- **DESIGN-REQ-001**: Source section 1 requires the Create page to be the single task-authoring surface for manual steps, attachments, presets, Jira imports, dependencies, execution configuration, and create/edit/rerun of task-shaped Temporal executions. Scope: in scope. Maps to FR-001, FR-004, FR-005, FR-006, and FR-009.
- **DESIGN-REQ-002**: Source section 3 requires the Create page to remain a MoonMind-native task authoring surface, with browser clients calling MoonMind APIs and manual authoring remaining first-class when optional integrations are unavailable. Scope: in scope. Maps to FR-006, FR-007, FR-008, and FR-009.
- **DESIGN-REQ-003**: Source section 4 requires `/tasks/new` to be the canonical route, compatibility aliases to redirect to it, server-side runtime configuration to flow through the boot payload, and all page actions to use MoonMind REST APIs. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-007, and FR-009.
- **DESIGN-REQ-004**: Source sections 5 and 19 require a single task-first composition form with the canonical sections ordered as Header, Steps, Task Presets, Dependencies, Execution context, Execution controls, Schedule, Submit, while avoiding image-editor, Jira-native, or binary-transport behavior. Scope: in scope. Maps to FR-004, FR-005, FR-006, FR-008, and FR-009.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST serve the Create page at `/tasks/new` through the Mission Control React shell.
- **FR-002**: The `/tasks/new` server response MUST include server-generated runtime configuration in the boot payload.
- **FR-003**: Compatibility create routes MUST redirect to `/tasks/new` and MUST NOT define separate product behavior.
- **FR-004**: The Create page MUST expose exactly one task-first composition form for create mode.
- **FR-005**: Edit and rerun modes opened from `/tasks/new` MUST use the same composition surface as create mode.
- **FR-006**: The Create page form shell MUST expose canonical sections in this order: Header, Steps, Task Presets, Dependencies, Execution context, Execution controls, Schedule, Submit.
- **FR-007**: Create page browser actions MUST call MoonMind REST API endpoints and MUST NOT call Jira, object storage, or model providers directly.
- **FR-008**: Manual task authoring and submission MUST remain available when optional presets, Jira integration, or image upload are unavailable.
- **FR-009**: The Create page MUST remain a MoonMind-native task surface and MUST NOT become a generic workflow builder, Jira-native surface, image editor, or binary transport layer.
- **FR-010**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key MM-376.

### Key Entities

- **Create Page Shell**: The server-rendered Mission Control page that hosts the task composition form and receives runtime configuration through the boot payload.
- **Task Composition Form**: The single form used for create, edit, and rerun task authoring.
- **Canonical Section**: An ordered form region with one of the section names defined by the Create Page desired-state contract.
- **Runtime Boot Payload**: Server-generated configuration passed to the browser to define MoonMind REST endpoints and optional feature availability.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Backend route tests verify `/tasks/new` renders the React shell and includes the Create page boot payload.
- **SC-002**: Backend route tests verify compatibility create aliases redirect to `/tasks/new` without rendering independent content.
- **SC-003**: Frontend tests verify the canonical section order is Header, Steps, Task Presets, Dependencies, Execution context, Execution controls, Schedule, Submit.
- **SC-004**: Frontend tests verify create, edit, and rerun modes use the same Create page composition surface.
- **SC-005**: Frontend request-shape tests verify task submission uses the configured MoonMind REST create endpoint.
- **SC-006**: Frontend tests verify manual task authoring remains available when optional presets, Jira integration, and image upload are absent.
- **SC-007**: Verification evidence preserves MM-376 as the source Jira issue for the feature.
