# Contract: Step Type Payload Validation

## Scope

This contract defines the expected behavior for draft authoring, preset expansion, and executable task submission validation for `MM-569`.

## Draft Step Contract

Accepted draft Step Types:
- `tool`
- `skill`
- `preset`

Common required properties:
- stable step identity
- title or generated display label
- Step Type discriminator
- exactly one matching type-specific payload

Invalid draft behavior:
- missing Step Type when emitted by a new authoring surface
- more than one type-specific payload
- type-specific payload that does not match `type`
- shell/script/task-scoped override fields embedded in a step

Legacy-reader allowance:
- Existing persisted payloads without explicit `type` may be read during migration.
- New authoring and submission output must emit an explicit normalized Step Type.

## Tool Step Contract

Tool steps are valid only when:
- `type` is `tool`
- `tool.id` or `tool.name` is present
- `tool.inputs` is an object when provided
- `tool.requiredCapabilities` is a list when provided
- command-like tools include bounded inputs plus policy metadata
- authorization, worker capability, forbidden-field, retry, and side-effect checks run where available

Tool steps are invalid when:
- they include a `skill` or `preset` payload
- they embed arbitrary shell snippets outside an approved typed command tool

## Skill Step Contract

Skill steps are valid only when:
- `type` is `skill`
- `skill.id` resolves or uses documented `auto` semantics
- `skill.args` is an object when provided
- runtime compatibility, required context, permissions, and autonomy constraints are valid where metadata is available

Skill steps are invalid when:
- they include a non-skill Tool payload
- they include a Preset payload
- they include non-object context, permission, autonomy, or runtime metadata where an object is required

## Preset Step Contract

Preset steps are valid in authoring/apply/submit-expansion contexts only when:
- `type` is `preset`
- selected preset exists
- selected version is active for authoring
- `preset.inputs` validates against the preset input schema
- expansion is deterministic
- generated steps validate under Tool or Skill rules
- step count and policy limits are enforced
- warnings are visible to the operator

Preset steps are invalid in executable runtime payloads unless an explicit linked-preset execution mode is present.

## Error Contract

Validation errors must include:
- `path`
- `message`
- `code` when available
- `recoverable` when applicable

Examples:

```json
{
  "path": "task.steps[0].tool.id",
  "message": "Tool steps require tool.id or tool.name.",
  "code": "required",
  "recoverable": true
}
```

```json
{
  "path": "preset.inputs.jira_issue.key",
  "message": "Jira issue key is required.",
  "code": "required",
  "recoverable": true
}
```

## Boundary Tests

Unit tests must cover:
- valid Tool, Skill, and Preset payloads
- mixed type-specific payload rejection
- missing required type-specific payload rejection
- command-like Tool policy rejection
- Skill metadata shape rejection
- Preset input schema and generated-step validation
- legacy-reader acceptance with normalized new emissions

Integration tests must cover:
- executable submission rejects unresolved Preset steps
- accepted executable payloads contain only Tool or Skill steps plus provenance
- API-visible validation failures are field-addressable
- failed Preset expansion preserves input values and errors
