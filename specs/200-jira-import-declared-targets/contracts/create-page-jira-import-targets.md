# Contract: Create Page Jira Import Targets

## Browser Entry Points

The Create page exposes Jira browser entry points only when runtime Jira integration is enabled and endpoint templates are MoonMind-owned API paths.

Required entry points:
- Preset objective text: opens target `preset-text`.
- Preset objective attachments: opens target `preset-attachments` when attachment policy is enabled.
- Step instructions: opens target `step-text:<stepLocalId>`.
- Step attachments: opens target `step-attachments:<stepLocalId>` when attachment policy is enabled.

## In-Browser Target Selection

The Jira browser displays the current target and provides target selection across the enabled targets above.

Rules:
- Opening from a field preselects the matching target.
- Switching targets inside the browser preserves the selected issue.
- Attachment-only targets do not write Jira text into instruction fields.

## Text Import

Text targets support:
- Append to target text.
- Replace target text.

Rules:
- Preset text imports use `recommendedImports.presetInstructions` when available.
- Step text imports use `recommendedImports.stepInstructions` when available.
- Empty import text leaves the target unchanged.
- Text imports do not create tasks or bypass Create page validation.

## Image Import

Attachment targets support importing supported Jira image attachments.

Rules:
- Imported images become local draft attachment candidates on the selected target.
- Objective images submit through `task.inputAttachments` after artifact upload.
- Step images submit through the owning `task.steps[n].inputAttachments` after artifact upload.
- Images are not injected into instruction text as markdown, HTML, inline data, or filename-derived references.

## Failure Behavior

Jira provider, project, board, issue-list, or issue-detail failures remain local to the Jira browser.

Rules:
- Draft text, attachments, repository, runtime, publish, and schedule settings remain unchanged.
- Manual task authoring remains available.
- Browser failure output must not include raw credentials or direct Atlassian request details.
