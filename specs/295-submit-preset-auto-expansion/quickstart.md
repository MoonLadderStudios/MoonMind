# Quickstart: Submit Preset Auto-Expansion

## Goal

Validate that the Create page can submit drafts with unresolved Preset steps by expanding them automatically into executable Tool/Skill steps during an explicit Create, Update, or Rerun submit attempt.

## Prerequisites

- Repository dependencies installed.
- Frontend dependencies available through `npm ci --no-fund --no-audit` when `node_modules` is missing or stale.
- Managed-agent local test mode:

```bash
export MOONMIND_FORCE_LOCAL_TESTS=1
```

## Test-first Validation Plan

1. Add failing Vitest coverage in `frontend/src/entrypoints/task-create.test.tsx`.
2. Add or preserve pytest task-contract coverage in `tests/unit/workflows/tasks/test_task_contract.py`.
3. Implement the Create-page submit-time expansion path.
4. Run focused validation.
5. Run the full required unit suite.
6. Run hermetic integration when API/task-contract boundaries change.

## Focused Frontend Tests

Run focused Create-page tests:

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

or through the repository unit runner:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Required frontend scenarios:
- Successful Create submit with one unresolved Preset auto-expands and submits only Tool/Skill steps.
- Successful Update or Rerun submit uses the same expansion path.
- Three unresolved Presets expand in authored order.
- Expansion failure blocks final submission, creates no side effect, and preserves the draft.
- Duplicate clicks during expansion produce no duplicate create/update/rerun side effect.
- Stale or cancelled expansion response is ignored.
- Manual Preview and Apply still work.
- Non-submit interactions do not auto-expand or submit.
- Ambiguous attachment retargeting blocks auto-submission for manual review.
- Returned Preset provenance is preserved in generated executable steps.

## Focused Python Contract Tests

Run task-contract tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only -- tests/unit/workflows/tasks/test_task_contract.py
```

Required contract scenarios:
- `type: "preset"` remains rejected at the authoritative task contract boundary.
- Flat generated Tool/Skill steps with Preset provenance remain accepted.
- Mixed or stale incompatible executable payloads remain rejected.

## Full Unit Verification

Before finalizing implementation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Hermetic Integration Verification

Run when the implementation changes API routes, task contract normalization, artifact handling, or execution submission boundaries:

```bash
./tools/test_integration.sh
```

The expected integration focus is that create/update/rerun submissions never execute unresolved Preset steps and task input artifacts preserve executable-only payload shape.

## End-to-End Acceptance Check

1. Open `/tasks/new`.
2. Author a task with a Preset step and valid Preset inputs.
3. Do not click manual Preview or Apply.
4. Click Create.
5. Confirm the UI shows expansion progress and then normal creation progress.
6. Inspect the submitted request or resulting task input snapshot.
7. Confirm the final task has only Tool/Skill steps and preserves Preset provenance.
8. Repeat with an expansion failure and confirm no task is created and the visible draft remains editable.
