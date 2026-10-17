"""Unit tests for the provider-agnostic ``repo.*`` Temporal activities."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from moonmind.config.settings import settings

pytestmark = [pytest.mark.asyncio]


async def test_repo_create_pr_activity_validates_payload():
    from moonmind.workflows.temporal.activities.repo_activities import repo_create_pr_activity
    with pytest.raises(ValueError, match="Invalid payload"):
        await repo_create_pr_activity({})

@pytest.mark.parametrize("high_security_mode", [False, True])
async def test_repo_create_pr_activity_delegates(monkeypatch, high_security_mode):
    from moonmind.workflows.temporal.activities.repo_activities import repo_create_pr_activity

    monkeypatch.setattr(settings.security, "high_security_mode", high_security_mode)
    
    with patch("moonmind.workflows.adapters.github_service.GitHubService") as mock_svc_cls:
        mock_svc = mock_svc_cls.return_value
        
        from moonmind.workflows.adapters.github_service import CreatePRResult
        expected_inner = CreatePRResult(url="https://github.com/abc", created=True, summary="")
        mock_svc.create_pull_request = AsyncMock(return_value=expected_inner)
        
        payload = {
            "repo": "owner/repo",
            "head": "branch",
            "base": "main",
            "title": "Title",
            "body": "Body"
        }
        
        result = await repo_create_pr_activity(payload)

        assert result == expected_inner.model_dump(by_alias=True)
        mock_svc.create_pull_request.assert_awaited_once_with(
            repo="owner/repo",
            head="branch",
            base="main",
            title="Title",
            body="Body",
            draft=False,
        )


@pytest.mark.parametrize("field", ["title", "body"])
@pytest.mark.parametrize(
    "sensitive_text",
    [
        "Authorization: Bearer unit-test-pr-bearer-value",
        "password=unit-test-pr-password-value",
    ],
)
@pytest.mark.parametrize("high_security_mode", [False, True])
async def test_repo_create_pr_activity_scans_final_text_before_github(
    monkeypatch, field, sensitive_text, high_security_mode
):
    from moonmind.workflows.adapters.github_service import CreatePRResult
    from moonmind.workflows.temporal.activities.repo_activities import (
        repo_create_pr_activity,
    )

    monkeypatch.setattr(settings.security, "high_security_mode", high_security_mode)
    payload = {
        "repo": "owner/repo",
        "head": "branch",
        "base": "main",
        "title": "Saved candidate",
        "body": "Unverified criteria: CI unavailable.",
    }
    payload[field] += f"\n{sensitive_text}"
    with patch("moonmind.workflows.adapters.github_service.GitHubService") as service:
        create = AsyncMock(
            return_value=CreatePRResult(
                url="https://github.com/owner/repo/pull/42", created=True, summary=""
            )
        )
        service.return_value.create_pull_request = create
        if high_security_mode:
            with pytest.raises(ValueError, match="Request blocked") as exc_info:
                await repo_create_pr_activity(payload)
            assert "unit-test-pr-" not in str(exc_info.value)
            service.assert_not_called()
            create.assert_not_awaited()
        else:
            result = await repo_create_pr_activity(payload)
            assert result["created"] is True
            create.assert_awaited_once_with(**payload, draft=False)


@pytest.mark.parametrize("field", ["title", "body"])
@pytest.mark.parametrize("high_security_mode", [False, True])
async def test_repo_create_pr_activity_preserves_provider_token_blocking(
    monkeypatch, field, high_security_mode
):
    from moonmind.workflows.temporal.activities.repo_activities import (
        repo_create_pr_activity,
    )

    monkeypatch.setattr(settings.security, "high_security_mode", high_security_mode)
    payload = {
        "repo": "owner/repo",
        "head": "branch",
        "base": "main",
        "title": "Saved candidate",
        "body": "Unverified criteria: CI unavailable.",
    }
    payload[field] += "\n" + "ghp_" + "x" * 36
    with patch("moonmind.workflows.adapters.github_service.GitHubService") as service:
        with pytest.raises(ValueError, match="Request blocked"):
            await repo_create_pr_activity(payload)
        service.assert_not_called()


async def test_repo_create_pr_activity_delegates_draft_flag():
    from moonmind.workflows.temporal.activities.repo_activities import repo_create_pr_activity

    with patch("moonmind.workflows.adapters.github_service.GitHubService") as mock_svc_cls:
        mock_svc = mock_svc_cls.return_value

        from moonmind.workflows.adapters.github_service import CreatePRResult

        mock_svc.create_pull_request = AsyncMock(
            return_value=CreatePRResult(
                url="https://github.com/abc", created=True, summary=""
            )
        )

        await repo_create_pr_activity(
            {
                "repo": "owner/repo",
                "head": "branch",
                "base": "main",
                "title": "Title",
                "body": "Body",
                "draft": True,
            }
        )

        assert (
            mock_svc.create_pull_request.await_args.kwargs["draft"] is True
        )


async def test_repo_create_pr_activity_returns_adopted_result():
    from moonmind.workflows.temporal.activities.repo_activities import repo_create_pr_activity

    with patch("moonmind.workflows.adapters.github_service.GitHubService") as mock_svc_cls:
        mock_svc = mock_svc_cls.return_value

        from moonmind.workflows.adapters.github_service import CreatePRResult

        mock_svc.create_pull_request = AsyncMock(
            return_value=CreatePRResult(
                url="https://github.com/owner/repo/pull/42",
                created=False,
                adopted=True,
                summary="adopted existing PR",
                headSha="abc123",
            )
        )

        result = await repo_create_pr_activity(
            {
                "repo": "owner/repo",
                "head": "branch",
                "base": "main",
                "title": "Title",
                "body": "Body",
            }
        )

    assert result["created"] is False
    assert result["adopted"] is True
    assert result["url"] == "https://github.com/owner/repo/pull/42"


@pytest.mark.parametrize('failure', [500, 503, 429, 'timeout'])
@pytest.mark.usefixtures('deployment_without_recorded_connections')
async def test_repo_create_pr_transient_failure_retries_and_adopts_lost_ack(monkeypatch, failure):
    """Real adapter/activity handoff: retry reconciles a remotely created PR."""
    import httpx
    from temporalio.exceptions import ApplicationError
    from moonmind.workflows.temporal.activities.repo_activities import repo_create_pr_activity

    monkeypatch.setenv('GITHUB_TOKEN', 'github-token-fixture')
    calls = []
    created = False
    pr = {
        'number': 42, 'html_url': 'https://github.com/o/r/pull/42', 'draft': True,
        'head': {'ref': 'feature', 'sha': 'abc123', 'repo': {'full_name': 'o/r'}},
        'base': {'ref': 'main', 'repo': {'full_name': 'o/r'}},
    }

    def github(request):
        nonlocal created
        calls.append(request.method)
        assert request.headers['Authorization'] == 'Bearer github-token-fixture'
        if request.method == 'GET':
            return httpx.Response(200, json=[pr] if created else [])
        if request.method == 'POST':
            assert not created
            created = True
            if failure == 'timeout':
                raise httpx.ReadTimeout('lost response', request=request)
            return httpx.Response(failure, json={'message': 'temporarily unavailable'})
        raise AssertionError(f'adoption must not write: {request.method}')

    client_class = httpx.AsyncClient
    with patch('moonmind.workflows.adapters.github_service.httpx.AsyncClient',
               side_effect=lambda **kwargs: client_class(transport=httpx.MockTransport(github), **kwargs)):
        payload = {'repo': 'o/r', 'head': 'feature', 'base': 'main',
                   'title': 'Saved candidate', 'body': 'Remaining work', 'draft': True}
        with pytest.raises(ApplicationError) as exc:
            await repo_create_pr_activity(payload)
        assert exc.value.type == 'GitHubTransientError'
        assert not exc.value.non_retryable
        if failure == 429:
            assert exc.value.next_retry_delay.total_seconds() >= 60
        result = await repo_create_pr_activity(payload)
    assert result['adopted'] is True
    assert result['url'] == pr['html_url']
    # Adopting the lost-ack PR performs no metadata overwrite (#4018).
    assert calls == ['GET', 'POST', 'GET']
