# UI Contract: Create Page Step Type Presentation

## Step Type Selector

Each ordinary step editor exposes exactly one accessible selector named `Step Type`.

Options:

- `Tool`
- `Skill`
- `Preset`

Default selection: `Skill` for newly created steps.

## Helper Copy

The Step Type area exposes concise copy for each choice:

- Tool: runs a typed integration or system operation directly.
- Skill: asks an agent to perform work using reusable behavior.
- Preset: inserts a reusable set of configured steps.

The helper copy must not present `Capability`, `Activity`, `Invocation`, `Command`, or `Script` as the primary Step Type discriminator.

## Type-Specific Presentation

- When `Step Type = Tool`, Tool-specific controls are visible and Skill/Preset controls are hidden.
- When `Step Type = Skill`, Skill-specific controls are visible and Tool/Preset controls are hidden.
- When `Step Type = Preset`, Preset-specific controls are visible and Tool/Skill controls are hidden.

## Data Preservation

- Instructions remain visible and preserved when Step Type changes.
- Hidden Skill fields are not submitted for Tool or Preset steps.
- Step Type and Preset selections remain scoped to each individual step.
