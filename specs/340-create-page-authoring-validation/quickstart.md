# Quickstart: Create Page Authoring Validation

## Preconditions

- JavaScript dependencies are installed from `package-lock.json`.
- Python dependencies are available in the managed agent environment.
- No external provider credentials are required for the planned unit tests.
- Hermetic integration tests use local/compose-backed dependencies only.

## Test-First Workflow

1. Add or update frontend unit tests in `frontend/src/entrypoints/task-create.test.tsx` that first fail because Repository, Branch, and Publish Mode are not inside the Steps card.
2. Add or update frontend unit tests proving invalid repository/runtime/publish/branch/dependency/attachment drafts are blocked before `/api/executions` after the controls move.
3. Add or update a combined valid submission test proving `task.git.branch`, `task.publish.mode`, attachments, dependencies, authored presets, applied templates, and Jira provenance survive submission.
4. Add backend unit or integration tests only if implementation changes touch `api_service/api/routers/executions.py` or execution-visible payload shaping.
5. Implement the Create page layout and any necessary validation adjustments.
6. Run focused tests, then final unit verification.

## Focused Frontend Commands

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

or through the repository unit runner:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

## Backend Unit Strategy

Use backend unit tests if payload normalization or API validation changes:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_executions.py
```

Expected evidence:
- `payload.task.git.targetBranch` remains rejected where canonical task-shaped payloads require `task.git.branch`.
- `authoredPresets`, `appliedStepTemplates`, attachments, dependencies, runtime, publish mode, and branch remain preserved.

## Integration Strategy

Run hermetic integration tests when backend payload or snapshot behavior changes:

```bash
./tools/test_integration.sh
```

Targeted local iteration, when available:

```bash
pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -m integration_ci -q --tb=short
pytest tests/integration/api/test_task_contract_normalization.py -m integration_ci -q --tb=short
```

Expected evidence:
- Execution-visible task parameters contain `task.git.branch` and no legacy `targetBranch` for new task-shaped submissions.
- Task input snapshots preserve attachment refs, provenance, dependencies, publish mode, runtime, and branch.

## End-to-End Story Validation

Manual or automated story validation should confirm:
- Repository, Branch, and Publish Mode are visible inside the Steps card.
- Publish Mode semantics are unchanged after visual relocation.
- Invalid drafts are blocked before submission.
- Valid drafts produce canonical task-shaped payloads preserving authored intent.
- `MM-641`, the original Jira preset brief, local DESIGN-REQ-001 through DESIGN-REQ-005, and original Jira coverage ID DESIGN-REQ-007 remain traceable in MoonSpec artifacts and final verification evidence.
