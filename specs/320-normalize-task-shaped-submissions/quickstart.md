# Quickstart: Normalize Task-Shaped Submissions

## Goal

Verify MM-627 by proving create, edit, and rerun task submissions normalize into the canonical task-shaped contract while preserving explicit attachment targets and authored metadata.

## Focused Unit Iteration

Frontend create/edit/rerun payload shaping:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Backend API normalization and validation:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_executions.py
```

Expected additions:
- tests that preserve objective and step attachments across create, edit, and rerun submissions
- tests that prove new task-shaped output uses `task.git.branch` and does not emit `targetBranch`
- tests that preserve Jira provenance, authored preset bindings, applied template metadata, step identity/order, dependencies, runtime, and publish mode
- negative tests for invalid repository, runtime, publish mode, dependencies, attachment policy, missing targets, unknown targets, and ambiguous target bindings

## Integration Verification

Run hermetic integration CI after unit coverage and implementation:

```bash
./tools/test_integration.sh
```

Expected integration focus:
- execution creation receives the backend-normalized canonical task payload
- artifact-backed original task input snapshots preserve objective and step attachment refs
- edit and rerun flows preserve snapshot-backed attachment targets
- execution-visible task data does not contain binary payload text or legacy target branch output for new task-shaped submissions

## Final Unit Suite

Before MoonSpec verification:

```bash
./tools/test_unit.sh
```

## Traceability Check

```bash
rg -n "MM-627|DESIGN-REQ-001|DESIGN-REQ-003|DESIGN-REQ-006|DESIGN-REQ-008|DESIGN-REQ-011|DESIGN-REQ-025" specs/320-normalize-task-shaped-submissions
```

Expected result:
- MM-627 and the original preset brief remain preserved in `spec.md`
- all listed source design IDs remain mapped through `spec.md`, `plan.md`, tasks, and final verification evidence
