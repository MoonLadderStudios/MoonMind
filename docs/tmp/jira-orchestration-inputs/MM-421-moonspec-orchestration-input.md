# MM-421 MoonSpec Orchestration Input

## Source

- Jira issue: MM-421
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Show recent manifest runs below the run form
- Labels: moonmind-workflow-mm-f78d0769-fb1e-4712-a140-47c34e83cc3f
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-421 from MM project
Summary: Show recent manifest runs below the run form
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-421 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-421: Show recent manifest runs below the run form

Source Reference
- Source Document: docs/UI/ManifestsPage.md
- Source Title: Manifests Page
- Source Sections:
  - Recent Runs section
  - Data source
  - Table purpose
  - Recommended columns
  - Manifest-specific status detail
  - Filters
  - Empty state
  - Responsive behavior
  - Accessibility
- Coverage IDs:
  - DESIGN-REQ-002
  - DESIGN-REQ-010
  - DESIGN-REQ-011
  - DESIGN-REQ-012
  - DESIGN-REQ-013
  - DESIGN-REQ-014
  - DESIGN-REQ-017

User Story
As a dashboard user, I want recent manifest runs visible below the Run Manifest card so I can immediately check start state, current stage, result, timing, and details for manifest executions.

Acceptance Criteria
- Given /tasks/manifests loads, then Recent Runs requests /api/executions?entry=manifest&limit=200.
- Given manifest runs exist, then the history surface shows run ID/details link, manifest label, action, status, current stage when available, started time, duration when available, and supported row actions.
- Given a run is active and stage data is available, then the status display includes manifest-specific stage detail such as Running · fetch.
- Given status, manifest name, or free-text filters are used, then the visible run list updates without a heavy filter-builder flow.
- Given no manifest runs exist, then the empty state says no manifest runs exist and points users to run a registry manifest or submit inline YAML above.
- Given the viewport is narrow, then recent runs remain readable as compact cards or stacked rows with clear action labels.
- Given row actions are icon-based in implementation, then they include accessible names.

Requirements
- Recent Runs must appear below the Run Manifest card on the same page.
- The history request must use the existing manifest execution endpoint for phase 1.
- The history view must help answer whether the run started, whether it is still running, what stage it is in, whether it succeeded or failed, and how to open details/logs.
- Filters must remain lightweight and bounded to status, manifest name, search, and optional action support.
- The implementation must not require a backend redesign or new manifest-centric history API for phase 1.

Implementation Notes
- This story is about showing recent manifest execution history below the existing Run Manifest card, not replacing the existing manifest execution contract.
- Use the existing manifest execution endpoint for the initial history request: /api/executions?entry=manifest&limit=200.
- The UI should make recent runs scannable by exposing run identity/details, manifest label, action, status, current stage, timing, and available row actions.
- Active runs should show manifest-specific stage detail when stage data exists.
- Filters should remain lightweight and focused on status, manifest name, search, and optional action support.
- Preserve MM-421 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
