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
from moonmind.omnigent.generic_host_janitor import GenericOmnigentHostJanitor
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.omnigent.runtime_bindings import (
    InMemoryStableRuntimeBindingStore,
    RuntimeBindingSessionAuthoritySink,
    RuntimeBindingState,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult


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


def _cleanup_realizer(store, *, order, calls):
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

    realizer._session_cleanup = SimpleNamespace(drain=drain)
    realizer._workspace_publisher = SimpleNamespace(
        save_request_workspace=save_request_workspace
    )
    realizer._host_runtime = SimpleNamespace(
        cleanup_prepared=cleanup_prepared, cleanup_authorities=cleanup_authorities
    )
    realizer._credentials = SimpleNamespace(cleanup_all=cleanup_all)
    realizer._provider_leases = SimpleNamespace(release_all=release_all)
    realizer._host_leases = SimpleNamespace()
    return realizer


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
