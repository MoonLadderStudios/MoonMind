import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.auth import _DEFAULT_USER_ID
from api_service.db import base as db_base
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ProviderCredentialSource,
    ProviderProfileAuthState,
    UserProfile,
)
from api_service.main import startup_event

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.asyncio
async def test_startup_profile_seeding(disabled_env_keys, tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    with (patch("api_service.main._initialize_oidc_provider"),):
        await startup_event()

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(UserProfile).where(
                UserProfile.user_id == uuid.UUID(_DEFAULT_USER_ID)
            )
        )
        profile = result.scalars().first()
        assert profile is not None
        assert profile.openai_api_key_encrypted is not None
        assert profile.google_api_key_encrypted is not None
        zen = await session.get(
            ManagedAgentProviderProfile,
            "opencode-zen-free",
        )
        assert zen is not None
        assert zen.enabled is True
        assert zen.auth_state == ProviderProfileAuthState.CONNECTED
        assert zen.credential_source == ProviderCredentialSource.NONE
        assert zen.secret_refs == {}
        assert zen.default_model == "opencode/muse-spark-1.3-contributor-free"
        assert zen.default_effort == "xhigh"
        # MoonLadderStudios/MoonMind#3877: the credentialless Zen seed holds
        # runtime-default authority for `opencode` straight out of startup.
        assert zen.is_default is True
        opencode_defaults = list(
            (
                await session.execute(
                    select(ManagedAgentProviderProfile.profile_id).where(
                        ManagedAgentProviderProfile.runtime_id == "opencode",
                        ManagedAgentProviderProfile.is_default.is_(True),
                    )
                )
            ).scalars()
        )
        assert opencode_defaults == ["opencode-zen-free"]


@pytest.mark.asyncio
async def test_startup_reclaims_a_released_opencode_runtime_default(
    disabled_env_keys, tmp_path
):
    """Replay coverage for the persisted ``is_default`` flag at real startup.

    MoonLadderStudios/MoonMind#3877 changed which profile owns runtime-default
    authority for ``opencode``. Deployments upgrading from the previous release
    carry a persisted assignment that no longer matches the contract, and the
    seed only applied ``is_default`` on INSERT, so a restart left it alone. A
    restart must now settle the default on the credentialless Zen profile,
    because neither documented release condition (an explicit operator disable,
    or an explicit default selection) is recorded on the rows.
    """

    db_url = f"sqlite+aiosqlite:///{tmp_path}/restart.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    with (patch("api_service.main._initialize_oidc_provider"),):
        await startup_event()

    # Model the pre-change persisted state: the Zen row exists but does not
    # hold runtime-default authority, and nothing records an operator choice.
    async with db_base.async_session_maker() as session:
        zen = await session.get(ManagedAgentProviderProfile, "opencode-zen-free")
        assert zen is not None
        zen.is_default = False
        zen.default_selected_by_operator = False
        await session.commit()

    with (patch("api_service.main._initialize_oidc_provider"),):
        await startup_event()

    async with db_base.async_session_maker() as session:
        zen = await session.get(ManagedAgentProviderProfile, "opencode-zen-free")
        assert zen is not None
        assert zen.enabled is True
        assert zen.is_default is True
        assert zen.default_selected_by_operator is False
        opencode_defaults = list(
            (
                await session.execute(
                    select(ManagedAgentProviderProfile.profile_id).where(
                        ManagedAgentProviderProfile.runtime_id == "opencode",
                        ManagedAgentProviderProfile.is_default.is_(True),
                    )
                )
            ).scalars()
        )
        assert opencode_defaults == ["opencode-zen-free"]
