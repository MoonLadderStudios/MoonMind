# Contract: Step Type Authoring UI

## Surface

Create page step editor.

## Required Behavior

1. Each ordinary authored step exposes one `Step Type` selector.
2. The selector contains `Skill`, `Tool`, and `Preset` options.
3. Helper copy communicates:
   - Tool: run a typed integration or system operation directly.
   - Skill: ask an agent to perform work using reusable behavior.
   - Preset: insert a reusable set of configured steps.
4. Selecting a Step Type displays only that type's configuration form.
5. Shared instructions remain when Step Type changes.
6. Meaningful incompatible type-specific configuration is cleared with visible feedback or guarded by confirmation.
7. The primary selector does not use Capability, Activity, Invocation, Command, or Script as umbrella labels.

## Test Boundary

Vitest/Testing Library renders `TaskCreatePage`, interacts with the Step Type selector, and asserts visible controls, preserved instructions, discard feedback, and independent step state.
