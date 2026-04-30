# Quickstart: Add Step Type Authoring Controls

## Scope

Verify MM-568 against the Create page Step Type authoring controls.

## Validation Commands

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create-step-type.test.tsx
```

Focused frontend-only iteration, after dependencies are available:

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create-step-type.test.tsx
```

Traceability:

```bash
rg -n "MM-568|DESIGN-REQ-001|DESIGN-REQ-002|DESIGN-REQ-008|DESIGN-REQ-017" specs/287-add-step-type-authoring-controls
```

## Manual Verification Scenario

1. Open the Create page.
2. Inspect the first authored step.
3. Confirm there is one primary control labeled `Step Type`.
4. Confirm `Tool`, `Skill`, and `Preset` are available with concise helper copy.
5. Enter instructions.
6. Switch among Skill, Tool, and Preset.
7. Confirm instructions remain and only the matching configuration area is visible.
8. Enter Skill-specific advanced values, switch to Tool, and confirm those hidden Skill values are not submitted as active Tool configuration.
9. Confirm the Step Type selector does not use Capability, Activity, Invocation, Command, or Script as the umbrella label.
