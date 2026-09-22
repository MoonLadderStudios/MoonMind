"""Pre-apply validation and post-apply verification model.

* Pre-apply: validate target configuration, authorization, persistent
  storage, and preservation of access settings. Old API/worker health is
  diagnostic input only -- never admission to repair.
* Post-apply: service, ordinary-dispatch, and operator-access verification.
  Failed or unavailable mandatory checks stay explicit while the controller
  remains usable. Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TargetConfig:
    target_image: str
    project_name: str = "moonmind"
    compose_files: tuple[str, ...] = ()
    env_file: str | None = None
    # Access settings that must survive the update unchanged.
    preserved_access: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    mandatory: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PreApplyVerdict:
    allowed: bool
    checks: tuple[CheckResult, ...] = ()


@dataclass(frozen=True, slots=True)
class PostApplyVerdict:
    status: str  # SUCCEEDED | FAILED | PARTIALLY_VERIFIED
    checks: tuple[CheckResult, ...] = ()


def validate_before_apply(
    *,
    config: TargetConfig,
    authorized: bool,
    compose_valid: bool,
    storage_ready: bool,
    access_preserved: bool,
    old_health: CheckResult | None = None,
) -> PreApplyVerdict:
    """Validate before mutation. ``old_health`` (old API/worker health) is
    recorded as diagnostic only and can never block repair."""
    checks = [
        CheckResult("target_config", bool(config.target_image.strip()), True),
        CheckResult("authorization", authorized, True),
        CheckResult("compose_valid", compose_valid, True),
        CheckResult("persistent_storage", storage_ready, True),
        CheckResult("access_preserved", access_preserved, True),
    ]
    if old_health is not None:
        checks.append(
            CheckResult(
                name="old_health_diagnostic",
                ok=old_health.ok,
                mandatory=False,
                detail=old_health.detail or "old stack health is diagnostic, not admission",
            )
        )
    allowed = all(c.ok for c in checks if c.mandatory)
    return PreApplyVerdict(allowed=allowed, checks=tuple(checks))


def verify_after_apply(*, checks: tuple[CheckResult, ...]) -> PostApplyVerdict:
    """Verify after apply. Missing mandatory evidence can never be SUCCEEDED;
    unavailable optional checks degrade to PARTIALLY_VERIFIED, never hide the
    failure, and never disable the controller."""
    mandatory_failed = [c for c in checks if c.mandatory and not c.ok]
    optional_failed = [c for c in checks if not c.mandatory and not c.ok]
    if mandatory_failed:
        # If every mandatory check is merely unavailable (not proven bad) the
        # result is PARTIALLY_VERIFIED with the gaps explicit; a proven bad
        # mandatory check is FAILED.
        unavailable_only = all("unavailable" in c.detail.lower() for c in mandatory_failed)
        status = "PARTIALLY_VERIFIED" if unavailable_only else "FAILED"
    elif optional_failed:
        status = "PARTIALLY_VERIFIED"
    else:
        status = "SUCCEEDED"
    return PostApplyVerdict(status=status, checks=checks)


def summarize(checks: tuple[CheckResult, ...]) -> dict:
    return {
        "mandatoryFailed": [c.name for c in checks if c.mandatory and not c.ok],
        "optionalFailed": [c.name for c in checks if not c.mandatory and not c.ok],
    }
