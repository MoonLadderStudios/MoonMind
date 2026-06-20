from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "check_openapi_affecting_changes.sh"


def _run_script(*paths: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *paths],
        cwd=REPO_ROOT,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    "path",
    [
        "api_service/api/routers/agent_runs.py",
        "api_service/services/temporal_execution_service.py",
        "moonmind/core/artifacts.py",
        "moonmind/schemas/agent_runtime_models.py",
        "moonmind/schemas/agent_skill_models.py",
        "moonmind/schemas/chat_models.py",
        "moonmind/schemas/documents_models.py",
        "moonmind/schemas/manifest_models.py",
        "moonmind/schemas/manifest_v0_models.py",
        "moonmind/schemas/manifest_ingest_models.py",
        "moonmind/schemas/temporal_activity_models.py",
        "moonmind/schemas/temporal_artifact_models.py",
        "moonmind/schemas/temporal_models.py",
        "moonmind/schemas/temporal_signal_contracts.py",
        "moonmind/schemas/managed_session_models.py",
        "moonmind/schemas/__init__.py",
        "moonmind/schemas/workflow_models.py",
        "moonmind/schemas/workflow_proposal_models.py",
        "tools/export_openapi.py",
        "tools/generate_openapi_types.py",
        "tools/run_repo_python.sh",
        "frontend/src/generated/openapi.ts",
        "package.json",
        "package-lock.json",
        "pyproject.toml",
        "uv.lock",
    ],
)
def test_matches_openapi_affecting_paths_from_args(path: str) -> None:
    result = _run_script(path)

    assert result.returncode == 0


def test_matches_schema_descendants_from_stdin() -> None:
    result = _run_script(
        stdin="frontend/src/entrypoints/workflow-detail.tsx\nmoonmind/schemas/temporal_artifact_models.py\n"
    )

    assert result.returncode == 0


def test_managed_session_models_is_openapi_affecting_exported_api_schema() -> None:
    init_file = REPO_ROOT / "moonmind" / "schemas" / "__init__.py"
    router_file = REPO_ROOT / "api_service" / "api" / "routers" / "agent_runs.py"

    assert "from .managed_session_models import (" in init_file.read_text(encoding="utf-8")
    assert "from moonmind.schemas.managed_session_models import" in router_file.read_text(
        encoding="utf-8"
    )

    result = _run_script("moonmind/schemas/managed_session_models.py")

    assert result.returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        "moonmind/workflows/temporal/runtime/codex_session_runtime.py",
        "moonmind/workflows/temporal/activity_runtime.py",
        "moonmind/manifest/interpolation.py",
        "moonmind/schemas/_validation.py",
        "moonmind/schemas/jules_models.py",
        "moonmind/schemas/step_execution_models.py",
        "moonmind/schemas/temporal_payload_policy.py",
        "moonmind/schemas/workload_models.py",
        "frontend/src/entrypoints/workflow-detail.tsx",
        "docs/UI/WorkflowConsoleArchitecture.md",
    ],
)
def test_ignores_paths_that_do_not_affect_openapi_contracts(path: str) -> None:
    result = _run_script(path)

    assert result.returncode == 1


def test_ignores_multiple_unrelated_paths_from_args() -> None:
    result = _run_script(
        "frontend/src/entrypoints/workflow-detail.tsx",
        "docs/UI/WorkflowConsoleArchitecture.md",
        "moonmind/workflows/temporal/runtime/codex_session_runtime.py",
    )

    assert result.returncode == 1
