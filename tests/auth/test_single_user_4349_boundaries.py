"""Issue #4349 boundaries: seeding removal, visibility, manager, OAuth, sweep.

Verifies:
- R1: ``UserManager`` register/login hooks never create a legacy
  ``UserProfile`` row, and ``load_execution_configurations`` no longer
  filters private execution configurations by human owner.
- R3: ``AuthProviderManager`` bound to an explicit profile fails closed
  (no ambient env fallback); unbound legacy callers keep prior default.
- R4: two synthetic credentials stay isolated across separate sessions
  used concurrently (real storage boundary).
- R5: legacy owner-encoded OAuth mounts stay readable through the
  transition check and ``refresh_binding_generation`` rewrites them to
  profile-owned mounts; GitHub resolution honors the explicit credential
  and fails closed on blank-explicit instead of selecting ambient creds.
- R6: systematic non-exposure sweep — plaintext secrets absent from
  sanitized settings readbacks, API row payloads, migration summaries,
  logs, argv/URL/Temporal-payload analogues.
"""

import logging
import os
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ManagedSecret,
    OmnigentAgentProfile,
    OmnigentAgentProfileVersion,
    SecretStatus,
    User,
    UserProfile,
)


def _factory(tmp_path, name="bound4349.db"):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


def _provider_profile_row(profile_id, secret_refs, generation=1):
    return ManagedAgentProviderProfile(
        profile_id=profile_id,
        runtime_id="codex_cli",
        provider_id="openai",
        credential_source="secret_ref",
        runtime_materialization_mode="api_key_env",
        secret_refs=secret_refs,
        credential_generation=generation,
        enabled=True,
        auth_state="connected",
    )


@pytest.mark.asyncio
async def test_register_login_hooks_never_create_user_profile(tmp_path):
    from api_service.auth import UserManager

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        user = User(id=uuid.uuid4(), email="op@example.com")
        session.add(user)
        await session.commit()
        manager = UserManager(MagicMock())
        assert await manager._ensure_profile(user) is None
        assert await manager.on_after_register(user) is None
        assert await manager.on_after_login(user) is None
        rows = (await session.execute(select(UserProfile))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_execution_configurations_ignore_human_owner(tmp_path):
    from api_service.services.profile_execution_selection import (
        load_execution_configurations,
    )

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    owner = uuid.uuid4()
    stranger = SimpleNamespace(id=uuid.uuid4())
    async with factory() as session:
        session.add(
            OmnigentAgentProfile(
                profile_id="exec-private",
                display_name="Private exec",
                owner_id=owner,
                visibility="private",
                state="active",
                active_version=1,
            )
        )
        session.add(
            OmnigentAgentProfileVersion(
                profile_id="exec-private",
                version=1,
                digest="sha256:" + "a" * 64,
                document={},
            )
        )
        await session.commit()
        rows = await load_execution_configurations(session, stranger, [])
        assert [row.profile_id for row, _ in rows] == ["exec-private"]


@pytest.mark.asyncio
async def test_manager_suppresses_env_fallback_for_bound_profile(tmp_path, monkeypatch):
    from moonmind.auth.env_provider import EnvAuthProvider
    from moonmind.auth.manager import AuthProviderManager
    from moonmind.auth.profile_provider import ProfileAuthProvider

    monkeypatch.setenv("SYNTHETIC_4349_FALLBACK_KEY", "ambient-value")
    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(
            ManagedSecret(
                slug="bound-key", ciphertext="synthetic-bound", status=SecretStatus.ACTIVE
            )
        )
        session.add(_provider_profile_row("bound-prof", {"OTHER_KEY": "db://bound-key"}))
        await session.commit()
        manager = AuthProviderManager(ProfileAuthProvider(session), EnvAuthProvider())
        # Bound to an explicit profile but asking for an unmapped key: fail
        # closed, never ambient env, never another profile/account/route.
        assert (
            await manager.get_secret(
                "profile",
                key="SYNTHETIC_4349_FALLBACK_KEY",
                profile_id="bound-prof",
            )
            is None
        )
        # Unbound legacy callers keep the prior env-fallback default.
        assert (
            await manager.get_secret("profile", key="SYNTHETIC_4349_FALLBACK_KEY")
            == "ambient-value"
        )
        # Explicit opt-in still permits fallback for bound callers.
        assert (
            await manager.get_secret(
                "profile",
                key="SYNTHETIC_4349_FALLBACK_KEY",
                profile_id="bound-prof",
                allow_env_fallback=True,
            )
            == "ambient-value"
        )


@pytest.mark.asyncio
async def test_two_credentials_isolated_across_concurrent_sessions(tmp_path):
    import asyncio

    from moonmind.auth.profile_provider import ProfileAuthProvider

    engine, factory = _factory(tmp_path, "iso4349.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(
            ManagedSecret(slug="k1", ciphertext="synthetic-1", status=SecretStatus.ACTIVE)
        )
        session.add(
            ManagedSecret(slug="k2", ciphertext="synthetic-2", status=SecretStatus.ACTIVE)
        )
        session.add(_provider_profile_row("p1", {"OPENAI_API_KEY": "db://k1"}))
        session.add(_provider_profile_row("p2", {"OPENAI_API_KEY": "db://k2"}))
        await session.commit()

    async def _resolve(profile_id):
        async with factory() as session:
            provider = ProfileAuthProvider(session)
            return await provider.get_secret(
                key="OPENAI_API_KEY", profile_id=profile_id
            )

    r1, r2 = await asyncio.gather(_resolve("p1"), _resolve("p2"))
    assert r1 == "synthetic-1"
    assert r2 == "synthetic-2"
    r1b, r2b = await asyncio.gather(_resolve("p1"), _resolve("p2"))
    assert (r1b, r2b) == ("synthetic-1", "synthetic-2")


@pytest.mark.asyncio
async def test_oauth_legacy_owner_mount_transitions_to_profile_mount(tmp_path):
    from api_service.db.models import (
        OmnigentOAuthHostBindingRecord,
        ProviderCredentialSource,
        RuntimeMaterializationMode,
    )
    from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository

    engine, factory = _factory(tmp_path, "oauth4349.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    owner = uuid.uuid4()
    async with factory() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="oauth-prof",
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                max_parallel_runs=1,
                credential_generation=1,
                volume_ref="oauth-prof-volume",
                volume_mount_path="/home/app/.codex",
                owner_user_id=owner,
            )
        )
        await session.commit()
    legacy_mount = {
        "authVolumeRef": {
            "providerProfileId": "oauth-prof",
            "runtimeId": "codex_cli",
            "providerId": "openai",
            "volumeRef": "oauth-prof-volume",
            "credentialGeneration": 1,
            "ownerUserId": str(owner),
        },
        "targetPath": "/home/app/.codex",
        "accessMode": "read_write",
        "runtimeUid": 1000,
        "runtimeGid": 1000,
    }
    async with factory() as session:
        session.add(
            OmnigentOAuthHostBindingRecord(
                binding_ref="omnigent-oauth:oauth-prof",
                provider_profile_id="oauth-prof",
                endpoint_ref="default",
                harness="codex-native",
                credential_mount_template_json=legacy_mount,
            )
        )
        await session.commit()
    repo = OmnigentOAuthHostRepository(factory)
    # Legacy owner-encoded row stays readable through the transition check.
    binding = await repo.get_binding_for_profile("oauth-prof")
    assert binding is not None
    assert binding.provider_profile_id == "oauth-prof"
    # Refresh rewrites the mount to profile identity and stays operational.
    refreshed = await repo.refresh_binding_generation("oauth-prof")
    assert refreshed is not None
    assert refreshed.credential_mount_ref.auth_volume_ref.owner_user_id == (
        "profile:oauth-prof"
    )
    reread = await repo.get_binding_for_profile("oauth-prof")
    assert reread == refreshed


@pytest.mark.asyncio
async def test_github_explicit_token_scoping_is_fail_closed(monkeypatch):
    from moonmind.auth.github_credentials import (
        GitHubCredentialSource,
        resolve_github_credential,
    )

    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token")
    monkeypatch.setenv("GH_TOKEN", "ambient-token-2")
    resolved = await resolve_github_credential(
        "synthetic-gh-explicit", repo="owner/repo"
    )
    assert resolved.resolved is True
    assert resolved.token == "synthetic-gh-explicit"
    assert resolved.source == GitHubCredentialSource.EXPLICIT
    # Blank-explicit is configured-empty: fail closed, never ambient.
    blank = await resolve_github_credential("   ", repo="owner/repo")
    assert blank.resolved is False
    assert blank.token == ""
    assert blank.source == GitHubCredentialSource.UNRESOLVABLE


@pytest.mark.asyncio
async def test_secret_non_exposure_sweep(tmp_path, caplog):
    from api_service.api.routers.provider_profiles import _row_to_dict
    from api_service.services.profile_secret_migration import (
        migrate_legacy_profile_secrets,
    )
    from moonmind.auth.profile_provider import ProfileAuthProvider

    plaintext_a = "sweep-synthetic-A"
    plaintext_b = "sweep-synthetic-B"
    engine, factory = _factory(tmp_path, "sweep4349.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        user = User(id=uuid.uuid4(), email="sweep@example.com")
        session.add(user)
        await session.flush()
        session.add(UserProfile(user_id=user.id, openai_api_key_encrypted=plaintext_a))
        session.add(
            ManagedSecret(
                slug="sweep-key", ciphertext=plaintext_b, status=SecretStatus.ACTIVE
            )
        )
        session.add(_provider_profile_row("sweep-prof", {"OPENAI_API_KEY": "db://sweep-key"}))
        await session.commit()

        with caplog.at_level(logging.INFO):
            migration = await migrate_legacy_profile_secrets(session)
            provider = ProfileAuthProvider(session)
            secret = await provider.get_secret(
                key="OPENAI_API_KEY", profile_id="sweep-prof"
            )
            assert secret == plaintext_b
            logging.getLogger("sweep-test").info("resolved sweep-prof actief")
            sanitized = await __import__(
                "api_service.services.profile_service", fromlist=["ProfileService"]
            ).ProfileService().get_sanitized_profile_by_user_id(session, user.id)
            from api_service.api.routers.provider_profiles import (
                _secret_ref_results_for_rows,
            )

            profile = await session.get(ManagedAgentProviderProfile, "sweep-prof")
            payload = _row_to_dict(
                profile,
                secret_ref_results=_secret_ref_results_for_rows([profile]).get(
                    "sweep-prof"
                ),
            )

        surfaces = {
            "migration_summary": str(migration),
            "sanitized_settings": str(sanitized),
            "api_payload": str(payload),
            "logs": caplog.text,
            "argv": f"run --profile sweep-prof --ref {payload['secret_refs']}",
            "url": f"https://api.example.com/profiles/sweep-prof?ref={payload['secret_refs']}",
            "temporal_payload": str(
                {"profile_id": "sweep-prof", "refs": payload["secret_refs"]}
            ),
        }
        for name, surface in surfaces.items():
            assert plaintext_a not in surface, name
            assert plaintext_b not in surface, name
        # References (not values) remain usable for binding.
        assert payload["secret_refs"] == {"OPENAI_API_KEY": "db://sweep-key"}
        assert os.environ.get("SYNTHETIC_4349_SWEEP_UNSET_XYZ") is None
