# Data Model: Create Page Authoring Validation

## Task Draft

Represents the user-authored Create page state before submission.

Fields:
- `objective`: primary task instructions derived from the primary step or preset feature request.
- `steps`: ordered authored steps, including manual, skill, preset-derived, included, detached, or Jira-derived content.
- `repository`: selected repository value used for branch lookup and task submission.
- `branch`: selected authored branch value.
- `publishMode`: selected publish mode: none, branch, or pull request.
- `runtime`: selected runtime mode plus optional model, effort, and provider profile.
- `inputAttachments`: objective-scoped attachment refs.
- `step.inputAttachments`: step-scoped attachment refs.
- `dependencies`: direct prerequisite execution IDs.
- `authoredPresets` and `appliedStepTemplates`: provenance and reconstruction metadata.

Validation rules:
- Repository is required when no system default repository exists.
- Repository must be a supported token-free repository reference.
- Runtime must be one of the configured task runtimes.
- Publish mode must normalize to none, branch, or pr.
- Branch is required for branch publish mode.
- Attachment policy and target validation must pass before submission.
- Dependency count and values must respect existing Create page dependency rules.

## Steps Card Controls

Represents the visual and interactive grouping of Repository, Branch, and Publish Mode inside the Create page Steps card.

Fields:
- `repositoryControl`: repository text/select input with MoonMind-owned suggestions.
- `branchControl`: branch input backed by MoonMind branch lookup when available.
- `publishModeControl`: publish mode selector.
- `submitAction`: remains the task submission action but does not own Publish Mode semantics.

Validation rules:
- Moving controls into the Steps card must not change field names, accessible labels, or payload semantics unless tests are updated for intentional accessibility improvements.
- Branch lookup still uses the selected repository through MoonMind API surfaces.
- Publish Mode remains task submission data.

## Task Payload

Represents the normalized payload sent to task execution.

Fields:
- `task.instructions`: objective instructions.
- `task.steps`: normalized authored steps when explicit steps are submitted.
- `task.git.branch`: the single authored branch field.
- `task.publish.mode`: selected publish mode after self-managed skill adjustments.
- `task.runtime`: selected runtime information.
- `task.inputAttachments` and `task.steps[].inputAttachments`: attachment refs.
- `task.dependsOn`: selected direct dependencies.
- `task.authoredPresets` and `task.appliedStepTemplates`: provenance metadata.

Validation rules:
- New submissions must not emit `targetBranch`.
- Unsupported branch aliases must fail at backend boundaries where canonical task-shaped payloads are required.
- Payload generation must preserve authoring provenance relevant to reconstruction, audit, diagnostics, and rerun behavior.

## State Transitions

1. User edits draft fields in the Create page.
2. Repository changes may refresh branch options and stale branch messaging.
3. Publish Mode changes may require branch validation or self-managed publish adjustment.
4. Submit validates repository, runtime, publish mode, branch, dependencies, and attachment policy.
5. Valid draft normalizes into Task Payload.
6. Invalid draft remains editable and displays actionable validation feedback.
