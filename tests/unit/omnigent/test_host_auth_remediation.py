"""Persistence, transport parity, and leakage regressions for issue #3423."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import omnigent_bridge as bridge_router
from api_service.api.routers import omnigent_bridge_composition as bridge_composition
from api_service.db.models import OmnigentHostAuthProfileRecord
from moonmind.omnigent.bridge_artifacts import OmnigentArtifactGateway, capture_artifact_json
from moonmind.omnigent.bridge_security import redact_raw_events
from moonmind.omnigent.host_auth_adapter import (
    OmnigentHostAuthAdapter,
    UpstreamHostAuthError,
)
from moonmind.omnigent.host_auth_contracts import (
    HostAuthCredentialProfile,
    HostAuthProfileError,
    ResolvedHostAuthCredentials,
    profile_persistence_metadata,
    rotate_host_auth_profile,
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
    monkeypatch.setattr(bridge_composition, "build_host_auth_store", lambda: store)
    monkeypatch.setattr(
        bridge_composition,
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
        bridge_composition,
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
    monkeypatch.setattr(bridge_composition, "build_host_auth_store", lambda: store)
    monkeypatch.setattr(
        bridge_composition,
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
@pytest.mark.parametrize("tunnel", ["host", "runner"])
async def test_retired_tunnels_close_without_host_auth_admission(
    monkeypatch, tunnel
) -> None:
    """Retired tunnels refuse before touching credentials or channels.

    MoonLadderStudios/MoonMind#3955: no new embedded-transport admission means
    the handshake never resolves the host-auth profile, so revocation state
    cannot change the outcome.
    """

    resolve = AsyncMock(side_effect=AssertionError("must not resolve profiles"))
    monkeypatch.setattr(
        bridge_composition, "resolve_active_host_auth_profile", resolve
    )
    socket = _HandshakeSocket({})

    if tunnel == "host":
        await bridge_router.retired_embedded_host_tunnel(socket, "host")
    else:
        await bridge_router.retired_embedded_runner_tunnel(socket, "runner")

    assert socket.accepted is False
    assert socket.closes == [(4404, "omnigent_embedded_transport_retired")]
    resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (
            HostAuthProfileError(
                "sensitive revoked detail", code="host_auth_revoked"
            ),
            "host_auth_revoked",
        ),
        (
            HostAuthProfileError(
                "sensitive disabled detail", code="host_auth_disabled"
            ),
            "host_auth_disabled",
        ),
        (
            HostAuthProfileError(
                "sensitive incompatible detail",
                code="host_auth_profile_incompatible",
            ),
            "host_auth_profile_incompatible",
        ),
        (
            HostAuthProfileError(
                "sensitive unavailable detail",
                code="host_auth_secret_unavailable",
            ),
            "host_auth_secret_unavailable",
        ),
    ],
)
async def test_host_auth_profile_failures_keep_stable_readiness_codes(
    monkeypatch, failure, code
) -> None:
    """Profile failures retain stable, secret-free readiness codes.

    The embedded handshake that used to map these onto HTTP/WS retry signals
    was retired (MoonLadderStudios/MoonMind#3955); the surviving lifecycle
    surface is the readiness projection, which must keep the exact codes.
    """

    monkeypatch.setattr(
        bridge_composition,
        "resolve_active_host_auth_profile",
        AsyncMock(side_effect=failure),
    )
    readiness = await bridge_composition.evaluate_active_host_auth_readiness()

    assert readiness == {"ready": False, "code": code}
    assert failure.args[0] not in str(readiness)


class _RecordingArtifactGateway(OmnigentArtifactGateway):
    def __init__(self) -> None:
        self.payloads = []

    async def write_json(self, *, request, name, payload, link_type):
        self.payloads.append(payload)
        return f"artifact://{name}"


@pytest.mark.asyncio
async def test_cross_channel_serializers_redact_or_reject_host_secret(caplog) -> None:
    token = "sentinel-http-token"

    # The real raw-event persistence serializer removes credential-shaped data.
    persisted_events = redact_raw_events(
        [{"type": "host.failure", "hostCredential": token}]
    )
    assert token not in str(persisted_events)

    # The Temporal-facing checkpoint contract rejects credential material.
    with pytest.raises(ValueError, match="reference, not credential data"):
        OmnigentCheckpointIdentity(
            workflowId="workflow",
            runId="run",
            logicalStepId="step",
            stepExecutionId="execution",
            attemptOrdinal=1,
            boundary="after_execution",
            providerProfileId="provider",
            credentialRef="credential://provider",
            credentialGeneration=9,
            hostBindingRef="binding",
            endpointRef=f"token={token}",
            bridgeSessionId="bridge",
            externalStateRef="artifact://external",
            externalStateDigest="sha256:" + "0" * 64,
            idempotencyKey="idem",
            effectiveLaunchRef="omnigent-launch:sha256:" + "0" * 64,
            executionProfileRef="profile://provider",
            launchPolicyRef="policy://default",
            workspaceLocator={"kind": "sandbox", "workspaceId": "workspace"},
            baselineCommit="abc",
            headCommit="def",
            headRef="artifact://head",
            headDigest="sha256:" + "1" * 64,
            workspaceCheckpointRef="artifact://workspace",
            workspaceCheckpointDigest="sha256:" + "2" * 64,
            sourceBranch="main",
            publicationState="unpublished",
            capturedAt=datetime(2026, 7, 12, tzinfo=UTC),
            producerVersion="test",
            validation={
                "valid": False,
                "liveReattachAvailable": False,
                "workspaceColdRestoreAvailable": False,
                "branchCreationAvailable": False,
                "reasons": ["credential_reference_rejected"],
            },
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
    "headers",
    [
        {},
        (
            _Headers({
                "X-Omnigent-Runner-Tunnel-Token": "current",
                "x-omnigent-runner-tunnel-token": "duplicate",
            })
        ),
        (_Headers({"X-Omnigent-Runner-Tunnel-Token": "invalid"})),
        (_Headers({"X-Omnigent-Runner-Tunnel-Token": "stale"})),
        ({"Authorization": "Bearer current"}),
    ],
)
async def test_pinned_verifier_rejects_tunnel_credentials_without_admission(
    headers,
) -> None:
    """The pinned verifier still rejects bad tunnel credentials.

    The MoonMind-side handshake that used to map these rejections onto the
    embedded tunnel was retired (MoonLadderStudios/MoonMind#3955): the
    retired tunnel refuses before consulting the verifier, so a rejected
    credential can never become a session or lease consumer.
    """

    token = "current"
    adapter = OmnigentHostAuthAdapter(allowed_tokens=frozenset({token}))
    with pytest.raises(UpstreamHostAuthError):
        adapter.verify(headers)

    socket = _HandshakeSocket(headers)
    await bridge_router.retired_embedded_host_tunnel(socket, "untrusted-host")
    assert socket.accepted is False
    assert socket.closes == [(4404, "omnigent_embedded_transport_retired")]
