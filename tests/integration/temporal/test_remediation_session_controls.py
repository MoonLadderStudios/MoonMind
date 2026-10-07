"""Canonical remediation controls against durable owners and a controlled provider."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from api_service.db import models
from api_service.services.remediation_actions import TemporalRemediationControlPlane
from api_service.services.remediation_session_controls import (
    RemediationSessionControlService,
)
from moonmind.omnigent.bridge_config import OmnigentBridgeConfig
from moonmind.omnigent.bridge_proxy import OmnigentBridgeSessionProxy
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.control_plane.repositories import OmnigentControlPlaneStore
from moonmind.omnigent.effective_capabilities import CAPABILITY_NAMES
from moonmind.workflows.temporal.remediation_tools import (
    RemediationTargetHealthSnapshot,
)
from moonmind.workflows.temporal.remediation_verification import (
    CanonicalRecordEvidenceReader,
    RemediationVerificationPhase,
    TargetEvidenceSnapshot,
    verification_contract_for,
)
from tests.unit.workflows.temporal.test_remediation_context import (
    _admin_permissions,
    _admin_profile,
    _create_target_and_remediation,
    _read_artifact_json,
)
from tests.unit.workflows.temporal.test_remediation_context import (
    mock_client_adapter as _mock_client_adapter_fixture,
)
from tests.unit.workflows.temporal.test_remediation_context import temporal_db

mock_client_adapter = _mock_client_adapter_fixture

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


async def _fixture(
    session, tmp_path, *, lost_ack=False, workflow_id="wf-3624", run_id="run-3624"
):
    grants = {name: True for name in CAPABILITY_NAMES}
    bridge = models.OmnigentBridgeSession(
        bridge_session_id="bridge-3624",
        provider="omnigent",
        compatibility_profile="omnigent.server.v1",
        moonmind_workflow_id=workflow_id,
        moonmind_run_id=run_id,
        moonmind_agent_run_id="agent-3624",
        step_execution_id="step-3624",
        idempotency_key="launch-3624",
        omnigent_endpoint_ref="controlled://provider",
        omnigent_session_id="provider-3624",
        host_type="managed",
        status="running",
        provider_profile_id="profile-3624",
        credential_generation=4,
        effective_launch_snapshot_json={
            "executionProfileRef": "profile://agent",
            "executionProfileDigest": "sha256:agent",
            "launchPolicyRef": "policy://launch",
            "snapshotRef": "artifact://launch",
            "policyAuthority": {
                "snapshotRef": "artifact://policy",
                "policyDigest": "sha256:policy",
            },
        },
        metadata_={
            "hostProtocolMode": "proxy",
            "capabilityAuthority": {
                "fresh": True,
                "providerProfileGeneration": 4,
                "upstream": grants,
                "agentProfile": grants,
                "launchPolicy": grants,
                "state": {
                    "sessionEpoch": 2,
                    "activeTurnId": "original-3624",
                    "capabilities": grants,
                },
            },
        },
    )
    canonical = models.OmnigentSession(
        session_id="session-3624",
        moonmind_workflow_id=workflow_id,
        provider="omnigent",
        provider_session_ref="provider-3624",
        moonmind_agent_run_id="agent-3624",
        step_execution_id="step-3624",
        active_turn_attempt_id="original-3624",
    )
    original = models.OmnigentTurnAttempt(
        turn_attempt_id="original-3624",
        session_id="session-3624",
        idempotency_key="original-3624",
        state="running",
        lineage_kind="initial",
    )
    session.add_all([bridge, canonical, original])
    await session.commit()
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    store = OmnigentBridgeSessionStore(factory)
    control_store = OmnigentControlPlaneStore(factory)

    class Provider:
        effects = 0

        async def post_event(self, session_id, payload):
            assert session_id == "provider-3624"
            self.effects += 1
            if lost_ack:
                raise TimeoutError("provider accepted; response lost")
            return {"ok": True, "request_id": "receipt-3624"}

        async def get_session(self, session_id):
            return {"id": session_id, "status": "running"}

    provider = Provider()
    proxy = OmnigentBridgeSessionProxy(
        run_store=store, client=provider, config=OmnigentBridgeConfig()
    )
    # The registry owns only retired capability drainage. A fixture supplies its
    # empty scoped registry interface; session/command/receipt persistence is real.
    registry = SimpleNamespace(
        has_live_session_authority=lambda **_: False, revoke_scope=lambda **_: []
    )
    owner = RemediationSessionControlService(
        session,
        store=store,
        proxy=proxy,
        config=OmnigentBridgeConfig(),
        registry=registry,
    )
    target = RemediationTargetHealthSnapshot(
        workflow_id=workflow_id,
        pinned_run_id=run_id,
        current_run_id=run_id,
        state="executing",
        close_status=None,
        title=None,
        summary=None,
        target_run_changed=False,
        runtime="omnigent",
    )
    return bridge, control_store, provider, owner, target


async def _verify(session, target, request, result):
    phase = RemediationVerificationPhase(
        reader=CanonicalRecordEvidenceReader(session), max_poll_cap=0
    )
    return await phase.run(
        contract=verification_contract_for(request["actionKind"]),
        action_kind=request["actionKind"],
        action_id=request["actionId"],
        delivery_status=result["status"],
        target_workflow_id=target.workflow_id,
        pinned_run_id=target.pinned_run_id,
        before_snapshot=TargetEvidenceSnapshot(
            stage="before",
            available=True,
            workflow_id=target.workflow_id,
            run_id=target.pinned_run_id,
            state="executing",
        ),
        action_result=result,
    )


async def _confirm(control_store, kind):
    async with control_store.transaction() as repos:
        canonical = await repos.sessions.get("session-3624")
        if kind == "session.interrupt_turn":
            turn = await repos.turn_attempts.get("original-3624")
            await repos.turn_attempts.mark_terminal(
                turn.turn_attempt_id,
                "interrupted",
                expected_revision=turn.revision,
                expected_fencing_generation=canonical.fencing_generation,
                terminal_evidence_ref="artifact://controlled-interruption",
            )
        else:
            await repos.sessions.mark_terminal(
                canonical.session_id,
                "canceled",
                expected_revision=canonical.revision,
                expected_fencing_generation=canonical.fencing_generation,
                terminal_evidence_ref="artifact://controlled-cancellation",
            )


@pytest.mark.parametrize("kind", ["session.interrupt_turn", "session.cancel"])
async def test_canonical_session_control_separates_delivery_effect_and_replay(
    tmp_path, kind
):
    async with temporal_db(tmp_path) as session:
        bridge, store, provider, owner, target = await _fixture(session, tmp_path)
        plane = TemporalRemediationControlPlane(session_control_service=owner)
        request = {
            "actionKind": kind,
            "actionId": "control-3624",
            "remediationWorkflowId": "repair-3624",
            "requester": "service:remediator",
            "params": {"bridgeSessionId": bridge.bridge_session_id},
        }
        result = await plane.handlers()[kind](request, {}, target)
        assert result["status"] == "accepted"
        assert provider.effects == 1
        assert result["resultingIdentity"]["targetTurnAttemptId"] == "original-3624"
        pending = await _verify(session, target, request, result)
        assert pending.pending and pending.outcome is None
        # A new adapter instance is a worker restart. The durable command and
        # posted receipt own deduplication, not in-process state.
        replay_owner = RemediationSessionControlService(
            session,
            store=owner._store,
            proxy=owner._proxy,
            config=owner._config,
            registry=owner._registry,
        )
        replay = await replay_owner.execute(action_request=request, target=target)
        assert replay["resultingIdentity"] == result["resultingIdentity"]
        assert provider.effects == 1
        changed = await plane.handlers()[kind](
            {**request, "params": {**request["params"], "reason": "changed"}},
            {},
            target,
        )
        assert changed["status"] == "precondition_failed"
        assert provider.effects == 1
        await _confirm(store, kind)
        verified = await _verify(session, target, request, result)
        assert verified.outcome == "verified_resolved" and not verified.pending
        # A sibling command/turn or rotated credential cannot prove this effect.
        for field, value in (
            ("commandId", "sibling"),
            ("targetTurnAttemptId", "sibling"),
            ("providerProfileGeneration", 5),
        ):
            wrong = await _verify(
                session,
                target,
                request,
                {
                    **result,
                    "resultingIdentity": {**result["resultingIdentity"], field: value},
                },
            )
            assert wrong.outcome == "evidence_unavailable" and wrong.pending


@pytest.mark.parametrize("kind", ["session.interrupt_turn", "session.cancel"])
async def test_lost_session_ack_is_reconciled_without_redelivery(tmp_path, kind):
    async with temporal_db(tmp_path) as session:
        bridge, store, provider, owner, target = await _fixture(
            session, tmp_path, lost_ack=True
        )
        request = {
            "actionKind": kind,
            "actionId": "lost-3624",
            "remediationWorkflowId": "repair-3624",
            "params": {"bridgeSessionId": bridge.bridge_session_id},
        }
        first = await owner.execute(action_request=request, target=target)
        assert first["status"] == "delivery_unknown"
        second = await owner.execute(action_request=request, target=target)
        assert second["status"] == "delivery_unknown"
        assert first["resultingIdentity"] == second["resultingIdentity"]
        assert provider.effects == 1
        await _confirm(store, kind)
        verified = await _verify(session, target, request, first)
        assert verified.outcome == "verified_resolved"
        assert provider.effects == 1


@pytest.mark.parametrize(
    "params",
    [
        {"bridgeSessionId": "other"},
        {"expectedProviderProfileGeneration": 5},
        {"expectedActiveTurn": "other"},
        {"expectedSessionEpoch": 3},
    ],
)
async def test_wrong_or_stale_session_authority_never_forwards(tmp_path, params):
    async with temporal_db(tmp_path) as session:
        _bridge, _store, provider, owner, target = await _fixture(session, tmp_path)
        plane = TemporalRemediationControlPlane(session_control_service=owner)
        result = await plane.handlers()["session.interrupt_turn"](
            {
                "actionKind": "session.interrupt_turn",
                "actionId": "stale-3624",
                "remediationWorkflowId": "repair-3624",
                "params": params,
            },
            {},
            target,
        )
        assert result["status"] == "precondition_failed"
        assert provider.effects == 0


async def test_rotated_session_generation_at_effect_boundary_never_forwards(tmp_path):
    async with temporal_db(tmp_path) as session:
        bridge, _control_store, provider, owner, target = await _fixture(
            session, tmp_path
        )
        claim = owner._store.claim_canonical_turn_command

        async def rotate_after_claim(**kwargs):
            result = await claim(**kwargs)
            factory = async_sessionmaker(session.bind, expire_on_commit=False)
            async with factory() as writer:
                row = await writer.get(
                    models.OmnigentBridgeSession, bridge.bridge_session_id
                )
                row.credential_generation = 5
                await writer.commit()
            return result

        owner._store.claim_canonical_turn_command = rotate_after_claim
        plane = TemporalRemediationControlPlane(session_control_service=owner)
        result = await plane.handlers()["session.interrupt_turn"](
            {
                "actionKind": "session.interrupt_turn",
                "actionId": "rotate-3624",
                "remediationWorkflowId": "repair-3624",
                "params": {},
            },
            {},
            target,
        )
        assert result["status"] == "precondition_failed"
        assert provider.effects == 0


async def test_retained_interrupt_is_unavailable_without_disabling_execution_controls(
    tmp_path, mock_client_adapter
):
    from api_service.services.remediation_capabilities import (
        project_remediation_action_inputs,
        remediation_link_capabilities,
    )

    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session, mock_client_adapter, authority_mode="admin_auto"
        )
        bridge, *_ = await _fixture(
            session, tmp_path, workflow_id=target.workflow_id, run_id=target.run_id
        )
        bridge.metadata_ = {**bridge.metadata_, "hostProtocolMode": "embedded"}
        await session.commit()
        link = await session.get(
            models.TemporalExecutionRemediationLink, remediation.workflow_id
        )
        await project_remediation_action_inputs(link, session=session)
        rows = {row["actionKind"]: row for row in remediation_link_capabilities(link)}
        assert not rows["session.interrupt_turn"]["requestable"]
        assert (
            "omnigent_bridge_mode_unsupported"
            in rows["session.interrupt_turn"]["blockedReasons"]
        )
        assert rows["execution.pause"]["requestable"]


@pytest.mark.parametrize("kind", ["session.interrupt_turn", "session.cancel"])
async def test_tool_session_control_persists_and_resumes_exact_ui_verification(
    tmp_path, mock_client_adapter, monkeypatch, kind
):
    from api_service.api.routers.executions import (
        _attach_remediation_capability_projection,
        _serialize_remediation_link_summary,
    )
    from moonmind.omnigent.policies import (
        compile_policy_snapshot,
        document_digest,
        policy_authority_evidence,
    )
    from moonmind.workflows.temporal import (
        LocalTemporalArtifactStore,
        TemporalArtifactRepository,
        TemporalArtifactService,
    )
    from moonmind.workflows.temporal.remediation_actions import (
        RemediationActionAuthorityService,
        RemediationMutationGuardPolicy,
        RemediationMutationGuardService,
    )
    from moonmind.workflows.temporal.remediation_context import (
        RemediationContextBuilder,
    )
    from moonmind.workflows.temporal.remediation_tools import (
        MoonMindControlPlaneRemediationActionExecutor,
        RemediationEvidenceToolService,
    )
    from tests.unit.omnigent.test_policy_authority import policy_document

    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session, mock_client_adapter, authority_mode="admin_auto"
        )
        bridge, store, provider, owner, health = await _fixture(
            session, tmp_path, workflow_id=target.workflow_id, run_id=target.run_id
        )
        document = policy_document()
        document["approvals"]["actions"][kind] = {
            "decision": "allow",
            "reason": "admitted session control",
        }
        validation = {"valid": True}
        snapshot = compile_policy_snapshot(
            policy_id="control-3624",
            version=1,
            document=document,
            validation=validation,
        )
        session.add(
            models.OmnigentPolicy(policy_id="control-3624", name="Fixture policy")
        )
        session.add(
            models.OmnigentPolicyVersion(
                policy_id="control-3624",
                version=1,
                state="active",
                document_json=document,
                digest=document_digest(document),
                created_by="fixture",
                validation_json=validation,
            )
        )
        bridge.effective_launch_snapshot_json = {
            **bridge.effective_launch_snapshot_json,
            "policyAuthority": policy_authority_evidence(snapshot),
        }
        canonical = await session.get(
            models.TemporalExecutionCanonicalRecord, target.workflow_id
        )
        canonical.search_attributes = {
            **canonical.search_attributes,
            "mm_target_runtime": "omnigent",
        }
        parameters = dict(canonical.parameters)
        parameters["workflow"] = {
            **parameters.get("workflow", {}),
            "runtime": {"mode": "omnigent"},
        }
        canonical.parameters = parameters
        # Session cancellation must preserve the original failed source, its
        # saved work, and its diagnostics rather than turning it into success.
        canonical.state = models.MoonMindWorkflowState.FAILED
        canonical.memo = {**canonical.memo, "summary": "Original target failure"}
        await session.commit()
        artifacts = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        await RemediationContextBuilder(
            session=session, artifact_service=artifacts
        ).build_context(remediation_workflow_id=remediation.workflow_id)
        params = {"bridgeSessionId": bridge.bridge_session_id}
        authority = await RemediationActionAuthorityService(
            session=session
        ).evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind=kind,
            parameters=params,
            dry_run=False,
            idempotency_key="tool-3624",
            requesting_principal="service:remediator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(allowed_action_kinds=(kind,)),
        )
        assert authority.executable, authority.to_dict()
        guard = await RemediationMutationGuardService(session=session).evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind=kind,
            idempotency_key="tool-3624",
            parameters=params,
            policy=RemediationMutationGuardPolicy(cooldown_seconds=0),
            now=datetime.now(timezone.utc),
        )
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifacts,
            action_executor=MoonMindControlPlaneRemediationActionExecutor(
                TemporalRemediationControlPlane(session_control_service=owner).handlers(
                    enforce_policy=True
                )
            ),
            verification_phase=RemediationVerificationPhase(
                reader=CanonicalRecordEvidenceReader(session), max_poll_cap=0
            ),
        )
        context = await tools.get_context(
            remediation_workflow_id=remediation.workflow_id,
            principal="service:test",
            admitted_principal="service:remediation-context",
        )
        link = await session.get(
            models.TemporalExecutionRemediationLink, remediation.workflow_id
        )
        await _attach_remediation_capability_projection(
            link, session=session, principal="service:remediation-context"
        )
        ui = _serialize_remediation_link_summary(link)
        assert (
            ui.model_dump(by_alias=True)["actionCapabilities"]
            == context["actionCapabilities"]
        )
        assert kind in context["allowedActions"], next(
            row for row in context["actionCapabilities"] if row["actionKind"] == kind
        )
        first = await tools.execute_action(
            remediation_workflow_id=remediation.workflow_id,
            authority_result=authority.to_dict(),
            guard_result=guard.to_dict(),
            principal="service:test",
            admitted_principal="service:remediation-context",
        )
        assert first["verification"]["pending"]
        result = await _read_artifact_json(
            artifacts, first["artifactRefs"]["actionResult"]
        )
        identity = result["resultingIdentity"]
        assert identity["providerSessionId"] == bridge.omnigent_session_id, identity
        assert (
            identity["providerProfileGeneration"] == bridge.credential_generation
        ), identity
        assert identity["workflowId"] == target.workflow_id, identity
        assert identity["runId"] == target.run_id, identity
        # The real terminal owner must resume the saved verifier after commit,
        # without a caller invoking continuation or repeating the control.
        monkeypatch.setattr(
            "moonmind.workflows.get_temporal_artifact_service",
            lambda db: TemporalArtifactService(
                TemporalArtifactRepository(db), store=artifacts._store
            ),
        )
        await _confirm(store, kind)
        await session.refresh(link)
        ui = _serialize_remediation_link_summary(link)
        assert ui.deliveryStatus == "accepted"
        assert (
            ui.verificationOutcome == "verified_resolved"
        ), link.mutation_guard_ledger_state["entries"]["tool-3624"]["execution"][
            "verificationResponse"
        ][
            "verification"
        ][
            "reason"
        ]
        assert provider.effects == 1
        await session.refresh(canonical)
        assert canonical.state == models.MoonMindWorkflowState.FAILED
        assert canonical.memo["summary"] == "Original target failure"


async def test_sequential_remediations_have_distinct_canonical_commands(tmp_path):
    async with temporal_db(tmp_path) as session:
        bridge, store, provider, owner, target = await _fixture(session, tmp_path)
        request = {
            "actionKind": "session.interrupt_turn",
            "actionId": "control-1",
            "remediationWorkflowId": "repair-first",
            "params": {"bridgeSessionId": bridge.bridge_session_id},
        }
        first = await owner.execute(action_request=request, target=target)
        second = await owner.execute(
            action_request={**request, "remediationWorkflowId": "repair-second"},
            target=target,
        )
        assert (
            first["resultingIdentity"]["commandId"]
            != second["resultingIdentity"]["commandId"]
        )
        assert provider.effects == 2
        replay = await owner.execute(action_request=request, target=target)
        assert replay["resultingIdentity"] == first["resultingIdentity"]
        assert provider.effects == 2


async def test_embedded_cancel_supplies_recorded_drain_facade(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    async with temporal_db(tmp_path) as session:
        bridge, _store, provider, owner, target = await _fixture(session, tmp_path)
        bridge.metadata_ = {**bridge.metadata_, "hostProtocolMode": "embedded"}
        await session.commit()
        from moonmind.omnigent.bridge_config import HOST_PROTOCOL_MODE_EMBEDDED

        owner._config = OmnigentBridgeConfig(
            enabled=False,
            compatibility={"hostProtocolMode": HOST_PROTOCOL_MODE_EMBEDDED},
        )
        facade = object()
        build = __import__("unittest.mock", fromlist=["Mock"]).Mock(return_value=facade)
        monkeypatch.setattr(
            "api_service.api.routers.omnigent_bridge_composition.build_embedded_host_facade",
            build,
        )
        apply = AsyncMock(return_value={"ok": True})
        monkeypatch.setattr(
            "api_service.api.routers.omnigent_bridge._apply_owned_session_control",
            apply,
        )
        result = await owner.execute(
            action_request={
                "actionKind": "session.cancel",
                "actionId": "stop-1",
                "remediationWorkflowId": "repair-embedded",
                "params": {},
            },
            target=target,
        )
        assert result["status"] == "accepted"
        assert apply.await_args.kwargs["control_facade"] is facade
        build.assert_called_once_with(owner._config, drain_retained_sessions=True)


async def test_concurrent_session_discovery_requires_exact_selector(
    tmp_path, mock_client_adapter
):
    from api_service.services.remediation_capabilities import (
        project_remediation_action_inputs,
        remediation_link_capabilities,
    )

    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session, mock_client_adapter, authority_mode="admin_auto"
        )
        bridge, _store, _provider, owner, health = await _fixture(
            session, tmp_path, workflow_id=target.workflow_id, run_id=target.run_id
        )
        other = models.OmnigentBridgeSession(
            bridge_session_id="bridge-other",
            provider="omnigent",
            compatibility_profile="omnigent.server.v1",
            moonmind_workflow_id=target.workflow_id,
            moonmind_run_id=target.run_id,
            moonmind_agent_run_id="agent-other",
            step_execution_id="step-other",
            idempotency_key="launch-other",
            omnigent_endpoint_ref="controlled://provider",
            omnigent_session_id="provider-other",
            host_type="managed",
            status="running",
            effective_launch_snapshot_json=bridge.effective_launch_snapshot_json,
            metadata_=bridge.metadata_,
            credential_generation=4,
        )
        canonical = await session.get(
            models.TemporalExecutionCanonicalRecord, target.workflow_id
        )
        canonical.parameters = {
            **canonical.parameters,
            "workflow": {
                **canonical.parameters.get("workflow", {}),
                "runtime": {"mode": "omnigent"},
            },
        }
        session.add(other)
        await session.commit()
        link = await session.get(
            models.TemporalExecutionRemediationLink, remediation.workflow_id
        )
        await project_remediation_action_inputs(link, session=session)
        rows = {row["actionKind"]: row for row in remediation_link_capabilities(link)}
        for kind in ("session.interrupt_turn", "session.cancel"):
            assert rows[kind]["requestable"], rows[kind]
            assert rows[kind]["targetSelectorRequired"] is True
            assert rows[kind]["targetSelectorOptions"] == [
                "bridgeSessionId",
                "stepExecutionId",
            ]
        plane = TemporalRemediationControlPlane(session_control_service=owner)
        request = {
            "actionKind": "session.interrupt_turn",
            "actionId": "selector-1",
            "remediationWorkflowId": remediation.workflow_id,
            "params": {},
        }
        unscoped = await plane.handlers()[request["actionKind"]](request, {}, health)
        assert unscoped["status"] == "precondition_failed"
        selected = await owner.execute(
            action_request={
                **request,
                "params": {"bridgeSessionId": bridge.bridge_session_id},
            },
            target=health,
        )
        assert selected["status"] == "accepted"


async def test_terminal_notification_failure_keeps_evidence_and_retries(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    async with temporal_db(tmp_path) as session:
        _bridge, store, _provider, _owner, target = await _fixture(session, tmp_path)
        notify = AsyncMock(side_effect=OSError("verification unavailable"))
        monkeypatch.setattr(
            "moonmind.workflows.temporal.remediation_tools.resume_pending_action_verifications",
            notify,
        )
        with pytest.raises(OSError, match="verification unavailable"):
            await _confirm(store, "session.cancel")
        async with store.transaction() as repos:
            canonical = await repos.sessions.get("session-3624")
            assert canonical.terminal_state == "canceled"
            assert (
                canonical.terminal_evidence_ref == "artifact://controlled-cancellation"
            )
        notify.side_effect = None
        notify.return_value = 0
        await _confirm(store, "session.cancel")
        assert notify.await_count == 2
        assert notify.await_args.kwargs == {"workflow_id": target.workflow_id}


async def test_rolled_back_terminal_write_never_notifies(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    async with temporal_db(tmp_path) as session:
        _bridge, store, _provider, _owner, _target = await _fixture(session, tmp_path)
        notify = AsyncMock()
        monkeypatch.setattr(
            "moonmind.workflows.temporal.remediation_tools.resume_pending_action_verifications",
            notify,
        )

        async def abort_terminal_write():
            async with store.transaction() as repos:
                canonical = await repos.sessions.get("session-3624")
                await repos.sessions.mark_terminal(
                    canonical.session_id,
                    "canceled",
                    expected_revision=canonical.revision,
                    expected_fencing_generation=canonical.fencing_generation,
                    terminal_evidence_ref="artifact://cancellation",
                )
                raise ValueError("abort write")

        with pytest.raises(ValueError, match="abort write"):
            await abort_terminal_write()
        notify.assert_not_awaited()
        async with store.transaction() as repos:
            canonical = await repos.sessions.get("session-3624")
            assert canonical.terminal_state is None
