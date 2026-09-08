"""Replay the API startup failure caused by Compose's empty secret defaults."""

from pathlib import Path

import pytest
from fastapi import FastAPI

from api_service import main as api_main
from api_service.auth_providers import build_moonmind_control_plane_config
from moonmind.security import auth_modes_4120 as auth_modes


@pytest.fixture
def session_key_path(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    monkeypatch.setattr(api_main.settings.oidc, "AUTH_PROVIDER", "disabled")
    monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", "127.0.0.1")
    monkeypatch.delenv("MOONMIND_AUTH_MIGRATION_DECISION", raising=False)
    monkeypatch.delenv("MOONMIND_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("MOONMIND_SESSION_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    # Restore the process-level mode after exercising the real startup owner.
    monkeypatch.setattr(auth_modes, "_ACTIVE_PRODUCTION_MODE", None)
    path = tmp_path / "session-keys" / "moonmind_session_key"
    monkeypatch.setenv("MOONMIND_SESSION_KEY_PATH", str(path))
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("secret_env", [None, ""], ids=["omitted", "compose-empty"])
async def test_startup_generates_durable_key_and_request_reuses_it(
    monkeypatch, session_key_path: Path, secret_env
):
    if secret_env is not None:
        monkeypatch.setenv("MOONMIND_SESSION_SECRET", secret_env)
        monkeypatch.setenv("JWT_SECRET", secret_env)

    app = FastAPI()
    await api_main._initialize_oidc_provider(app)
    first_key = session_key_path.read_bytes()
    assert len(first_key) >= 32
    assert session_key_path.stat().st_mode & 0o777 == 0o600
    assert app.state.auth_production_mode == "disabled"
    assert build_moonmind_control_plane_config().cookie_secret == first_key

    await api_main._initialize_oidc_provider(FastAPI())
    assert session_key_path.read_bytes() == first_key
    assert build_moonmind_control_plane_config().cookie_secret == first_key


@pytest.mark.asyncio
@pytest.mark.parametrize("env_name", ["MOONMIND_SESSION_SECRET", "JWT_SECRET"])
async def test_startup_rejects_explicit_placeholder_without_generating_key(
    monkeypatch, session_key_path: Path, env_name
):
    monkeypatch.setenv(env_name, "devsecret")
    with pytest.raises(RuntimeError, match="insecure placeholder"):
        await api_main._initialize_oidc_provider(FastAPI())
    assert not session_key_path.exists()


@pytest.mark.asyncio
async def test_remote_startup_empty_secrets_still_require_provisioned_key(
    monkeypatch, session_key_path: Path
):
    monkeypatch.setenv("MOONMIND_SESSION_SECRET", "")
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "https://moonmind.example.com")
    with pytest.raises(RuntimeError, match="remote production"):
        await api_main._initialize_oidc_provider(FastAPI())
    assert not session_key_path.exists()
