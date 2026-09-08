from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools/run_omnigent_conformance.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_omnigent_conformance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cumulative_remediation_stays_pending_until_production_shaped_proof() -> None:
    runner = _load_runner()

    assert "product.cumulative-remediation" not in runner.DETERMINISTIC_CASES
    assert runner.PENDING_PRODUCTION_SHAPED_CASES == {
        "product.cumulative-remediation"
    }
    assert runner.PENDING_EVIDENCE_GROUPS == {"cumulativeJourney"}
    assert runner.EVIDENCE_GROUPS["cumulativeJourney"] == (
        "tests/integration/reliability_journey/"
        "test_omnigent_cumulative_remediation_journey.py",
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
        "tests/integration/reliability_journey/"
        "test_omnigent_cumulative_remediation_journey.py",
        "tests/integration/omnigent/test_embedded_recovery.py",
    )
    assert runner.EVIDENCE_GROUPS["rolloutAndReplay"] == (
        "tests/unit/workflows/adapters/test_external_adapter_registry.py",
        "tests/unit/workflows/temporal/test_temporal_workers.py",
        "tests/unit/workflows/temporal/workflows/test_run_bounded_story_loop.py",
        "frontend/src/entrypoints/workflow-detail.test.tsx",
    )
    flattened_commands = {
        argument
        for command in runner.COMMANDS
        for argument in command
        if isinstance(argument, str)
    }
    for group in ("failureAndRestartMatrix", "rolloutAndReplay"):
        for path in runner.EVIDENCE_GROUPS[group]:
            assert path in flattened_commands


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
    monkeypatch.setattr(runner, "PENDING_EVIDENCE_GROUPS", {"journey"})
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
    assert evidence["deterministicCoverage"][
        "pendingProductionShapedCaseIds"
    ] == ["product.cumulative-remediation"]
    cumulative = next(
        case
        for case in evidence["cases"]
        if case["caseId"] == "product.cumulative-remediation"
    )
    assert cumulative["status"] == "skipped"
    assert evidence["deterministicCoverage"]["evidenceGroupResults"]["journey"][
        "status"
    ] == "pending"
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


def test_fake_provider_activation_via_cli_flag_is_recorded() -> None:
    runner = _load_runner()
    selection = runner.resolve_fake_provider_selection(
        cli_flag=True, env={}, auth_mode="deterministic-fake"
    )
    assert selection == {
        "requested": True,
        "engaged": True,
        "authMode": "deterministic-fake",
        "source": "cli",
    }


def test_fake_provider_activation_via_env_var_is_recorded() -> None:
    runner = _load_runner()
    selection = runner.resolve_fake_provider_selection(
        cli_flag=False,
        env={runner.FAKE_PROVIDER_ENV: "1"},
        auth_mode="deterministic-fake",
    )
    assert selection["requested"] is True
    assert selection["engaged"] is True
    assert selection["source"] == "env"


def test_fake_provider_not_requested_stays_disengaged() -> None:
    runner = _load_runner()
    selection = runner.resolve_fake_provider_selection(
        cli_flag=False, env={}, auth_mode="credentialed-live"
    )
    assert selection == {
        "requested": False,
        "engaged": False,
        "authMode": "credentialed-live",
    }


def test_fake_provider_requires_deterministic_fake_auth_mode() -> None:
    runner = _load_runner()
    with pytest.raises(runner.ConformanceContractError):
        runner.resolve_fake_provider_selection(
            cli_flag=True, env={}, auth_mode="credentialed-live"
        )


def test_main_records_fake_provider_selection_in_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    runner = _load_runner()
    monkeypatch.setattr(
        runner, "COMMANDS", (("python", "-m", "pytest", "proof-a.py"),)
    )
    monkeypatch.setattr(runner, "EVIDENCE_GROUPS", {})
    monkeypatch.setattr(runner, "PENDING_EVIDENCE_GROUPS", set())
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(runner, "assert_secret_free", lambda _value: None)
    monkeypatch.delenv(runner.FAKE_PROVIDER_ENV, raising=False)
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
            "--fake-provider",
        ],
    )

    runner.main()
    evidence = json.loads((tmp_path / "runner-evidence.json").read_text())
    assert evidence["fakeProvider"] == {
        "requested": True,
        "engaged": True,
        "authMode": "deterministic-fake",
        "source": "cli",
    }


def test_main_fails_fast_when_fake_provider_auth_mode_mismatches(
    monkeypatch, tmp_path: Path
) -> None:
    runner = _load_runner()
    # No layer should run when activation is rejected up front.
    ran: list[object] = []
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: ran.append(args) or SimpleNamespace(returncode=0),
    )
    monkeypatch.delenv(runner.FAKE_PROVIDER_ENV, raising=False)
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
            "--auth-mode",
            "credentialed-live",
            "--fake-provider",
        ],
    )

    assert runner.main() == 1
    assert ran == []
    assert not (tmp_path / "runner-evidence.json").exists()
