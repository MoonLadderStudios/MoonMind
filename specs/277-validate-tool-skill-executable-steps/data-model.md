# Data Model: Validate Tool and Skill Executable Steps

## Executable Step Blueprint

Represents one authored executable step inside a task template version or saved task preset.

Fields:
- `type`: optional Step Type discriminator for executable steps. Allowed values: `tool`, `skill`. Omitted values are treated as the existing Skill-shaped default only when no Tool payload is present.
- `instructions`: non-empty operator-facing instructions or description.
- `title`: optional display label.
- `tool`: Tool Step Payload, required when `type = tool`.
- `skill`: Skill Step Payload, allowed when `type = skill` or omitted legacy Skill-shaped data.
- `annotations`: optional metadata object.

Validation rules:
- Unsupported `type` values fail validation.
- `type = tool` requires `tool` and rejects `skill`.
- `type = skill` rejects Tool-only payloads.
- Top-level arbitrary shell fields fail validation.

## Tool Step Payload

Fields:
- `id` or `name`: required non-empty typed tool identifier.
- `version`: optional version string.
- `inputs` or `args`: optional object; normalized to `inputs`.
- `requiredAuthorization`, `requiredCapabilities`, `sideEffectPolicy`, `retryPolicy`, `execution`, `validation`: optional metadata preserved when present.

Validation rules:
- `inputs`/`args` must be an object when supplied.
- `requiredCapabilities` must be a list when supplied.
- Command-like tools are accepted only as typed Tool payloads with object inputs and policy metadata.

## Skill Step Payload

Fields:
- `id`: optional skill selector; defaults to `auto` for documented auto semantics.
- `args`: optional object; defaults to `{}`.
- `requiredCapabilities`: optional list.
- `context`, `permissions`, `autonomy`, `runtime`: optional metadata preserved when present.

Validation rules:
- `args` must be an object when supplied.
- `requiredCapabilities` must be a list when supplied.
- Tool-only payload fields are rejected for Skill steps.
