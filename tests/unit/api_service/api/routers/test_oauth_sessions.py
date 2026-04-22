"""Unit tests for OAuth session API router behavior."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.future import select
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import (
    Base,
    ManagedAgentOAuthSession,
    ManagedAgentProviderProfile,
    OAuthSessionStatus,
    ProviderCredentialSource,
    RuntimeMaterializationMode,
)
from api_service.main import app
from api_service.api.routers.oauth_sessions import (
    _handle_oauth_terminal_ws_message,
    _make_terminal_output_sender,
)
from moonmind.workflows.temporal.runtime.terminal_bridge import (
    InMemoryPtyAdapter,
    TerminalBridgeConnection,
)


@pytest.fixture(scope="module")
def _module_db(tmp_path_factory):
    """Create a single SQLite engine and schema for the module."""
    import asyncio

    tmp = tmp_path_factory.mktemp("integration_db_oauth")
    db_url = f"sqlite+aiosqlite:///{tmp}/shared.db"

    async def _setup():
        engine = create_async_engine(db_url, future=True)
        session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, session_maker

    async def _teardown(engine):
        await engine.dispose()

    engine, session_maker = asyncio.run(_setup())
    original = (db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker)
    db_base.DATABASE_URL = db_url
    db_base.engine = engine
    db_base.async_session_maker = session_maker
    yield
    db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker = original
    asyncio.run(_teardown(engine))


@pytest.fixture
def client_app(_module_db) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _oauth_payload(profile_id: str) -> dict[str, object]:
    return {
        "runtime_id": "codex_cli",
        "profile_id": profile_id,
        "volume_ref": "codex_auth_volume",
        "account_label": "codex account",
        "max_parallel_runs": 1,
        "cooldown_after_429_seconds": 300,
        "rate_limit_policy": "backoff",
    }


@pytest.mark.asyncio
async def test_create_oauth_session_expires_stale_active_before_conflict_check(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_profile_id = "codex-cli-stale-profile"

    async with db_base.async_session_maker() as session:
        stale = ManagedAgentOAuthSession(
            session_id="oas_staleexisting1",
            runtime_id="codex_cli",
            profile_id=stale_profile_id,
            status=OAuthSessionStatus.PENDING,
            requested_by_user_id="None",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=80),
        )
        session.add(stale)
        await session.commit()

    async def _noop_start(_session_model):
        return None

    monkeypatch.setattr(
        "api_service.services.oauth_session_service.start_oauth_session_workflow",
        _noop_start,
    )

    async with client_app as client:
        response = await client.post(
            "/api/v1/oauth-sessions",
            json=_oauth_payload(stale_profile_id),
        )

    assert response.status_code == 201
    created = response.json()
    assert created["profile_id"] == stale_profile_id
    assert created["status"] == OAuthSessionStatus.PENDING.value

    async with db_base.async_session_maker() as session:
        stale_row = await session.get(ManagedAgentOAuthSession, "oas_staleexisting1")
        assert stale_row is not None
        assert stale_row.status == OAuthSessionStatus.EXPIRED
        assert stale_row.failure_reason is not None


@pytest.mark.asyncio
async def test_create_codex_oauth_session_applies_durable_auth_volume_defaults(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    async def _capture_start(session_model):
        captured["volume_ref"] = session_model.volume_ref
        captured["volume_mount_path"] = session_model.volume_mount_path
        captured["session_transport"] = session_model.session_transport
        captured["metadata_json"] = session_model.metadata_json

    monkeypatch.setattr(
        "api_service.services.oauth_session_service.start_oauth_session_workflow",
        _capture_start,
    )

    payload = {
        "runtime_id": "codex_cli",
        "profile_id": "codex-cli-default-volume",
        "account_label": "codex account",
    }
    async with client_app as client:
        response = await client.post("/api/v1/oauth-sessions", json=payload)

    assert response.status_code == 201
    assert response.json()["session_transport"] == "moonmind_pty_ws"
    assert captured["volume_ref"] == "codex_auth_volume"
    assert captured["volume_mount_path"] == "/home/app/.codex"
    assert captured["session_transport"] == "moonmind_pty_ws"
    assert captured["metadata_json"]["provider_id"] == "openai"
    assert captured["metadata_json"]["provider_label"] == "OpenAI"


@pytest.mark.asyncio
async def test_create_claude_oauth_session_applies_profile_and_transport_defaults(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}
    profile_id = "claude_anthropic_route_defaults"

    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id="claude_code",
                provider_id="anthropic",
                provider_label="Anthropic",
                credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                volume_ref="claude_auth_volume",
                volume_mount_path="/home/app/.claude",
                account_label="Claude Anthropic OAuth",
                clear_env_keys=[
                    "ANTHROPIC_API_KEY",
                    "CLAUDE_API_KEY",
                    "OPENAI_API_KEY",
                ],
            )
        )
        await session.commit()

    async def _capture_start(session_model):
        captured["profile_id"] = session_model.profile_id
        captured["volume_ref"] = session_model.volume_ref
        captured["volume_mount_path"] = session_model.volume_mount_path
        captured["session_transport"] = session_model.session_transport
        captured["metadata_json"] = session_model.metadata_json

    monkeypatch.setattr(
        "api_service.services.oauth_session_service.start_oauth_session_workflow",
        _capture_start,
    )

    async with client_app as client:
        response = await client.post(
            "/api/v1/oauth-sessions",
            json={
                "runtime_id": "claude_code",
                "profile_id": profile_id,
                "account_label": "Claude Anthropic OAuth",
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["runtime_id"] == "claude_code"
    assert body["profile_id"] == profile_id
    assert body["session_transport"] == "moonmind_pty_ws"
    assert body["profile_summary"]["profile_id"] == profile_id
    assert body["profile_summary"]["runtime_id"] == "claude_code"
    assert body["profile_summary"]["provider_id"] == "anthropic"
    assert captured["profile_id"] == profile_id
    assert captured["volume_ref"] == "claude_auth_volume"
    assert captured["volume_mount_path"] == "/home/app/.claude"
    assert captured["session_transport"] == "moonmind_pty_ws"
    assert captured["metadata_json"]["provider_id"] == "anthropic"
    assert captured["metadata_json"]["provider_label"] == "Anthropic"


@pytest.mark.asyncio
async def test_create_oauth_session_returns_terminal_transport_refs(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_id = "codex-cli-terminal-refs"

    async def _capture_start(session_model):
        session_model.terminal_session_id = f"term_{session_model.session_id}"
        session_model.terminal_bridge_id = f"br_{session_model.session_id}"
        session_model.session_transport = "moonmind_pty_ws"

    monkeypatch.setattr(
        "api_service.services.oauth_session_service.start_oauth_session_workflow",
        _capture_start,
    )

    async with client_app as client:
        response = await client.post(
            "/api/v1/oauth-sessions",
            json=_oauth_payload(profile_id),
        )

    assert response.status_code == 201
    body = response.json()
    assert body["terminal_session_id"] == f"term_{body['session_id']}"
    assert body["terminal_bridge_id"] == f"br_{body['session_id']}"
    assert body["session_transport"] == "moonmind_pty_ws"


@pytest.mark.asyncio
async def test_reconnect_oauth_session_preserves_terminal_transport(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}
    old_session_id = "oas_reconnectpty1"
    profile_id = "codex-cli-reconnect-pty"

    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentOAuthSession(
                session_id=old_session_id,
                runtime_id="codex_cli",
                profile_id=profile_id,
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                session_transport="moonmind_pty_ws",
                status=OAuthSessionStatus.FAILED,
                requested_by_user_id="None",
                metadata_json={"provider_id": "openai"},
            )
        )
        await session.commit()

    async def _capture_start(session_model):
        captured["session_transport"] = session_model.session_transport
        captured["session_id"] = session_model.session_id

    monkeypatch.setattr(
        "api_service.services.oauth_session_service.start_oauth_session_workflow",
        _capture_start,
    )

    async with client_app as client:
        response = await client.post(
            f"/api/v1/oauth-sessions/{old_session_id}/reconnect"
        )

    assert response.status_code == 201
    body = response.json()
    assert body["session_id"] != old_session_id
    assert body["session_transport"] == "moonmind_pty_ws"
    assert captured["session_transport"] == "moonmind_pty_ws"


@pytest.mark.asyncio
async def test_oauth_session_response_redacts_secret_like_failure_reason(
    client_app: AsyncClient, _module_db
) -> None:
    session_id = "oas_redactfailure1"
    raw_secret = "sk-test-oauth-secret-value"

    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentOAuthSession(
                session_id=session_id,
                runtime_id="codex_cli",
                profile_id="codex-cli-redact-failure",
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                status=OAuthSessionStatus.FAILED,
                requested_by_user_id="None",
                failure_reason=f"token={raw_secret} in /home/app/.codex/auth.json",
            )
        )
        await session.commit()

    async with client_app as client:
        response = await client.get(f"/api/v1/oauth-sessions/{session_id}")

    assert response.status_code == 200
    payload = response.json()
    assert raw_secret not in response.text
    assert "/home/app/.codex/auth.json" not in response.text
    assert payload["failure_reason"] == "token=[REDACTED] in [REDACTED_AUTH_PATH]"


@pytest.mark.asyncio
async def test_oauth_session_response_includes_safe_provider_profile_summary(
    client_app: AsyncClient, _module_db
) -> None:
    session_id = "oas_profilesummary1"
    profile_id = "codex-cli-profile-summary"
    raw_secret = "sk-test-profile-summary-secret"

    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id="codex_cli",
                provider_id="openai",
                provider_label="OpenAI",
                credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                account_label="codex account",
                secret_refs={"provider_api_key": "env://OPENAI_API_KEY"},
                env_template={"OPENAI_API_KEY": raw_secret},
                enabled=True,
                is_default=True,
            )
        )
        session.add(
            ManagedAgentOAuthSession(
                session_id=session_id,
                runtime_id="codex_cli",
                profile_id=profile_id,
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                account_label="codex account",
                status=OAuthSessionStatus.FAILED,
                requested_by_user_id="None",
                failure_reason=f"token={raw_secret} in /home/app/.codex/auth.json",
            )
        )
        await session.commit()

    async with client_app as client:
        response = await client.get(f"/api/v1/oauth-sessions/{session_id}")

    assert response.status_code == 200
    payload = response.json()
    assert raw_secret not in response.text
    assert "/home/app/.codex/auth.json" not in response.text
    assert payload["failure_reason"] == "token=[REDACTED] in [REDACTED_AUTH_PATH]"
    assert payload["profile_summary"] == {
        "profile_id": profile_id,
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "provider_label": "OpenAI",
        "credential_source": "oauth_volume",
        "runtime_materialization_mode": "oauth_home",
        "account_label": "codex account",
        "enabled": True,
        "is_default": True,
        "rate_limit_policy": "backoff",
    }
    assert "secret_refs" not in payload["profile_summary"]
    assert "env_template" not in payload["profile_summary"]
    assert "volume_ref" not in payload["profile_summary"]
    assert "volume_mount_path" not in payload["profile_summary"]


@pytest.mark.asyncio
async def test_oauth_session_response_omits_profile_summary_for_other_owner(
    client_app: AsyncClient, _module_db
) -> None:
    session_id = "oas_profilesummary2"
    profile_id = "codex-cli-profile-other-owner"

    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id="codex_cli",
                provider_id="openai",
                provider_label="Other Owner",
                credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                account_label="other owner account",
                owner_user_id=uuid.uuid4(),
            )
        )
        session.add(
            ManagedAgentOAuthSession(
                session_id=session_id,
                runtime_id="codex_cli",
                profile_id=profile_id,
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                status=OAuthSessionStatus.SUCCEEDED,
                requested_by_user_id="None",
            )
        )
        await session.commit()

    async with client_app as client:
        response = await client.get(f"/api/v1/oauth-sessions/{session_id}")

    assert response.status_code == 200
    assert response.json()["profile_summary"] is None
    assert "Other Owner" not in response.text
    assert "other owner account" not in response.text


@pytest.mark.asyncio
async def test_create_codex_oauth_session_uses_configured_volume_defaults(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    monkeypatch.setenv("CODEX_VOLUME_NAME", "custom_codex_auth")
    monkeypatch.setenv("CODEX_VOLUME_PATH", "/runtime/codex-home")

    async def _capture_start(session_model):
        captured["volume_ref"] = session_model.volume_ref
        captured["volume_mount_path"] = session_model.volume_mount_path

    monkeypatch.setattr(
        "api_service.services.oauth_session_service.start_oauth_session_workflow",
        _capture_start,
    )

    async with client_app as client:
        response = await client.post(
            "/api/v1/oauth-sessions",
            json={
                "runtime_id": "codex_cli",
                "profile_id": "codex-cli-configured-volume",
                "account_label": "codex account",
            },
        )

    assert response.status_code == 201
    assert captured["volume_ref"] == "custom_codex_auth"
    assert captured["volume_mount_path"] == "/runtime/codex-home"


@pytest.mark.asyncio
async def test_create_oauth_session_rejects_profile_owned_by_another_user(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_id = "codex-cli-other-owner"

    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id="codex_cli",
                provider_id="openai",
                provider_label="OpenAI",
                credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                account_label="other owner",
                owner_user_id=uuid.uuid4(),
            )
        )
        await session.commit()

    async def _unexpected_start(_session_model):
        raise AssertionError(
            "workflow start should not be called for another user's profile"
        )

    monkeypatch.setattr(
        "api_service.services.oauth_session_service.start_oauth_session_workflow",
        _unexpected_start,
    )

    async with client_app as client:
        response = await client.post(
            "/api/v1/oauth-sessions",
            json=_oauth_payload(profile_id),
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to use this profile ID."


@pytest.mark.asyncio
async def test_create_oauth_session_returns_conflict_for_non_stale_active(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_id = "codex-cli-busy-profile"

    async with db_base.async_session_maker() as session:
        active = ManagedAgentOAuthSession(
            session_id="oas_activeexisting1",
            runtime_id="codex_cli",
            profile_id=profile_id,
            status=OAuthSessionStatus.PENDING,
            requested_by_user_id="None",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        session.add(active)
        await session.commit()

    async def _unexpected_start(_session_model):
        raise AssertionError("workflow start should not be called when conflict exists")

    monkeypatch.setattr(
        "api_service.services.oauth_session_service.start_oauth_session_workflow",
        _unexpected_start,
    )

    async with client_app as client:
        response = await client.post("/api/v1/oauth-sessions", json=_oauth_payload(profile_id))

    assert response.status_code == 409
    assert response.json()["detail"] == "An active OAuth session already exists for this profile."


@pytest.mark.asyncio
async def test_create_oauth_session_marks_failed_when_workflow_start_fails(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_id = "codex-cli-start-failure"

    async def _raise_start(_session_model):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr(
        "api_service.services.oauth_session_service.start_oauth_session_workflow",
        _raise_start,
    )

    async with client_app as client:
        response = await client.post(
            "/api/v1/oauth-sessions",
            json=_oauth_payload(profile_id),
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "Failed to start OAuth session workflow. Please retry."

    async with db_base.async_session_maker() as session:
        query = await session.execute(
            select(ManagedAgentOAuthSession)
            .where(ManagedAgentOAuthSession.profile_id == profile_id)
            .order_by(desc(ManagedAgentOAuthSession.created_at))
        )
        failed_row = query.scalars().first()
        assert failed_row is not None
        assert failed_row.status == OAuthSessionStatus.FAILED
        assert failed_row.failure_reason == "Failed to start OAuth session workflow"


@pytest.mark.asyncio
async def test_oauth_terminal_attach_returns_one_time_websocket_token(
    client_app: AsyncClient, _module_db
) -> None:
    session_id = "oas_terminalattach1"

    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentOAuthSession(
                session_id=session_id,
                runtime_id="codex_cli",
                profile_id="codex-cli-terminal-attach",
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                status=OAuthSessionStatus.AWAITING_USER,
                requested_by_user_id="None",
                terminal_session_id="term_oas_terminalattach1",
                terminal_bridge_id="br_oas_terminalattach1",
            )
        )
        await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/oauth-sessions/{session_id}/terminal/attach"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == session_id
    assert payload["terminal_session_id"] == "term_oas_terminalattach1"
    assert payload["terminal_bridge_id"] == "br_oas_terminalattach1"
    assert payload["attach_token"]
    assert payload["attach_token"] in payload["websocket_url"]
    assert "docker" not in response.text.lower()

    async with db_base.async_session_maker() as session:
        row = await session.get(ManagedAgentOAuthSession, session_id)
        assert row is not None
        assert row.metadata_json is not None
        assert row.metadata_json["terminal_attach_token_sha256"] != payload["attach_token"]
        assert row.metadata_json["terminal_attach_token_used"] is False


@pytest.mark.asyncio
async def test_oauth_terminal_attach_rejects_expired_session(
    client_app: AsyncClient, _module_db
) -> None:
    session_id = "oas_terminalexpired1"

    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentOAuthSession(
                session_id=session_id,
                runtime_id="codex_cli",
                profile_id="codex-cli-terminal-expired",
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                status=OAuthSessionStatus.AWAITING_USER,
                requested_by_user_id="None",
                terminal_session_id="term_oas_terminalexpired1",
                terminal_bridge_id="br_oas_terminalexpired1",
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )
        await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/oauth-sessions/{session_id}/terminal/attach"
        )

    assert response.status_code == 410
    assert response.json()["detail"] == "OAuth terminal session has expired."

    async with db_base.async_session_maker() as session:
        row = await session.get(ManagedAgentOAuthSession, session_id)
        assert row is not None
        assert row.metadata_json is None


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent_json: list[dict[str, object]] = []
        self.sent_text: list[str] = []
        self.closed: list[int] = []

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent_json.append(payload)

    async def send_text(self, payload: str) -> None:
        self.sent_text.append(payload)

    async def close(self, code: int) -> None:
        self.closed.append(code)


class _FailingWritePtyAdapter(InMemoryPtyAdapter):
    async def write_bytes(self, data: bytes) -> None:
        raise BrokenPipeError("auth runner exited")


@pytest.mark.asyncio
async def test_oauth_terminal_message_handler_proxies_to_pty_with_safe_metadata() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminalwspty1",
        terminal_bridge_id="br_oas_terminalwspty1",
        owner_user_id="None",
    )
    pty = InMemoryPtyAdapter()
    websocket = _FakeWebSocket()

    should_close, close_reason = await _handle_oauth_terminal_ws_message(
        {"text": '{"type":"input","data":"codex login\\n"}'},
        bridge=bridge,
        pty_adapter=pty,
        websocket=websocket,
    )
    assert should_close is False
    assert close_reason is None
    assert websocket.sent_json[-1] == {"type": "input_ack", "bytes": 12}

    await _handle_oauth_terminal_ws_message(
        {"text": '{"type":"resize","cols":100,"rows":30}'},
        bridge=bridge,
        pty_adapter=pty,
        websocket=websocket,
    )
    await _handle_oauth_terminal_ws_message(
        {"text": '{"type":"heartbeat"}'},
        bridge=bridge,
        pty_adapter=pty,
        websocket=websocket,
    )
    should_close, close_reason = await _handle_oauth_terminal_ws_message(
        {"text": '{"type":"close"}'},
        bridge=bridge,
        pty_adapter=pty,
        websocket=websocket,
    )

    assert should_close is True
    assert close_reason == "client_closed"
    assert pty.written == [b"codex login\n"]
    assert pty.resizes == [(100, 30)]
    assert websocket.closed == [1000]
    metadata = bridge.safe_metadata()
    assert metadata == {
        "terminal_heartbeat_count": 1,
        "terminal_input_event_count": 1,
        "terminal_output_event_count": 0,
        "terminal_last_cols": 100,
        "terminal_last_rows": 30,
    }
    assert "codex login" not in str(metadata)


@pytest.mark.asyncio
async def test_oauth_terminal_message_handler_closes_on_pty_write_failure() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminalwsbroken1",
        terminal_bridge_id="br_oas_terminalwsbroken1",
        owner_user_id="None",
    )
    websocket = _FakeWebSocket()

    should_close, close_reason = await _handle_oauth_terminal_ws_message(
        {"text": '{"type":"input","data":"codex login\\n"}'},
        bridge=bridge,
        pty_adapter=_FailingWritePtyAdapter(),
        websocket=websocket,
    )

    assert should_close is True
    assert close_reason == "pty_disconnected"
    assert websocket.sent_json == [
        {"type": "error", "detail": "OAuth terminal PTY disconnected."}
    ]
    assert websocket.closed == [1011]


@pytest.mark.asyncio
async def test_oauth_terminal_output_sender_preserves_split_utf8_sequences() -> None:
    websocket = _FakeWebSocket()
    send_terminal_output = _make_terminal_output_sender(websocket)

    await send_terminal_output("Auth code: ".encode("utf-8") + b"\xe2")
    await send_terminal_output(b"\x82\xac\r\n")

    assert websocket.sent_text == ["Auth code: ", "€\r\n"]


@pytest.mark.asyncio
async def test_oauth_terminal_message_handler_rejects_generic_exec_frame() -> None:
    bridge = TerminalBridgeConnection(
        session_id="oas_terminalwsreject1",
        terminal_bridge_id="br_oas_terminalwsreject1",
        owner_user_id="None",
    )
    websocket = _FakeWebSocket()

    should_close, close_reason = await _handle_oauth_terminal_ws_message(
        {"text": '{"type":"docker_exec","command":"sh"}'},
        bridge=bridge,
        pty_adapter=InMemoryPtyAdapter(),
        websocket=websocket,
    )

    assert should_close is True
    assert close_reason == "generic Docker exec and task terminal frames are not supported"
    assert websocket.sent_json == [
        {
            "type": "error",
            "detail": "generic Docker exec and task terminal frames are not supported",
        }
    ]
    assert websocket.closed == [4400]


@pytest.mark.asyncio
async def test_finalize_oauth_session_rejects_failed_volume_verification(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "oas_verifyfailed1"

    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentOAuthSession(
                session_id=session_id,
                runtime_id="codex_cli",
                profile_id="codex-cli-failed-verify",
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                status=OAuthSessionStatus.AWAITING_USER,
                requested_by_user_id="None",
                account_label="codex account",
            )
        )
        await session.commit()

    stopped = {}
    failed_signal = {}

    async def _failed_verify(**_kwargs):
        return {"verified": False, "reason": "no_credentials_found"}

    async def _capture_stop(session_obj):
        stopped["session_id"] = session_obj.session_id
        stopped["container_name"] = session_obj.container_name

    async def _capture_fail_signal(session_id, reason):
        failed_signal["session_id"] = session_id
        failed_signal["reason"] = reason

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.providers.volume_verifiers.verify_volume_credentials",
        _failed_verify,
    )
    monkeypatch.setattr(
        "api_service.api.routers.oauth_sessions._stop_oauth_auth_runner",
        _capture_stop,
    )
    monkeypatch.setattr(
        "api_service.api.routers.oauth_sessions._fail_oauth_session_workflow",
        _capture_fail_signal,
    )

    async with db_base.async_session_maker() as session:
        row = await session.get(ManagedAgentOAuthSession, session_id)
        assert row is not None
        row.container_name = "moonmind_auth_oas_verifyfailed1"
        await session.commit()

    async with client_app as client:
        response = await client.post(f"/api/v1/oauth-sessions/{session_id}/finalize")

    assert response.status_code == 400
    assert response.json()["detail"] == "Volume verification failed: no_credentials_found"

    async with db_base.async_session_maker() as session:
        row = await session.get(ManagedAgentOAuthSession, session_id)
        assert row is not None
        assert row.status == OAuthSessionStatus.FAILED
        assert row.failure_reason == "Volume verification failed: no_credentials_found"
    assert stopped == {
        "session_id": session_id,
        "container_name": "moonmind_auth_oas_verifyfailed1",
    }
    assert failed_signal == {
        "session_id": session_id,
        "reason": "Volume verification failed: no_credentials_found",
    }


@pytest.mark.asyncio
async def test_finalize_oauth_session_registers_oauth_home_codex_profile(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "oas_registercodex1"
    profile_id = "codex-cli-register-oauth"

    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentOAuthSession(
                session_id=session_id,
                runtime_id="codex_cli",
                profile_id=profile_id,
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                status=OAuthSessionStatus.AWAITING_USER,
                requested_by_user_id="None",
                account_label="codex account",
                metadata_json={
                    "provider_id": "openai",
                    "provider_label": "OpenAI",
                    "max_parallel_runs": 2,
                    "cooldown_after_429_seconds": 300,
                    "rate_limit_policy": "backoff",
                },
            )
        )
        await session.commit()

    stopped = {}
    completed_signal = {}

    async def _successful_verify(**kwargs):
        assert kwargs["runtime_id"] == "codex_cli"
        assert kwargs["volume_ref"] == "codex_auth_volume"
        assert kwargs["volume_mount_path"] == "/home/app/.codex"
        return {"verified": True, "found": ["auth.json"], "missing": []}

    async def _noop_sync(**_kwargs):
        return None

    async def _capture_stop(session_obj):
        stopped["session_id"] = session_obj.session_id
        stopped["container_name"] = session_obj.container_name

    async def _capture_complete_signal(signal_session_id):
        completed_signal["session_id"] = signal_session_id

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.providers.volume_verifiers.verify_volume_credentials",
        _successful_verify,
    )
    monkeypatch.setattr(
        "api_service.api.routers.oauth_sessions.sync_provider_profile_manager",
        _noop_sync,
    )
    monkeypatch.setattr(
        "api_service.api.routers.oauth_sessions._stop_oauth_auth_runner",
        _capture_stop,
    )
    monkeypatch.setattr(
        "api_service.api.routers.oauth_sessions._complete_oauth_session_workflow",
        _capture_complete_signal,
    )

    async with db_base.async_session_maker() as session:
        row = await session.get(ManagedAgentOAuthSession, session_id)
        assert row is not None
        row.container_name = "moonmind_auth_oas_registercodex1"
        await session.commit()

    async with client_app as client:
        response = await client.post(f"/api/v1/oauth-sessions/{session_id}/finalize")

    assert response.status_code == 200

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, profile_id)
        assert profile is not None
        assert profile.runtime_id == "codex_cli"
        assert profile.provider_id == "openai"
        assert profile.provider_label == "OpenAI"
        assert profile.credential_source == ProviderCredentialSource.OAUTH_VOLUME
        assert (
            profile.runtime_materialization_mode
            == RuntimeMaterializationMode.OAUTH_HOME
        )
        assert profile.volume_ref == "codex_auth_volume"
        assert profile.volume_mount_path == "/home/app/.codex"
        assert profile.max_parallel_runs == 2
    assert stopped == {
        "session_id": session_id,
        "container_name": "moonmind_auth_oas_registercodex1",
    }
    assert completed_signal == {"session_id": session_id}
