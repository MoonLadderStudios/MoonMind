"""Activity-boundary tests for MM-826 terminal-disposition handoff gating.

These exercise the producing-step accepted assertion wired into the concrete
external handoff activities ``repo.create_pr`` and ``repo.merge_pr``. A denied
handoff must return a structured non-performing result with a blocked
side-effect record and must NOT construct or call ``GitHubService``. An absent
``stepExecutionGate`` must preserve legacy behavior (in-flight compatibility).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.asyncio]


def _accepted_gate(operation: str) -> dict:
    return {
        "operation": operation,
        "effectClass": "publication",
        "terminalDisposition": "accepted",
        "gateApproved": True,
    }


def _denying_gate(operation: str) -> dict:
    return {
        "operation": operation,
        "effectClass": "publication",
        "terminalDisposition": "candidate",
        "gateApproved": True,
    }


async def test_create_pr_denied_when_step_not_accepted() -> None:
    from moonmind.workflows.temporal.activities.jules_activities import (
        repo_create_pr_activity,
    )

    with patch(
        "moonmind.workflows.adapters.github_service.GitHubService"
    ) as mock_service_cls:
        result = await repo_create_pr_activity(
            {
                "repo": "org/repo",
                "head": "feature",
                "base": "main",
                "title": "T",
                "body": "B",
                "stepExecutionGate": _denying_gate("repo.create_pr"),
            }
        )

    # The GitHub adapter must never be constructed for a denied handoff.
    mock_service_cls.assert_not_called()
    assert result["created"] is False
    assert result.get("url") is None
    assert result["blockedSideEffect"]["disposition"] == "blocked"
    assert result["blockedSideEffect"]["class"] == "publication"


async def test_merge_pr_denied_when_step_not_accepted() -> None:
    from moonmind.workflows.temporal.activities.jules_activities import (
        repo_merge_pr_activity,
    )

    with patch(
        "moonmind.workflows.adapters.github_service.GitHubService"
    ) as mock_service_cls:
        result = await repo_merge_pr_activity(
            {
                "pr_url": "https://github.com/org/repo/pull/1",
                "stepExecutionGate": _denying_gate("repo.merge_pr"),
            }
        )

    mock_service_cls.assert_not_called()
    assert result["merged"] is False
    assert result["blockedSideEffect"]["disposition"] == "blocked"


async def test_create_pr_allowed_when_accepted_and_gate_approved() -> None:
    from moonmind.workflows.temporal.activities.jules_activities import (
        repo_create_pr_activity,
    )

    with patch(
        "moonmind.workflows.adapters.github_service.GitHubService"
    ) as mock_service_cls:
        service = mock_service_cls.return_value
        service.create_pull_request = AsyncMock(
            return_value=type(
                "CreateResult",
                (),
                {
                    "model_dump": lambda self, by_alias=True: {
                        "url": "https://github.com/org/repo/pull/7",
                        "created": True,
                        "summary": "created",
                    }
                },
            )()
        )
        result = await repo_create_pr_activity(
            {
                "repo": "org/repo",
                "head": "feature",
                "base": "main",
                "title": "T",
                "body": "B",
                "stepExecutionGate": _accepted_gate("repo.create_pr"),
            }
        )

    service.create_pull_request.assert_awaited_once()
    assert result["created"] is True
    assert result["url"] == "https://github.com/org/repo/pull/7"


async def test_merge_pr_allowed_when_accepted_and_gate_approved() -> None:
    from moonmind.workflows.temporal.activities.jules_activities import (
        repo_merge_pr_activity,
    )

    with patch(
        "moonmind.workflows.adapters.github_service.GitHubService"
    ) as mock_service_cls:
        service = mock_service_cls.return_value
        service.merge_pull_request = AsyncMock(
            return_value=type(
                "MergeResult",
                (),
                {
                    "model_dump": lambda self, by_alias=True: {
                        "prUrl": "https://github.com/org/repo/pull/1",
                        "merged": True,
                        "summary": "merged",
                    }
                },
            )()
        )
        result = await repo_merge_pr_activity(
            {
                "pr_url": "https://github.com/org/repo/pull/1",
                "stepExecutionGate": _accepted_gate("repo.merge_pr"),
            }
        )

    service.merge_pull_request.assert_awaited_once()
    assert result["merged"] is True


async def test_create_pr_legacy_payload_without_gate_is_unchanged() -> None:
    # In-flight compatibility (Constitution III): no stepExecutionGate ⇒ legacy path.
    from moonmind.workflows.temporal.activities.jules_activities import (
        repo_create_pr_activity,
    )

    with patch(
        "moonmind.workflows.adapters.github_service.GitHubService"
    ) as mock_service_cls:
        service = mock_service_cls.return_value
        service.create_pull_request = AsyncMock(
            return_value=type(
                "CreateResult",
                (),
                {
                    "model_dump": lambda self, by_alias=True: {
                        "url": "https://github.com/org/repo/pull/9",
                        "created": True,
                        "summary": "created",
                    }
                },
            )()
        )
        result = await repo_create_pr_activity(
            {
                "repo": "org/repo",
                "head": "feature",
                "base": "main",
                "title": "T",
                "body": "B",
            }
        )

    service.create_pull_request.assert_awaited_once()
    assert result["created"] is True
    assert "blockedSideEffect" not in result


async def test_merge_pr_legacy_payload_without_gate_is_unchanged() -> None:
    from moonmind.workflows.temporal.activities.jules_activities import (
        repo_merge_pr_activity,
    )

    with patch(
        "moonmind.workflows.adapters.github_service.GitHubService"
    ) as mock_service_cls:
        service = mock_service_cls.return_value
        service.merge_pull_request = AsyncMock(
            return_value=type(
                "MergeResult",
                (),
                {
                    "model_dump": lambda self, by_alias=True: {
                        "prUrl": "https://github.com/org/repo/pull/1",
                        "merged": True,
                        "summary": "merged",
                    }
                },
            )()
        )
        result = await repo_merge_pr_activity(
            {"pr_url": "https://github.com/org/repo/pull/1"}
        )

    service.merge_pull_request.assert_awaited_once()
    assert result["merged"] is True
    assert "blockedSideEffect" not in result
