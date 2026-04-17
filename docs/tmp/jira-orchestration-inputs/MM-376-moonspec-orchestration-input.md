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
