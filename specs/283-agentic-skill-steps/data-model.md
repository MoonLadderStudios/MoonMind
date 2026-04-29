# Data Model: Agentic Skill Step Authoring

## Skill Step Draft

Represents an editable Create-page step with Step Type `Skill`.

Fields:
- `stepType`: `skill`.
- `instructions`: required when the step performs work.
- `skillId`: optional selector; blank uses documented `auto` behavior where supported.
- `skillArgs`: optional JSON object text for skill-specific context and inputs.
- `skillRequiredCapabilities`: optional CSV normalized to `requiredCapabilities`.

Validation:
- `skillArgs` must parse to a JSON object when present.
- hidden advanced Skill fields are not submitted while advanced mode is off.
- Skill steps do not submit Tool-only manual tool id/version/input fields.

## Skill Payload

Submitted executable Skill payload.

Fields:
- `type`: `skill` on the step when an explicit step shape is submitted.
- `tool`: legacy-compatible skill tool metadata with `type: skill`, `name`, `version`, optional `inputs`, and optional `requiredCapabilities`.
- `skill`: explicit Skill metadata with `id`, `args`, optional `requiredCapabilities`, and metadata preserved by template boundaries where present.

Validation:
- Skill steps reject non-skill Tool payloads.
- Skill required capabilities must be a list after normalization.
- Tool-only executable payloads must not coexist with Skill payloads.

## Agentic Controls

Optional controls that shape agentic work.

Fields:
- context or issue data in `skill.args` for direct task submission.
- `requiredCapabilities` for worker/tool affordances.
- template-preserved metadata such as `context`, `permissions`, and `autonomy` when authored through task templates or presets.
