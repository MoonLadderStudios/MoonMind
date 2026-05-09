# Quickstart: Validate Define Canonical Task-Shaped Contract

**Feature**: 330-define-canonical-task-contract (MM-638)

## Prerequisites

```bash
# Ensure Python unit test deps are available
./tools/test_unit.sh --help
```

## Run Unit Tests

```bash
# Run the full unit suite (verifies no regressions)
./tools/test_unit.sh

# Run only task_contract tests for fast iteration
MOONMIND_FORCE_LOCAL_TESTS=1 python -m pytest tests/unit/workflows/tasks/test_task_contract.py -v
```

## Acceptance Scenario Validation

The following snippets reproduce the key acceptance scenarios from the spec using the Python REPL or pytest.

### SC-001: Well-formed resume_from_failed_step payload accepted

```python
from moonmind.workflows.tasks.task_contract import build_canonical_task_view

payload = {
    "repository": "test/repo",
    "task": {
        "instructions": "Resume the failed task",
        "recovery": {
            "kind": "resume_from_failed_step",
            "sourceWorkflowId": "mm:abc123",
            "sourceRunId": "run-1",
        },
        "resume": {
            "kind": "resume_from_failed_step",
            "sourceWorkflowId": "mm:abc123",
            "sourceRunId": "run-1",
            "failedStepId": "step-3",
            "resumeCheckpointRef": "art_ckpt_abc",
            "taskInputSnapshotRef": "art_snap_abc",
        },
    },
}
result = build_canonical_task_view(job_type="task", payload=payload)
assert result["task"]["recovery"]["kind"] == "resume_from_failed_step"
assert result["task"]["resume"]["failedStepId"] == "step-3"
print("SC-001 PASS")
```

### SC-002: Missing resume block with resume_from_failed_step → error

```python
from moonmind.workflows.tasks.task_contract import TaskContractError
import pytest

try:
    build_canonical_task_view(job_type="task", payload={
        "repository": "test/repo",
        "task": {
            "instructions": "Resume",
            "recovery": {"kind": "resume_from_failed_step", "sourceWorkflowId": "mm:x", "sourceRunId": "r1"},
        },
    })
    assert False, "Expected error"
except TaskContractError as e:
    assert "resume" in str(e).lower()
    print("SC-002 PASS:", e)
```

### SC-003: resume block without matching recovery.kind → error

```python
try:
    build_canonical_task_view(job_type="task", payload={
        "repository": "test/repo",
        "task": {
            "instructions": "Retry",
            "recovery": {"kind": "exact_full_rerun", "sourceWorkflowId": "mm:x", "sourceRunId": "r1"},
            "resume": {
                "kind": "resume_from_failed_step",
                "sourceWorkflowId": "mm:x",
                "sourceRunId": "r1",
                "failedStepId": "step-3",
                "resumeCheckpointRef": "art_ckpt",
                "taskInputSnapshotRef": "art_snap",
            },
        },
    })
    assert False, "Expected error"
except TaskContractError as e:
    print("SC-003 PASS:", e)
```

### SC-004: exact_full_rerun accepted

```python
result = build_canonical_task_view(job_type="task", payload={
    "repository": "test/repo",
    "task": {
        "instructions": "Rerun from scratch",
        "recovery": {"kind": "exact_full_rerun", "sourceWorkflowId": "mm:x", "sourceRunId": "r1"},
    },
})
assert result["task"]["recovery"]["kind"] == "exact_full_rerun"
assert result["task"].get("resume") is None
print("SC-004 PASS")
```

### SC-005: dependsOn preserved verbatim

```python
result = build_canonical_task_view(job_type="task", payload={
    "repository": "test/repo",
    "task": {
        "instructions": "Dependent task",
        "dependsOn": ["mm:workflow-1", "mm:workflow-2"],
    },
})
assert result["task"]["dependsOn"] == ["mm:workflow-1", "mm:workflow-2"]
print("SC-005 PASS")
```

### SC-006: targetBranch not in canonical output

```python
result = build_canonical_task_view(job_type="task", payload={
    "repository": "test/repo",
    "task": {
        "instructions": "Push to branch",
        "git": {"targetBranch": "feature/my-branch"},
    },
})
git = result["task"]["git"]
assert git.get("branch") == "feature/my-branch"
assert "targetBranch" not in git or git.get("targetBranch") is None
print("SC-006 PASS")
```

## Expected Test Output

All new tests in `test_task_contract.py` covering FR-001 through FR-013 should pass. No existing tests should regress. Run:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 python -m pytest tests/unit/workflows/tasks/test_task_contract.py -v --tb=short 2>&1 | tail -20
```
