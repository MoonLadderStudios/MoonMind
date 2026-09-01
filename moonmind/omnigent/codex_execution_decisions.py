"""Pure Codex profile-bound execution decisions.

Source issue: MoonLadderStudios/MoonMind#3711.

These are the launch-classification, budget-authority, request-identity, and
authored-request binding decisions that the replay-visible
``codex-profile-bound@1`` realizer applies. They are pure: no persistence,
provider transport, container, framework, settings, or environment authority
crosses this boundary, so each decision is separately testable without a
coordinator, a Provider Profile, or a durable host.

The legacy coordinator (:mod:`moonmind.omnigent.profile_bound_execution`) keeps
its side-effect ownership and imports these decisions. It must not re-implement
them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from moonmind.omnigent.checkpoints import (
    CandidateWorkspaceAuthority,
    OmnigentRestoreMaterial,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformFailure,
    remediation_for,
)
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
)


__all__ = [
    "bind_candidate_workspace",
    "bind_cold_restore_workspace_spec",
    "bind_exact_host",
    "classify_launch_failure_evidence",
    "compile_follow_up_retrieval_policy",
    "enforce_required_follow_up_retrieval",
    "max_budget_enforcement_rejection",
    "persisted_diagnostics_ref",
    "prepare_host_failure_stage",
    "request_identity",
]


def classify_launch_failure_evidence(exc: Exception) -> tuple[str, str, str]:
    """Return stable launch classification and operator remediation."""

    code = str(getattr(exc, "code", None) or type(exc).__name__)[:96]
    lowered = code.lower()
    if "policy" in lowered or "authorization" in lowered:
        return code, "authorization_error", "contact_administrator"
    if "profile_resolution" in lowered:
        return code, "configuration_error", "select_execution_profile"
    if "profile_readiness" in lowered:
        return code, "configuration_error", "validate_codex_oauth"
    if "credential" in lowered or "oauth" in lowered:
        return code, "configuration_error", "validate_codex_oauth"
    if "lease" in lowered:
        return code, "resource_unavailable", "wait_for_profile_lease"
    if "auth" in lowered:
        return code, "configuration_error", "repair_bridge_authentication"
    if "binding" in lowered or "harness" in lowered or "capability" in lowered:
        return code, "configuration_error", "correct_host_binding"
    if "image" in lowered or "container" in lowered:
        return code, "configuration_error", "repair_host_image"
    if "network" in lowered or "endpoint" in lowered:
        return code, "integration_error", "repair_server_endpoint"
    if code in HarnessPlatformFailure.__members__:
        # A platform-typed code that matched no launch keyword above still
        # declares its own remediation; do not downgrade it to a transient retry.
        return code, "integration_error", remediation_for(code)
    return code, "integration_error", "retry_transient_upstream"


def prepare_host_failure_stage(exc: Exception) -> str | None:
    """Map a prepare-host failure to the boundary that actually reported it."""

    code = str(getattr(exc, "code", None) or "").lower()
    if any(
        marker in code
        for marker in ("credential_volume", "credential_owner", "credential_generation")
    ):
        return "credential_mount"
    if "oauth" in code or "credential" in code or "github_auth" in code:
        return "credential_preflight"
    if "host_registration" in code:
        return "host_registration"
    if "capability" in code or "harness" in code:
        return "harness_readiness"
    if "bridge_auth" in code or "server_endpoint" in code:
        return "bridge_authentication"
    return None


def persisted_diagnostics_ref(value: object) -> str | None:
    """Extract only an already-persisted diagnostics reference from failures/results."""

    for name in ("diagnostics_ref", "diagnosticsRef", "artifact_ref"):
        ref = str(getattr(value, name, "") or "").strip()
        if ref:
            return ref[:1024]
    return None


def request_identity(request: AgentExecutionRequest) -> tuple[str, str | None]:
    if request.step_execution is not None:
        return (
            request.step_execution.workflow_id,
            request.step_execution.step_execution_id,
        )
    parameters = request.parameters if isinstance(request.parameters, Mapping) else {}
    step = parameters.get("stepExecution")
    if not isinstance(step, Mapping):
        step = {}
    workflow_id = str(
        step.get("workflowId") or parameters.get("workflowId") or request.correlation_id
    ).strip()
    step_execution_id = str(step.get("stepExecutionId") or "").strip() or None
    return workflow_id, step_execution_id


def max_budget_enforcement_rejection(
    request: AgentExecutionRequest,
) -> AgentRunResult | None:
    """Return a terminal rejection when a USD cap cannot be enforced."""

    parameters = request.parameters if isinstance(request.parameters, Mapping) else {}
    value = parameters.get("maxBudgetUsd")
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        return AgentRunResult(
            summary="maxBudgetUsd must be a finite positive number",
            failureClass="user_error",
            providerErrorCode="OMNIGENT_MAX_BUDGET_INVALID",
            retryRecommendation="correct_max_budget",
        )
    # This path exposes terminal usage evidence, not a provider-native
    # prospective USD hard stop. Starting anyway would silently discard
    # billing authority, so reject before creating a bridge run or acquiring
    # Provider Profile capacity.
    return AgentRunResult(
        summary="The selected profile-bound runtime cannot enforce maxBudgetUsd.",
        failureClass="user_error",
        providerErrorCode="OMNIGENT_MAX_BUDGET_ENFORCEMENT_UNAVAILABLE",
        retryRecommendation="remove_budget_or_select_capable_runtime",
    )


def bind_exact_host(
    request: AgentExecutionRequest,
    *,
    host_id: str,
    workspace_path: str,
    profile_authorization: Mapping[str, Any],
    harness: str,
    agent_name: str,
) -> AgentExecutionRequest:
    parameters = dict(request.parameters or {})
    raw = parameters.get("omnigent")
    omnigent = dict(raw) if isinstance(raw, Mapping) else {}
    raw_session = omnigent.get("session")
    session = dict(raw_session) if isinstance(raw_session, Mapping) else {}
    caller_host_id = str(session.get("hostId") or session.get("host_id") or "").strip()
    if caller_host_id and caller_host_id != host_id:
        raise OmnigentOAuthHostError(
            "caller-provided hostId does not match the profile binding",
            code="OMNIGENT_HOST_BINDING_MISMATCH",
        )
    session["hostType"] = "external"
    session["hostId"] = host_id
    session["workspace"] = workspace_path
    session.pop("host_id", None)
    omnigent["session"] = session
    agent = dict(omnigent.get("agent") or {})
    caller_harness = str(agent.get("harnessOverride") or "").strip()
    if caller_harness and caller_harness != harness:
        raise OmnigentOAuthHostError(
            "selected Omnigent harness conflicts with the execution profile",
            code="OMNIGENT_HARNESS_PROVIDER_MISMATCH",
        )
    agent["harnessOverride"] = harness
    agent["agentName"] = agent_name
    omnigent["agent"] = agent
    omnigent["_moonmindProfileAuthorization"] = dict(profile_authorization)
    parameters["omnigent"] = omnigent
    return request.model_copy(update={"parameters": parameters})


def bind_candidate_workspace(
    request: AgentExecutionRequest,
    candidate: CandidateWorkspaceAuthority,
) -> AgentExecutionRequest:
    """Bind continuation to the exact MoonMind checkpoint, never a workspace root."""

    parameters = dict(request.parameters or {})
    parameters["candidateWorkspace"] = candidate.model_dump(by_alias=True, mode="json")
    return request.model_copy(
        update={
            "parameters": parameters,
            "input_refs": list(
                dict.fromkeys(
                    [
                        *request.input_refs,
                        candidate.head_ref,
                        candidate.checkpoint_ref,
                    ]
                )
            ),
        }
    )


def bind_cold_restore_workspace_spec(
    authored_spec: Mapping[str, Any],
    *,
    restore_material: OmnigentRestoreMaterial,
    candidate_workspace: CandidateWorkspaceAuthority,
) -> dict[str, Any]:
    """Route validated restore evidence through the canonical workspace boundary.

    The host materializer consumes ``checkoutCommit`` and ``restoreInputRefs``;
    keeping them only in execution parameters would launch a clean host without
    reconstructing the repository plane.
    """

    workspace_spec = dict(authored_spec or {})
    existing_checkout = str(
        workspace_spec.get("checkoutCommit")
        or workspace_spec.get("baseCommit")
        or ""
    ).strip()
    if existing_checkout and existing_checkout != restore_material.baseline_commit:
        raise ValueError("cold restore baseline conflicts with authored workspace")
    workspace_spec["checkoutCommit"] = restore_material.baseline_commit
    workspace_spec.pop("baseCommit", None)
    existing_refs = workspace_spec.get("restoreInputRefs")
    if existing_refs is not None and not isinstance(existing_refs, (list, tuple)):
        raise ValueError("workspaceSpec.restoreInputRefs must be a list")
    workspace_spec["restoreInputRefs"] = list(
        dict.fromkeys(
            [
                *(str(ref).strip() for ref in (existing_refs or ()) if str(ref).strip()),
                restore_material.workspace_checkpoint_ref,
                *([restore_material.diff_ref] if restore_material.diff_ref else []),
                restore_material.head_ref,
                candidate_workspace.checkpoint_ref,
                candidate_workspace.head_ref,
            ]
        )
    )
    # Workspace checkpoints are executable restore authority, not passive input
    # attachments.  Keep the applying boundary explicit for the owning host.
    workspace_spec["workspaceCheckpointRestoreRef"] = (
        restore_material.workspace_checkpoint_ref
    )
    return workspace_spec


# Optional positive-integer ceilings an authoring surface may narrow. The
# retrieval gateway (``_bridge_authoritative_issue``) and the deployment budget
# snapshot (``_server_policy_snapshot``) clamp any host request against these at
# issue time, so the compiled block is an *authored* per-run ceiling and can
# never broaden deployment policy.
_FOLLOW_UP_RETRIEVAL_INT_FIELDS: tuple[str, ...] = (
    "topK",
    "maxSources",
    "maxQueryBytes",
    "maxContextBytes",
    "maxContextTokens",
    "maxQueries",
    "latencyMs",
    "maxConcurrency",
    "maxRequestsPerMinute",
    "embeddingTimeoutMs",
    "searchTimeoutMs",
    "overlayMaxAgeSeconds",
    "retentionDays",
    "maxLifetimeSeconds",
)


def _coerce_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def compile_follow_up_retrieval_policy(
    policy_snapshot: Mapping[str, Any],
    parameters: Mapping[str, Any] | None,
    *,
    repository: str,
    tenant_id: str,
) -> dict[str, Any]:
    """Compile the runtime ``followUpRetrieval`` block carried by the launch snapshot.

    In-session (follow-up) retrieval grants the host an authorized capability, so
    it is an authority boundary and stays disabled unless an authoring surface
    explicitly enables it via ``parameters["followUpRetrieval"]``. Deployment
    policy (``boundaries.rag``) supplies the default budgets; the gateway and the
    server budget snapshot enforce the deployment ceilings, so this block is only
    the authored per-run ceiling and never a broadening of policy.
    """

    authored: dict[str, Any] = {}
    if isinstance(parameters, Mapping):
        raw = parameters.get("followUpRetrieval")
        if isinstance(raw, Mapping):
            authored = dict(raw)
    if authored.get("enabled") is not True:
        return {"enabled": False}

    rag: dict[str, Any] = {}
    boundaries = (
        policy_snapshot.get("boundaries")
        if isinstance(policy_snapshot, Mapping)
        else None
    )
    if isinstance(boundaries, Mapping) and isinstance(boundaries.get("rag"), Mapping):
        rag = dict(boundaries["rag"])

    collections = list(
        dict.fromkeys(
            str(item).strip()
            for item in authored.get("collections", ())
            if str(item).strip()
        )
    )
    repository = str(repository or "").strip()
    tenant_id = str(tenant_id or "").strip()
    policy_version = (
        str(policy_snapshot.get("policyRef") or "").strip()
        if isinstance(policy_snapshot, Mapping)
        else ""
    )

    if not (repository and tenant_id and policy_version and collections):
        # Enabling without a resolvable scope would only yield an auditable 409
        # from the gateway; keep the capability unavailable with an explicit,
        # non-fatal reason instead of persisting a broken authority block.
        return {"enabled": False, "reason": "incomplete_follow_up_retrieval_scope"}

    block: dict[str, Any] = {
        "enabled": True,
        "required": bool(authored.get("required", False)),
        "repository": repository,
        "tenantId": tenant_id,
        "policyVersion": policy_version,
        "collections": collections,
        "overlayPolicy": (
            "skip" if str(authored.get("overlayPolicy")) == "skip" else "include"
        ),
        "staleOverlayAllowed": bool(authored.get("staleOverlayAllowed", False)),
        "fallbackAllowed": bool(authored.get("fallbackAllowed", False)),
    }

    filters = authored.get("filters")
    if isinstance(filters, Mapping):
        compiled_filters = {
            str(key): str(value)
            for key, value in filters.items()
            if str(key).strip() and str(value).strip()
        }
        if compiled_filters:
            block["filters"] = compiled_filters

    for field in _FOLLOW_UP_RETRIEVAL_INT_FIELDS:
        coerced = _coerce_positive_int(authored.get(field))
        if coerced is not None:
            block[field] = coerced

    # Clamp the policy-backed budgets to ``boundaries.rag`` so an authored run
    # override can only ever narrow deployment policy, never broaden it. When the
    # author omitted the field the policy value becomes the ceiling; when both are
    # present the tighter (minimum) value wins. The gateway clamps host requests
    # against deployment *environment* limits, not the selected policy, so the
    # selected-policy ceiling must be folded in here or a run could receive a
    # larger retrieval budget than its policy authorizes.
    for block_field, policy_key in (
        ("latencyMs", "latencyBudgetMs"),
        ("maxContextTokens", "tokenBudget"),
    ):
        policy_value = _coerce_positive_int(rag.get(policy_key))
        if policy_value is None:
            continue
        authored_value = block.get(block_field)
        block[block_field] = (
            min(authored_value, policy_value)
            if isinstance(authored_value, int)
            else policy_value
        )

    return block


def enforce_required_follow_up_retrieval(
    authored_follow_up: Mapping[str, Any] | None,
    compiled_block: Mapping[str, Any],
) -> None:
    """Fail the launch when required follow-up retrieval cannot be made available.

    Follow-up retrieval is an authority boundary. When an operator explicitly
    enables it with ``required: true`` the advertised guarantee must hold: if the
    compiled capability is unavailable (for example an incomplete, unresolvable
    scope), the step must block instead of silently launching with retrieval
    disabled. Optional retrieval (``required`` unset/false) degrades quietly.
    """

    if not isinstance(authored_follow_up, Mapping):
        return
    if authored_follow_up.get("enabled") is not True:
        return
    if not bool(authored_follow_up.get("required")):
        return
    if compiled_block.get("enabled") is True:
        return
    reason = str(compiled_block.get("reason") or "follow_up_retrieval_unavailable")
    raise OmnigentOAuthHostError(
        f"required follow-up retrieval is unavailable: {reason}",
        code="OMNIGENT_REQUIRED_FOLLOW_UP_RETRIEVAL_UNAVAILABLE",
    )
