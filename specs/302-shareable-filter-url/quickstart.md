# Quickstart: Shareable Filter URL Compatibility

## Focused Validation

Run frontend URL/filter tests:

```bash
npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx
```

Run backend execution-list query tests:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_executions.py
```

## End-To-End Story Checks

1. Open `/tasks/list?state=completed&repo=moon%2Fdemo`.
2. Confirm the list request remains `scope=tasks`, Status is filtered to `completed`, Repository is filtered exactly to `moon/demo`, and the URL normalizes away legacy-only ambiguity.
3. Open `/tasks/list?scope=system&workflowType=MoonMind.ProviderProfileManager&entry=manifest`.
4. Confirm system or manifest rows are not exposed and a recoverable message is shown.
5. Open `/tasks/list?targetRuntimeIn=codex_cli&targetRuntimeIn=claude_code`.
6. Confirm the runtime chip uses product labels while URL/API state preserves raw runtime identifiers.
7. Open `/tasks/list?stateIn=completed&stateNotIn=canceled`.
8. Confirm a clear validation error is shown instead of silently choosing include or exclude.

## Full Verification

Before final MoonSpec verification, run:

```bash
./tools/test_unit.sh
```

Then run `/moonspec-verify` equivalent against `specs/302-shareable-filter-url`.
