# Quickstart: Step Ledger Phase 4

## 1. Run targeted task-detail browser tests

```bash
npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx
```

## 2. Run frontend type-checking

```bash
npm run ui:typecheck
```

## 3. Run the Spec Kit task-scope gate

```bash
.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
```

## 4. Run the required final verification path

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx
```

## Expected verification

- Task detail renders Steps above Timeline and Artifacts.
- Latest-run step rows come from `/api/executions/{workflowId}/steps`.
- Expanding a row fetches step-scoped observability only when `taskRunId` exists.
- Delayed `taskRunId` arrival upgrades an expanded row from waiting copy to observability-backed panels.
