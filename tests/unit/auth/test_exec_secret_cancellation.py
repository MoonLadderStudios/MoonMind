"""Bounded assessment credential checks must leave no resolver process (#5187)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from moonmind.auth.resolvers import ExecSecretResolver
from moonmind.auth.secret_refs import parse_secret_ref


@pytest.mark.asyncio
async def test_cancelled_resolution_kills_and_reaps_the_started_process(monkeypatch):
    proc = SimpleNamespace(
        returncode=None,
        communicate=AsyncMock(side_effect=[asyncio.CancelledError(), (b"", b"")]),
        kill=Mock(),
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    with pytest.raises(asyncio.CancelledError):
        await ExecSecretResolver().resolve(
            parse_secret_ref("exec://op?read?registry-login")
        )
    proc.kill.assert_called_once()
    assert proc.communicate.await_count == 2
