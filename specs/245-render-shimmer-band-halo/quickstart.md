# Quickstart: Themed Shimmer Band and Halo Layers

## Focused Unit Validation

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts
```

Expected coverage:

- The executing shimmer preserves the base running-pill appearance as an additive treatment.
- The shimmer CSS defines a distinct bright band and a wider dimmer halo rather than a single flat overlay.
- The shimmer derives its color roles from existing MoonMind theme tokens.
- The shimmer exposes reusable effect tokens or equivalent variables for band, halo, and related tunable values.
- MM-489 traceability is preserved in implementation or test-facing artifacts.

## Focused Integration Validation

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx
```

Expected coverage:

- Executing pills on task list and task detail surfaces render the MM-489 shimmer treatment without changing visible text.
- Non-executing pills on those same surfaces remain plain.
- The layered treatment remains bounded to the existing pill footprint and does not require layout-changing wrappers.
- The shared status-pill rendering path continues to use the same selector contract while proving MM-489-specific behavior.

## Final Unit Suite

```bash
./tools/test_unit.sh
```

Run after focused validation passes.

## Integration Strategy Note

This story is isolated frontend UI behavior, so integration evidence comes from Vitest entrypoint render tests rather than compose-backed `integration_ci` coverage. The explicit integration strategy for MM-489 is to exercise list and detail surfaces together with the shared Mission Control stylesheet and treat any failing proof as an implementation contingency.

## Independent Story Verification

Render supported Mission Control executing status pills in light and dark themes, then verify the existing base appearance remains visible while a bright diagonal band and wider dimmer halo communicate active progress. The story passes when executing pills alone show the layered treatment, text remains readable, the effect stays bounded to the pill, reusable effect tokens or equivalent variables exist, and MM-489 appears in downstream verification evidence.
