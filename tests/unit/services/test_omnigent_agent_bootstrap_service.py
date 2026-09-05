"""Durable bootstrap default resolution + seeding (MoonLadderStudios/MoonMind#3517 §8).

Also covers which MoonMind-managed profile holds ``default_for_runtime``
(MoonLadderStudios/MoonMind#3877).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers.omnigent_agent_profiles import (
    AgentProfileDocument,
    _digest as router_digest,
    _normalized,
)
from api_service.db.models import (
    Base,
    OmnigentAgentProfile,
    OmnigentAgentProfileAuditEvent,
    OmnigentAgentProfileVersion,
    OmnigentHarnessCatalogSnapshotRecord,
    OmnigentHarnessTrustRecord,
    OmnigentPolicy,
    OmnigentPolicyVersion,
)
from api_service.services.omnigent_agent_bootstrap_service import (
    BOOTSTRAP_PROFILE_ID,
    OPENCODE_BUILTIN_PROFILE_ID,
    BootstrapDefaultConflictError,
    build_bootstrap_document,
    default_agent_profile_ready,
    reconcile_bootstrap_agent_profile,
    reconcile_managed_default_agent_profile,
    resolve_default_agent_selection,
    seed_bootstrap_agent_profile,
)
from api_service.services.omnigent_agent_profile_service import (
    synchronize_upstream_inventory,
)

TRUST_CORE_TRUSTED = "core_trusted"

pytestmark = [pytest.mark.asyncio]


def _inventory(agent_id: str, *, name: str | None = None):
    return [{
        "id": agent_id,
        "name": name or agent_id,
        "harness": "codex-native",
        "capabilities": ["session.start"],
    }]


@pytest_asyncio.fixture()
async def session(tmp_path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bootstrap.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        yield db
    await engine.dispose()


async def _add_default_profile(
    session: AsyncSession,
    *,
    state: str = "active",
    active_version: int | None = 1,
    upstream_id: str = "codex-prod",
    upstream_name: str | None = "Codex Production",
) -> None:
    document = build_bootstrap_document(upstream_id)
    profile = OmnigentAgentProfile(
        profile_id="codex-team",
        display_name="Codex Team",
        visibility="workspace",
        state=state,
        active_version=active_version,
        default_for_runtime=True,
    )
    version = OmnigentAgentProfileVersion(
        profile_id="codex-team",
        version=1,
        digest=router_digest(document),
        document=document,
        upstream_snapshot={"id": upstream_id, "name": upstream_name}
        if upstream_name
        else {"id": upstream_id},
    )
    session.add_all([profile, version])
    await session.commit()


async def test_bootstrap_document_matches_router_normalized_form():
    document = build_bootstrap_document("codex-default")
    normalized = _normalized(AgentProfileDocument.model_validate(document))
    assert normalized == document
    assert router_digest(document) == router_digest(normalized)


async def test_resolves_env_fallback_and_records_use(session):
    resolution = await resolve_default_agent_selection(
        session, env={"OMNIGENT_DEFAULT_AGENT_NAME": "codex-default"}
    )
    assert resolution.source == "env_fallback"
    assert resolution.used_env_fallback is True
    assert resolution.default_agent_name == "codex-default"
    assert resolution.profile_id is None


async def test_resolves_none_when_no_durable_state_and_no_env(session):
    resolution = await resolve_default_agent_selection(session, env={})
    assert resolution.source == "none"
    assert resolution.default_agent_name == ""
    assert resolution.used_env_fallback is False


async def test_durable_active_default_wins_over_env(session):
    await _add_default_profile(session, upstream_id="codex-prod", upstream_name="Codex")
    resolution = await resolve_default_agent_selection(
        session, env={"OMNIGENT_DEFAULT_AGENT_NAME": "env-ignored"}
    )
    assert resolution.source == "durable_profile"
    assert resolution.used_env_fallback is False
    assert resolution.agent_id == "codex-prod"
    # The launch boundary preserves the durable ID instead of a mutable name.
    assert resolution.default_agent_name == "codex-prod"
    assert resolution.profile_id == "codex-team"
    assert resolution.version == 1


async def test_durable_default_without_snapshot_name_falls_back_to_stable_id(session):
    await _add_default_profile(session, upstream_id="codex-prod", upstream_name=None)
    resolution = await resolve_default_agent_selection(session, env={})
    assert resolution.agent_id == "codex-prod"
    assert resolution.default_agent_name == "codex-prod"


async def test_default_marked_but_not_active_fails_closed(session):
    await _add_default_profile(session, state="draft", active_version=None)
    with pytest.raises(BootstrapDefaultConflictError):
        await resolve_default_agent_selection(
            session, env={"OMNIGENT_DEFAULT_AGENT_NAME": "env-ignored"}
        )


async def test_seed_materializes_active_bootstrap_profile(session):
    ready = await reconcile_bootstrap_agent_profile(
        session,
        env={"OMNIGENT_DEFAULT_AGENT_NAME": "codex-default"},
        inventory=_inventory("codex-default"),
    )
    assert ready is True

    profile = await session.get(OmnigentAgentProfile, BOOTSTRAP_PROFILE_ID)
    assert profile is not None
    assert profile.state == "active"
    assert profile.default_for_runtime is True
    assert profile.active_version == 1

    version = await session.scalar(
        select(OmnigentAgentProfileVersion).where(
            OmnigentAgentProfileVersion.profile_id == BOOTSTRAP_PROFILE_ID
        )
    )
    assert version.version == 1
    assert version.document["source"]["upstreamId"] == "codex-default"
    assert version.digest == router_digest(build_bootstrap_document("codex-default"))
    assert version.rollout_metadata["origin"] == "env_bootstrap"
    assert version.validation_result["ready"] is True

    audit = await session.scalar(
        select(OmnigentAgentProfileAuditEvent).where(
            OmnigentAgentProfileAuditEvent.profile_id == BOOTSTRAP_PROFILE_ID
        )
    )
    assert audit.action == "bootstrap_materialized"

    resolution = await resolve_default_agent_selection(
        session, env={"OMNIGENT_DEFAULT_AGENT_NAME": "codex-default"}
    )
    assert resolution.source == "durable_profile"
    assert resolution.default_agent_name == "codex-default"


async def test_seed_is_idempotent(session):
    await synchronize_upstream_inventory(
        session,
        endpoint_ref="default",
        bridge_mode="proxy",
        inventory=_inventory("codex-default"),
    )
    first = await seed_bootstrap_agent_profile(
        session, env={"OMNIGENT_DEFAULT_AGENT_NAME": "codex-default"}
    )
    second = await seed_bootstrap_agent_profile(
        session, env={"OMNIGENT_DEFAULT_AGENT_NAME": "codex-default"}
    )
    assert first == BOOTSTRAP_PROFILE_ID
    assert second is None
    count = await session.scalar(
        select(func.count()).select_from(OmnigentAgentProfile)
    )
    assert count == 1


async def test_seed_skipped_when_durable_state_exists(session):
    await _add_default_profile(session)
    seeded = await seed_bootstrap_agent_profile(
        session, env={"OMNIGENT_DEFAULT_AGENT_NAME": "codex-default"}
    )
    assert seeded is None
    assert await session.get(OmnigentAgentProfile, BOOTSTRAP_PROFILE_ID) is None


async def test_seed_uses_builtin_codex_when_env_absent(session):
    assert await reconcile_bootstrap_agent_profile(
        session,
        env={},
        inventory=_inventory("codex-native-ui"),
    ) is True
    count = await session.scalar(
        select(func.count()).select_from(OmnigentAgentProfile)
    )
    assert count == 1
    profile = await session.get(OmnigentAgentProfile, BOOTSTRAP_PROFILE_ID)
    assert profile is not None
    assert profile.state == "active"
    version = await session.scalar(
        select(OmnigentAgentProfileVersion).where(
            OmnigentAgentProfileVersion.profile_id == BOOTSTRAP_PROFILE_ID
        )
    )
    assert version.document["source"]["upstreamId"] == "codex-native-ui"
    assert version.rollout_metadata["origin"] == "builtin_default"


async def test_seed_does_not_claim_ready_before_upstream_identity_is_observed(session):
    assert await seed_bootstrap_agent_profile(session, env={}) is None
    assert await session.get(OmnigentAgentProfile, BOOTSTRAP_PROFILE_ID) is None


async def test_reconcile_uses_stable_upstream_id_when_selector_matches_name(session):
    assert await reconcile_bootstrap_agent_profile(
        session,
        env={},
        inventory=_inventory("agent-1", name="codex-native-ui"),
    ) is True
    version = await session.scalar(
        select(OmnigentAgentProfileVersion).where(
            OmnigentAgentProfileVersion.profile_id == BOOTSTRAP_PROFILE_ID
        )
    )
    assert version.document["source"]["upstreamId"] == "agent-1"


async def test_reconcile_preserves_numeric_stock_agent_version(session):
    inventory = _inventory("agent-1", name="codex-native-ui")
    inventory[0]["version"] = 2

    assert await reconcile_bootstrap_agent_profile(
        session,
        env={},
        inventory=inventory,
    ) is True

    version = await session.scalar(
        select(OmnigentAgentProfileVersion).where(
            OmnigentAgentProfileVersion.profile_id == BOOTSTRAP_PROFILE_ID
        )
    )
    assert version.document["source"] == {
        "upstreamId": "agent-1",
        "upstreamVersion": "2",
    }


async def test_reconcile_versions_managed_profile_after_policy_cutover(session):
    assert await reconcile_bootstrap_agent_profile(
        session,
        env={},
        inventory=_inventory("codex-native-ui"),
    ) is True

    session.add_all(
        [
            OmnigentPolicy(
                policy_id="codex-on-demand",
                name="Codex on-demand host",
                visibility="deployment",
                default_version=2,
            ),
            OmnigentPolicyVersion(
                policy_id="codex-on-demand",
                version=2,
                state="active",
                document_json={},
                digest="sha256:" + "2" * 64,
                created_by="bootstrap",
                validation_json={"valid": True},
                compatibility_json={},
                rollout_json={},
            ),
        ]
    )
    await session.commit()

    assert await reconcile_bootstrap_agent_profile(
        session,
        env={},
        inventory=_inventory("codex-native-ui"),
    ) is True

    profile = await session.get(OmnigentAgentProfile, BOOTSTRAP_PROFILE_ID)
    assert profile is not None
    assert profile.active_version == 2
    versions = list(
        (
            await session.execute(
                select(OmnigentAgentProfileVersion)
                .where(
                    OmnigentAgentProfileVersion.profile_id
                    == BOOTSTRAP_PROFILE_ID
                )
                .order_by(OmnigentAgentProfileVersion.version)
            )
        )
        .scalars()
        .all()
    )
    assert [version.document["policyRef"] for version in versions] == [
        "codex-on-demand@1",
        "codex-on-demand@2",
    ]
    assert versions[1].parent_version == 1
    cutover = await session.scalar(
        select(OmnigentAgentProfileAuditEvent).where(
            OmnigentAgentProfileAuditEvent.action
            == "bootstrap_launch_policy_cutover"
        )
    )
    assert cutover is not None
    assert cutover.version == 2


def _opencode_catalog_authority(monkeypatch: pytest.MonkeyPatch):
    """Publish the exact catalog authority that makes ``opencode-native`` launchable."""

    from moonmind.omnigent.bootstrap import store
    from moonmind.omnigent.harness_platform.catalog import (
        TrustState,
        classify_harness_trust,
        create_catalog_snapshot,
    )

    server_ref = "registry.test/server@sha256:" + "1" * 64
    host_ref = "registry.test/opencode@sha256:" + "6" * 64
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_HOST_ENABLED", "true")
    monkeypatch.setenv("MOONMIND_OMNIGENT_OPENCODE_ENABLED", "true")
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


async def _add_ready_opencode_builtin_profile(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    *,
    ready: bool = True,
) -> dict:
    """Materialize the built-in OpenCode profile the way catalog sync does."""

    snapshot, harness, trust = _opencode_catalog_authority(monkeypatch)
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
            trust_state=TRUST_CORE_TRUSTED,
        )
    )
    await session.commit()

    # One endpoint observation carries every advertised agent. Publishing only
    # the OpenCode row would mark the Codex projection unavailable and make the
    # fallback default look unready for the wrong reason.
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
            },
            *_inventory("codex-native-ui"),
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
                digest=router_digest(document),
                document=document,
                upstream_snapshot={"id": "opencode-native-ui"},
                validation_result={"ready": ready},
            ),
        ]
    )
    await session.commit()
    return document


async def test_managed_default_moves_to_the_launch_ready_opencode_builtin(
    session, monkeypatch
):
    """MoonLadderStudios/MoonMind#3877: the OpenCode built-in is the deployment default."""

    assert await reconcile_bootstrap_agent_profile(
        session,
        env={},
        inventory=_inventory("codex-native-ui"),
    ) is True
    codex = await session.get(OmnigentAgentProfile, BOOTSTRAP_PROFILE_ID)
    assert codex is not None and codex.default_for_runtime is True

    await _add_ready_opencode_builtin_profile(session, monkeypatch)
    selected = await reconcile_managed_default_agent_profile(session, env={})

    assert selected == OPENCODE_BUILTIN_PROFILE_ID
    await session.refresh(codex)
    opencode = await session.get(OmnigentAgentProfile, OPENCODE_BUILTIN_PROFILE_ID)
    assert opencode is not None
    assert opencode.default_for_runtime is True
    assert codex.default_for_runtime is False
    assert (
        await session.scalar(
            select(func.count())
            .select_from(OmnigentAgentProfile)
            .where(OmnigentAgentProfile.default_for_runtime.is_(True))
        )
        == 1
    )
    audit = await session.scalar(
        select(OmnigentAgentProfileAuditEvent).where(
            OmnigentAgentProfileAuditEvent.action == "managed_default_selected"
        )
    )
    assert audit is not None
    assert audit.profile_id == OPENCODE_BUILTIN_PROFILE_ID
    assert audit.metadata_json["previousProfileId"] == BOOTSTRAP_PROFILE_ID
    assert await default_agent_profile_ready(session) is True


async def test_managed_default_reconciliation_is_idempotent(session, monkeypatch):
    await _add_ready_opencode_builtin_profile(session, monkeypatch)

    first = await reconcile_managed_default_agent_profile(session, env={})
    second = await reconcile_managed_default_agent_profile(session, env={})

    assert first == second == OPENCODE_BUILTIN_PROFILE_ID
    assert (
        await session.scalar(
            select(func.count())
            .select_from(OmnigentAgentProfileAuditEvent)
            .where(
                OmnigentAgentProfileAuditEvent.action == "managed_default_selected"
            )
        )
        == 1
    )


async def test_disabled_opencode_support_keeps_the_codex_bootstrap_default(
    session, monkeypatch
):
    """A kill switch is an explicit disable, so the fallback default stays."""

    assert await reconcile_bootstrap_agent_profile(
        session,
        env={},
        inventory=_inventory("codex-native-ui"),
    ) is True
    # Catalog synchronization records ``support-qualification: not ready`` in the
    # built-in version when the OpenCode kill switch is off.
    await _add_ready_opencode_builtin_profile(session, monkeypatch, ready=False)

    selected = await reconcile_managed_default_agent_profile(session, env={})

    assert selected == BOOTSTRAP_PROFILE_ID
    opencode = await session.get(OmnigentAgentProfile, OPENCODE_BUILTIN_PROFILE_ID)
    assert opencode is not None
    assert opencode.default_for_runtime is False


async def test_losing_opencode_readiness_returns_the_default_to_the_fallback(
    session, monkeypatch
):
    """A managed default must never remain on a profile that cannot launch."""

    assert await reconcile_bootstrap_agent_profile(
        session,
        env={},
        inventory=_inventory("codex-native-ui"),
    ) is True
    await _add_ready_opencode_builtin_profile(session, monkeypatch)
    assert (
        await reconcile_managed_default_agent_profile(session, env={})
        == OPENCODE_BUILTIN_PROFILE_ID
    )

    version = await session.scalar(
        select(OmnigentAgentProfileVersion).where(
            OmnigentAgentProfileVersion.profile_id == OPENCODE_BUILTIN_PROFILE_ID,
            OmnigentAgentProfileVersion.version == 1,
        )
    )
    version.validation_result = {"ready": False}
    await session.commit()

    selected = await reconcile_managed_default_agent_profile(session, env={})

    assert selected == BOOTSTRAP_PROFILE_ID
    opencode = await session.get(OmnigentAgentProfile, OPENCODE_BUILTIN_PROFILE_ID)
    assert opencode is not None
    assert opencode.default_for_runtime is False
    assert await default_agent_profile_ready(session) is True


async def test_env_agent_override_preserves_the_current_managed_default(
    session, monkeypatch
):
    assert await reconcile_bootstrap_agent_profile(
        session,
        env={"OMNIGENT_DEFAULT_AGENT_NAME": "codex-default"},
        inventory=_inventory("codex-default"),
    ) is True
    await _add_ready_opencode_builtin_profile(session, monkeypatch)

    selected = await reconcile_managed_default_agent_profile(
        session, env={"OMNIGENT_DEFAULT_AGENT_NAME": "codex-default"}
    )

    assert selected == BOOTSTRAP_PROFILE_ID
    opencode = await session.get(OmnigentAgentProfile, OPENCODE_BUILTIN_PROFILE_ID)
    assert opencode is not None
    assert opencode.default_for_runtime is False


async def test_operator_made_default_is_never_displaced(session, monkeypatch):
    assert await reconcile_bootstrap_agent_profile(
        session,
        env={},
        inventory=_inventory("codex-native-ui"),
    ) is True
    session.add(
        OmnigentAgentProfileAuditEvent(
            profile_id=BOOTSTRAP_PROFILE_ID,
            action="made_default",
            version=1,
            actor_id=None,
            metadata_json={},
        )
    )
    await session.commit()
    await _add_ready_opencode_builtin_profile(session, monkeypatch)

    selected = await reconcile_managed_default_agent_profile(session, env={})

    assert selected == BOOTSTRAP_PROFILE_ID
    opencode = await session.get(OmnigentAgentProfile, OPENCODE_BUILTIN_PROFILE_ID)
    assert opencode is not None
    assert opencode.default_for_runtime is False


async def test_operator_authored_default_is_never_displaced(session, monkeypatch):
    await _add_default_profile(session)
    await _add_ready_opencode_builtin_profile(session, monkeypatch)

    selected = await reconcile_managed_default_agent_profile(session, env={})

    assert selected == "codex-team"
    opencode = await session.get(OmnigentAgentProfile, OPENCODE_BUILTIN_PROFILE_ID)
    assert opencode is not None
    assert opencode.default_for_runtime is False


async def test_superseded_operator_selection_no_longer_pins_the_default(
    session, monkeypatch
):
    """Only the newest ``made_default`` event is a live operator claim.

    Audit rows are immutable, so the profile an operator selected first keeps
    its ``made_default`` row after a later ``/default`` request moves the
    selection. Treating that superseded row as current authority would pin the
    default to a profile the operator already replaced, permanently, once
    automatic reconciliation had fallen back to it.
    """

    assert await reconcile_bootstrap_agent_profile(
        session,
        env={},
        inventory=_inventory("codex-native-ui"),
    ) is True
    await _add_ready_opencode_builtin_profile(session, monkeypatch)
    selected_at = datetime.now(timezone.utc)
    session.add_all(
        [
            OmnigentAgentProfileAuditEvent(
                profile_id=BOOTSTRAP_PROFILE_ID,
                action="made_default",
                version=1,
                actor_id=None,
                metadata_json={},
                created_at=selected_at - timedelta(hours=2),
            ),
            # The operator moved the selection to the OpenCode built-in, which
            # supersedes the Codex bootstrap claim above.
            OmnigentAgentProfileAuditEvent(
                profile_id=OPENCODE_BUILTIN_PROFILE_ID,
                action="made_default",
                version=1,
                actor_id=None,
                metadata_json={},
                created_at=selected_at - timedelta(hours=1),
            ),
        ]
    )
    # Reconciliation previously fell back to the Codex bootstrap profile while
    # the newer selection could not launch.
    bootstrap = await session.get(OmnigentAgentProfile, BOOTSTRAP_PROFILE_ID)
    opencode = await session.get(OmnigentAgentProfile, OPENCODE_BUILTIN_PROFILE_ID)
    assert bootstrap is not None and opencode is not None
    bootstrap.default_for_runtime = True
    opencode.default_for_runtime = False
    await session.commit()

    selected = await reconcile_managed_default_agent_profile(session, env={})

    assert selected == OPENCODE_BUILTIN_PROFILE_ID
    await session.refresh(bootstrap)
    assert bootstrap.default_for_runtime is False


async def test_no_launch_ready_managed_profile_clears_the_stale_default(
    session, monkeypatch
):
    """A managed default must not publish launch authority it cannot honor.

    ``resolve_default_agent_profile_snapshot`` only requires an active profile
    with a version, not a ready validation, so leaving ``default_for_runtime``
    on an unready profile lets a default submission resolve stale authority
    instead of reporting that no default is available.
    """

    assert await reconcile_bootstrap_agent_profile(
        session,
        env={},
        inventory=_inventory("codex-native-ui"),
    ) is True
    bootstrap = await session.get(OmnigentAgentProfile, BOOTSTRAP_PROFILE_ID)
    assert bootstrap is not None
    assert bootstrap.default_for_runtime is True
    version = await session.scalar(
        select(OmnigentAgentProfileVersion).where(
            OmnigentAgentProfileVersion.profile_id == BOOTSTRAP_PROFILE_ID,
            OmnigentAgentProfileVersion.version == bootstrap.active_version,
        )
    )
    assert version is not None
    version.validation_result = {"ready": False}
    await session.commit()

    selected = await reconcile_managed_default_agent_profile(session, env={})

    assert selected is None
    await session.refresh(bootstrap)
    assert bootstrap.default_for_runtime is False
    cleared = await session.scalar(
        select(func.count())
        .select_from(OmnigentAgentProfileAuditEvent)
        .where(
            OmnigentAgentProfileAuditEvent.profile_id == BOOTSTRAP_PROFILE_ID,
            OmnigentAgentProfileAuditEvent.action == "managed_default_cleared",
        )
    )
    assert cleared == 1
