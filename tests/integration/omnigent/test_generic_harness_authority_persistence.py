"""Production persistence boundary for generic harness facade authority."""

from __future__ import annotations

import copy

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, OmnigentBridgeSession
from moonmind.omnigent.bridge_store import (
    OmnigentBridgeSessionStore,
    OmnigentIdempotencyError,
)
from moonmind.omnigent.effective_capabilities import (
    CAPABILITY_NAMES,
    resolve_bridge_row_capabilities,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from tests.unit.omnigent.test_effective_capabilities import (
    _generic_harness_authority,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci, pytest.mark.asyncio]


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="generic-authority-workflow",
        idempotencyKey="generic-authority-session",
    )


def _launch(authority: dict) -> dict:
    plan = authority["executionPlan"]
    grants = dict.fromkeys(CAPABILITY_NAMES, True)
    return {
        "snapshotRef": "omnigent-launch:sha256:" + "3" * 64,
        "executionProfileRef": "agent-profile://p/versions/7",
        "executionProfileDigest": "sha256:agent",
        "launchPolicyRef": "policy://launch/3",
        "executionPlanRef": plan["planRef"],
        "executionRealizerRef": "generic-omnigent-host@1",
        "agentProfileCapabilities": grants,
        "capabilities": grants,
        "sessionStateCapabilities": grants,
        "policyAuthority": {
            "policyId": "generic-launch",
            "policyVersion": 1,
            "policyRef": "policy://launch/3",
            "policyDigest": "sha256:policy",
            "snapshotRef": "artifact://policy",
            "validation": {"valid": True},
        },
    }


@pytest_asyncio.fixture
async def persisted(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/authority.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    store = OmnigentBridgeSessionStore(factory)
    authority = _generic_harness_authority()
    request = _request()
    await store.bind_profile_authorization(
        request=request,
        endpoint_ref="default",
        provider_profile_id="provider-1",
        provider_lease_id="provider-lease-1",
        credential_generation=4,
        host_binding_ref="host-binding:1",
        host_lease_ref="host-lease:1",
        omnigent_host_id="host-1",
        effective_launch_snapshot=_launch(authority),
    )
    await store.attach_session(request.idempotency_key, "provider-session")
    row = await store.bind_harness_authority(
        request=request,
        harness_authority=authority,
    )
    row = await store.record_session_created(
        request.idempotency_key,
        session_id="provider-session",
        capabilities=dict.fromkeys(CAPABILITY_NAMES, True),
        session_status="active",
    )
    yield store, factory, request, authority, row
    await engine.dispose()


async def test_production_writer_feeds_facade_capability_authority(persisted) -> None:
    _store, _factory, _request_value, authority, row = persisted

    assert row.metadata_["harnessAuthority"] == authority
    decision = resolve_bridge_row_capabilities(
        row,
        caller_capabilities=dict.fromkeys(CAPABILITY_NAMES, True),
    )
    assert all(decision.capabilities.values()), decision.disabled_reasons


async def test_production_writer_rejects_tampered_authority(persisted) -> None:
    store, _factory, request, authority, _row = persisted
    tampered = copy.deepcopy(authority)
    tampered["hostHarnessAttestation"]["hostId"] = "foreign-host"

    with pytest.raises(OmnigentIdempotencyError, match="harness_authority_invalid"):
        await store.bind_harness_authority(
            request=request,
            harness_authority=tampered,
        )


async def test_persisted_authority_fails_closed_after_host_fence_changes(
    persisted,
) -> None:
    _store, factory, _request_value, _authority, row = persisted
    async with factory() as session:
        stored = await session.get(OmnigentBridgeSession, row.bridge_session_id)
        stored.omnigent_host_id = "replacement-host"
        await session.commit()
        await session.refresh(stored)
        session.expunge(stored)

    decision = resolve_bridge_row_capabilities(
        stored,
        caller_capabilities=dict.fromkeys(CAPABILITY_NAMES, True),
    )
    assert set(decision.capabilities.values()) == {False}
    assert set(decision.disabled_reasons.values()) == {"harness_authority_invalid"}
