"""Shared cross-runtime managed-session conformance suite (MM-883).

This module defines a runtime-neutral conformance contract for MoonMind managed
sessions. It does two things:

1. Defines the bounded, runtime-neutral *capability metadata* that a managed
   session-capable runtime adapter exposes about itself
   (:class:`ManagedSessionRuntimeCapabilities`).
2. Evaluates that metadata against the canonical set of required managed-session
   behaviors (launch, turn control, interrupt, reset/epoch, resume, terminate,
   rate-limit, no-progress, checkpoint, outbound scan, and correlation) and
   produces a deterministic conformance report.

The determination is *truthful* and *binary*: a runtime is either session-capable
(every required behavior conforms) or it is not. There is intentionally no
"partially session-capable" verdict. Non-conforming runtimes are reported with
precise, actionable capability gaps so they cannot be surfaced as session-capable
by mistake.

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

ManagedSessionConformanceBehavior = Literal[
    "launch",
    "turn_control",
    "interrupt",
    "reset_epoch",
    "resume",
    "terminate",
    "rate_limit",
    "no_progress",
    "checkpoint",
    "outbound_scan",
    "correlation",
]

# The canonical, ordered set of behaviors a managed-session runtime must satisfy
# before it can be surfaced as session-capable. These map one-to-one to the
# MM-883 acceptance criteria.
REQUIRED_MANAGED_SESSION_BEHAVIORS: tuple[ManagedSessionConformanceBehavior, ...] = (
    "launch",
    "turn_control",
    "interrupt",
    "reset_epoch",
    "resume",
    "terminate",
    "rate_limit",
    "no_progress",
    "checkpoint",
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


class ManagedSessionBehaviorSupport(BaseModel):
    """Runtime-declared support for one required managed-session behavior."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    behavior: ManagedSessionConformanceBehavior = Field(..., alias="behavior")
    supported: bool = Field(..., alias="supported")
    invocation: str | None = Field(None, alias="invocation")
    evidence: tuple[str, ...] = Field(default=(), alias="evidence")
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
    behaviors: tuple[ManagedSessionBehaviorSupport, ...] = Field(
        ..., alias="behaviors"
    )

    @model_validator(mode="after")
    def _validate(self) -> "ManagedSessionRuntimeCapabilities":
        keys = [support.behavior for support in self.behaviors]
        if len(keys) != len(set(keys)):
            raise ValueError("behaviors must be unique per behavior key")
        return self

    def behavior(
        self, behavior: ManagedSessionConformanceBehavior
    ) -> ManagedSessionBehaviorSupport | None:
        for support in self.behaviors:
            if support.behavior == behavior:
                return support
        return None


def _gap(behavior: str, reason: str) -> dict[str, Any]:
    return {"behavior": behavior, "reason": reason}


def evaluate_managed_session_conformance(
    capabilities: ManagedSessionRuntimeCapabilities,
) -> dict[str, Any]:
    """Evaluate one runtime's capability metadata against the required behaviors.

    Returns a deterministic report. ``sessionCapable`` is a truthful binary
    determination: it is ``True`` only when every required behavior conforms.
    Any missing or unsupported behavior produces an actionable capability gap and
    forces ``sessionCapable`` to ``False`` -- there is no partial verdict.
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
                    "invocation": None,
                    "evidence": [],
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
                    "invocation": support.invocation,
                    "evidence": list(support.evidence),
                    "gapReason": reason,
                }
            )
            continue
        behavior_decisions.append(
            {
                "behavior": behavior,
                "decision": "conforms",
                "supported": True,
                "invocation": support.invocation,
                "evidence": list(support.evidence),
                "gapReason": None,
            }
        )

    canonical_runtime_id = canonical_managed_session_runtime_id(
        capabilities.runtime_id
    )
    # Defense in depth: a runtime with no canonical managed-session id must never
    # be determined session-capable, even if it declares every behavior.
    if canonical_runtime_id is None and not capability_gaps:
        reason = (
            f"runtime '{capabilities.runtime_id}' has no canonical managed-session "
            "runtime id and must not be surfaced as session-capable"
        )
        capability_gaps.append(_gap("runtime_identity", reason))

    session_capable = not capability_gaps
    claim_truthful = session_capable == capabilities.session_capable_claim

    return {
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


def _codex_behavior(
    behavior: ManagedSessionConformanceBehavior,
    invocation: str,
    evidence: tuple[str, ...],
) -> ManagedSessionBehaviorSupport:
    return ManagedSessionBehaviorSupport(
        behavior=behavior,
        supported=True,
        invocation=invocation,
        evidence=evidence,
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
                "checkpoint",
                "PublishCodexManagedSessionArtifactsRequest",
                (
                    "CodexManagedSessionSummary.latestCheckpointRef",
                    "latestResetBoundaryRef",
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
        "passed" if all(report["result"] == "passed" for report in reports) else "failed"
    )
    return {
        "suite": MANAGED_SESSION_CONFORMANCE_SUITE_ID,
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
