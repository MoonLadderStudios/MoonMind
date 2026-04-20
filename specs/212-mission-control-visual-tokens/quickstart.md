# Quickstart: Mission Control Visual Tokens and Atmosphere

## Focused UI Contract Test

```bash
npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx
```

Expected result: the shared Mission Control test file passes, including token contract, atmosphere, chrome usage, and existing app-shell regression checks.

## Final Unit Wrapper

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/mission-control.test.tsx
```

Expected result: repository unit wrapper completes with the targeted Mission Control UI tests.

## Manual Visual Check

1. Open a Mission Control route such as `/tasks` or `/tasks/new`.
2. Verify the page background has subtle violet, cyan, and warm atmospheric layers over the base background.
3. Toggle dark mode, then verify the atmosphere remains balanced and text stays readable.
4. Confirm masthead and primary panels retain glass/chrome separation without changing route behavior.
