# MM-428 MoonSpec Orchestration Input

## Source

- Jira issue: MM-428
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Apply page-specific composition to task workflows
- Labels: `moonmind-workflow-mm-d6451203-b4c9-4ae4-92b2-2b618c958888`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-428 from MM project
Summary: Apply page-specific composition to task workflows
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-428 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-428: Apply page-specific composition to task workflows

Source Reference
Source Document: docs/UI/MissionControlDesignSystem.md
Source Title: Mission Control Design System
Source Sections:
- 11. Page-specific composition rules
- 11.1 /tasks/list
- 11.2 /tasks/new
- 11.3 Task detail and evidence-heavy pages
Coverage IDs:
- DESIGN-REQ-014
- DESIGN-REQ-017
- DESIGN-REQ-019
- DESIGN-REQ-020
- DESIGN-REQ-021

User Story
As a Mission Control operator, I want the task list, task creation flow, and task detail/evidence pages to use the documented composition patterns so each workflow has a clear primary surface and readable supporting content.


Acceptance Criteria
- /tasks/list has a compact filter/control deck above a distinct matte table slab.
- /tasks/list uses right-side utility/telemetry placement, visible active filter chips, sticky table header, and pagination/page-size controls attached to the table system.
- /tasks/new uses matte/satin step cards and a bottom floating launch rail as the page hero surface.
- The /tasks/new primary CTA reads as the clear launch/commit action and large textareas remain matte.
- Task detail pages keep summary, facts, steps, evidence, logs, and actions structurally separate and readable.
- Evidence-heavy pages avoid glass effects that compete with dense evidence or logs.

Requirements
- Implement task list page composition.
- Implement create page launch-flow composition.
- Implement task detail/evidence composition.
- Validate route-specific one-hero-effect and matte dense-region rules.

Implementation Notes
- Preserve MM-428 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/UI/MissionControlDesignSystem.md` as the source design reference for page-specific Mission Control task workflow composition.
- Scope implementation to `/tasks/list`, `/tasks/new`, and task detail/evidence-heavy page composition unless related shared UI primitives must be adjusted to satisfy the route-specific behavior.
- Keep dense task workflow surfaces matte/readable and reserve elevated or hero treatment for the documented primary surface on each route.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-428 is blocked by MM-427, whose embedded status is Done.
- Trusted Jira link metadata also shows MM-428 blocks MM-429, which is not a blocker for MM-428 and is ignored for dependency gating.
