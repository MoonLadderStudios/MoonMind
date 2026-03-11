# Contract: Skill Adapter Resolution and Execution

## Adapter Resolution

- Input: `selected_skill` from stage execution decision.
- Output: concrete `adapter_id` from registry.
- Rule: if no adapter exists for `selected_skill`, fail immediately with adapter resolution error.

## Execution Semantics

- `execution_path = "skill"` only when an adapter is resolved and invoked.
- `execution_path = "direct_only"` only when skills mode is disabled by policy decision.
- `execution_path = "direct_fallback"` only when adapter invocation fails and fallback is enabled.

## Error Contract

Unsupported skill adapter error payload/log fields include:

- `code`: `skill_adapter_not_registered`
- `selectedSkill`: requested skill name
- `stageName`: workflow stage
- `message`: actionable instruction to register adapter or select supported skill

## Dependency Check Contract

- Speckit dependency verification runs only when effective skills include `speckit`.
- Non-speckit-only contexts must skip Speckit checks and proceed.
