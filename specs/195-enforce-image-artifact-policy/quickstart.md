# Quickstart: Enforce Image Artifact Storage and Policy

## Focused Test Commands

Unit/API iteration:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/unit/api/routers/test_executions.py -q
MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/unit/workflows/temporal/test_artifacts.py -q
MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/unit/api/routers/test_temporal_artifacts.py -q
```

Contract/API iteration:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/contract/test_temporal_execution_api.py -q
```

Final unit verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## End-to-End Story Checks

1. Create a completed PNG/JPEG/WebP artifact through `/api/artifacts`, then submit a task-shaped execution with that artifact in `task.inputAttachments`.
2. Confirm the execution starts only after the artifact is complete and the task snapshot preserves the attachment ref.
3. Submit refs for `image/svg+xml`, incomplete artifacts, too many artifacts, over-limit artifacts, unknown fields, and disabled policy.
4. Confirm each invalid case returns a validation error before workflow start.
5. Attempt a worker-style upload into a reserved input attachment namespace and confirm it is rejected.

## Source Coverage

- DESIGN-REQ-008: artifact-backed image storage and execution linkage.
- DESIGN-REQ-009: default allowed content types and SVG rejection.
- DESIGN-REQ-010: repeated server-side validation before completion/start.
- DESIGN-REQ-017: artifact-first authorization and reserved namespace protection.
