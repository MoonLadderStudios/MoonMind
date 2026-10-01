from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import Base, ManagedSecret, SecretStatus
from api_service.main import startup_event

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

async def _seed_db(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

def _isolate_secret_env_sources(monkeypatch, tmp_path) -> None:
    empty_dotenv = tmp_path / ".env.empty"
    empty_dotenv.write_text("", encoding="utf-8")
    monkeypatch.setattr("moonmind.config.paths.ENV_FILE", empty_dotenv)
    for key in ("GITHUB_TOKEN", "GITHUB_PAT", "ATLASSIAN_API_KEY"):
        monkeypatch.delenv(key, raising=False)

async def _secret(slug: str):
    async with db_base.async_session_maker() as session:
        return (
            await session.execute(select(ManagedSecret).where(ManagedSecret.slug == slug))
        ).scalar_one_or_none()

@pytest.mark.asyncio
async def test_startup_syncs_managed_secrets_from_env(monkeypatch, disabled_env_keys, tmp_path):
    await _seed_db(tmp_path)
    _isolate_secret_env_sources(monkeypatch, tmp_path)

    monkeypatch.setenv("GITHUB_TOKEN", "ghp-test-token")
    monkeypatch.setenv("GITHUB_PAT", "ghp-test-pat")
    monkeypatch.setenv("ATLASSIAN_API_KEY", "atl-token")

    with (
        patch("api_service.main._initialize_oidc_provider"),
    ):
        await startup_event()

    atlassian_secret = await _secret("ATLASSIAN_API_KEY")
    assert atlassian_secret is not None
    assert atlassian_secret.status == SecretStatus.ACTIVE
    assert atlassian_secret.ciphertext == "atl-token"
    assert atlassian_secret.details.get("imported_from") == ".env"
    # MoonLadderStudios/MoonMind#4023: the GitHub credential is a reference on
    # the default repository connection, not a token copied on every boot.
    assert await _secret("GITHUB_TOKEN") is None
    assert await _secret("GITHUB_PAT") is None

@pytest.mark.asyncio
async def test_startup_updates_existing_env_managed_secret(monkeypatch, disabled_env_keys, tmp_path):
    await _seed_db(tmp_path)
    _isolate_secret_env_sources(monkeypatch, tmp_path)

    async with db_base.async_session_maker() as session:
        session.add_all(
            [
                ManagedSecret(
                    slug="ATLASSIAN_API_KEY",
                    ciphertext="old-atlassian-key",
                    status=SecretStatus.ACTIVE,
                    details={"imported_from": ".env", "migrated_at": "older"},
                ),
                ManagedSecret(
                    slug="GITHUB_TOKEN",
                    ciphertext="recorded-github-token",
                    status=SecretStatus.ACTIVE,
                    details={"imported_from": ".env", "migrated_at": "older"},
                ),
            ]
        )
        await session.commit()

    monkeypatch.setenv("ATLASSIAN_API_KEY", "new-atlassian-key")
    monkeypatch.setenv("GITHUB_TOKEN", "new-github-token")
    monkeypatch.setenv("GITHUB_PAT", "new-github-pat")

    with (
        patch("api_service.main._initialize_oidc_provider"),
    ):
        await startup_event()

    refreshed = await _secret("ATLASSIAN_API_KEY")
    assert refreshed.ciphertext == "new-atlassian-key"
    assert refreshed.details.get("migrated_at") != "older"
    # A migrated connection may name db://GITHUB_TOKEN; startup leaves the
    # operator's managed secret as recorded instead of overwriting it.
    recorded = await _secret("GITHUB_TOKEN")
    assert recorded.ciphertext == "recorded-github-token"
    assert recorded.details.get("migrated_at") == "older"
    assert await _secret("GITHUB_PAT") is None

@pytest.mark.asyncio
async def test_startup_syncs_managed_secrets_from_dotenv_file(
    monkeypatch, disabled_env_keys, tmp_path
):
    await _seed_db(tmp_path)
    _isolate_secret_env_sources(monkeypatch, tmp_path)

    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "GITHUB_TOKEN=ghp-dotenv-token\nATLASSIAN_API_KEY=atl-dotenv-token\n"
    )
    monkeypatch.setattr("moonmind.config.paths.ENV_FILE", dotenv_path)

    with (
        patch("api_service.main._initialize_oidc_provider"),
    ):
        await startup_event()

    atlassian_secret = await _secret("ATLASSIAN_API_KEY")
    assert atlassian_secret is not None
    assert atlassian_secret.ciphertext == "atl-dotenv-token"
    assert await _secret("GITHUB_TOKEN") is None

@pytest.mark.asyncio
async def test_startup_ignores_whitespace_only_env_tokens(
    monkeypatch, disabled_env_keys, tmp_path
):
    await _seed_db(tmp_path)
    _isolate_secret_env_sources(monkeypatch, tmp_path)

    monkeypatch.setenv("ATLASSIAN_API_KEY", "\t")

    with (
        patch("api_service.main._initialize_oidc_provider"),
    ):
        await startup_event()

    assert await _secret("ATLASSIAN_API_KEY") is None
