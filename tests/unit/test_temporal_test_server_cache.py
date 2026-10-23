"""Image preparation recovers transient downloads without hiding startup errors."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from api_service.docker import cache_temporal_test_server as cache


@pytest.fixture
def servers(monkeypatch):
    skipping = SimpleNamespace(shutdown=AsyncMock())
    local = SimpleNamespace(shutdown=AsyncMock())
    starts = SimpleNamespace(
        start_time_skipping=AsyncMock(return_value=skipping),
        start_local=AsyncMock(return_value=local),
    )
    sleep = AsyncMock()
    monkeypatch.setattr(cache, "WorkflowEnvironment", starts)
    monkeypatch.setattr(cache, "asyncio", SimpleNamespace(sleep=sleep))
    return SimpleNamespace(starts=starts, skipping=skipping, local=local, sleep=sleep)


def download_error(status):
    kind = "server" if status >= 500 else "client"
    return RuntimeError(
        "Failed starting Temporal dev server: failed to download ephemeral server "
        f"executable: HTTP status {kind} error ({status})"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_transient_download_retries_only_the_unfinished_server(
    servers, capsys, status
):
    error = download_error(status)
    servers.starts.start_local.side_effect = [error, servers.local]

    await cache._cache_test_server()

    servers.starts.start_time_skipping.assert_awaited_once_with()
    assert servers.starts.start_local.await_count == 2
    servers.sleep.assert_awaited_once_with(30)
    servers.skipping.shutdown.assert_awaited_once_with()
    servers.local.shutdown.assert_awaited_once_with()
    assert str(error) in capsys.readouterr().err


@pytest.mark.asyncio
async def test_persistent_download_failure_stops_after_bounded_retries(servers):
    error = download_error(429)
    servers.starts.start_time_skipping.side_effect = error

    with pytest.raises(RuntimeError) as raised:
        await cache._cache_test_server()

    assert raised.value is error
    assert servers.starts.start_time_skipping.await_count == 3
    assert servers.sleep.await_args_list == [call(30), call(60)]
    servers.starts.start_local.assert_not_awaited()
    servers.skipping.shutdown.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [download_error(404), RuntimeError("server exited 1")]
)
async def test_permanent_startup_errors_are_not_retried(servers, error):
    servers.starts.start_time_skipping.side_effect = error

    with pytest.raises(RuntimeError) as raised:
        await cache._cache_test_server()

    assert raised.value is error
    servers.starts.start_time_skipping.assert_awaited_once_with()
    servers.sleep.assert_not_awaited()
    servers.starts.start_local.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_failure_does_not_start_another_server(servers):
    error = RuntimeError("shutdown failed")
    servers.skipping.shutdown.side_effect = error

    with pytest.raises(RuntimeError) as raised:
        await cache._cache_test_server()

    assert raised.value is error
    servers.starts.start_time_skipping.assert_awaited_once_with()
    servers.sleep.assert_not_awaited()
    servers.starts.start_local.assert_not_awaited()
