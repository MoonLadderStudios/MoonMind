# MM-426 MoonSpec Orchestration Input

## Source

- Jira issue: MM-426
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Standardize Mission Control layout and table composition patterns
- Labels: `moonmind-workflow-mm-d6451203-b4c9-4ae4-92b2-2b618c958888`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-426 from MM project
Summary: Standardize Mission Control layout and table composition patterns
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-426 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-426: Standardize Mission Control layout and table composition patterns

Source Reference
- Source Document: docs/UI/MissionControlDesignSystem.md
- Source Title: Mission Control Design System
- Source Sections:
  - 7. Layout system
  - 10.1 Masthead and navigation
  - 10.4 Control decks and filter clusters
  - 10.5 Tables and dense list surfaces
  - 10.6 Column economics
  - 11.1 /tasks/list
- Coverage IDs:
  - DESIGN-REQ-012
  - DESIGN-REQ-013
  - DESIGN-REQ-014
  - DESIGN-REQ-019

User Story
As an operator scanning operational work, I want Mission Control layouts, mastheads, control decks, utility clusters, and data slabs to make comparison and route-level hierarchy fast and predictable.

Acceptance Criteria
- Mission Control shell supports constrained and data-wide modes with documented width ranges or equivalent responsive constraints.
- Masthead uses left brand, viewport-centered nav pills, and right utility/telemetry zone rather than centering nav only in leftover space.
- List/console pages separate primary filters and utilities from the matte table/data slab.
- Upper-right desktop space is used for compact utilities such as live toggle, result counts, active filter summary, page size, or pagination where relevant.
- The task list remains table-first on desktop and uses cards only for narrow/mobile layouts.
- Sticky table headers support long-scroll scanning.
- Pagination and page-size controls are visually attached to the table system, not treated as primary filters.

Requirements
- Implement shell width modes and spacing rhythm.
- Align masthead architecture.
- Create or apply control deck plus data slab primitives.
- Preserve comparison-oriented desktop table economics.

Implementation Notes
- Preserve MM-426 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/UI/MissionControlDesignSystem.md` as the source design reference for Mission Control layout, masthead, control deck, table, and column-composition patterns.
- Keep desktop Mission Control list and console pages comparison-oriented and table-first.
- Use narrow/mobile cards only for constrained viewports where table economics no longer fit.
- Keep compact utility controls visually associated with route-level state and table/data-slab systems rather than treating them as primary filters.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-426 is blocked by MM-427, whose embedded status is Backlog.
- Trusted Jira link metadata also shows MM-426 blocks MM-425, which is not a blocker for MM-426 and is ignored for dependency gating.
