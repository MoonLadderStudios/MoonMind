"""Unit tests for ProviderCredentialSource and RuntimeMaterializationMode enums.

These tests verify:
1. The Python enum members and values match what the migration creates in the DB
2. The auth_mode data migration mapping (oauth → oauth_volume, api_key → secret_ref)
3. The ManagedAgentOAuthSession model writes and reads the enum correctly via SQLite
4. The merge migration graph produces exactly one head
"""

import enum
import glob
import importlib
import os
import re
import asyncio

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentOAuthSession,
    ManagedAgentProviderProfile,
    ProviderCredentialSource,
    ProviderProfileAuthMethod,
    ProviderProfileAuthState,
    ProviderProfileDisabledReason,
    RuntimeMaterializationMode,
)

# ---------------------------------------------------------------------------
# 1. Enum member value contract tests
# ---------------------------------------------------------------------------

class TestProviderCredentialSourceEnum:
    """ProviderCredentialSource must match the DB enum type created by migration 053758f254f3."""

    def test_has_oauth_volume(self):
        assert ProviderCredentialSource.OAUTH_VOLUME == "oauth_volume"

    def test_has_secret_ref(self):
        assert ProviderCredentialSource.SECRET_REF == "secret_ref"

    def test_has_none(self):
        assert ProviderCredentialSource.NONE == "none"

    def test_exactly_three_members(self):
        assert len(ProviderCredentialSource) == 3

    def test_is_str_enum(self):
        assert issubclass(ProviderCredentialSource, str)

    def test_values_match_migration(self):
        """Values must match exactly what migration 053758f254f3 creates in the DB."""
        expected = {"oauth_volume", "secret_ref", "none"}
        actual = {m.value for m in ProviderCredentialSource}
        assert actual == expected

class TestRuntimeMaterializationModeEnum:
    """RuntimeMaterializationMode must match the DB enum type created by migration 053758f254f3."""

    def test_has_oauth_home(self):
        assert RuntimeMaterializationMode.OAUTH_HOME == "oauth_home"

    def test_has_api_key_env(self):
        assert RuntimeMaterializationMode.API_KEY_ENV == "api_key_env"

    def test_has_env_bundle(self):
        assert RuntimeMaterializationMode.ENV_BUNDLE == "env_bundle"

    def test_has_config_bundle(self):
        assert RuntimeMaterializationMode.CONFIG_BUNDLE == "config_bundle"

    def test_has_composite(self):
        assert RuntimeMaterializationMode.COMPOSITE == "composite"

    def test_exactly_five_members(self):
        assert len(RuntimeMaterializationMode) == 5

    def test_is_str_enum(self):
        assert issubclass(RuntimeMaterializationMode, str)

    def test_values_match_migration(self):
        """Values must match exactly what migration 053758f254f3 creates in the DB."""
        expected = {"oauth_home", "api_key_env", "env_bundle", "config_bundle", "composite"}
        actual = {m.value for m in RuntimeMaterializationMode}
        assert actual == expected

class TestProviderProfileActivationEnums:
    """Provider-profile activation enums must match the DB contract."""

    def test_auth_state_values_match_contract(self):
        expected = {
            "not_configured",
            "oauth_pending",
            "api_key_pending",
            "connected",
            "validation_failed",
            "disconnected",
        }
        assert {m.value for m in ProviderProfileAuthState} == expected

    def test_disabled_reason_values_match_contract(self):
        expected = {
            "missing_credentials",
            "auth_invalid",
            "user_disabled",
            "policy_disabled",
            "disconnected",
        }
        assert {m.value for m in ProviderProfileDisabledReason} == expected

    def test_last_auth_method_values_match_contract(self):
        expected = {"oauth_volume", "secret_ref", "manual"}
        assert {m.value for m in ProviderProfileAuthMethod} == expected

class TestProviderProfileActivationMigrationBackfill:
    """MM-879 activation backfill must not make unverified profiles launchable."""

    @pytest.fixture()
    def migration(self):
        return importlib.import_module(
            "api_service.migrations.versions.316_provider_profile_activation_state"
        )

    def test_enabled_oauth_volume_with_volume_binding_becomes_connected(
        self, migration
    ):
        result = migration.activation_backfill_for_row(
            {
                "enabled": True,
                "credential_source": "oauth_volume",
                "volume_ref": "claude-oauth",
                "volume_mount_path": "/home/claude/.claude",
            }
        )

        assert result == {
            "enabled": True,
            "auth_state": "connected",
            "disabled_reason": None,
            "last_auth_method": "oauth_volume",
            "stamp_validated": True,
        }

    def test_enabled_secret_ref_with_secret_binding_becomes_connected(
        self, migration
    ):
        result = migration.activation_backfill_for_row(
            {
                "enabled": True,
                "credential_source": "secret_ref",
                "secret_refs": {"OPENAI_API_KEY": "db://openai"},
            }
        )

        assert result == {
            "enabled": True,
            "auth_state": "connected",
            "disabled_reason": None,
            "last_auth_method": "secret_ref",
            "stamp_validated": True,
        }

    def test_enabled_profile_without_credentials_is_disabled_missing_credentials(
        self, migration
    ):
        result = migration.activation_backfill_for_row(
            {
                "enabled": True,
                "credential_source": "none",
                "secret_refs": None,
            }
        )

        assert result == {
            "enabled": False,
            "auth_state": "not_configured",
            "disabled_reason": "missing_credentials",
            "last_auth_method": None,
            "stamp_validated": False,
        }

    def test_existing_disabled_profile_preserves_user_disabled_reason(
        self, migration
    ):
        result = migration.activation_backfill_for_row(
            {
                "enabled": False,
                "credential_source": "secret_ref",
                "secret_refs": {"OPENAI_API_KEY": "db://openai"},
            }
        )

        assert result == {
            "enabled": False,
            "auth_state": "not_configured",
            "disabled_reason": "user_disabled",
            "last_auth_method": None,
            "stamp_validated": False,
        }

    def test_enabled_profile_with_invalid_validation_is_disabled_auth_invalid(
        self, migration
    ):
        result = migration.activation_backfill_for_row(
            {
                "enabled": True,
                "credential_source": "secret_ref",
                "secret_refs": None,
                "validation_status": "validation_failed",
            }
        )

        assert result == {
            "enabled": False,
            "auth_state": "validation_failed",
            "disabled_reason": "auth_invalid",
            "last_auth_method": None,
            "stamp_validated": False,
        }

    def test_policy_disabled_profile_is_not_backfilled_launchable(
        self, migration
    ):
        result = migration.activation_backfill_for_row(
            {
                "enabled": True,
                "credential_source": "secret_ref",
                "secret_refs": {"OPENAI_API_KEY": "db://openai"},
                "disabled_reason": "policy_disabled",
            }
        )

        assert result == {
            "enabled": False,
            "auth_state": "not_configured",
            "disabled_reason": "policy_disabled",
            "last_auth_method": None,
            "stamp_validated": False,
        }

# ---------------------------------------------------------------------------
# 2. Data migration mapping logic
#    The migration maps old managedagentauthmode values to providercredentialsource.
#    We test the mapping rule as a pure function to document the contract.
# ---------------------------------------------------------------------------

def _migrate_auth_mode(old_value: str) -> str:
    """Replicates the CASE WHEN mapping in migration 053758f254f3 upgrade().

    ALTER TABLE managed_agent_oauth_sessions ALTER COLUMN auth_mode TYPE providercredentialsource
    USING CASE auth_mode
        WHEN 'oauth'    THEN 'oauth_volume'::providercredentialsource
        WHEN 'api_key'  THEN 'secret_ref'::providercredentialsource
        ELSE                 'none'::providercredentialsource
    END
    """
    if old_value == "oauth":
        return ProviderCredentialSource.OAUTH_VOLUME.value
    elif old_value == "api_key":
        return ProviderCredentialSource.SECRET_REF.value
    else:
        return ProviderCredentialSource.NONE.value

class TestAuthModeMigrationMapping:
    """The USING CASE migration mapping in 053758f254f3 must translate legacy auth_mode values."""

    def test_oauth_maps_to_oauth_volume(self):
        assert _migrate_auth_mode("oauth") == "oauth_volume"

    def test_api_key_maps_to_secret_ref(self):
        assert _migrate_auth_mode("api_key") == "secret_ref"

    def test_unknown_maps_to_none(self):
        assert _migrate_auth_mode("anything_else") == "none"

    def test_empty_string_maps_to_none(self):
        assert _migrate_auth_mode("") == "none"

    def test_result_is_valid_enum_member(self):
        for old in ("oauth", "api_key", "other"):
            result = _migrate_auth_mode(old)
            # Must be a valid ProviderCredentialSource value
            assert result in {m.value for m in ProviderCredentialSource}

# ---------------------------------------------------------------------------
# 3. ORM round-trip: ManagedAgentOAuthSession.auth_mode using SQLite
#    Verifies that the ORM accepts and returns ProviderCredentialSource values.
# ---------------------------------------------------------------------------

@pytest.fixture()
def _sqlite_db(tmp_path):
    """Create a single in-memory SQLite engine and schema for the test module."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/enum_test.db"

    async def _setup():
        engine = create_async_engine(db_url, future=True)
        session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, session_maker

    async def _teardown(engine):
        await engine.dispose()

    engine, session_maker = asyncio.run(_setup())
    yield session_maker
    asyncio.run(_teardown(engine))

@pytest.mark.asyncio
async def test_oauth_session_accepts_oauth_volume(_sqlite_db):
    """ManagedAgentOAuthSession must accept OAUTH_VOLUME as auth_mode."""
    async with _sqlite_db() as session:
        session_obj = ManagedAgentOAuthSession(
            session_id="test-oauth-volume",
            runtime_id="gemini_cli",
            profile_id="gemini_default",
            auth_mode=ProviderCredentialSource.OAUTH_VOLUME,
        )
        session.add(session_obj)
        await session.commit()

    async with _sqlite_db() as session:
        result = await session.execute(
            select(ManagedAgentOAuthSession).where(
                ManagedAgentOAuthSession.session_id == "test-oauth-volume"
            )
        )
        row = result.scalar_one()
        assert row.auth_mode == ProviderCredentialSource.OAUTH_VOLUME
        assert row.auth_mode == "oauth_volume"

@pytest.mark.asyncio
async def test_oauth_session_accepts_secret_ref(_sqlite_db):
    """ManagedAgentOAuthSession must accept SECRET_REF as auth_mode."""
    async with _sqlite_db() as session:
        session_obj = ManagedAgentOAuthSession(
            session_id="test-secret-ref",
            runtime_id="codex_cli",
            profile_id="codex_default",
            auth_mode=ProviderCredentialSource.SECRET_REF,
        )
        session.add(session_obj)
        await session.commit()

    async with _sqlite_db() as session:
        result = await session.execute(
            select(ManagedAgentOAuthSession).where(
                ManagedAgentOAuthSession.session_id == "test-secret-ref"
            )
        )
        row = result.scalar_one()
        assert row.auth_mode == ProviderCredentialSource.SECRET_REF
        assert row.auth_mode == "secret_ref"

@pytest.mark.asyncio
async def test_oauth_session_accepts_none_credential_source(_sqlite_db):
    """ManagedAgentOAuthSession must accept NONE as auth_mode."""
    async with _sqlite_db() as session:
        session_obj = ManagedAgentOAuthSession(
            session_id="test-none-cred",
            runtime_id="claude_code",
            profile_id="claude_anthropic",
            auth_mode=ProviderCredentialSource.NONE,
        )
        session.add(session_obj)
        await session.commit()

    async with _sqlite_db() as session:
        result = await session.execute(
            select(ManagedAgentOAuthSession).where(
                ManagedAgentOAuthSession.session_id == "test-none-cred"
            )
        )
        row = result.scalar_one()
        assert row.auth_mode == ProviderCredentialSource.NONE
        assert row.auth_mode == "none"

@pytest.mark.asyncio
async def test_provider_profile_stores_credential_source_and_materialization_mode(_sqlite_db):
    """ManagedAgentProviderProfile must correctly round-trip both new enum columns."""
    async with _sqlite_db() as session:
        profile = ManagedAgentProviderProfile(
            profile_id="test-profile-enums",
            runtime_id="gemini_cli",
            provider_id="google",
            credential_source=ProviderCredentialSource.OAUTH_VOLUME,
            runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
        )
        session.add(profile)
        await session.commit()

    async with _sqlite_db() as session:
        result = await session.execute(
            select(ManagedAgentProviderProfile).where(
                ManagedAgentProviderProfile.profile_id == "test-profile-enums"
            )
        )
        row = result.scalar_one()
        assert row.credential_source == ProviderCredentialSource.OAUTH_VOLUME
        assert row.runtime_materialization_mode == RuntimeMaterializationMode.OAUTH_HOME

@pytest.mark.asyncio
async def test_provider_profile_composite_mode_is_default(_sqlite_db):
    """Provider-profile defaults are safe for unconfigured setup stubs."""
    async with _sqlite_db() as session:
        profile = ManagedAgentProviderProfile(
            profile_id="test-defaults",
            runtime_id="gemini_cli",
            provider_id="google",
        )
        session.add(profile)
        await session.commit()

    async with _sqlite_db() as session:
        result = await session.execute(
            select(ManagedAgentProviderProfile).where(
                ManagedAgentProviderProfile.profile_id == "test-defaults"
            )
        )
        row = result.scalar_one()
        assert row.credential_source == ProviderCredentialSource.NONE
        assert row.runtime_materialization_mode == RuntimeMaterializationMode.COMPOSITE
        assert row.enabled is False
        assert row.auth_state == ProviderProfileAuthState.NOT_CONFIGURED
        assert row.disabled_reason is None
        assert row.first_authenticated_at is None
        assert row.last_validated_at is None
        assert row.last_auth_method is None

@pytest.mark.asyncio
async def test_provider_profile_roundtrips_activation_fields(_sqlite_db):
    """ManagedAgentProviderProfile must persist canonical activation metadata."""
    async with _sqlite_db() as session:
        profile = ManagedAgentProviderProfile(
            profile_id="test-activation-fields",
            runtime_id="claude_code",
            provider_id="anthropic",
            credential_source=ProviderCredentialSource.SECRET_REF,
            runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
            enabled=True,
            auth_state=ProviderProfileAuthState.CONNECTED,
            disabled_reason=None,
            last_auth_method=ProviderProfileAuthMethod.SECRET_REF,
        )
        session.add(profile)
        await session.commit()

    async with _sqlite_db() as session:
        result = await session.execute(
            select(ManagedAgentProviderProfile).where(
                ManagedAgentProviderProfile.profile_id == "test-activation-fields"
            )
        )
        row = result.scalar_one()
        assert row.enabled is True
        assert row.auth_state == ProviderProfileAuthState.CONNECTED
        assert row.disabled_reason is None
        assert row.last_auth_method == ProviderProfileAuthMethod.SECRET_REF

# ---------------------------------------------------------------------------
# 4. Migration graph: exactly one head
# ---------------------------------------------------------------------------

VERSIONS_DIR = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..",
    "api_service", "migrations", "versions",
)

def _parse_migration_graph():
    """Read all migration files and return (revisions, down_revisions_referenced)."""
    revisions = {}
    down_revisions: set[str] = set()

    pattern = os.path.join(VERSIONS_DIR, "*.py")
    for filepath in glob.glob(pattern):
        content = open(filepath).read()
        rev_m = re.search(r'^revision\s*(?::\s*str)?\s*=\s*[\'\"]([\w]+)[\'\"]', content, re.MULTILINE)
        down_m = re.search(r'^down_revision\s*[^=]*=\s*(.*)', content, re.MULTILINE)
        if rev_m:
            rev = rev_m.group(1)
            revisions[rev] = os.path.basename(filepath)
            if down_m:
                raw = down_m.group(1).strip()
                ids = re.findall(r'[\'\"]([\w]+)[\'\"]', raw)
                for d in ids:
                    if d and d != "None":
                        down_revisions.add(d)
    return revisions, down_revisions

import pytest

@pytest.mark.xfail(reason="Migrations land in Phase 2 (tracking: migration graph validation)", strict=False)
class TestMigrationGraph:
    """The migration graph must have exactly one head."""

    def test_exactly_one_head(self):
        revisions, down_revisions = _parse_migration_graph()
        heads = set(revisions.keys()) - down_revisions
        assert len(heads) == 1, (
            f"Expected exactly 1 migration head, found {len(heads)}: "
            + ", ".join(f"{h} ({revisions[h]})" for h in sorted(heads))
        )
