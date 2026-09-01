"""Bounded smoke validation for Omnigent agent profiles (MoonLadderStudios/MoonMind#3517 §7)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentUpstreamAgentProjection,
    ProviderCredentialSource,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
)
from api_service.services.omnigent_agent_profile_service import projection_identity
from api_service.services.omnigent_agent_smoke_service import (
    ReadinessOutcome,
    run_profile_readiness_checks,
    run_smoke_validation,
    scrub_diagnostics,
)

pytestmark = [pytest.mark.asyncio]


# --------------------------------------------------------------------------
# Orchestration: timeout budget, guaranteed cleanup, secret-scanned diagnostics
# --------------------------------------------------------------------------


def _ready_outcome():
    return ReadinessOutcome(
        checks=[{"name": "provider_profile", "ready": True, "reason": None}],
        upstream_snapshot={"id": "agent-1"},
    )


async def _ok_probe():
    return {"ready": True, "reason": None}


class _FakeClock:
    def __init__(self):
        self._t = 0.0

    def __call__(self):
        self._t += 0.25
        return self._t


async def test_smoke_pass_appends_session_start_and_is_ready():
    async def preflight():
        return _ready_outcome()

    result = await run_smoke_validation(
        preflight=preflight,
        session_start_probe=_ok_probe,
        monotonic=_FakeClock(),
        profile_id="codex",
        version=3,
    )
    assert result["ready"] is True
    assert result["timedOut"] is False
    assert result["version"] == 3
    names = [check["name"] for check in result["checks"]]
    assert names == ["provider_profile", "session_start"]
    assert result["durationMs"] >= 0


async def test_failing_check_marks_not_ready():
    async def preflight():
        return ReadinessOutcome(
            checks=[{"name": "provider_profile", "ready": False, "reason": "no provider"}]
        )

    result = await run_smoke_validation(
        preflight=preflight,
        session_start_probe=_ok_probe,
        profile_id="codex",
        version=1,
    )
    assert result["ready"] is False


async def test_timeout_releases_leases_and_reports_timed_out():
    released = {"value": False}

    async def slow_preflight():
        await asyncio.sleep(5)
        return _ready_outcome()

    async def cleanup():
        released["value"] = True

    result = await run_smoke_validation(
        preflight=slow_preflight,
        session_start_probe=_ok_probe,
        cleanup=cleanup,
        timeout_seconds=0.05,
        profile_id="codex",
        version=1,
    )
    assert result["timedOut"] is True
    assert result["ready"] is False
    assert result["checks"][0]["name"] == "smoke_timeout"
    assert released["value"] is True


async def test_cleanup_runs_when_preflight_errors():
    released = {"value": False}

    async def broken_preflight():
        raise RuntimeError("boom")

    async def cleanup():
        released["value"] = True

    result = await run_smoke_validation(
        preflight=broken_preflight,
        session_start_probe=_ok_probe,
        cleanup=cleanup,
        profile_id="codex",
        version=1,
    )
    assert result["ready"] is False
    assert result["checks"][0]["name"] == "smoke_error"
    assert released["value"] is True


async def test_probe_diagnostics_are_secret_scanned():
    diagnostics: list[str] = []

    async def preflight():
        return _ready_outcome()

    async def leaky_probe():
        diagnostics.append("session-start probe failed: token=ghp_" + "a" * 36)
        return {"ready": False, "reason": "endpoint unreachable"}

    result = await run_smoke_validation(
        preflight=preflight,
        session_start_probe=leaky_probe,
        diagnostics=diagnostics,
        profile_id="codex",
        version=1,
    )
    assert result["ready"] is False
    joined = " ".join(result["diagnostics"])
    assert "ghp_" not in joined
    assert "redacted smoke diagnostic" in joined


async def test_scrub_diagnostics_bounds_and_redacts():
    lines = [f"line {i}" for i in range(100)]
    lines.append("password: hunter2secretvalue")
    scrubbed = scrub_diagnostics(lines)
    assert len(scrubbed) <= 32
    long_line = scrub_diagnostics(["x" * 5000])[0]
    assert len(long_line) <= 512


# --------------------------------------------------------------------------
# Shared readiness-check core
# --------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def session(tmp_path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/smoke.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        yield db
    await engine.dispose()


def _document():
    return {
        "endpointRef": "default",
        "bridgeMode": "proxy",
        "harness": "codex-native",
        "requiredCapabilities": ["session.start"],
        "source": {"upstreamId": "agent-1", "upstreamVersion": "v1"},
        "providerRequirements": {
            "runtimeId": "codex_cli",
            "credentialSource": "secret_ref",
            "materializationMode": "api_key_env",
            "providerIds": [],
        },
    }


async def _noop_refresh():
    return None


async def _unused_bundle_reader(artifact_id):  # pragma: no cover - not reached
    raise AssertionError("bundle reader should not run for an upstream source")


async def test_readiness_upstream_missing_and_no_provider(session):
    outcome = await run_profile_readiness_checks(
        session,
        document=_document(),
        refresh_upstream=_noop_refresh,
        read_bundle_bytes=_unused_bundle_reader,
    )
    assert outcome.ready is False
    by_name = {check["name"]: check for check in outcome.checks}
    assert by_name["upstream_identity"]["ready"] is False
    assert by_name["provider_profile"]["ready"] is False


async def test_v2_readiness_returns_typed_checks_instead_of_using_v1_fields(session):
    document = {
        "schemaVersion": "moonmind.omnigent-agent-profile.v2",
        "endpointRef": "default",
        "source": {
            "kind": "upstream",
            "upstreamId": "opencode-native-ui",
            "upstreamVersion": "1",
            "upstreamSnapshotDigest": "sha256:" + "1" * 64,
        },
        "harness": {
            "id": "opencode-native",
            "catalogRef": "omnigent-harness-catalog:sha256:" + "2" * 64,
            "implementationRef": (
                "omnigent-harness-implementation:sha256:" + "3" * 64
            ),
        },
        "credentialSlots": [
            {"id": "primary-model", "acceptedProviderIds": ["opencode-go"]}
        ],
        "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
    }

    outcome = await run_profile_readiness_checks(
        session,
        document=document,
        refresh_upstream=_noop_refresh,
        read_bundle_bytes=_unused_bundle_reader,
    )

    by_name = {check["name"]: check for check in outcome.checks}
    assert by_name["upstream_identity"]["ready"] is False
    assert by_name["harness_catalog"]["ready"] is False
    assert by_name["provider_profile"]["ready"] is False


async def test_readiness_ready_when_projection_fresh_and_provider_compatible(session):
    now = datetime.now(timezone.utc)
    session.add(
        OmnigentUpstreamAgentProjection(
            projection_id=projection_identity("default", "agent-1", "v1"),
            endpoint_ref="default",
            bridge_mode="proxy",
            upstream_id="agent-1",
            upstream_version="v1",
            metadata_snapshot={
                "harness": "codex-native",
                "capabilities": ["session.start"],
                "name": "Codex",
            },
            available=True,
            compatible=True,
            last_successful_sync_at=now,
            last_attempt_at=now,
        )
    )
    session.add(
        ManagedAgentProviderProfile(
            profile_id="prov-1",
            runtime_id="codex_cli",
            provider_id="openai",
            credential_source=ProviderCredentialSource.SECRET_REF,
            runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
            enabled=True,
            auth_state=ProviderProfileAuthState.CONNECTED,
            secret_refs={"openai_api_key": "env://OPENAI_API_KEY"},
        )
    )
    await session.commit()

    outcome = await run_profile_readiness_checks(
        session,
        document=_document(),
        refresh_upstream=_noop_refresh,
        read_bundle_bytes=_unused_bundle_reader,
    )
    assert outcome.ready is True
    assert outcome.upstream_snapshot["name"] == "Codex"


async def test_readiness_bundle_provenance_fails_for_missing_artifact(session):
    document = _document()
    document["source"] = {
        "bundleArtifactRef": "artifact:missing",
        "bundleDigest": "sha256:" + "a" * 64,
    }

    outcome = await run_profile_readiness_checks(
        session,
        document=document,
        refresh_upstream=_noop_refresh,
        read_bundle_bytes=_unused_bundle_reader,
    )
    by_name = {check["name"]: check for check in outcome.checks}
    assert by_name["bundle_provenance"]["ready"] is False
    assert "bundle_contents" not in by_name


# --------------------------------------------------------------------------
# Model catalog admission: identity and observation age
# --------------------------------------------------------------------------


def _opencode_v2_document(*, harness, catalog_ref: str) -> dict:
    return {
        "schemaVersion": "moonmind.omnigent-agent-profile.v2",
        "endpointRef": "default",
        "source": {
            "kind": "upstream",
            "upstreamId": "opencode-native-ui",
            "upstreamVersion": "1",
            "upstreamSnapshotDigest": "sha256:" + "7" * 64,
        },
        "harness": {
            "id": harness.id,
            "catalogRef": catalog_ref,
            "implementationRef": harness.implementation.implementation_ref(),
        },
        "credentialSlots": [
            {"id": "primary-model", "acceptedProviderIds": ["opencode-go"]}
        ],
        "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
        "model": {"qualifiedId": "opencode-go/test-model"},
    }


async def _seed_opencode_smoke_admission(session, *, validated_at: datetime):
    """Seed the exact rows the v2 provider admission loop reads."""

    from api_service.db.models import (
        OmnigentHarnessCatalogSnapshotRecord,
        OmnigentHarnessTrustRecord,
    )
    from moonmind.omnigent.harness_platform.catalog import (
        TrustState,
        create_catalog_snapshot,
    )

    snapshot = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="0.11.0",
        omnigentBuildDigest="sha256:" + "4" * 64,
        sourceDigest="sha256:" + "5" * 64,
        harnesses=[
            {
                "id": "opencode-native",
                "label": "OpenCode",
                "implementation": {
                    "sourceKind": "core",
                    "package": "omnigent",
                    "version": "0.11.0",
                    "digest": "sha256:" + "3" * 64,
                },
                "capabilities": {"integrationMode": "native-server"},
            }
        ],
    )
    harness = snapshot.harnesses[0]
    session.add(
        OmnigentHarnessCatalogSnapshotRecord(
            catalog_ref=snapshot.catalogRef,
            endpoint_ref="default",
            omnigent_version=snapshot.omnigentVersion,
            omnigent_build_digest=snapshot.omnigentBuildDigest,
            observed_at=datetime.now(timezone.utc),
            source_digest=snapshot.sourceDigest,
            snapshot_json=snapshot.model_dump(mode="json"),
            diagnostics_json={},
        )
    )
    await session.flush()
    session.add(
        OmnigentHarnessTrustRecord(
            implementation_ref=harness.implementation.implementation_ref(),
            harness_id=harness.id,
            catalog_ref=snapshot.catalogRef,
            trust_state=TrustState.core_trusted.value,
        )
    )
    provider = ManagedAgentProviderProfile(
        profile_id="opencode-go-primary",
        runtime_id="opencode",
        provider_id="opencode-go",
        credential_source=ProviderCredentialSource.SECRET_REF,
        runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
        enabled=True,
        auth_state=ProviderProfileAuthState.CONNECTED,
        secret_refs={"opencode_api_key": "env://OPENCODE_API_KEY"},
        credential_generation=4,
        model_catalog_evidence_json={
            "credentialGeneration": 4,
            "imageRef": "registry.test/opencode@sha256:" + "6" * 64,
            "models": [{"qualifiedId": "opencode-go/test-model"}],
            "validatedAt": validated_at.isoformat(),
        },
    )
    session.add(provider)
    await session.commit()
    return _opencode_v2_document(harness=harness, catalog_ref=snapshot.catalogRef)


async def _provider_check(session, document):
    outcome = await run_profile_readiness_checks(
        session,
        document=document,
        refresh_upstream=_noop_refresh,
        read_bundle_bytes=_unused_bundle_reader,
    )
    return {check["name"]: check for check in outcome.checks}["provider_profile"]


async def test_smoke_admission_rejects_an_expired_model_catalog(session, monkeypatch):
    """Smoke launches the real host, so it must not admit an expired catalog.

    The pinned host image refreshes its catalog from the provider at probe
    time. Binding admission to the credential generation and image digest alone
    would let smoke keep launching from the first catalog it ever observed --
    including a model the provider has since removed -- because neither of
    those changes on a healthy deployment.
    """

    from moonmind.omnigent.bootstrap import store

    server_ref = "registry.test/omnigent@sha256:" + "5" * 64
    host_ref = "registry.test/opencode@sha256:" + "6" * 64
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", server_ref)
    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", host_ref)
    monkeypatch.setattr(
        store,
        "load_resolved_state",
        lambda: SimpleNamespace(
            server_image_ref=server_ref,
            opencode_host_image_ref=host_ref,
            details={
                "opencodeHostCompatibility": {
                    "status": "ready",
                    "failureCode": None,
                    "serverImageRef": server_ref,
                    "hostImageRef": host_ref,
                }
            },
        ),
    )
    # Exercise the documented default catalog interval, not an inherited value.
    monkeypatch.delenv("OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS", raising=False)
    document = await _seed_opencode_smoke_admission(
        session, validated_at=datetime.now(timezone.utc)
    )

    assert (await _provider_check(session, document))["ready"] is True

    provider = await session.get(ManagedAgentProviderProfile, "opencode-go-primary")
    provider.model_catalog_evidence_json = {
        **provider.model_catalog_evidence_json,
        "validatedAt": (
            datetime.now(timezone.utc) - timedelta(hours=9)
        ).isoformat(),
    }
    await session.commit()

    assert (await _provider_check(session, document))["ready"] is False

    # The interval is deployment-configurable; ``0`` restores identity-only
    # staleness at this boundary exactly as it does at the reconciler.
    monkeypatch.setenv("OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS", "0")
    assert (await _provider_check(session, document))["ready"] is True
