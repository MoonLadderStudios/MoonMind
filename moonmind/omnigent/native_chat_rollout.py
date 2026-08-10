"""Rollout / canary / rollback gate for native Omnigent Workflow Chat.

MoonLadderStudios/MoonMind#3642 §10. The native Workflow Chat feature (the
binding, the scoped HTTP/SSE facade, the served native UI, the outbound scan,
the read-only diagnostics fallback) is implemented by the dependency issues
#3633-#3641. This module is the *rollout control* that decides, per deployment,
whether interactive native Chat is served, gated behind a canary that requires
recorded acceptance evidence, rolled back to read-only diagnostics, or fully
disabled.

The controlling gate for whether interactive native Chat is *safe to make
primary* is the machine-readable acceptance report
(:mod:`moonmind.omnigent.native_chat_acceptance`). The canary mode consumes that
proof: it admits interactive Chat only when the deployment has recorded a
passing acceptance report ref, and otherwise degrades to the read-only
diagnostics projection rather than silently routing messages through a different
runtime or the legacy ``/chat-instructions`` path.

Design decisions:

* A rollback never silently substitutes a different runtime or the deferred
  ``SubmitChatInstruction`` path. It either presents the durable read-only
  diagnostics projection (``read_only``) or disables interactive Chat entirely
  (``disabled``). Historical diagnostic reads are preserved in both.
* The rollout flag is *temporary*. Once the deterministic and protected-live
  acceptance evidence passes and the fallback window completes, the flag is
  retired (removed) and the canonical path serves interactive Chat
  unconditionally. :func:`rollout_flag_retirement` records that contract so the
  temporary flag is not mistaken for durable configuration.
* An unrecognized/garbage mode fails closed to the safest posture that still
  preserves diagnostics (``read_only``), never to interactive.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# The temporary environment flag that selects the rollout posture. Documented as
# temporary so it is retired after the canonical path is proven (see
# ``rollout_flag_retirement``); it is not durable runtime configuration.
NATIVE_CHAT_ROLLOUT_FLAG = "OMNIGENT_NATIVE_CHAT_ROLLOUT"


class NativeChatRolloutMode(StrEnum):
    """Deployment rollout posture for interactive native Workflow Chat."""

    # Interactive native Chat is disabled; native Chat surfaces are unavailable.
    DISABLED = "disabled"
    # Rolled back: no interactive native UI is served; the durable read-only
    # diagnostics projection is presented and historical reads are preserved.
    READ_ONLY = "read_only"
    # Gated: interactive native Chat is admitted only once the deployment has
    # recorded a passing acceptance report; otherwise it degrades to read-only.
    CANARY = "canary"
    # Fully rolled out: interactive native Chat is served (post-proof steady
    # state / the behavior once the temporary flag is retired).
    ENABLED = "enabled"


# Stable, redacted reason codes attached to a decision (safe to surface).
REASON_ENABLED = "enabled"
REASON_CANARY_ADMITTED = "canary_admitted"
REASON_CANARY_AWAITING_EVIDENCE = "canary_awaiting_acceptance_evidence"
REASON_ROLLED_BACK_READ_ONLY = "rolled_back_read_only"
REASON_DISABLED = "native_chat_disabled"
REASON_UNKNOWN_MODE_FAILED_CLOSED = "unknown_rollout_mode_failed_closed"


def parse_rollout_mode(value: str | None) -> NativeChatRolloutMode:
    """Map a raw flag value to a mode, failing closed on an unknown value.

    An unset value selects :data:`NativeChatRolloutMode.CANARY`: interactive
    Chat remains fail-closed until current deterministic and protected-live
    acceptance evidence is recorded. An explicitly set but unrecognized value
    degrades to ``read_only`` rather than crashing serving or silently enabling
    interactive Chat.
    """

    raw = str(value or "").strip().lower()
    if not raw:
        return NativeChatRolloutMode.CANARY
    try:
        return NativeChatRolloutMode(raw)
    except ValueError:
        return NativeChatRolloutMode.READ_ONLY


@dataclass(frozen=True, slots=True)
class NativeChatRolloutDecision:
    """Resolved rollout decision for one deployment posture.

    ``interactive`` and ``serve_native_ui`` are always equal today (interactive
    Chat is the native UI), but they are kept distinct so a future presentation
    change cannot conflate "serve the native application" with "grant interactive
    authority". ``read_only_fallback`` selects the durable read-only diagnostics
    projection; ``interactive`` and ``read_only_fallback`` are mutually
    exclusive.
    """

    mode: NativeChatRolloutMode
    interactive: bool
    serve_native_ui: bool
    read_only_fallback: bool
    reason: str

    def telemetry_readiness(self) -> str:
        """Bounded readiness value for the rollout telemetry gauge."""

        if self.interactive:
            return "ready"
        if self.read_only_fallback:
            return "degraded"
        return "unavailable"


def resolve_native_chat_rollout(
    *,
    mode: NativeChatRolloutMode | str,
    acceptance_recorded: bool,
) -> NativeChatRolloutDecision:
    """Resolve whether interactive native Chat is served for this deployment.

    ``acceptance_recorded`` is whether the deployment has recorded a passing
    native-chat acceptance report ref (the controlling gate). It only matters in
    ``canary`` mode: ``enabled`` trusts the operator's post-proof steady state,
    ``read_only``/``disabled`` never serve interactive regardless, and ``canary``
    admits interactive Chat only when the proof is present.
    """

    resolved = mode if isinstance(mode, NativeChatRolloutMode) else parse_rollout_mode(mode)

    if resolved is NativeChatRolloutMode.DISABLED:
        return NativeChatRolloutDecision(
            mode=resolved,
            interactive=False,
            serve_native_ui=False,
            read_only_fallback=False,
            reason=REASON_DISABLED,
        )
    if resolved is NativeChatRolloutMode.READ_ONLY:
        return NativeChatRolloutDecision(
            mode=resolved,
            interactive=False,
            serve_native_ui=False,
            read_only_fallback=True,
            reason=REASON_ROLLED_BACK_READ_ONLY,
        )
    if resolved is NativeChatRolloutMode.CANARY:
        if acceptance_recorded:
            return NativeChatRolloutDecision(
                mode=resolved,
                interactive=True,
                serve_native_ui=True,
                read_only_fallback=False,
                reason=REASON_CANARY_ADMITTED,
            )
        return NativeChatRolloutDecision(
            mode=resolved,
            interactive=False,
            serve_native_ui=False,
            read_only_fallback=True,
            reason=REASON_CANARY_AWAITING_EVIDENCE,
        )
    # ENABLED (and the fail-closed default for any mode not handled above).
    return NativeChatRolloutDecision(
        mode=NativeChatRolloutMode.ENABLED,
        interactive=True,
        serve_native_ui=True,
        read_only_fallback=False,
        reason=REASON_ENABLED,
    )


@dataclass(frozen=True, slots=True)
class RolloutFlagRetirement:
    """The retirement contract for the temporary rollout flag."""

    flag: str
    temporary: bool
    retire_when: str
    steady_state_mode: NativeChatRolloutMode


def rollout_flag_retirement() -> RolloutFlagRetirement:
    """Return the temporary-flag retirement contract (brief §10).

    The rollout flag exists only to gate and canary the cutover. Once the
    deterministic and protected-live acceptance evidence passes and the
    read-only fallback window completes, the flag is removed and native Chat is
    served unconditionally (``ENABLED``). This is recorded here so the temporary
    flag is not mistaken for durable configuration and downstream verification
    can assert the retirement condition.
    """

    return RolloutFlagRetirement(
        flag=NATIVE_CHAT_ROLLOUT_FLAG,
        temporary=True,
        retire_when=(
            "deterministic + protected-live native-chat acceptance evidence "
            "passes and the read-only fallback window completes"
        ),
        steady_state_mode=NativeChatRolloutMode.ENABLED,
    )


__all__ = [
    "NATIVE_CHAT_ROLLOUT_FLAG",
    "NativeChatRolloutDecision",
    "NativeChatRolloutMode",
    "REASON_CANARY_ADMITTED",
    "REASON_CANARY_AWAITING_EVIDENCE",
    "REASON_DISABLED",
    "REASON_ENABLED",
    "REASON_ROLLED_BACK_READ_ONLY",
    "REASON_UNKNOWN_MODE_FAILED_CLOSED",
    "RolloutFlagRetirement",
    "parse_rollout_mode",
    "resolve_native_chat_rollout",
    "rollout_flag_retirement",
]
