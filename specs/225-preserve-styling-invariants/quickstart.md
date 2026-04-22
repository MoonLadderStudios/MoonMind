# Quickstart: Mission Control Styling Source and Build Invariants

## Focused Test-First Loop

1. Add failing MM-430 tests for semantic class stability, additive modifiers, token-first semantic role styling, light/dark token parity, Tailwind source scanning, and generated dist boundary protection.

2. Run the focused tests:

```bash
npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/vite-config.test.ts
```

3. Implement the smallest source-only changes needed if the tests expose gaps:

- `frontend/src/styles/mission-control.css`
- `tailwind.config.cjs`
- source templates/components under `api_service/templates/` or `frontend/src/`

Do not hand-edit files under `api_service/static/task_dashboard/dist/`.

4. Run focused Mission Control regressions:

```bash
npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/tasks-list.test.tsx frontend/src/vite-config.test.ts
```

5. Run the repository unit wrapper before final verification when feasible:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## End-to-End Verification

The story is validated when:

- MM-430 traceability appears in `spec.md`, `tasks.md`, and `verification.md`.
- `frontend/src/entrypoints/mission-control.test.tsx` covers semantic class, modifier, token, theme parity, and generated-boundary invariants.
- `frontend/src/vite-config.test.ts` or equivalent coverage confirms Tailwind scan inputs and Vite dist boundaries.
- No generated dist files under `api_service/static/task_dashboard/dist/` are modified by hand.
- Focused Mission Control UI tests continue to pass.
