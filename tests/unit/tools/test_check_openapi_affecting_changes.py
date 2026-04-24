from __future__ import annotations

import subprocess
from pathlib import Path

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

def test_matches_backend_api_descendants_from_args() -> None:
    result = _run_script("api_service/api/routers/task_runs.py")

    assert result.returncode == 0

def test_matches_backend_schema_descendants_from_stdin() -> None:
    result = _run_script(
        stdin="frontend/src/entrypoints/task-detail.tsx\nmoonmind/schemas/temporal_artifact_models.py\n"
    )

    assert result.returncode == 0

def test_ignores_unrelated_paths() -> None:
    result = _run_script(
        "frontend/src/entrypoints/task-detail.tsx",
        "docs/UI/MissionControlArchitecture.md",
    )

    assert result.returncode == 1
