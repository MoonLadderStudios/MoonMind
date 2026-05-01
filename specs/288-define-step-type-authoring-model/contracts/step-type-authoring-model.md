# Contract: Step Type Authoring Model

## Authoring UI Contract

- Each authored step exposes one user-facing `Step Type` selector.
- Valid choices are `Tool`, `Skill`, and `Preset`.
- Selecting `Tool` shows Tool configuration only.
- Selecting `Skill` shows Skill configuration only.
- Selecting `Preset` shows Preset selection, preview, and apply controls only.
- Switching Step Type preserves shared instructions.
- Switching Step Type clears or visibly handles incompatible type-specific state.
- The selector and helper copy use Step Type vocabulary, not capability/activity/invocation/command/script as the umbrella label.

## Runtime Payload Contract

- Runtime executable steps accept `type: tool` or `type: skill`.
- Runtime executable steps reject `type: preset`, `type: activity`, and case variants such as `Activity`.
- Tool steps reject an attached Skill payload.
- Skill steps reject a non-skill Tool payload.
- Preset-derived runtime steps carry provenance as metadata after expansion, not as unresolved runtime work.

## Verification Surface

- `frontend/src/entrypoints/task-create-step-type.test.tsx` verifies the rendered authoring UI contract.
- `tests/unit/workflows/tasks/test_task_contract.py` verifies executable payload contract rejection paths.
