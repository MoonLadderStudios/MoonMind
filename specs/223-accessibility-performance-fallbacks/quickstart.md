# Quickstart: MM-429 Accessibility, Performance, and Fallback Posture

## Focused Test-First Commands

1. Add failing MM-429 CSS/UI contract tests:

```bash
npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/lib/liquidGL/useLiquidGL.test.tsx
```

If npm cannot resolve `vitest`, use:

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/lib/liquidGL/useLiquidGL.test.tsx
```

2. Implement the smallest Mission Control CSS or hook changes needed for failing MM-429 assertions.

3. Run targeted validation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/lib/liquidGL/useLiquidGL.test.tsx
```

4. Run task workflow regression coverage:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx
```

5. Run `moonspec-verify` and record results in `verification.md`.

## Expected End State

- MM-429 is preserved in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Labels, table text, placeholder text, chips, buttons, focus states, and glass-over-gradient surfaces have explicit readable-token coverage.
- Representative interactive surfaces have visible high-contrast focus states.
- Reduced-motion mode suppresses routine control motion and running/live pulse effects.
- Backdrop-filter and liquidGL fallbacks keep complete CSS shells and readable controls.
- Heavy premium effects remain limited to strategic surfaces and absent from dense reading/editing/evidence regions.
