"""Deployment-configured OpenCode credential readiness.

Two things must hold before an OpenCode target is launchable: a connected
Provider Profile must exist, and its model catalog evidence must have been
observed on the exact digest-pinned host image the deployment selects. These
tests pin the self-healing contract for both — enrollment straight from
``OPENCODE_API_KEY``, and re-validation after the pinned image moves — plus the
boundaries that must not be crossed to get there.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from moonmind.omnigent.bootstrap.provider_revalidation import (
    ProviderReconcileOutcome,
    REVALIDATION_FAILURE_KEY,
    evidence_identity_for_profile,
    evidence_is_current,
    reconcile_opencode_provider_readiness,
    reset_enrollment_attempts,
    revalidation_is_exhausted,
)

CURRENT_IMAGE = "ghcr.io/moonmind/omnigent-host-opencode@sha256:" + "a" * 64
PREVIOUS_IMAGE = "ghcr.io/moonmind/omnigent-host-opencode@sha256:" + "b" * 64
API_KEY = "sk-" + "z" * 40
# Distinguishes "use a fresh observation timestamp" from an explicit ``None``
# that omits the field entirely.
_UNSET_VALIDATED_AT = "__unset__"


@pytest.fixture(autouse=True)
def _clear_enrollment_latch(monkeypatch):
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    reset_enrollment_attempts()
    yield
    reset_enrollment_attempts()


def _profile(
    *,
    profile_id: str = "opencode-go-default",
    generation: int = 3,
    evidence_image: str | None = PREVIOUS_IMAGE,
    evidence_generation: int | None = None,
    enabled: bool = True,
    auth_state: str = "connected",
    secret_refs: dict[str, str] | None = None,
    command_behavior: dict | None = None,
    evidence_validated_at: str | None = _UNSET_VALIDATED_AT,
    provider_id: str = "opencode-go",
    default_model: str = "opencode-go/muse-spark-1.2-contributor",
    credential_source: str = "secret_ref",
    disabled_reason: str | None = None,
) -> SimpleNamespace:
    evidence = None
    if evidence_image is not None:
        evidence = {
            "schemaVersion": "moonmind.provider-model-catalog-evidence.v1",
            "models": [{"qualifiedId": default_model}],
            "imageRef": evidence_image,
            "runtimeVersions": {"opencode": "1.18.11"},
            "materializerRef": (
                "none@1" if provider_id == "opencode" else "opencode-auth-json@1"
            ),
            "credentialGeneration": (
                generation if evidence_generation is None else evidence_generation
            ),
        }
        if evidence_validated_at is _UNSET_VALIDATED_AT:
            evidence["validatedAt"] = datetime.now(UTC).isoformat()
        elif evidence_validated_at is not None:
            evidence["validatedAt"] = evidence_validated_at
    return SimpleNamespace(
        profile_id=profile_id,
        runtime_id="opencode",
        provider_id=provider_id,
        enabled=enabled,
        auth_state=auth_state,
        disabled_reason=disabled_reason,
        credential_source=credential_source,
        credential_generation=generation,
        capacity_scope_ref=None,
        default_model=default_model,
        command_behavior=dict(command_behavior or {}),
        secret_refs=(
            {"opencode_api_key": "db://opencode-key"}
            if secret_refs is None
            else secret_refs
        ),
        model_catalog_evidence_json=evidence,
    )


def _evidence(profile, image_ref: str) -> dict:
    """One well-formed observation for the given profile and host image."""

    return {
        "schemaVersion": "moonmind.provider-model-catalog-evidence.v1",
        "models": [{"qualifiedId": profile.default_model}],
        "imageRef": image_ref,
        "runtimeVersions": {"opencode": "1.18.11"},
        "materializerRef": (
            "none@1" if profile.provider_id == "opencode" else "opencode-auth-json@1"
        ),
        "validatedAt": datetime.now(UTC).isoformat(),
        "credentialGeneration": profile.credential_generation,
    }


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return iter(self._rows)


class _Session:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return _Result(self._rows)

    async def get(self, _model, key):
        return next((row for row in self._rows if row.profile_id == key), None)

    async def commit(self):
        return None


def _session_factory(rows):
    return lambda: _Session(rows)


@pytest.mark.asyncio
async def test_failed_discovery_preserves_enrolled_credential_readiness():
    from moonmind.omnigent.bootstrap.provider_revalidation import _record_revalidation_failure

    profile = _profile(command_behavior={"auth_readiness": {
        "connected": True, "launch_ready": True, "backing_secret_exists": True,
    }})
    await _record_revalidation_failure(
        session_factory=_session_factory([profile]), profile_id=profile.profile_id,
        image_ref=CURRENT_IMAGE,
    )
    assert profile.command_behavior["auth_readiness"]["launch_ready"] is True
    assert profile.auth_state == "connected"


def _install_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    validate=None,
    image_ref: str | None = CURRENT_IMAGE,
    releases: list[str] | None = None,
    acquire_error: Exception | None = None,
    already_held: bool = False,
    acquired: list[str] | None = None,
):
    """Stub the pinned image, credential lease, and runtime validation substrate."""

    def _image() -> str:
        if image_ref is None:
            raise RuntimeError("OMNIGENT_OPENCODE_HOST_IMAGE_REF must be set")
        return image_ref

    class _Guard:
        def __init__(self, profile_id: str) -> None:
            # MoonLadderStudios/MoonMind#3878: the manager reports whether this
            # exact evidence identity was already held by another maintainer.
            self.lease = SimpleNamespace(
                lease_id=f"lease-{profile_id}", already_held=already_held
            )
            self._profile_id = profile_id

        async def release(self) -> None:
            if releases is not None:
                releases.append(self._profile_id)

    async def acquire(*, runtime_id, profile_id, purpose, operation_id, metadata=None):
        del runtime_id, purpose, metadata
        if acquired is not None:
            acquired.append(operation_id)
        if acquire_error is not None:
            raise acquire_error
        return _Guard(profile_id)

    class _ValidationService:
        def __init__(self, *, session_factory, resolver, image_ref):
            del session_factory, resolver
            self.image_ref = image_ref

        async def validate(self, *, profile, lease, **kwargs):
            assert validate is not None, "validation must not run in this scenario"
            return await validate(profile, self.image_ref, lease, kwargs)

    monkeypatch.setattr(
        "moonmind.omnigent.harness_platform.host_classes.get_opencode_host_image_ref",
        _image,
    )
    monkeypatch.setattr(
        "moonmind.provider_profiles.maintenance.acquire_credential_maintenance_guard",
        acquire,
    )
    monkeypatch.setattr(
        "moonmind.omnigent.opencode_runtime_validation."
        "OpenCodeProviderRuntimeValidationService",
        _ValidationService,
    )
    monkeypatch.setattr(
        "moonmind.omnigent.production.build_omnigent_secret_resolver",
        lambda *args, **kwargs: object(),
    )


class _Controller:
    """Records what the canonical bootstrap was asked to enroll."""

    def __init__(self, *, state: str = "ready", error: Exception | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._state = state
        self._error = error

    async def configure_opencode(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(
            state=SimpleNamespace(value=self._state),
            provider_profile_ref="opencode-go-default",
            failure=None if self._state == "ready" else {"message": "rejected"},
        )


def test_evidence_is_current_requires_generation_and_pinned_image() -> None:
    assert evidence_is_current(
        _profile(evidence_image=CURRENT_IMAGE), image_ref=CURRENT_IMAGE
    )
    assert not evidence_is_current(_profile(), image_ref=CURRENT_IMAGE)
    assert not evidence_is_current(
        _profile(evidence_image=CURRENT_IMAGE, evidence_generation=2),
        image_ref=CURRENT_IMAGE,
    )
    assert not evidence_is_current(
        _profile(evidence_image=None), image_ref=CURRENT_IMAGE
    )


def test_evidence_is_current_rejects_fabricated_or_stale_model_catalogs() -> None:
    fabricated = _profile(evidence_image=CURRENT_IMAGE)
    fabricated.model_catalog_evidence_json.pop("runtimeVersions")
    fabricated.model_catalog_evidence_json.pop("materializerRef")
    assert not evidence_is_current(fabricated, image_ref=CURRENT_IMAGE)

    rotated = _profile(evidence_image=CURRENT_IMAGE)
    rotated.model_catalog_evidence_json["models"] = [
        {"qualifiedId": "opencode-go/gpt-5.6-luna"}
    ]
    assert not evidence_is_current(rotated, image_ref=CURRENT_IMAGE)


def test_catalog_observation_expires_by_default() -> None:
    """An unchanged credential and image must not freeze the model catalog.

    The pinned host image refreshes its catalog from the provider at probe
    time, so binding the observation only to credential generation and image
    digest keeps whichever catalog the first probe saw. Models the provider
    publishes later -- contributor tiers included -- would never reach
    selection, readiness, or admission on an otherwise unchanged deployment.
    """

    now = datetime.now(UTC)
    fresh = _profile(
        evidence_image=CURRENT_IMAGE,
        evidence_validated_at=(now - timedelta(hours=1)).isoformat(),
    )
    assert evidence_is_current(fresh, image_ref=CURRENT_IMAGE, env={}, now=now)

    aged = _profile(
        evidence_image=CURRENT_IMAGE,
        evidence_validated_at=(now - timedelta(hours=7)).isoformat(),
    )
    assert not evidence_is_current(aged, image_ref=CURRENT_IMAGE, env={}, now=now)


def test_catalog_observation_interval_is_operator_configurable() -> None:
    now = datetime.now(UTC)
    aged = _profile(
        evidence_image=CURRENT_IMAGE,
        evidence_validated_at=(now - timedelta(hours=7)).isoformat(),
    )
    assert evidence_is_current(
        aged,
        image_ref=CURRENT_IMAGE,
        env={"OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS": "24"},
        now=now,
    )
    # ``0`` restores identity-only staleness for a deployment that pins its
    # catalog deliberately.
    assert evidence_is_current(
        aged,
        image_ref=CURRENT_IMAGE,
        env={"OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS": "0"},
        now=now,
    )


def test_catalog_observation_from_the_future_is_not_current() -> None:
    """A backward clock correction must not keep an old catalog authoritative.

    A ``validatedAt`` ahead of now yields a negative age that satisfies any
    interval for as long as the timestamp stays ahead, so readiness and
    planning would keep admitting models the provider may already have removed
    for days after a VM snapshot restore.
    """

    now = datetime.now(UTC)
    ahead = _profile(
        evidence_image=CURRENT_IMAGE,
        evidence_validated_at=(now + timedelta(days=3)).isoformat(),
    )
    assert not evidence_is_current(ahead, image_ref=CURRENT_IMAGE, env={}, now=now)

    # Ordinary disagreement between the probing host's clock and this one is
    # tolerated rather than forcing a re-probe on every pass.
    skewed = _profile(
        evidence_image=CURRENT_IMAGE,
        evidence_validated_at=(now + timedelta(minutes=1)).isoformat(),
    )
    assert evidence_is_current(skewed, image_ref=CURRENT_IMAGE, env={}, now=now)


def test_catalog_observation_without_a_timestamp_is_not_current() -> None:
    """An observation that cannot state its age cannot be shown to be current."""

    undated = _profile(evidence_image=CURRENT_IMAGE, evidence_validated_at=None)
    assert "validatedAt" not in undated.model_catalog_evidence_json
    assert not evidence_is_current(undated, image_ref=CURRENT_IMAGE, env={})
    # Pinning the interval off keeps the historical identity-only contract for
    # evidence written before the field existed.
    assert evidence_is_current(
        undated,
        image_ref=CURRENT_IMAGE,
        env={"OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS": "0"},
    )


@pytest.mark.asyncio
async def test_configured_api_key_enrolls_the_profile_on_a_cold_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPENCODE_API_KEY alone must produce a launchable Provider Profile."""

    _install_stubs(monkeypatch)
    monkeypatch.setenv("OPENCODE_API_KEY", API_KEY)
    monkeypatch.delenv("OPENCODE_ACCEPT_CONTRIBUTOR_DATA_USE", raising=False)
    controller = _Controller()

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory([]),
        controller=controller,
    )

    assert outcome.ready is True
    assert outcome.enrolled is True
    # The contributor acknowledgement defaults to accepted so the documented
    # one-value setup completes without a console action.
    assert controller.calls == [
        {"api_key": API_KEY, "accept_contributor_data_use": True}
    ]


@pytest.mark.asyncio
async def test_credentialless_zen_does_not_suppress_configured_go_enrollment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stubs(monkeypatch)
    monkeypatch.setenv("OPENCODE_API_KEY", API_KEY)
    controller = _Controller()
    zen = _profile(
        profile_id="opencode-zen-free",
        provider_id="opencode",
        credential_source="none",
        secret_refs={},
        default_model="opencode/muse-spark-1.2-contributor-free",
        evidence_image=CURRENT_IMAGE,
    )

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory([zen]),
        controller=controller,
    )

    assert outcome.ready is True
    assert outcome.enrolled is True
    assert controller.calls == [
        {"api_key": API_KEY, "accept_contributor_data_use": True}
    ]


@pytest.mark.asyncio
async def test_declined_contributor_acknowledgement_is_not_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stubs(monkeypatch)
    monkeypatch.setenv("OPENCODE_API_KEY", API_KEY)
    monkeypatch.setenv("OPENCODE_ACCEPT_CONTRIBUTOR_DATA_USE", "false")
    controller = _Controller()

    await reconcile_opencode_provider_readiness(
        session_factory=_session_factory([]),
        controller=controller,
    )

    assert controller.calls[0]["accept_contributor_data_use"] is False


@pytest.mark.asyncio
async def test_enrollment_waits_for_image_and_catalog_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first-boot race must not consume this configuration's one attempt."""

    _install_stubs(monkeypatch)
    monkeypatch.setenv("OPENCODE_API_KEY", API_KEY)
    controller = _Controller()

    deferred = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory([]),
        allow_enrollment=False,
        controller=controller,
    )
    assert deferred.ready is False
    assert controller.calls == []

    # Once the authorities land, the attempt is still available.
    allowed = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory([]),
        controller=controller,
    )
    assert allowed.enrolled is True
    assert len(controller.calls) == 1


@pytest.mark.asyncio
async def test_transient_enrollment_failures_stay_retryable_within_a_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Temporal, Docker, and the database are startup dependencies."""

    from moonmind.omnigent.bootstrap import provider_revalidation

    _install_stubs(monkeypatch)
    monkeypatch.setenv("OPENCODE_API_KEY", API_KEY)
    controller = _Controller(error=RuntimeError("temporal is unavailable"))

    budget = provider_revalidation._MAX_ENROLLMENT_ATTEMPTS
    for _ in range(budget):
        outcome = await reconcile_opencode_provider_readiness(
            session_factory=_session_factory([]), controller=controller
        )
        assert outcome.ready is False
    # A transient failure keeps retrying rather than latching on the first pass.
    assert len(controller.calls) == budget

    exhausted = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory([]), controller=controller
    )
    assert exhausted.ready is False
    assert "exhausted its attempts" in (exhausted.reason or "")
    assert len(controller.calls) == budget

    # Correcting the credential produces a new configuration with a new budget.
    monkeypatch.setenv("OPENCODE_API_KEY", "sk-" + "y" * 40)
    await reconcile_opencode_provider_readiness(
        session_factory=_session_factory([]), controller=controller
    )
    assert len(controller.calls) == budget + 1


@pytest.mark.asyncio
async def test_invalid_configuration_consumes_the_whole_budget_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying cannot fix a malformed key or a declined acknowledgement."""

    _install_stubs(monkeypatch)
    monkeypatch.setenv("OPENCODE_API_KEY", API_KEY)
    controller = _Controller(error=ValueError("API key appears invalid"))

    first = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory([]), controller=controller
    )
    second = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory([]), controller=controller
    )

    assert "configuration is invalid" in (first.reason or "")
    assert second.ready is False
    assert len(controller.calls) == 1


@pytest.mark.asyncio
async def test_no_configured_credential_leaves_console_enrollment_to_the_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stubs(monkeypatch)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    controller = _Controller()

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory([]), controller=controller
    )

    assert outcome.ready is True
    assert outcome.enrolled is False
    assert controller.calls == []


@pytest.mark.asyncio
async def test_current_evidence_is_left_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stubs(monkeypatch)
    controller = _Controller()
    rows = [_profile(evidence_image=CURRENT_IMAGE)]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=controller
    )

    assert outcome == ProviderReconcileOutcome(ready=True, checked=1)
    assert controller.calls == []


@pytest.mark.asyncio
async def test_targeted_revalidation_ignores_an_unrelated_stale_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default qualification must not inherit another profile's failure."""

    _install_stubs(monkeypatch)
    selected = _profile(
        profile_id="opencode-zen-free",
        evidence_image=CURRENT_IMAGE,
    )
    unrelated = _profile(
        profile_id="opencode-go-default",
        evidence_image=PREVIOUS_IMAGE,
    )

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory([selected, unrelated]),
        profile_ids=(selected.profile_id,),
        controller=_Controller(),
    )

    assert outcome == ProviderReconcileOutcome(ready=True, checked=1)


@pytest.mark.asyncio
async def test_stale_host_image_evidence_is_revalidated_and_refreshed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The connected credential re-qualifies itself against the pinned image."""

    observed: list[tuple[str, str, dict]] = []
    releases: list[str] = []

    async def validate(profile, image_ref, lease, kwargs):
        observed.append((profile.profile_id, image_ref, kwargs))
        assert lease.lease_id == "lease-opencode-go-default"
        return {
            "schemaVersion": "moonmind.provider-model-catalog-evidence.v1",
            "models": [{"qualifiedId": "opencode-go/muse-spark-1.2-contributor"}],
            "imageRef": image_ref,
            "runtimeVersions": {"opencode": "1.18.11"},
            "materializerRef": "opencode-auth-json@1",
            # Production stamps the observation at probe time; a refreshed
            # profile is only current because the catalog was just observed.
            "validatedAt": datetime.now(UTC).isoformat(),
            "credentialGeneration": profile.credential_generation,
        }

    _install_stubs(monkeypatch, validate=validate, releases=releases)
    controller = _Controller()
    rows = [_profile()]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=controller
    )

    assert outcome.ready is True
    assert outcome.refreshed == ("opencode-go-default",)
    assert outcome.deferred == ()
    # An enrolled profile is refreshed in place, never re-bootstrapped.
    assert controller.calls == []
    # No candidate secret is supplied: the enrolled SecretRef stays authoritative.
    assert observed == [("opencode-go-default", CURRENT_IMAGE, {})]
    assert releases == ["opencode-go-default"]
    assert rows[0].model_catalog_evidence_json["imageRef"] == CURRENT_IMAGE
    assert rows[0].command_behavior["runtime_validation"]["image_ref"] == CURRENT_IMAGE
    assert evidence_is_current(rows[0], image_ref=CURRENT_IMAGE)


@pytest.mark.asyncio
async def test_credentialless_zen_is_promoted_only_after_runtime_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qualified_model = "opencode/muse-spark-1.2-contributor-free"

    async def validate(profile, image_ref, _lease, _kwargs):
        return {
            "schemaVersion": "moonmind.provider-model-catalog-evidence.v1",
            "models": [{"qualifiedId": qualified_model}],
            "imageRef": image_ref,
            "runtimeVersions": {"opencode": "1.18.11"},
            "materializerRef": "none@1",
            "validatedAt": datetime.now(UTC).isoformat(),
            "credentialGeneration": profile.credential_generation,
        }

    _install_stubs(monkeypatch, validate=validate)
    controller = _Controller()
    rows = [
        _profile(
            profile_id="opencode-zen-free",
            provider_id="opencode",
            default_model=qualified_model,
            evidence_image=None,
            auth_state="connected",
            credential_source="none",
            secret_refs={},
            command_behavior={
                "auth_state": "connected",
                "auth_readiness": {
                    "connected": True,
                    "backing_secret_exists": False,
                    "launch_ready": False,
                },
            },
        )
    ]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=controller
    )

    assert outcome.ready is True
    assert outcome.refreshed == ("opencode-zen-free",)
    assert controller.calls == []
    assert rows[0].auth_state.value == "connected"
    assert rows[0].command_behavior["auth_readiness"]["launch_ready"] is True
    assert rows[0].command_behavior["auth_readiness"]["backing_secret_exists"] is False
    assert rows[0].model_catalog_evidence_json["models"] == [
        {"qualifiedId": qualified_model}
    ]


@pytest.mark.asyncio
async def test_rotated_default_model_persists_catalog_but_defers_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catalog rotation must not silently substitute a billing-relevant model."""

    async def validate(profile, image_ref, _lease, _kwargs):
        return {
            "schemaVersion": "moonmind.provider-model-catalog-evidence.v1",
            "models": [{"qualifiedId": "opencode-go/gpt-5.6-luna"}],
            "imageRef": image_ref,
            "runtimeVersions": {"opencode": "1.18.11"},
            "materializerRef": "opencode-auth-json@1",
            "validatedAt": "2026-08-27T01:00:00+00:00",
            "credentialGeneration": profile.credential_generation,
        }

    _install_stubs(monkeypatch, validate=validate)
    rows = [_profile(evidence_image=PREVIOUS_IMAGE)]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=_Controller()
    )

    assert outcome.ready is False
    assert outcome.deferred == ("opencode-go-default",)
    assert rows[0].model_catalog_evidence_json["models"] == [
        {"qualifiedId": "opencode-go/gpt-5.6-luna"}
    ]
    assert not evidence_is_current(rows[0], image_ref=CURRENT_IMAGE)


@pytest.mark.asyncio
async def test_rejected_credential_defers_without_downgrading_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def validate(*_args, **_kwargs):
        raise RuntimeError("pinned OpenCode runtime rejected the Provider Profile")

    releases: list[str] = []
    _install_stubs(monkeypatch, validate=validate, releases=releases)
    rows = [_profile()]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=_Controller()
    )

    assert outcome.ready is False
    assert outcome.deferred == ("opencode-go-default",)
    assert outcome.refreshed == ()
    assert rows[0].model_catalog_evidence_json["imageRef"] == PREVIOUS_IMAGE
    assert rows[0].enabled is True
    assert rows[0].auth_state == "connected"
    assert releases == ["opencode-go-default"]


@pytest.mark.asyncio
async def test_unavailable_maintenance_lease_defers_the_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stubs(monkeypatch, acquire_error=RuntimeError("profile is busy"))
    rows = [_profile()]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=_Controller()
    )

    assert outcome.ready is False
    assert outcome.deferred == ("opencode-go-default",)
    assert rows[0].model_catalog_evidence_json["imageRef"] == PREVIOUS_IMAGE


@pytest.mark.asyncio
async def test_lease_failures_never_consume_the_revalidation_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lease contention is not a provider verdict, so it must stay retryable.

    ``acquire_credential_maintenance_guard`` fails on maintainer contention and
    on transient Temporal/profile-manager errors, and no provider probe runs in
    either case. Charging those to ``MAX_REVALIDATION_ATTEMPTS`` would let three
    of them mark the identity exhausted, after which the reconciler skips the
    profile entirely until its credential or host image changes.
    """

    from moonmind.omnigent.bootstrap.provider_revalidation import (
        MAX_REVALIDATION_ATTEMPTS,
        REVALIDATION_FAILURE_KEY,
        revalidation_is_exhausted,
    )

    _install_stubs(monkeypatch, acquire_error=RuntimeError("profile is busy"))
    rows = [_profile()]

    for _ in range(MAX_REVALIDATION_ATTEMPTS + 1):
        outcome = await reconcile_opencode_provider_readiness(
            session_factory=_session_factory(rows), controller=_Controller()
        )
        assert outcome.deferred == ("opencode-go-default",)

    assert REVALIDATION_FAILURE_KEY not in (rows[0].command_behavior or {})
    assert not revalidation_is_exhausted(rows[0], image_refs=(CURRENT_IMAGE,))

    # The full budget is intact, so the very next pass still probes the
    # provider once the lease is available again.
    probed: list[str] = []

    async def validate(profile, image_ref, _lease, _kwargs):
        probed.append(profile.profile_id)
        return {
            "schemaVersion": "moonmind.provider-model-catalog-evidence.v1",
            "models": [{"qualifiedId": "opencode-go/muse-spark-1.2-contributor"}],
            "imageRef": image_ref,
            "runtimeVersions": {"opencode": "1.18.11"},
            "materializerRef": "opencode-auth-json@1",
            "validatedAt": datetime.now(UTC).isoformat(),
            "credentialGeneration": profile.credential_generation,
        }

    _install_stubs(monkeypatch, validate=validate)
    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=_Controller()
    )

    assert probed == ["opencode-go-default"]
    assert outcome.refreshed == ("opencode-go-default",)


@pytest.mark.asyncio
async def test_missing_pinned_image_defers_instead_of_recording_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stubs(monkeypatch, image_ref=None)
    rows = [_profile()]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=_Controller()
    )

    assert outcome.ready is False
    assert outcome.reason == "pinned OpenCode host image unavailable"
    assert rows[0].model_catalog_evidence_json["imageRef"] == PREVIOUS_IMAGE


@pytest.mark.asyncio
async def test_unenrolled_profile_rows_are_re_enrolled_from_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row that exists but never connected is not mistaken for an enrollment."""

    _install_stubs(monkeypatch)
    monkeypatch.setenv("OPENCODE_API_KEY", API_KEY)
    controller = _Controller()
    rows = [
        _profile(profile_id="never-connected", auth_state="api_key_pending"),
        _profile(profile_id="no-secret", secret_refs={}),
    ]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=controller
    )

    assert outcome.enrolled is True
    assert outcome.checked == 2
    assert len(controller.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled_reason", ["user_disabled", "policy_disabled"])
async def test_authoritatively_disabled_profile_is_never_re_enabled_by_configuration(
    monkeypatch: pytest.MonkeyPatch,
    disabled_reason: str,
) -> None:
    """Enrollment must preserve explicit user and policy disable authority."""

    _install_stubs(monkeypatch)
    monkeypatch.setenv("OPENCODE_API_KEY", API_KEY)
    controller = _Controller()
    # Connected and enrolled, but the operator turned it off. Its evidence is
    # also stale, which must not become a reason to touch it either.
    rows = [_profile(enabled=False, disabled_reason=disabled_reason)]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=controller
    )

    assert controller.calls == []
    assert outcome.enrolled is False
    assert outcome.refreshed == ()
    assert outcome.ready is True
    assert rows[0].enabled is False


@pytest.mark.asyncio
async def test_deployment_configuration_repairs_non_authoritative_disabled_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restart repairs partial enrollment without overriding user intent."""

    _install_stubs(monkeypatch)
    monkeypatch.setenv("OPENCODE_API_KEY", API_KEY)
    controller = _Controller()
    rows = [_profile(enabled=False, disabled_reason="auth_invalid")]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=controller
    )

    assert len(controller.calls) == 1
    assert outcome.enrolled is True
    assert outcome.checked == 1


@pytest.mark.asyncio
async def test_credential_rotation_during_validation_keeps_rotation_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generation that moved mid-flight owns its own evidence."""

    async def validate(profile, image_ref, _lease, _kwargs):
        # The rotation lands while the pinned runtime check is in flight.
        profile.credential_generation = 4
        return {
            "schemaVersion": "moonmind.provider-model-catalog-evidence.v1",
            "models": [{"qualifiedId": "opencode-go/muse-spark-1.2-contributor"}],
            "imageRef": image_ref,
            "validatedAt": "2026-08-25T01:00:00+00:00",
            "credentialGeneration": 3,
        }

    _install_stubs(monkeypatch, validate=validate)
    rows = [_profile()]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=_Controller()
    )

    assert rows[0].model_catalog_evidence_json["imageRef"] == PREVIOUS_IMAGE
    # The write did not land, so the pass must not claim the profile refreshed.
    assert outcome.refreshed == ()
    assert outcome.deferred == ("opencode-go-default",)
    assert outcome.ready is False


@pytest.mark.asyncio
async def test_repeated_rejection_is_recorded_and_marked_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A revoked credential must become distinguishable from a wait in flight."""

    from moonmind.omnigent.bootstrap.provider_revalidation import (
        MAX_REVALIDATION_ATTEMPTS,
        REVALIDATION_FAILURE_KEY,
    )

    async def validate(*_args, **_kwargs):
        raise RuntimeError("pinned OpenCode runtime rejected the Provider Profile")

    _install_stubs(monkeypatch, validate=validate)
    rows = [_profile()]

    for _ in range(MAX_REVALIDATION_ATTEMPTS):
        await reconcile_opencode_provider_readiness(
            session_factory=_session_factory(rows), controller=_Controller()
        )

    record = rows[0].command_behavior[REVALIDATION_FAILURE_KEY]
    assert record["attempts"] == MAX_REVALIDATION_ATTEMPTS
    assert record["exhausted"] is True
    assert record["imageRef"] == CURRENT_IMAGE
    assert record["credentialGeneration"] == 3
    # The credential itself is untouched; only an operator can reconnect it.
    assert rows[0].auth_state == "connected"
    assert rows[0].model_catalog_evidence_json["imageRef"] == PREVIOUS_IMAGE


@pytest.mark.asyncio
async def test_a_successful_refresh_clears_a_recorded_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.omnigent.bootstrap.provider_revalidation import (
        REVALIDATION_FAILURE_KEY,
    )

    async def validate(profile, image_ref, _lease, _kwargs):
        return {
            "schemaVersion": "moonmind.provider-model-catalog-evidence.v1",
            "models": [{"qualifiedId": "opencode-go/muse-spark-1.2-contributor"}],
            "imageRef": image_ref,
            "runtimeVersions": {"opencode": "1.18.11"},
            "materializerRef": "opencode-auth-json@1",
            "validatedAt": "2026-08-25T01:00:00+00:00",
            "credentialGeneration": profile.credential_generation,
        }

    _install_stubs(monkeypatch, validate=validate)
    rows = [
        _profile(
            command_behavior={
                REVALIDATION_FAILURE_KEY: {
                    "imageRef": CURRENT_IMAGE,
                    "credentialGeneration": 3,
                    "attempts": 1,
                    "exhausted": False,
                }
            }
        )
    ]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=_Controller()
    )

    assert outcome.refreshed == ("opencode-go-default",)
    assert REVALIDATION_FAILURE_KEY not in rows[0].command_behavior


@pytest.mark.asyncio
async def test_exhausted_revalidation_stops_probing_the_pinned_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recorded attempt budget must bound provider work, not just reporting.

    The startup maintainer re-enters this boundary every 120 seconds for as
    long as readiness stays false, so a provider outage or a revoked key would
    otherwise launch a Docker-backed probe forever.
    """

    from moonmind.omnigent.bootstrap.provider_revalidation import (
        REVALIDATION_FAILURE_KEY,
    )

    # ``validate=None`` makes the stub assert if the runtime probe is reached.
    _install_stubs(monkeypatch)
    rows = [
        _profile(
            command_behavior={
                REVALIDATION_FAILURE_KEY: {
                    "imageRef": CURRENT_IMAGE,
                    "credentialGeneration": 3,
                    "attempts": 3,
                    "exhausted": True,
                }
            }
        )
    ]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=_Controller()
    )

    assert outcome.refreshed == ()
    assert outcome.deferred == ("opencode-go-default",)
    # Not "ready": the profile still cannot launch, and only an operator can
    # change that. The pass simply stops paying for the same answer.
    assert outcome.ready is False
    assert "exhausted" in (outcome.reason or "")
    assert rows[0].command_behavior[REVALIDATION_FAILURE_KEY]["attempts"] == 3


@pytest.mark.asyncio
async def test_an_expired_catalog_stops_probing_once_attempts_are_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interval-driven staleness inherits the same bounded attempt budget."""

    from moonmind.omnigent.bootstrap.provider_revalidation import (
        MAX_REVALIDATION_ATTEMPTS,
        REVALIDATION_FAILURE_KEY,
    )

    attempts: list[str] = []

    async def validate(profile, image_ref, _lease, _kwargs):
        del profile, image_ref
        attempts.append("probe")
        raise RuntimeError("provider catalog service is unavailable")

    _install_stubs(monkeypatch, validate=validate)
    aged = (datetime.now(UTC) - timedelta(hours=9)).isoformat()
    rows = [_profile(evidence_image=CURRENT_IMAGE, evidence_validated_at=aged)]

    for _ in range(MAX_REVALIDATION_ATTEMPTS + 3):
        outcome = await reconcile_opencode_provider_readiness(
            session_factory=_session_factory(rows), controller=_Controller()
        )

    assert len(attempts) == MAX_REVALIDATION_ATTEMPTS
    assert rows[0].command_behavior[REVALIDATION_FAILURE_KEY]["exhausted"] is True
    assert outcome.ready is False
    assert outcome.deferred == ("opencode-go-default",)


@pytest.mark.asyncio
async def test_a_new_pinned_image_restores_the_revalidation_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhaustion is scoped to the identity it was earned against."""

    from moonmind.omnigent.bootstrap.provider_revalidation import (
        REVALIDATION_FAILURE_KEY,
    )

    async def validate(profile, image_ref, _lease, _kwargs):
        return {
            "schemaVersion": "moonmind.provider-model-catalog-evidence.v1",
            "models": [{"qualifiedId": "opencode-go/muse-spark-1.2-contributor"}],
            "imageRef": image_ref,
            "runtimeVersions": {"opencode": "1.18.11"},
            "materializerRef": "opencode-auth-json@1",
            "validatedAt": datetime.now(UTC).isoformat(),
            "credentialGeneration": profile.credential_generation,
        }

    _install_stubs(monkeypatch, validate=validate)
    rows = [
        _profile(
            command_behavior={
                REVALIDATION_FAILURE_KEY: {
                    # Earned against the image the deployment no longer pins.
                    "imageRef": PREVIOUS_IMAGE,
                    "credentialGeneration": 3,
                    "attempts": 3,
                    "exhausted": True,
                }
            }
        )
    ]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=_Controller()
    )

    assert outcome.refreshed == ("opencode-go-default",)
    assert outcome.ready is True
    assert REVALIDATION_FAILURE_KEY not in rows[0].command_behavior


def test_revalidation_is_exhausted_is_scoped_to_image_and_generation() -> None:
    from moonmind.omnigent.bootstrap.provider_revalidation import (
        REVALIDATION_FAILURE_KEY,
        revalidation_is_exhausted,
    )

    def _record(**overrides):
        record = {
            "imageRef": CURRENT_IMAGE,
            "credentialGeneration": 3,
            "attempts": 3,
            "exhausted": True,
        }
        record.update(overrides)
        return _profile(command_behavior={REVALIDATION_FAILURE_KEY: record})

    assert revalidation_is_exhausted(_record(), image_refs=(CURRENT_IMAGE,))
    assert not revalidation_is_exhausted(_record(), image_refs=(PREVIOUS_IMAGE,))
    assert not revalidation_is_exhausted(
        _record(credentialGeneration=2), image_refs=(CURRENT_IMAGE,)
    )
    assert not revalidation_is_exhausted(
        _record(exhausted=False), image_refs=(CURRENT_IMAGE,)
    )
    assert not revalidation_is_exhausted(_profile(), image_refs=(CURRENT_IMAGE,))


def test_evidence_observation_is_current_is_the_shared_admission_predicate() -> None:
    """Every admission boundary asks this one question about observation age."""

    from moonmind.omnigent.bootstrap.provider_revalidation import (
        evidence_observation_is_current,
    )

    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    fresh = {"validatedAt": (now - timedelta(hours=5)).isoformat()}
    aged = {"validatedAt": (now - timedelta(hours=7)).isoformat()}

    assert evidence_observation_is_current(fresh, env={}, now=now)
    assert not evidence_observation_is_current(aged, env={}, now=now)
    # An explicit interval overrides the default in both directions.
    assert evidence_observation_is_current(
        aged, env={"OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS": "12"}, now=now
    )
    assert not evidence_observation_is_current(
        fresh, env={"OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS": "1"}, now=now
    )
    # ``0`` restores identity-only staleness for every boundary at once.
    assert evidence_observation_is_current(
        aged, env={"OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS": "0"}, now=now
    )
    # Evidence that cannot state when it was observed is never current.
    assert not evidence_observation_is_current({}, env={}, now=now)
    assert not evidence_observation_is_current(None, env={}, now=now)
    # Neither is evidence stamped beyond the clock-skew tolerance into the
    # future, at every boundary and for every configured interval.
    ahead = {"validatedAt": (now + timedelta(days=3)).isoformat()}
    assert not evidence_observation_is_current(ahead, env={}, now=now)
    assert not evidence_observation_is_current(
        ahead, env={"OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS": "720"}, now=now
    )
    assert evidence_observation_is_current(
        {"validatedAt": (now + timedelta(minutes=1)).isoformat()}, env={}, now=now
    )


# ---------------------------------------------------------------------------
# MoonLadderStudios/MoonMind#3878: single-flight credentialless re-validation
# ---------------------------------------------------------------------------


def test_evidence_identity_is_stable_for_one_refresh() -> None:
    """Two maintainers racing the same refresh must derive the same owner."""

    from moonmind.omnigent.bootstrap.provider_revalidation import (
        MODEL_CATALOG_PROBE_CONTRACT_VERSION,
        model_catalog_evidence_identity,
    )

    kwargs = {
        "profile_id": "opencode-zen-free",
        "image_ref": CURRENT_IMAGE,
        "credential_generation": 4,
        "materializer_ref": "none@1",
        "provider_route": "opencode",
        "model_route": "opencode/muse-spark-1.2-contributor",
    }

    first = model_catalog_evidence_identity(**kwargs)
    second = model_catalog_evidence_identity(**kwargs)

    assert first == second
    assert first.startswith(
        f"opencode-model-catalog:{MODEL_CATALOG_PROBE_CONTRACT_VERSION}:"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profile_id", "another-profile"),
        ("image_ref", PREVIOUS_IMAGE),
        ("credential_generation", 5),
        # MoonLadderStudios/MoonMind#3879: the identity must cover every input
        # the launch boundary later compares, or one probe's result would stand
        # in for a question it never asked.
        ("materializer_ref", "opencode-auth-json@1"),
        ("provider_route", "opencode-go"),
        ("model_route", "opencode/some-other-model"),
        ("probe_contract_version", "v1"),
    ],
)
def test_a_different_refresh_is_a_different_identity(field: str, value: object) -> None:
    """A re-pinned image or rotated credential must never be blocked as a dupe."""

    from moonmind.omnigent.bootstrap.provider_revalidation import (
        model_catalog_evidence_identity,
    )

    base = {
        "profile_id": "opencode-zen-free",
        "image_ref": CURRENT_IMAGE,
        "credential_generation": 4,
        "materializer_ref": "none@1",
        "provider_route": "opencode",
        "model_route": "opencode/muse-spark-1.2-contributor",
    }
    changed = {**base, field: value}

    assert model_catalog_evidence_identity(
        **base
    ) != model_catalog_evidence_identity(**changed)


@pytest.mark.asyncio
async def test_revalidation_leases_the_evidence_identity_not_a_fresh_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A random operation id would make every maintainer a distinct owner."""

    from moonmind.omnigent.bootstrap.provider_revalidation import (
        evidence_identity_for_profile,
    )

    async def validate(profile, image_ref, _lease, _kwargs):
        return {
            "schemaVersion": "moonmind.provider-model-catalog-evidence.v1",
            "models": [{"qualifiedId": profile.default_model}],
            "imageRef": image_ref,
            "runtimeVersions": {"opencode": "1.18.11"},
            "materializerRef": "opencode-auth-json@1",
            "validatedAt": datetime.now(UTC).isoformat(),
            "credentialGeneration": profile.credential_generation,
        }

    acquired: list[str] = []
    _install_stubs(monkeypatch, validate=validate, acquired=acquired)
    rows = [_profile(evidence_image=PREVIOUS_IMAGE)]

    await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=_Controller()
    )

    assert acquired == [
        evidence_identity_for_profile(rows[0], image_ref=CURRENT_IMAGE)
    ]


@pytest.mark.asyncio
async def test_an_in_flight_refresh_stands_down_without_re_probing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant 3: the second maintainer defers instead of duplicating load."""

    _install_stubs(
        monkeypatch,
        validate=None,  # asserts inside _ValidationService if it ever runs
        already_held=True,
    )
    rows = [_profile(evidence_image=PREVIOUS_IMAGE)]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=_Controller()
    )

    assert outcome.deferred == ("opencode-go-default",)
    assert outcome.refreshed == ()
    # The holder's evidence is untouched; standing down published nothing.
    assert rows[0].model_catalog_evidence_json["imageRef"] == PREVIOUS_IMAGE


@pytest.mark.asyncio
async def test_standing_down_never_consumes_the_revalidation_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deferring to a holder is not a provider verdict and must stay retryable."""

    from moonmind.omnigent.bootstrap.provider_revalidation import (
        MAX_REVALIDATION_ATTEMPTS,
        REVALIDATION_FAILURE_KEY,
        revalidation_is_exhausted,
    )

    _install_stubs(monkeypatch, validate=None, already_held=True)
    rows = [_profile(evidence_image=PREVIOUS_IMAGE)]

    for _ in range(MAX_REVALIDATION_ATTEMPTS + 1):
        outcome = await reconcile_opencode_provider_readiness(
            session_factory=_session_factory(rows), controller=_Controller()
        )
        assert outcome.deferred == ("opencode-go-default",)

    assert REVALIDATION_FAILURE_KEY not in (rows[0].command_behavior or {})
    assert not revalidation_is_exhausted(rows[0], image_refs=(CURRENT_IMAGE,))


# ---------------------------------------------------------------------------
# MoonLadderStudios/MoonMind#3879: proactive refresh under sustained load
# ---------------------------------------------------------------------------


def test_the_refresh_lead_defaults_to_a_bounded_fraction_of_the_interval() -> None:
    """The omitted value takes the same path as an explicit one."""

    from moonmind.omnigent.settings import (
        OPENCODE_MODEL_CATALOG_MAX_DERIVED_REFRESH_LEAD,
        opencode_model_catalog_refresh_lead,
    )

    short = opencode_model_catalog_refresh_lead(
        env={"OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS": "0.5"}
    )
    long = opencode_model_catalog_refresh_lead(
        env={"OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS": "24"}
    )

    assert short == timedelta(minutes=3)
    # The derived lead is bounded: a long interval does not start refreshing
    # hours early and spend provider attempts on evidence that is still good.
    assert long == OPENCODE_MODEL_CATALOG_MAX_DERIVED_REFRESH_LEAD


def test_an_operator_owns_the_refresh_lead_and_it_stays_bounded() -> None:
    """A slow probe or a distant provider is a deployment fact, not a constant."""

    from moonmind.omnigent.settings import opencode_model_catalog_refresh_lead

    widened = opencode_model_catalog_refresh_lead(
        env={
            "OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS": "6",
            "OPENCODE_MODEL_CATALOG_REFRESH_LEAD_MINUTES": "45",
        }
    )
    absurd = opencode_model_catalog_refresh_lead(
        env={
            "OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS": "6",
            "OPENCODE_MODEL_CATALOG_REFRESH_LEAD_MINUTES": "600",
        }
    )
    disabled_interval = opencode_model_catalog_refresh_lead(
        env={"OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS": "0"}
    )

    assert widened == timedelta(minutes=45)
    # A lead at or beyond the interval would make every observation stale the
    # moment it was written, so it is clamped rather than obeyed.
    assert absurd == timedelta(hours=3)
    # No interval means identity is the only staleness rule; nothing to lead.
    assert disabled_interval == timedelta(0)


def test_an_invalid_refresh_lead_fails_fast() -> None:
    from moonmind.omnigent.settings import opencode_model_catalog_refresh_lead

    with pytest.raises(ValueError):
        opencode_model_catalog_refresh_lead(
            env={"OPENCODE_MODEL_CATALOG_REFRESH_LEAD_MINUTES": "-1"}
        )
    with pytest.raises(ValueError):
        opencode_model_catalog_refresh_lead(
            env={"OPENCODE_MODEL_CATALOG_REFRESH_LEAD_MINUTES": "soon"}
        )


@pytest.mark.asyncio
async def test_the_deployment_owned_lead_decides_when_a_refresh_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evidence inside the interval but inside the lead is refreshed early."""

    probes: list[str] = []

    async def validate(profile, image_ref, _lease, _kwargs):
        probes.append(profile.profile_id)
        return _evidence(profile, image_ref)

    _install_stubs(monkeypatch, validate=validate)
    monkeypatch.setenv("OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS", "6")
    # 5h50m old: current now, stale in ten minutes.
    observed = (datetime.now(UTC) - timedelta(hours=5, minutes=50)).isoformat()

    monkeypatch.delenv(
        "OPENCODE_MODEL_CATALOG_REFRESH_LEAD_MINUTES", raising=False
    )
    default_rows = [
        _profile(evidence_image=CURRENT_IMAGE, evidence_validated_at=observed)
    ]
    await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(default_rows), controller=_Controller()
    )
    assert probes == [], "the default 5 minute lead has not been reached yet"

    monkeypatch.setenv("OPENCODE_MODEL_CATALOG_REFRESH_LEAD_MINUTES", "30")
    widened_rows = [
        _profile(evidence_image=CURRENT_IMAGE, evidence_validated_at=observed)
    ]
    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(widened_rows), controller=_Controller()
    )

    assert probes == ["opencode-go-default"]
    assert outcome.refreshed == ("opencode-go-default",)


@pytest.mark.asyncio
async def test_a_probe_that_completes_against_a_superseded_image_is_not_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under load a probe can finish after the deployment re-pinned its host."""

    async def validate(profile, image_ref, _lease, _kwargs):
        # The deployment re-pins its host image while this probe is running.
        monkeypatch.setattr(
            "moonmind.omnigent.harness_platform.host_classes."
            "get_opencode_host_image_ref",
            lambda: PREVIOUS_IMAGE,
        )
        return _evidence(profile, image_ref)

    _install_stubs(monkeypatch, validate=validate)
    rows = [_profile(evidence_image=None)]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=_Controller()
    )

    assert outcome.refreshed == ()
    assert outcome.deferred == ("opencode-go-default",)
    # Nothing was promoted on evidence the current launch boundary rejects.
    assert rows[0].model_catalog_evidence_json is None


@pytest.mark.asyncio
async def test_evidence_observed_on_another_image_is_never_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The commit boundary re-checks the identity the probe answered for."""

    async def validate(profile, _image_ref, _lease, _kwargs):
        return _evidence(profile, PREVIOUS_IMAGE)

    _install_stubs(monkeypatch, validate=validate)
    rows = [_profile(evidence_image=None)]

    outcome = await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=_Controller()
    )

    assert outcome.refreshed == ()
    assert outcome.deferred == ("opencode-go-default",)
    assert rows[0].model_catalog_evidence_json is None


@pytest.mark.asyncio
async def test_sustained_load_refreshes_once_per_interval_not_once_per_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The startup maintainer re-enters this boundary every two minutes."""

    probes: list[str] = []

    async def validate(profile, image_ref, _lease, _kwargs):
        probes.append(profile.profile_id)
        return _evidence(profile, image_ref)

    _install_stubs(monkeypatch, validate=validate)
    monkeypatch.setenv("OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS", "6")
    rows = [_profile(evidence_image=PREVIOUS_IMAGE)]

    for _ in range(25):
        outcome = await reconcile_opencode_provider_readiness(
            session_factory=_session_factory(rows), controller=_Controller()
        )

    # One probe refreshed the identity; every later pass reads the committed
    # observation and reports ready without spending another provider attempt.
    assert probes == ["opencode-go-default"]
    assert outcome.ready is True
    assert outcome.refreshed == ()


@pytest.mark.asyncio
async def test_standing_down_does_not_release_the_holders_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Releasing would cancel the authority of the maintainer still probing."""

    releases: list[str] = []
    _install_stubs(
        monkeypatch, validate=None, already_held=True, releases=releases
    )
    rows = [_profile(evidence_image=PREVIOUS_IMAGE)]

    await reconcile_opencode_provider_readiness(
        session_factory=_session_factory(rows), controller=_Controller()
    )

    assert releases == []


def test_exhaustion_is_scoped_to_the_complete_evidence_identity() -> None:
    """A changed model, route, materializer, or contract restarts the budget."""

    profile = _profile()
    identity = evidence_identity_for_profile(profile, image_ref=CURRENT_IMAGE)
    profile.command_behavior = {
        REVALIDATION_FAILURE_KEY: {
            "imageRef": CURRENT_IMAGE,
            "credentialGeneration": profile.credential_generation,
            "evidenceIdentity": identity,
            "attempts": 3,
            "exhausted": True,
        }
    }
    assert revalidation_is_exhausted(profile, image_refs=(CURRENT_IMAGE,)) is True

    profile.default_model = "opencode-go/some-other-model"
    assert revalidation_is_exhausted(profile, image_refs=(CURRENT_IMAGE,)) is False


def test_exhaustion_without_a_stored_identity_keeps_the_legacy_key() -> None:
    """Records written before the identity still exhaust on image+generation."""

    profile = _profile()
    profile.command_behavior = {
        REVALIDATION_FAILURE_KEY: {
            "imageRef": CURRENT_IMAGE,
            "credentialGeneration": profile.credential_generation,
            "attempts": 3,
            "exhausted": True,
        }
    }
    assert revalidation_is_exhausted(profile, image_refs=(CURRENT_IMAGE,)) is True


@pytest.mark.asyncio
async def test_failure_budget_is_counted_per_evidence_identity() -> None:
    from moonmind.omnigent.bootstrap.provider_revalidation import (
        _record_revalidation_failure,
    )

    profile = _profile()
    factory = _session_factory([profile])
    await _record_revalidation_failure(
        session_factory=factory,
        profile_id=profile.profile_id,
        image_ref=CURRENT_IMAGE,
        evidence_identity="identity-a",
    )
    await _record_revalidation_failure(
        session_factory=factory,
        profile_id=profile.profile_id,
        image_ref=CURRENT_IMAGE,
        evidence_identity="identity-a",
    )
    await _record_revalidation_failure(
        session_factory=factory,
        profile_id=profile.profile_id,
        image_ref=CURRENT_IMAGE,
        evidence_identity="identity-a",
    )
    record = profile.command_behavior[REVALIDATION_FAILURE_KEY]
    assert record["evidenceIdentity"] == "identity-a"
    assert record["attempts"] == 3
    assert record["exhausted"] is True

    # Genuinely new validation work restarts the budget instead of inheriting
    # the old identity's exhaustion.
    await _record_revalidation_failure(
        session_factory=factory,
        profile_id=profile.profile_id,
        image_ref=CURRENT_IMAGE,
        evidence_identity="identity-b",
    )
    record = profile.command_behavior[REVALIDATION_FAILURE_KEY]
    assert record["evidenceIdentity"] == "identity-b"
    assert record["attempts"] == 1
    assert record["exhausted"] is False
