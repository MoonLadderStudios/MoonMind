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
from datetime import UTC, datetime, timedelta
from typing import Any, Collection, Mapping

from sqlalchemy import select

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

logger = logging.getLogger(__name__)

# The runtime-backed model catalog evidence contract exists for the OpenCode
# family. Keep both OpenCode routes explicit because they share the same host
# image while retaining distinct credential materializers.
OPENCODE_RUNTIME_ID = "opencode"
OPENCODE_PROVIDER_ID = "opencode-go"
OPENCODE_PROVIDER_IDS = (OPENCODE_PROVIDER_ID, "opencode")
OPENCODE_SECRET_ROLE = "opencode_api_key"
OPENCODE_DEPLOYMENT_SECRET_REF = "env://OPENCODE_API_KEY"

# Readiness reads this ``command_behavior`` entry to distinguish a bounded
# re-validation attempt in flight from a credential the pinned runtime keeps
# rejecting, which only an operator can fix.
REVALIDATION_FAILURE_KEY = "runtime_revalidation_failure"
MAX_REVALIDATION_ATTEMPTS = 3

# Observations are stamped by whichever host ran the probe, so a small forward
# skew is ordinary clock disagreement rather than a stale catalog. Anything
# further ahead cannot be shown to have been taken inside the interval.
MAX_OBSERVATION_CLOCK_SKEW = timedelta(minutes=5)

# Enrollment runs the full bootstrap (image acquisition, catalog sync, pinned
# runtime validation, qualification). Repeating that forever for configuration
# the provider has definitively rejected would burn the deployment's Docker and
# provider budget with no new information, so attempts per distinct
# configuration are bounded rather than unlimited. They are not limited to one:
# Temporal, Docker, and the database are all startup dependencies whose
# transient unavailability must stay retryable through the caller's existing
# backoff. A configuration the deployment states incorrectly -- a malformed key,
# or a declined acknowledgement -- is terminal on the first attempt because no
# amount of retrying changes it. Changing the key, the acknowledgement, or the
# resolved host image produces a new fingerprint; so does a restart.
_MAX_ENROLLMENT_ATTEMPTS = 5
_ENROLLMENT_ATTEMPTS: dict[str, int] = {}


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
    """Clear the per-configuration attempt budget (tests and operator retry)."""

    _ENROLLMENT_ATTEMPTS.clear()


def evidence_observation_is_current(
    evidence: Any,
    *,
    env: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Report whether the catalog observation is still inside its interval.

    The pinned host image refreshes its model catalog from the provider at
    probe time, so an observation bound only to credential generation and image
    digest freezes whichever catalog the first probe saw. Models the provider
    publishes afterwards -- contributor tiers included -- would never reach
    selection, readiness, or admission on an otherwise unchanged deployment.

    Every boundary that admits an OpenCode target answers this same question:
    the bootstrap reconciler, the execution-readiness catalog, pre-session
    planning, and smoke admission. Enforcing the interval in the reconciler
    alone would leave the other three advertising and launching from a catalog
    the provider has since changed -- including a model it has removed --
    whenever a refresh has not yet succeeded.
    """

    from moonmind.omnigent.settings import opencode_model_catalog_max_age

    max_age = opencode_model_catalog_max_age(env=env)
    if max_age is None:
        return True
    if not isinstance(evidence, Mapping):
        return False
    raw = str(evidence.get("validatedAt") or "").strip()
    if not raw:
        # An observation that cannot state when it was taken cannot be shown to
        # be current. Re-probe rather than trust an unbounded snapshot.
        return False
    try:
        observed = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    age = (now or datetime.now(UTC)) - observed
    if age < -MAX_OBSERVATION_CLOCK_SKEW:
        # A snapshot restore or a backward host-clock correction leaves an
        # observation stamped in the future. Its negative age would satisfy any
        # interval for as long as the timestamp stays ahead, keeping a catalog
        # -- including a model the provider has removed -- authoritative for
        # that whole window. Treat it as unproven and re-probe instead.
        return False
    return age <= max_age


def revalidation_is_exhausted(profile: Any, *, image_refs: Collection[str]) -> bool:
    """Report whether re-validation gave up on this credential and image.

    Re-validation preserves the enrolled credential and its prior evidence when
    the pinned runtime rejects it, so a revoked key or a provider outage looks
    exactly like an attempt in flight. ``_record_revalidation_failure`` records
    the bounded outcome, and reading it is what keeps the reconciler from
    probing the provider on every background pass and keeps readiness from
    promising a wait that finishes automatically.

    The record is scoped to the image and credential generation it was earned
    against, so re-pinning the host image or reconnecting the credential
    restores the full attempt budget without a separate reset action.
    """

    behavior = getattr(profile, "command_behavior", None) or {}
    record = behavior.get(REVALIDATION_FAILURE_KEY)
    if not isinstance(record, Mapping) or record.get("exhausted") is not True:
        return False
    try:
        generation = int(record.get("credentialGeneration") or 0)
    except (TypeError, ValueError):
        return False
    if generation != int(profile.credential_generation):
        # A rotated credential has not been attempted yet.
        return False
    return str(record.get("imageRef") or "") in set(image_refs)


def evidence_matches_launchable_identity(
    evidence: Any, *, profile: Any, image_ref: str
) -> bool:
    """Report whether one observation belongs to the launchable identity.

    This mirrors the readiness and planner admission checks exactly: evidence
    must belong to the profile's current credential generation, to the host
    image the deployment currently pins, and to the materializer the launch
    path uses, and it must contain the profile's selected model.
    """

    if not isinstance(evidence, Mapping):
        return False
    try:
        generation = int(evidence.get("credentialGeneration") or 0)
    except (TypeError, ValueError):
        return False
    if generation != int(profile.credential_generation):
        return False
    if str(evidence.get("imageRef") or "") != image_ref:
        return False
    if not isinstance(evidence.get("runtimeVersions"), dict):
        return False
    from moonmind.omnigent.harness_platform.materializers import (
        materializer_ref_for_provider,
    )

    try:
        expected_materializer_ref = materializer_ref_for_provider(
            str(profile.runtime_id or ""),
            str(profile.provider_id or ""),
        )
    except HarnessPlatformError:
        return False
    if str(evidence.get("materializerRef") or "") != expected_materializer_ref:
        return False
    models = {
        str(item.get("qualifiedId") or "")
        for item in evidence.get("models", [])
        if isinstance(item, dict)
    }
    default_model = str(getattr(profile, "default_model", None) or "").strip()
    return bool(default_model and default_model in models)


def evidence_is_current(
    profile: Any,
    *,
    image_ref: str,
    env: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Report whether persisted evidence still answers for this deployment.

    Two separate questions must both hold: the observation belongs to the
    launchable identity, and it was taken inside the configured catalog
    interval. Identity alone would keep a first observation forever, because
    the credential generation and image digest of a healthy deployment never
    change on their own.
    """

    evidence = profile.model_catalog_evidence_json
    if not evidence_matches_launchable_identity(
        evidence, profile=profile, image_ref=image_ref
    ):
        return False
    return evidence_observation_is_current(evidence, env=env, now=now)


def _has_configured_runtime_authority(profile: Any) -> bool:
    """Report whether this profile can authorize an OpenCode runtime probe.

    Deliberately independent of ``enabled``. Enrollment applies API-key setup
    with ``enabled=True``, but only an explicit user or policy disable prevents
    deployment configuration from repairing a disabled profile. The one
    pending state accepted here is the startup seed's exact deployment
    SecretRef: it is already materializable and must be promoted only by the
    pinned-runtime evidence path below.
    """

    from api_service.db.models import ProviderProfileAuthState

    state = getattr(profile.auth_state, "value", profile.auth_state)
    credential_source = getattr(
        getattr(profile, "credential_source", None),
        "value",
        getattr(profile, "credential_source", None),
    )
    if (
        str(getattr(profile, "provider_id", "") or "") == "opencode"
        and state == ProviderProfileAuthState.CONNECTED.value
        and credential_source == "none"
    ):
        return True
    secret_ref = str((profile.secret_refs or {}).get(OPENCODE_SECRET_ROLE) or "")
    return (state == ProviderProfileAuthState.CONNECTED.value and bool(secret_ref)) or (
        state == ProviderProfileAuthState.API_KEY_PENDING.value
        and secret_ref == OPENCODE_DEPLOYMENT_SECRET_REF
    )


def _has_authoritative_disable(profile: Any) -> bool:
    """Report whether startup configuration must preserve the disabled state."""

    reason = getattr(
        getattr(profile, "disabled_reason", None),
        "value",
        getattr(profile, "disabled_reason", None),
    )
    return str(reason or "") in {"user_disabled", "policy_disabled"}


def _pinned_image_ref() -> str | None:
    from moonmind.omnigent.harness_platform.host_classes import (
        get_opencode_host_image_ref,
    )

    try:
        return get_opencode_host_image_ref()
    except Exception:
        return None


def model_catalog_evidence_identity(
    *,
    profile_id: str,
    image_ref: str,
    credential_generation: Any = None,
) -> str:
    """Return the exact evidence identity one re-validation pass refreshes.

    MoonLadderStudios/MoonMind#3878 invariant 3: credentialless validation is
    single-flight for one exact evidence identity. The identity is exactly what
    :func:`evidence_matches_launchable_identity` compares — the profile, the
    pinned host image, and the credential generation — so two maintainers that
    would produce interchangeable evidence collapse onto one probe, while a
    re-pinned image or a rotated credential is a different identity that is
    always allowed to run.
    """

    material = "\x1f".join(
        (
            str(profile_id or "").strip(),
            str(image_ref or "").strip(),
            str(credential_generation if credential_generation is not None else ""),
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"opencode-model-catalog:{digest}"


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
                        ManagedAgentProviderProfile.provider_id.in_(
                            OPENCODE_PROVIDER_IDS
                        ),
                    )
                )
            ).scalars()
        )


async def reconcile_opencode_provider_readiness(
    *,
    session_factory: Any,
    allow_enrollment: bool = True,
    profile_ids: Collection[str] | None = None,
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
    if profile_ids is not None:
        selected_ids = {
            str(profile_id).strip()
            for profile_id in profile_ids
            if str(profile_id).strip()
        }
        profiles = [
            profile for profile in profiles if profile.profile_id in selected_ids
        ]
    enrolled = [
        profile for profile in profiles if _has_configured_runtime_authority(profile)
    ]
    api_key = resolved_opencode_api_key(env=env)
    key_backed_enrolled = any(
        str(getattr(profile, "provider_id", "") or "") == OPENCODE_PROVIDER_ID
        for profile in enrolled
    )
    recoverable_deployment_profile = next(
        (
            profile
            for profile in profiles
            if profile.profile_id == "opencode-go-default"
            and str(getattr(profile, "provider_id", "") or "")
            == OPENCODE_PROVIDER_ID
            and not profile.enabled
            and not _has_authoritative_disable(profile)
        ),
        None,
    )
    # An authoritative disable on the deployment-owned default remains
    # authoritative even when the profile lacks runtime authority (for example
    # policy_disabled with auth_state=not_configured is excluded from enrolled).
    # Detect it from all matching profiles before deciding enrollment is absent.
    has_authoritative_disable_on_default = any(
        profile.profile_id == "opencode-go-default"
        and str(getattr(profile, "provider_id", "") or "") == OPENCODE_PROVIDER_ID
        and _has_authoritative_disable(profile)
        for profile in profiles
    )
    # OpenCode Zen and OpenCode Go are distinct provider routes. A launchable
    # credential-free Zen profile must not consume an explicitly configured Go
    # credential or prevent its separate enrollment.
    if (
        api_key
        and profile_ids is None
        and (not key_backed_enrolled or recoverable_deployment_profile is not None)
        and not has_authoritative_disable_on_default
    ):
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
    # Explicit user and policy disables remain authoritative. Any other
    # inconsistent disabled state on the stable deployment-owned Go profile was
    # repaired through canonical bootstrap above when OPENCODE_API_KEY exists.
    launchable = [profile for profile in enrolled if profile.enabled]

    if not enrolled:
        if has_authoritative_disable_on_default:
            # The deployment-owned default is explicitly disabled. Preserve
            # that authority instead of re-enrolling from configuration.
            return ProviderReconcileOutcome(
                ready=True,
                checked=len(profiles),
                reason="every enrolled OpenCode Provider Profile is disabled",
            )
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

    if not launchable:
        return ProviderReconcileOutcome(
            ready=True,
            checked=len(profiles),
            reason="every enrolled OpenCode Provider Profile is disabled",
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
        profiles=launchable,
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
    attempts = _ENROLLMENT_ATTEMPTS.get(fingerprint, 0)
    if attempts >= _MAX_ENROLLMENT_ATTEMPTS:
        return ProviderReconcileOutcome(
            ready=False,
            checked=checked,
            reason=(
                "OpenCode enrollment exhausted its attempts for this "
                "configuration; correct the credential or acknowledgement"
            ),
        )
    _ENROLLMENT_ATTEMPTS[fingerprint] = attempts + 1

    active = controller or BootstrapController(session_factory=session_factory)
    try:
        record = await active.configure_opencode(
            api_key=api_key,
            accept_contributor_data_use=accepted,
        )
    except ValueError as exc:
        # The deployment stated its configuration incorrectly: a malformed key,
        # or a declined contributor acknowledgement. Retrying cannot change the
        # outcome, so this consumes the whole budget instead of one attempt.
        _ENROLLMENT_ATTEMPTS[fingerprint] = _MAX_ENROLLMENT_ATTEMPTS
        logger.warning("OpenCode enrollment configuration is invalid: %s", exc)
        return ProviderReconcileOutcome(
            ready=False,
            checked=checked,
            reason=f"OpenCode enrollment configuration is invalid: {exc}",
        )
    except Exception as exc:
        # Temporal, Docker, the registry, and the database are all startup
        # dependencies. Their transient unavailability keeps the remaining
        # attempts and defers to the caller's bounded backoff.
        logger.warning(
            "OpenCode enrollment from deployment configuration failed "
            "(attempt %s of %s): %s",
            attempts + 1,
            _MAX_ENROLLMENT_ATTEMPTS,
            exc,
        )
        return ProviderReconcileOutcome(
            ready=False, checked=checked, reason=f"OpenCode enrollment failed: {exc}"
        )

    state = getattr(record.state, "value", record.state)
    if state != BootstrapState.ready.value:
        failure = (record.failure or {}).get("message") or state
        logger.warning(
            "OpenCode enrollment from deployment configuration did not complete "
            "(attempt %s of %s): %s",
            attempts + 1,
            _MAX_ENROLLMENT_ATTEMPTS,
            failure,
        )
        return ProviderReconcileOutcome(
            ready=False,
            checked=checked,
            reason=f"OpenCode enrollment did not complete: {failure}",
        )

    _ENROLLMENT_ATTEMPTS.pop(fingerprint, None)
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
    evidence for an image other than the one the deployment selects. Once the
    recorded attempt budget is exhausted for that identity the profile stops
    being probed at all and is reported as an operator-actionable deferral.
    Only an attempt the provider answered spends that budget: a pass that never
    acquired the credential maintenance lease defers with the budget intact.
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

    # An identity this image has already rejected ``MAX_REVALIDATION_ATTEMPTS``
    # times learns nothing from another Docker-backed probe, and the startup
    # maintainer re-enters this boundary every 120 seconds for as long as
    # readiness stays false. Without this the recorded budget bounds nothing:
    # a provider outage or a revoked key would probe the provider forever.
    # Re-pinning the image or reconnecting the credential restores the budget.
    exhausted_ids = {
        profile.profile_id
        for profile in stale
        if revalidation_is_exhausted(profile, image_refs=(image_ref,))
    }
    for profile_id in sorted(exhausted_ids):
        logger.warning(
            "OpenCode Provider Profile re-validation is exhausted; skipping the "
            "pinned runtime probe until the credential or host image changes: "
            "profile_id=%s image_ref=%s",
            profile_id,
            image_ref,
        )
    pending = [p for p in stale if p.profile_id not in exhausted_ids]

    from moonmind.omnigent.harness_platform.materializers import (
        materializer_ref_for_provider,
    )

    needs_secret_resolution = any(
        materializer_ref_for_provider(row.runtime_id, row.provider_id) != "none@1"
        for row in pending
    )
    resolver = build_omnigent_secret_resolver() if needs_secret_resolution else None
    refreshed: list[str] = []
    deferred: list[str] = sorted(exhausted_ids)
    for row in pending:
        profile_id = row.profile_id
        # MoonLadderStudios/MoonMind#3878 invariant 3: the lease identity is the
        # exact evidence identity being refreshed, not a fresh operation id. Two
        # maintainers racing the same refresh derive the same owner, so the
        # manager grants one of them and reports ``already_held`` to the other
        # instead of running a second provider probe.
        operation_id = model_catalog_evidence_identity(
            profile_id=profile_id,
            image_ref=image_ref,
            credential_generation=getattr(row, "credential_generation", None),
        )
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
        except Exception as exc:
            # The maintenance lease is a MoonMind-side authority handoff, not a
            # provider verdict: contention with another maintainer, or a
            # transient Temporal/profile-manager error, means no probe ran.
            # Charging it to MAX_REVALIDATION_ATTEMPTS would let three such
            # deferrals retire a credential the provider never rejected, and
            # `revalidation_is_exhausted` would then skip the profile until its
            # credential or host image changes. Preserve the budget and defer to
            # the startup maintainer's own retry interval.
            deferred.append(profile_id)
            logger.warning(
                "OpenCode Provider Profile re-validation could not acquire the "
                "credential maintenance lease; the attempt budget is preserved: "
                "profile_id=%s image_ref=%s error=%s",
                profile_id,
                image_ref,
                exc,
            )
            continue

        if guard.lease.already_held:
            # Another maintainer already owns this exact evidence identity.
            # Re-probing would duplicate provider load, and releasing the lease
            # would cancel the holder's authority, so stand down with the
            # attempt budget intact.
            deferred.append(profile_id)
            logger.info(
                "OpenCode Provider Profile re-validation is already in flight for "
                "this evidence identity; deferring to the current holder: "
                "profile_id=%s image_ref=%s",
                profile_id,
                image_ref,
            )
            continue

        evidence: dict[str, Any] | None = None
        try:
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
            try:
                await guard.release()
            except Exception:
                logger.warning(
                    "OpenCode re-validation lease release failed: profile_id=%s",
                    profile_id,
                    exc_info=True,
                )
        if evidence is None:
            await _record_revalidation_failure(
                session_factory=session_factory,
                profile_id=profile_id,
                image_ref=image_ref,
            )
            continue

        committed = await _persist_evidence(
            session_factory=session_factory,
            profile_id=profile_id,
            evidence=evidence,
        )
        if not committed:
            # The authority handoff did not land, so readiness and planning still
            # reject this profile. Reporting it refreshed would let the startup
            # coordinator record a successful pass over an unlaunchable profile.
            deferred.append(profile_id)
            logger.warning(
                "OpenCode Provider Profile evidence was not committed: "
                "profile_id=%s image_ref=%s",
                profile_id,
                image_ref,
            )
            continue
        refreshed.append(profile_id)
        logger.info(
            "Refreshed OpenCode Provider Profile model evidence: "
            "profile_id=%s image_ref=%s",
            profile_id,
            image_ref,
        )

    reasons: list[str] = []
    if exhausted_ids:
        reasons.append(
            "pinned runtime re-validation exhausted; reconnect the credential "
            "or re-pin the OpenCode host image"
        )
    if len(deferred) > len(exhausted_ids):
        reasons.append("pinned runtime re-validation deferred")
    return ProviderReconcileOutcome(
        ready=not deferred,
        checked=checked,
        refreshed=tuple(refreshed),
        deferred=tuple(deferred),
        reason="; ".join(reasons) or None,
    )


async def _persist_evidence(
    *,
    session_factory: Any,
    profile_id: str,
    evidence: dict[str, Any],
) -> bool:
    """Record refreshed evidence without touching enrolled credential identity.

    Returns whether this pass actually committed launchable evidence, so the
    caller never reports a profile refreshed that readiness still rejects.
    """

    from api_service.db.models import (
        ManagedAgentProviderProfile,
        ProviderProfileAuthState,
    )

    async with session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, profile_id)
        if profile is None:
            return False
        if int(evidence.get("credentialGeneration") or 0) != int(
            profile.credential_generation
        ):
            # The credential rotated while validation ran; that rotation owns
            # the authoritative evidence for its own generation.
            return False
        models = [
            str(item.get("qualifiedId") or "")
            for item in evidence.get("models", [])
            if isinstance(item, dict)
        ]
        profile.model_catalog_evidence_json = evidence
        behavior = dict(profile.command_behavior or {})
        behavior["runtime_validation"] = {
            "last_validated_at": evidence.get("validatedAt")
            or datetime.now(UTC).isoformat(),
            "image_ref": evidence["imageRef"],
            "runtime_versions": evidence.get("runtimeVersions"),
            "model_count": len(models),
        }
        behavior.pop(REVALIDATION_FAILURE_KEY, None)
        # This pass just observed the catalog, so the refresh interval has
        # nothing to say about it. Only the launchable identity is in question.
        launchable = evidence_matches_launchable_identity(
            evidence,
            profile=profile,
            image_ref=str(evidence.get("imageRef") or ""),
        )
        from moonmind.omnigent.harness_platform.materializers import (
            materializer_ref_for_provider,
        )

        credentialless = (
            materializer_ref_for_provider(profile.runtime_id, profile.provider_id)
            == "none@1"
        )
        readiness = dict(behavior.get("auth_readiness") or {})
        readiness.update(
            {
                "connected": launchable,
                "backing_secret_exists": not credentialless,
                "launch_ready": launchable,
            }
        )
        if launchable:
            readiness.pop("failure_reason", None)
            profile.auth_state = ProviderProfileAuthState.CONNECTED
            behavior["auth_state"] = "connected"
            behavior["auth_status_label"] = (
                "No API key required" if credentialless else "OpenCode API key ready"
            )
        else:
            readiness["failure_reason"] = (
                "The selected model was not observed by the pinned OpenCode runtime."
            )
        behavior["auth_readiness"] = readiness
        profile.command_behavior = behavior
        await session.commit()
        return launchable


async def _record_revalidation_failure(
    *,
    session_factory: Any,
    profile_id: str,
    image_ref: str,
) -> None:
    """Count a failed re-validation so readiness can stop promising a retry.

    Re-validation deliberately preserves the enrolled credential and its prior
    evidence when the pinned runtime rejects it, which is indistinguishable from
    an in-flight attempt unless the outcome is recorded. Readiness reads this to
    tell "MoonMind is re-validating" apart from "this credential needs an
    operator".
    """

    from api_service.db.models import ManagedAgentProviderProfile

    async with session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, profile_id)
        if profile is None:
            return
        behavior = dict(profile.command_behavior or {})
        previous = behavior.get(REVALIDATION_FAILURE_KEY)
        attempts = 0
        if (
            isinstance(previous, dict)
            and str(previous.get("imageRef") or "") == image_ref
            and int(previous.get("credentialGeneration") or 0)
            == int(profile.credential_generation)
        ):
            try:
                attempts = int(previous.get("attempts") or 0)
            except (TypeError, ValueError):
                attempts = 0
        behavior[REVALIDATION_FAILURE_KEY] = {
            "imageRef": image_ref,
            "credentialGeneration": int(profile.credential_generation),
            "attempts": attempts + 1,
            "exhausted": attempts + 1 >= MAX_REVALIDATION_ATTEMPTS,
            "lastAttemptAt": datetime.now(UTC).isoformat(),
        }
        readiness = dict(behavior.get("auth_readiness") or {})
        readiness.update(
            {
                "launch_ready": False,
                "failure_reason": "Pinned OpenCode runtime validation failed.",
            }
        )
        behavior["auth_readiness"] = readiness
        profile.command_behavior = behavior
        await session.commit()


__all__ = [
    "MAX_OBSERVATION_CLOCK_SKEW",
    "MAX_REVALIDATION_ATTEMPTS",
    "OPENCODE_PROVIDER_ID",
    "OPENCODE_PROVIDER_IDS",
    "OPENCODE_RUNTIME_ID",
    "OPENCODE_SECRET_ROLE",
    "REVALIDATION_FAILURE_KEY",
    "ProviderReconcileOutcome",
    "evidence_is_current",
    "evidence_matches_launchable_identity",
    "evidence_observation_is_current",
    "model_catalog_evidence_identity",
    "reconcile_opencode_provider_readiness",
    "reset_enrollment_attempts",
    "revalidation_is_exhausted",
]
