from __future__ import annotations

import asyncio
import os
from pathlib import Path
import uuid

import pytest

from moonmind.workflows.temporal.runtime.github_auth_broker import (
    GitHubAuthBrokerManager,
    request_github_token,
)


@pytest.mark.asyncio
async def test_github_auth_broker_serves_token_and_cleans_socket():
    manager = GitHubAuthBrokerManager()
    socket_path = Path("/tmp") / f"mm-gh-broker-{os.getpid()}-{uuid.uuid4().hex[:8]}.sock"

    await manager.start(
        run_id="run-1",
        token="ghp_testtoken123",
        socket_path=str(socket_path),
    )

    assert (
        await asyncio.to_thread(request_github_token, str(socket_path))
        == "ghp_testtoken123"
    )
    assert Path(socket_path).exists()

    await manager.stop("run-1")

    assert not Path(socket_path).exists()
