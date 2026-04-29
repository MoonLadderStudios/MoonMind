# Data Model: Governed Tool Step Authoring

## Tool Step Draft

- `stepType`: `tool`
- `instructions`: optional task-author text preserved across Step Type switches
- `toolId`: required non-empty Tool identifier before submission
- `toolVersion`: optional resolvable/pinned version text
- `toolInputs`: JSON object text, defaulting to `{}`

Validation:
- `toolId` must be non-empty for Tool steps.
- `toolInputs` must parse to a JSON object and cannot parse to an array or primitive.
- forbidden shell/script/command keys are not submitted as executable step fields.

## Tool Payload

- `type`: `tool`
- `id`: selected Tool identifier
- `version`: optional Tool version when supplied
- `inputs`: parsed JSON object

Validation:
- Tool steps submit the Tool payload under `tool`.
- Tool steps do not submit `skill`.
- The backend task contract rejects executable step fields named `command`, `cmd`, `script`, `shell`, or `bash`.
