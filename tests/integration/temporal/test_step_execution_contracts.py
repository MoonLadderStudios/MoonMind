from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


BOUNDARY_PATHS = (
    Path("moonmind/schemas/step_execution_models.py"),
    Path("moonmind/schemas/temporal_models.py"),
    Path("moonmind/schemas/agent_runtime_models.py"),
    Path("moonmind/workflows/temporal/step_executions.py"),
    Path("moonmind/workflows/temporal/step_checkpoints.py"),
    Path("moonmind/workflows/temporal/step_ledger.py"),
    Path("moonmind/workflows/temporal/workflows/run.py"),
    Path("moonmind/workflows/tasks/task_contract.py"),
    Path("docs/Steps/StepExecutionsAndCheckpointing.md"),
)


FORBIDDEN_SELECTED_CONTRACT_TERMS = (
    "Step" + "Attempt",
    "step" + "AttemptId",
    "step-" + "attempt",
    "resume_from_failed" + "_step",
    "continue_from_previous" + "_attempt",
    "restore_pre" + "_attempt",
    "apply_previous_diff_to_clean" + "_baseline",
)


REQUIRED_SELECTED_CONTRACT_TERMS = (
    "StepExecution",
    "stepExecutionId",
    "executionOrdinal",
    "step-execution",
    "recover_from_failed_step",
    "previous_execution",
)


def test_step_execution_boundary_contracts_do_not_reintroduce_stale_names() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in BOUNDARY_PATHS)

    for forbidden in FORBIDDEN_SELECTED_CONTRACT_TERMS:
        assert forbidden not in combined

    for required in REQUIRED_SELECTED_CONTRACT_TERMS:
        assert required in combined
