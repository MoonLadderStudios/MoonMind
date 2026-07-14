from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

COMPOSE_TEST_RUNNERS = (
    "tools/test_integration.sh",
    "tools/test_unit_docker.sh",
    "tools/test_jules_provider.sh",
    "tools/test-unit.ps1",
    "tools/test-integration.ps1",
    "tools/test-e2e.ps1",
    "tools/test-provider.ps1",
)


def _module_path(module_name: str) -> Path:
    return REPO_ROOT / Path(*module_name.split(".")).with_suffix(".py")


def _marker_names_from_node(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        marker_names: set[str] = set()
        for element in node.elts:
            marker_names.update(_marker_names_from_node(element))
        return marker_names

    if isinstance(node, ast.Call):
        return _marker_names_from_node(node.func)

    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
        and node.value.attr == "mark"
    ):
        return {node.attr}

    return set()


def _marker_names(module_name: str) -> set[str]:
    module_ast = ast.parse(_module_path(module_name).read_text(encoding="utf-8"))

    marker_names: set[str] = set()
    for statement in module_ast.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
            value = statement.value
        else:
            continue

        if value is None:
            continue

        if any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in targets
        ):
            marker_names.update(_marker_names_from_node(value))

    return marker_names


def test_pytest_registers_phase1_taxonomy_markers(pytestconfig) -> None:
    registered_markers = {
        marker.split(":", 1)[0].strip() for marker in pytestconfig.getini("markers")
    }

    assert "integration_ci" in registered_markers
    assert "provider_verification" in registered_markers
    assert "jules" in registered_markers
    assert "requires_credentials" in registered_markers


def test_ci_safe_temporal_artifact_and_topology_modules_are_marked_for_integration_ci() -> (
    None
):
    expected_modules = (
        "tests.integration.temporal.test_temporal_artifact_local_dev",
        "tests.integration.temporal.test_temporal_artifact_auth_preview",
        "tests.integration.temporal.test_temporal_artifact_lifecycle",
        "tests.integration.temporal.test_temporal_artifact_authorization",
        "tests.integration.temporal.test_activity_worker_topology",
    )

    for module_name in expected_modules:
        marker_names = _marker_names(module_name)
        assert "integration_ci" in marker_names
        assert "integration" in marker_names


def test_live_jules_suite_is_provider_verification_only() -> None:
    marker_names = _marker_names("tests.provider.jules.test_jules_integration")

    assert "provider_verification" in marker_names
    assert "jules" in marker_names
    assert "requires_credentials" in marker_names
    assert "integration_ci" not in marker_names
    assert "integration" not in marker_names


def test_provider_verification_suite_lives_outside_tests_integration() -> None:
    assert not (
        REPO_ROOT / "tests" / "integration" / "test_jules_integration.py"
    ).exists()
    assert (
        REPO_ROOT / "tests" / "provider" / "jules" / "test_jules_integration.py"
    ).exists()


def test_temporal_topology_integration_suite_stays_jules_free() -> None:
    topology_module = (
        REPO_ROOT
        / "tests"
        / "integration"
        / "temporal"
        / "test_activity_worker_topology.py"
    ).read_text(encoding="utf-8")

    assert "integration.jules." not in topology_module
    assert "JulesTaskResponse" not in topology_module
    assert "_FakeJulesClient" not in topology_module


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

    assert "integration_ci" in shell_runner
    assert "provider_verification and jules" in provider_runner
    assert "pytest tests/provider/jules" in provider_runner
    assert "integration_ci" in powershell_runner


def test_compose_tests_use_an_explicit_isolated_project() -> None:
    compose_file = (REPO_ROOT / "docker-compose.test.yaml").read_text(encoding="utf-8")
    # Keep the file parseable by legacy docker-compose; runners own the project name.
    assert not compose_file.startswith("name:")

    for relative_path in COMPOSE_TEST_RUNNERS:
        runner = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "MOONMIND_TEST_COMPOSE_PROJECT_NAME" in runner, relative_path
        assert "--project-name" in runner, relative_path
        assert "moonmind-test" in runner, relative_path
        assert "down" in runner, relative_path
        assert "--remove-orphans" in runner, relative_path


def test_powershell_compose_runners_preserve_test_exit_codes_after_cleanup() -> None:
    for relative_path in (
        "tools/test-unit.ps1",
        "tools/test-integration.ps1",
        "tools/test-e2e.ps1",
    ):
        runner = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        capture_index = runner.index("$testExitCode = $LASTEXITCODE")
        cleanup_index = runner.rindex("down --remove-orphans")
        exit_index = runner.rindex("exit $testExitCode")
        assert capture_index < cleanup_index < exit_index, relative_path


def test_bash_compose_runners_register_cleanup_before_setup() -> None:
    for relative_path in (
        "tools/test_integration.sh",
        "tools/test_jules_provider.sh",
    ):
        runner = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert runner.index("trap cleanup EXIT") < runner.index("docker network inspect"), (
            relative_path
        )


def test_bash_compose_test_runners_reject_the_deployment_project() -> None:
    environment = {
        **os.environ,
        "MOONMIND_TEST_COMPOSE_PROJECT_NAME": "moonmind",
    }

    for relative_path in COMPOSE_TEST_RUNNERS[:3]:
        result = subprocess.run(
            ["bash", str(REPO_ROOT / relative_path)],
            cwd=REPO_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2, relative_path
        assert "must be 'moonmind-test'" in result.stderr, relative_path


def test_provider_verification_is_not_configured_as_a_github_action() -> None:
    provider_workflow = (
        REPO_ROOT / ".github" / "workflows" / "provider-verification.yml"
    )
    required_workflow = (
        REPO_ROOT / ".github" / "workflows" / "pytest-unit-tests.yml"
    ).read_text(encoding="utf-8")

    assert not provider_workflow.exists()
    assert "./tools/test_integration.sh" in required_workflow
    assert "./tools/test_jules_provider.sh" not in required_workflow


def test_docker_compose_test_runners_provision_the_shared_network() -> None:
    shell_runner = (REPO_ROOT / "tools" / "test_integration.sh").read_text(
        encoding="utf-8"
    )
    provider_runner = (REPO_ROOT / "tools" / "test_jules_provider.sh").read_text(
        encoding="utf-8"
    )
    powershell_runner = (REPO_ROOT / "tools" / "test-integration.ps1").read_text(
        encoding="utf-8"
    )

    assert "MOONMIND_DOCKER_NETWORK" in shell_runner
    assert 'docker network inspect "$NETWORK_NAME"' in shell_runner
    assert 'docker network create "$NETWORK_NAME"' in shell_runner
    assert "MOONMIND_DOCKER_NETWORK" in provider_runner
    assert 'docker network inspect "$NETWORK_NAME"' in provider_runner
    assert 'docker network create "$NETWORK_NAME"' in provider_runner
    assert "$env:MOONMIND_DOCKER_NETWORK" in powershell_runner
    assert "docker network inspect $networkName" in powershell_runner
    assert "docker network create $networkName" in powershell_runner


def test_phase6_integration_ci_suite_stays_focused_on_highest_risk_seams() -> None:
    """Phase 6: the required integration_ci suite must stay focused on artifacts,
    worker topology, live logs, managed runtime, and compose foundation.

    Tests that require external credentials or third-party providers must NOT
    be in the integration_ci suite.
    """
    # Verify the performance test uses deterministic bounds (Phase 6 hardening)
    perf_test = (
        REPO_ROOT
        / "tests"
        / "integration"
        / "temporal"
        / "test_live_logs_performance.py"
    ).read_text(encoding="utf-8")
    assert (
        "10000" not in perf_test
    ), "Performance test should not use 10k events — use deterministic bounds"
    assert (
        "500" in perf_test
    ), "Performance test should use 500 deterministic event count"


def test_phase6_artifact_authorization_tests_require_oidc() -> None:
    """Phase 6: artifact authorization tests must toggle AUTH_PROVIDER to
    exercise real ownership checks, not rely on disabled auth."""
    authz_test = (
        REPO_ROOT
        / "tests"
        / "integration"
        / "temporal"
        / "test_temporal_artifact_authorization.py"
    ).read_text(encoding="utf-8")
    assert "AUTH_PROVIDER" in authz_test
    assert "keycloak" in authz_test
    assert "TemporalArtifactAuthorizationError" in authz_test
