"""Credential-free container journey for OpenCode reliability (MoonMind#4526).

Exercises the normal Profile -> OpenCode validation -> persistence ->
launch/admission boundary with controlled external responses and isolated
``moonmind-test-*`` resources. No production credentials are used; the
credentialless Zen route (``none@1``) carries no secret.

Scenario:
  * Requested image A is unavailable; trusted compatible image B (same
    repository) is installed.
  * The first catalog probe fails with a transient infrastructure error
    (connection refused), then recovers with unchanged configuration.
  * The journey asserts B is used throughout with truthful evidence,
    the transient cause never becomes ``auth_invalid``, and the later
    reconcile succeeds without duplicate launch or double-committed
    generation.

SQLite is real; Docker/registry responses are scripted at the container
boundary. Safe for required CI (``integration_ci``).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ProviderCredentialSource,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
)
from moonmind.omnigent.bootstrap.opencode import ZEN_FREE_QUALIFIED

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

PROFILE_ID = "moonmind-test-opencode-4526"
REQUESTED_IMAGE = "ghcr.io/moonmind-test/opencode@sha256:" + "a" * 64
EFFECTIVE_IMAGE = "ghcr.io/moonmind-test/opencode@sha256:" + "b" * 64


@pytest.fixture()
def _journey_db(tmp_path, monkeypatch):
    """Isolated SQLite DB; never the deployment project."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/journey_4526.db"

    async def _setup():
        engine = create_async_engine(db_url, future=True)
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, maker

    engine, maker = asyncio.run(_setup())
    orig = (db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker)
    db_base.DATABASE_URL = db_url
    db_base.engine = engine
    db_base.async_session_maker = maker
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    try:
        yield maker
    finally:
        db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker = orig
        asyncio.run(engine.dispose())


class _FakeDockerBackend:
    """Controlled registry/image responses: A missing, B installed."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.catalog_calls = 0

    async def run(self, argv, **kwargs):
        self.commands.append(list(argv))
        text = " ".join(argv)
        if argv[1:3] == ["image", "inspect"]:
            if REQUESTED_IMAGE in argv:
                return 1, b"", b"Error: No such image"
            if EFFECTIVE_IMAGE in argv:
                return 0, b"sha256:" + b"b" * 64 + b"\n", b""
            return 1, b"", b"Error: No such image"
        if "models --refresh" in text:
            self.catalog_calls += 1
            if self.catalog_calls == 1:
                return 1, b"", b"dial tcp: connection refused"
            return 0, f"{ZEN_FREE_QUALIFIED}\n".encode(), b""
        return 0, b"1.18.11\n", b""


class _FakeArtifacts:
    async def write_json(self, **kwargs):
        return "artifact:moonmind-test-attestation"


async def _seed_credentialless_profile(maker) -> None:
    async with maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=PROFILE_ID,
                runtime_id="opencode",
                provider_id="opencode",
                provider_label="OpenCode Zen (moonmind-test)",
                default_model=ZEN_FREE_QUALIFIED,
                default_effort="xhigh",
                credential_source=ProviderCredentialSource.NONE,
                runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
                secret_refs={},
                enabled=True,
                is_default=False,
                auth_state=ProviderProfileAuthState.CONNECTED,
                credential_generation=1,
                command_behavior={},
            )
        )
        await session.commit()


async def test_opencode_reliability_journey_uses_compatible_image_and_recovers(
    _journey_db, monkeypatch
) -> None:
    """A-unavailable/B-installed plus transient-then-recovery, end to end."""

    from moonmind.omnigent import opencode_runtime_validation as validation_module
    from moonmind.omnigent.bootstrap import provider_revalidation
    from moonmind.omnigent.bootstrap.provider_revalidation import (
        REVALIDATION_FAILURE_KEY,
        evidence_is_current,
        evidence_matches_launchable_identity,
        reconcile_opencode_provider_readiness,
    )
    from moonmind.omnigent.harness_platform import host_classes

    maker = _journey_db
    await _seed_credentialless_profile(maker)

    backend = _FakeDockerBackend()
    real_service = validation_module.OpenCodeProviderRuntimeValidationService

    def _service_factory(*, session_factory, resolver, image_ref):
        assert image_ref == REQUESTED_IMAGE
        return real_service(
            session_factory=session_factory,
            resolver=resolver,
            image_ref=image_ref,
            backend=backend,
            artifact_gateway=_FakeArtifacts(),
        )

    monkeypatch.setattr(
        validation_module,
        "OpenCodeProviderRuntimeValidationService",
        _service_factory,
    )
    monkeypatch.setattr(
        host_classes, "get_opencode_host_image_ref", lambda: REQUESTED_IMAGE
    )
    monkeypatch.setattr(
        "moonmind.omnigent.opencode_runtime_validation.compatible_deployed_fallback",
        lambda requested, **kwargs: (
            EFFECTIVE_IMAGE if requested == REQUESTED_IMAGE else None
        ),
    )

    releases: list[str] = []

    class _Guard:
        def __init__(self, profile_id: str) -> None:
            self.lease = SimpleNamespace(
                lease_id=f"lease-{profile_id}", already_held=False
            )
            self._profile_id = profile_id

        async def release(self) -> None:
            releases.append(self._profile_id)

    async def _acquire(**kwargs):
        return _Guard(kwargs["profile_id"])

    monkeypatch.setattr(
        "moonmind.provider_profiles.maintenance.acquire_credential_maintenance_guard",
        _acquire,
    )

    # Pass 1: transient infrastructure failure. No verdict about the
    # credential, so the attempt budget stays intact and nothing persists
    # auth_invalid or a disabled profile.
    first = await reconcile_opencode_provider_readiness(
        session_factory=maker, controller=SimpleNamespace()
    )
    assert first.ready is False
    assert PROFILE_ID in first.deferred
    assert first.refreshed == ()

    async with maker() as session:
        row = await session.get(ManagedAgentProviderProfile, PROFILE_ID)
        assert row is not None
        assert REVALIDATION_FAILURE_KEY not in (row.command_behavior or {})
        assert row.auth_state == ProviderProfileAuthState.CONNECTED
        assert row.enabled is True
        assert row.disabled_reason is None
        assert row.credential_generation == 1
        assert row.model_catalog_evidence_json is None

    # The transient cause is classified retryably, never as credential
    # rejection.
    from moonmind.omnigent.opencode_runtime_validation import (
        is_confirmed_credential_rejection,
        is_transient_validation_error,
    )

    transient = validation_module._catalog_probe_failure(
        exit_code=1,
        stderr=b"dial tcp: connection refused",
        effective_image_ref=EFFECTIVE_IMAGE,
    )
    assert is_transient_validation_error(transient) is True
    assert is_confirmed_credential_rejection(transient) is False

    # Pass 2: unchanged configuration recovers; the compatible image is used
    # throughout with truthful evidence and a single committed generation.
    second = await reconcile_opencode_provider_readiness(
        session_factory=maker, controller=SimpleNamespace()
    )
    assert second.refreshed == (PROFILE_ID,)
    assert second.deferred == ()
    assert second.ready is True

    async with maker() as session:
        row = await session.get(ManagedAgentProviderProfile, PROFILE_ID)
        assert row is not None
        evidence = row.model_catalog_evidence_json
        assert evidence["imageRef"] == EFFECTIVE_IMAGE
        assert evidence["requestedImageRef"] == REQUESTED_IMAGE
        assert evidence["credentialGeneration"] == 1
        assert evidence["materializerRef"] == "none@1"
        assert {"qualifiedId": ZEN_FREE_QUALIFIED} in evidence["models"]
        assert row.credential_generation == 1
        assert row.auth_state == ProviderProfileAuthState.CONNECTED
        assert row.enabled is True
        assert REVALIDATION_FAILURE_KEY not in (row.command_behavior or {})

        # Launch/admission: the compatible observation answers for execution
        # while exact provenance still schedules a discovery refresh.
        assert (
            evidence_matches_launchable_identity(
                evidence, profile=row, image_ref=REQUESTED_IMAGE
            )
            is True
        )
        assert (
            evidence_is_current(row, image_ref=REQUESTED_IMAGE, env={}) is False
        )

    # Every Docker probe ran the effective image; the stale digest was never
    # pulled or executed, and no duplicate launch occurred.
    catalog_probes = [
        argv for argv in backend.commands if "models --refresh" in " ".join(argv)
    ]
    assert len(catalog_probes) == 2
    for probe in catalog_probes:
        assert EFFECTIVE_IMAGE in probe
        assert REQUESTED_IMAGE not in probe
    version_probes = [argv for argv in backend.commands if "--version" in argv]
    assert len(version_probes) == 2
    for probe in version_probes:
        assert EFFECTIVE_IMAGE in probe
        assert REQUESTED_IMAGE not in probe
    assert backend.catalog_calls == 2
    assert releases == [PROFILE_ID, PROFILE_ID]
    assert not any(
        argv[1:2] == ["pull"] and REQUESTED_IMAGE in argv
        for argv in backend.commands
    )
