# Quickstart: Calm Shimmer Motion and Reduced-Motion Fallback

## Focused Unit Validation

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts
```

Expected coverage:
- Shared Mission Control CSS preserves the MM-490 timing tokens and keyframe contract.
- The executing shimmer cadence encodes a total 1.6 to 1.8 second cycle including idle gap.
- The CSS contract proves no overlap between cycles and center-focused emphasis.
- Reduced-motion conditions disable animation and keep a static active highlight.
- MM-490 traceability is preserved in runtime-adjacent helper/test exports.

## Focused Integration Validation

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx
```

Expected coverage:
- Executing pills on task list and task detail continue to use the shared shimmer contract.
- Non-executing pills remain plain on the same surfaces.
- Reduced-motion conditions still present executing as active without animation.
- Shared shimmer behavior remains reusable across supported surfaces after MM-490 timing refinements.

## Final Unit Suite

```bash
./tools/test_unit.sh
```

Run after focused validation passes.

## Integration Strategy Note

This story does not require a compose-backed `integration_ci` suite because it is isolated frontend UI behavior. Integration evidence comes from Vitest entrypoint render tests that exercise shared Mission Control surfaces together with the shared status-pill contract.

## Independent Story Verification

Render supported Mission Control status-pill surfaces in executing, non-executing, and reduced-motion conditions. The story passes when executing pills alone use a calm left-to-right shimmer sweep with non-overlapping cadence, reduced motion receives a static active highlight, and MM-490 traceability appears in runtime-adjacent verification evidence.
