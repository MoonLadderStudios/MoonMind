# Data Model: Author Agentic Skill Steps

## Skill Step Draft

Fields:
- `type`: must be `skill` for this story.
- `instructions`: user-authored instructions for the agentic work.
- `skillId`: selected Skill id or a documented supported auto selector.
- `skillArgs`: optional JSON object with structured inputs such as `{ "issueKey": "MM-577", "mode": "runtime" }`.
- `requiredCapabilities`: optional list of capability names such as `git` or `jira`.
- `runtimePreferences`: optional runtime/model preferences when supported by the authoring surface.
- `permissionsAndApprovals`: optional allowed tools, permission, or autonomy metadata when supported by the authoring surface.

Validation rules:
- `skillArgs` must be an object when present.
- `requiredCapabilities` must normalize to a list of non-empty strings.
- Missing or unresolved Skill selector values are rejected unless documented supported auto semantics apply.
- Tool-only payload fields are invalid for Skill steps.

## Skill Payload

Fields:
- `type`: `skill`.
- `tool.type`: `skill` metadata for existing task payload compatibility.
- `tool.name` / `skill.id`: selected Skill id.
- `tool.inputs` / `skill.args`: validated Skill args.
- `requiredCapabilities`: normalized capability list when supplied.

Relationships:
- A Skill Step Draft produces one Skill Payload at submission time.
- The payload preserves `MM-577` in args when the author supplied it.

State transitions:
- Draft -> Validated Skill Payload when selector, args, and capabilities validate.
- Draft -> Validation Error when required Skill fields are missing or malformed.
