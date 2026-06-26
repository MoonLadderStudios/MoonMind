"""Hermetic end-to-end test for the batch-dependabot-resolver skill.

Drives ``main()`` with ``gh pr list`` (subprocess) and the executions API
(``httpx.AsyncClient``) mocked, so it requires no compose services and runs
directly under pytest. Marked ``integration``/``integration_ci`` because it
exercises the full discovery → Dependabot filter → submit → artifact pipeline
across module boundaries.
"""

from __future__ import annotations

import asyncio
import json
import runpy
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _load_module() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[3]
    return runpy.run_path(
        str(
            repo_root
            / ".agents"
            / "skills"
            / "batch-dependabot-resolver"
            / "bin"
            / "batch_dependabot_resolver.py"
        )
    )


def _mixed_pr_set() -> list[dict[str, Any]]:
    return [
        {  # genuine Dependabot match
            "number": 1,
            "title": "Bump anthropic from 0.105.2 to 0.107.1",
            "author": {"login": "dependabot[bot]"},
            "headRefName": "dependabot/pip/anthropic-0.107.1",
            "headRefOid": "sha-1",
            "headRepository": {"name": "MoonMind"},
            "headRepositoryOwner": {"login": "MoonLadderStudios"},
            "isCrossRepository": False,
            "labels": [],
        },
        {  # fork / cross-repo
            "number": 2,
            "title": "Bump requests from 2.0.0 to 2.1.0",
            "author": {"login": "dependabot[bot]"},
            "headRefName": "dependabot/pip/requests-2.1.0",
            "headRefOid": "sha-2",
            "headRepository": {"name": "MoonMind"},
            "headRepositoryOwner": {"login": "a-fork-owner"},
            "isCrossRepository": True,
            "labels": [],
        },
        {  # human PR
            "number": 3,
            "title": "Add a feature",
            "author": {"login": "octocat"},
            "headRefName": "feature/cool",
            "headRefOid": "sha-3",
            "headRepository": {"name": "MoonMind"},
            "headRepositoryOwner": {"login": "MoonLadderStudios"},
            "isCrossRepository": False,
            "labels": [],
        },
        {  # Dependabot but non-matching title
            "number": 4,
            "title": "Bump the pip group with 2 updates",
            "author": {"login": "dependabot[bot]"},
            "headRefName": "dependabot/pip/group-update",
            "headRefOid": "sha-4",
            "headRepository": {"name": "MoonMind"},
            "headRepositoryOwner": {"login": "MoonLadderStudios"},
            "isCrossRepository": False,
            "labels": [],
        },
        {  # second genuine Dependabot match (npm)
            "number": 5,
            "title": "Bump eslint from 8.0.0 to 9.0.0",
            "author": {"login": "dependabot[bot]"},
            "headRefName": "dependabot/npm_and_yarn/eslint-9.0.0",
            "headRefOid": "sha-5",
            "headRepository": {"name": "MoonMind"},
            "headRepositoryOwner": {"login": "MoonLadderStudios"},
            "isCrossRepository": False,
            "labels": [],
        },
    ]


class _FakeAsyncClient:
    submissions: list[dict[str, Any]] = []

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        pass

    async def post(self, _path: str, **kwargs: Any) -> Any:
        body = kwargs.get("json")
        _FakeAsyncClient.submissions.append(body)
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(
            return_value={"workflowId": f"mm:wf-{len(_FakeAsyncClient.submissions)}"}
        )
        return response


def _run_main(
    module: dict[str, Any], argv: list[str], monkeypatch: Any, tmp_path: Path
) -> dict[str, Any]:
    _FakeAsyncClient.submissions = []
    monkeypatch.setenv("MOONMIND_URL", "http://api:8000")
    monkeypatch.delenv("MOONMIND_WORKER_TOKEN", raising=False)
    monkeypatch.delenv("MOONMIND_WORKER_TOKEN_FILE", raising=False)
    for env_key in (
        "MOONMIND_TASK_WORKFLOW_ID",
        "MOONMIND_WORKFLOW_ID",
        "TEMPORAL_WORKFLOW_ID",
        "MOONMIND_SESSION_ARTIFACT_SPOOL_PATH",
        "MOONMIND_DEFAULT_RUNTIME",
        "MOONMIND_EXECUTION_PROFILE_REF",
    ):
        monkeypatch.delenv(env_key, raising=False)

    completed = subprocess.CompletedProcess(
        args=["gh"], returncode=0, stdout=json.dumps(_mixed_pr_set()), stderr=""
    )

    artifacts_dir = tmp_path / "artifacts"
    full_argv = [*argv, "--artifacts-dir", str(artifacts_dir)]

    import httpx

    with patch.object(subprocess, "run", return_value=completed), patch.object(
        httpx, "AsyncClient", _FakeAsyncClient
    ):
        exit_code = asyncio.run(module["main"](full_argv))

    result_path = artifacts_dir / "batch_dependabot_resolver_result.json"
    summary = json.loads(result_path.read_text())
    summary["_exit_code"] = exit_code
    return summary


def test_end_to_end_mixed_pr_set(monkeypatch: Any, tmp_path: Path) -> None:
    module = _load_module()
    summary = _run_main(
        module, ["--repo", "MoonLadderStudios/MoonMind"], monkeypatch, tmp_path
    )

    # Only PRs 1 and 5 are genuine Dependabot version bumps.
    assert summary["requested"] == 5
    assert summary["matched"] == 2
    assert summary["created"] == 2
    assert sorted(item["pr"] for item in summary["queued"]) == [1, 5]
    reasons = {entry["pr"]: entry["reason"] for entry in summary["skipped"]}
    assert reasons == {2: "fork-pr", 3: "not-dependabot-author", 4: "title-mismatch"}
    assert summary["_exit_code"] == 0

    # Every discovered PR is accounted for exactly once (INV-1 / SC-004).
    accounted = (
        len(summary["queued"])
        + len(summary["wouldQueue"])
        + len(summary["skipped"])
        + len(summary["errors"])
    )
    assert accounted == summary["requested"]

    # Each child carries publish.mode=none and a stable idempotency key.
    for body in _FakeAsyncClient.submissions:
        assert body["payload"]["task"]["publish"]["mode"] == "none"
        assert body["payload"]["task"]["skill"]["name"] == "pr-resolver"
        assert "version" not in body["payload"]["task"]["skill"]
        assert body["payload"]["idempotencyKey"].startswith(
            "batch-dependabot-resolver:MoonLadderStudios/MoonMind:pr:"
        )


def test_end_to_end_dry_run_submits_nothing(monkeypatch: Any, tmp_path: Path) -> None:
    module = _load_module()
    summary = _run_main(
        module,
        ["--repo", "MoonLadderStudios/MoonMind", "--dry-run"],
        monkeypatch,
        tmp_path,
    )

    assert summary["dryRun"] is True
    assert summary["created"] == 0
    assert summary["queued"] == []
    assert sorted(item["pr"] for item in summary["wouldQueue"]) == [1, 5]
    assert _FakeAsyncClient.submissions == []  # nothing submitted
    assert summary["_exit_code"] == 0
    # No no-op signal on a dry run even though created==0.
    assert not (tmp_path / "artifacts" / "skill_outcome.json").exists()


def test_end_to_end_package_manager_filter(monkeypatch: Any, tmp_path: Path) -> None:
    module = _load_module()
    summary = _run_main(
        module,
        ["--repo", "MoonLadderStudios/MoonMind", "--package-managers", "pip"],
        monkeypatch,
        tmp_path,
    )
    # PR 5 (npm) is filtered out; only PR 1 (pip) is queued.
    assert [item["pr"] for item in summary["queued"]] == [1]
    reasons = {entry["pr"]: entry["reason"] for entry in summary["skipped"]}
    assert reasons[5] == "package-manager-filtered"
