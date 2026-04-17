# MM-390 MoonSpec Orchestration Input

## Source

- Jira issue: MM-390
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: The Create button for the Create Page should actually be an arrow pointing to the right
- Labels: None
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, description, acceptance criteria, or relevant implementation notes.

## Canonical MoonSpec Feature Request

Jira issue: MM-390 from MM project
Summary: The Create button for the Create Page should actually be an arrow pointing to the right
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-390 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-390: The Create button for the Create Page should actually be an arrow pointing to the right

Source Reference
- Source Document: docs/UI/CreatePage.md
- Source Title: Create Page

User Story
As a Mission Control user, I want the Create Page submit action to use a right-pointing arrow icon so the primary action visually communicates forward progress.

Acceptance Criteria
- The Create Page primary Create button uses a right-pointing arrow icon for the submit action.
- The button remains recognizable as the primary Create action and preserves the existing submit behavior.
- The icon change does not alter task creation, validation, disabled/loading states, or Jira/preset import behavior.
- The button remains accessible, with existing text or accessible labeling sufficient for screen readers.
- The button layout remains stable across desktop and mobile Create Page viewports.

Requirements
- Update the Create Page submit button presentation so the visual icon points to the right.
- Preserve the existing task submission contract and all current Create Page controls.
- Preserve existing accessibility semantics for the submit action.
- Add or update focused UI coverage when an existing test asserts the button content or icon.

Relevant Implementation Notes
- Treat this as a narrow Create Page UI polish story.
- Prefer the existing Create Page component and icon system rather than introducing a new visual dependency.
- Do not change task execution payloads, preset expansion, Jira import, dependency controls, runtime controls, or publish behavior.
- If the current UI already uses an icon near the Create button, replace only that icon with the right-pointing arrow equivalent.
- If no icon is present, add the right-pointing arrow in a way that does not remove the Create action's accessible name.

Verification
- Confirm the Create Page primary Create button visibly uses a right-pointing arrow icon.
- Confirm submitting a valid task still uses the existing Create Page submission path.
- Confirm disabled/loading and validation states still behave as before.
- Confirm accessible labeling for the primary Create action remains intact.
- Preserve MM-390 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
