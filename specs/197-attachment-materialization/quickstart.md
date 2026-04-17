# Quickstart: Materialize Attachment Manifest and Workspace Files

## Focused Unit Validation

Run the new attachment materialization tests:

```bash
./tools/test_unit.sh tests/unit/agents/codex_worker/test_attachment_materialization.py
```

Expected coverage:
- objective attachments write under `.moonmind/inputs/objective/`
- step attachments write under `.moonmind/inputs/steps/<stepRef>/`
- manifest entries include canonical fields
- unsafe filenames are sanitized
- stable fallback step refs are assigned
- failed downloads stop prepare

## Full Unit Validation

Run the required unit suite before final verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Hermetic Integration Validation

When Docker is available, run the required hermetic integration suite:

```bash
./tools/test_integration.sh
```

## Manual Story Check

1. Submit or construct a task-shaped payload with both `task.inputAttachments` and `task.steps[n].inputAttachments`.
2. Run worker prepare for the task.
3. Confirm `.moonmind/attachments_manifest.json` exists in the repo workspace.
4. Confirm objective files exist under `.moonmind/inputs/objective/`.
5. Confirm step files exist under `.moonmind/inputs/steps/<stepRef>/`.
6. Confirm a missing artifact fails prepare and prevents runtime execution.

## Traceability

Confirm `MM-370` is preserved in:
- `spec.md`
- `plan.md`
- `tasks.md`
- `verification.md`
- commit text and pull request metadata when delivery metadata is created
