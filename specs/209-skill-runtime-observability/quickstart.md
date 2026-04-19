# Quickstart: Skill Runtime Observability and Verification

## Focused Development Loop

1. Run focused backend tests for execution detail serialization:

```bash
python -m pytest tests/unit/api/routers/test_executions.py -q
```

2. Run focused materialization regression tests:

```bash
python -m pytest tests/unit/services/test_skill_materialization.py -q
```

3. Run focused task-detail UI tests after JS dependencies are prepared:

```bash
npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx
```

Or route the UI test through the repo unit runner:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx
```

4. Final unit verification:

```bash
./tools/test_unit.sh
```

## End-to-End Story Check

1. Submit or simulate a skill-enabled execution with task skill selectors and materialization metadata.
2. Open the task detail API response or Mission Control task detail view.
3. Confirm `MM-408` behavior:
   - resolved snapshot ID is visible when present,
   - selected skills and versions are visible when present,
   - source provenance and materialization mode are visible when present,
   - visible path, backing path, manifest ref, and prompt-index ref are visible when present,
   - no full skill body text appears in the response or UI,
   - projection diagnostics include path, object kind, attempted action, and remediation.
4. Verify proposal, schedule, rerun, retry, or replay metadata makes skill intent or snapshot reuse explicit.
5. Confirm boundary-level tests prove single-skill projection, multi-skill projection, read-only materialization, activation summary injection, collision failure, exact-snapshot replay, and repo-skill input without in-place mutation.

## Integration Verification

Run hermetic integration only if implementation changes cross a compose-backed route or Temporal worker boundary:

```bash
./tools/test_integration.sh
```

Provider verification is not required for this story.
