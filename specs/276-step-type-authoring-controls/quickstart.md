# Quickstart: Step Type Authoring Controls

## Focused frontend validation

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

## Managed unit validation

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

## Full unit validation

```bash
./tools/test_unit.sh
```

## Manual verification

1. Open the Create page.
2. Confirm Step 1 shows one `Step Type` control with Tool, Skill, and Preset.
3. Select Skill and confirm the Skill selector is visible.
4. Select Tool and confirm Skill and Preset controls are hidden while instructions remain.
5. Select Preset and confirm preset selection and Apply controls are shown inside the step editor.
6. Confirm the canonical Create page authoring sections no longer include a separate Task Presets section.
