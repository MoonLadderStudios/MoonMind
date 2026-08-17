"""Live Omnigent verification health, freshness, and safe-failure evidence.

Source issue: MoonLadderStudios/MoonMind#3710.

This module contains no provider semantics.  It turns synthetic, non-secret
runner / workflow-run / acceptance-evidence status into two portable
contracts:

* ``evaluate_live_verification_health`` — a readiness projection that fails
  closed when the protected provider-verification tier is queued, offline,
  stale, incomplete, or has no successful canary for the deployed commit and
  required image digests.  A required (Tier 4) soak/matrix outage must not
  flip Tier-1 (noncredentialed PR conformance) closed, but it must make the
  protected rollout/readiness gate fail closed.

* ``build_safe_failure_diagnostics`` — bounded, redacted failure evidence that
  is uploaded even when a case fails *before* the final secret-safety gate,
  instead of a single opaque ``withheld`` marker.

Both outputs are guaranteed secret-free through
:func:`moonmind.omnigent.conformance.assert_secret_free`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from moonmind.omnigent.conformance import (
    ConformanceContractError,
    SECRET_PATTERN,
    assert_secret_free,
)

LIVE_VERIFICATION_HEALTH_VERSION = "moonmind.omnigent.live-verification-health/v1"
SAFE_FAILURE_DIAGNOSTICS_VERSION = "moonmind.omnigent.live-failure-diagnostics/v1"

# The complete independent live matrix inventory (issue #3710 / #3508).  A
# silently-dropped mode must surface as an incomplete matrix, never as ready.
REQUIRED_LIVE_MATRIX_MODES = (
    "browser",
    "product",
    "cumulative",
    "stock",
    "static",
    "ondemand",
    "failures",
    "workflow_chat",
)

# A scheduled canary that stays queued longer than this is treated as a
# stalled protected runner, not a passing gate.
DEFAULT_MAX_QUEUE_AGE_SECONDS = 6 * 60 * 60

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REDACTED = "[redacted]"
DEFAULT_MAX_LOG_LINES = 200
DEFAULT_MAX_LINE_LENGTH = 500


class LiveVerificationHealthError(ValueError):
    """Raised when live-verification status cannot safely be interpreted."""


@dataclass(frozen=True, slots=True)
class ReadinessSignal:
    """One named readiness signal with a bounded, non-secret explanation."""

    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def _parse_timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise LiveVerificationHealthError(f"{field} timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LiveVerificationHealthError(
            f"{field} timestamp is malformed: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise LiveVerificationHealthError(f"{field} timestamp needs a timezone")
    return parsed


def _digest_of(image_ref: Any) -> str | None:
    if not isinstance(image_ref, str):
        return None
    digest = image_ref.rsplit("@", 1)[-1]
    return digest if _DIGEST.fullmatch(digest) else None


def evaluate_live_verification_health(
    *,
    runner: Mapping[str, Any],
    queue: Mapping[str, Any],
    latest_run: Mapping[str, Any] | None,
    manifest: Mapping[str, Any] | None,
    deployed_commit: str,
    required_digests: Mapping[str, str],
    now: datetime | None = None,
    max_queue_age_seconds: int = DEFAULT_MAX_QUEUE_AGE_SECONDS,
    protected_tier_required: bool = True,
    tier4_healthy: bool = True,
) -> dict[str, Any]:
    """Project protected-live verification readiness, failing closed.

    All inputs are expected to be non-secret projections derived from the
    GitHub Actions / runner API and published acceptance evidence.  The
    returned document is validated to be secret-free before it is returned.
    """
    observed_at = now or datetime.now(timezone.utc)
    if not isinstance(deployed_commit, str) or not deployed_commit.strip():
        raise LiveVerificationHealthError("deployed_commit is required")

    signals: list[ReadinessSignal] = []

    # --- Runner online / idle-busy state -------------------------------------
    runner_status = str(runner.get("status", "")).strip().lower()
    runner_online = runner_status == "online"
    busy = bool(runner.get("busy", False))
    signals.append(
        ReadinessSignal(
            "runner_online",
            runner_online,
            f"runner status={runner_status or 'unknown'} busy={busy}",
        )
    )

    # --- Oldest queued provider-verification job age -------------------------
    oldest_age = queue.get("oldestQueuedAgeSeconds")
    if oldest_age is None:
        queue_ok = True
        queue_detail = "no provider-verification job queued"
    else:
        try:
            oldest_age_value = int(oldest_age)
        except (TypeError, ValueError) as exc:
            raise LiveVerificationHealthError(
                "oldestQueuedAgeSeconds must be an integer or null"
            ) from exc
        queue_ok = oldest_age_value <= max_queue_age_seconds
        queue_detail = (
            f"oldest queued job age {oldest_age_value}s "
            f"(policy {max_queue_age_seconds}s)"
        )
    signals.append(ReadinessSignal("queue_within_policy", queue_ok, queue_detail))

    # --- A successful canary exists for the deployed commit ------------------
    last_started = None
    last_completed = None
    if latest_run is None:
        signals.append(
            ReadinessSignal(
                "successful_canary",
                False,
                "no live-conformance run recorded",
            )
        )
    else:
        run_status = str(latest_run.get("status", "")).strip().lower()
        run_commit = str(latest_run.get("sourceCommit", "")).strip()
        last_started = latest_run.get("startedAt")
        last_completed = latest_run.get("completedAt")
        run_ok = run_status == "success" and run_commit == deployed_commit
        if run_status != "success":
            detail = f"latest run status={run_status or 'unknown'}"
        elif run_commit != deployed_commit:
            detail = "latest successful run is for a different source commit"
        else:
            detail = f"successful canary for commit {run_commit[:12]}"
        signals.append(ReadinessSignal("successful_canary", run_ok, detail))

        # --- Matrix completeness -------------------------------------------
        observed_modes = latest_run.get("modes")
        if not isinstance(observed_modes, (list, tuple)):
            raise LiveVerificationHealthError("latest_run modes must be a list")
        missing_modes = [
            mode for mode in REQUIRED_LIVE_MATRIX_MODES if mode not in observed_modes
        ]
        signals.append(
            ReadinessSignal(
                "matrix_complete",
                not missing_modes,
                "all required modes present"
                if not missing_modes
                else f"missing matrix modes: {missing_modes}",
            )
        )

    # --- Acceptance evidence freshness + immutable digest binding ------------
    if manifest is None:
        signals.append(
            ReadinessSignal("evidence_fresh", False, "no acceptance manifest published")
        )
        signals.append(
            ReadinessSignal("evidence_digests_match", False, "no acceptance manifest")
        )
        expires_at = None
    else:
        generated_at = _parse_timestamp(
            manifest.get("generatedAt"), field="manifest generatedAt"
        )
        expires_at = _parse_timestamp(
            manifest.get("expiresAt"), field="manifest expiresAt"
        )
        fresh = generated_at <= observed_at < expires_at
        if generated_at > observed_at:
            detail = "acceptance manifest is future-dated"
        elif observed_at >= expires_at:
            detail = f"acceptance evidence expired at {expires_at.isoformat()}"
        else:
            detail = f"acceptance evidence valid until {expires_at.isoformat()}"
        manifest_commit = str(manifest.get("sourceCommit", "")).strip()
        fresh = fresh and manifest_commit == deployed_commit
        if manifest_commit != deployed_commit:
            detail = "acceptance evidence is for a different source commit"
        signals.append(ReadinessSignal("evidence_fresh", fresh, detail))

        images = manifest.get("images")
        images = images if isinstance(images, Mapping) else {}
        observed_digests = {
            "server": _digest_of(images.get("serverDigest") or images.get("server")),
            "host": _digest_of(images.get("hostDigest") or images.get("host")),
        }
        digests_match = all(
            required_digests.get(role) is not None
            and observed_digests.get(role) == required_digests.get(role)
            for role in ("server", "host")
        )
        signals.append(
            ReadinessSignal(
                "evidence_digests_match",
                digests_match,
                "acceptance evidence matches required image digests"
                if digests_match
                else "acceptance evidence does not match required image digests",
            )
        )

    # --- Tier separation ------------------------------------------------------
    # Tier 1 (noncredentialed PR conformance) does not depend on the protected
    # self-hosted runner, so a protected/Tier-4 outage must not flip it closed.
    tier1_ready = True
    signals.append(
        ReadinessSignal(
            "tier4_soak_healthy",
            bool(tier4_healthy),
            "scheduled soak/failure matrix healthy"
            if tier4_healthy
            else "scheduled soak/failure matrix is degraded",
        )
    )

    protected_signal_names = {
        "runner_online",
        "queue_within_policy",
        "successful_canary",
        "matrix_complete",
        "evidence_fresh",
        "evidence_digests_match",
        "tier4_soak_healthy",
    }
    protected_tier_ready = all(
        signal.ok for signal in signals if signal.name in protected_signal_names
    )
    rollout_ready = tier1_ready and (
        protected_tier_ready or not protected_tier_required
    )
    not_ready_reasons = [signal.name for signal in signals if not signal.ok]

    projection = {
        "schemaVersion": LIVE_VERIFICATION_HEALTH_VERSION,
        "generatedAt": observed_at.isoformat(),
        "deployedCommit": deployed_commit,
        "requiredDigests": {
            role: required_digests.get(role) for role in ("server", "host")
        },
        "runner": {"online": runner_online, "busy": busy},
        "queue": {"oldestQueuedAgeSeconds": oldest_age},
        "lastStartedAt": last_started,
        "lastCompletedAt": last_completed,
        "acceptanceExpiresAt": expires_at.isoformat() if expires_at else None,
        "protectedTierRequired": bool(protected_tier_required),
        "tier1Ready": tier1_ready,
        "protectedTierReady": protected_tier_ready,
        "rolloutReady": rollout_ready,
        "notReadyReasons": not_ready_reasons,
        "signals": [signal.as_dict() for signal in signals],
    }
    assert_secret_free(projection)
    return projection


def _redact(text: str) -> str:
    return SECRET_PATTERN.sub(_REDACTED, text)


def build_safe_failure_diagnostics(
    *,
    mode: str,
    outcome: str,
    setup_stage: str,
    runner_health: Mapping[str, Any] | None = None,
    failure_summary: str,
    log_tail: Sequence[str] = (),
    duration_seconds: float | None = None,
    now: datetime | None = None,
    max_log_lines: int = DEFAULT_MAX_LOG_LINES,
    max_line_length: int = DEFAULT_MAX_LINE_LENGTH,
) -> dict[str, Any]:
    """Build bounded, redacted failure evidence for an unsuccessful live case.

    A case that fails before the final secret-safety gate must still upload a
    safe failure summary, the setup stage reached, runner health, sanitized
    log lines, and its duration — never a single opaque ``withheld`` marker.
    """
    if not isinstance(mode, str) or not mode.strip():
        raise LiveVerificationHealthError("mode is required")
    if not isinstance(setup_stage, str) or not setup_stage.strip():
        raise LiveVerificationHealthError("setup_stage is required")
    if not isinstance(failure_summary, str) or not failure_summary.strip():
        raise LiveVerificationHealthError("failure_summary is required")

    observed_at = (now or datetime.now(timezone.utc)).isoformat()

    tail = [str(line) for line in log_tail][-max_log_lines:]
    redacted_tail = [_redact(line)[:max_line_length] for line in tail]

    safe_runner_health: dict[str, Any] = {}
    if runner_health is not None:
        for key, value in runner_health.items():
            if str(key).strip().lower() in {"token", "password", "authorization"}:
                continue
            safe_runner_health[str(key)] = (
                _redact(value) if isinstance(value, str) else value
            )

    diagnostics = {
        "schemaVersion": SAFE_FAILURE_DIAGNOSTICS_VERSION,
        "mode": mode,
        "status": "failed",
        "outcome": str(outcome),
        "setupStage": setup_stage,
        "failureSummary": _redact(failure_summary)[:max_line_length],
        "runnerHealth": safe_runner_health,
        "durationSeconds": duration_seconds,
        "logTail": redacted_tail,
        "logTailTruncated": len(tail) < len(log_tail),
        "generatedAt": observed_at,
        "secretScan": {"status": "passed", "redaction": "applied"},
    }
    try:
        assert_secret_free(diagnostics)
    except ConformanceContractError as exc:  # pragma: no cover - defensive
        raise LiveVerificationHealthError(
            "safe failure diagnostics still contained secret-like material after "
            "redaction"
        ) from exc
    return diagnostics
