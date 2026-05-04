from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from moonmind.publish.service import PublishService
from moonmind.workflows.adapters.github_service import GitHubService

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


def _response(status: int, json_body, *, headers: dict[str, str] | None = None):
    return httpx.Response(
        status,
        json=json_body,
        headers=headers or {},
        request=httpx.Request("GET", "https://api.github.com/test"),
    )


async def test_readiness_permission_403_reports_unavailable_evidence(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, **_kwargs):
            if url.endswith("/pulls/12"):
                return _response(200, {"state": "open", "head": {"sha": "abc"}})
            if url.endswith("/status"):
                return _response(200, {"state": "success", "statuses": []})
            if url.endswith("/check-runs"):
                return _response(
                    403,
                    {"message": "Resource not accessible by personal access token"},
                    headers={"X-Accepted-GitHub-Permissions": "checks=read"},
                )
            raise AssertionError(url)

    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        lambda **_kwargs: _Client(),
    )

    result = await GitHubService().evaluate_pull_request_readiness(
        repo="owner/repo",
        pr_number=12,
        head_sha="abc",
        policy={"checks": "required", "automatedReview": "disabled"},
    )

    assert result.checks_complete is None
    assert result.blockers[0]["kind"] == "readiness_evidence_unavailable"
    assert result.blockers[0]["missingPermission"] == "Checks: read"


async def test_publish_pr_boundary_uses_explicit_credential(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "publish-token")
    calls: list[dict[str, object]] = []
    created: dict[str, object] = {}

    async def _run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        if command[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(stdout=" M file.py\n")
        return SimpleNamespace(stdout="")

    async def _create_pr(**kwargs):
        created.update(kwargs)
        return SimpleNamespace(created=True, url="https://github.com/owner/repo/pull/9")

    result = await PublishService(github_create_pull_request=_create_pr).publish(
        job_id=uuid4(),
        instruction="make change",
        publish_mode="pr",
        publish_base_branch="main",
        runtime_mode="codex",
        repo_dir=tmp_path,
        run_command=_run,
        repo="owner/repo",
    )

    assert result.endswith("/pull/9")
    assert created["github_token"] == "publish-token"
    assert all(call["command"][:3] != ["gh", "pr", "create"] for call in calls)


async def test_publish_branch_boundary_uses_token_aware_push(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "publish-token")
    calls: list[dict[str, object]] = []

    async def _run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        if command[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(stdout=" M file.py\n")
        return SimpleNamespace(stdout="")

    result = await PublishService().publish(
        job_id=uuid4(),
        instruction="make change",
        publish_mode="branch",
        publish_base_branch=None,
        runtime_mode="codex",
        repo_dir=tmp_path,
        run_command=_run,
        repo="owner/repo",
    )

    push_call = next(call for call in calls if call["command"][:2] == ["git", "push"])
    assert result.startswith("published branch")
    assert push_call["env"]["GITHUB_TOKEN"] == "publish-token"
    assert push_call["env"]["GH_TOKEN"] == "publish-token"
    assert push_call["env"]["GIT_TERMINAL_PROMPT"] == "0"


async def test_github_permission_diagnostic_crosses_service_boundary(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            return _response(
                403,
                {
                    "message": "Resource not accessible by personal access token",
                    "documentation_url": "https://docs.github.com/rest/pulls/pulls",
                },
                headers={"X-Accepted-GitHub-Permissions": "pull_requests=write"},
            )

    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        lambda **_kwargs: _Client(),
    )

    result = await GitHubService().create_pull_request(
        repo="owner/repo",
        head="feature",
        base="main",
        title="Title",
        body="Body",
    )

    assert result.created is False
    assert "Resource not accessible by personal access token" in result.summary
    assert "pull_requests=write" in result.summary
    assert "https://docs.github.com/rest/pulls/pulls" in result.summary
