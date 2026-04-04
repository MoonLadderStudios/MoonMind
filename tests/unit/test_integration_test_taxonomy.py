from __future__ import annotations

import importlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _marker_names(module_name: str) -> set[str]:
    module = importlib.import_module(module_name)
    pytestmark = getattr(module, "pytestmark", [])
    if not isinstance(pytestmark, list):
        pytestmark = [pytestmark]
    return {mark.name for mark in pytestmark}


def test_pytest_registers_phase1_taxonomy_markers() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "integration_ci:" in pyproject
    assert "provider_verification:" in pyproject
    assert "jules:" in pyproject
    assert "requires_credentials:" in pyproject


def test_ci_safe_temporal_artifact_and_topology_modules_are_marked_for_integration_ci() -> None:
    expected_modules = (
        "tests.integration.temporal.test_temporal_artifact_local_dev",
        "tests.integration.temporal.test_temporal_artifact_auth_preview",
        "tests.integration.temporal.test_temporal_artifact_lifecycle",
        "tests.integration.temporal.test_activity_worker_topology",
    )

    for module_name in expected_modules:
        marker_names = _marker_names(module_name)
        assert "integration_ci" in marker_names
        assert "integration" in marker_names


def test_live_jules_suite_is_provider_verification_only() -> None:
    marker_names = _marker_names("tests.integration.test_jules_integration")

    assert "provider_verification" in marker_names
    assert "jules" in marker_names
    assert "requires_credentials" in marker_names
    assert "integration_ci" not in marker_names
    assert "integration" not in marker_names


def test_shell_and_powershell_runner_commands_select_new_taxonomy() -> None:
    shell_runner = (REPO_ROOT / "tools" / "test_integration.sh").read_text(
        encoding="utf-8"
    )
    provider_runner = (REPO_ROOT / "tools" / "test_jules_provider.sh").read_text(
        encoding="utf-8"
    )
    powershell_runner = (REPO_ROOT / "tools" / "test-integration.ps1").read_text(
        encoding="utf-8"
    )

    assert 'integration_ci' in shell_runner
    assert 'provider_verification and jules' in provider_runner
    assert 'integration_ci' in powershell_runner
