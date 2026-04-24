# Quickstart: Shared Executing Shimmer for Status Pills

## Focused Unit Validation

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts
```

Expected coverage:

- Shared status-pill CSS exposes the executing shimmer modifier contract.
- Preferred executing-state hooks and fallback executing marker resolve to the same shared modifier behavior.
- Non-executing states do not match shimmer selectors.
- Reduced-motion conditions disable animated sweep and retain a non-animated active treatment.
- CSS contract preserves existing Mission Control tokens and avoids warning/error visual reads.
- MM-488 traceability is preserved in the planned test/export surface.

## Focused Integration Validation

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx
```

Expected coverage:

- Executing pills on task list table rows and cards render the shared executing shimmer selector contract.
- Executing pills on task detail render the same shared contract.
- Text content, casing, and existing surrounding UI remain unchanged while the modifier is present.
- Non-executing pills on the same pages do not opt into the shimmer.

## Final Unit Suite

```bash
./tools/test_unit.sh
```

Run after focused validation passes.

## Integration Strategy Note

This story does not require a compose-backed `integration_ci` suite because it is isolated frontend UI behavior. Integration evidence comes from Vitest entrypoint render tests that exercise list and detail surfaces together with the shared Mission Control stylesheet.

## Independent Story Verification

Render supported Mission Control status-pill surfaces in executing, non-executing, and reduced-motion conditions. The story passes when executing pills alone opt into one shared shimmer modifier, reduced motion receives a non-animated active treatment, and the modifier does not change status text, icon choice, layout footprint, polling behavior, or live-update behavior.
