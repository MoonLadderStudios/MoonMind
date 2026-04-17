# Data Model: Dependencies and Execution Options

## Create Task Draft

Represents the author's current Create page state.

Fields:
- `instructions`: primary task objective text.
- `steps`: ordered step drafts.
- `inputAttachments`: objective-scoped attachment refs.
- `dependencies`: selected direct `MoonMind.Run` dependency IDs.
- `runtime`: selected runtime id.
- `providerProfile`: selected provider profile id, when available.
- `model`: optional model override.
- `effort`: optional effort override.
- `repository`: repository target.
- `startingBranch`: optional starting branch.
- `targetBranch`: optional target branch.
- `publishMode`: one of `pr`, `branch`, or `none`.
- `mergeAutomationEnabled`: boolean UI state, only submittable for ordinary PR-publishing tasks.
- `priority`: integer execution priority.
- `maxAttempts`: integer retry attempt cap.
- `proposeTasks`: boolean follow-up proposal control.
- `schedule`: optional immediate, one-time, deferred, or recurring schedule settings.

Validation:
- Required task instructions or equivalent primary step/preset objective rules must pass before submission.
- Repository validation applies regardless of Jira import or image upload.
- Runtime must be one of the server-provided supported runtimes.
- Publish mode must be `pr`, `branch`, or `none`.
- Priority must be an integer.
- Max attempts must be an integer greater than or equal to 1.

## Run Dependency

Represents one direct prerequisite execution.

Fields:
- `taskId`: execution or workflow identifier submitted in `task.dependsOn`.
- `workflowType`: must resolve to `MoonMind.Run`.
- `entry`: must resolve to `run`.
- `title`: display label for the picker.
- `state`: current execution state for display only.

Validation:
- At most 10 direct dependencies may be selected.
- Duplicate `taskId` values are rejected.
- Dependency fetch failure does not invalidate the rest of the draft.

## Runtime Configuration

Represents server-provided execution defaults and options.

Fields:
- `supportedTaskRuntimes`: selectable runtime IDs.
- `defaultTaskRuntime`: default runtime ID.
- `defaultTaskModelByRuntime`: default model per runtime.
- `defaultTaskEffortByRuntime`: default effort per runtime.
- `providerProfiles.list`: provider profile endpoint.
- `attachmentPolicy`: runtime attachment policy when available.

Validation:
- Provider profiles are scoped to the selected runtime.
- Runtime changes may update model, effort, and provider profile defaults unless the author has made an explicit model override.

## Publish Configuration

Represents publish behavior in the submitted task payload.

Fields:
- `publishMode`: top-level normalized publish mode.
- `task.publish.mode`: nested task publish mode.
- `mergeAutomation`: optional `{ enabled: true }` when available and selected.

Validation:
- `mergeAutomation.enabled=true` is submitted only when publish mode is `pr`, the page is creating a new task, and the effective selected skill is not resolver-style.
- Resolver-style skills force direct task publish behavior to `none`.

## Resolver-Style Task

Represents direct execution of resolver skills.

Fields:
- `skillId`: selected skill id or effective preset skill id.

Resolver skill IDs:
- `pr-resolver`
- `batch-pr-resolver`

Validation:
- Direct resolver-style tasks do not expose merge automation.
- Direct resolver-style tasks do not submit enabled merge automation fields.
