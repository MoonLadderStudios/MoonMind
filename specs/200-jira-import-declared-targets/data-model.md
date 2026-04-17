# Data Model: Jira Import Into Declared Targets

## Jira Import Target

Represents the declared destination for one Jira import.

Fields:
- `kind`: preset objective or step.
- `localId`: present for step targets.
- `attachmentsOnly`: true when the target is an attachment target.

Validation:
- Preset targets do not carry `localId`.
- Step targets must reference an existing draft step.
- Attachment targets require enabled attachment policy.

## Jira Issue Detail

Normalized issue data returned by MoonMind APIs.

Fields:
- `issueKey`
- `summary`
- `descriptionText`
- `acceptanceCriteriaText`
- `recommendedImports.presetInstructions`
- `recommendedImports.stepInstructions`
- `attachments[]`

Validation:
- Empty text values must not mutate draft text.
- Image attachments must pass the active attachment policy before becoming draft attachments.

## Draft Attachment

Local or persisted attachment associated with an objective or step target.

Fields:
- `filename`
- `contentType`
- `sizeBytes`
- local `File` or persisted artifact ref
- target identity: objective or step local id

Validation:
- Objective attachments submit through `task.inputAttachments`.
- Step attachments submit through the owning `task.steps[n].inputAttachments`.
- Attachment meaning is target-defined, not filename-derived.

## Applied Preset State

Tracks whether a preset has been applied and whether objective text or objective attachments now require explicit reapply.

State transitions:
- Applied preset + changed objective text -> reapply needed.
- Applied preset + changed objective attachments -> reapply needed.
- Step import -> no preset-objective reapply transition.
- Reapply -> applied state refreshes to current objective text and objective attachments.

## Template-Bound Step

Draft step retaining template identity from an applied preset.

State transitions:
- Jira text import into the step -> manual customization and template instruction identity is detached.
- Jira image import into the step attachment target -> manual customization and template attachment identity is detached.
- Import into another step -> unchanged.
