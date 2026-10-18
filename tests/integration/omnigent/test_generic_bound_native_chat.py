"""Bound native chat through admitted generic execution and real HTTP transport.

The shared journey exercises production planning, credentials, host attestation,
runtime and bridge stores. Docker/provider/Temporal transports are controlled;
these tests do not qualify installed images or paid provider execution.
"""

from __future__ import annotations

# Imported pytest fixtures are consumed by dependency name.
# ruff: noqa: F811
import copy
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from api_service.api.routers import omnigent_bridge as bridge
from api_service.api.routers import omnigent_native_ui as ui
from api_service.auth_providers import get_current_user
from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentBridgeSession,
    OmnigentCleanupAuthority,
    OmnigentCommand,
    OmnigentHostLeaseRecordV2,
    OmnigentRuntimeBindingRecord,
    OmnigentSession,
    OmnigentTurnAttempt,
    ProviderProfileSlotLease,
)
from moonmind.omnigent.bridge_proxy import OmnigentBridgeSessionProxy
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.control_plane.identities import canonical_turn_command_key
from moonmind.omnigent.runtime_bindings import RuntimeBindingState
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from tests.integration.omnigent.test_generic_native_durable_journey import (
    native_journey,  # noqa: F401
    session_factory,  # noqa: F401
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


def _application(store, base_url):
    user = SimpleNamespace(id=uuid4(), is_superuser=True)
    proxy = OmnigentBridgeSessionProxy(
        run_store=store,
        client=OmnigentHttpClient(base_url=base_url),
        default_agent_name="unused",
    )
    app = FastAPI()
    app.include_router(
        bridge.workflow_chat_router, prefix=bridge.WORKFLOW_CHAT_BINDINGS_MOUNT_PATH
    )
    app.include_router(ui.native_ui_router, prefix=ui.NATIVE_UI_MOUNT_PATH)
    app.dependency_overrides[get_current_user()] = lambda: user
    app.dependency_overrides[bridge._get_execution_service] = lambda: None
    app.dependency_overrides[bridge._get_bridge_store] = lambda: store
    app.dependency_overrides[bridge._get_bridge_proxy] = lambda: proxy
    app.dependency_overrides[bridge._get_create_embedded_facade] = lambda: None
    app.dependency_overrides[bridge.get_capability_registry] = lambda: None
    app.dependency_overrides[bridge._require_bridge_enabled] = lambda: SimpleNamespace(
        enabled=True, host_protocol_mode="proxy"
    )
    app.dependency_overrides[ui.get_native_ui_upstream] = lambda: ui.NativeUiUpstream(
        base_url=base_url
    )
    return app


async def _during_active_session(journey, callback):
    errors = []
    called = []

    async def active():
        called.append(True)
        try:
            await callback()
        except Exception as exc:  # noqa: BLE001 - preserve lifecycle cleanup before surfacing callback failures
            errors.append(exc)

    journey.server.on_first_message = active
    result = await journey.realizer.execute(journey.request, journey.plan)
    if errors:
        raise errors[0]
    assert called and result.failure_class is None, result


def _message(text="Bound follow-up"):
    return {
        "type": "message",
        "data": {"content": [{"type": "input_text", "text": text}]},
    }


def _select_request_plan_shape(journey, explicit_binding):
    if not explicit_binding:
        journey.request = journey.request.model_copy(
            update={
                "omnigent_execution_plan": None,
                "parameters": {
                    **journey.request.parameters,
                    "executionPlanRef": journey.plan.planRef,
                },
            }
        )


@pytest.mark.parametrize(
    "native_journey",
    ["codex-native", "claude-native", "opencode-native"],
    indirect=True,
)
@pytest.mark.parametrize("explicit_binding", [True, False])
async def test_attested_generic_native_binding_reads_and_sends(
    native_journey, session_factory, explicit_binding
):
    journey = native_journey
    _select_request_plan_shape(journey, explicit_binding)
    store = OmnigentBridgeSessionStore(session_factory)
    follow_up = _message("Return only the heading of the generated report.")

    async def active():
        row = await store.resolve_projection_session(
            idempotency_key=journey.request.idempotency_key
        )
        resolution = await store.resolve_chat_binding(
            workflow_id=row.moonmind_workflow_id
        )
        assert resolution.read_only is False
        assert resolution.capabilities["viewTranscript"] is True
        assert resolution.capabilities["sendMessage"] is True
        binding_id = row.chat_binding_id
        base_url = os.environ["OMNIGENT_SERVER_URL"]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_application(store, base_url)),
            base_url="http://moonmind.test",
        ) as client:
            document = await client.get(
                f"/omnigent-ui/workflow-chat/{binding_id}?embedded=1"
            )
            assert document.status_code == 200
            assert '"sendMessage":true' in document.text
            path = f"/api/workflow-chat-bindings/{binding_id}/omnigent/v1/sessions/{binding_id}"
            response = await client.get(path)
            assert response.status_code == 200, response.text
            assert response.json()["id"] == binding_id
            assert response.json()["status"] == "running"
            response = await client.post(
                path + "/events",
                json=follow_up,
                headers={"Idempotency-Key": "bound-follow-up"},
            )
            assert response.status_code == 200, response.text
            assert response.json() == {"queued": True, "pending_id": "pending-2"}
            # Queue acceptance is not answer completion. Observe the actual
            # consumed input and a distinct useful response through the bridge.
            transcript = await client.get(path)
            assert transcript.status_code == 200, transcript.text
            items = transcript.json()["items"]
            user_item = next(
                item
                for item in items
                if item["data"]
                == {"role": "user", "content": follow_up["data"]["content"]}
            )
            answers = [item for item in items if item["data"]["role"] == "assistant"]
            assert len(answers) == 1, "queued follow-up produced no distinct answer"
            assert answers[0]["data"]["content"] == [
                {"type": "output_text", "text": "# Native generated result"}
            ]
            assert answers[0]["id"] != user_item["id"]
            assert items.index(user_item) < items.index(answers[0])
            assert transcript.json()["pending_inputs"] == []
            event_count = len(journey.server.events)
            replay = await client.post(
                path + "/events",
                json=follow_up,
                headers={"Idempotency-Key": "bound-follow-up"},
            )
            assert replay.status_code == 200, replay.text
            assert len(journey.server.events) == event_count
            replay_transcript = await client.get(path)
            assert replay_transcript.status_code == 200, replay_transcript.text
            assert replay_transcript.json()["items"] == items
            assert event_count == 2
            assert (
                journey.server.events[-1]["data"]["content"][0]["text"]
                == follow_up["data"]["content"][0]["text"]
            )
            assert "sessions.get" in journey.server.route_calls
            foreign = await client.get(
                path.replace(f"/sessions/{binding_id}", "/sessions/foreign-session")
            )
            assert foreign.status_code in {403, 404}

    await _during_active_session(journey, active)


async def _authority_records(store, row):
    """Snapshot fixture records so each fault leaves lifecycle cleanup runnable."""
    from sqlalchemy import inspect

    authority = row.metadata_["runtimeAuthority"]
    result = []
    async with store._session_factory() as session:
        runtime = (
            await session.execute(
                select(OmnigentRuntimeBindingRecord).where(
                    OmnigentRuntimeBindingRecord.binding_id == authority["bindingId"]
                )
            )
        ).scalar_one()
        host = await session.get(OmnigentHostLeaseRecordV2, row.host_lease_ref)
        bridge_row = await session.get(OmnigentBridgeSession, row.bridge_session_id)
        canonical = await session.get(OmnigentSession, authority["canonicalSessionId"])
        profile = await session.get(
            ManagedAgentProviderProfile, row.provider_profile_id
        )
        lease = (await session.execute(select(ProviderProfileSlotLease))).scalar_one()
        cleanup = await session.get(
            OmnigentCleanupAuthority, authority["canonicalSessionId"]
        )
        for record in (runtime, host, bridge_row, canonical, profile, lease, cleanup):
            mapper = inspect(type(record))
            result.append(
                (
                    type(record),
                    inspect(record).identity[0],
                    {
                        attr.key: copy.deepcopy(getattr(record, attr.key))
                        for attr in mapper.column_attrs
                    },
                )
            )
    return result


async def _restore_authority(store, records):
    async with store._session_factory() as session:
        for model, key, values in records:
            record = await session.get(model, key)
            for name, value in values.items():
                setattr(record, name, value)
        await session.commit()


async def _invalidate_authority(store, row, fault):
    from moonmind.omnigent.runtime_bindings import DbRuntimeBindingStore

    if fault.startswith("runtime_") and fault not in {"runtime_string", "runtime_list"}:
        runtime_store = DbRuntimeBindingStore(store._session_factory)
        binding = await runtime_store.get(
            row.metadata_["runtimeAuthority"]["bindingId"]
        )
        updates = {
            "runtime_session": {"omnigentSessionId": "other-session"},
            "runtime_attestation": {"attestationRefs": {}},
            "runtime_host_generation": {
                "hostLeaseGeneration": binding.hostLeaseGeneration + 1
            },
        }.get(fault)
        await runtime_store.update(
            binding.bindingId,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            increment_fence=fault == "runtime_fence",
            state=RuntimeBindingState.cleanup_pending
            if fault == "runtime_cleanup"
            else None,
            updates=updates,
        )
        return
    async with store._session_factory() as session:
        stored = await session.get(OmnigentBridgeSession, row.bridge_session_id)
        if fault == "profile_rotation":
            profile = await session.get(
                ManagedAgentProviderProfile, stored.provider_profile_id
            )
            profile.credential_generation += 1
        elif fault.startswith("ledger_"):
            lease = (
                await session.execute(select(ProviderProfileSlotLease))
            ).scalar_one()
            if fault == "ledger_fence":
                lease.fencing_generation += 1
            elif fault == "ledger_expired":
                lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            elif fault == "ledger_plan":
                lease.execution_plan_ref = None
            elif fault == "ledger_request":
                lease.idempotency_key = "foreign-request"
            elif fault == "ledger_owner":
                lease.owner_id = "foreign-owner"
            else:
                lease.lease_state = "cleanup_requested"
        elif fault == "provider_lease":
            stored.provider_lease_id = "foreign-lease"
        elif fault == "provider_generation":
            stored.credential_generation += 1
        elif fault == "provider":
            stored.provider = "unknown"
        elif fault == "endpoint":
            stored.omnigent_endpoint_ref = "foreign-endpoint"
        elif fault == "session_epoch":
            evidence = copy.deepcopy(stored.metadata_["capabilityAuthority"])
            evidence["state"]["sessionEpoch"] += 1
            stored.metadata_ = {**stored.metadata_, "capabilityAuthority": evidence}
        elif fault == "session":
            stored.omnigent_session_id = "foreign-session"
        elif fault == "missing_plan":
            stored.metadata_ = {
                key: value
                for key, value in stored.metadata_.items()
                if key != "executionPlanRef"
            }
        elif fault == "plan_digest":
            stored.metadata_ = {
                **stored.metadata_,
                "executionPlanDigest": "sha256:" + "0" * 64,
            }
        elif fault in {"missing_runtime", "missing_runtime_none"}:
            stored.metadata_ = {
                **stored.metadata_,
                "runtimeAuthority": {} if fault == "missing_runtime" else None,
            }
        elif fault in {"runtime_string", "runtime_list"}:
            stored.metadata_ = {
                **stored.metadata_,
                "runtimeAuthority": "invalid" if fault == "runtime_string" else [],
            }
        elif fault.startswith("canonical_"):
            canonical = await session.get(
                OmnigentSession,
                stored.metadata_["runtimeAuthority"]["canonicalSessionId"],
            )
            if fault in {"canonical_claimed", "canonical_cleaned"}:
                cleanup = await session.get(
                    OmnigentCleanupAuthority, canonical.session_id
                )
                cleanup.state = (
                    "claimed" if fault == "canonical_claimed" else "complete"
                )
            elif fault == "canonical_fence":
                canonical.fencing_generation += 1
            elif fault == "canonical_cleanup":
                canonical.cleanup_state = "completed"
            elif fault == "canonical_runtime":
                canonical.runtime_binding_ref = "foreign-runtime"
            elif fault == "canonical_session":
                canonical.provider_session_ref = "foreign-session"
            else:
                canonical.terminal_state = "stopped"
        elif fault == "foreign_scope":
            stored.idempotency_key = "another-execution"
        elif fault == "launch":
            launch = copy.deepcopy(stored.effective_launch_snapshot_json)
            launch["capabilities"]["stopSession"] = not launch["capabilities"][
                "stopSession"
            ]
            stored.effective_launch_snapshot_json = launch
        elif fault.startswith("host_"):
            host = await session.get(OmnigentHostLeaseRecordV2, row.host_lease_ref)
            if fault == "host_cleanup":
                host.status = "cleaned"
            else:
                host.generation += 1
        else:
            raise AssertionError(fault)
        await session.commit()


@pytest.mark.parametrize("native_journey", ["opencode-native"], indirect=True)
@pytest.mark.parametrize(
    "fault",
    [
        "profile_rotation",
        "ledger_fence",
        "ledger_expired",
        "ledger_plan",
        "ledger_request",
        "ledger_owner",
        "ledger_cleanup",
        "provider_lease",
        "provider_generation",
        "session",
        "plan_digest",
        "missing_plan",
        "missing_runtime",
        "missing_runtime_none",
        "runtime_string",
        "runtime_list",
        "canonical_claimed",
        "canonical_cleaned",
        "canonical_fence",
        "canonical_cleanup",
        "canonical_runtime",
        "canonical_session",
        "canonical_terminal",
        "foreign_scope",
        "launch",
        "runtime_session",
        "endpoint",
        "session_epoch",
        "runtime_attestation",
        "runtime_host_generation",
        "runtime_fence",
        "runtime_cleanup",
        "host_cleanup",
        "host_generation",
    ],
)
async def test_generic_native_chat_denies_changed_authority(
    native_journey, session_factory, fault
):
    journey = native_journey
    store = OmnigentBridgeSessionStore(session_factory)

    async def active():
        row = await store.resolve_projection_session(
            idempotency_key=journey.request.idempotency_key
        )
        records = await _authority_records(store, row)
        try:
            await _invalidate_authority(store, row, fault)
            resolution = await store.resolve_chat_binding(
                workflow_id=row.moonmind_workflow_id
            )
            assert resolution.read_only and resolution.capabilities == {}
            calls = list(journey.server.route_calls)
            binding_id = row.chat_binding_id
            app = _application(store, os.environ["OMNIGENT_SERVER_URL"])
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://moonmind.test"
            ) as client:
                path = f"/api/workflow-chat-bindings/{binding_id}/omnigent/v1/sessions/{binding_id}"
                for response in (
                    await client.get(path),
                    await client.post(path + "/events", json=_message()),
                ):
                    assert response.status_code == 403, response.text
                assert journey.server.route_calls == calls
        finally:
            await _restore_authority(store, records)

    await _during_active_session(journey, active)


@pytest.mark.parametrize("native_journey", ["opencode-native"], indirect=True)
@pytest.mark.parametrize("explicit_binding", [True, False])
async def test_generic_native_mutation_rechecks_runtime_fence(
    native_journey, session_factory, monkeypatch, explicit_binding
):
    journey = native_journey
    _select_request_plan_shape(journey, explicit_binding)
    store = OmnigentBridgeSessionStore(session_factory)

    async def active():
        row = await store.resolve_projection_session(
            idempotency_key=journey.request.idempotency_key
        )
        records = await _authority_records(store, row)
        original_lookup = store.get_session_by_chat_binding_id
        lookups = 0

        async def replace_before_handoff(binding_id):
            nonlocal lookups
            lookups += 1
            if lookups == 2:
                await _invalidate_authority(store, row, "runtime_fence")
            return await original_lookup(binding_id)

        monkeypatch.setattr(
            store, "get_session_by_chat_binding_id", replace_before_handoff
        )
        try:
            calls = list(journey.server.route_calls)
            binding_id = row.chat_binding_id
            app = _application(store, os.environ["OMNIGENT_SERVER_URL"])
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://moonmind.test"
            ) as client:
                response = await client.post(
                    f"/api/workflow-chat-bindings/{binding_id}/omnigent/v1/sessions/{binding_id}/events",
                    json=_message(),
                    headers={"Idempotency-Key": "fenced-follow-up"},
                )
                assert response.status_code == 409, response.text
                assert lookups == 2
                assert journey.server.route_calls == calls
        finally:
            await _restore_authority(store, records)

    await _during_active_session(journey, active)


@pytest.mark.parametrize("native_journey", ["opencode-native"], indirect=True)
@pytest.mark.parametrize(
    "snapshot_overrides",
    [
        {"id": "foreign-session"},
        {
            "harness": "unknown",
            "host_id": "foreign",
        },
        {"capabilities": {}},
        {
            "capabilities": {"sendMessage": False},
        },
        {"capabilities": None},
        {
            "capabilities": {"sendMessage": 0, "sendFollowUp": True},
        },
        {
            "capabilities": {"sendMessage": None, "sendFollowUp": True},
        },
        {
            "capabilities": {"sendMessage": "false", "sendFollowUp": True},
        },
    ],
)
async def test_generic_native_message_requires_observed_protocol(
    native_journey, session_factory, snapshot_overrides, monkeypatch
):
    journey = native_journey
    store = OmnigentBridgeSessionStore(session_factory)
    original_fault = journey.server._fault

    async def snapshot_fault(request, route):
        if route == "sessions.get":
            # Keep live session, host and harness identity valid unless this
            # case explicitly changes it. Map denials then exercise map policy.
            journey.server.scenario.malformed_payloads[route] = (
                journey.server.snapshot(request.match_info["session_id"])
                | snapshot_overrides
            )
        return await original_fault(request, route)

    monkeypatch.setattr(journey.server, "_fault", snapshot_fault)

    async def active():
        monkeypatch.setattr(journey.server, "_fault", original_fault)
        journey.server.scenario.malformed_payloads.clear()
        row = await store.resolve_projection_session(
            idempotency_key=journey.request.idempotency_key
        )
        resolution = await store.resolve_chat_binding(
            workflow_id=row.moonmind_workflow_id
        )
        assert resolution.read_only is True
        assert "sendMessage" not in resolution.capabilities
        if isinstance(snapshot_overrides.get("capabilities"), dict):
            # This is a restrictive provider map, not failed identity attestation.
            assert row.metadata_["capabilityAuthority"]["fresh"] is True
            assert (
                row.metadata_["capabilityAuthority"]["upstream"]["sendMessage"] is False
            )
        binding_id = row.chat_binding_id
        calls = list(journey.server.route_calls)
        app = _application(store, os.environ["OMNIGENT_SERVER_URL"])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://moonmind.test"
        ) as client:
            response = await client.post(
                f"/api/workflow-chat-bindings/{binding_id}/omnigent/v1/sessions/{binding_id}/events",
                json=_message(),
            )
            assert response.status_code == 403, response.text
        assert journey.server.route_calls == calls

    await _during_active_session(journey, active)


@pytest.mark.parametrize("native_journey", ["opencode-native"], indirect=True)
async def test_generic_native_stream_rechecks_runtime_cleanup(
    native_journey, session_factory, monkeypatch
):
    journey = native_journey
    store = OmnigentBridgeSessionStore(session_factory)

    async def active():
        row = await store.resolve_projection_session(
            idempotency_key=journey.request.idempotency_key
        )
        records = await _authority_records(store, row)
        try:
            initial = await store.get_session_by_chat_binding_id(row.chat_binding_id)
            user = SimpleNamespace(id=uuid4(), is_superuser=True)
            assert bridge._effective_capabilities(initial, user).capabilities[
                "viewTranscript"
            ]
            await _invalidate_authority(store, row, "runtime_cleanup")
            monkeypatch.setattr(bridge, "_FACADE_STREAM_REAUTH_EVERY_POLLS", 1)

            async def connected():
                return False

            stream = bridge._stream_workflow_chat_events(
                request=SimpleNamespace(is_disconnected=connected),
                chat_binding_id=row.chat_binding_id,
                bridge_session_id=row.bridge_session_id,
                provider_session_id=row.omnigent_session_id,
                initial_cursor=0,
                user=user,
                service=None,
                store=store,
            )
            event = await anext(stream)
            assert "omnigent_chat_binding_unknown" in event
            with pytest.raises(StopAsyncIteration):
                await anext(stream)
        finally:
            await _restore_authority(store, records)

    await _during_active_session(journey, active)


@pytest.mark.parametrize("native_journey", ["opencode-native"], indirect=True)
async def test_generic_native_scan_scope_and_canonical_command(
    native_journey, session_factory, monkeypatch
):
    journey = native_journey
    store = OmnigentBridgeSessionStore(session_factory)
    monkeypatch.setattr(
        "moonmind.omnigent.native_outbound_scan.resolve_high_security_mode",
        lambda *a, **k: True,
    )

    async def active():
        row = await store.resolve_projection_session(
            idempotency_key=journey.request.idempotency_key
        )
        baseline = await store.resolve_chat_binding(
            workflow_id=row.moonmind_workflow_id
        )
        assert not baseline.read_only
        provider_row = await store.get_session_by_provider_session_id(
            row.omnigent_session_id
        )
        user = SimpleNamespace(id=uuid4(), is_superuser=True)
        assert bridge._effective_capabilities(provider_row, user).capabilities[
            "sendMessage"
        ], "provider lookup never refreshed"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=_application(store, os.environ["OMNIGENT_SERVER_URL"])
            ),
            base_url="http://moonmind.test",
        ) as client:
            path = f"/api/workflow-chat-bindings/{row.chat_binding_id}/omnigent/v1/sessions/{row.chat_binding_id}/events"
            payload = _message()
            payload["data"]["content"][0]["text"] = "ghp_" + "a" * 36
            count = len(journey.server.events)
            blocked = await client.post(
                path, json=payload, headers={"Idempotency-Key": "review-blocked"}
            )
            assert blocked.status_code == 403, blocked.text
            assert "ghp_" not in blocked.text
            assert len(journey.server.events) == count
            response = await client.post(
                path, json=_message(), headers={"Idempotency-Key": "review-allowed"}
            )
            assert response.status_code == 200, response.text
            replay = await client.post(
                path, json=_message(), headers={"Idempotency-Key": "review-allowed"}
            )
            assert replay.status_code == 200, replay.text
            assert len(journey.server.events) == count + 1
            conflict_body = _message()
            conflict_body["data"]["content"][0]["text"] = "Different payload"
            conflict = await client.post(
                path, json=conflict_body, headers={"Idempotency-Key": "review-allowed"}
            )
            assert conflict.status_code == 409, conflict.text
            assert len(journey.server.events) == count + 1
        async with session_factory() as session:
            commands = list(
                (
                    await session.execute(
                        select(OmnigentCommand).where(
                            OmnigentCommand.idempotency_key
                            == canonical_turn_command_key(
                                row.moonmind_workflow_id, "review-allowed"
                            )
                        )
                    )
                ).scalars()
            )
            assert len(commands) == 1
            assert (
                commands[0].session_id
                == row.metadata_["runtimeAuthority"]["canonicalSessionId"]
            )
            turn = await session.get(OmnigentTurnAttempt, commands[0].turn_attempt_id)
            assert turn.lineage_kind == "workflow_chat"
            assert turn.step_execution_id == row.step_execution_id
            blocked = list(
                (
                    await session.execute(
                        select(OmnigentCommand).where(
                            OmnigentCommand.idempotency_key
                            == canonical_turn_command_key(
                                row.moonmind_workflow_id, "review-blocked"
                            )
                        )
                    )
                ).scalars()
            )
            assert not blocked

    await _during_active_session(journey, active)


@pytest.mark.parametrize("native_journey", ["opencode-native"], indirect=True)
@pytest.mark.parametrize("fault", ["runtime_cleanup", "caller_revoke", "turn_replaced"])
async def test_generic_native_denies_cleanup_during_command_claim(
    native_journey, session_factory, monkeypatch, fault
):
    journey = native_journey
    store = OmnigentBridgeSessionStore(session_factory)

    async def active():
        row = await store.resolve_projection_session(
            idempotency_key=journey.request.idempotency_key
        )
        baseline = await store.resolve_chat_binding(
            workflow_id=row.moonmind_workflow_id
        )
        assert not baseline.read_only
        records = await _authority_records(store, row)
        original = store.claim_canonical_turn_command

        async def race(**kwargs):
            if fault == "turn_replaced":
                async with session_factory() as session:
                    stored = await session.get(
                        OmnigentBridgeSession, row.bridge_session_id
                    )
                    metadata = copy.deepcopy(stored.metadata_)
                    metadata["capabilityAuthority"]["state"]["activeTurnId"] = (
                        "replaced-turn"
                    )
                    stored.metadata_ = metadata
                    await session.commit()
            elif fault == "caller_revoke":
                from moonmind.omnigent.effective_capabilities import CAPABILITY_NAMES

                async with session_factory() as session:
                    stored = await session.get(
                        OmnigentBridgeSession, row.bridge_session_id
                    )
                    stored.metadata_ = {
                        **stored.metadata_,
                        "callerAuthorities": {
                            kwargs["actor_principal"]: dict.fromkeys(
                                CAPABILITY_NAMES, False
                            )
                        },
                    }
                    await session.commit()
            else:
                await _invalidate_authority(store, row, fault)
            return await original(**kwargs)

        monkeypatch.setattr(store, "claim_canonical_turn_command", race)
        try:
            count = len(journey.server.events)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(
                    app=_application(store, os.environ["OMNIGENT_SERVER_URL"])
                ),
                base_url="http://moonmind.test",
            ) as client:
                path = f"/api/workflow-chat-bindings/{row.chat_binding_id}/omnigent/v1/sessions/{row.chat_binding_id}/events"
                response = await client.post(
                    path, json=_message(), headers={"Idempotency-Key": "review-race"}
                )
                assert response.status_code in (403, 409), (
                    response.status_code,
                    response.text,
                    len(journey.server.events) - count,
                )
                assert len(journey.server.events) == count
        finally:
            await _restore_authority(store, records)

    await _during_active_session(journey, active)


@pytest.mark.parametrize("native_journey", ["opencode-native"], indirect=True)
async def test_generic_native_upgrades_verified_routing_projection(
    native_journey, session_factory
):
    journey = native_journey
    store = OmnigentBridgeSessionStore(session_factory)

    async def active():
        row = await store.resolve_projection_session(
            idempotency_key=journey.request.idempotency_key
        )
        plan = journey.plan
        routing = {
            "executionPlanRef": plan.planRef,
            "runtimeBindingRef": row.metadata_["runtimeAuthority"]["bindingId"],
            "hostClassRef": plan.payload.hostClassRef,
            "launchPolicyRef": plan.payload.launchPolicyRef,
            "executionRealizerRef": plan.payload.executionRealizerRef,
        }
        async with session_factory() as session:
            stored = await session.get(OmnigentBridgeSession, row.bridge_session_id)
            stored.effective_launch_snapshot_json = routing
            stored.metadata_ = {
                key: value
                for key, value in stored.metadata_.items()
                if key not in {"runtimeAuthority", "capabilityAuthority"}
            }
            await session.commit()
        before = await store.resolve_chat_binding(workflow_id=row.moonmind_workflow_id)
        assert before.read_only and before.capabilities == {}
        rebound = await store.bind_profile_authorization(
            request=journey.request,
            endpoint_ref=row.omnigent_endpoint_ref,
            provider_profile_id=row.provider_profile_id,
            provider_lease_id=row.provider_lease_id,
            credential_generation=row.credential_generation,
            host_binding_ref=row.host_binding_ref,
            host_lease_ref=row.host_lease_ref,
            omnigent_host_id=row.omnigent_host_id,
            effective_launch_snapshot=routing,
        )
        assert (
            rebound.effective_launch_snapshot_json == row.effective_launch_snapshot_json
        )
        client = OmnigentHttpClient(base_url=os.environ["OMNIGENT_SERVER_URL"])
        snapshot = await client.get_session(row.omnigent_session_id)
        await store.record_session_created(
            row.idempotency_key,
            session_id=row.omnigent_session_id,
            session_status="running",
            session_snapshot=snapshot,
        )
        after = await store.resolve_chat_binding(workflow_id=row.moonmind_workflow_id)
        assert after.read_only is False

    await _during_active_session(journey, active)


@pytest.mark.parametrize("native_journey", ["opencode-native"], indirect=True)
async def test_activity_owned_live_grant_allows_retained_ledger_generation(
    native_journey, session_factory
):
    from moonmind.omnigent.provider_leases import validate_bound_provider_leases
    from moonmind.omnigent.runtime_bindings import DbRuntimeBindingStore

    journey = native_journey
    store = OmnigentBridgeSessionStore(session_factory)

    async def active():
        row = await store.resolve_projection_session(
            idempotency_key=journey.request.idempotency_key
        )
        records = await _authority_records(store, row)
        try:
            binding = await DbRuntimeBindingStore(session_factory).get(
                row.metadata_["runtimeAuthority"]["bindingId"]
            )
            values = copy.deepcopy(binding.providerLeases)
            for value in values.values():
                value["leaseOwnerIsWorkflow"] = False
            async with session_factory() as session:
                lease = (
                    await session.execute(select(ProviderProfileSlotLease))
                ).scalar_one()
                lease.owner_is_workflow = False
                lease.purpose = "execution_omnigent"
                lease.credential_generation = None
                lease.step_execution_id = None
                lease.idempotency_key = None
                await session.commit()
            kwargs = {
                "session_factory": session_factory,
                "provider_leases": values,
                "execution_plan_ref": journey.plan.planRef,
                "step_execution_id": row.step_execution_id,
                "idempotency_key": row.idempotency_key,
            }
            assert await validate_bound_provider_leases(**kwargs)
            await _invalidate_authority(store, row, "profile_rotation")
            assert not await validate_bound_provider_leases(**kwargs)
        finally:
            await _restore_authority(store, records)

    await _during_active_session(journey, active)


@pytest.mark.parametrize("native_journey", ["opencode-native"], indirect=True)
@pytest.mark.parametrize(
    "changed",
    [{"id": "foreign-session"}, {"capabilities": None}, {"capabilities": "invalid"}],
)
async def test_generic_native_retry_rejects_changed_snapshot_identity(
    native_journey, session_factory, changed
):
    journey = native_journey
    store = OmnigentBridgeSessionStore(session_factory)

    async def active():
        row = await store.resolve_projection_session(
            idempotency_key=journey.request.idempotency_key
        )
        before = await store.resolve_chat_binding(workflow_id=row.moonmind_workflow_id)
        assert before.read_only is False
        await store.record_session_created(
            row.idempotency_key,
            session_id=row.omnigent_session_id,
            session_status="running",
            session_snapshot={
                "id": row.omnigent_session_id,
                "harness": journey.harness,
                "host_id": row.omnigent_host_id,
                **changed,
            },
        )
        # A reconciliation without a new observation cannot undo the denial.
        await store.record_session_created(
            row.idempotency_key,
            session_id=row.omnigent_session_id,
            session_status="running",
        )
        after = await store.resolve_chat_binding(workflow_id=row.moonmind_workflow_id)
        assert after.read_only and after.capabilities == {}

    await _during_active_session(journey, active)
