from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parents[3] / "tools" / "check_github_workflow_names.py"
)
SPEC = importlib.util.spec_from_file_location(
    "check_github_workflow_names", MODULE_PATH
)
assert SPEC and SPEC.loader
check_github_workflow_names = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_github_workflow_names
SPEC.loader.exec_module(check_github_workflow_names)


def _write_workflow(root: Path, filename: str, display_name: str) -> Path:
    path = root / ".github" / "workflows" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"name: {display_name}\non: workflow_dispatch\n", encoding="utf-8")
    return path


def test_current_workflows_have_stable_names() -> None:
    expected_names = {
        "alembic-migration-gate.yml": "CI / Alembic Migration Gate",
        "codex-conformance-canary.yml": "Provider / Codex Conformance Canary",
        "docker-publish.yml": "Release / Build App Image",
        "omnigent-live-conformance.yml": "Provider / Omnigent Live Conformance",
        "pentestgpt-runner.yml": "Security / Build PentestGPT Runner",
        "promote-ghcr-stable.yml": "Release / Promote Stable",
        "pytest-unit-tests.yml": "CI / Test Suite",
    }

    files = check_github_workflow_names.workflow_files(
        check_github_workflow_names.REPO_ROOT
    )
    actual_names = {
        path.name: check_github_workflow_names.workflow_display_name(path)
        for path in files
    }

    assert actual_names == expected_names
    assert check_github_workflow_names.check_workflows() == []


@pytest.mark.parametrize(
    "display_name",
    [
        "Apply PR patch",
        "Apply PR 2401 review fix",
        "Review fix runner",
        "Milestone documentation patch",
        "Clean up shared layout documentation",
        "'Repository task #3116'",
        "Repository task for issue 3116",
        "'Repository task for pull request #3116'",
    ],
)
def test_task_specific_display_names_are_rejected(
    tmp_path: Path, display_name: str
) -> None:
    path = _write_workflow(tmp_path, "temporary.yml", display_name)

    findings = check_github_workflow_names.check_workflow_file(path)

    assert len(findings) == 1
    assert "use a stable reusable workflow name" in findings[0].message


@pytest.mark.parametrize(
    "display_name",
    [
        "CI / Test Suite",
        "Maintenance / Repository Task",
        '"Release / Python 3.12 Image"',
        "'Security / Build Runner'",
    ],
)
def test_stable_reusable_display_names_are_allowed(
    tmp_path: Path, display_name: str
) -> None:
    path = _write_workflow(tmp_path, "permanent.yml", display_name)

    assert check_github_workflow_names.check_workflow_file(path) == []


def test_workflow_requires_an_explicit_top_level_name(tmp_path: Path) -> None:
    path = _write_workflow(tmp_path, "unnamed.yml", "Job name")
    path.write_text(
        "on: workflow_dispatch\njobs:\n  name: Nested job name\n", encoding="utf-8"
    )

    findings = check_github_workflow_names.check_workflow_file(path)

    assert len(findings) == 1
    assert "explicit top-level name" in findings[0].message
