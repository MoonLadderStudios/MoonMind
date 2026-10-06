"""Remediation handoff to the existing canonical Omnigent control owners."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models
from moonmind.omnigent.control_plane.identities import canonical_turn_command_key
from moonmind.omnigent.effective_capabilities import resolve_bridge_row_capabilities

SESSION_CONTROLS = {
    "session.interrupt_turn": ("interrupt", "interruptTurn"),
    "session.cancel": ("stop_session", "stopSession"),
}


async def resolve_remediation_session(
    session: AsyncSession, *, workflow_id: str, run_id: str, params: Mapping[str, Any]
) -> Any:
    """Resolve one persisted binding, never a runtime-derived workflow identifier."""
    query = select(models.OmnigentBridgeSession).where(
        models.OmnigentBridgeSession.moonmind_workflow_id == workflow_id,
        models.OmnigentBridgeSession.moonmind_run_id == run_id,
    )
    for key, column in (
        ("bridgeSessionId", models.OmnigentBridgeSession.bridge_session_id),
        ("stepExecutionId", models.OmnigentBridgeSession.step_execution_id),
    ):
        if params.get(key):
            query = query.where(column == str(params[key]))
    rows = (
        (
            await session.execute(
                query.limit(2).execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    if len(rows) != 1 or not rows[0].omnigent_session_id:
        raise ValueError("An exact persisted session binding is required")
    row = rows[0]
    if params.get("agentRunId") and params["agentRunId"] != row.moonmind_agent_run_id:
        raise ValueError("agentRunId does not match the persisted session binding")
    return row


def session_control_capabilities(row: Any, params: Mapping[str, Any] | None = None):
    """Intersect admitted controls with the native immutable capability authority."""
    params = params or {}
    return resolve_bridge_row_capabilities(
        row,
        caller_capabilities={
            capability: True for _, capability in SESSION_CONTROLS.values()
        },
        expected_session_epoch=params.get("expectedSessionEpoch"),
        expected_active_turn=params.get("expectedActiveTurn"),
        expected_provider_generation=params.get("expectedProviderProfileGeneration"),
        expected_agent_profile_digest=params.get("expectedAgentProfileDigest"),
        expected_launch_snapshot_ref=params.get("expectedLaunchSnapshotRef"),
        expected_policy_digest=params.get("expectedPolicyDigest"),
    )


class RemediationSessionControlService:
    """Adapt already-admitted remediation to shared claim, control, and receipt paths."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        store=None,
        proxy=None,
        config=None,
        registry=None,
    ):
        self._session = session
        self._store = store
        self._proxy = proxy
        self._config = config
        self._registry = registry

    async def execute(
        self, *, action_request: Mapping[str, Any], target: Any
    ) -> dict[str, Any]:
        # Lazy composition keeps model registration free of router imports. The
        # native owner continues to own stop/harvest/cleanup and uncertain POSTs.
        from api_service.api.routers import omnigent_bridge as native
        from api_service.api.routers.omnigent_bridge_composition import (
            build_bridge_session_proxy,
            build_bridge_session_store,
            build_embedded_host_facade,
        )
        from api_service.retrieval_capabilities import RetrievalCapabilityRegistry
        from moonmind.omnigent.bridge_config import (
            HOST_PROTOCOL_MODE_EMBEDDED,
            resolve_bridge_config,
        )
        from moonmind.omnigent.bridge_proxy import BridgeSessionEventRequest
        from moonmind.omnigent.control_plane.turn_sources import TurnSource
        from moonmind.omnigent.native_outbound_scan import (
            NativeScanSurface,
            scan_native_outbound,
        )

        kind = str(action_request["actionKind"])
        event_type, capability = SESSION_CONTROLS[kind]
        remediation_workflow_id = str(
            action_request.get("remediationWorkflowId") or ""
        ).strip()
        if not remediation_workflow_id:
            raise ValueError(
                "remediationWorkflowId is required for session command identity"
            )
        # The native command, claim and receipt share one namespace scoped to
        # the admitted remediation workflow, just like its authority ledger.
        action_id = canonical_turn_command_key(
            remediation_workflow_id, str(action_request["actionId"])
        )
        params = dict(action_request.get("params") or {})
        row = await resolve_remediation_session(
            self._session,
            workflow_id=target.workflow_id,
            run_id=target.pinned_run_id,
            params=params,
        )
        config = self._config or resolve_bridge_config()
        store = self._store or build_bridge_session_store()
        proxy = self._proxy or build_bridge_session_proxy(
            config=config, forward_headers={}
        )
        embedded_facade = (
            build_embedded_host_facade(config, drain_retained_sessions=True)
            if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
            else None
        )
        facade, embedded = await native._resolve_session_control_facade(
            session_id=row.omnigent_session_id,
            config=config,
            proxy=proxy,
            embedded_facade=embedded_facade,
            store=store,
        )
        if embedded and kind == "session.interrupt_turn":
            raise ValueError("The persisted host protocol does not support interrupt")
        if facade is None:
            raise ValueError("The persisted session control owner is unavailable")
        event = BridgeSessionEventRequest(
            type=event_type,
            **({"reason": params["reason"]} if params.get("reason") else {}),
        )
        body = {
            "event": event.model_dump(by_alias=True, exclude_none=True),
            "preconditions": params,
        }
        scan = scan_native_outbound(
            surface=NativeScanSurface.MESSAGE, body=body, idempotency_key=action_id
        )
        actor = str(action_request.get("requester") or "service:remediation-tools")
        command_key = canonical_turn_command_key(target.workflow_id, action_id)
        prior = (
            await self._session.execute(
                select(models.OmnigentCommand)
                .where(models.OmnigentCommand.idempotency_key == command_key)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if prior is not None:
            canonical = await self._session.get(
                models.OmnigentSession, prior.session_id, populate_existing=True
            )
            if (
                canonical is None
                or canonical.provider_session_ref != row.omnigent_session_id
                or prior.payload_digest != scan.payload_digest
                or prior.command_type != event_type
            ):
                raise ValueError(
                    "The action key conflicts with its persisted session command"
                )
            claimed = False
        else:
            admitted_capabilities = session_control_capabilities(row, params)
            decision = admitted_capabilities.decisions[capability]
            if not decision.allowed:
                raise ValueError(decision.reason or "Session control is unavailable")
            # The canonical owner validates the recorded principal, session
            # revision, fencing generation, and remediation source. No requested
            # authority dimensions are supplied or rewritten by this adapter.
            claimed = await native._claim_facade_message(
                store=store,
                row=row,
                event_type=event_type,
                actor=actor,
                idempotency_key=action_id,
                payload_digest=scan.payload_digest,
                turn_source=TurnSource.REMEDIATION,
                scan_evidence=scan,
                request_preconditions=params,
            )
        command = (
            await self._session.execute(
                select(models.OmnigentCommand)
                .where(models.OmnigentCommand.idempotency_key == command_key)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        turn = await self._session.get(
            models.OmnigentTurnAttempt, command.turn_attempt_id, populate_existing=True
        )
        receipt = (
            await store.get_lifecycle_event_metadata(
                row.idempotency_key, event_identity=f"workflow-chat-message:{action_id}"
            )
            or {}
        )
        identity = {
            "workflowId": target.workflow_id,
            "runId": target.pinned_run_id,
            "bridgeSessionId": row.bridge_session_id,
            "sessionId": command.session_id,
            "providerSessionId": receipt.get("providerSessionId")
            or row.omnigent_session_id,
            "providerProfileGeneration": receipt.get(
                "providerProfileGeneration", row.credential_generation
            ),
            "commandId": command.command_id,
            "turnAttemptId": command.turn_attempt_id,
            "targetTurnAttemptId": turn.parent_turn_attempt_id,
            "fencingGeneration": command.fencing_generation,
        }
        status = "accepted"
        if not claimed:
            # A replay can repair a lost receipt, but can never re-forward an
            # occupied command, even if the prior provider response was lost.
            reconciled = await native._reconcile_facade_mutation(
                store=store,
                row=row,
                facade=facade,
                control_type=event_type,
                idempotency_key=action_id,
                provider_session_id=row.omnigent_session_id,
                chat_binding_id=row.chat_binding_id or "",
                actor=actor,
                scan_evidence=scan,
            )
            if reconciled is None:
                status = "delivery_unknown"
        else:
            # Admission persists the command before provider I/O. Re-read the
            # scoped binding and immutable native authority at the effect
            # boundary; a rotated generation cannot inherit the old claim.
            fresh_row = await resolve_remediation_session(
                self._session,
                workflow_id=target.workflow_id,
                run_id=target.pinned_run_id,
                params=params,
            )
            fresh_capabilities = session_control_capabilities(fresh_row, params)
            canonical = await self._session.get(
                models.OmnigentSession, command.session_id, populate_existing=True
            )
            if (
                fresh_row.bridge_session_id != identity["bridgeSessionId"]
                or fresh_row.omnigent_session_id != identity["providerSessionId"]
                or fresh_row.credential_generation
                != identity["providerProfileGeneration"]
                or fresh_capabilities.authority_digest
                != admitted_capabilities.authority_digest
                or not fresh_capabilities.decisions[capability].allowed
                or canonical is None
                or canonical.provider_session_ref != identity["providerSessionId"]
                or canonical.fencing_generation != identity["fencingGeneration"]
            ):
                raise ValueError("Session authority changed before control dispatch")
            try:
                result = await native._apply_owned_session_control(
                    control_facade=facade,
                    session_id=row.omnigent_session_id,
                    payload=event,
                    config=config,
                    actor=actor,
                    proxy=proxy,
                    embedded_facade=facade if embedded else None,
                    registry=self._registry or RetrievalCapabilityRegistry(),
                    store=store,
                )
            except Exception:
                await native._record_facade_mutation_audit(
                    store=store,
                    row=row,
                    control_type=event_type,
                    outcome="delivery_unknown",
                    actor=actor,
                    idempotency_key=action_id,
                    scan_evidence=scan,
                )
                status = "delivery_unknown"
            else:
                await native._record_facade_mutation_audit(
                    store=store,
                    row=row,
                    control_type=event_type,
                    outcome="posted",
                    actor=actor,
                    idempotency_key=action_id,
                    scan_evidence=scan,
                    upstream_result=result,
                )
        return {
            "status": status,
            "resultingIdentity": identity,
            "verificationRequired": True,
            "message": "Session control delivery requires separate durable effect verification.",
            "afterEvidenceRefs": [f"omnigent-command:{command.command_id}"],
        }
