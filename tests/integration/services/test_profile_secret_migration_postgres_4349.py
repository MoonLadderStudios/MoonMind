"""Real-PostgreSQL legacy profile-secret conversion evidence (#4349 R2).

Proves the production conversion path on PostgreSQL, not SQLite: eligible
legacy ``UserProfile``-held secrets convert into ``ManagedSecret`` rows with
deterministic collision-safe slugs, distinct source credentials stay distinct
even when their values match, profile IDs/bindings and effective settings are
preserved across transactional rewiring, encrypted content round-trips (raw
stored bytes are not plaintext), reruns converge, interrupted runs resume via
the reuse path, and multi-operator databases are blocked without mutation.
Runs an ephemeral local cluster with no manually managed test service
(mirrors the control-plane Postgres binding in
``test_account_lifecycle_postgres_4122.py``).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    ManagedAgentProviderProfile,
    ManagedSecret,
    SecretStatus,
    User,
    UserProfile,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

_TABLES = (
    User.__table__,
    UserProfile.__table__,
    ManagedSecret.__table__,
    ManagedAgentProviderProfile.__table__,
)


@pytest.fixture
def profile_secret_postgres_url():
    """Ephemeral PostgreSQL; honors MOONMIND_TEST_POSTGRES_URL when set."""
    configured = os.getenv("MOONMIND_TEST_POSTGRES_URL", "").strip()
    if configured:
        if configured.startswith("postgresql://"):
            configured = configured.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        if not configured.startswith("postgresql+asyncpg://"):
            pytest.fail("MOONMIND_TEST_POSTGRES_URL must use PostgreSQL")
        yield configured
        return
    initdb_path = shutil.which("initdb")
    if initdb_path is None:
        candidates = sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb"))
        initdb_path = str(candidates[-1]) if candidates else None
    if initdb_path is None:
        pytest.fail(
            "PostgreSQL test binaries are unavailable in the Python test image"
        )
    initdb = str(Path(initdb_path))
    pg_ctl = str(Path(initdb).with_name("pg_ctl"))
    data_root = Path(tempfile.mkdtemp(prefix="moonmind-profile-secret-postgres-"))
    data_dir = data_root / "data"
    log_path = data_root / "postgres.log"
    command_prefix: list[str] = []
    if os.geteuid() == 0:
        shutil.chown(data_root, user="postgres", group="postgres")
        command_prefix = ["runuser", "--user", "postgres", "--"]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    subprocess.run(
        [*command_prefix, initdb, "--pgdata", str(data_dir), "--username", "postgres",
         "--auth", "trust", "--encoding", "UTF8", "--no-locale"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        [*command_prefix, pg_ctl, "--pgdata", str(data_dir), "--log", str(log_path),
         "--options", f"-F -p {port} -h 127.0.0.1 -k {data_dir}", "--wait", "start"],
        check=True, capture_output=True, text=True,
    )
    try:
        yield f"postgresql+asyncpg://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(
            [*command_prefix, pg_ctl, "--pgdata", str(data_dir), "--mode", "immediate",
             "--wait", "stop"],
            check=False, capture_output=True, text=True,
        )
        shutil.rmtree(data_root, ignore_errors=True)


@pytest_asyncio.fixture()
async def pg_maker(profile_secret_postgres_url):
    engine = create_async_engine(profile_secret_postgres_url)
    async with engine.begin() as conn:
        for table in _TABLES:
            await conn.run_sync(table.create, checkfirst=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        async with engine.begin() as conn:
            for table in reversed(_TABLES):
                await conn.run_sync(table.drop, checkfirst=True)
        await engine.dispose()


def _profile_row(profile_id: str, **overrides):
    params = {
        "profile_id": profile_id,
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "credential_generation": 1,
        "enabled": True,
        "auth_state": "connected",
        "default_model": "gpt-5",
        "default_effort": "high",
    }
    params.update(overrides)
    return ManagedAgentProviderProfile(**params)


@pytest.mark.asyncio
async def test_postgres_conversion_preserves_secrets_ids_and_settings(pg_maker) -> None:
    """Distinct secrets, profile IDs/bindings, encrypted content, settings."""
    from api_service.services.profile_secret_migration import (
        legacy_secret_slug,
        run_single_operator_profile_secret_conversion,
    )

    async with pg_maker() as session:
        user = User(id=uuid.uuid4(), email="pg-solo@example.invalid")
        session.add(user)
        await session.flush()
        session.add(
            UserProfile(
                user_id=user.id,
                openai_api_key_encrypted="pg-same-value",
                github_token_encrypted="pg-same-value",
            )
        )
        session.add(_profile_row("pg-prof"))
        await session.commit()

        slug_openai = legacy_secret_slug(user.id, "openai_api_key")
        slug_github = legacy_secret_slug(user.id, "github_token")
        assert slug_openai != slug_github

        summary = await run_single_operator_profile_secret_conversion(
            session,
            profile_rewires={
                "pg-prof": {
                    "OPENAI_API_KEY": f"db://{slug_openai}",
                    "GITHUB_TOKEN": f"db://{slug_github}",
                }
            },
        )
        assert summary["eligibility"]["eligible"] is True
        assert summary["migration"]["migrated"] == 2
        assert summary["migration"]["created"] == 2
        assert summary["rewires"][0]["outcome"] == "rewired"
        assert summary["rewires"][0]["credential_generation"] == 2
        assert "pg-same-value" not in str(summary)

        # Encrypted content round-trips through the ORM decryption layer.
        rows = (await session.execute(select(ManagedSecret))).scalars().all()
        by_slug = {row.slug: row for row in rows}
        assert {by_slug[slug_openai].ciphertext, by_slug[slug_github].ciphertext} == {
            "pg-same-value"
        }
        assert all(row.status == SecretStatus.ACTIVE for row in by_slug.values())

        # Profile IDs, bindings, and effective settings preserved.
        profile = await session.get(ManagedAgentProviderProfile, "pg-prof")
        assert profile.profile_id == "pg-prof"
        assert profile.runtime_id == "codex_cli"
        assert profile.provider_id == "openai"
        assert profile.secret_refs == {
            "OPENAI_API_KEY": f"db://{slug_openai}",
            "GITHUB_TOKEN": f"db://{slug_github}",
        }
        assert profile.default_model == "gpt-5"
        assert profile.default_effort == "high"

    # Raw stored bytes are encrypted at rest, never plaintext.
    async with pg_maker() as session:
        raw = (
            await session.execute(text("SELECT slug, ciphertext FROM managed_secrets"))
        ).all()
        assert len(raw) == 2
        for _slug, stored in raw:
            assert "pg-same-value" not in str(stored)


@pytest.mark.asyncio
async def test_postgres_rerun_and_interruption_safe(pg_maker) -> None:
    """Reruns converge; interrupted runs resume through the reuse path."""
    from api_service.services.profile_secret_migration import (
        legacy_secret_slug,
        run_single_operator_profile_secret_conversion,
    )

    async with pg_maker() as session:
        user = User(id=uuid.uuid4(), email="pg-rerun@example.invalid")
        session.add(user)
        await session.flush()
        session.add(UserProfile(user_id=user.id, github_token_encrypted="pg-tok-1"))
        await session.commit()

        first = await run_single_operator_profile_secret_conversion(session)
        assert first["migration"]["created"] == 1
        slugs_first = {item["slug"] for item in first["migration"]["items"]}

        # Full rerun converges without duplicating or changing slugs.
        second = await run_single_operator_profile_secret_conversion(session)
        assert second["migration"]["created"] == 0
        assert {item["slug"] for item in second["migration"]["items"]} == slugs_first

        # Interrupted run: a second credential appears after the first
        # conversion committed; the next run converts only the newcomer and
        # reuses the already-converted secret.
        legacy = (
            await session.execute(
                select(UserProfile).where(UserProfile.user_id == user.id)
            )
        ).scalar_one()
        legacy.openai_api_key_encrypted = "pg-tok-2"
        await session.commit()
        third = await run_single_operator_profile_secret_conversion(session)
        assert third["migration"]["created"] == 1
        assert third["migration"]["reused"] == 1
        slugs_third = {item["slug"] for item in third["migration"]["items"]}
        assert slugs_first < slugs_third
        assert legacy_secret_slug(user.id, "openai_api_key") in slugs_third

        rows = (await session.execute(select(ManagedSecret))).scalars().all()
        assert len(rows) == 2


@pytest.mark.asyncio
async def test_postgres_multi_operator_blocked_without_mutation(pg_maker) -> None:
    """Multi-operator databases are blocked before any write on PostgreSQL."""
    from api_service.services.profile_secret_migration import (
        MultiOperatorAttributionError,
        run_single_operator_profile_secret_conversion,
    )

    async with pg_maker() as session:
        u1 = User(id=uuid.uuid4(), email="pg-a@example.invalid")
        u2 = User(id=uuid.uuid4(), email="pg-b@example.invalid")
        session.add_all([u1, u2])
        await session.flush()
        session.add(UserProfile(user_id=u1.id, openai_api_key_encrypted="pg-tok-a"))
        session.add(UserProfile(user_id=u2.id, openai_api_key_encrypted="pg-tok-b"))
        session.add(_profile_row("pg-prof-blocked"))
        await session.commit()

        with pytest.raises(MultiOperatorAttributionError):
            await run_single_operator_profile_secret_conversion(
                session,
                profile_rewires={
                    "pg-prof-blocked": {"OPENAI_API_KEY": "db://anything"}
                },
            )
        assert (await session.execute(select(ManagedSecret))).scalars().all() == []
        profile = await session.get(ManagedAgentProviderProfile, "pg-prof-blocked")
        assert (profile.secret_refs or {}) == {}
        assert profile.credential_generation == 1
