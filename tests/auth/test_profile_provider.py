"""Provider credentials resolve by profile and keep their redaction contract."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ManagedSecret,
    SecretStatus,
)
from moonmind.auth.profile_provider import ProfileAuthProvider


@pytest.mark.asyncio
async def test_profile_provider_returns_secret(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        session.add(
            ManagedSecret(slug="github-tok", ciphertext="tok", status=SecretStatus.ACTIVE)
        )
        session.add(
            ManagedAgentProviderProfile(
                profile_id="prof-legacy",
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source="secret_ref",
                runtime_materialization_mode="api_key_env",
                secret_refs={"GITHUB_TOKEN": "db://github-tok"},
                enabled=True,
                auth_state="connected",
            )
        )
        await session.commit()
        provider = ProfileAuthProvider(session)
        secret = await provider.get_secret(key="GITHUB_TOKEN", profile_id="prof-legacy")
        assert secret == "tok"
        assert "redacted-sha256" in repr(secret)
        # user-only legacy input no longer provisions or resolves.
        with pytest.raises(TypeError):
            await provider.get_secret(key="GITHUB_TOKEN", user=object())
