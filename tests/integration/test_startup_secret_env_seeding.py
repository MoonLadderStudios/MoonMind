from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import Base, ManagedSecret, SecretStatus
from api_service.main import startup_event


async def _seed_db(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_startup_syncs_managed_secrets_from_env(monkeypatch, disabled_env_keys, tmp_path):
    await _seed_db(tmp_path)

    monkeypatch.setenv("GITHUB_TOKEN", "ghp-test-token")
    monkeypatch.setenv("ATLASSIAN_API_KEY", "atl-token")

    with (
        patch("api_service.main._initialize_embedding_model"),
        patch("api_service.main._initialize_vector_store"),
        patch("api_service.main._initialize_contexts"),
        patch("api_service.main._load_or_create_vector_index"),
        patch("api_service.main._initialize_oidc_provider"),
    ):
        await startup_event()

    async with db_base.async_session_maker() as session:
        github_secret = (
            await session.execute(select(ManagedSecret).where(ManagedSecret.slug == "GITHUB_TOKEN"))
        ).scalar_one_or_none()
        atlassian_secret = (
            await session.execute(
                select(ManagedSecret).where(ManagedSecret.slug == "ATLASSIAN_API_KEY")
            )
        ).scalar_one_or_none()

    assert github_secret is not None
    assert github_secret.status == SecretStatus.ACTIVE
    assert github_secret.ciphertext == "ghp-test-token"
    assert github_secret.details.get("imported_from") == ".env"
    assert atlassian_secret is not None
    assert atlassian_secret.status == SecretStatus.ACTIVE
    assert atlassian_secret.ciphertext == "atl-token"
    assert atlassian_secret.details.get("imported_from") == ".env"


@pytest.mark.asyncio
async def test_startup_updates_existing_env_managed_secret(monkeypatch, disabled_env_keys, tmp_path):
    await _seed_db(tmp_path)

    async with db_base.async_session_maker() as session:
        existing_secret = ManagedSecret(
            slug="GITHUB_TOKEN",
            ciphertext="old-token",
            status=SecretStatus.ACTIVE,
            details={"imported_from": ".env", "migrated_at": "older"},
        )
        session.add(existing_secret)
        await session.commit()

    monkeypatch.setenv("GITHUB_TOKEN", "new-github-token")

    with (
        patch("api_service.main._initialize_embedding_model"),
        patch("api_service.main._initialize_vector_store"),
        patch("api_service.main._initialize_contexts"),
        patch("api_service.main._load_or_create_vector_index"),
        patch("api_service.main._initialize_oidc_provider"),
    ):
        await startup_event()

    async with db_base.async_session_maker() as session:
        refreshed = (
            await session.execute(select(ManagedSecret).where(ManagedSecret.slug == "GITHUB_TOKEN"))
        ).scalar_one()

    assert refreshed.ciphertext == "new-github-token"
    assert refreshed.details.get("migrated_at") is not None


@pytest.mark.asyncio
async def test_startup_syncs_managed_secrets_from_dotenv_file(
    monkeypatch, disabled_env_keys, tmp_path
):
    await _seed_db(tmp_path)

    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "GITHUB_TOKEN=ghp-dotenv-token\nATLASSIAN_API_KEY=atl-dotenv-token\n"
    )
    monkeypatch.setattr("moonmind.config.paths.ENV_FILE", dotenv_path)

    with (
        patch("api_service.main._initialize_embedding_model"),
        patch("api_service.main._initialize_vector_store"),
        patch("api_service.main._initialize_contexts"),
        patch("api_service.main._load_or_create_vector_index"),
        patch("api_service.main._initialize_oidc_provider"),
    ):
        await startup_event()

    async with db_base.async_session_maker() as session:
        github_secret = (
            await session.execute(
                select(ManagedSecret).where(ManagedSecret.slug == "GITHUB_TOKEN")
            )
        ).scalar_one_or_none()
        atlassian_secret = (
            await session.execute(
                select(ManagedSecret).where(
                    ManagedSecret.slug == "ATLASSIAN_API_KEY"
                )
            )
        ).scalar_one_or_none()

    assert github_secret is not None
    assert github_secret.ciphertext == "ghp-dotenv-token"
    assert atlassian_secret is not None
    assert atlassian_secret.ciphertext == "atl-dotenv-token"
