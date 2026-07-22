"""Persistence, transport parity, and leakage regressions for issue #3423."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import omnigent_bridge as bridge_router
from api_service.db.models import OmnigentHostAuthProfileRecord
from moonmind.omnigent.bridge_artifacts import OmnigentArtifactGateway, capture_artifact_json
from moonmind.omnigent.bridge_security import redact_raw_events
from moonmind.omnigent.bridge_embedded import verify_embedded_host_auth
from moonmind.omnigent.bridge_proxy import OmnigentBridgeError
from moonmind.omnigent.host_auth_adapter import (
    OmnigentHostAuthAdapter,
    UpstreamHostAuthError,
)
from moonmind.omnigent.host_auth_profile import (
    HostAuthCredentialProfile,
    HostAuthProfileError,
    ResolvedHostAuthCredentials,
    profile_persistence_metadata,
    rotate_host_auth_profile,
)
from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    parse_bridge_config,
)
from moonmind.omnigent.host_auth_store import HostAuthProfileStore
from moonmind.omnigent.checkpoints import OmnigentCheckpointIdentity
import moonmind.utils.logging as mm_logging


@pytest_asyncio.fixture
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
async def test_database_lifecycle_allows_exactly_one_concurrent_rotation(
    host_auth_store,
) -> None:
    """Two writers observing one generation cannot both publish successors."""

    store, _ = host_auth_store
    await store.put(HostAuthCredentialProfile("managed", "env://HOST_ONE", 1))
    start = asyncio.Event()

    async def rotate(secret_ref: str):
        current = await store.get_active()
        assert current is not None and current.current_generation == 1
        await start.wait()
        candidate = rotate_host_auth_profile(
            current,
            new_secret_ref=secret_ref,
            overlap=timedelta(minutes=5),
        )
        try:
            return await store.put(candidate, expected_generation=1)
        except RuntimeError as exc:
            return exc

    writers = [
        asyncio.create_task(rotate("env://HOST_TWO_A")),
        asyncio.create_task(rotate("env://HOST_TWO_B")),
    ]
    await asyncio.sleep(0)
    start.set()
    results = await asyncio.gather(*writers)

    winners = [
        result for result in results if isinstance(result, HostAuthCredentialProfile)
    ]
    losers = [result for result in results if isinstance(result, RuntimeError)]
    assert len(winners) == len(losers) == 1
    durable = await store.get_active()
    assert durable is not None
    assert durable.current_secret_ref == winners[0].current_secret_ref
    assert durable.current_generation == 2


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


@pytest.mark.asyncio
async def test_operator_profile_put_is_initial_only(monkeypatch, host_auth_store) -> None:
    store, _ = host_auth_store
    await store.put(HostAuthCredentialProfile("managed", "env://HOST_ONE", 2))
    monkeypatch.setattr(bridge_router, "_host_auth_store", lambda: store)
    monkeypatch.setattr(
        bridge_router,
        "resolve_host_auth_credentials",
        AsyncMock(return_value=ResolvedHostAuthCredentials(
            HostAuthCredentialProfile("managed", "env://HOST_STALE", 1), {1: "stale"}
        )),
    )

    with pytest.raises(HTTPException) as excinfo:
        await bridge_router.put_embedded_host_auth_profile(
            bridge_router.HostAuthProfilePutRequest(
                profileId="managed", currentSecretRef="env://HOST_STALE", currentGeneration=1
            ),
            user=SimpleNamespace(is_superuser=True),
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "host_auth_already_configured"
    assert (await store.get_active()).current_generation == 2


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
        (HostAuthProfileError("sensitive revoked detail", code="host_auth_revoked"), 4403),
        (HostAuthProfileError("sensitive disabled detail", code="host_auth_disabled"), 4403),
        (
            HostAuthProfileError(
                "sensitive incompatible detail",
                code="host_auth_profile_incompatible",
            ),
            1013,
        ),
    ],
)
async def test_websocket_profile_failure_close_code_matrix(
    monkeypatch, failure, expected_code
) -> None:
    socket = _HandshakeSocket({})
    monkeypatch.setattr(bridge_router, "get_bridge_config", _embedded_config)
    monkeypatch.setattr(
        bridge_router, "_require_embedded_mode", AsyncMock(return_value=_embedded_config())
    )
    monkeypatch.setattr(
        bridge_router, "_active_host_auth_profile", AsyncMock(side_effect=failure)
    )
    await bridge_router.embedded_omnigent_host_tunnel(socket, "host")
    assert socket.closes == [(expected_code, failure.code)]
    assert failure.args[0] not in str(socket.closes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "http_status", "ws_code", "retryable"),
    [
        (HostAuthProfileError("revoked", code="host_auth_revoked"), 503, 4403, False),
        (HostAuthProfileError("disabled", code="host_auth_disabled"), 503, 4403, False),
        (
            HostAuthProfileError("incompatible", code="host_auth_profile_incompatible"),
            503,
            1013,
            True,
        ),
        (
            HostAuthProfileError("unavailable", code="host_auth_secret_unavailable"),
            503,
            1013,
            True,
        ),
    ],
)
async def test_http_websocket_profile_failure_retryability_matrix(
    monkeypatch, failure, http_status, ws_code, retryable
) -> None:
    """Profile failures retain stable HTTP/WS retry interpretations."""

    monkeypatch.setattr(
        bridge_router, "_active_host_auth_profile", AsyncMock(side_effect=failure)
    )
    with pytest.raises(HTTPException) as http_exc:
        await bridge_router._embedded_auth_context(
            request=SimpleNamespace(headers={}), config=_embedded_config()
        )
    assert http_exc.value.status_code == http_status
    assert http_exc.value.detail["code"] == failure.code
    # RFC 6455 1013 is the stock-host-compatible retry signal; policy failures
    # use the permanent 4403 close. This is an explicit transport contract,
    # independent of the router result being tested below.
    authoritative_retryable_close_codes = frozenset({1013})
    assert (ws_code in authoritative_retryable_close_codes) is retryable

    socket = _HandshakeSocket({})
    monkeypatch.setattr(bridge_router, "get_bridge_config", _embedded_config)
    monkeypatch.setattr(
        bridge_router, "_require_embedded_mode", AsyncMock(return_value=_embedded_config())
    )
    await bridge_router.embedded_omnigent_host_tunnel(socket, "host")
    assert socket.closes == [(ws_code, failure.code)]


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
    monkeypatch.setattr(bridge_router, "get_bridge_config", _embedded_config)
    monkeypatch.setattr(
        bridge_router, "_require_embedded_mode", AsyncMock(return_value=_embedded_config())
    )
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


class _RecordingArtifactGateway(OmnigentArtifactGateway):
    def __init__(self) -> None:
        self.payloads = []

    async def write_json(self, *, request, name, payload, link_type):
        self.payloads.append(payload)
        return f"artifact://{name}"


@pytest.mark.asyncio
async def test_cross_channel_serializers_redact_or_reject_host_secret(caplog) -> None:
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

    # The real raw-event persistence serializer removes credential-shaped data.
    persisted_events = redact_raw_events(
        [{"type": "host.failure", "hostCredential": token}]
    )
    assert token not in str(persisted_events)

    # The Temporal-facing checkpoint contract rejects credential material.
    with pytest.raises(ValueError, match="reference, not credential data"):
        OmnigentCheckpointIdentity(
            providerProfileId="provider",
            credentialGeneration=9,
            hostBindingRef="binding",
            endpointRef=f"token={token}",
            bridgeSessionId="bridge",
            externalStateRef="artifact://external",
            idempotencyKey="idem",
            effectiveLaunchRef="omnigent-launch:sha256:" + "0" * 64,
        )

    # The actual artifact gateway boundary redacts structured diagnostics.
    gateway = _RecordingArtifactGateway()
    await capture_artifact_json(
        gateway,
        SimpleNamespace(),
        {},
        key="diagnosticsRef",
        name="diagnostics.json",
        payload={"hostCredential": token, "code": "host_auth_rejected"},
        link_type="diagnostics.omnigent",
    )
    assert token not in str(gateway.payloads)

    # Structured logging uses the production redactor with the resolved secret.
    caplog.set_level(logging.ERROR)
    logging.getLogger(__name__).error(
        "host handshake failed: %s", mm_logging.SecretRedactor([token]).scrub(token)
    )
    assert token not in caplog.text
    assert "***" in caplog.text

    # Safe profile metadata remains reference-only at its persistence boundary.
    metadata = profile_persistence_metadata(
        HostAuthCredentialProfile("managed", "env://HOST", 9)
    )
    assert token not in str(metadata)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "http_status", "http_code", "ws_code"),
    [
        ({}, 401, "host_auth_rejected", 4401),
        (
            _Headers({
                "X-Omnigent-Runner-Tunnel-Token": "current",
                "x-omnigent-runner-tunnel-token": "duplicate",
            }),
            401,
            "host_auth_rejected",
            4401,
        ),
        (
            _Headers({"X-Omnigent-Runner-Tunnel-Token": "invalid"}),
            401,
            "host_auth_rejected",
            4401,
        ),
        (
            _Headers({"X-Omnigent-Runner-Tunnel-Token": "stale"}),
            401,
            "host_auth_rejected",
            4401,
        ),
        ({"Authorization": "Bearer current"}, 401, "host_auth_rejected", 4401),
    ],
)
async def test_pinned_upstream_http_websocket_rejection_parity(
    monkeypatch, headers, http_status, http_code, ws_code
) -> None:
    """The same pinned-verifier rejection retains HTTP and WS retry semantics."""

    token = "current"
    profile = HostAuthCredentialProfile("managed", "env://HOST", 2)
    resolved = ResolvedHostAuthCredentials(profile, {2: token})
    adapter = OmnigentHostAuthAdapter(allowed_tokens=frozenset({token}))
    with pytest.raises(UpstreamHostAuthError):
        adapter.verify(headers)

    # The pinned verifier exposes one credential-rejection class. MoonMind maps
    # every such failure to permanent authentication rejection on both
    # transports; only server/profile availability uses the retryable 1013 path.
    assert http_status == 401
    assert ws_code == 4401

    monkeypatch.setattr(
        bridge_router, "_active_host_auth_profile", AsyncMock(return_value=profile)
    )
    monkeypatch.setattr(
        bridge_router,
        "resolve_host_auth_credentials",
        AsyncMock(return_value=resolved),
    )
    request = SimpleNamespace(headers=headers)
    with pytest.raises(HTTPException) as http_exc:
        await bridge_router._embedded_auth_context(request=request, config=_embedded_config())
    assert http_exc.value.status_code == http_status
    assert http_exc.value.detail["code"] == http_code

    socket = _HandshakeSocket(headers)
    monkeypatch.setattr(bridge_router, "get_bridge_config", _embedded_config)
    monkeypatch.setattr(
        bridge_router, "_require_embedded_mode", AsyncMock(return_value=_embedded_config())
    )
    await bridge_router.embedded_omnigent_host_tunnel(socket, "untrusted-host")
    assert socket.closes == [(ws_code, http_code)]


def _embedded_config():
    return parse_bridge_config(
        {
            "enabled": True,
            "compatibility": {"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
            "hostConnection": {
                "embedded": {
                    "proxyConformanceEvidenceRef": "artifact://proxy",
                    "liveSmokeEvidenceRef": "artifact://smoke",
                    "hostAuthConformanceEvidenceRef": "artifact://auth",
                }
            },
        }
    )
