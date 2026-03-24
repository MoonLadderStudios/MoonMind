"""Tests for MANAGED_API_KEY_REF resolution."""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
    resolve_managed_api_key_reference,
)

pytestmark = pytest.mark.asyncio


async def test_resolve_from_worker_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TEST_SECRET_KEY", "abc123")
    out = await resolve_managed_api_key_reference("MY_TEST_SECRET_KEY")
    assert out == "abc123"


async def test_resolve_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        await resolve_managed_api_key_reference("  ")


async def test_resolve_unknown_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOT_SET_XYZ", raising=False)
    with pytest.raises(ValueError, match="Unable to resolve"):
        await resolve_managed_api_key_reference("NOT_SET_XYZ")
