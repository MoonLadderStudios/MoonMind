# Quickstart: Show Attachment and Recovery Diagnostics By Target

Use Jira issue `MM-635` and the canonical Jira preset brief preserved in `spec.md` as the source of truth.

## Test-First Plan

1. Add backend unit tests in `tests/unit/api/routers/test_executions.py` that build execution records with objective and step attachment evidence, generated context refs, attachment failure diagnostics, Resume provenance, and degraded evidence.
2. Add frontend unit tests in `frontend/src/entrypoints/task-detail.test.tsx` that render:
   - objective and step target groups,
   - targets with no attachments,
   - manifest and generated context refs,
   - upload, validation, materialization, and context-generation failure phases,
   - resumed execution source and preserved prior steps,
   - failed Resume phase labels.
3. Add or extend integration evidence for target-aware generated context artifacts and failed-step Resume preservation.
4. Confirm the new tests fail before implementation, then implement the projection/UI changes.

## Unit Test Commands

Focused backend:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_executions.py
```

Focused frontend:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx
```

Final unit verification:

```bash
./tools/test_unit.sh
```

## Integration Test Commands

Focused hermetic integration candidates:

```bash
pytest tests/integration/vision/test_context_artifacts.py -m 'integration_ci' -q --tb=short
pytest tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py -q --tb=short
```

Final hermetic integration verification when Docker is available:

```bash
./tools/test_integration.sh
```

## End-To-End Validation

1. Create or fixture a task with objective-scoped and step-scoped attachments.
2. Ensure task detail shows each attachment under its owning objective or step target.
3. Ensure manifest and generated context refs are visible by target when available.
4. Simulate attachment failures for upload, validation, materialization, and context generation; verify each failure shows one target and one bounded phase.
5. Simulate a resumed execution; verify the source workflow/run and preserved prior steps are visible.
6. Simulate failed Resume diagnostics; verify the displayed phase is checkpoint validation, workspace restoration, preserved-output injection, or failed-step execution.
7. Confirm raw diagnostics remain available but are not required for the above checks.
