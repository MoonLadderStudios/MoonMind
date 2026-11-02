"""A lost observation must not hide authorization errors or pre-dispatch failures."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from moonmind.omnigent.execute import _enqueue_stream_events, _queued_stream_events
from moonmind.workflows.adapters.omnigent_client import OmnigentClientError


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["before_dispatch", "authorization_denied"])
async def test_stream_reconciliation_keeps_nonrecoverable_errors_visible(case):
    queue = asyncio.Queue()
    posted = asyncio.Event()
    diagnostics = []
    error = OmnigentClientError("original observation failure", status_code=403)
    if case == "authorization_denied":
        posted.set()

    async def stream(_session, *, on_admitted=None):
        yield {"type": "session.created"}
        if case == "before_dispatch":
            raise error from httpx.ReadError("not yet dispatched")
        raise error

    task = asyncio.create_task(
        _enqueue_stream_events(
            client=SimpleNamespace(stream_events=stream),
            session_id="session",
            queue=queue,
            message_posted=posted,
            observation_diagnostics=diagnostics,
        )
    )
    with pytest.raises(OmnigentClientError) as raised:
        async for _event in _queued_stream_events(queue=queue, stream_task=task):
            pass
    await task
    assert raised.value is error
    assert diagnostics == []
