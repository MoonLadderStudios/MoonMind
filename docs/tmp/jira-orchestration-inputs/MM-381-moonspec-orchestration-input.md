# MM-381 MoonSpec Orchestration Input

## Source

- Jira issue: MM-381
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Jira Import Into Declared Targets
- Labels: `moonmind-workflow-mm-5818081f-60f0-45dd-ad16-3f7753de93ae`
- Trusted fetch tool: `jira.get_issue`
- Normalized detail source: `/api/jira/issues/MM-381`
- Canonical source: `recommendedImports.presetInstructions` from the normalized trusted Jira issue detail response.

## Canonical MoonSpec Feature Request

Jira issue: MM-381 from MM project
Summary: Jira Import Into Declared Targets
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-381 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-381: Jira Import Into Declared Targets

Source Reference
Source Document: docs/UI/CreatePage.md
Source Title: Create Page
Source Sections:
- 12. Jira integration contract
- 16. Failure and empty-state rules
- 17. Accessibility and interaction rules
- 18. Testing requirements
Coverage IDs:
- DESIGN-REQ-017
- DESIGN-REQ-018
- DESIGN-REQ-003
- DESIGN-REQ-010
- DESIGN-REQ-012
- DESIGN-REQ-015
- DESIGN-REQ-022
- DESIGN-REQ-023
- DESIGN-REQ-024
- DESIGN-REQ-025
As a task author, I can browse Jira as an external instruction source and explicitly import issue text or supported images into a declared Create page target without automatic draft mutation.

## Normalized Jira Detail

Acceptance criteria:
- Given I open Jira from a Create page field, then the browser preselects that matching target and displays the current target explicitly.
- Given I select a Jira issue, then the draft does not mutate until I confirm a text or image import action.
- Given I switch import targets inside the Jira browser, then the selected issue remains selected.
- Given I import text, then I can choose Replace target text or Append to target text for preset objective text or a step instruction target.
- Given I import supported Jira images, then selected images become structured attachments on the selected objective or step attachment target and are not injected as markdown, HTML, or inline data.
- Given Jira is unavailable or the issue fetch fails, then the draft is not mutated and I can continue manual authoring.
- Given import succeeds, then focus returns predictably to the updated field or an explicit success notice.

Requirements:
- Support Jira import targets for preset objective text, preset objective attachments, step text, and step attachments.
- Require explicit confirmation for all Jira text and image imports.
- Preserve selected issue state while switching targets inside the browser.
- Import Jira images only as structured attachments on the declared target.
- Mark already-applied preset state as needing reapply when importing into preset objective text or attachment targets.
- Detach template-bound steps when Jira text or images import into them.
- Keep Jira access behind MoonMind APIs and separate from task execution substrate behavior.
- Cover explicit import, no-mutation-before-confirm, image target mapping, template detachment, focus return, and failure behavior in tests.

Recommended step instructions:

Complete Jira issue MM-381: Jira Import Into Declared Targets

Description
Source Reference
Source Document: docs/UI/CreatePage.md
Source Title: Create Page
Source Sections:
- 12. Jira integration contract
- 16. Failure and empty-state rules
- 17. Accessibility and interaction rules
- 18. Testing requirements
Coverage IDs:
- DESIGN-REQ-017
- DESIGN-REQ-018
- DESIGN-REQ-003
- DESIGN-REQ-010
- DESIGN-REQ-012
- DESIGN-REQ-015
- DESIGN-REQ-022
- DESIGN-REQ-023
- DESIGN-REQ-024
- DESIGN-REQ-025
As a task author, I can browse Jira as an external instruction source and explicitly import issue text or supported images into a declared Create page target without automatic draft mutation.

Acceptance criteria
- Given I open Jira from a Create page field, then the browser preselects that matching target and displays the current target explicitly.
- Given I select a Jira issue, then the draft does not mutate until I confirm a text or image import action.
- Given I switch import targets inside the Jira browser, then the selected issue remains selected.
- Given I import text, then I can choose Replace target text or Append to target text for preset objective text or a step instruction target.
- Given I import supported Jira images, then selected images become structured attachments on the selected objective or step attachment target and are not injected as markdown, HTML, or inline data.
- Given Jira is unavailable or the issue fetch fails, then the draft is not mutated and I can continue manual authoring.
- Given import succeeds, then focus returns predictably to the updated field or an explicit success notice.
Requirements
- Support Jira import targets for preset objective text, preset objective attachments, step text, and step attachments.
- Require explicit confirmation for all Jira text and image imports.
- Preserve selected issue state while switching targets inside the browser.
- Import Jira images only as structured attachments on the declared target.
- Mark already-applied preset state as needing reapply when importing into preset objective text or attachment targets.
- Detach template-bound steps when Jira text or images import into them.
- Keep Jira access behind MoonMind APIs and separate from task execution substrate behavior.
- Cover explicit import, no-mutation-before-confirm, image target mapping, template detachment, focus return, and failure behavior in tests.
