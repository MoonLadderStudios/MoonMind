"""Durable bootstrap default resolution + seeding (MoonLadderStudios/MoonMind#3517 §8)."""
from __future__ import annotations

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
)
from api_service.services.omnigent_agent_bootstrap_service import (
    BOOTSTRAP_PROFILE_ID,
    BootstrapDefaultConflictError,
    build_bootstrap_document,
    reconcile_bootstrap_agent_profile,
    resolve_default_agent_selection,
    seed_bootstrap_agent_profile,
)
from api_service.services.omnigent_agent_profile_service import (
    synchronize_upstream_inventory,
)

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
