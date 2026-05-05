# Quickstart: Shared Additive Shimmer Masks

## Prerequisites

- Run from repository root.
- Use local managed-agent mode:

```bash
export MOONMIND_FORCE_LOCAL_TESTS=1
```

Frontend dependencies are prepared by `./tools/test_unit.sh` when needed.

## Unit Strategy

Run the focused CSS contract test:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts
```

Expected coverage:
- Shared moving light-field token exists.
- Fill and border masks use the shared gradient and keyframes.
- Text mask uses the shared gradient, keyframes, and text clipping.
- Glyph brightening remains fallback-only.
- Reduced-motion and forced-colors branches disable decorative animation.
- Non-active statuses remain outside the shimmer selector contract.

## Integration Strategy

Run focused Mission Control entrypoint render tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx
```

Expected coverage:
- Task list table and card active status pills receive shimmer metadata.
- Task detail active status pill receives shimmer metadata.
- Active labels preserve text content, aria label, hidden visual glyph span, and grapheme span shape.
- Non-active statuses remain outside the shimmer selector contract.

## Full Dashboard Verification

Run the full frontend suite before closing the story:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only
```

## Type and Lint Verification

Managed workspaces with colons in their path may break npm script PATH lookup for local binaries. Use the repo-local binaries directly when that happens:

```bash
node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
node_modules/.bin/eslint -c frontend/eslint.config.mjs frontend/src
```

## End-to-End Manual Check

1. Open Mission Control with tasks that include `executing`, `planning`, and non-active statuses.
2. Confirm active pills show one coherent shimmer crossing fill, border, and text together.
3. Confirm text remains readable and labels do not shift.
4. Enable reduced motion and confirm shimmer animation stops while active status remains visually distinct.
5. Enable forced-colors or high-contrast mode and confirm decorative masks do not obscure readable text.
