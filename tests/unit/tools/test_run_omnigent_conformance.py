from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


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
        "tests/unit/workflows/adapters/test_external_adapter_registry.py",
        "tests/unit/workflows/temporal/test_temporal_workers.py",
        "tests/unit/workflows/temporal/workflows/test_run_bounded_story_loop.py",
    )


def test_runner_derives_group_results_from_executed_commands(
    monkeypatch, tmp_path: Path
) -> None:
    runner = _load_runner()
    monkeypatch.setattr(
        runner,
        "COMMANDS",
        (("python", "-m", "pytest", "proof-a.py"),),
    )
    monkeypatch.setattr(
        runner,
        "EVIDENCE_GROUPS",
        {"journey": ("proof-a.py",), "undeclared": ("proof-b.py",)},
    )
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        runner,
        "assert_secret_free",
        lambda _value: None,
    )
    monkeypatch.setattr(
        runner.sys,
        "argv",
        [
            "run_omnigent_conformance.py",
            "--output-dir",
            str(tmp_path),
            "--server-image",
            "server@sha256:" + "a" * 64,
            "--host-image",
            "host@sha256:" + "b" * 64,
            "--host-architecture",
            "linux/amd64",
        ],
    )

    assert runner.main() == 1
    evidence = json.loads((tmp_path / "runner-evidence.json").read_text())
    assert evidence["deterministicCoverage"]["evidenceGroupResults"]["journey"][
        "status"
    ] == "passed"
    missing = evidence["deterministicCoverage"]["evidenceGroupResults"]["undeclared"]
    assert missing == {
        "status": "failed",
        "paths": [
            {
                "path": "proof-b.py",
                "status": "failed",
                "commandIndexes": [],
            }
        ],
    }
    assert evidence["commandResults"][0]["exitCode"] == 0
    assert evidence["commandResults"][0]["logDigest"].startswith("sha256:")
