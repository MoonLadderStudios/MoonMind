# Data Model: Jira Import Actions

## JiraIssueDetail

Represents the selected Jira story after MoonMind normalization.

Fields:

- `issueKey`: Stable issue key displayed to the operator.
- `summary`: Issue summary displayed in preview and used in generated import text.
- `descriptionText`: Plain-text description available for preview and Description only mode.
- `acceptanceCriteriaText`: Plain-text acceptance criteria available for preview and Acceptance criteria only mode.
- `recommendedImports.presetInstructions`: Preferred text for Preset brief mode when present.
- `recommendedImports.stepInstructions`: Preferred text for Execution brief mode when present.

Validation rules:

- Import actions require a selected issue detail.
- Empty text for the chosen mode must not erase existing target content.
- Browser clients consume normalized text only.

## JiraImportTarget

Represents where imported Jira text will be written.

Variants:

- `preset`: The preset Feature Request / Initial Instructions field.
- `step`: A specific step Instructions field identified by local step identity.

Validation rules:

- The target selected when opening the browser is the default target.
- Importing into a missing step must not write into another step.
- Exactly one target is affected per import action.

## JiraImportMode

Represents the text shape selected by the operator before import.

Values:

- `preset-brief`: Summary plus description, preferring normalized preset recommendation.
- `execution-brief`: Execution-oriented issue brief, preferring normalized step recommendation.
- `description-only`: Description text only.
- `acceptance-only`: Acceptance criteria text only.

Validation rules:

- Preset target defaults to `preset-brief`.
- Step target defaults to `execution-brief`.
- Operators may change the mode before importing.
- Import preview reflects the currently selected mode.

## JiraWriteAction

Represents the explicit write operation.

Values:

- `replace`: Replace current target text with selected import text.
- `append`: Preserve current target text and add selected import text after a clear separator.

Validation rules:

- Selection and preview do not trigger a write.
- Append into an empty target produces only imported text.
- Replace and Append must be keyboard-accessible actions.

## PresetObjectiveDraft

Represents the existing preset Feature Request / Initial Instructions draft field.

Fields:

- `text`: Current preset objective text.
- `appliedTemplates`: Existing applied preset state used to determine whether a reapply-needed message is required.
- `message`: Non-blocking status text shown to the operator.

State transitions:

- Empty or existing text -> imported text after Replace.
- Existing text -> existing text plus separator plus imported text after Append.
- Applied preset present + Jira import -> reapply-needed message.
- Jira import never automatically rewrites expanded preset steps.

## StepDraft

Represents an existing Create page step draft.

Fields:

- `localId`: Client-local step identity used as Jira import target.
- `instructions`: Authored step instructions.
- `id`: Template-step identity when the step is still template-bound.
- `templateStepId`: Original template-step identity.
- `templateInstructions`: Template-provided instructions used for divergence checks.

State transitions:

- Selected step instructions -> imported text after Replace.
- Selected step instructions -> existing text plus separator plus imported text after Append.
- Template-bound step + imported instructions different from template instructions -> manual customized step with detached template identity.
