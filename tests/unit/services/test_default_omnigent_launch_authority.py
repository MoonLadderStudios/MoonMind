"""Default-launch authority for the OpenCode Zen route (MoonLadderStudios/MoonMind#3877).

A default deployment must complete a default Omnigent launch without any
configuration: the managed default Agent Profile is the built-in OpenCode
profile, and the Provider Profile it resolves is the credentialless
``opencode-zen-free`` seed. These tests drive the real admission boundary
(``resolve_default_agent_profile_snapshot``) against the real seeded provider
rows rather than asserting the two halves independently.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentAgentProfile,
    OmnigentAgentProfileVersion,
    OmnigentHarnessCatalogSnapshotRecord,
    OmnigentHarnessTrustRecord,
    ProviderProfileDisabledReason,
)
from api_service.services.omnigent_agent_bootstrap_service import (
    OPENCODE_BUILTIN_PROFILE_ID,
    reconcile_managed_default_agent_profile,
)
from api_service.services.omnigent_agent_profile_selection import (
    resolve_default_agent_profile_snapshot,
)
from api_service.services.omnigent_agent_profile_service import (
    synchronize_upstream_inventory,
)

pytestmark = [pytest.mark.asyncio]


async def test_profile_inventory_and_default_resolution_survive_catalog_expiry(session, monkeypatch):
    from api_service.api.routers.provider_profiles import list_profiles

    await _seed_default_deployment(session, monkeypatch)
    row = await session.get(ManagedAgentProviderProfile, "opencode-zen-free")
    row.model_catalog_evidence_json = {
        "validatedAt": "2000-01-01T00:00:00+00:00", "imageRef": "previous-image",
        "credentialGeneration": 0, "models": [],
    }
    await session.commit()
    user = SimpleNamespace(id=uuid4(), is_superuser=True)
    rows = await list_profiles(include_execution=True, session=session, current_user=user)
    selected = next(item for item in rows if item["profile_id"] == row.profile_id)
    assert selected["execution_selection"]["providerProfileRef"] == row.profile_id
    assert selected["launch_ready"] is True
    automatic = await resolve_default_agent_profile_snapshot(
        session, provider_profile_ref=None, launch_policy_ref=None,
        consumer_type="workflow", consumer_id="default-expired", user=user,
    )
    explicit = await resolve_default_agent_profile_snapshot(
        session, provider_profile_ref=row.profile_id, launch_policy_ref=None,
        consumer_type="workflow", consumer_id="explicit-expired", user=user,
    )
    assert automatic == explicit


async def test_disabled_profile_remains_in_inventory_without_gaining_launch_authority(session, monkeypatch):
    from api_service.api.routers.provider_profiles import list_profiles

    await _seed_default_deployment(session, monkeypatch)
    row = await session.get(ManagedAgentProviderProfile, "opencode-zen-free")
    row.enabled = False
    row.disabled_reason = ProviderProfileDisabledReason.USER_DISABLED
    await session.commit()
    user = SimpleNamespace(id=uuid4(), is_superuser=True)
    rows = await list_profiles(include_execution=True, session=session, current_user=user)
    selected = next(item for item in rows if item["profile_id"] == row.profile_id)
    assert selected["launch_ready"] is False
    assert selected["execution_selection"]["providerProfileRef"] == row.profile_id
    with pytest.raises(HTTPException):
        await resolve_default_agent_profile_snapshot(
            session, provider_profile_ref=row.profile_id, launch_policy_ref=None,
            consumer_type="workflow", consumer_id="disabled", user=user,
        )


async def test_inventory_loads_active_and_explicitly_pinned_versions_only(session, monkeypatch):
    from api_service.services.profile_execution_selection import load_execution_configurations

    await _seed_default_deployment(session, monkeypatch)
    profile = await session.get(OmnigentAgentProfile, OPENCODE_BUILTIN_PROFILE_ID)
    old = await session.scalar(select(OmnigentAgentProfileVersion).where(
        OmnigentAgentProfileVersion.profile_id == profile.profile_id,
        OmnigentAgentProfileVersion.version == profile.active_version,
    ))
    provider = await session.get(ManagedAgentProviderProfile, "opencode-zen-free")
    for number in (2, 3):
        session.add(OmnigentAgentProfileVersion(
            profile_id=profile.profile_id, version=number,
            digest="sha256:" + str(number) * 64,
            document={**old.document, "version": number},
            validation_result=old.validation_result,
        ))
    profile.active_version = 3
    provider.execution_configuration = {
        "profileId": profile.profile_id, "version": 1, "digest": old.digest,
    }
    await session.flush()
    user = SimpleNamespace(id=uuid4(), is_superuser=True)
    pinned = await load_execution_configurations(session, user, [provider])
    assert {version.version for row, version in pinned if row.profile_id == profile.profile_id} == {1, 3}
    provider.execution_configuration = None
    automatic = await load_execution_configurations(session, user, [provider])
    assert {version.version for row, version in automatic if row.profile_id == profile.profile_id} == {3}

_SERVER_IMAGE_REF = "registry.test/server@sha256:" + "1" * 64
_OPENCODE_HOST_IMAGE_REF = "registry.test/opencode@sha256:" + "6" * 64


@pytest.fixture(autouse=True)
def _clear_seed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MINIMAX_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENCODE_API_KEY",
        "OMNIGENT_DEFAULT_AGENT_NAME",
        "MOONMIND_SKIP_PROVIDER_PROFILE_SEED",
    ):
        monkeypatch.delenv(env_name, raising=False)


@pytest_asyncio.fixture()
async def session(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Bind the startup seeder and the resolver to one throwaway database."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/default-launch.db"
    engine = create_async_engine(db_url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(db_base, "DATABASE_URL", db_url, raising=False)
    monkeypatch.setattr(db_base, "engine", engine, raising=False)
    monkeypatch.setattr(db_base, "async_session_maker", maker, raising=False)
    async with maker() as db:
        yield db
    await engine.dispose()


def _publish_opencode_catalog_authority(monkeypatch: pytest.MonkeyPatch):
    """Publish the exact authority that makes ``opencode-native`` launchable."""

    from moonmind.omnigent.bootstrap import store
    from moonmind.omnigent.harness_platform.catalog import (
        TrustState,
        classify_harness_trust,
        create_catalog_snapshot,
    )

    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_HOST_ENABLED", "true")
    monkeypatch.setenv("MOONMIND_OMNIGENT_OPENCODE_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", _SERVER_IMAGE_REF)
    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", _OPENCODE_HOST_IMAGE_REF)
    monkeypatch.setattr(
        store,
        "load_resolved_state",
        lambda: SimpleNamespace(
            server_image_ref=_SERVER_IMAGE_REF,
            opencode_host_image_ref=_OPENCODE_HOST_IMAGE_REF,
            details={
                "opencodeHostCompatibility": {
                    "status": "ready",
                    "failureCode": None,
                    "serverImageRef": _SERVER_IMAGE_REF,
                    "hostImageRef": _OPENCODE_HOST_IMAGE_REF,
                }
            },
        ),
    )
    snapshot = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="0.12.0",
        omnigentBuildDigest="sha256:" + "4" * 64,
        sourceDigest="sha256:" + "5" * 64,
        harnesses=[
            {
                "id": "opencode-native",
                "label": "OpenCode",
                "implementation": {
                    "sourceKind": "core",
                    "package": "omnigent",
                    "version": "0.12.0",
                    "digest": "sha256:" + "3" * 64,
                },
                "capabilities": {"integrationMode": "native-server"},
            }
        ],
    )
    harness = snapshot.harnesses[0]
    trust = classify_harness_trust(
        harnessId=harness.id,
        implementation=harness.implementation,
        trustState=TrustState.core_trusted,
    )
    return snapshot, harness, trust


async def _seed_default_deployment(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce the default deployment's startup authority in dependency order."""

    from api_service.main import _auto_seed_provider_profiles

    snapshot, harness, trust = _publish_opencode_catalog_authority(monkeypatch)

    # Provider defaults are seeded before the first Omnigent reconciliation.
    seeded = await _auto_seed_provider_profiles()
    assert "opencode" in seeded

    session.add(
        OmnigentHarnessCatalogSnapshotRecord(
            catalog_ref=snapshot.catalogRef,
            endpoint_ref=snapshot.endpointRef,
            omnigent_version=snapshot.omnigentVersion,
            omnigent_build_digest=snapshot.omnigentBuildDigest,
            observed_at=snapshot.observedAt,
            source_digest=snapshot.sourceDigest,
            snapshot_json=snapshot.model_dump(by_alias=True, mode="json"),
            diagnostics_json={},
        )
    )
    await session.flush()
    session.add(
        OmnigentHarnessTrustRecord(
            implementation_ref=trust.implementationRef,
            harness_id=trust.harnessId,
            catalog_ref=snapshot.catalogRef,
            trust_state="core_trusted",
        )
    )
    await session.commit()

    await synchronize_upstream_inventory(
        session,
        endpoint_ref="default",
        bridge_mode="proxy",
        inventory=[
            {
                "id": "opencode-native-ui",
                "name": "opencode-native-ui",
                "version": "1",
                "harness": "opencode-native",
                "capabilities": [],
            }
        ],
    )
    await session.commit()

    document = {
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
            "catalogRef": snapshot.catalogRef,
            "implementationRef": harness.implementation.implementation_ref(),
        },
        "credentialSlots": [
            {
                "id": "primary-model",
                "acceptedAuthModels": ["own-auth", "none"],
                "acceptedProviderIds": ["opencode-go", "opencode"],
            }
        ],
        "allowedLaunchPolicyRefs": ["omnigent-on-demand@1", "opencode-on-demand@1"],
    }
    session.add_all(
        [
            OmnigentAgentProfile(
                profile_id=OPENCODE_BUILTIN_PROFILE_ID,
                display_name="OpenCode via Omnigent",
                visibility="workspace",
                state="active",
                active_version=1,
            ),
            OmnigentAgentProfileVersion(
                profile_id=OPENCODE_BUILTIN_PROFILE_ID,
                version=1,
                digest="sha256:" + "8" * 64,
                document=document,
                upstream_snapshot={"id": "opencode-native-ui"},
                validation_result={"ready": True},
            ),
        ]
    )
    await session.commit()
    assert (
        await reconcile_managed_default_agent_profile(session, env={})
        == OPENCODE_BUILTIN_PROFILE_ID
    )


async def test_default_launch_resolves_the_credentialless_zen_profile(
    session, monkeypatch
):
    await _seed_default_deployment(session, monkeypatch)

    snapshot = await resolve_default_agent_profile_snapshot(
        session,
        provider_profile_ref=None,
        launch_policy_ref=None,
        consumer_type="workflow",
        consumer_id="mm:default-launch-1",
        user=SimpleNamespace(id=uuid4(), is_superuser=True),
    )

    assert snapshot["profileId"] == OPENCODE_BUILTIN_PROFILE_ID
    assert snapshot["providerProfileRef"] == "opencode-zen-free"
    assert snapshot["launchPolicyRef"] == "omnigent-on-demand@1"
    assert snapshot["executionProfileRef"] == "omnigent-opencode@1"
    assert snapshot["agentId"] == "opencode-native-ui"


async def test_explicit_provider_selection_still_wins_over_the_default(
    session, monkeypatch
):
    await _seed_default_deployment(session, monkeypatch)
    # A second credentialless OpenCode profile stands in for an operator-created
    # alternative; naming it before the Zen seed proves the selection follows
    # the explicit request, not table ordering.
    async with db_base.async_session_maker() as writer:
        zen = await writer.get(ManagedAgentProviderProfile, "opencode-zen-free")
        assert zen is not None
        writer.add(
            ManagedAgentProviderProfile(
                profile_id="aaa-operator-opencode",
                runtime_id=zen.runtime_id,
                provider_id=zen.provider_id,
                provider_label=zen.provider_label,
                account_label="Operator OpenCode",
                default_model=zen.default_model,
                default_effort=zen.default_effort,
                model_tiers=zen.model_tiers,
                default_model_tier=zen.default_model_tier,
                credential_source=zen.credential_source,
                runtime_materialization_mode=zen.runtime_materialization_mode,
                secret_refs={},
                clear_env_keys=list(zen.clear_env_keys or []),
                env_template={},
                command_behavior=dict(zen.command_behavior or {}),
                tags=list(zen.tags or []),
                enabled=True,
                is_default=False,
                auth_state=zen.auth_state,
                disabled_reason=None,
            )
        )
        await writer.commit()

    snapshot = await resolve_default_agent_profile_snapshot(
        session,
        provider_profile_ref="aaa-operator-opencode",
        launch_policy_ref=None,
        consumer_type="workflow",
        consumer_id="mm:default-launch-explicit",
        user=SimpleNamespace(id=uuid4(), is_superuser=True),
    )

    assert snapshot["providerProfileRef"] == "aaa-operator-opencode"


async def test_disabling_zen_releases_the_default_to_the_next_ranked_profile(
    session, monkeypatch
):
    """An explicit disable is the documented way to stop launching through Zen."""

    await _seed_default_deployment(session, monkeypatch)
    async with db_base.async_session_maker() as writer:
        zen = await writer.get(ManagedAgentProviderProfile, "opencode-zen-free")
        assert zen is not None
        zen.enabled = False
        zen.is_default = False
        zen.disabled_reason = ProviderProfileDisabledReason.USER_DISABLED
        await writer.commit()

    with pytest.raises(HTTPException) as caught:
        await resolve_default_agent_profile_snapshot(
            session,
            provider_profile_ref=None,
            launch_policy_ref=None,
            consumer_type="workflow",
            consumer_id="mm:default-launch-disabled",
            user=SimpleNamespace(id=uuid4(), is_superuser=True),
        )

    assert caught.value.status_code == 409
    assert "no launch-ready Provider Profile" in str(caught.value.detail)
    remaining = list(
        (
            await session.execute(
                select(ManagedAgentProviderProfile).where(
                    ManagedAgentProviderProfile.runtime_id == "opencode"
                )
            )
        )
        .scalars()
        .all()
    )
    assert [row.is_default for row in remaining] == [False]


async def test_selection_rejects_a_provider_the_slot_auth_model_forbids(
    session, monkeypatch
):
    """A credential slot's accepted auth models bound Provider Profile selection.

    The plan builder rebuilds the slot auth model from whichever materializer
    the selected Provider Profile resolves to, so a slot restricted to ``none``
    must reject an ``opencode-go`` profile whose materializer is ``own-auth``.
    Provider ids and harness compatibility alone would let it through and launch
    API-key credentials the immutable Agent Profile forbids.
    """

    await _seed_default_deployment(session, monkeypatch)
    async with db_base.async_session_maker() as writer:
        version = await writer.scalar(
            select(OmnigentAgentProfileVersion).where(
                OmnigentAgentProfileVersion.profile_id == OPENCODE_BUILTIN_PROFILE_ID,
                OmnigentAgentProfileVersion.version == 1,
            )
        )
        assert version is not None
        document = dict(version.document)
        document["credentialSlots"] = [
            {
                "id": "primary-model",
                "acceptedAuthModels": ["none"],
                "acceptedProviderIds": ["opencode-go", "opencode"],
            }
        ]
        version.document = document
        zen = await writer.get(ManagedAgentProviderProfile, "opencode-zen-free")
        assert zen is not None
        writer.add(
            ManagedAgentProviderProfile(
                profile_id="operator-opencode-go",
                runtime_id=zen.runtime_id,
                provider_id="opencode-go",
                provider_label=zen.provider_label,
                account_label="Operator OpenCode Go",
                default_model=zen.default_model,
                default_effort=zen.default_effort,
                model_tiers=zen.model_tiers,
                default_model_tier=zen.default_model_tier,
                credential_source=zen.credential_source,
                runtime_materialization_mode=zen.runtime_materialization_mode,
                secret_refs={},
                clear_env_keys=list(zen.clear_env_keys or []),
                env_template={},
                command_behavior=dict(zen.command_behavior or {}),
                tags=list(zen.tags or []),
                enabled=True,
                is_default=False,
                auth_state=zen.auth_state,
                disabled_reason=None,
            )
        )
        await writer.commit()

    with pytest.raises(HTTPException) as caught:
        await resolve_default_agent_profile_snapshot(
            session,
            provider_profile_ref="operator-opencode-go",
            launch_policy_ref=None,
            consumer_type="workflow",
            consumer_id="mm:default-launch-auth-model",
            user=SimpleNamespace(id=uuid4(), is_superuser=True),
        )

    assert caught.value.status_code == 409
    # The incompatibility is the slot auth-model contract, not readiness: an
    # unenforced contract would fall through to the launch-ready check instead.
    assert caught.value.detail["code"] == "profile_execution_configuration_required"

    # The automatic default still resolves the credentialless route the slot
    # does accept.
    snapshot = await resolve_default_agent_profile_snapshot(
        session,
        provider_profile_ref=None,
        launch_policy_ref=None,
        consumer_type="workflow",
        consumer_id="mm:default-launch-auth-model-default",
        user=SimpleNamespace(id=uuid4(), is_superuser=True),
    )

    assert snapshot["providerProfileRef"] == "opencode-zen-free"
