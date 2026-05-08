# Data Model: Normalize Task-Shaped Submissions

## Task Submission

Represents the authored create, edit, or rerun request before execution receives it.

Fields:
- `instructions`: objective text authored by the user.
- `steps`: ordered task steps with stable identity when applicable.
- `inputAttachments`: objective-scoped attachment refs.
- `runtime`: runtime mode, profile, model, and effort intent.
- `publish`: publish mode and publish options.
- `git.branch`: the single authored branch field for new task-shaped submissions.
- `dependsOn`: declared execution dependency IDs.
- `jira provenance`: Jira issue metadata where the task contract supports it.
- `authoredPresets`: authored preset binding metadata when present.
- `appliedStepTemplates`: flattened template provenance when present.

Validation rules:
- A submission must remain task-shaped; users do not author workflow internals.
- Invalid repository, runtime, publish mode, dependencies, attachment policy, and attachment target values fail explicitly.
- New task-shaped submissions must not produce a separate target branch field.

## Attachment Target

Represents the explicit destination for an input attachment.

Fields:
- `targetKind`: `objective` or `step`.
- `stepRef`: step identifier when `targetKind` is `step`.
- `stepOrdinal`: step order position used only as supporting evidence, not as the sole durable meaning when a step ID exists.
- `attachmentRef`: structured attachment metadata.

Validation rules:
- Every attachment belongs to exactly one target.
- Step-scoped attachments must reference a declared step.
- Duplicate or conflicting declarations fail explicitly.
- Binary content is never embedded in text fields.

## Task Step

Represents one ordered unit of work inside a task submission.

Fields:
- `id`: stable step identity when preserved from a template or previous task.
- `title`: optional user-visible step label.
- `instructions`: textual step instructions.
- `inputAttachments`: step-scoped attachment refs.
- `source`: provenance for manual, preset-derived, preset-include, or detached steps.
- `skill`/`tool`/`skills`: existing executable selection metadata.
- `storyOutput`/`jiraOrchestration`: existing structured orchestration metadata where supported.

Validation rules:
- Reordering steps must not retarget attachments.
- Text changes must not retarget attachments.
- Task-scoped controls are not accepted inside a step.

## Authored Preset Binding

Represents preset selection and composition metadata authored before submission.

Fields:
- `presetId`
- `presetSlug`
- `version`
- `alias`
- `includePath`
- `inputMapping`
- `scope`

Validation rules:
- Preset binding metadata remains durable in normalized task output when supplied.
- Preset aliases or migration-era input must not silently change attachment targets.

## Normalized Task Output

Represents the task-shaped contract handed to execution after validation.

Fields:
- All accepted task submission fields after normalization.
- Attachment arrays preserving objective and step scope.
- Canonical branch field only for new task-shaped branch intent.
- Compact provenance metadata for Jira and presets where supported.

State transitions:
- `authored` -> `validated`: input shape and policy checks pass.
- `validated` -> `normalized`: canonical task output is produced.
- `validated` -> `rejected`: invalid repository, runtime, publish, dependency, attachment policy, or target binding fails explicitly.
- `normalized` -> `execution-received`: execution receives exactly the canonical task-shaped output.
