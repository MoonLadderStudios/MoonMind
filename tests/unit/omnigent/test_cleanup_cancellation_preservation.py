"""MoonLadderStudios/MoonMind#3825 REQ-04/REQ-09: cancellation and stale-cleanup
coverage for accepted-work invocation counts and real-content preservation.

The save/finalization slice is already proven in ``test_attempt_completion.py``.
These tests close the remaining slice named by the verifier: every
interruption kind (cancelled provider turn, janitor recovery, repeated
cleanup) must keep accepted-work invocation counts exact and must never
rewrite or drop the real saved candidate content.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from moonmind.omnigent.attempt_completion import complete_skill_turns
from moonmind.omnigent.control_plane.cleanup_authority import CanonicalCleanupClaim
from moonmind.omnigent.generic_host_janitor import GenericOmnigentHostJanitor
from moonmind.omnigent.realizers import generic_host
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.omnigent.runtime_bindings import (
    InMemoryStableRuntimeBindingStore,
    RuntimeBindingSessionAuthoritySink,
    RuntimeBindingState,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


def _request(*, idempotency_key: str) -> AgentExecutionRequest:
    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-1",
            "idempotencyKey": idempotency_key,
        }
    )


@pytest.mark.asyncio
async def test_cancelled_provider_turn_records_no_receipt_and_is_never_retried():
    """A cancelled billed turn leaves no receipt, so a retry cannot double-count it."""

    from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore

    store = InMemoryStableRuntimeBindingStore()
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "d" * 64,
        idempotency_key="cancelled-turn",
        provider_leases={},
    )
    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    request = _request(idempotency_key="cancelled-turn")
    calls = []

    async def driver(*args, **kwargs):
        calls.append(1)
        raise asyncio.CancelledError("host allocation revoked the delivery")

    with pytest.raises(asyncio.CancelledError):
        await complete_skill_turns(
            request=request, sink=sink, driver=driver, inspect_terminal=None
        )
    assert calls == [1]
    phases = (await store.get(binding.bindingId)).phaseResults or {}
    assert not [key for key in phases if key.startswith("turn:")]
    assert "publication" not in phases


def _cleanup_realizer(store, *, order, calls, save=None):
    realizer = object.__new__(GenericOmnigentHostRealizer)
    realizer._runtime_bindings = store
    realizer._turn_commands = None
    realizer._cleanup_authority = None
    realizer._artifacts = None

    async def drain(session_id: str):
        calls["session_drain"] += 1
        order.append("session")
        calls.setdefault("drained_session", []).append(session_id)
        return {"drained": True}

    async def save_request_workspace(request):
        calls["save"] += 1
        return {"checkpointRef": "artifact://unexpected-resave"}

    async def cleanup_prepared(prepared):
        calls["prepared"] += 1

    async def cleanup_authorities(refs):
        calls["authorities"] += 1
        order.append("authorities")

    async def cleanup_all(handles):
        calls["credentials"] += 1
        order.append("credentials")
        calls.setdefault("credential_handles", []).append(tuple(handles))
        return []

    async def release_all(acquired):
        calls["provider"] += 1
        order.append("provider")
        calls.setdefault("released", []).append(tuple(acquired))

    async def load_cleanup_handles(provider_leases, runtime_handles):
        return ("credential-handle-1",)

    async def release_from_binding(provider_leases):
        calls["provider"] += 1
        order.append("provider")

    async def host_cleanup(**kwargs):
        calls["host"] = calls.get("host", 0) + 1
        order.append("host")
        return {"removed": True}

    realizer._session_cleanup = SimpleNamespace(drain=drain)
    realizer._workspace_publisher = SimpleNamespace(
        save_request_workspace=save or save_request_workspace
    )
    realizer._host_runtime = SimpleNamespace(
        cleanup=host_cleanup,
        cleanup_prepared=cleanup_prepared,
        cleanup_authorities=cleanup_authorities,
    )
    realizer._credentials = SimpleNamespace(
        cleanup_all=cleanup_all, load_cleanup_handles=load_cleanup_handles
    )
    realizer._provider_leases = SimpleNamespace(
        release_all=release_all, release_from_binding=release_from_binding
    )
    realizer._host_leases = SimpleNamespace()
    return realizer


def _counters():
    return {
        "session_drain": 0,
        "save": 0,
        "prepared": 0,
        "authorities": 0,
        "credentials": 0,
        "provider": 0,
    }


async def _pending_save_binding(store, request, *, session_id="sess-1"):
    """A drained attempt whose authoritative workspace has no verified save."""

    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "4" * 64,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    # The same receipt shape ``_drive_session`` records before the first turn.
    await sink.record_phase(
        "workspace",
        request.model_dump(by_alias=True, mode="json", exclude_none=True)
        | {"workspaceSpec": {"workspaceLocator": {"workspaceId": "ws-1"}}},
    )
    return await store.update(
        sink.binding.bindingId,
        expected_revision=sink.binding.revision,
        expected_fencing_generation=sink.binding.fencingGeneration,
        updates={"omnigentSessionId": session_id},
    )


@pytest.mark.asyncio
async def test_cleanup_preserves_saved_content_and_releases_capacity_last():
    """Stale/interrupted cleanup keeps the real saved bytes and orders release last."""

    store = InMemoryStableRuntimeBindingStore()
    request = _request(idempotency_key="cleanup-preserves-save")
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "e" * 64,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    await sink.record_phase(
        "workspace", {"workspaceSpec": {"workspaceLocator": {"workspaceId": "ws-1"}}}
    )
    saved = {
        "checkpointRef": "artifact://saved-checkpoint",
        "archiveRef": "artifact://saved-candidate",
        "contentDigest": "sha256:" + "f" * 64,
        "files": {"result.txt": "real candidate bytes"},
    }
    await sink.record_phase("saved", saved)
    binding = await store.update(
        sink.binding.bindingId,
        expected_revision=sink.binding.revision,
        expected_fencing_generation=sink.binding.fencingGeneration,
        updates={"omnigentSessionId": "sess-1"},
    )
    order: list[str] = []
    calls = {
        "session_drain": 0,
        "save": 0,
        "prepared": 0,
        "authorities": 0,
        "credentials": 0,
        "provider": 0,
    }
    realizer = _cleanup_realizer(store, order=order, calls=calls)
    cleaned, _ = await realizer._cleanup(
        request=request,
        binding=binding,
        host_lease=None,
        host_context=None,
        prepared=None,
        credential_handles=("credential-handle-1",),
        acquired=("lease-a",),
    )
    assert cleaned.state is RuntimeBindingState.cleaned
    stored = (await store.get(binding.bindingId)).phaseResults or {}
    assert stored["saved"] == saved
    assert stored["cleanupSettlement"] == {"status": "not_required"}
    # The interrupted save is reused, never re-saved; capacity releases last.
    assert calls["save"] == 0
    assert calls["drained_session"] == ["sess-1"]
    assert calls["credential_handles"] == [("credential-handle-1",)]
    assert calls["released"] == [("lease-a",)]
    assert calls["session_drain"] == 1
    assert calls["authorities"] == 1
    assert calls["credentials"] == 1
    assert calls["provider"] == 1
    assert order.index("credentials") < order.index("provider")
    assert order.index("session") < order.index("provider")


@pytest.mark.asyncio
async def test_repeated_cleanup_is_idempotent_and_keeps_saved_content():
    """A second (stale janitor) cleanup pass performs no duplicate accepted work."""

    store = InMemoryStableRuntimeBindingStore()
    request = _request(idempotency_key="cleanup-idempotent")
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "0" * 64,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    await sink.record_phase(
        "workspace", {"workspaceSpec": {"workspaceLocator": {"workspaceId": "ws-1"}}}
    )
    saved = {
        "checkpointRef": "artifact://saved-checkpoint",
        "archiveRef": "artifact://saved-candidate",
        "contentDigest": "sha256:" + "1" * 64,
        "files": {"result.txt": "real candidate bytes"},
    }
    await sink.record_phase("saved", saved)
    binding = sink.binding
    order: list[str] = []
    calls = {
        "session_drain": 0,
        "save": 0,
        "prepared": 0,
        "authorities": 0,
        "credentials": 0,
        "provider": 0,
    }
    realizer = _cleanup_realizer(store, order=order, calls=calls)
    kwargs = dict(
        request=request,
        host_lease=None,
        host_context=None,
        prepared=None,
        credential_handles=(),
        acquired=(),
    )
    first, _ = await realizer._cleanup(binding=binding, **kwargs)
    second, _ = await realizer._cleanup(binding=first, **kwargs)
    assert second.state is RuntimeBindingState.cleaned
    assert calls["credentials"] == 1
    assert calls["provider"] == 1
    assert calls["authorities"] == 1
    stored = (await store.get(binding.bindingId)).phaseResults or {}
    assert stored["saved"] == saved


@pytest.mark.asyncio
async def test_janitor_skips_live_owner_and_reconciles_closed_binding_once():
    """Stale-cleanup recovery never steals a live owner and runs closed work once."""

    live = SimpleNamespace(
        bindingId="binding-live",
        executionPlanRef="omnigent-execution-plan:sha256:" + "2" * 64,
    )
    closed = SimpleNamespace(
        bindingId="binding-closed",
        executionPlanRef="omnigent-execution-plan:sha256:" + "3" * 64,
    )
    reconciled: list[str] = []

    class _Bindings:
        async def list_recoverable(self, *, stale_before):
            return (live, closed)

        async def get(self, binding_id):
            raise AssertionError("janitor must not reload a live owner")

    class _Leases:
        async def list_recoverable(self, *, stale_before):
            return ()

    async def owner_has_closed(binding):
        return binding.bindingId == "binding-closed"

    async def reconcile(plan_ref, binding_id):
        reconciled.append(binding_id)

    janitor = GenericOmnigentHostJanitor(
        host_leases=_Leases(),
        runtime_bindings=_Bindings(),
        realizer=SimpleNamespace(reconcile=reconcile),
        owner_has_closed=owner_has_closed,
    )
    summary = await janitor.run()
    assert reconciled == ["binding-closed"]
    assert summary["examined"] == 2
    assert summary["reconciled"] == 1
    assert summary["conflicts"] == 1
    assert summary["failures"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault", "error_type"),
    [("error", "ConnectionError"), ("hang", "TimeoutError")],
)
async def test_save_failure_after_drain_releases_capacity_and_keeps_save_pending(
    monkeypatch, fault, error_type
):
    """MoonLadderStudios/MoonMind#4016: retaining files never holds a provider slot.

    Once the session is drained, nothing consumes the credentials or the model
    slot. A failed or hung capture leaves the caller-owned workspace pending its
    save for the finalization owner, and releases everything else.
    """

    store = InMemoryStableRuntimeBindingStore()
    request = _request(idempotency_key=f"save-fails-{fault}")
    binding = await _pending_save_binding(store, request)
    order: list[str] = []
    calls = _counters()

    async def failing_save(request):
        calls["save"] += 1
        order.append("save")
        if fault == "hang":
            await asyncio.Event().wait()
        raise ConnectionError("artifact storage unavailable")

    monkeypatch.setattr(generic_host, "_CLEANUP_SAVE_TIMEOUT_SECONDS", 0.05)
    realizer = _cleanup_realizer(store, order=order, calls=calls, save=failing_save)
    cleaned, _ = await realizer._cleanup(
        request=request,
        binding=binding,
        host_lease=None,
        host_context=None,
        prepared=None,
        credential_handles=("credential-handle-1",),
        acquired=("lease-a",),
    )

    assert cleaned.state is RuntimeBindingState.cleaned
    stored = (await store.get(binding.bindingId)).phaseResults or {}
    # The pending decision is the existing one: a workspace receipt without a
    # verified save, which the next finalization delivery resumes.
    assert "workspace" in stored
    assert "saved" not in stored
    assert stored["saveFailure"]["errorType"] == error_type
    assert calls["save"] == 1
    assert calls["credentials"] == 1
    assert calls["released"] == [("lease-a",)]
    assert order.index("session") < order.index("save")
    assert order.index("save") < order.index("credentials") < order.index("provider")


@pytest.mark.asyncio
async def test_cleanup_report_failure_does_not_block_release():
    """An optional cleanup report cannot hold capacity or strand the binding."""

    store = InMemoryStableRuntimeBindingStore()
    request = _request(idempotency_key="cleanup-report-fails")
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "5" * 64,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    order: list[str] = []
    calls = _counters()
    realizer = _cleanup_realizer(store, order=order, calls=calls)

    async def write_json(**kwargs):
        raise ConnectionError("artifact service unavailable")

    realizer._artifacts = SimpleNamespace(write_json=write_json)
    cleaned, _ = await realizer._cleanup(
        request=request,
        binding=binding,
        host_lease=None,
        host_context=None,
        prepared=None,
        credential_handles=(),
        acquired=("lease-a",),
    )

    assert cleaned.state is RuntimeBindingState.cleaned
    assert calls["released"] == [("lease-a",)]
    assert "cleanupAttestationRef" not in cleaned.attestationRefs
    stored = (await store.get(binding.bindingId)).phaseResults or {}
    assert stored["cleanupSettlement"] == {"status": "not_required"}
    assert stored["cleanupReport"] == {"status": "failed", "errorType": "ConnectionError"}


class _SettledCleanupAuthority:
    """Canonical cleanup already settled by this binding's recorded claim."""

    def __init__(self):
        self.claims = 0
        self.completed = []

    async def resolve_session_id(self, session_id):
        return "canonical-" + session_id

    async def claim(self, session_id, *, owner_class):
        # A settled session has no claim left to grant.
        self.claims += 1
        return None

    async def complete(self, claim):
        self.completed.append(claim)
        return True


@pytest.mark.asyncio
async def test_janitor_converges_on_its_own_already_settled_cleanup_claim():
    """A lost acknowledgement after settlement must not become a conflict loop."""

    store = InMemoryStableRuntimeBindingStore()
    request = _request(idempotency_key="settled-claim")
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "6" * 64,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    claim = CanonicalCleanupClaim(
        session_id="canonical-sess-1",
        owner_class="omnigent_generic_host",
        claim_token="ocl_settled",
        generation=3,
    )
    binding = await store.update(
        binding.bindingId,
        expected_revision=binding.revision,
        expected_fencing_generation=binding.fencingGeneration,
        state=RuntimeBindingState.cleanup_pending,
        updates={
            "omnigentSessionId": "sess-1",
            "phaseResults": {"cleanupClaim": {
                "session_id": claim.session_id,
                "owner_class": claim.owner_class,
                "claim_token": claim.claim_token,
                "generation": claim.generation,
            }},
        },
    )
    order: list[str] = []
    calls = _counters()
    realizer = _cleanup_realizer(store, order=order, calls=calls)
    authority = _SettledCleanupAuthority()
    realizer._cleanup_authority = authority

    await realizer.reconcile(binding.executionPlanRef, binding.bindingId)

    assert (await store.get(binding.bindingId)).state is RuntimeBindingState.cleaned
    assert authority.completed == [claim]
    assert calls["credentials"] == 1
    assert calls["provider"] == 1


@pytest.mark.asyncio
async def test_janitor_save_failure_still_releases_host_credentials_and_capacity(
    monkeypatch,
):
    """Recovery keeps the workspace pending its save and releases the rest."""

    store = InMemoryStableRuntimeBindingStore()
    request = _request(idempotency_key="janitor-save-fails")
    binding = await _pending_save_binding(store, request)
    binding = await store.update(
        binding.bindingId,
        expected_revision=binding.revision,
        expected_fencing_generation=binding.fencingGeneration,
        updates={"hostLeaseRef": "host-lease-1"},
    )
    order: list[str] = []
    calls = _counters()

    async def failing_save(request):
        calls["save"] += 1
        order.append("save")
        raise ConnectionError("artifact storage unavailable")

    monkeypatch.setattr(generic_host, "_CLEANUP_SAVE_TIMEOUT_SECONDS", 0.05)
    realizer = _cleanup_realizer(store, order=order, calls=calls, save=failing_save)
    lease = SimpleNamespace(
        leaseRef="host-lease-1",
        status="ready",
        generation=1,
        launchGeneration=1,
        cleanupHandle={"kind": "host", "containerName": "host-1"},
    )

    async def get_lease(ref):
        return lease

    async def claim_cleanup(ref, *, expected_generation):
        return lease

    async def mark_cleaned(ref, *, expected_generation):
        order.append("host_lease_cleaned")
        return SimpleNamespace(**{**vars(lease), "status": "cleaned"})

    realizer._host_leases = SimpleNamespace(
        get=get_lease, claim_cleanup=claim_cleanup, mark_cleaned=mark_cleaned
    )

    await realizer.reconcile(binding.executionPlanRef, binding.bindingId)

    stored = await store.get(binding.bindingId)
    assert stored.state is RuntimeBindingState.cleaned
    assert "saved" not in (stored.phaseResults or {})
    assert stored.phaseResults["saveFailure"]["errorType"] == "ConnectionError"
    assert calls["save"] == 1
    assert calls["host"] == 1
    assert calls["credentials"] == 1
    assert calls["provider"] == 1
    assert order.index("session") < order.index("save") < order.index("host")
    assert order.index("credentials") < order.index("provider")
