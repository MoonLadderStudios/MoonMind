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
  retired (removed). The canonical path retains the default evidence-gated
  canary posture, so flag retirement cannot become a permanent fail-open.
  :func:`rollout_flag_retirement` records that contract.
* An unrecognized/garbage mode fails closed to the safest posture that still
  preserves diagnostics (``read_only``), never to interactive.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

from moonmind.omnigent.native_chat_acceptance import (
    REQUIRED_SCENARIOS,
    SCENARIO_LANES,
    SCHEMA_VERSION,
    TRUSTED_REPORT_PRODUCER,
    build_native_chat_acceptance_report,
)

# The temporary environment flag that selects the rollout posture. Documented as
# temporary so it is retired after the canonical path is proven (see
# ``rollout_flag_retirement``); it is not durable runtime configuration.
NATIVE_CHAT_ROLLOUT_FLAG = "OMNIGENT_NATIVE_CHAT_ROLLOUT"
_PINNED_IMAGE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


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
REASON_ACCEPTANCE_INVALID = "acceptance_evidence_invalid_or_stale"
REASON_ROLLED_BACK_READ_ONLY = "rolled_back_read_only"
REASON_DISABLED = "native_chat_disabled"
REASON_UNKNOWN_MODE_FAILED_CLOSED = "unknown_rollout_mode_failed_closed"


def parse_rollout_mode(value: str | None) -> NativeChatRolloutMode:
    """Map a raw flag value to a mode, failing closed on an unknown value.

    An unset value selects :data:`NativeChatRolloutMode.CANARY`: before the
    controlling proof is materialized, the canonical deployment preserves
    diagnostics but does not grant interactive authority. An explicitly set but
    unrecognized value degrades to
    ``read_only`` rather than crashing serving or silently enabling interactive
    Chat.
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


@dataclass(frozen=True, slots=True)
class ValidatedNativeChatAcceptance:
    """A resolved report bound to the bytes and current deployment identity."""

    ref: str
    sha256: str
    report: Mapping[str, Any]


def native_chat_deployment_identity(
    *, env: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    """Return the current identity the acceptance report must exactly cover.

    Missing or mutable identity is intentionally represented as ``None``: a
    deployment that cannot name its candidate commit and digest-pinned stock
    images cannot safely admit interactive Chat.
    """

    from moonmind.omnigent.native_outbound_scan import (
        NATIVE_OUTBOUND_SCAN_CONTRACT_VERSION,
    )
    from moonmind.omnigent.native_ui import (
        NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION,
        NATIVE_UI_ROUTE_FEATURE_VERSION,
    )
    from moonmind.omnigent.native_ui_compat import compatibility_map
    from moonmind.omnigent.native_chat_telemetry import NATIVE_CHAT_TELEMETRY_VERSION

    source = env if env is not None else os.environ
    commit = str(source.get("MOONMIND_BUILD_COMMIT") or "").strip()
    server = str(source.get("OMNIGENT_IMAGE_REF") or "").strip()
    ui = str(source.get("OMNIGENT_NATIVE_UI_IMAGE_REF") or server).strip()
    host = str(source.get("OMNIGENT_HOST_IMAGE_REF") or "").strip()
    if not commit or any(
        _PINNED_IMAGE.fullmatch(value) is None for value in (server, ui, host)
    ):
        return None
    manifest_bytes = json.dumps(
        compatibility_map(), sort_keys=True, separators=(",", ":")
    ).encode()
    return {
        "moonmindCommit": commit,
        "contractVersions": {
            "nativeUiBootstrap": NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION,
            "nativeUiRouteFeature": NATIVE_UI_ROUTE_FEATURE_VERSION,
            "outboundScan": NATIVE_OUTBOUND_SCAN_CONTRACT_VERSION,
            "telemetry": NATIVE_CHAT_TELEMETRY_VERSION,
        },
        "images": {"server": server, "ui": ui, "host": host},
        "compatibilityManifestDigest": (
            "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
        ),
    }


def validate_native_chat_acceptance_report(
    report: Mapping[str, Any],
    *,
    ref: str,
    sha256: str,
    deployed_identity: Mapping[str, Any],
    now: datetime | None = None,
) -> ValidatedNativeChatAcceptance:
    """Validate a published report again at the runtime authority boundary."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("rollout validation time must include a timezone")
    if (
        report.get("schemaVersion") != SCHEMA_VERSION
        or report.get("status") != "passed"
        or report.get("producer") != TRUSTED_REPORT_PRODUCER
    ):
        raise ValueError("acceptance report is not a trusted passing report")
    try:
        expires = datetime.fromisoformat(
            str(report["expiresAt"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("acceptance report expiry is invalid") from exc
    if expires.tzinfo is None or expires <= current:
        raise ValueError("acceptance report is expired")
    identities = report.get("identities")
    if not isinstance(identities, Mapping) or any(
        identities.get(key) != value for key, value in deployed_identity.items()
    ):
        raise ValueError("acceptance report does not match the deployment")
    scenarios = report.get("scenarios")
    if not isinstance(scenarios, Mapping) or set(scenarios) != set(REQUIRED_SCENARIOS):
        raise ValueError("acceptance report scenario inventory is incomplete")
    for name in REQUIRED_SCENARIOS:
        row = scenarios.get(name)
        if (
            not isinstance(row, Mapping)
            or row.get("status") != "passed"
            or row.get("lane") != SCENARIO_LANES[name]
            or not row.get("evidenceRefs")
        ):
            raise ValueError("acceptance report contains an unproven scenario")
    cleanup = report.get("cleanup")
    scan = report.get("secretScan")
    if (
        not isinstance(cleanup, Mapping)
        or cleanup.get("status") != "passed"
        or cleanup.get("historicalEvidencePreserved") is not True
        or cleanup.get("leasesReleased") is not True
        or not cleanup.get("evidenceRefs")
        or not isinstance(scan, Mapping)
        or scan.get("status") != "passed"
        or not scan.get("evidenceRefs")
        or not scan.get("scannedRefs")
    ):
        raise ValueError("acceptance report safety attestations are incomplete")
    # Re-run the controlling acceptance validator over the complete retained
    # evidence graph.  The local file digest authenticates the exact bytes; this
    # replay proves those bytes still contain every required case, independently
    # resolvable nested ref, cleanup/lease fact, and retained-evidence scan.  A
    # copied producer string plus top-level ``passed`` values is not authority.
    rebuilt = build_native_chat_acceptance_report(
        report,
        now=current,
        expected_commit=str(deployed_identity.get("moonmindCommit") or ""),
    )
    for key in (
        "identities",
        "safeIdentities",
        "profilePolicyRefs",
        "scenarios",
        "cleanup",
        "secretScan",
    ):
        if rebuilt.get(key) != report.get(key):
            raise ValueError("acceptance report is not a canonical validated report")
    return ValidatedNativeChatAcceptance(ref=ref, sha256=sha256, report=dict(report))


def load_native_chat_acceptance_report(
    ref: str,
    *,
    deployed_identity: Mapping[str, Any],
    now: datetime | None = None,
) -> ValidatedNativeChatAcceptance:
    """Resolve a digest-bound, locally materialized report and validate it.

    The API service deliberately does not treat an opaque ``artifact://`` id as
    proof. Operators materialize the published report read-only and configure a
    ``file://...#sha256=<hex>`` reference; both the bytes and deployment identity
    are checked on every decision.
    """

    parsed = urlsplit(str(ref or ""))
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("acceptance report must be a materialized file ref")
    expected = (parse_qs(parsed.fragment).get("sha256") or [""])[0].lower()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("acceptance report ref must include its sha256")
    path = Path(unquote(parsed.path)).resolve()
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise ValueError("acceptance report digest mismatch")
    value = json.loads(raw)
    if not isinstance(value, Mapping):
        raise ValueError("acceptance report is malformed")
    return validate_native_chat_acceptance_report(
        value, ref=ref, sha256=actual, deployed_identity=deployed_identity, now=now
    )


def resolve_native_chat_rollout(
    *,
    mode: NativeChatRolloutMode | str,
    acceptance: ValidatedNativeChatAcceptance | None,
    acceptance_invalid: bool = False,
) -> NativeChatRolloutDecision:
    """Resolve whether interactive native Chat is served for this deployment.

    Both ``canary`` and the temporary explicit ``enabled`` posture require a
    resolved report.  There is no string-presence or permanent operator bypass;
    post-proof steady state is reached by retiring this temporary gate.
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
    if resolved in {NativeChatRolloutMode.CANARY, NativeChatRolloutMode.ENABLED}:
        if acceptance is not None:
            return NativeChatRolloutDecision(
                mode=resolved,
                interactive=True,
                serve_native_ui=True,
                read_only_fallback=False,
                reason=(
                    REASON_CANARY_ADMITTED
                    if resolved is NativeChatRolloutMode.CANARY
                    else REASON_ENABLED
                ),
            )
        return NativeChatRolloutDecision(
            mode=resolved,
            interactive=False,
            serve_native_ui=False,
            read_only_fallback=True,
            reason=(
                REASON_ACCEPTANCE_INVALID
                if acceptance_invalid
                else REASON_CANARY_AWAITING_EVIDENCE
            ),
        )
    # Defensive fail-closed default for any future enum value.
    return NativeChatRolloutDecision(
        mode=NativeChatRolloutMode.READ_ONLY,
        interactive=False,
        serve_native_ui=False,
        read_only_fallback=True,
        reason=REASON_UNKNOWN_MODE_FAILED_CLOSED,
    )


def current_native_chat_rollout_decision(
    *, env: Mapping[str, Any] | None = None, now: datetime | None = None
) -> NativeChatRolloutDecision:
    """Resolve the one production decision used by every native Chat boundary."""

    source = env if env is not None else os.environ
    mode = parse_rollout_mode(str(source.get(NATIVE_CHAT_ROLLOUT_FLAG) or ""))
    if mode in {NativeChatRolloutMode.DISABLED, NativeChatRolloutMode.READ_ONLY}:
        return resolve_native_chat_rollout(mode=mode, acceptance=None)
    identity = native_chat_deployment_identity(env=source)
    ref = str(source.get("OMNIGENT_NATIVE_CHAT_ACCEPTANCE_REF") or "").strip()
    if identity is None or not ref:
        return resolve_native_chat_rollout(mode=mode, acceptance=None)
    try:
        acceptance = load_native_chat_acceptance_report(
            ref, deployed_identity=identity, now=now
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return resolve_native_chat_rollout(
            mode=mode, acceptance=None, acceptance_invalid=True
        )
    return resolve_native_chat_rollout(mode=mode, acceptance=acceptance)


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
    read-only fallback window completes, the flag is removed. The resulting
    unset/default posture remains evidence-gated (``CANARY``), so retirement
    cannot turn a missing or stale report into interactive authority.
    """

    return RolloutFlagRetirement(
        flag=NATIVE_CHAT_ROLLOUT_FLAG,
        temporary=True,
        retire_when=(
            "deterministic + protected-live native-chat acceptance evidence "
            "passes and the read-only fallback window completes"
        ),
        steady_state_mode=NativeChatRolloutMode.CANARY,
    )


__all__ = [
    "NATIVE_CHAT_ROLLOUT_FLAG",
    "NativeChatRolloutDecision",
    "NativeChatRolloutMode",
    "ValidatedNativeChatAcceptance",
    "REASON_ACCEPTANCE_INVALID",
    "REASON_CANARY_ADMITTED",
    "REASON_CANARY_AWAITING_EVIDENCE",
    "REASON_DISABLED",
    "REASON_ENABLED",
    "REASON_ROLLED_BACK_READ_ONLY",
    "REASON_UNKNOWN_MODE_FAILED_CLOSED",
    "RolloutFlagRetirement",
    "current_native_chat_rollout_decision",
    "load_native_chat_acceptance_report",
    "native_chat_deployment_identity",
    "parse_rollout_mode",
    "resolve_native_chat_rollout",
    "rollout_flag_retirement",
    "validate_native_chat_acceptance_report",
]
