"""Persistence, transport parity, and leakage regressions for issue #3423."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import omnigent_bridge as bridge_router
from api_service.db.models import OmnigentHostAuthProfileRecord
from moonmind.omnigent.bridge_embedded import verify_embedded_host_auth
from moonmind.omnigent.bridge_proxy import OmnigentBridgeError
from moonmind.omnigent.host_auth_adapter import OmnigentHostAuthAdapter
from moonmind.omnigent.host_auth_profile import (
    HostAuthCredentialProfile,
    HostAuthProfileError,
    ResolvedHostAuthCredentials,
    profile_persistence_metadata,
)
from moonmind.omnigent.host_auth_store import HostAuthProfileStore


@pytest.fixture
async def host_auth_store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/host-auth.db")
    async with engine.begin() as connection:
        await connection.run_sync(OmnigentHostAuthProfileRecord.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield HostAuthProfileStore(factory), factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_lifecycle_is_atomic_generation_checked_and_redacted(
    host_auth_store,
) -> None:
    store, factory = host_auth_store
    sentinel = "sentinel-host-secret-never-durable"
    initial = HostAuthCredentialProfile("managed", "env://HOST_ONE", 1)
    await store.put(initial)

    rotated = await store.rotate(
        new_secret_ref="env://HOST_TWO", overlap=timedelta(minutes=5)
    )
    assert (rotated.current_generation, rotated.previous_generation) == (2, 1)

    # A lifecycle writer that observed generation 1 cannot overwrite generation 2.
    with pytest.raises(RuntimeError, match="changed during lifecycle update"):
        await store.put(initial, expected_generation=1)
    assert (await store.get_active()).current_generation == 2

    revoked = await store.revoke()
    assert revoked.revoked is True
    assert revoked.previous_generation is None
    async with factory() as session:
        row = await session.get(OmnigentHostAuthProfileRecord, "managed")
        durable = str(row.metadata_json)
    assert sentinel not in durable
    assert "HOST_TWO" in durable  # Safe SecretRefs are durable; bodies are not.
    assert "previousSecretRef': None" in durable


@pytest.mark.asyncio
async def test_failed_rotation_validation_rolls_back_database_state(host_auth_store) -> None:
    store, _ = host_auth_store
    await store.put(HostAuthCredentialProfile("managed", "env://HOST_ONE", 4))
    with pytest.raises(HostAuthProfileError) as excinfo:
        await store.rotate(
            new_secret_ref="env://HOST_TWO", overlap=timedelta(minutes=16)
        )
    assert excinfo.value.code == "host_auth_rotation_invalid"
    current = await store.get_active()
    assert current.current_generation == 4
    assert current.current_secret_ref == "env://HOST_ONE"


@pytest.mark.asyncio
async def test_operator_api_serialization_and_failures_never_return_secret_body(
    monkeypatch, host_auth_store
) -> None:
    store, _ = host_auth_store
    sentinel = "sentinel-host-secret-never-returned"
    monkeypatch.setattr(bridge_router, "_host_auth_store", lambda: store)
    monkeypatch.setattr(
        bridge_router,
        "resolve_host_auth_credentials",
        AsyncMock(
            return_value=ResolvedHostAuthCredentials(
                HostAuthCredentialProfile("managed", "env://HOST_ONE", 1),
                {1: sentinel},
            )
        ),
    )
    result = await bridge_router.put_embedded_host_auth_profile(
        bridge_router.HostAuthProfilePutRequest(
            profileId="managed", currentSecretRef="env://HOST_ONE", currentGeneration=1
        ),
        user=SimpleNamespace(is_superuser=True),
    )
    assert sentinel not in str(result)
    assert "currentSecretRef" not in result

    monkeypatch.setattr(
        bridge_router,
        "resolve_host_auth_credentials",
        AsyncMock(
            side_effect=HostAuthProfileError(
                "credential unavailable", code="host_auth_secret_unavailable"
            )
        ),
    )
    with pytest.raises(HTTPException) as excinfo:
        await bridge_router.rotate_embedded_host_auth_profile(
            bridge_router.HostAuthRotateRequest(
                newSecretRef="env://HOST_TWO", overlapSeconds=60
            ),
            user=SimpleNamespace(is_superuser=True),
        )
    assert sentinel not in str(excinfo.value.detail)
    assert (await store.get_active()).current_generation == 1


class _Headers(dict):
    def getlist(self, key: str):
        return [value for name, value in self.items() if name.lower() == key.lower()]


class _HandshakeSocket:
    def __init__(self, headers):
        self.headers = headers
        self.closes = []
        self.accepted = False

    async def close(self, code, reason=None):
        self.closes.append((code, reason))

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        return "{}"

    async def send_text(self, value):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (HostAuthProfileError("revoked", code="host_auth_revoked"), 4403),
        (HostAuthProfileError("disabled", code="host_auth_disabled"), 4403),
        (HostAuthProfileError("incompatible", code="host_auth_profile_incompatible"), 1013),
    ],
)
async def test_websocket_profile_failure_close_code_matrix(
    monkeypatch, failure, expected_code
) -> None:
    socket = _HandshakeSocket({})
    monkeypatch.setattr(bridge_router, "get_bridge_config", lambda: _embedded_config())
    monkeypatch.setattr(
        bridge_router, "_active_host_auth_profile", AsyncMock(side_effect=failure)
    )
    await bridge_router.embedded_omnigent_host_tunnel(socket, "host")
    assert socket.closes == [(expected_code, failure.code)]
    assert failure.args[0] not in str(socket.closes)


@pytest.mark.asyncio
async def test_connected_tunnel_is_drained_immediately_after_revocation(monkeypatch) -> None:
    token = "sentinel-connected-token"
    runner_id = OmnigentHostAuthAdapter(
        allowed_tokens=frozenset({token})
    ).runner_id_for_binding_token(token)
    profile = HostAuthCredentialProfile("managed", "env://HOST", 3)
    resolved = ResolvedHostAuthCredentials(profile, {3: token})
    socket = _HandshakeSocket(_Headers({"X-Omnigent-Runner-Tunnel-Token": token}))
    facade = SimpleNamespace(disconnect_host=AsyncMock())
    channel = SimpleNamespace()
    monkeypatch.setattr(bridge_router, "get_bridge_config", lambda: _embedded_config())
    monkeypatch.setattr(
        bridge_router, "_active_host_auth_profile", AsyncMock(return_value=profile)
    )
    monkeypatch.setattr(
        bridge_router,
        "resolve_host_auth_credentials",
        AsyncMock(
            side_effect=[
                resolved,
                HostAuthProfileError("revoked", code="host_auth_revoked"),
            ]
        ),
    )
    monkeypatch.setattr(bridge_router.embedded_host_channels, "connect", lambda **_: channel)
    monkeypatch.setattr(bridge_router.embedded_host_channels, "disconnect", lambda _: None)
    monkeypatch.setattr(
        bridge_router, "OmnigentEmbeddedHostProtocolFacade", lambda **_: facade
    )
    await bridge_router.embedded_omnigent_host_tunnel(socket, runner_id)
    assert socket.accepted is True
    assert socket.closes == [(4403, "host_auth_revoked")]
    facade.disconnect_host.assert_awaited_once()


def test_http_auth_parity_and_cross_channel_sentinel_scan(caplog) -> None:
    token = "sentinel-http-token"
    config = _embedded_config()
    cases = [
        {},
        {"Authorization": f"Bearer {token}"},
        {"Cookie": f"session={token}"},
        {"X-Omnigent-Runner-Tunnel-Token": "malformed"},
    ]
    for headers in cases:
        with pytest.raises(OmnigentBridgeError) as excinfo:
            verify_embedded_host_auth(
                headers=headers,
                config=config,
                configured_credentials={9: token},
                credential_profile_id="managed",
            )
        assert token not in str(excinfo.value)

    assert token not in caplog.text

    durable_or_temporal = {
        "profile": profile_persistence_metadata(
            HostAuthCredentialProfile("managed", "env://HOST", 9)
        ),
        "authContext": {
            "credentialProfileId": "managed",
            "credentialGeneration": 9,
        },
        "diagnostics": {"code": "host_auth_rejected"},
        "artifacts": [{"code": "host_auth_rejected"}],
    }
    assert token not in str(durable_or_temporal)


def _embedded_config():
    return bridge_router.parse_bridge_config(
        {
            "enabled": True,
            "compatibility": {"hostProtocolMode": "embedded"},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://proxy",
                    "liveSmokeEvidenceRef": "artifact://smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://auth",
                }
            },
        }
    )
