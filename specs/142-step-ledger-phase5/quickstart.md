# Quickstart: Step Ledger Phase 5

## 1. Run the workflow-boundary review/check tests

```bash
pytest tests/unit/workflows/temporal/workflows/test_run_step_ledger.py -q
```

## 2. Run the targeted Mission Control step-detail tests

```bash
npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx
```

## 3. Run the Spec Kit task-scope gate

```bash
.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
```

## 4. Run the required final verification path

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx tests/unit/workflows/temporal/workflows/test_run_step_ledger.py
```

## Expected verification

- Eligible steps enter `reviewing` during approval-policy review.
- Final step rows expose structured `approval_policy` checks with retry counts and artifact refs.
- Full review request/verdict bodies stay artifact-backed rather than inflating workflow state.
- Mission Control renders verdict badges, retry counts, and review artifact refs inside the Checks section.
