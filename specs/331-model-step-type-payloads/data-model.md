# Data Model: Model Explicit Step Type Payloads and Validation

## Step Payload

Represents one authored, submitted, or executable task step.

Fields:
- `id`: Stable local identity. Required before validation succeeds.
- `title` or generated display label: User-visible label. Required directly or through deterministic generation.
- `type`: Step Type discriminator. Allowed draft values are `tool`, `skill`, and `preset`; executable values are `tool` and `skill` unless an explicit linked-preset mode exists.
- `tool`: Type-specific payload for Tool steps only.
- `skill`: Type-specific payload for Skill steps only.
- `preset`: Type-specific payload for Preset steps only in draft/apply/submit-expansion contexts.
- `source` or `provenance`: Optional metadata recording preset/proposal/manual origin without making runtime execution depend on live preset lookup.
- `validationErrors`: Field-addressable validation results shown before submission.

Validation rules:
- Exactly one type-specific payload must match `type`.
- Mixed type-specific payloads are invalid.
- Missing type-specific payloads are invalid.
- Executable runtime payloads must not contain unresolved Preset steps by default.
- Step-scoped fields must not override task-scoped runtime, repository, git, publish, container, shell, or script controls.

## Tool Payload

Represents a typed executable operation.

Fields:
- `id` or `name`: Selected tool identity.
- `version`: Optional resolved or pinned version.
- `inputs`: Input object validated against the tool schema.
- `requiredCapabilities`: Optional worker capability requirements.
- `sideEffectPolicy`: Policy metadata for side-effecting or command-like tools.
- `validation`: Optional validation metadata for typed command tools.

Validation rules:
- Tool identity is required.
- Inputs must be an object.
- Required capabilities must be a normalized list when provided.
- Command-like tools require bounded inputs and policy metadata.
- Authorization, worker capability, retry, forbidden-field, and side-effect checks must run where local services are available.

## Skill Payload

Represents an agent-facing behavior invocation.

Fields:
- `id`: Selected skill identity, with documented `auto` behavior when allowed.
- `args`: Skill input object.
- `requiredCapabilities`: Optional capability requirements.
- `runtime`: Optional runtime compatibility metadata.
- `context`: Required or optional context metadata.
- `permissions`: Allowed tools or permission metadata.
- `autonomy`: Approval/autonomy constraints.

Validation rules:
- Skill input fields must use object shapes where required.
- Skill resolution and runtime compatibility must be checked where resolver/runtime metadata is available.
- Required context, allowed tools or permissions, and autonomy constraints must be explicit enough for the runtime boundary to enforce.

## Preset Payload

Represents an authoring-time composition step.

Fields:
- `id` or `slug`: Selected preset identity.
- `version`: Optional active authoring version.
- `inputs`: Preset input object.
- `expansionState`: `not_expanded`, `applied`, or `error`.
- `warnings`: Visible expansion warnings.

Validation rules:
- Preset identity and active version are required for apply/submit expansion.
- Inputs must validate against the preset input schema.
- Expansion must be deterministic for the same preset, version, and inputs.
- Generated steps must validate as executable Tool or Skill steps.
- Step count and policy limits must be enforced.
- Failed expansion preserves entered inputs and field-addressable errors.

## Validation Error

Represents an operator-visible pre-submission validation failure.

Fields:
- `path`: Field path such as `task.steps[0].tool.id` or `preset.inputs.jira_issue.key`.
- `message`: Human-readable reason.
- `code`: Stable machine-readable category when available.
- `recoverable`: Whether the operator can fix the input and retry.

Validation rules:
- Every validation failure exposed to users must include a field path and reason.
- Secret-like or credential-like values must not be echoed in messages.

## State Transitions

```text
Draft Preset Step
  -> validate preset inputs
  -> expand deterministically
  -> Generated Tool/Skill Steps with provenance
  -> executable task submission

Draft Tool/Skill Step
  -> validate matching payload and metadata
  -> executable task submission

Executable submission with unresolved Preset Step
  -> rejected unless explicit linked-preset mode exists
```
