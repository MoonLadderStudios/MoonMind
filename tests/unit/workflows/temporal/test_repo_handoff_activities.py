"""Activity-boundary gating tests for external handoff activities (MM-826).

Covers FR-004 (assert producing step accepted + gate-approved before the
external mutation) and FR-005 (deny non-idempotent action at the actual
activity boundary) for ``repo.create_pr`` and ``repo.merge_pr``, plus the
in-flight compatibility case where no ``handoffGate`` evidence is supplied.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.asyncio]


def _merge_service(mock_service_cls):
    service = mock_service_cls.return_value
    service.update_pull_request_base = AsyncMock(
        return_value=(True, "Base updated to main")
    )
    service.merge_pull_request = AsyncMock(
        return_value=type(
            "MergeResult",
            (),
            {
                "model_dump": lambda self, by_alias=True: {
                    "prUrl": "https://github.com/org/repo/pull/123",
                    "merged": True,
                    "mergeSha": "abc123",
                    "summary": "Merged successfully",
                }
            },
        )()
    )
    return service


def _create_service(mock_service_cls):
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
    return service


_NOT_ACCEPTED_GATE = {
    "terminalDisposition": "candidate",
    "gateApproved": False,
    "operation": "repo.merge",
    "effectClass": "publication",
}
_ACCEPTED_GATE = {
    "terminalDisposition": "accepted",
    "gateApproved": True,
    "operation": "repo.merge",
    "effectClass": "publication",
}


# --- repo.merge_pr -------------------------------------------------------


async def test_merge_pr_denied_when_handoff_gate_not_accepted():
    from moonmind.workflows.temporal.activities.jules_activities import (
        repo_merge_pr_activity,
    )

    with patch(
        "moonmind.workflows.adapters.github_service.GitHubService"
    ) as mock_service_cls:
        service = _merge_service(mock_service_cls)
        with pytest.raises(Exception) as excinfo:
            await repo_merge_pr_activity(
                {
                    "pr_url": "https://github.com/org/repo/pull/123",
                    "target_branch": "main",
                    "handoffGate": _NOT_ACCEPTED_GATE,
                }
            )

    # No external mutation occurred at the boundary.
    service.update_pull_request_base.assert_not_awaited()
    service.merge_pull_request.assert_not_awaited()
    assert "terminal_disposition_not_accepted" in str(excinfo.value)


async def test_merge_pr_allowed_when_handoff_gate_accepted():
    from moonmind.workflows.temporal.activities.jules_activities import (
        repo_merge_pr_activity,
    )

    with patch(
        "moonmind.workflows.adapters.github_service.GitHubService"
    ) as mock_service_cls:
        service = _merge_service(mock_service_cls)
        result = await repo_merge_pr_activity(
            {
                "pr_url": "https://github.com/org/repo/pull/123",
                "handoffGate": _ACCEPTED_GATE,
            }
        )

    service.merge_pull_request.assert_awaited_once()
    assert result["merged"] is True


async def test_merge_pr_compat_when_handoff_gate_absent():
    """In-flight runs that never set handoffGate keep working (compat)."""
    from moonmind.workflows.temporal.activities.jules_activities import (
        repo_merge_pr_activity,
    )

    with patch(
        "moonmind.workflows.adapters.github_service.GitHubService"
    ) as mock_service_cls:
        service = _merge_service(mock_service_cls)
        result = await repo_merge_pr_activity(
            {"pr_url": "https://github.com/org/repo/pull/123"}
        )

    service.merge_pull_request.assert_awaited_once()
    assert result["merged"] is True


# --- repo.create_pr ------------------------------------------------------


async def test_create_pr_denied_when_handoff_gate_not_accepted():
    from moonmind.workflows.temporal.activities.jules_activities import (
        repo_create_pr_activity,
    )

    payload = {
        "repo": "org/repo",
        "head": "feature",
        "base": "main",
        "title": "T",
        "body": "B",
        "handoffGate": {
            "terminalDisposition": "blocked",
            "gateApproved": False,
            "operation": "repo.create_pr",
            "effectClass": "publication",
        },
    }
    with patch(
        "moonmind.workflows.adapters.github_service.GitHubService"
    ) as mock_service_cls:
        service = _create_service(mock_service_cls)
        with pytest.raises(Exception) as excinfo:
            await repo_create_pr_activity(payload)

    service.create_pull_request.assert_not_awaited()
    assert "terminal_disposition_not_accepted" in str(excinfo.value)


async def test_create_pr_allowed_when_handoff_gate_accepted():
    from moonmind.workflows.temporal.activities.jules_activities import (
        repo_create_pr_activity,
    )

    payload = {
        "repo": "org/repo",
        "head": "feature",
        "base": "main",
        "title": "T",
        "body": "B",
        "handoffGate": {
            "terminalDisposition": "accepted",
            "gateApproved": True,
            "operation": "repo.create_pr",
            "effectClass": "publication",
        },
    }
    with patch(
        "moonmind.workflows.adapters.github_service.GitHubService"
    ) as mock_service_cls:
        service = _create_service(mock_service_cls)
        result = await repo_create_pr_activity(payload)

    service.create_pull_request.assert_awaited_once()
    assert result["created"] is True


async def test_create_pr_compat_when_handoff_gate_absent():
    from moonmind.workflows.temporal.activities.jules_activities import (
        repo_create_pr_activity,
    )

    payload = {
        "repo": "org/repo",
        "head": "feature",
        "base": "main",
        "title": "T",
        "body": "B",
    }
    with patch(
        "moonmind.workflows.adapters.github_service.GitHubService"
    ) as mock_service_cls:
        service = _create_service(mock_service_cls)
        result = await repo_create_pr_activity(payload)

    service.create_pull_request.assert_awaited_once()
    assert result["created"] is True
