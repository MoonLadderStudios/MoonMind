# Quickstart: Shimmer Quality Regression Guardrails

## Focused Unit Validation

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts
```

Expected coverage:
- Shared Mission Control CSS proves the executing shimmer remains clipped to rounded pill bounds.
- The shared shimmer contract preserves text-priority and non-layout-changing guardrails.
- The state-matrix coverage set keeps shimmer activation executing-only.
- Reduced-motion conditions disable animation and preserve a static active fallback.
- MM-491 traceability is preserved in runtime-adjacent helper/test exports.

## Focused Integration Validation

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx
```

Expected coverage:
- Executing pills on task list and task detail continue to use the shared shimmer contract.
- The listed non-executing states remain plain on supported surfaces.
- Theme-aware assertions confirm the executing shimmer still reads as an intentional active treatment.
- Reduced-motion conditions still present executing as active without animation.
- Layout-focused assertions confirm activating the shimmer does not shift pill or surrounding surface layout.
- Regression assertions confirm MM-491 still validates the existing shimmer treatment rather than accepting an unrelated replacement effect family.

## Final Unit Suite

```bash
./tools/test_unit.sh
```

Run after focused validation passes.

## Integration Strategy Note

This story does not require a compose-backed `integration_ci` suite because it is isolated frontend UI behavior. Integration evidence comes from Vitest entrypoint render tests that exercise shared Mission Control surfaces together with the shared status-pill contract.

## Independent Story Verification

Render supported Mission Control status-pill surfaces in executing, every listed non-executing state, light and dark themes, and reduced-motion conditions. The story passes when executing pills remain readable, bounded, and layout-stable, non-executing pills remain plain, reduced motion preserves an active fallback without animation, the shared shimmer model remains the effect under test, and MM-491 traceability appears in runtime-adjacent verification evidence.
