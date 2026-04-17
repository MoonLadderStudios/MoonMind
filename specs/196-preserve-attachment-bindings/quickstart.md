# Quickstart: Preserve Attachment Bindings in Snapshots and Reruns

## Focused Unit Checks

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx
pytest tests/unit/api/routers/test_executions.py -q
```

Expected coverage:
- Draft reconstruction preserves objective-scoped and step-scoped persisted attachment refs.
- Create-page edit/rerun state distinguishes persisted refs from new local files.
- Edit/rerun submissions include unchanged persisted refs and explicit add/remove changes.
- Backend serialization exposes task input snapshot descriptors and disables edit/rerun when authoritative snapshots are missing.

## Contract Checks

```bash
pytest tests/contract/test_temporal_execution_api.py -q
```

Expected coverage:
- Task-shaped execution creation persists snapshot artifacts containing canonical `inputAttachments`.
- Snapshot artifacts preserve attachment refs in task body and compact `attachmentRefs` metadata.
- Artifact links and filenames do not replace task snapshot binding.

## Full Local Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
./tools/test_integration.sh
```

Notes:
- `./tools/test_unit.sh` is the required final unit-test runner.
- `./tools/test_integration.sh` requires Docker access; record the exact blocker if Docker is unavailable in the managed-agent container.
