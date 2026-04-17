# Quickstart: Inject Attachment Context Into Runtimes

## Focused Unit Validation

Run:

```bash
./tools/test_unit.sh tests/unit/agents/codex_worker/test_worker.py tests/unit/agents/codex_worker/test_attachment_materialization.py
```

Expected:
- Step instruction composition includes `INPUT ATTACHMENTS` before `WORKSPACE`.
- Objective and current-step manifest/context entries are present.
- Non-current step workspace and context paths are omitted.
- Planning inventory is compact.
- Raw bytes and data URLs are not embedded.

## Full Unit Validation

Run before final completion when feasible:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Integration Validation

No Docker-backed integration command is required for this story because no Temporal workflow/activity contract or external service boundary changes. If Docker is available, the standard hermetic suite remains:

```bash
./tools/test_integration.sh
```
