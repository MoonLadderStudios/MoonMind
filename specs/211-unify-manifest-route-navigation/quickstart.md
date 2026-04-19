# Quickstart: Unify Manifest Route And Navigation

## Focused Validation

```bash
./tools/test_unit.sh --python-only --no-xdist tests/unit/api/routers/test_task_dashboard.py
npm run ui:test -- frontend/src/entrypoints/manifests.test.tsx
```

## Manual Smoke Path

1. Start Mission Control.
2. Open `/tasks/manifests`.
3. Confirm the navigation contains `Manifests` and does not contain `Manifest Submit`.
4. Confirm the page contains `Run Manifest` and `Recent Runs`.
5. Open `/tasks/manifests/new` and confirm it redirects to `/tasks/manifests`.
6. Submit a registry manifest run or inline YAML run.
7. Confirm the page stays on `/tasks/manifests`, reports the started run, and refreshes recent runs.

## Full Validation

```bash
./tools/test_unit.sh
```
