# Data Model: Author Governed Tool Steps

## Tool Metadata

Represents one trusted typed Tool exposed to task authors.

Fields:
- `name`: stable Tool id, such as `jira.transition_issue`.
- `description`: human-readable operation summary.
- `inputSchema`: JSON Schema describing accepted inputs.
- `group`: derived authoring group, usually the prefix before the first dot.
- `searchText`: derived lowercase text from name, description, group, and schema property names.

Validation rules:
- `name` must be non-empty and must match the trusted metadata response.
- Unknown tool ids cannot be submitted from governed Tool authoring.

## Governed Tool Step Draft

Represents a task step being authored with Step Type `Tool`.

Fields:
- `stepType`: `tool`.
- `toolId`: selected trusted Tool id.
- `toolVersion`: optional pinned or displayed resolved version when available.
- `toolInputs`: JSON object text or schema-guided field state converted to a JSON object.
- `toolSchema`: selected Tool metadata schema.
- `dynamicOptions`: field-specific option values returned by trusted option providers.
- `validationState`: actionable errors for missing metadata, invalid schema inputs, option provider failure, authorization/capability unavailability, forbidden fields, and unknown policy.

Validation rules:
- Tool id must be selected from trusted metadata.
- Inputs must parse to an object and satisfy selected schema requirements supported by the authoring surface.
- Dynamic option fields must use returned options when an option provider is required.
- Provider failures fail closed and block submission.
- Shell-like fields remain forbidden unless the selected typed command Tool explicitly permits bounded command inputs and policy.

## Dynamic Option Result

Represents field options derived from trusted integration state.

Fields:
- `toolId`: Tool id whose field is being populated.
- `field`: input field name, such as `targetStatus`.
- `prerequisites`: input fields used for lookup, such as `issueKey`.
- `options`: allowed string values and labels.
- `status`: `idle`, `loading`, `ready`, or `error`.
- `errorMessage`: sanitized failure text for authoring feedback.

State transitions:
- `idle` -> `loading` when prerequisites become available.
- `loading` -> `ready` when trusted options return.
- `loading` -> `error` when the trusted lookup fails.
- `ready` -> `idle` when prerequisites change and options are stale.
