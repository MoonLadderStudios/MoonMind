from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools/run_omnigent_conformance.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_omnigent_conformance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cumulative_remediation_case_is_backed_by_production_boundary_tests() -> None:
    runner = _load_runner()

    assert "product.cumulative-remediation" in runner.DETERMINISTIC_CASES
    assert runner.EVIDENCE_GROUPS["cumulativeJourney"] == (
        "tests/integration/reliability/test_checkpoint_cold_resume.py",
        "tests/unit/workflows/temporal/test_remediation_workspace_head.py",
        "tests/unit/workflows/temporal/workflows/test_run_integration.py",
    )
    flattened_commands = {
        argument
        for command in runner.COMMANDS
        for argument in command
        if isinstance(argument, str)
    }
    for path in runner.EVIDENCE_GROUPS["cumulativeJourney"]:
        assert path in flattened_commands


def test_3480_report_declares_failure_rollout_and_parent_linkage() -> None:
    runner = _load_runner()

    assert runner.ISSUE_LINKS == (
        "MoonLadderStudios/MoonMind#3480",
        "MoonLadderStudios/MoonMind#3471",
        "MoonLadderStudios/MoonMind#3456",
    )
    assert runner.EVIDENCE_GROUPS["failureAndRestartMatrix"] == (
        "tests/integration/omnigent/test_embedded_recovery.py",
    )
    assert runner.EVIDENCE_GROUPS["rolloutAndReplay"] == (
        "tests/unit/workflows/temporal/test_report_workflow_rollout.py",
        "tests/unit/workflows/temporal/workflows/test_run_bounded_story_loop.py",
    )
