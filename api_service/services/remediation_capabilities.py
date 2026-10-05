"""Shared exact-target remediation availability for API and tool consumers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models
from api_service.services.checkpoint_branch_turn_execution import (
    checkpoint_branch_turn_owner_operational,
)
from api_service.services.remediation_actions import build_remediation_action_executor
from moonmind.workflows.temporal.remediation_actions import (
    RemediationCapabilityContext,
    remediation_action_capability_matrix,
    remediation_action_kinds,
)
from moonmind.workflows.temporal.remediation_verification import (
    verification_backend_operational,
)


def remediation_link_capabilities(link: Any) -> list[dict[str, Any]]:
    """Evaluate the same projected authority/readiness inputs for every consumer."""
    authority_mode = str(getattr(link, "authority_mode", "") or "")
    policy_actions = tuple(getattr(link, "allowed_actions", None) or ())
    target_state = str(getattr(link, "current_target_state", "") or "").strip()
    unavailable_evidence = set(
        getattr(link, "unavailable_evidence_classes", None) or ()
    )
    known_evidence = {
        "execution_and_steps",
        "execution_state",
        "workflow_history",
        "target_identity",
        "action_result",
        "continuity_boundary",
    } - unavailable_evidence
    if getattr(link, "core_target_evidence_available", False):
        # Fresh canonical identity/state are usable even when optional context
        # enrichment or step journals were unavailable at snapshot time.
        known_evidence.update(
            {"execution_and_steps", "execution_state", "target_identity"}
        )
    approval_backend_ready = authority_mode in {"admin_auto", "approval_gated"}
    executor = build_remediation_action_executor()
    backend_readiness = {
        action_kind: action_kind in executor._adapters
        for action_kind in (
            row["actionKind"] for row in remediation_action_capability_matrix()
        )
    }
    checkpoint_action = "checkpoint_branch.create_from_remediation_context"
    checkpoint_owner_ready = bool(getattr(link, "checkpoint_branch_owner_ready", False))
    checkpoint_verifier_ready = bool(
        getattr(link, "checkpoint_branch_verifier_ready", False)
    )
    backend_readiness.update(getattr(link, "session_control_readiness", None) or {})
    backend_readiness[checkpoint_action] = (
        checkpoint_owner_ready and checkpoint_verifier_ready
    )
    verifier_readiness = {
        str(row["actionKind"]): True for row in remediation_action_capability_matrix()
    }
    verifier_readiness[checkpoint_action] = checkpoint_verifier_ready
    capability_context = RemediationCapabilityContext(
        target_runtime=(str(getattr(link, "target_runtime", "") or "").strip() or None),
        host_mode=(str(getattr(link, "host_mode", "") or "").strip() or None),
        target_state_eligible=target_state.lower() not in {"unknown", "missing"},
        target_state=target_state or None,
        target_paused=getattr(link, "target_paused", None),
        current_evidence_classes=tuple(sorted(known_evidence)),
        require_current_evidence=bool(getattr(link, "evidence_degraded", False)),
        # Missing persisted policy projection is not permission to advertise
        # every catalog action. Fail closed until the owning projection supplies
        # the immutable target-policy intersection.
        policy_allowed_action_kinds=tuple(policy_actions or ()),
        caller_allowed_action_kinds=tuple(policy_actions or ()),
        execution_backend_readiness=backend_readiness,
        action_blocked_reasons=getattr(link, "session_control_blocked_reasons", None),
        approval_backend_ready=approval_backend_ready,
        verification_backend_readiness=verifier_readiness,
    )
    return [
        dict(row)
        for row in remediation_action_capability_matrix(context=capability_context)
    ]


async def project_remediation_action_inputs(link: Any, *, session: AsyncSession) -> Any:
    """Read fresh persisted bindings; optional runtime readiness bounds only its actions."""
    target = await session.get(
        db_models.TemporalExecutionCanonicalRecord,
        str(getattr(link, "target_workflow_id", "")),
        populate_existing=True,
    )
    remediation = await session.get(
        db_models.TemporalExecutionCanonicalRecord,
        str(getattr(link, "remediation_workflow_id", "")),
        populate_existing=True,
    )
    link.core_target_evidence_available = (
        target is not None and target.run_id == link.target_run_id
    )
    if target is None or remediation is None:
        link.current_target_state = "missing"
        link.allowed_actions = ()
        link.evidence_degraded = True
        link.unavailable_evidence_classes = ("target_identity",)
        return None

    state = getattr(target, "state", None)
    link.current_target_state = str(getattr(state, "value", state) or "")
    link.target_paused = bool(target.paused)
    if target.run_id != link.target_run_id:
        link.current_target_state = "unknown"
    target_parameters = dict(getattr(target, "parameters", None) or {})
    target_workflow = target_parameters.get("workflow") or target_parameters.get("task")
    target_workflow = target_workflow if isinstance(target_workflow, Mapping) else {}
    runtime = target_workflow.get("runtime")
    runtime = runtime if isinstance(runtime, Mapping) else {}
    search_attributes = dict(getattr(target, "search_attributes", None) or {})
    link.target_runtime = (
        str(
            runtime.get("mode")
            or target_parameters.get("targetRuntime")
            or search_attributes.get("mm_target_runtime")
            or ""
        ).strip()
        or None
    )

    remediation_parameters = dict(getattr(remediation, "parameters", None) or {})
    remediation_workflow = remediation_parameters.get(
        "workflow"
    ) or remediation_parameters.get("task")
    remediation_workflow = (
        remediation_workflow if isinstance(remediation_workflow, Mapping) else {}
    )
    policy = remediation_workflow.get("remediation")
    policy = policy if isinstance(policy, Mapping) else {}
    link.allowed_actions = (
        remediation_action_kinds()
        if policy.get("actionPolicyRef") == "admin_healer_default"
        else ()
    )

    bridge = (
        await session.execute(
            select(db_models.OmnigentBridgeSession)
            .where(
                db_models.OmnigentBridgeSession.moonmind_workflow_id
                == link.target_workflow_id,
                db_models.OmnigentBridgeSession.moonmind_run_id == link.target_run_id,
            )
            .order_by(db_models.OmnigentBridgeSession.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    from api_service.services.remediation_session_controls import (
        SESSION_CONTROLS,
        session_control_capabilities,
    )

    # Multiple bindings require an explicit selector at dispatch. Do not guess
    # which concurrent turn an unscoped control should interrupt.
    bindings = (
        (
            await session.execute(
                select(db_models.OmnigentBridgeSession.bridge_session_id)
                .where(
                    db_models.OmnigentBridgeSession.moonmind_workflow_id
                    == link.target_workflow_id,
                    db_models.OmnigentBridgeSession.moonmind_run_id
                    == link.target_run_id,
                )
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    native_capabilities = (
        session_control_capabilities(bridge)
        if bridge is not None and len(bindings) == 1
        else None
    )
    link.session_control_blocked_reasons = (
        {
            kind: (
                [
                    (
                        native_capabilities.decisions[capability].reason
                        or "session_binding_unavailable"
                    )
                ]
                if native_capabilities is None
                or not native_capabilities.decisions[capability].allowed
                else []
            )
            for kind, (_, capability) in SESSION_CONTROLS.items()
        }
        if native_capabilities is not None
        else {kind: ["session_binding_unavailable"] for kind in SESSION_CONTROLS}
    )
    link.session_control_readiness = {
        kind: bool(
            bridge is not None
            and bridge.omnigent_session_id
            and native_capabilities is not None
            and native_capabilities.decisions[capability].allowed
        )
        for kind, (_, capability) in SESSION_CONTROLS.items()
    }
    # Retained sessions keep their recorded control owner. Its embedded
    # protocol cannot interrupt a turn; basic execution controls remain usable.
    if (
        bridge is not None
        and (bridge.metadata_ or {}).get("hostProtocolMode") == "embedded"
    ):
        link.session_control_readiness["session.interrupt_turn"] = False
        link.session_control_blocked_reasons["session.interrupt_turn"] = [
            "omnigent_bridge_mode_unsupported"
        ]
    launch = (
        bridge.effective_launch_snapshot_json
        if bridge is not None
        and isinstance(bridge.effective_launch_snapshot_json, Mapping)
        else {}
    )
    link.host_mode = str(launch.get("hostMode") or "").strip() or None
    link.evidence_degraded = False
    link.unavailable_evidence_classes = ()
    link.checkpoint_branch_owner_ready = bool(
        bridge is not None
        and checkpoint_branch_turn_owner_operational()
        and all(
            str(launch.get(field) or "").strip()
            for field in (
                "providerProfileId",
                "executionProfileRef",
                "launchPolicyRef",
            )
        )
    )
    # The verifier is an independent authority boundary. Keep its readiness
    # separate so graph/executor availability cannot advertise repair proof.
    link.checkpoint_branch_verifier_ready = verification_backend_operational(
        "checkpoint_branch.create_from_remediation_context"
    )
    return remediation
