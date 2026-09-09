"""Compose's omitted session secret must create one durable local generation."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI

from api_service import main
from moonmind.security import auth_modes_4120 as auth_modes

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
REPLAY = json.loads(
    (
        Path(__file__).parent
        / "reliability/replays/session-secret-compose-default/manifest.json"
    ).read_text()
)


@pytest.fixture
def local_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    monkeypatch.setattr(main.settings.oidc, "AUTH_PROVIDER", "disabled")
    monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", "127.0.0.1")
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "")
    monkeypatch.setenv("MOONMIND_SESSION_KEY_PATH", str(tmp_path / "session-key"))
    monkeypatch.delenv("MOONMIND_AUTH_MIGRATION_DECISION", raising=False)
    monkeypatch.setattr(auth_modes, "_ACTIVE_PRODUCTION_MODE", None)
    yield tmp_path / "session-key"


@pytest.mark.parametrize("compose_empty", [False, True])
async def test_local_startup_reuses_generated_key(
    local_startup, monkeypatch, compose_empty
):
    for key, value in REPLAY["composeEnvironment"].items():
        if compose_empty:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)
    await main._initialize_oidc_provider(FastAPI())
    first = local_startup.read_bytes()
    assert len(first) >= 32
    await main._initialize_oidc_provider(FastAPI())
    assert local_startup.read_bytes() == first


@pytest.mark.parametrize("key", ["MOONMIND_SESSION_SECRET", "JWT_SECRET"])
async def test_nonempty_placeholder_still_fails_closed(local_startup, monkeypatch, key):
    monkeypatch.setenv("MOONMIND_SESSION_SECRET", "")
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv(key, "devsecret")
    with pytest.raises(RuntimeError, match="insecure placeholder"):
        await main._initialize_oidc_provider(FastAPI())
    assert not local_startup.exists()


async def test_remote_startup_still_requires_provisioned_key(
    local_startup, monkeypatch
):
    monkeypatch.setenv("MOONMIND_SESSION_SECRET", "")
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "https://moonmind.example.com")
    with pytest.raises(RuntimeError, match="remote production"):
        await main._initialize_oidc_provider(FastAPI())
    assert not local_startup.exists()
