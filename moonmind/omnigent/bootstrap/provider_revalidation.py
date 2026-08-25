"""Keep the deployment's configured OpenCode credential launchable.

Two things must be true before ``moonmind.omnigent-execution-readiness.v3``
advertises an OpenCode target: a Provider Profile must be enrolled and
connected, and its persisted ``model_catalog_evidence_json`` must have been
observed on the exact digest-pinned host image the deployment currently selects.

Both used to require a console action. Enrollment only happened through the
one-action bootstrap endpoint, and evidence was only written at that moment, so
a rebuilt or re-pulled OpenCode host image silently invalidated a perfectly good
credential. Either way the operator was told to "connect and validate a
compatible Provider Profile" for a deployment that already had everything it
needed.

This boundary closes both gaps from deployment configuration alone:

* enrollment runs the canonical bootstrap for the configured
  ``OPENCODE_API_KEY`` when no connected profile exists; and
* re-validation re-runs the pinned-runtime check against the already-enrolled
  SecretRef when only the evidence is stale.

Neither path substitutes a credential, an image, or a weaker evidence contract.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import select

logger = logging.getLogger(__name__)

# The runtime-backed model catalog evidence contract exists for exactly one
# provider route today. Keep that identity explicit rather than inferring it
# from a name list that would drift from the materializer contract.
OPENCODE_RUNTIME_ID = "opencode"
OPENCODE_PROVIDER_ID = "opencode-go"
OPENCODE_SECRET_ROLE = "opencode_api_key"

# Enrollment runs the full bootstrap (image acquisition, catalog sync, pinned
# runtime validation, qualification). Repeating that every reconciliation pass
# for configuration the runtime already rejected would burn the deployment's
# Docker and provider budget with no new information, so an attempt is made once
# per distinct configuration. Changing the key, the acknowledgement, or the
# resolved host image produces a new fingerprint and retries; so does a restart.
_ATTEMPTED_ENROLLMENTS: set[str] = set()


@dataclass(frozen=True, slots=True)
class ProviderReconcileOutcome:
    """Result of one bounded pass over the configured OpenCode credential."""

    ready: bool
    checked: int = 0
    enrolled: bool = False
    refreshed: tuple[str, ...] = field(default_factory=tuple)
    deferred: tuple[str, ...] = field(default_factory=tuple)
    reason: str | None = None


def reset_enrollment_attempts() -> None:
    """Clear the per-configuration enrollment latch (tests and operator retry)."""

    _ATTEMPTED_ENROLLMENTS.clear()


def evidence_is_current(profile: Any, *, image_ref: str) -> bool:
    """Report whether persisted evidence still matches the launchable identity.

    This mirrors the readiness and planner admission checks exactly: evidence
    must belong to the profile's current credential generation and to the host
    image the deployment currently pins.
    """

    evidence = profile.model_catalog_evidence_json
    if not isinstance(evidence, dict):
        return False
    try:
        generation = int(evidence.get("credentialGeneration") or 0)
    except (TypeError, ValueError):
        return False
    if generation != int(profile.credential_generation):
        return False
    return str(evidence.get("imageRef") or "") == image_ref


def _is_enrolled(profile: Any) -> bool:
    """Report whether this profile already carries a usable enrolled credential."""

    from api_service.db.models import ProviderProfileAuthState

    state = getattr(profile.auth_state, "value", profile.auth_state)
    return (
        bool(profile.enabled)
        and state == ProviderProfileAuthState.CONNECTED.value
        and OPENCODE_SECRET_ROLE in (profile.secret_refs or {})
    )


def _pinned_image_ref() -> str | None:
    from moonmind.omnigent.harness_platform.host_classes import (
        get_opencode_host_image_ref,
    )

    try:
        return get_opencode_host_image_ref()
    except Exception:
        return None


def _enrollment_fingerprint(
    *,
    api_key: str,
    accepted: bool,
    image_ref: str,
) -> str:
    """Return a non-secret identity for one enrollment configuration."""

    material = "\x1f".join((api_key, "1" if accepted else "0", image_ref))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


async def _opencode_profiles(session_factory: Any) -> list[Any]:
    from api_service.db.models import ManagedAgentProviderProfile

    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(ManagedAgentProviderProfile).where(
                        ManagedAgentProviderProfile.runtime_id == OPENCODE_RUNTIME_ID,
                        ManagedAgentProviderProfile.provider_id
                        == OPENCODE_PROVIDER_ID,
                    )
                )
            ).scalars()
        )


async def reconcile_opencode_provider_readiness(
    *,
    session_factory: Any,
    allow_enrollment: bool = True,
    env: Mapping[str, Any] | None = None,
    controller: Any | None = None,
) -> ProviderReconcileOutcome:
    """Enroll or re-validate the configured OpenCode credential.

    ``allow_enrollment`` is the caller's statement that the immutable image and
    harness-catalog authorities enrollment depends on are already established.
    Enrollment is skipped (not failed) until then so a first-boot race cannot
    burn the one attempt this configuration gets.
    """

    from moonmind.omnigent.settings import (
        opencode_contributor_data_use_accepted,
        resolved_opencode_api_key,
    )

    image_ref = _pinned_image_ref()
    profiles = await _opencode_profiles(session_factory)
    enrolled = [profile for profile in profiles if _is_enrolled(profile)]

    if not enrolled:
        api_key = resolved_opencode_api_key(env=env)
        if not api_key:
            # Nothing configured: the console enrollment path stays available and
            # readiness correctly reports that no compatible profile exists.
            return ProviderReconcileOutcome(
                ready=True,
                checked=len(profiles),
                reason="no deployment-configured OpenCode credential",
            )
        if not allow_enrollment:
            return ProviderReconcileOutcome(
                ready=False,
                checked=len(profiles),
                reason="waiting for immutable image and harness catalog authority",
            )
        return await _enroll_from_deployment_config(
            session_factory=session_factory,
            api_key=api_key,
            accepted=opencode_contributor_data_use_accepted(env=env),
            image_ref=image_ref or "",
            checked=len(profiles),
            controller=controller,
        )

    if image_ref is None:
        # The image-policy leg owns acquiring the pinned image. Defer rather
        # than record evidence for an image the deployment does not select.
        return ProviderReconcileOutcome(
            ready=False,
            checked=len(profiles),
            reason="pinned OpenCode host image unavailable",
        )
    return await _revalidate_stale_evidence(
        session_factory=session_factory,
        profiles=enrolled,
        checked=len(profiles),
        image_ref=image_ref,
    )


async def _enroll_from_deployment_config(
    *,
    session_factory: Any,
    api_key: str,
    accepted: bool,
    image_ref: str,
    checked: int,
    controller: Any | None,
) -> ProviderReconcileOutcome:
    """Run the canonical bootstrap for the deployment-configured credential."""

    from moonmind.omnigent.bootstrap.controller import BootstrapController
    from moonmind.omnigent.bootstrap.models import BootstrapState

    fingerprint = _enrollment_fingerprint(
        api_key=api_key, accepted=accepted, image_ref=image_ref
    )
    if fingerprint in _ATTEMPTED_ENROLLMENTS:
        return ProviderReconcileOutcome(
            ready=False,
            checked=checked,
            reason=(
                "OpenCode enrollment already failed for this configuration; "
                "correct the credential or acknowledgement and restart"
            ),
        )
    _ATTEMPTED_ENROLLMENTS.add(fingerprint)

    active = controller or BootstrapController(session_factory=session_factory)
    try:
        record = await active.configure_opencode(
            api_key=api_key,
            accept_contributor_data_use=accepted,
        )
    except Exception as exc:
        logger.warning(
            "OpenCode enrollment from deployment configuration failed: %s", exc
        )
        return ProviderReconcileOutcome(
            ready=False, checked=checked, reason=f"OpenCode enrollment failed: {exc}"
        )

    state = getattr(record.state, "value", record.state)
    if state != BootstrapState.ready.value:
        failure = (record.failure or {}).get("message") or state
        logger.warning(
            "OpenCode enrollment from deployment configuration did not complete: %s",
            failure,
        )
        return ProviderReconcileOutcome(
            ready=False,
            checked=checked,
            reason=f"OpenCode enrollment did not complete: {failure}",
        )

    _ATTEMPTED_ENROLLMENTS.discard(fingerprint)
    logger.info(
        "Enrolled the deployment-configured OpenCode Provider Profile: profile_id=%s",
        record.provider_profile_ref,
    )
    return ProviderReconcileOutcome(ready=True, checked=checked, enrolled=True)


async def _revalidate_stale_evidence(
    *,
    session_factory: Any,
    profiles: list[Any],
    checked: int,
    image_ref: str,
) -> ProviderReconcileOutcome:
    """Refresh stale OpenCode model evidence against the pinned host image.

    A profile the runtime rejects is left untouched and reported as deferred:
    this boundary never downgrades an enrolled credential, and it never records
    evidence for an image other than the one the deployment selects.
    """

    from moonmind.omnigent.opencode_runtime_validation import (
        OpenCodeProviderRuntimeValidationService,
    )
    from moonmind.omnigent.production import build_omnigent_secret_resolver
    from moonmind.provider_profiles.lease_client import CredentialLeasePurpose
    from moonmind.provider_profiles.maintenance import (
        acquire_credential_maintenance_guard,
    )

    stale = [
        profile
        for profile in profiles
        if not evidence_is_current(profile, image_ref=image_ref)
    ]
    if not stale:
        return ProviderReconcileOutcome(ready=True, checked=checked)

    resolver = build_omnigent_secret_resolver()
    refreshed: list[str] = []
    deferred: list[str] = []
    for row in stale:
        profile_id = row.profile_id
        operation_id = uuid4().hex
        guard = None
        evidence: dict[str, Any] | None = None
        try:
            guard = await acquire_credential_maintenance_guard(
                runtime_id=OPENCODE_RUNTIME_ID,
                profile_id=profile_id,
                purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION,
                operation_id=operation_id,
                metadata={
                    "workflowId": f"provider-revalidation:{operation_id}",
                    "ownerIsWorkflow": False,
                },
            )
            evidence = await OpenCodeProviderRuntimeValidationService(
                session_factory=session_factory,
                resolver=resolver,
                image_ref=image_ref,
            ).validate(profile=row, lease=guard.lease)
        except Exception as exc:
            deferred.append(profile_id)
            logger.warning(
                "OpenCode Provider Profile re-validation deferred: "
                "profile_id=%s image_ref=%s error=%s",
                profile_id,
                image_ref,
                exc,
            )
        finally:
            if guard is not None:
                try:
                    await guard.release()
                except Exception:
                    logger.warning(
                        "OpenCode re-validation lease release failed: profile_id=%s",
                        profile_id,
                        exc_info=True,
                    )
        if evidence is None:
            continue

        await _persist_evidence(
            session_factory=session_factory,
            profile_id=profile_id,
            evidence=evidence,
        )
        refreshed.append(profile_id)
        logger.info(
            "Refreshed OpenCode Provider Profile model evidence: "
            "profile_id=%s image_ref=%s",
            profile_id,
            image_ref,
        )

    return ProviderReconcileOutcome(
        ready=not deferred,
        checked=checked,
        refreshed=tuple(refreshed),
        deferred=tuple(deferred),
        reason=None if not deferred else "pinned runtime re-validation deferred",
    )


async def _persist_evidence(
    *,
    session_factory: Any,
    profile_id: str,
    evidence: dict[str, Any],
) -> None:
    """Record refreshed evidence without touching enrolled credential identity."""

    from api_service.db.models import ManagedAgentProviderProfile

    async with session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, profile_id)
        if profile is None:
            return
        if int(evidence.get("credentialGeneration") or 0) != int(
            profile.credential_generation
        ):
            # The credential rotated while validation ran; that rotation owns
            # the authoritative evidence for its own generation.
            return
        models = [
            str(item.get("qualifiedId") or "")
            for item in evidence.get("models", [])
            if isinstance(item, dict)
        ]
        profile.model_catalog_evidence_json = evidence
        if not profile.default_model and models:
            profile.default_model = models[0]
        behavior = dict(profile.command_behavior or {})
        behavior["runtime_validation"] = {
            "last_validated_at": evidence.get("validatedAt")
            or datetime.now(UTC).isoformat(),
            "image_ref": evidence["imageRef"],
            "runtime_versions": evidence.get("runtimeVersions"),
            "model_count": len(models),
        }
        profile.command_behavior = behavior
        await session.commit()


__all__ = [
    "OPENCODE_PROVIDER_ID",
    "OPENCODE_RUNTIME_ID",
    "OPENCODE_SECRET_ROLE",
    "ProviderReconcileOutcome",
    "evidence_is_current",
    "reconcile_opencode_provider_readiness",
    "reset_enrollment_attempts",
]
