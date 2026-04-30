# Contract: Create Page Step Type Authoring Controls

Traceability: `MM-568`, FR-001 through FR-007, SC-001 through SC-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-008, DESIGN-REQ-017.

## UI Contract

Each ordinary authored step in the Create page exposes one primary control:

```text
Step Type
Skill | Tool | Preset
```

Option helper copy:

- Skill: asks an agent to perform work using reusable behavior.
- Tool: runs a typed integration or system operation directly.
- Preset: inserts a reusable set of configured steps.

## Type-Specific Visibility

When `Skill` is selected:

- Skill selector and advanced Skill fields are visible.
- Tool fields are hidden.
- Preset selection, preview, and apply controls are hidden.

When `Tool` is selected:

- Tool identifier/version/input fields or governed Tool picker are visible.
- Skill fields are hidden.
- Preset selection, preview, and apply controls are hidden.

When `Preset` is selected:

- Preset selection, input, preview, and apply controls are visible.
- Skill fields are hidden.
- Tool fields are hidden.

## Data Preservation and Submission

- Step instructions are preserved when changing Step Type.
- Hidden incompatible type-specific values are not submitted as active configuration for a different Step Type.
- Tool steps without a selected governed Tool are blocked with an operator-visible validation message.
- Preset steps must be previewed/applied into executable steps before submission.
- Preset state remains scoped to the authored step where it is configured.

## Terminology Contract

The primary discriminator uses:

- Step Type
- Tool
- Skill
- Preset

The primary discriminator must not use these as umbrella labels:

- Capability
- Activity
- Invocation
- Command
- Script

Narrow technical uses of capability outside the Step Type discriminator are not part of this UI contract.
