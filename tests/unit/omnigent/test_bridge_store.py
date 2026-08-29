"""Unit tests for the canonical Omnigent bridge session store (MM-1152).

Covers the OmnigentBridge design §7.1/§7.2 and §17 requirements:
- lifecycle-to-normalized status coalescence,
- terminal-status failure classification (``timed_out`` kept distinct),
- the non-lossy per-event normalized status stream in the event index,
- unique idempotency_key session identity.
Source design traceability: OmnigentBridge.md (MM-1152, source issue MM-1140).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, OmnigentBridgeSession
from moonmind.omnigent.bridge_store import (
    BRIDGE_EVENT_JOURNAL_KEY,
    CHAT_BINDING_ID_PREFIX,
    CHAT_BINDING_STATE_AVAILABLE,
    CHAT_BINDING_STATE_ENDED,
    CHAT_BINDING_STATE_STARTING,
    CHAT_BINDING_STATE_UNAVAILABLE,
    FIRST_MESSAGE_ITEM_FRONTIER_KEY,
    FIRST_MESSAGE_TERMINAL,
    SESSION_CREATED_EVENT_TYPE,
    STATUS_ACTIVE,
    STATUS_CREATING,
    STATUS_DECLARED,
    BridgeChatBindingAmbiguousError,
    BridgeProjectionAmbiguousError,
    OmnigentBridgeSessionStore,
    OmnigentDigestMismatchError,
    OmnigentIdempotencyError,
    bridge_failure_class,
    coalesce_bridge_status,
)
from moonmind.omnigent.control_plane.turn_sources import TurnSource
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRuntimeStepExecutionLaunch,
)


@pytest_asyncio.fixture()
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield OmnigentBridgeSessionStore(session_maker)
    await engine.dispose()


@pytest.mark.asyncio
async def test_active_host_protocol_modes_reports_ownership_and_unknown(store) -> None:
    await store.get_or_create(
        request=_request("proxy"),
        endpoint_ref="endpoint",
        agent_id=None,
        agent_name=None,
        target_metadata={"hostProtocolMode": "proxy"},
    )
    await store.get_or_create(
        request=_request("legacy"),
        endpoint_ref="endpoint",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    await store.get_or_create(
        request=_request("terminal"),
        endpoint_ref="endpoint",
        agent_id=None,
        agent_name=None,
        target_metadata={"hostProtocolMode": "embedded"},
    )
    await store.record_lifecycle_event(
        "terminal",
        event_type="terminal",
        status="completed",
    )

    assert await store.active_host_protocol_modes() == {"proxy": 1, "unknown": 1}


def _request(idempotency_key: str = "idem-1", *, with_step: bool = False):
    step = None
    if with_step:
        step = AgentRuntimeStepExecutionLaunch(
            workflowId="mm:wf-1",
            runId="run-7",
            logicalStepId="implement",
            executionOrdinal=1,
            stepExecutionId="mm:wf-1:run-7:implement:execution:1",
            runtimeContextPolicy="fresh_agent_run",
        )
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-1",
        idempotencyKey=idempotency_key,
        stepExecution=step,
    )


def _effective_launch() -> dict:
    return {
        "snapshotRef": "omnigent-launch:sha256:" + "3" * 64,
        "launchPolicyRef": "codex-static@1",
        "policyAuthority": {
            "policyId": "codex-static",
            "policyVersion": 1,
            "policyRef": "codex-static@1",
            "policyDigest": "sha256:" + "1" * 64,
            "snapshotRef": "policy:sha256:" + "2" * 64,
            "validation": {"valid": True},
        },
        "enforcedEgress": True,
    }


@pytest.mark.asyncio
async def test_generic_profile_authorization_persists_plan_and_binding(store) -> None:
    request = _request("generic-authority", with_step=True)
    launch = {
        "executionPlanRef": "omnigent-execution-plan:sha256:" + "4" * 64,
        "runtimeBindingRef": "omnigent-runtime-binding:sha256:" + "5" * 64,
        "hostClassRef": "omnigent-opencode@1",
        "launchPolicyRef": "omnigent-on-demand@1",
        "executionRealizerRef": "generic-omnigent-host@1",
    }

    row = await store.bind_profile_authorization(
        request=request,
        endpoint_ref="default",
        provider_profile_id="opencode-profile",
        provider_lease_id="provider-lease-generic",
        credential_generation=3,
        host_binding_ref="host-binding-generic",
        host_lease_ref="host-lease-generic",
        omnigent_host_id="host-generic",
        effective_launch_snapshot=launch,
    )

    assert row.effective_launch_snapshot_json == launch
    claim = await store.claim_canonical_turn_command(
        row=row,
        command_type="message",
        turn_source=TurnSource.WORKFLOW_CHAT,
        idempotency_key="generic-follow-up",
        payload_digest="sha256:" + "6" * 64,
    )
    assert claim.owns_delivery is True


@pytest.mark.asyncio
async def test_stopped_lease_retry_retires_and_rebinds_cleanup_authority(
    store,
) -> None:
    request = _request("stopped-lease-retry")
    launch = _effective_launch()
    await store.bind_profile_authorization(
        request=request,
        endpoint_ref="embedded",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=4,
        host_binding_ref="binding-1",
        host_lease_ref="lease-1",
        omnigent_host_id="host-1",
        effective_launch_snapshot=launch,
    )
    await store.bind_egress_cleanup_authority(
        request=request,
        host_lease_ref="lease-1",
        egress_evidence={
            "attachmentIdentity": "host-container-1",
            "endpointIdentity": "endpoint-1",
            "validationResult": "passed",
        },
        launch_evidence_ref="artifact://launch-egress-1",
    )
    await store.record_lifecycle_event(
        request.idempotency_key,
        event_type="terminal",
        status="failed",
        metadata={"cleanupCompleted": False, "leaseReleased": False},
    )
    await store.record_terminal_cleanup(
        host_lease_ref="lease-1",
        completed=True,
        egress_evidence_ref="artifact://terminal-egress-1",
        launch_evidence_ref="artifact://launch-egress-1",
        lease_released=True,
    )

    reopened = await store.get_or_create(
        request=request,
        endpoint_ref="embedded",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )

    assert reopened.status == STATUS_DECLARED
    history = reopened.metadata_["unpostedAttemptHistory"][-1]
    assert history["egressCleanupAuthority"] == {
        "hostLeaseRef": "lease-1",
        "effectiveLaunchRef": launch["snapshotRef"],
        "launchEvidenceRef": "artifact://launch-egress-1",
        "phase": "attested",
    }

    await store.bind_profile_authorization(
        request=request,
        endpoint_ref="embedded",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=4,
        host_binding_ref="binding-1",
        host_lease_ref="lease-1",
        omnigent_host_id="host-2",
        effective_launch_snapshot=launch,
    )
    assert await store.get_egress_cleanup_authority(host_lease_ref="lease-1") is None

    await store.bind_egress_cleanup_authority(
        request=request,
        host_lease_ref="lease-1",
        egress_evidence={
            "attachmentIdentity": "host-container-2",
            "endpointIdentity": "endpoint-2",
            "validationResult": "passed",
        },
        launch_evidence_ref="artifact://launch-egress-2",
    )
    rebound = await store.get_egress_cleanup_authority(host_lease_ref="lease-1")
    assert rebound is not None
    assert rebound["egressEvidence"]["endpointIdentity"] == "endpoint-2"


@pytest.mark.asyncio
async def test_unposted_retry_preserves_live_cleanup_authority_without_cleanup_proof(
    store,
) -> None:
    request = _request("ambiguous-cleanup-retry")
    launch = _effective_launch()
    await store.bind_profile_authorization(
        request=request,
        endpoint_ref="embedded",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=4,
        host_binding_ref="binding-1",
        host_lease_ref="lease-1",
        omnigent_host_id="host-1",
        effective_launch_snapshot=launch,
    )
    await store.bind_egress_cleanup_authority(
        request=request,
        host_lease_ref="lease-1",
        egress_evidence={
            "attachmentIdentity": "host-container-1",
            "endpointIdentity": "endpoint-1",
            "validationResult": "passed",
        },
        launch_evidence_ref="artifact://launch-egress-1",
    )
    await store.record_lifecycle_event(
        request.idempotency_key,
        event_type="terminal",
        status="failed",
        metadata={"cleanupCompleted": False, "leaseReleased": False},
    )
    await store.get_or_create(
        request=request,
        endpoint_ref="embedded",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    await store.bind_profile_authorization(
        request=request,
        endpoint_ref="embedded",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=4,
        host_binding_ref="binding-1",
        host_lease_ref="lease-1",
        omnigent_host_id="host-2",
        effective_launch_snapshot=launch,
    )

    authority = await store.get_egress_cleanup_authority(host_lease_ref="lease-1")
    assert authority is not None
    assert authority["egressEvidence"]["endpointIdentity"] == "endpoint-1"
    with pytest.raises(OmnigentIdempotencyError):
        await store.bind_egress_cleanup_authority(
            request=request,
            host_lease_ref="lease-1",
            egress_evidence={
                "attachmentIdentity": "host-container-2",
                "endpointIdentity": "endpoint-2",
                "validationResult": "passed",
            },
            launch_evidence_ref="artifact://launch-egress-2",
        )


@pytest.mark.asyncio
async def test_egress_cleanup_authority_round_trips_and_terminal_refs_persist(
    store,
) -> None:
    request = _request()
    policy_authority = {
        "policyId": "codex-static",
        "policyVersion": 1,
        "policyRef": "codex-static@1",
        "policyDigest": "sha256:" + "1" * 64,
        "snapshotRef": "policy:sha256:" + "2" * 64,
        "validation": {"valid": True},
    }
    effective_launch = {
        "snapshotRef": "omnigent-launch:sha256:" + "3" * 64,
        "launchPolicyRef": "codex-static@1",
        "policyAuthority": policy_authority,
        "enforcedEgress": True,
    }
    await store.bind_profile_authorization(
        request=request,
        endpoint_ref="embedded",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=4,
        host_binding_ref="binding-1",
        host_lease_ref="lease-1",
        omnigent_host_id="host-1",
        effective_launch_snapshot=effective_launch,
    )
    await store.bind_egress_cleanup_authority(
        request=request,
        host_lease_ref="lease-1",
        egress_evidence={
            "attachmentIdentity": "host-container-1",
            "validationResult": "passed",
        },
        launch_evidence_ref="artifact://launch-egress",
    )

    authority = await store.get_egress_cleanup_authority(
        host_lease_ref="lease-1"
    )

    assert authority is not None
    assert authority["effectiveLaunch"] == effective_launch
    assert authority["launchEvidenceRef"] == "artifact://launch-egress"
    assert authority["evidenceRequest"] == {
        "correlationId": request.correlation_id,
        "idempotencyKey": request.idempotency_key,
        "remediation": False,
    }

    row = await store.record_terminal_cleanup(
        host_lease_ref="lease-1",
        completed=True,
        egress_evidence_ref="artifact://terminal-egress",
        launch_evidence_ref="artifact://launch-egress",
        lease_released=True,
    )
    assert row is not None
    assert row.terminal_refs["egressEvidenceRef"] == "artifact://terminal-egress"
    assert row.terminal_refs["egressLaunchEvidenceRef"] == (
        "artifact://launch-egress"
    )


@pytest.mark.asyncio
async def test_egress_cleanup_authority_upgrades_launch_to_attested_phase(store) -> None:
    request = _request()
    policy_authority = {
        "policyId": "codex-static",
        "policyVersion": 1,
        "policyRef": "codex-static@1",
        "policyDigest": "sha256:" + "1" * 64,
        "snapshotRef": "policy:sha256:" + "2" * 64,
        "validation": {"valid": True},
    }
    effective_launch = {
        "snapshotRef": "omnigent-launch:sha256:" + "3" * 64,
        "launchPolicyRef": "codex-static@1",
        "policyAuthority": policy_authority,
        "enforcedEgress": True,
    }
    await store.bind_profile_authorization(
        request=request,
        endpoint_ref="embedded",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=4,
        host_binding_ref="binding-1",
        host_lease_ref="lease-1",
        omnigent_host_id="host-1",
        effective_launch_snapshot=effective_launch,
    )
    provisional = {
        "attachmentIdentity": "host-container-1",
        "profileRef": "omnigent-egress@1",
        "deniedConnectionCount": 0,
    }
    await store.bind_egress_cleanup_authority(
        request=request,
        host_lease_ref="lease-1",
        egress_evidence=provisional,
        launch_evidence_ref="artifact://launch-pending",
        phase="launched",
    )
    await store.bind_egress_cleanup_authority(
        request=request,
        host_lease_ref="lease-1",
        egress_evidence={
            **provisional,
            "deniedConnectionCount": 2,
            "networkIdentity": "network-1",
            "endpointIdentity": "endpoint-1",
        },
        launch_evidence_ref="artifact://launch-attested",
        phase="attested",
    )

    authority = await store.get_egress_cleanup_authority(
        host_lease_ref="lease-1"
    )

    assert authority is not None
    assert authority["phase"] == "attested"
    assert authority["launchEvidenceRef"] == "artifact://launch-attested"
    assert authority["egressEvidence"]["deniedConnectionCount"] == 2
    assert authority["egressEvidence"]["networkIdentity"] == "network-1"


@pytest.mark.asyncio
async def test_egress_cleanup_authority_survives_newer_continuation_row(store) -> None:
    initial = _request("initial")
    continuation = _request("initial:repository-continuation:1")
    policy_authority = {
        "policyId": "codex-static",
        "policyVersion": 1,
        "policyRef": "codex-static@1",
        "policyDigest": "sha256:" + "1" * 64,
        "snapshotRef": "policy:sha256:" + "2" * 64,
        "validation": {"valid": True},
    }
    effective_launch = {
        "snapshotRef": "omnigent-launch:sha256:" + "3" * 64,
        "launchPolicyRef": "codex-static@1",
        "policyAuthority": policy_authority,
        "enforcedEgress": True,
    }
    authorization = {
        "endpoint_ref": "embedded",
        "provider_profile_id": "profile-1",
        "provider_lease_id": "provider-lease-1",
        "credential_generation": 4,
        "host_binding_ref": "binding-1",
        "host_lease_ref": "lease-1",
        "omnigent_host_id": "host-1",
        "effective_launch_snapshot": effective_launch,
    }
    await store.bind_profile_authorization(request=initial, **authorization)
    await store.bind_egress_cleanup_authority(
        request=initial,
        host_lease_ref="lease-1",
        egress_evidence={
            "attachmentIdentity": "host-container-1",
            "validationResult": "passed",
        },
        launch_evidence_ref="artifact://launch-egress",
    )

    # The production repository-publication continuation owns a new bridge row
    # while deliberately reusing the same host lease. The bridge row is host and
    # egress evidence only -- canonical session/turn authority is owned by the
    # control plane, where every continuation is a distinct turn attempt on one
    # canonical session (#3707). The new row must not hide the launch row's
    # immutable cleanup authority from a later janitor process.
    await store.bind_profile_authorization(request=continuation, **authorization)

    authority = await store.get_egress_cleanup_authority(
        host_lease_ref="lease-1"
    )

    assert authority is not None
    assert authority["effectiveLaunch"] == effective_launch
    assert authority["launchEvidenceRef"] == "artifact://launch-egress"
    assert authority["evidenceRequest"]["idempotencyKey"] == "initial"

    await store.record_terminal_cleanup(
        host_lease_ref="lease-1",
        completed=True,
        egress_evidence_ref="artifact://terminal-egress",
        launch_evidence_ref="artifact://launch-egress",
        lease_released=True,
    )
    continuation_row = await store.get_existing(
        "initial:repository-continuation:1"
    )
    assert continuation_row is not None
    assert continuation_row.terminal_refs["egressEvidenceRef"] == (
        "artifact://terminal-egress"
    )
    assert continuation_row.terminal_refs["egressLaunchEvidenceRef"] == (
        "artifact://launch-egress"
    )
    assert continuation_row.terminal_refs["leaseReleaseState"] == "released"


@pytest.mark.asyncio
async def test_egress_cleanup_authority_rejects_conflicting_host_lease_rows(
    store,
) -> None:
    initial = _request("initial")
    conflicting = _request("conflicting")
    policy_authority = {
        "policyId": "codex-static",
        "policyVersion": 1,
        "policyRef": "codex-static@1",
        "policyDigest": "sha256:" + "1" * 64,
        "snapshotRef": "policy:sha256:" + "2" * 64,
        "validation": {"valid": True},
    }
    effective_launch = {
        "snapshotRef": "omnigent-launch:sha256:" + "3" * 64,
        "launchPolicyRef": "codex-static@1",
        "policyAuthority": policy_authority,
        "enforcedEgress": True,
    }
    authorization = {
        "endpoint_ref": "embedded",
        "provider_profile_id": "profile-1",
        "provider_lease_id": "provider-lease-1",
        "credential_generation": 4,
        "host_binding_ref": "binding-1",
        "host_lease_ref": "lease-1",
        "omnigent_host_id": "host-1",
        "effective_launch_snapshot": effective_launch,
    }
    for request in (initial, conflicting):
        await store.bind_profile_authorization(request=request, **authorization)
    await store.bind_egress_cleanup_authority(
        request=initial,
        host_lease_ref="lease-1",
        egress_evidence={"attachmentIdentity": "host-container-1"},
        launch_evidence_ref="artifact://launch-egress",
    )
    await store.bind_egress_cleanup_authority(
        request=conflicting,
        host_lease_ref="lease-1",
        egress_evidence={"attachmentIdentity": "host-container-conflict"},
        launch_evidence_ref="artifact://launch-egress-conflict",
    )

    with pytest.raises(
        OmnigentIdempotencyError,
        match="conflicting egress cleanup authority",
    ):
        await store.get_egress_cleanup_authority(host_lease_ref="lease-1")


@pytest.mark.asyncio
async def test_initial_retrieval_store_rejects_unbounded_or_unknown_evidence(
    store,
) -> None:
    await store.get_or_create(
        request=_request(),
        endpoint_ref="endpoint",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )

    with pytest.raises(ValueError, match="unsupported fields"):
        await store.record_initial_context(
            "idem-1", evidence={"state": "completed", "body": "not bounded"}
        )
    with pytest.raises(ValueError, match="16 KiB"):
        await store.record_initial_context(
            "idem-1", evidence={"state": "completed", "queryPreview": "x" * 17_000}
        )


# --- coalescence (§7.1) -----------------------------------------------------


@pytest.mark.parametrize(
    "normalized",
    [
        "created",
        "launching",
        "provisioning",
        "running",
        "waiting",
        "idle",
        "awaiting_approval",
        "intervention_requested",
    ],
)
def test_non_terminal_statuses_coalesce_to_active(normalized):
    assert coalesce_bridge_status(normalized) == STATUS_ACTIVE


@pytest.mark.parametrize(
    "terminal",
    ["completed", "failed", "canceled", "timed_out"],
)
def test_terminal_statuses_pass_through(terminal):
    assert coalesce_bridge_status(terminal) == terminal


def test_provider_aliases_normalized():
    assert coalesce_bridge_status("cancelled") == "canceled"
    assert coalesce_bridge_status("timeout") == "timed_out"


def test_lifecycle_statuses_pass_through():
    assert coalesce_bridge_status(STATUS_DECLARED) == STATUS_DECLARED
    assert coalesce_bridge_status(STATUS_CREATING) == STATUS_CREATING
    assert coalesce_bridge_status(STATUS_ACTIVE) == STATUS_ACTIVE


def test_unknown_status_fails_fast():
    with pytest.raises(ValueError):
        coalesce_bridge_status("banana")


def test_timed_out_is_distinct_system_error():
    # timed_out is never collapsed into failed and maps to system_error (§17).
    assert coalesce_bridge_status("timed_out") == "timed_out"
    assert bridge_failure_class("timed_out") == "system_error"
    assert bridge_failure_class("canceled") == "system_error"
    assert bridge_failure_class("failed") == "execution_error"
    assert bridge_failure_class("completed") is None


# --- store lifecycle --------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_is_idempotent_and_declared(store):
    request = _request(with_step=True)
    row = await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed", "workspace": "https://x/y#main"},
    )
    assert row.status == STATUS_DECLARED
    assert row.provider == "omnigent"
    assert row.compatibility_profile == "omnigent.server.v1"
    assert row.moonmind_workflow_id == "mm:wf-1"
    assert row.moonmind_run_id == "run-7"
    assert row.moonmind_agent_run_id == "run-7"
    assert row.step_execution_id == "mm:wf-1:run-7:implement:execution:1"
    assert row.host_type == "managed"
    assert row.workspace == "https://x/y#main"
    assert row.bridge_session_id.startswith("brs_")

    again = await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed", "workspace": "https://x/y#main"},
    )
    assert again.bridge_session_id == row.bridge_session_id


@pytest.mark.asyncio
async def test_get_or_create_reopens_terminal_attempt_that_never_posted(store):
    """Temporal retries must not inherit a false first-message terminal edge."""

    request = _request("unposted-retry")
    created = await store.get_or_create(
        request=request,
        endpoint_ref="endpoint",
        agent_id="agent-1",
        agent_name="Agent One",
        target_metadata={},
    )
    await store.mark_terminal(
        request.idempotency_key,
        status="failed",
        terminal_refs={"summary": "host registration failed before session start"},
    )

    reopened = await store.get_or_create(
        request=request,
        endpoint_ref="endpoint",
        agent_id="agent-1",
        agent_name="Agent One",
        target_metadata={},
    )

    assert reopened.bridge_session_id == created.bridge_session_id
    assert reopened.status == STATUS_DECLARED
    assert reopened.first_message_state == "not_prepared"
    assert reopened.terminal_refs == {}
    assert reopened.metadata_["unpostedAttemptHistory"][-1]["status"] == "failed"
    assert (
        reopened.metadata_["unpostedAttemptHistory"][-1]["terminalRefs"]["summary"]
        == "host registration failed before session start"
    )


@pytest.mark.asyncio
async def test_get_or_create_persists_binding_identity_override(store):
    # The Session API Facade holds a verified workflow id out-of-band and
    # synthesizes a request with no step_execution (correlation id != workflow
    # id). The explicit override must be persisted, not the correlation id.
    request = _request()  # correlationId="corr-1", no step_execution
    row = await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-verified",
        agent_run_id="ar-verified",
    )
    assert row.moonmind_workflow_id == "mm:wf-verified"
    assert row.moonmind_agent_run_id == "ar-verified"


@pytest.mark.asyncio
async def test_get_or_create_without_override_derives_from_request(store):
    # Managed-execution path behavior is preserved when no override is given.
    request = _request()  # no step_execution -> falls back to correlation id
    row = await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed"},
    )
    assert row.moonmind_workflow_id == "corr-1"
    assert row.moonmind_agent_run_id == "corr-1"


@pytest.mark.asyncio
async def test_get_existing_returns_none_then_row(store):
    request = _request()
    assert await store.get_existing(request.idempotency_key) is None
    await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-1",
    )
    row = await store.get_existing(request.idempotency_key)
    assert row is not None
    assert row.moonmind_workflow_id == "mm:wf-1"
    assert row.omnigent_agent_id == "ag_1"


@pytest.mark.asyncio
async def test_get_session_owner_resolves_by_session_id(store):
    request = _request()
    await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-owner",
        agent_run_id="ar-owner",
    )
    await store.attach_session(request.idempotency_key, "sess-abc")

    owner = await store.get_session_owner("sess-abc")
    assert owner is not None
    assert owner.workflow_id == "mm:wf-owner"
    assert owner.agent_run_id == "ar-owner"

    assert await store.get_session_owner("sess-missing") is None
    assert await store.get_session_owner("") is None


@pytest.mark.asyncio
async def test_resolve_projection_session_falls_back_after_explicit_key_miss(store):
    first = await store.get_or_create(
        request=_request("idem-first"),
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-owner",
        agent_run_id="ar-first",
    )
    second = await store.get_or_create(
        request=_request("idem-second"),
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-owner",
        agent_run_id="ar-second",
    )
    await store.mark_posting("idem-second")

    by_key = await store.resolve_projection_session(idempotency_key="idem-first")
    assert by_key is not None
    assert by_key.bridge_session_id == first.bridge_session_id

    missed_key = await store.resolve_projection_session(
        workflow_id="mm:wf-owner",
        idempotency_key="stale-or-execution-key",
    )
    assert missed_key is not None
    assert missed_key.bridge_session_id == second.bridge_session_id

    latest = await store.resolve_projection_session(workflow_id="mm:wf-owner")
    assert latest is not None
    assert latest.bridge_session_id == second.bridge_session_id

    scoped = await store.resolve_projection_session(
        workflow_id="mm:wf-owner",
        agent_run_id="ar-first",
    )
    assert scoped is not None
    assert scoped.bridge_session_id == first.bridge_session_id


@pytest.mark.asyncio
async def test_resolve_projection_session_binding_precedes_idempotency(store):
    first = await store.get_or_create(
        request=_request("idem-first"),
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent One",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-owner",
        agent_run_id="ar-first",
    )
    second = await store.get_or_create(
        request=_request("idem-second"),
        endpoint_ref="default",
        agent_id="ag_1",
        agent_name="Agent Two",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-owner",
        agent_run_id="ar-second",
    )
    resolved = await store.resolve_projection_session(
        workflow_id="mm:wf-owner",
        agent_run_id="ar-first",
        idempotency_key="idem-second",
    )
    assert resolved is not None
    assert resolved.bridge_session_id == first.bridge_session_id
    assert resolved.bridge_session_id != second.bridge_session_id


@pytest.mark.asyncio
async def test_resolve_projection_session_rejects_ambiguous_explicit_binding(store):
    for key in ("idem-first", "idem-second"):
        await store.get_or_create(
            request=_request(key),
            endpoint_ref="default",
            agent_id="ag_1",
            agent_name="Agent One",
            target_metadata={"hostType": "managed"},
            workflow_id="mm:wf-owner",
            agent_run_id="ar-shared",
        )
    with pytest.raises(BridgeProjectionAmbiguousError):
        await store.resolve_projection_session(
            workflow_id="mm:wf-owner", agent_run_id="ar-shared"
        )


@pytest.mark.asyncio
async def test_attach_and_first_message_transitions(store):
    request = _request()
    await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    attached = await store.attach_session("idem-1", "sess-1")
    assert attached.omnigent_session_id == "sess-1"
    assert attached.status == STATUS_CREATING

    await store.mark_prepared("idem-1", digest="sha256:abc", marker="marker")
    posting = await store.mark_posting("idem-1")
    assert posting.status == STATUS_ACTIVE
    assert posting.first_message_state == "posting"

    posted = await store.mark_posted(
        "idem-1", response={"pending_id": "pnd-1", "item_id": "itm-1"}
    )
    assert posted.first_message_state == "posted"
    assert posted.first_message_pending_id == "pnd-1"
    assert posted.first_message_item_id == "itm-1"


@pytest.mark.asyncio
async def test_first_message_item_frontier_is_durable_and_immutable(store):
    await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )

    recorded = await store.record_first_message_item_frontier(
        "idem-1", item_ids=["prior-2", "prior-1", "prior-1"]
    )
    assert recorded.metadata_[FIRST_MESSAGE_ITEM_FRONTIER_KEY] == [
        "prior-1",
        "prior-2",
    ]
    repeated = await store.record_first_message_item_frontier(
        "idem-1", item_ids=["prior-1", "prior-2"]
    )
    assert repeated.metadata_[FIRST_MESSAGE_ITEM_FRONTIER_KEY] == [
        "prior-1",
        "prior-2",
    ]

    await store.mark_prepared("idem-1", digest="sha256:abc", marker="marker")
    await store.mark_posting("idem-1")
    await store.mark_posted("idem-1")
    with pytest.raises(
        OmnigentIdempotencyError,
        match="pre-dispatch item frontier changed",
    ):
        await store.record_first_message_item_frontier(
            "idem-1", item_ids=["replacement-item"]
        )


@pytest.mark.asyncio
async def test_digest_mismatch_fails_fast(store):
    request = _request()
    await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    await store.mark_prepared("idem-1", digest="sha256:first", marker="m")
    with pytest.raises(OmnigentDigestMismatchError):
        await store.mark_prepared("idem-1", digest="sha256:second", marker="m")


@pytest.mark.asyncio
async def test_initial_retrieval_cannot_change_after_message_preparation(store):
    await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    evidence = {
        "state": "completed",
        "contextPackRef": "artifact://context/pack.json",
        "preparedMessageRef": "artifact://omnigent/prepared.json",
        "preparedMessageDigest": "sha256:prepared",
    }
    await store.record_initial_context("idem-1", evidence=evidence)
    await store.mark_prepared("idem-1", digest="sha256:first", marker="marker")

    with pytest.raises(
        OmnigentDigestMismatchError,
        match="initial retrieval changed after first-message preparation",
    ):
        await store.record_initial_context(
            "idem-1",
            evidence={**evidence, "contextPackRef": "artifact://context/other.json"},
        )

    unchanged = await store.record_initial_context("idem-1", evidence=evidence)
    assert unchanged.metadata_["initialRetrieval"] == evidence


@pytest.mark.asyncio
async def test_initial_retrieval_appends_bounded_lifecycle_evidence(store):
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    evidence = {
        "state": "degraded",
        "contextPackRef": "artifact://context/pack.json",
        "failureClass": "artifact_publication_failed",
        "mode": "degraded_without_context",
        "reason": "context_artifact_publication_failed",
    }

    await store.record_initial_context("idem-1", evidence=evidence)
    await store.record_initial_context("idem-1", evidence=evidence)

    events = await store.list_events(row.bridge_session_id)
    retrieval_events = [
        event for event in events if event.event_type == "lifecycle.initial_retrieval"
    ]
    assert len(retrieval_events) == 1
    event = retrieval_events[0]
    assert event.artifact_ref == "artifact://context/pack.json"
    assert event.metadata_["status"] == "running"
    assert event.metadata_["failureClass"] == "artifact_publication_failed"
    assert event.metadata_["metadata"] == {
        "retrievalState": "degraded",
        "retrievalMode": "degraded_without_context",
        "retrievalReason": "context_artifact_publication_failed",
    }


@pytest.mark.asyncio
async def test_workspace_resolution_metadata_persists_through_allowlist(store):
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    resolution = {
        "locatorKind": "sandbox",
        "workspaceId": "ws-abc123",
        "relativePath": "repo",
        "identityVerified": True,
        "materialization": {
            "action": "materialized",
            "sourceKind": "github_https",
            "checkedOut": "feature",
            "restoreInputs": [{"ref": "artifact://a", "bytes": 12}],
        },
    }

    await store.record_lifecycle_event(
        "idem-1",
        event_type="workspace_resolution",
        metadata=resolution,
    )

    events = await store.list_events(row.bridge_session_id)
    resolution_events = [
        event for event in events if event.event_type == "workspace_resolution"
    ]
    assert len(resolution_events) == 1
    # The durable resolution evidence reaches Workflow Detail intact rather than
    # being silently dropped by the metadata allowlist.
    assert resolution_events[0].metadata_["metadata"] == resolution


@pytest.mark.asyncio
async def test_authority_chain_metadata_persists_through_allowlist(store):
    """The unified #3561 authority-chain projection survives the allowlist intact.

    It is nested under the single ``authorityChain`` key so the whole compact
    workspace -> runtime -> publication -> terminal -> cleanup -> lease structure
    reaches Workflow Detail without the top-level allowlist pruning its subtree.
    """

    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    authority_chain = {
        "schemaVersion": "omnigent-authority-chain-v1",
        "workspace": {"locatorKind": "sandbox", "workspaceId": "ws-1"},
        "runtime": {"hostMode": "static_compose", "mountClasses": ["workspace"]},
        "publication": {
            "publishMode": "branch",
            "outputBranch": "agent/impl",
            "publicationState": "authorized_pending_publication",
        },
        "terminal": {
            "cleanupCompleted": True,
            "leaseReleased": True,
            "releaseOrdering": [
                "host_cleanup_completed",
                "provider_lease_released",
                "terminal",
            ],
        },
        "reasons": [],
    }

    await store.record_lifecycle_event(
        "idem-1",
        event_type="authority_chain",
        event_identity="idem-1:attempt:1:authority_chain:completed",
        status="completed",
        metadata={"authorityChain": authority_chain},
    )

    events = await store.list_events(row.bridge_session_id)
    authority_events = [
        event for event in events if event.event_type == "lifecycle.authority_chain"
    ]
    assert len(authority_events) == 1
    assert authority_events[0].metadata_["metadata"] == {
        "authorityChain": authority_chain
    }


@pytest.mark.asyncio
async def test_workspace_intent_evidence_persists_through_allowlist(store):
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    # A representative subset of bounded, credential-free workspace-intent
    # compilation evidence (see ``WorkspaceIntentRecord.evidence``).
    evidence = {
        "schemaVersion": "v1",
        "producerVersion": "omnigent-workspace-intent@1",
        "intentDigest": "sha256:abc123",
        "repository": "https://github.com/acme/widgets.git",
        "repositoryKind": "github_https",
        "sourceCommit": "abc1234",
        "startingBranch": "main",
        "targetBranch": "feature/x",
        "publishMode": "pr",
        "repositoryMutation": True,
        "requiredCapabilities": ["gh", "git"],
        "credentialInjectionPolicy": "in_memory_only",
        "inputRefCount": 2,
        "restoreInputRefCount": 1,
        "externalStateRefCount": 1,
        "skillProjectionDigests": ["sha256:aa"],
        "locatorKind": "sandbox",
    }

    await store.record_lifecycle_event(
        "idem-1",
        event_type="workspace_intent_compiled",
        event_identity="workspace_intent_compiled:sha256:abc123",
        metadata=evidence,
    )

    events = await store.list_events(row.bridge_session_id)
    intent_events = [
        event
        for event in events
        if event.event_type == "lifecycle.workspace_intent_compiled"
    ]
    assert len(intent_events) == 1
    # The advertised durable compilation evidence reaches Workflow Detail intact
    # rather than being reduced to only the locator kind by the allowlist.
    assert intent_events[0].metadata_["metadata"] == evidence


@pytest.mark.asyncio
async def test_conflicting_intent_digest_records_a_distinct_event(store):
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    # A deterministic retry (same digest) deduplicates; a conflicting
    # resubmission under the same idempotency key (new digest) is recorded as a
    # distinct durable event rather than silently retaining the stale evidence.
    await store.record_lifecycle_event(
        "idem-1",
        event_type="workspace_intent_compiled",
        event_identity="workspace_intent_compiled:sha256:first",
        metadata={"intentDigest": "sha256:first"},
    )
    await store.record_lifecycle_event(
        "idem-1",
        event_type="workspace_intent_compiled",
        event_identity="workspace_intent_compiled:sha256:first",
        metadata={"intentDigest": "sha256:first"},
    )
    await store.record_lifecycle_event(
        "idem-1",
        event_type="workspace_intent_compiled",
        event_identity="workspace_intent_compiled:sha256:second",
        metadata={"intentDigest": "sha256:second"},
    )

    events = await store.list_events(row.bridge_session_id)
    digests = sorted(
        event.metadata_["metadata"]["intentDigest"]
        for event in events
        if event.event_type == "lifecycle.workspace_intent_compiled"
    )
    assert digests == ["sha256:first", "sha256:second"]


@pytest.mark.asyncio
async def test_long_lifecycle_retry_identities_preserve_distinct_attempts(store):
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    attempt_prefix = (
        "mm:96eb128d-ff2e-4d22-9fe5-cde712d2c678:"
        "019fd325-7bba-79b5-a0b7-ed9c17c708f9:"
        "node-1:execution:1:agent_execute"
    )

    for attempt in (1, 2):
        identity = f"{attempt_prefix}:attempt:{attempt}:request_validated:started"
        await store.record_lifecycle_event(
            "idem-1",
            event_type="request_validated",
            event_identity=identity,
        )
        # Replaying one activity attempt still resolves to the same event.
        await store.record_lifecycle_event(
            "idem-1",
            event_type="request_validated",
            event_identity=identity,
        )

    events = await store.list_events(row.bridge_session_id)
    assert len(events) == 2
    assert len({event.deduplication_key for event in events}) == 2
    assert all(len(event.deduplication_key) <= 128 for event in events)


@pytest.mark.asyncio
async def test_attach_conflicting_session_fails(store):
    request = _request()
    await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    await store.attach_session("idem-1", "sess-1")
    with pytest.raises(OmnigentIdempotencyError):
        await store.attach_session("idem-1", "sess-2")


@pytest.mark.asyncio
async def test_missing_row_requires_get_or_create(store):
    with pytest.raises(OmnigentIdempotencyError):
        await store.mark_posting("never-created")


# --- terminal coalescence + event index (§7.1/§7.2) -------------------------


@pytest.mark.asyncio
async def test_terminal_lifecycle_event_projects_session_terminal_state(store):
    request = _request()
    row = await store.get_or_create(
        request=request,
        endpoint_ref="pending",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    await store.record_lifecycle_event(
        request.idempotency_key,
        event_type="terminal",
        status="failed",
        event_identity="idem-1:attempt:1:terminal:failed",
    )

    projected = await store.get_bridge_session(row.bridge_session_id)
    assert projected is not None
    assert projected.status == "failed"
    assert projected.first_message_state == FIRST_MESSAGE_TERMINAL


@pytest.mark.asyncio
async def test_comparison_lifecycle_event_preserves_diagnostic_fields(store):
    request = _request()
    row = await store.get_or_create(
        request=request,
        endpoint_ref="pending",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    await store.record_lifecycle_event(
        request.idempotency_key,
        event_type="codex_direct_compat.comparison",
        metadata={
            "duplicateEventCount": 2,
            "reordered": True,
            "semanticMismatchCount": 1,
            "comparisonAvailable": True,
        },
    )

    events = await store.list_events(row.bridge_session_id)
    assert events[-1].metadata_["metadata"] == {
        "duplicateEventCount": 2,
        "reordered": True,
        "semanticMismatchCount": 1,
        "comparisonAvailable": True,
    }


@pytest.mark.asyncio
async def test_mark_terminal_coalesces_and_preserves_event_stream(store):
    request = _request()
    created = await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    await store.record_lifecycle_event(
        request.idempotency_key,
        event_type="profile_resolution",
        status="completed",
        event_identity="idem-1:attempt:1:profile_resolution:completed",
    )

    # Full non-lossy normalized status stream, including a terminal timeout.
    stream = [
        {"eventType": "session.created", "normalizedStatus": "created", "sequence": 1},
        {"eventType": "response.delta", "normalizedStatus": "running", "sequence": 2},
        {
            "eventType": "response.elicitation_request",
            "normalizedStatus": "awaiting_approval",
            "sequence": 3,
        },
        {"eventType": "response.delta", "normalizedStatus": "running", "sequence": 4},
        {
            "eventType": "response.failed",
            "normalizedStatus": "timed_out",
            "sequence": 5,
        },
    ]

    terminal = await store.mark_terminal(
        "idem-1",
        status="timed_out",
        terminal_refs={"outputRefs": ["art_final"]},
        events=stream,
    )
    # Session status keeps the terminal value distinct (not collapsed to failed).
    assert terminal.status == "timed_out"
    assert terminal.first_message_state == FIRST_MESSAGE_TERMINAL
    assert terminal.terminal_refs == {"outputRefs": ["art_final"]}

    events = await store.list_events(created.bridge_session_id)
    # The event index preserves the full, non-lossy per-event normalized stream,
    # including the non-terminal statuses coalesced away at the session level.
    assert [e.sequence for e in events] == [1, 2, 3, 4, 5, 6]
    assert events[0].event_type == "lifecycle.profile_resolution"
    assert [e.normalized_status for e in events[1:]] == [
        "created",
        "running",
        "awaiting_approval",
        "running",
        "timed_out",
    ]
    assert all(e.direction == "host_to_moonmind" for e in events[1:])
    assert events[1].event_type == "session.created"


@pytest.mark.asyncio
async def test_mark_terminal_event_indexing_is_idempotent_on_retry(store):
    # A Temporal activity retry can reattach to the durable session and call
    # mark_terminal again with the same idempotency key. The event index must not
    # accumulate duplicate sequences (§7.2).
    request = _request()
    created = await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    stream = [
        {"eventType": "session.created", "normalizedStatus": "created", "sequence": 1},
        {
            "eventType": "response.completed",
            "normalizedStatus": "completed",
            "sequence": 2,
        },
    ]

    await store.mark_terminal(
        "idem-1",
        status="completed",
        terminal_refs={"outputRefs": ["art"]},
        events=stream,
    )
    # Retry: same key, same events.
    await store.mark_terminal(
        "idem-1",
        status="completed",
        terminal_refs={"outputRefs": ["art"]},
        events=stream,
    )

    events = await store.list_events(created.bridge_session_id)
    assert [e.sequence for e in events] == [1, 2]
    assert [e.normalized_status for e in events] == ["created", "completed"]


@pytest.mark.asyncio
async def test_mark_terminal_populates_canonical_ref_columns(store):
    # The dedicated first-class evidence ref columns must be populated from the
    # capture bundle refs (§7.1) instead of remaining NULL for new runs.
    request = _request()
    await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    terminal_refs = {
        "outputRefs": ["art_final", "art_norm", "art_manifest"],
        "diagnosticsRef": "art_diag",
        "metadataRefs": {
            "rawSseStreamRef": "art_raw",
            "normalizedEventStreamRef": "art_norm",
            "initialSnapshotRef": "art_initial",
            "finalSnapshotRef": "art_final",
            "captureManifestRef": "art_manifest",
            "externalStateRef": "art_external",
        },
    }

    terminal = await store.mark_terminal(
        "idem-1", status="completed", terminal_refs=terminal_refs
    )

    assert terminal.raw_events_ref == "art_raw"
    assert terminal.normalized_events_ref == "art_norm"
    assert terminal.initial_snapshot_ref == "art_initial"
    assert terminal.final_snapshot_ref == "art_final"
    assert terminal.capture_manifest_ref == "art_manifest"
    assert terminal.external_state_ref == "art_external"
    assert terminal.diagnostics_ref == "art_diag"
    # The JSON terminal_refs blob is preserved unchanged alongside the columns.
    assert terminal.terminal_refs == terminal_refs


@pytest.mark.asyncio
async def test_unique_idempotency_key_enforced(store):
    request = _request()
    await store.get_or_create(
        request=request,
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    # A second row with the same idempotency_key but a different primary key must
    # be rejected by the unique constraint.
    async with store._session_factory() as session:  # noqa: SLF001 - constraint test
        session.add(
            OmnigentBridgeSession(
                bridge_session_id="brs_dupe",
                provider="omnigent",
                compatibility_profile="omnigent.server.v1",
                moonmind_workflow_id="corr-1",
                moonmind_agent_run_id="corr-1",
                idempotency_key="idem-1",
                omnigent_endpoint_ref="default",
                host_type="managed",
                status=STATUS_DECLARED,
                first_message_state="not_prepared",
                terminal_refs={},
                metadata_={},
            )
        )
        with pytest.raises(Exception):
            await session.commit()


@pytest.mark.asyncio
async def test_append_events_allocates_monotonic_sequences_and_keeps_terminal_status(
    store,
):
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )

    await store.append_events(
        row.bridge_session_id,
        [
            {"eventType": "response.delta", "normalizedStatus": "running"},
            {"eventType": "response.completed", "normalizedStatus": "completed"},
        ],
    )
    after_terminal = await store.get_bridge_session(row.bridge_session_id)
    assert after_terminal is not None
    assert after_terminal.status == "completed"

    await store.append_events(
        row.bridge_session_id,
        [{"eventType": "stream.done", "normalizedStatus": "running"}],
    )

    events = await store.list_events(row.bridge_session_id)
    assert [event.sequence for event in events] == [1, 2, 3]
    final = await store.get_bridge_session(row.bridge_session_id)
    assert final is not None
    assert final.status == "completed"


@pytest.mark.asyncio
async def test_append_events_deduplicates_replay_but_preserves_identical_distinct_deltas(
    store,
):
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    first = {
        "eventType": "response.delta",
        "normalizedStatus": "running",
        "textPreview": "same",
        "deduplicationKey": "cursor:7:abc",
    }
    second = {**first, "deduplicationKey": "cursor:8:abc"}

    assert len(await store.append_events(row.bridge_session_id, [first])) == 1
    assert await store.append_events(row.bridge_session_id, [first]) == []
    assert len(await store.append_events(row.bridge_session_id, [second])) == 1

    events = await store.list_events(row.bridge_session_id)
    assert [event.sequence for event in events] == [1, 2]
    assert [event.text_preview for event in events] == ["same", "same"]


@pytest.mark.asyncio
async def test_append_events_deduplicates_replay_within_one_reconnect_batch(store):
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    replay = {
        "eventType": "response.delta",
        "normalizedStatus": "running",
        "deduplicationKey": "provider:event-1",
    }

    appended = await store.append_events(row.bridge_session_id, [replay, replay])

    assert len(appended) == 1
    assert [
        event.sequence for event in await store.list_events(row.bridge_session_id)
    ] == [1]


@pytest.mark.asyncio
async def test_terminal_reconciliation_never_deletes_live_rows(store):
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    live = {
        "eventType": "response.delta",
        "normalizedStatus": "running",
        "deduplicationKey": "provider:event-1",
    }
    terminal = {
        "eventType": "response.completed",
        "normalizedStatus": "completed",
        "deduplicationKey": "provider:event-2",
    }
    await store.append_events(row.bridge_session_id, [live])
    await store.mark_terminal("idem-1", status="completed", events=[live, terminal])
    await store.mark_terminal("idem-1", status="completed", events=[live, terminal])

    events = await store.list_events(row.bridge_session_id)
    assert [event.sequence for event in events] == [1, 2]
    assert [event.event_type for event in events] == [
        "response.delta",
        "response.completed",
    ]


@pytest.mark.asyncio
async def test_lifecycle_events_share_the_ordered_projection(store):
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    await store.record_lifecycle_event(
        "idem-1",
        event_type="profile.resolved",
        code="ready",
        summary="Profile authorized",
    )
    await store.append_events(
        row.bridge_session_id,
        [
            {
                "eventType": "response.delta",
                "normalizedStatus": "running",
                "deduplicationKey": "provider:event-1",
            }
        ],
    )

    events = await store.list_events(row.bridge_session_id)
    assert [event.sequence for event in events] == [1, 2]
    assert [event.event_type for event in events] == [
        "profile.resolved",
        "response.delta",
    ]


# --- session.created journal (MM-1155, §8.2 step 6) -------------------------


async def _seed_created_session(store) -> None:
    await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id="agent-1",
        agent_name="codex",
        target_metadata={"hostType": "managed", "workspace": "https://x/y#main"},
    )
    await store.attach_session("idem-1", "sess-1")


@pytest.mark.asyncio
async def test_record_session_created_appends_event(store):
    await _seed_created_session(store)

    row = await store.record_session_created(
        "idem-1", session_id="sess-1", agent_id="agent-1", endpoint_ref="default"
    )

    journal = row.metadata_[BRIDGE_EVENT_JOURNAL_KEY]
    assert len(journal) == 1
    event = journal[0]
    assert event["type"] == SESSION_CREATED_EVENT_TYPE
    assert event["omnigentSessionId"] == "sess-1"
    assert event["omnigentAgentId"] == "agent-1"
    assert event["endpointRef"] == "default"
    assert event["sequence"] == 1
    assert event["timestamp"]
    # Existing session metadata is preserved alongside the journal.
    assert row.metadata_["hostType"] == "managed"


@pytest.mark.asyncio
async def test_record_session_created_is_idempotent(store):
    await _seed_created_session(store)

    await store.record_session_created("idem-1", session_id="sess-1")
    row = await store.record_session_created("idem-1", session_id="sess-1")

    assert len(row.metadata_[BRIDGE_EVENT_JOURNAL_KEY]) == 1


@pytest.mark.asyncio
async def test_record_session_created_reactivates_live_session_after_attempt_timeout(
    store,
):
    await _seed_created_session(store)
    await store.mark_terminal("idem-1", status="failed")

    row = await store.record_session_created(
        "idem-1",
        session_id="sess-1",
        capabilities={},
        session_status="idle",
    )

    assert row.status == STATUS_ACTIVE
    authority = row.metadata_["capabilityAuthority"]
    # An empty provider capability object retains safe presentation reads but
    # cannot manufacture mutation authority.
    assert authority["fresh"] is True
    assert authority["upstream"]["viewTranscript"] is True
    assert authority["upstream"]["readResources"] is True
    assert authority["upstream"]["sendMessage"] is False


@pytest.mark.asyncio
async def test_record_session_created_does_not_reactivate_without_fresh_status(store):
    await _seed_created_session(store)
    await store.mark_terminal("idem-1", status="failed")

    row = await store.record_session_created(
        "idem-1",
        session_id="sess-1",
        capabilities={},
        session_status=None,
    )

    assert row.status == "failed"


@pytest.mark.asyncio
async def test_record_session_created_preserves_state_and_advances_replacement_epoch(
    store,
):
    await _seed_created_session(store)
    capabilities = {"sendFollowUp": True}
    created = await store.record_session_created(
        "idem-1", session_id="sess-1", capabilities=capabilities
    )
    async with store._session_factory() as session:
        db_row = await session.get(OmnigentBridgeSession, created.bridge_session_id)
        metadata = dict(db_row.metadata_ or {})
        authority = dict(metadata["capabilityAuthority"])
        state = dict(authority["state"])
        state["activeTurnId"] = "turn-7"
        state["elicitationId"] = "el-3"
        authority["state"] = state
        metadata["capabilityAuthority"] = authority
        db_row.metadata_ = metadata
        await session.commit()

    retried = await store.record_session_created(
        "idem-1", session_id="sess-1", capabilities=capabilities
    )
    retried_state = retried.metadata_["capabilityAuthority"]["state"]
    assert retried_state["sessionEpoch"] == 1
    assert retried_state["activeTurnId"] == "turn-7"
    assert retried_state["elicitationId"] == "el-3"

    async with store._session_factory() as session:
        db_row = await session.get(OmnigentBridgeSession, created.bridge_session_id)
        db_row.omnigent_session_id = "sess-2"
        await session.commit()

    replaced = await store.record_session_created("idem-1", session_id="sess-2")
    replaced_state = replaced.metadata_["capabilityAuthority"]["state"]
    assert replaced_state["sessionEpoch"] == 2
    assert replaced_state["activeTurnId"] is None
    assert replaced_state["elicitationId"] is None
    assert [
        event["omnigentSessionId"]
        for event in replaced.metadata_[BRIDGE_EVENT_JOURNAL_KEY]
    ] == ["sess-1", "sess-2"]


@pytest.mark.asyncio
async def test_record_session_created_persists_across_get_or_create(store):
    await _seed_created_session(store)
    await store.record_session_created("idem-1", session_id="sess-1")

    # A subsequent get_or_create (retry) must not clobber the journal.
    row = await store.get_or_create(
        request=_request(),
        endpoint_ref="default",
        agent_id="agent-1",
        agent_name="codex",
        target_metadata={"hostType": "managed", "workspace": "https://x/y#main"},
    )
    assert BRIDGE_EVENT_JOURNAL_KEY in row.metadata_
    assert len(row.metadata_[BRIDGE_EVENT_JOURNAL_KEY]) == 1


# --- opaque chat-binding identity + resolution (MoonLadderStudios/MoonMind#3633)


async def _seed_chat_session(
    store,
    *,
    key: str,
    workflow_id: str,
    agent_run_id: str,
    session_id: str | None,
    capabilities: dict | None = None,
    terminal_status: str | None = None,
    delete_session: bool = False,
):
    """Create a bridge row and drive it to the requested lifecycle state."""

    await store.get_or_create(
        request=_request(key),
        endpoint_ref="default",
        agent_id="agent-1",
        agent_name="codex",
        target_metadata={"hostType": "managed"},
        workflow_id=workflow_id,
        agent_run_id=agent_run_id,
    )
    if session_id is not None:
        await store.attach_session(key, session_id)
        await store.record_session_created(
            key, session_id=session_id, capabilities=capabilities
        )
    if terminal_status is not None:
        await store.mark_terminal(key, status=terminal_status)
    if delete_session and session_id is not None:
        await store.record_provider_session_deleted(session_id)


@pytest.mark.asyncio
async def test_chat_binding_allocated_only_after_provider_binding(store):
    await store.get_or_create(
        request=_request("idem-1"),
        endpoint_ref="default",
        agent_id="agent-1",
        agent_name="codex",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-1",
        agent_run_id="ar-1",
    )
    # No provider session yet -> no durable binding to allocate.
    assert (
        await store.ensure_chat_binding_id(
            (await store.get_existing("idem-1")).bridge_session_id
        )
        is None
    )

    await store.attach_session("idem-1", "sess-1")
    created = await store.record_session_created("idem-1", session_id="sess-1")
    assert created.chat_binding_id
    assert created.chat_binding_id.startswith(CHAT_BINDING_ID_PREFIX)


@pytest.mark.asyncio
async def test_chat_binding_allocation_is_idempotent_across_retries(store):
    await _seed_chat_session(
        store,
        key="idem-1",
        workflow_id="mm:wf-1",
        agent_run_id="ar-1",
        session_id="sess-1",
    )
    row = await store.get_existing("idem-1")
    first = row.chat_binding_id
    assert first

    # A replayed session.created must reuse the same id.
    again = await store.record_session_created("idem-1", session_id="sess-1")
    assert again.chat_binding_id == first
    # ensure_chat_binding_id is also idempotent.
    assert await store.ensure_chat_binding_id(row.bridge_session_id) == first


@pytest.mark.asyncio
async def test_ensure_chat_binding_backfills_historical_row(store):
    """A row created before the column existed is backfilled on first touch."""

    await _seed_chat_session(
        store,
        key="idem-1",
        workflow_id="mm:wf-1",
        agent_run_id="ar-1",
        session_id="sess-1",
    )
    row = await store.get_existing("idem-1")
    # Simulate a historical row with a bound provider session but no binding id.
    async with store._session_factory() as session:
        db_row = await session.get(OmnigentBridgeSession, row.bridge_session_id)
        db_row.chat_binding_id = None
        await session.commit()

    backfilled = await store.ensure_chat_binding_id(row.bridge_session_id)
    assert backfilled and backfilled.startswith(CHAT_BINDING_ID_PREFIX)
    # Stable on repeat.
    assert await store.ensure_chat_binding_id(row.bridge_session_id) == backfilled


@pytest.mark.asyncio
async def test_resolve_chat_binding_active_available(store):
    await _seed_chat_session(
        store,
        key="idem-1",
        workflow_id="mm:wf-1",
        agent_run_id="ar-1",
        session_id="sess-1",
        capabilities={"viewTranscript": True, "sendMessage": True},
    )
    resolution = await store.resolve_chat_binding(workflow_id="mm:wf-1")
    assert resolution.state == CHAT_BINDING_STATE_AVAILABLE
    assert resolution.read_only is False
    assert resolution.chat_binding_id
    assert resolution.capabilities == {"viewTranscript": True, "sendMessage": True}


@pytest.mark.asyncio
async def test_resolve_chat_binding_read_only_from_capabilities(store):
    await _seed_chat_session(
        store,
        key="idem-1",
        workflow_id="mm:wf-1",
        agent_run_id="ar-1",
        session_id="sess-1",
        capabilities={"viewTranscript": True, "sendMessage": False},
    )
    resolution = await store.resolve_chat_binding(workflow_id="mm:wf-1")
    # Active workflow, but the capability projection withholds sendMessage.
    assert resolution.state == CHAT_BINDING_STATE_AVAILABLE
    assert resolution.read_only is True


@pytest.mark.asyncio
async def test_resolve_chat_binding_starting_has_no_binding(store):
    await store.get_or_create(
        request=_request("idem-1"),
        endpoint_ref="default",
        agent_id="agent-1",
        agent_name="codex",
        target_metadata={"hostType": "managed"},
        workflow_id="mm:wf-1",
        agent_run_id="ar-1",
    )
    resolution = await store.resolve_chat_binding(workflow_id="mm:wf-1")
    assert resolution.state == CHAT_BINDING_STATE_STARTING
    assert resolution.chat_binding_id is None
    assert resolution.read_only is True


@pytest.mark.asyncio
async def test_resolve_chat_binding_terminal_read_only(store):
    await _seed_chat_session(
        store,
        key="idem-1",
        workflow_id="mm:wf-1",
        agent_run_id="ar-1",
        session_id="sess-1",
        terminal_status="completed",
    )
    resolution = await store.resolve_chat_binding(workflow_id="mm:wf-1")
    assert resolution.state == CHAT_BINDING_STATE_ENDED
    assert resolution.read_only is True
    assert resolution.chat_binding_id


@pytest.mark.asyncio
async def test_resolve_chat_binding_unavailable_when_no_session(store):
    resolution = await store.resolve_chat_binding(workflow_id="mm:wf-unknown")
    assert resolution.state == CHAT_BINDING_STATE_UNAVAILABLE
    assert resolution.unavailable_reason == "no_session"
    assert resolution.chat_binding_id is None


@pytest.mark.asyncio
async def test_resolve_chat_binding_cleaned_up_session(store):
    await _seed_chat_session(
        store,
        key="idem-1",
        workflow_id="mm:wf-1",
        agent_run_id="ar-1",
        session_id="sess-1",
        terminal_status="completed",
        delete_session=True,
    )
    resolution = await store.resolve_chat_binding(workflow_id="mm:wf-1")
    assert resolution.state == CHAT_BINDING_STATE_UNAVAILABLE
    assert resolution.unavailable_reason == "session_cleaned_up"


@pytest.mark.asyncio
async def test_resolve_chat_binding_unsupported_runtime(store):
    await _seed_chat_session(
        store,
        key="idem-1",
        workflow_id="mm:wf-1",
        agent_run_id="ar-1",
        session_id="sess-1",
    )
    row = await store.get_existing("idem-1")
    async with store._session_factory() as session:
        db_row = await session.get(OmnigentBridgeSession, row.bridge_session_id)
        db_row.provider = "some-other-runtime"
        await session.commit()

    resolution = await store.resolve_chat_binding(workflow_id="mm:wf-1")
    assert resolution.state == CHAT_BINDING_STATE_UNAVAILABLE
    assert resolution.unavailable_reason == "unsupported_runtime"


@pytest.mark.asyncio
async def test_resolve_chat_binding_rejects_ambiguous_active_sessions(store):
    await _seed_chat_session(
        store,
        key="idem-1",
        workflow_id="mm:wf-1",
        agent_run_id="ar-1",
        session_id="sess-1",
    )
    await _seed_chat_session(
        store,
        key="idem-2",
        workflow_id="mm:wf-1",
        agent_run_id="ar-2",
        session_id="sess-2",
    )
    with pytest.raises(BridgeChatBindingAmbiguousError):
        await store.resolve_chat_binding(workflow_id="mm:wf-1")


@pytest.mark.asyncio
async def test_resolve_chat_binding_prefers_active_over_terminal(store):
    # A terminal session plus one active session -> the active one wins and is
    # not treated as ambiguous.
    await _seed_chat_session(
        store,
        key="idem-old",
        workflow_id="mm:wf-1",
        agent_run_id="ar-old",
        session_id="sess-old",
        terminal_status="completed",
    )
    await _seed_chat_session(
        store,
        key="idem-new",
        workflow_id="mm:wf-1",
        agent_run_id="ar-new",
        session_id="sess-new",
    )
    active_binding = (await store.get_existing("idem-new")).chat_binding_id
    resolution = await store.resolve_chat_binding(workflow_id="mm:wf-1")
    assert resolution.state == CHAT_BINDING_STATE_AVAILABLE
    assert resolution.chat_binding_id == active_binding


@pytest.mark.asyncio
async def test_resolve_chat_binding_read_only_without_capability_snapshot(store):
    """Fail closed when no capability snapshot exists (MoonLadderStudios/MoonMind#3633)."""

    # Historical rows and compatibility paths persist no ``interventionCapabilities``
    # snapshot; the binding must be read-only rather than advertising send.
    await _seed_chat_session(
        store,
        key="idem-1",
        workflow_id="mm:wf-1",
        agent_run_id="ar-1",
        session_id="sess-1",
        capabilities=None,
    )
    resolution = await store.resolve_chat_binding(workflow_id="mm:wf-1")
    assert resolution.state == CHAT_BINDING_STATE_AVAILABLE
    assert resolution.read_only is True
    assert resolution.capabilities == {}


@pytest.mark.asyncio
async def test_resolve_chat_binding_read_only_when_send_capability_absent(store):
    """A partial snapshot without ``sendMessage`` still fails closed."""

    await _seed_chat_session(
        store,
        key="idem-1",
        workflow_id="mm:wf-1",
        agent_run_id="ar-1",
        session_id="sess-1",
        capabilities={"viewTranscript": True},
    )
    resolution = await store.resolve_chat_binding(workflow_id="mm:wf-1")
    assert resolution.read_only is True


@pytest.mark.asyncio
async def test_resolve_chat_binding_rejects_cross_runtime_compat_session(store):
    """A direct-Codex compatibility row reuses ``provider="omnigent"`` but the
    Omnigent facade cannot serve it (MoonLadderStudios/MoonMind#3633)."""

    await store.get_or_create(
        request=_request("idem-1"),
        endpoint_ref="direct-codex-compat",
        agent_id="agent-1",
        agent_name="Codex CLI",
        target_metadata={
            "hostType": "managed",
            "compatibilityProfile": "moonmind.codex_direct_compat.v1",
            "producer": "direct_codex_managed_session",
        },
        workflow_id="mm:wf-1",
        agent_run_id="ar-1",
    )
    await store.attach_session("idem-1", "sess-1")
    await store.record_session_created("idem-1", session_id="sess-1")

    row = await store.get_existing("idem-1")
    # The provider column is still the shared Omnigent provider...
    assert row.provider == "omnigent"

    resolution = await store.resolve_chat_binding(workflow_id="mm:wf-1")
    # ...but the compatibility profile is not one the facade can serve.
    assert resolution.state == CHAT_BINDING_STATE_UNAVAILABLE
    assert resolution.unavailable_reason == "unsupported_runtime"


async def _set_updated_at(store, bridge_session_id: str, when: datetime) -> None:
    async with store._session_factory() as session:
        row = await session.get(OmnigentBridgeSession, bridge_session_id)
        row.updated_at = when
        await session.commit()


@pytest.mark.asyncio
async def test_resolve_chat_binding_fails_closed_when_latest_terminal_cleaned_up(store):
    """Never replay an older run's transcript when the newest terminal session
    was cleaned up (MoonLadderStudios/MoonMind#3633)."""

    base = datetime(2026, 1, 1, tzinfo=UTC)
    # Older terminal session whose transcript is still present.
    await _seed_chat_session(
        store,
        key="idem-old",
        workflow_id="mm:wf-1",
        agent_run_id="ar-old",
        session_id="sess-old",
        terminal_status="completed",
    )
    old_row = await store.get_existing("idem-old")
    # Newest terminal session, whose provider transcript was cleaned up.
    await _seed_chat_session(
        store,
        key="idem-new",
        workflow_id="mm:wf-1",
        agent_run_id="ar-new",
        session_id="sess-new",
        terminal_status="completed",
        delete_session=True,
    )
    new_row = await store.get_existing("idem-new")
    await _set_updated_at(store, old_row.bridge_session_id, base)
    await _set_updated_at(store, new_row.bridge_session_id, base + timedelta(minutes=5))

    resolution = await store.resolve_chat_binding(workflow_id="mm:wf-1")
    assert resolution.state == CHAT_BINDING_STATE_UNAVAILABLE
    assert resolution.unavailable_reason == "session_cleaned_up"


@pytest.mark.asyncio
async def test_resolve_chat_binding_uses_latest_terminal_when_older_cleaned_up(store):
    """The authoritative latest terminal session is returned even if an older
    session was cleaned up."""

    base = datetime(2026, 1, 1, tzinfo=UTC)
    await _seed_chat_session(
        store,
        key="idem-old",
        workflow_id="mm:wf-1",
        agent_run_id="ar-old",
        session_id="sess-old",
        terminal_status="completed",
        delete_session=True,
    )
    old_row = await store.get_existing("idem-old")
    await _seed_chat_session(
        store,
        key="idem-new",
        workflow_id="mm:wf-1",
        agent_run_id="ar-new",
        session_id="sess-new",
        terminal_status="completed",
    )
    new_row = await store.get_existing("idem-new")
    await _set_updated_at(store, old_row.bridge_session_id, base)
    await _set_updated_at(store, new_row.bridge_session_id, base + timedelta(minutes=5))

    resolution = await store.resolve_chat_binding(workflow_id="mm:wf-1")
    assert resolution.state == CHAT_BINDING_STATE_ENDED
    assert resolution.read_only is True
    assert resolution.chat_binding_id == new_row.chat_binding_id


@pytest.mark.asyncio
async def test_get_or_create_persists_logical_step_id_in_metadata(store):
    """The logical step id is projected onto the chat binding
    (MoonLadderStudios/MoonMind#3633)."""

    await store.get_or_create(
        request=_request("idem-1", with_step=True),
        endpoint_ref="default",
        agent_id="agent-1",
        agent_name="codex",
        target_metadata={"hostType": "managed"},
    )
    row = await store.get_existing("idem-1")
    assert row.metadata_.get("logicalStepId") == "implement"

    await store.attach_session("idem-1", "sess-1")
    await store.record_session_created("idem-1", session_id="sess-1")
    resolution = await store.resolve_chat_binding(workflow_id="mm:wf-1")
    assert resolution.logical_step_id == "implement"


@pytest.mark.asyncio
async def test_ensure_chat_binding_returns_persisted_winner_on_concurrent_allocation(
    tmp_path, monkeypatch
):
    """The losing side of a concurrent first-time allocation must return the
    *persisted* winning id, never its own unpersisted candidate
    (MoonLadderStudios/MoonMind#3633)."""

    from moonmind.omnigent import bridge_store as bridge_store_module

    db_path = f"{tmp_path}/bridge_race.db"
    # WAL lets the simulated concurrent winner commit through a second connection
    # while the resolver holds its read transaction open.
    seed = sqlite3.connect(db_path)
    try:
        seed.execute("PRAGMA journal_mode=WAL")
    finally:
        seed.close()

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", connect_args={"timeout": 30}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    race_store = OmnigentBridgeSessionStore(session_maker)
    try:
        await race_store.get_or_create(
            request=_request("idem-race"),
            endpoint_ref="default",
            agent_id="agent-1",
            agent_name="codex",
            target_metadata={"hostType": "managed"},
            workflow_id="mm:wf-race",
            agent_run_id="ar-race",
        )
        # A bound provider session with no chat-binding id yet (historical row).
        await race_store.attach_session("idem-race", "sess-race")
        row = await race_store.get_existing("idem-race")
        assert row.chat_binding_id is None

        winner = f"{CHAT_BINDING_ID_PREFIX}winner"
        loser = f"{CHAT_BINDING_ID_PREFIX}loser"

        def _winning_allocation() -> str:
            # Commit the winning id in the gap between this caller's SELECT and
            # its guarded UPDATE, so the guarded UPDATE affects zero rows.
            writer = sqlite3.connect(db_path, timeout=30)
            try:
                writer.execute(
                    "UPDATE omnigent_bridge_sessions SET chat_binding_id = ? "
                    "WHERE bridge_session_id = ? AND chat_binding_id IS NULL",
                    (winner, row.bridge_session_id),
                )
                writer.commit()
            finally:
                writer.close()
            return loser

        monkeypatch.setattr(
            bridge_store_module, "_generate_chat_binding_id", _winning_allocation
        )

        resolved = await race_store.ensure_chat_binding_id(row.bridge_session_id)
        assert resolved == winner

        persisted = await race_store.get_existing("idem-race")
        assert persisted.chat_binding_id == winner
    finally:
        await engine.dispose()
