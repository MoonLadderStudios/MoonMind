"""Shared cross-runtime managed-session conformance suite (MM-883).

This module defines a runtime-neutral conformance contract for MoonMind managed
sessions. It does two things:

1. Defines the bounded, runtime-neutral *capability metadata* that a managed
   session-capable runtime adapter exposes about itself
   (:class:`ManagedSessionRuntimeCapabilities`).
2. Evaluates that metadata against the canonical set of required managed-session
   behaviors (launch, turn control, interrupt, reset/epoch, resume, terminate,
   rate-limit, no-progress, session-state checkpointing, step-workspace
   checkpoint capture/restore, outbound scan, and correlation) and
   produces a deterministic conformance report.

The session determination is *truthful* and *binary*: a runtime is either capable
of the required managed-session lifecycle or it is not. Step-workspace capture and
restore are reported independently because policy may treat those gaps as
recoverability-only. There is intentionally no "partially session-capable"
verdict. Non-conforming behaviors are reported with precise, actionable gaps.

Concrete adapters expose their descriptor at the adapter boundary (for example
``CodexSessionAdapter.managed_session_capabilities()``) so the suite runs against
the same metadata the runtime advertises. Boundary-level adapter tests assert that
the declared invocation shapes and trace/artifact correlation surfaces match the
runtime's real behavior.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.schemas._validation import NonBlankStr
from moonmind.schemas.managed_session_models import (
    MANAGED_SESSION_CONTROL_ACTIONS,
    ManagedSessionControlAction,
    canonical_managed_session_runtime_id,
)

MANAGED_SESSION_CONFORMANCE_SUITE_ID = "managed-session-conformance"
MANAGED_SESSION_CONFORMANCE_REPORT_VERSION = 2

ManagedSessionConformanceBehavior = Literal[
    "launch",
    "turn_control",
    "interrupt",
    "reset_epoch",
    "resume",
    "terminate",
    "rate_limit",
    "no_progress",
    "session_state_checkpoint",
    "step_workspace_checkpoint_capture",
    "step_workspace_checkpoint_restore",
    "outbound_scan",
    "correlation",
]

# The canonical, ordered set of behaviors every descriptor must report. Workspace
# capture and restore are execution-policy capabilities rather than prerequisites
# for preserving a managed session.
REQUIRED_MANAGED_SESSION_BEHAVIORS: tuple[ManagedSessionConformanceBehavior, ...] = (
    "launch",
    "turn_control",
    "interrupt",
    "reset_epoch",
    "resume",
    "terminate",
    "rate_limit",
    "no_progress",
    "session_state_checkpoint",
    "step_workspace_checkpoint_capture",
    "step_workspace_checkpoint_restore",
    "outbound_scan",
    "correlation",
)

# Coverage IDs carried through from the MM-883 design reference so downstream
# verification and the pull request can trace the requirements this suite covers.
MM883_COVERAGE_IDS: tuple[str, ...] = (
    "DESIGN-REQ-001",
    "DESIGN-REQ-002",
    "DESIGN-REQ-005",
    "DESIGN-REQ-006",
    "DESIGN-REQ-007",
    "DESIGN-REQ-011",
    "DESIGN-REQ-013",
    "DESIGN-REQ-014",
)

# Runtimes the suite evaluates by default. Only ``codex_cli`` currently has a
# concrete managed-session plane; the others must be reported as non-conforming
# with explicit capability gaps rather than session-capable.
KNOWN_MANAGED_SESSION_RUNTIMES: tuple[str, ...] = (
    "codex_cli",
    "claude",
    "claude_code",
)

ManagedSessionBehaviorDecision = Literal["conforms", "capability_gap"]
WorkspaceAuthority = Literal[
    "moonmind_sandbox", "managed_runtime", "external_provider", "none"
]

_CHECKPOINT_BEHAVIORS = frozenset(
    {
        "session_state_checkpoint",
        "step_workspace_checkpoint_capture",
        "step_workspace_checkpoint_restore",
    }
)

# Workspace capture and restore are execution-policy capabilities. They are
# deliberately not prerequisites for preserving a managed session/thread.
_SESSION_CAPABILITY_BEHAVIORS = tuple(
    behavior
    for behavior in REQUIRED_MANAGED_SESSION_BEHAVIORS
    if behavior
    not in {"step_workspace_checkpoint_capture", "step_workspace_checkpoint_restore"}
)


class ManagedSessionBehaviorSupport(BaseModel):
    """Runtime-declared support for one required managed-session behavior."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    behavior: ManagedSessionConformanceBehavior = Field(..., alias="behavior")
    supported: bool = Field(..., alias="supported")
    invocation: str | None = Field(None, alias="invocation")
    owner: str | None = Field(None, alias="owner")
    workspace_authorities: tuple[WorkspaceAuthority, ...] = Field(
        default=(), alias="workspaceAuthorities"
    )
    checkpoint_kinds: tuple[str, ...] = Field(default=(), alias="checkpointKinds")
    compatible_workspace_policies: tuple[str, ...] = Field(
        default=(), alias="compatibleWorkspacePolicies"
    )
    evidence: tuple[str, ...] = Field(default=(), alias="evidence")
    idempotency: str | None = Field(None, alias="idempotency")
    retry_replay: str | None = Field(None, alias="retryReplay")
    security_boundary: str | None = Field(None, alias="securityBoundary")
    boundary_test: str | None = Field(None, alias="boundaryTest")
    gap_reason: str | None = Field(None, alias="gapReason")

    @model_validator(mode="after")
    def _validate(self) -> "ManagedSessionBehaviorSupport":
        if self.supported:
            if not (self.invocation or "").strip():
                raise ValueError(
                    f"behavior '{self.behavior}' is supported but declares no "
                    "invocation contract"
                )
            if not self.evidence:
                raise ValueError(
                    f"behavior '{self.behavior}' is supported but declares no "
                    "evidence surfaces"
                )
            if self.gap_reason is not None:
                raise ValueError(
                    f"behavior '{self.behavior}' is supported and must not carry "
                    "a gapReason"
                )
            if self.behavior in _CHECKPOINT_BEHAVIORS:
                required = {
                    "owner": self.owner,
                    "workspaceAuthorities": self.workspace_authorities,
                    "checkpointKinds": self.checkpoint_kinds,
                    "idempotency": self.idempotency,
                    "retryReplay": self.retry_replay,
                    "securityBoundary": self.security_boundary,
                    "boundaryTest": self.boundary_test,
                }
                missing = [
                    name
                    for name, value in required.items()
                    if not value or (isinstance(value, str) and not value.strip())
                ]
                if missing:
                    raise ValueError(
                        f"checkpoint behavior '{self.behavior}' is supported but "
                        f"is missing required conformance fields: {', '.join(missing)}"
                    )
                if any(not kind.strip() for kind in self.checkpoint_kinds):
                    raise ValueError(
                        f"checkpoint behavior '{self.behavior}' is supported but "
                        "declares a blank checkpointKind"
                    )
                if (
                    self.behavior == "step_workspace_checkpoint_restore"
                    and not self.compatible_workspace_policies
                ):
                    raise ValueError(
                        "step workspace checkpoint restore support must declare "
                        "compatibleWorkspacePolicies"
                    )
        else:
            if not (self.gap_reason or "").strip():
                raise ValueError(
                    f"behavior '{self.behavior}' is unsupported and must declare "
                    "an actionable gapReason"
                )
        return self


class ManagedSessionRuntimeCapabilities(BaseModel):
    """Bounded, runtime-neutral metadata a managed-session adapter exposes."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    runtime_id: NonBlankStr = Field(..., alias="runtimeId")
    runtime_family: str | None = Field(None, alias="runtimeFamily")
    session_capable_claim: bool = Field(..., alias="sessionCapableClaim")
    control_actions: tuple[ManagedSessionControlAction, ...] = Field(
        default=(), alias="controlActions"
    )
    behaviors: tuple[ManagedSessionBehaviorSupport, ...] = Field(..., alias="behaviors")

    @model_validator(mode="after")
    def _validate(self) -> "ManagedSessionRuntimeCapabilities":
        keys = [support.behavior for support in self.behaviors]
        if len(keys) != len(set(keys)):
            raise ValueError("behaviors must be unique per behavior key")
        capture = self.behavior("step_workspace_checkpoint_capture")
        restore = self.behavior("step_workspace_checkpoint_restore")
        if capture and restore and capture.supported and restore.supported:
            if capture.invocation == restore.invocation:
                raise ValueError(
                    "workspace capture and restore must declare distinct compatible "
                    "invocations"
                )
            if set(capture.evidence) == set(restore.evidence):
                raise ValueError(
                    "workspace capture and restore cannot use identical evidence "
                    "surfaces"
                )
        return self

    def behavior(
        self, behavior: ManagedSessionConformanceBehavior
    ) -> ManagedSessionBehaviorSupport | None:
        for support in self.behaviors:
            if support.behavior == behavior:
                return support
        return None


class RuntimeExecutionCapabilities(BaseModel):
    """Compact execution-policy projection of managed-session conformance.

    This is the checkpoint-focused integration boundary for the general runtime
    descriptor tracked by #3148. It intentionally exposes separate session state,
    workspace capture, and workspace restore decisions and has no generic
    ``checkpointSupported`` field.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    capability_set_version: Literal["managed-session-checkpoints-v2"] = Field(
        "managed-session-checkpoints-v2", alias="capabilitySetVersion"
    )
    runtime_id: NonBlankStr = Field(..., alias="runtimeId")
    runtime_family: str | None = Field(None, alias="runtimeFamily")
    workspace_authority: WorkspaceAuthority = Field(..., alias="workspaceAuthority")
    session_state_checkpoint: ManagedSessionBehaviorDecision = Field(
        ..., alias="sessionStateCheckpoint"
    )
    step_workspace_checkpoint_capture: ManagedSessionBehaviorDecision = Field(
        ..., alias="stepWorkspaceCheckpointCapture"
    )
    step_workspace_checkpoint_restore: ManagedSessionBehaviorDecision = Field(
        ..., alias="stepWorkspaceCheckpointRestore"
    )
    checkpoint_capture_kinds: tuple[str, ...] = Field(
        default=(), alias="checkpointCaptureKinds"
    )
    checkpoint_restore_kinds: tuple[str, ...] = Field(
        default=(), alias="checkpointRestoreKinds"
    )
    checkpoint_capture_activity: str | None = Field(
        None, alias="checkpointCaptureActivity"
    )
    checkpoint_restore_activity: str | None = Field(
        None, alias="checkpointRestoreActivity"
    )
    supports_same_session_continuation: bool = Field(
        ..., alias="supportsSameSessionContinuation"
    )
    supports_active_command_introspection: bool = Field(
        False, alias="supportsActiveCommandIntrospection"
    )
    terminal_contract_ids: tuple[str, ...] = Field(
        default=(), alias="terminalContractIds"
    )
    post_execution_checkpoint_criticality: Literal[
        "required", "recoverability_only", "unsupported"
    ] = Field(..., alias="postExecutionCheckpointCriticality")

    @model_validator(mode="after")
    def _validate_checkpoint_claims(self) -> "RuntimeExecutionCapabilities":
        capture_conforms = self.step_workspace_checkpoint_capture == "conforms"
        restore_conforms = self.step_workspace_checkpoint_restore == "conforms"
        if capture_conforms != bool(
            self.checkpoint_capture_kinds and self.checkpoint_capture_activity
        ):
            raise ValueError(
                "workspace capture conformance must match declared capture kinds "
                "and activity owner"
            )
        if restore_conforms != bool(
            self.checkpoint_restore_kinds and self.checkpoint_restore_activity
        ):
            raise ValueError(
                "workspace restore conformance must match declared restore kinds "
                "and activity owner"
            )
        return self


def _gap(behavior: str, reason: str) -> dict[str, Any]:
    return {"behavior": behavior, "reason": reason}


def _decision_details(
    support: ManagedSessionBehaviorSupport | None,
) -> dict[str, Any]:
    """Serialize bounded invocation and evidence metadata for one behavior."""

    return {
        "invocation": support.invocation if support else None,
        "owner": support.owner if support else None,
        "workspaceAuthorities": list(support.workspace_authorities) if support else [],
        "checkpointKinds": list(support.checkpoint_kinds) if support else [],
        "compatibleWorkspacePolicies": (
            list(support.compatible_workspace_policies) if support else []
        ),
        "evidence": list(support.evidence) if support else [],
        "idempotency": support.idempotency if support else None,
        "retryReplay": support.retry_replay if support else None,
        "securityBoundary": support.security_boundary if support else None,
        "boundaryTest": support.boundary_test if support else None,
    }


def evaluate_managed_session_conformance(
    capabilities: ManagedSessionRuntimeCapabilities,
) -> dict[str, Any]:
    """Evaluate one runtime's capability metadata against the required behaviors.

    Returns a deterministic report. ``sessionCapable`` is a truthful binary
    determination over the managed-session lifecycle behaviors. Workspace capture
    and restore decisions remain independent capability gaps so execution policy
    can treat them as required, recoverability-only, or unsupported.
    """

    behavior_decisions: list[dict[str, Any]] = []
    capability_gaps: list[dict[str, Any]] = []

    for behavior in REQUIRED_MANAGED_SESSION_BEHAVIORS:
        support = capabilities.behavior(behavior)
        if support is None:
            reason = (
                f"runtime '{capabilities.runtime_id}' does not declare the "
                f"'{behavior}' managed-session behavior"
            )
            capability_gaps.append(_gap(behavior, reason))
            behavior_decisions.append(
                {
                    "behavior": behavior,
                    "decision": "capability_gap",
                    "supported": False,
                    **_decision_details(None),
                    "gapReason": reason,
                }
            )
            continue
        if not support.supported:
            reason = support.gap_reason or (
                f"runtime '{capabilities.runtime_id}' does not support the "
                f"'{behavior}' managed-session behavior"
            )
            capability_gaps.append(_gap(behavior, reason))
            behavior_decisions.append(
                {
                    "behavior": behavior,
                    "decision": "capability_gap",
                    "supported": False,
                    **_decision_details(support),
                    "gapReason": reason,
                }
            )
            continue
        behavior_decisions.append(
            {
                "behavior": behavior,
                "decision": "conforms",
                "supported": True,
                **_decision_details(support),
                "gapReason": None,
            }
        )

    canonical_runtime_id = canonical_managed_session_runtime_id(capabilities.runtime_id)
    # Defense in depth: a runtime with no canonical managed-session id must never
    # be determined session-capable, even if it declares every behavior.
    if canonical_runtime_id is None:
        reason = (
            f"runtime '{capabilities.runtime_id}' has no canonical managed-session "
            "runtime id and must not be surfaced as session-capable"
        )
        capability_gaps.append(_gap("runtime_identity", reason))

    gap_behaviors = {gap["behavior"] for gap in capability_gaps}
    session_capable = (
        not any(behavior in gap_behaviors for behavior in _SESSION_CAPABILITY_BEHAVIORS)
        and canonical_runtime_id is not None
    )
    claim_truthful = session_capable == capabilities.session_capable_claim

    return {
        "reportSchemaVersion": MANAGED_SESSION_CONFORMANCE_REPORT_VERSION,
        "runtimeId": capabilities.runtime_id,
        "runtimeFamily": capabilities.runtime_family,
        "canonicalRuntimeId": canonical_runtime_id,
        "sessionCapableClaim": capabilities.session_capable_claim,
        "sessionCapable": session_capable,
        "claimTruthful": claim_truthful,
        "result": "passed" if claim_truthful else "failed",
        "controlActions": list(capabilities.control_actions),
        "behaviorDecisions": behavior_decisions,
        "capabilityGaps": capability_gaps,
    }


def migrate_managed_session_conformance_report(
    report: dict[str, Any],
) -> dict[str, Any]:
    """Upgrade a serialized v1 report to the precise v2 checkpoint model.

    V1's generic ``checkpoint`` decision proves only session-state reference
    publication. It is therefore migrated to ``session_state_checkpoint``.
    Workspace capture and restore are added as explicit capability gaps; the
    migration never manufactures those stronger claims from legacy evidence.
    """

    version = report.get("reportSchemaVersion", 1)
    if version == MANAGED_SESSION_CONFORMANCE_REPORT_VERSION:
        return dict(report)
    if version != 1:
        raise ValueError(
            f"unsupported managed-session report schema version: {version}"
        )

    if isinstance(report.get("reports"), list):
        migrated_summary = dict(report)
        migrated_summary["reportSchemaVersion"] = (
            MANAGED_SESSION_CONFORMANCE_REPORT_VERSION
        )
        migrated_summary["reports"] = [
            migrate_managed_session_conformance_report(dict(child))
            for child in report["reports"]
        ]
        required = report.get("requiredBehaviors")
        if isinstance(required, list):
            migrated_required: list[str] = []
            for behavior in required:
                if behavior == "checkpoint":
                    migrated_required.extend(
                        (
                            "session_state_checkpoint",
                            "step_workspace_checkpoint_capture",
                            "step_workspace_checkpoint_restore",
                        )
                    )
                else:
                    migrated_required.append(behavior)
            migrated_summary["requiredBehaviors"] = migrated_required
        return migrated_summary

    migrated = dict(report)

    compact_gaps = report.get("capabilityGaps")
    if isinstance(compact_gaps, dict):
        migrated_compact_gaps: dict[str, list[dict[str, Any]]] = {}
        for runtime_id, raw_gaps in compact_gaps.items():
            gaps = []
            for raw_gap in raw_gaps:
                gap = dict(raw_gap)
                if gap.get("behavior") == "checkpoint":
                    gap["behavior"] = "session_state_checkpoint"
                gaps.append(gap)
            existing = {gap.get("behavior") for gap in gaps}
            for behavior in (
                "step_workspace_checkpoint_capture",
                "step_workspace_checkpoint_restore",
            ):
                if behavior not in existing:
                    gaps.append(
                        _gap(
                            behavior,
                            f"legacy v1 report for runtime '{runtime_id}' did not "
                            f"distinguish or prove {behavior}",
                        )
                    )
            migrated_compact_gaps[runtime_id] = gaps
        migrated["reportSchemaVersion"] = MANAGED_SESSION_CONFORMANCE_REPORT_VERSION
        migrated["capabilityGaps"] = migrated_compact_gaps
        return migrated

    decisions: list[dict[str, Any]] = []
    for raw in report.get("behaviorDecisions", []):
        decision = dict(raw)
        if decision.get("behavior") == "checkpoint":
            decision["behavior"] = "session_state_checkpoint"
        for key, default in _decision_details(None).items():
            decision.setdefault(key, default)
        decisions.append(decision)

    existing = {item.get("behavior") for item in decisions}
    gaps = []
    for raw_gap in report.get("capabilityGaps", []):
        gap = dict(raw_gap)
        if gap.get("behavior") == "checkpoint":
            gap["behavior"] = "session_state_checkpoint"
        gaps.append(gap)
    for behavior in (
        "step_workspace_checkpoint_capture",
        "step_workspace_checkpoint_restore",
    ):
        if behavior in existing:
            continue
        reason = (
            f"legacy v1 report for runtime '{report.get('runtimeId', 'unknown')}' "
            f"did not distinguish or prove {behavior}"
        )
        decisions.append(
            {
                "behavior": behavior,
                "decision": "capability_gap",
                "supported": False,
                **_decision_details(None),
                "gapReason": reason,
            }
        )
        gaps.append(_gap(behavior, reason))

    migrated["reportSchemaVersion"] = MANAGED_SESSION_CONFORMANCE_REPORT_VERSION
    migrated["behaviorDecisions"] = decisions
    migrated["capabilityGaps"] = gaps
    return migrated


def _codex_behavior(
    behavior: ManagedSessionConformanceBehavior,
    invocation: str,
    evidence: tuple[str, ...],
    **checkpoint_contract: Any,
) -> ManagedSessionBehaviorSupport:
    return ManagedSessionBehaviorSupport(
        behavior=behavior,
        supported=True,
        invocation=invocation,
        evidence=evidence,
        **checkpoint_contract,
    )


def codex_managed_session_capabilities() -> ManagedSessionRuntimeCapabilities:
    """Canonical capability descriptor for the Codex CLI managed-session plane.

    Each behavior records the bounded invocation contract used at the adapter
    boundary plus the trace/artifact correlation surfaces that prove the behavior.
    Boundary-level adapter tests assert these claims against the real adapter.
    """

    return ManagedSessionRuntimeCapabilities(
        runtimeId="codex_cli",
        runtimeFamily="codex",
        sessionCapableClaim=True,
        controlActions=MANAGED_SESSION_CONTROL_ACTIONS,
        behaviors=(
            _codex_behavior(
                "launch",
                "LaunchCodexManagedSessionRequest",
                (
                    "CodexSessionAdapter._ensure_remote_session",
                    "start_session control action",
                ),
            ),
            _codex_behavior(
                "turn_control",
                "SendCodexManagedSessionTurnRequest",
                (
                    "send_turn control action",
                    "SteerCodexManagedSessionTurnRequest",
                ),
            ),
            _codex_behavior(
                "interrupt",
                "InterruptCodexManagedSessionTurnRequest",
                (
                    "CodexSessionAdapter.interrupt_turn",
                    "CodexSessionAdapter.cancel",
                    "interrupt_turn control action",
                ),
            ),
            _codex_behavior(
                "reset_epoch",
                "CodexManagedSessionClearRequest",
                (
                    "CodexSessionAdapter.clear_session",
                    "CodexManagedSessionState.clear_session",
                    "clear_session control action",
                    "sessionEpoch increment",
                ),
            ),
            _codex_behavior(
                "resume",
                "CodexManagedSessionLocator",
                (
                    "CodexSessionAdapter._ensure_remote_session resume path",
                    "resume_session control action",
                ),
            ),
            _codex_behavior(
                "terminate",
                "TerminateCodexManagedSessionRequest",
                (
                    "CodexSessionAdapter.terminate_session",
                    "terminate_session control action",
                ),
            ),
            _codex_behavior(
                "rate_limit",
                "classify_provider_failure",
                (
                    "provider_error_code=429",
                    "retry_after_cooldown recommendation",
                    "ManagedAgentAdapter cooldown_reporter",
                ),
            ),
            _codex_behavior(
                "no_progress",
                "TemporalTimeoutError -> turn_status=timed_out",
                (
                    "turnCompletionTimeoutSeconds budget",
                    "operator-actionable timeout summary",
                ),
            ),
            _codex_behavior(
                "session_state_checkpoint",
                "PublishCodexManagedSessionArtifactsRequest",
                (
                    "CodexManagedSessionSummary.latestCheckpointRef",
                    "latestResetBoundaryRef",
                ),
                owner="CodexSessionAdapter.fetch_result",
                workspaceAuthorities=("managed_runtime",),
                checkpointKinds=(
                    "session_state_ref",
                    "session_reset_boundary_ref",
                ),
                idempotency=(
                    "publication reuses the managed session identity and artifact refs"
                ),
                retryReplay=(
                    "adapter fetch/publish retries preserve sessionId, threadId, and "
                    "sessionEpoch"
                ),
                securityBoundary=(
                    "Codex managed-session controller resolves state inside the "
                    "runtime-owned session container"
                ),
                boundaryTest=(
                    "tests/unit/workflows/adapters/"
                    "test_managed_session_conformance_boundary.py::"
                    "test_session_state_checkpoint_boundary_invocation_and_evidence"
                ),
            ),
            ManagedSessionBehaviorSupport(
                behavior="step_workspace_checkpoint_capture",
                supported=False,
                gapReason=(
                    "Codex managed sessions can publish session-state refs but do "
                    "not yet declare a managed-runtime-owned Step Execution "
                    "workspace capture invocation for git_patch or worktree_archive"
                ),
            ),
            ManagedSessionBehaviorSupport(
                behavior="step_workspace_checkpoint_restore",
                supported=False,
                gapReason=(
                    "Codex managed sessions do not yet declare a workspace "
                    "restore/materialization invocation or compatible workspace "
                    "policies"
                ),
            ),
            _codex_behavior(
                "outbound_scan",
                "scan_outbound_text",
                (
                    "OutboundScanDecision.BLOCK",
                    "high_security_mode",
                    "redact_sensitive_text",
                ),
            ),
            _codex_behavior(
                "correlation",
                "AgentExecutionRequest.correlationId",
                (
                    "observabilityEventsRef",
                    "CodexManagedSessionLocator sessionId/containerId/threadId",
                    "AgentRunHandle.metadata.sessionId",
                ),
            ),
        ),
    )


def unsupported_runtime_managed_session_capabilities(
    runtime_id: str,
) -> ManagedSessionRuntimeCapabilities:
    """Descriptor for a runtime with no managed-session plane.

    Every required behavior is reported as an explicit, actionable capability gap
    so the runtime cannot be surfaced as session-capable.
    """

    base_reason = (
        f"runtime '{runtime_id}' has no managed-session plane; "
        "canonical_managed_session_runtime_id returns None until it implements "
        "its own runtime-specific session plane"
    )
    return ManagedSessionRuntimeCapabilities(
        runtimeId=runtime_id,
        runtimeFamily=None,
        sessionCapableClaim=False,
        controlActions=(),
        behaviors=tuple(
            ManagedSessionBehaviorSupport(
                behavior=behavior,
                supported=False,
                gapReason=f"{behavior}: {base_reason}",
            )
            for behavior in REQUIRED_MANAGED_SESSION_BEHAVIORS
        ),
    )


def managed_session_capabilities_for_runtime(
    runtime_id: str,
) -> ManagedSessionRuntimeCapabilities:
    """Return the capability descriptor the suite uses for ``runtime_id``."""

    if canonical_managed_session_runtime_id(runtime_id) == "codex_cli":
        return codex_managed_session_capabilities()
    return unsupported_runtime_managed_session_capabilities(runtime_id)


def runtime_execution_capabilities_for_runtime(
    runtime_id: str,
) -> RuntimeExecutionCapabilities:
    """Project precise conformance decisions into execution checkpoint policy."""

    managed = managed_session_capabilities_for_runtime(runtime_id)
    report = evaluate_managed_session_conformance(managed)
    decisions = {
        decision["behavior"]: decision for decision in report["behaviorDecisions"]
    }
    capture = decisions["step_workspace_checkpoint_capture"]
    restore = decisions["step_workspace_checkpoint_restore"]
    session_state = decisions["session_state_checkpoint"]
    resume = decisions["resume"]
    canonical_id = report["canonicalRuntimeId"]

    return RuntimeExecutionCapabilities(
        runtimeId=managed.runtime_id,
        runtimeFamily=managed.runtime_family,
        workspaceAuthority="managed_runtime" if canonical_id else "none",
        sessionStateCheckpoint=session_state["decision"],
        stepWorkspaceCheckpointCapture=capture["decision"],
        stepWorkspaceCheckpointRestore=restore["decision"],
        checkpointCaptureKinds=tuple(capture["checkpointKinds"]),
        checkpointRestoreKinds=tuple(restore["checkpointKinds"]),
        checkpointCaptureActivity=capture["owner"],
        checkpointRestoreActivity=restore["owner"],
        supportsSameSessionContinuation=(
            session_state["decision"] == "conforms" and resume["decision"] == "conforms"
        ),
        terminalContractIds=(
            ("codex_managed_session_summary_v1",) if canonical_id else ()
        ),
        postExecutionCheckpointCriticality=(
            "recoverability_only" if canonical_id else "unsupported"
        ),
    )


def build_managed_session_conformance_summary(
    *,
    capabilities: Iterable[ManagedSessionRuntimeCapabilities] | None = None,
) -> dict[str, Any]:
    """Evaluate every known managed-session runtime and aggregate the result."""

    descriptors = (
        list(capabilities)
        if capabilities is not None
        else [
            managed_session_capabilities_for_runtime(runtime_id)
            for runtime_id in KNOWN_MANAGED_SESSION_RUNTIMES
        ]
    )
    reports = [
        evaluate_managed_session_conformance(descriptor) for descriptor in descriptors
    ]
    overall_result = (
        "passed"
        if all(report["result"] == "passed" for report in reports)
        else "failed"
    )
    return {
        "suite": MANAGED_SESSION_CONFORMANCE_SUITE_ID,
        "reportSchemaVersion": MANAGED_SESSION_CONFORMANCE_REPORT_VERSION,
        "overallResult": overall_result,
        "requiredBehaviors": list(REQUIRED_MANAGED_SESSION_BEHAVIORS),
        "coverageIds": list(MM883_COVERAGE_IDS),
        "reports": reports,
        "sessionCapableRuntimes": [
            report["runtimeId"] for report in reports if report["sessionCapable"]
        ],
        "failedRuntimes": [
            report["runtimeId"] for report in reports if report["result"] != "passed"
        ],
    }


def run_managed_session_conformance() -> dict[str, Any]:
    """Run the suite and return a compact pass/fail result."""

    summary = build_managed_session_conformance_summary()
    return {
        "suite": summary["suite"],
        "reportSchemaVersion": summary["reportSchemaVersion"],
        "overallResult": summary["overallResult"],
        "sessionCapableRuntimes": summary["sessionCapableRuntimes"],
        "failedRuntimes": summary["failedRuntimes"],
        "capabilityGaps": {
            report["runtimeId"]: report["capabilityGaps"]
            for report in summary["reports"]
            if report["capabilityGaps"]
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="emit the full conformance summary instead of the compact result",
    )
    args = parser.parse_args(argv)

    if args.full:
        result = build_managed_session_conformance_summary()
    else:
        result = run_managed_session_conformance()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["overallResult"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
