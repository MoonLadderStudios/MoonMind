# Quickstart: Edit and Rerun Attachment Reconstruction

## Focused Unit Checks

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
pytest tests/unit/api/routers/test_executions.py -q
```

Expected coverage:
- Draft reconstruction preserves objective-scoped and step-scoped persisted attachment refs from the authoritative task snapshot.
- Create-page edit/rerun state distinguishes persisted refs from new local files.
- Edit/rerun submissions include unchanged persisted refs and explicit add/remove changes.
- Backend action availability fails explicitly when original task input snapshots are missing.

## Contract Checks

```bash
pytest tests/contract/test_temporal_execution_api.py -q
```

Expected coverage:
- Task-shaped execution creation persists snapshot artifacts containing canonical `inputAttachments`.
- Snapshot artifacts preserve attachment refs in task body and compact metadata.
- Artifact links and filenames do not replace task snapshot binding.

## Full Local Verification

```bash
SPECIFY_FEATURE=202-edit-rerun-attachment-reconstruction .specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
./tools/test_integration.sh
```

Notes:
- The `SPECIFY_FEATURE` prefix is required when running Moon Spec helpers from managed PR branches that do not use the numbered feature-branch form.
- `./tools/test_unit.sh` is the required final unit-test runner.
- `./tools/test_integration.sh` requires Docker access; record the exact blocker if Docker is unavailable in the managed-agent container.
