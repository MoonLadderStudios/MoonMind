# Data Model: Schema-Driven Capability Inputs

## Capability Input Contract

Represents selectable capability input metadata for presets, skills, and tools where applicable.

Fields:
- `id`: stable capability identifier.
- `kind`: one of `preset`, `skill`, or `tool` when surfaced in Create-page authoring.
- `label`: user-visible capability name.
- `description`: sanitized user-visible help text.
- `inputSchema`: JSON Schema-compatible object describing accepted input values.
- `uiSchema`: optional presentation metadata keyed by schema field path.
- `defaults`: safe default values for fields, excluding raw secrets or credentials.
- `validationMetadata`: optional metadata needed to map backend validation errors to generated fields.

Validation rules:
- `inputSchema` must be an object when present.
- `uiSchema` must be data only; no executable expressions.
- Defaults must not include secret-like raw values.
- The contract must preserve schema, UI hints, and defaults losslessly across API responses.

## Schema-Generated Field

Represents a field rendered from a capability input contract.

Fields:
- `path`: stable path to the field, such as `preset.inputs.jira_issue.key`.
- `label`: generated user-visible label from schema title, UI metadata, or field name.
- `required`: whether the schema requires the value.
- `value`: draft value held in Create-page state.
- `widget`: resolved widget registry key or fallback widget.
- `error`: optional field-addressable validation error.

Validation rules:
- Field paths must be stable enough for backend errors to map back to the same generated field.
- Unsupported widgets either fall back safely or produce a field-scoped unsupported-widget error.
- Existing draft values must survive validation and integration lookup failures.

## Widget Registry Entry

Represents an allowlisted reusable field renderer.

Fields:
- `name`: semantic widget key such as `text`, `textarea`, `select`, or `jira.issue-picker`.
- `supportedSchema`: schema shapes the widget can safely render.
- `fallbackPolicy`: whether fallback to a simpler widget is allowed.
- `redactionPolicy`: how displayed and submitted values avoid exposing secrets.

Validation rules:
- Widgets are selected by metadata, not capability IDs.
- Unknown widget names never execute arbitrary code.
- Widget descriptions and labels are sanitized before rendering.

## Jira Issue Input Value

Represents safe Jira issue context configured by a user.

Fields:
- `key`: required durable issue key when the field is required.
- `summary`: optional sanitized summary.
- `description`: optional sanitized description or excerpt when available.
- `url`: optional issue URL.
- `status`: optional sanitized status.
- `assignee`: optional sanitized assignee display value.

Validation rules:
- `key` is the minimum durable reference.
- Optional enrichment can be absent and must not be treated as source of truth.
- Raw Jira credentials, auth headers, tokens, cookies, and raw client responses are never stored in this value.
- Trusted Jira tooling may validate or enrich the value when integration access is available.

## Field-Addressable Validation Error

Represents validation feedback for generated fields.

Fields:
- `path`: generated field path.
- `message`: user-facing message.
- `code`: stable error code such as `required`, `unsupported_widget`, `invalid_type`, or `integration_unavailable`.
- `recoverable`: whether the user can correct the field without losing draft state.

State transitions:
- `clean` -> `invalid`: validation fails for a field.
- `invalid` -> `clean`: user corrects the value and validation passes.
- `lookup_pending` -> `clean`: optional enrichment succeeds.
- `lookup_pending` -> `invalid` or `clean_with_warning`: integration lookup fails depending on manual-entry policy.
