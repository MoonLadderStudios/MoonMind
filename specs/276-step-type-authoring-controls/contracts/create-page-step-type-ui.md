# UI Contract: Create Page Step Type Authoring

## Step Type Control

Each step editor exposes one accessible control named `Step Type` with exactly these user-facing options:

- `Tool`
- `Skill`
- `Preset`

Default selection: `Skill` for newly created steps.

## Skill Configuration Area

Visible only when `Step Type = Skill`.

Contains:

- Existing Skill selector.
- Existing Skill Args advanced field when advanced step options are enabled.
- Existing Skill Required Capabilities advanced field when advanced step options are enabled.

## Tool Configuration Area

Visible only when `Step Type = Tool`.

Contains Tool-specific authoring copy and controls/placeholders that do not present Skill, Preset, Capability, Activity, Invocation, Command, or Script as the step discriminator.

## Preset Configuration Area

Visible only when `Step Type = Preset`.

Contains existing preset-use controls:

- Preset selection.
- Feature Request / Initial Instructions.
- Visible preset inputs.
- Apply action.
- Preset status/error message.

Preset use must appear in the step editor, not as a separate canonical Create page section.

## Submission Compatibility

- Skill-specific fields are submitted only for Skill steps.
- Hidden advanced Skill fields are not submitted after switching away from Skill.
- Existing preset expansion endpoint and applied template metadata remain the source of concrete executable steps.
