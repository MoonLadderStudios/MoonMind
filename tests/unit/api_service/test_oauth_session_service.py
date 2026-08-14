from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.services.oauth_session_service import (
    get_oauth_session_workflow_status,
)


def _patch_temporal_client(
    monkeypatch: pytest.MonkeyPatch, *, description=None, error=None
):
    describe = AsyncMock(return_value=description, side_effect=error)
    client = SimpleNamespace(
        get_workflow_handle=lambda workflow_id: SimpleNamespace(describe=describe)
    )
    adapter = SimpleNamespace(get_client=AsyncMock(return_value=client))
    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        lambda: adapter,
    )
    return describe


@pytest.mark.asyncio
async def test_get_oauth_session_workflow_status_returns_remote_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    describe = _patch_temporal_client(
        monkeypatch,
        description=SimpleNamespace(status=SimpleNamespace(name="RUNNING")),
    )

    result = await get_oauth_session_workflow_status("oas_running")

    assert result == "RUNNING"
    describe.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_get_oauth_session_workflow_status_returns_none_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_found = RuntimeError("missing")
    not_found.status = SimpleNamespace(name="NOT_FOUND")
    _patch_temporal_client(monkeypatch, error=not_found)

    assert await get_oauth_session_workflow_status("oas_missing") is None


@pytest.mark.asyncio
async def test_get_oauth_session_workflow_status_fails_closed_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_temporal_client(monkeypatch, error=RuntimeError("unavailable"))

    with pytest.raises(RuntimeError, match="Failed to inspect OAuth session workflow"):
        await get_oauth_session_workflow_status("oas_unavailable")
