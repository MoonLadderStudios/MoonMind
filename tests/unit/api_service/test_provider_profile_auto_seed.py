"""Unit tests for _auto_seed_provider_profiles startup function."""

import asyncio
from datetime import UTC, datetime
from enum import Enum
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ProviderProfileAuthMethod,
    ProviderCredentialSource,
    ProviderProfileAuthState,
    ProviderProfileDisabledReason,
    RuntimeMaterializationMode,
)

FIRST_PARTY_SETUP_PROFILE_IDS = {
    "codex_openai_oauth",
    "claude_anthropic_oauth",
}

FIRST_PARTY_API_PROFILE_IDS = {
    "codex_openai_api",
    "claude_anthropic_api",
}

OPENCODE_PROFILE_IDS = {"opencode-zen-free"}
BASE_PROFILE_IDS = FIRST_PARTY_SETUP_PROFILE_IDS | OPENCODE_PROFILE_IDS

LEGACY_SETUP_PROFILE_SPECS = {
    "claude_anthropic_default": (
        "claude_code",
        "anthropic",
        "Claude Code (setup required)",
    ),
    "claude_anthropic": (
        "claude_code",
        "anthropic",
        "Claude Code (setup required)",
    ),
    "codex_openai_default": ("codex_cli", "openai", "Codex CLI (setup required)"),
    "codex_default": ("codex_cli", "openai", "Codex CLI (setup required)"),
    "gemini_google_default": (
        "gemini_cli",
        "google",
        "Gemini CLI (setup required)",
    ),
    "gemini_default": ("gemini_cli", "google", "Gemini CLI (setup required)"),
}


def test_legacy_setup_profile_detection_accepts_enum_values() -> None:
    """Database rows may expose enum objects instead of their stored strings."""
    from api_service.main import _is_untouched_legacy_setup_profile

    class DatabaseValue(Enum):
        NONE = "none"
        API_KEY_ENV = "api_key_env"
        NOT_CONFIGURED = "not_configured"
        MISSING_CREDENTIALS = "missing_credentials"

    assert _is_untouched_legacy_setup_profile(
        "claude_anthropic",
        {
            "runtime_id": "claude_code",
            "provider_id": "anthropic",
            "provider_label": "Anthropic",
            "account_label": "Claude Code (setup required)",
            "default_model": None,
            "default_effort": None,
            "model_overrides": None,
            "enabled": False,
            "is_default": False,
            "credential_source": DatabaseValue.NONE,
            "runtime_materialization_mode": DatabaseValue.API_KEY_ENV,
            "auth_state": DatabaseValue.NOT_CONFIGURED,
            "disabled_reason": DatabaseValue.MISSING_CREDENTIALS,
            "secret_refs": None,
            "tags": None,
            "priority": 100,
            "clear_env_keys": None,
            "env_template": None,
            "file_templates": None,
            "home_path_overrides": None,
            "command_behavior": None,
            "max_parallel_runs": 1,
            "cooldown_after_429_seconds": 900,
            "rate_limit_policy": "backoff",
            "max_lease_duration_seconds": 7200,
            "volume_ref": None,
            "volume_mount_path": None,
            "last_auth_method": None,
        },
    )


@pytest.fixture()
def _module_db(tmp_path):
    """Create a single in-memory SQLite engine and schema for the test."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/seed_test.db"

    async def _setup():
        engine = create_async_engine(db_url, future=True)
        session_maker = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, session_maker

    async def _teardown(engine):
        await engine.dispose()

    engine, session_maker = asyncio.run(_setup())

    _orig = (db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker)
    db_base.DATABASE_URL = db_url
    db_base.engine = engine
    db_base.async_session_maker = session_maker
    yield
    db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker = _orig
    asyncio.run(_teardown(engine))


@pytest.fixture(autouse=True)
def _clear_seed_env(monkeypatch):
    for env_name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MINIMAX_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENCODE_API_KEY",
        "MOONMIND_SKIP_PROVIDER_PROFILE_SEED",
    ):
        monkeypatch.delenv(env_name, raising=False)


@pytest.mark.asyncio
async def test_auto_seed_creates_default_profiles(_module_db, monkeypatch):
    """When the table is empty, auto-seeding should create disabled OAuth profiles."""
    from api_service.main import _auto_seed_provider_profiles

    seeded = await _auto_seed_provider_profiles()
    assert set(seeded) == {"codex_cli", "claude_code", "opencode"}

    # Verify they exist in the DB with correct profile_id values.
    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    assert len(profiles) == len(BASE_PROFILE_IDS)
    profile_ids = {p.profile_id for p in profiles}
    assert profile_ids == BASE_PROFILE_IDS
    # OAuth profiles are seeded with default_model=None so they inherit the
    # runtime default rather than storing a duplicate value.
    defaults = {p.profile_id: p.default_model for p in profiles}
    assert all(
        defaults[profile_id] is None for profile_id in FIRST_PARTY_SETUP_PROFILE_IDS
    )
    runtime_defaults = {p.profile_id: p.is_default for p in profiles}
    assert all(
        runtime_defaults[profile_id] is False
        for profile_id in FIRST_PARTY_SETUP_PROFILE_IDS
    )
    provider_ids = {p.profile_id: p.provider_id for p in profiles}
    assert provider_ids["codex_openai_oauth"] == "openai"
    assert provider_ids["claude_anthropic_oauth"] == "anthropic"
    assert provider_ids["opencode-zen-free"] == "opencode"
    provider_labels = {p.profile_id: p.provider_label for p in profiles}
    assert provider_labels["codex_openai_oauth"] == "OpenAI"
    assert provider_labels["claude_anthropic_oauth"] == "Anthropic"
    claude_profile = next(
        p for p in profiles if p.profile_id == "claude_anthropic_oauth"
    )
    assert claude_profile.enabled is False
    assert claude_profile.auth_state == ProviderProfileAuthState.OAUTH_PENDING
    assert (
        claude_profile.disabled_reason
        == ProviderProfileDisabledReason.MISSING_CREDENTIALS
    )
    assert claude_profile.credential_source == ProviderCredentialSource.NONE
    assert (
        claude_profile.runtime_materialization_mode
        == RuntimeMaterializationMode.API_KEY_ENV
    )
    assert claude_profile.volume_ref is None
    assert claude_profile.volume_mount_path is None
    assert claude_profile.clear_env_keys is None

    zen_profile = next(p for p in profiles if p.profile_id == "opencode-zen-free")
    assert zen_profile.default_model == "opencode/muse-spark-1.3-contributor-free"
    assert zen_profile.default_effort == "xhigh"
    assert zen_profile.model_tiers == [
        {
            "label": "Muse Spark 1.3 Contributor Free",
            "model": "opencode/muse-spark-1.3-contributor-free",
            "effort": "xhigh",
            "parameters": {},
            "annotations": {},
        }
    ]
    assert zen_profile.enabled is True
    assert zen_profile.credential_source == ProviderCredentialSource.NONE
    assert zen_profile.auth_state == ProviderProfileAuthState.CONNECTED
    assert zen_profile.secret_refs == {}
    assert zen_profile.is_default is True
    assert zen_profile.command_behavior["auth_readiness"] == {
        "connected": True,
        "backing_secret_exists": False,
        "launch_ready": True,
    }


@pytest.mark.asyncio
async def test_auto_seed_never_attaches_the_deployment_key_to_zen(
    _module_db, monkeypatch
):
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.setenv("OPENCODE_API_KEY", "test-opencode-key")

    await _auto_seed_provider_profiles()

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "opencode-zen-free")

    assert profile is not None
    assert profile.enabled is True
    assert profile.auth_state == ProviderProfileAuthState.CONNECTED
    assert profile.disabled_reason is None
    assert profile.default_effort == "xhigh"
    assert profile.model_tiers[0]["effort"] == "xhigh"
    assert profile.model_catalog_evidence_json is None
    readiness = profile.command_behavior["auth_readiness"]
    assert profile.credential_source == ProviderCredentialSource.NONE
    assert profile.secret_refs == {}
    assert readiness["connected"] is True
    assert readiness["backing_secret_exists"] is False
    assert readiness["launch_ready"] is True


async def _enroll_opencode_go_with_pinned_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    validation_error: Exception | None = None,
) -> list[tuple[str, dict]]:
    """Exercise the real bootstrap profile transaction with hermetic edges."""

    from moonmind.omnigent.bootstrap.controller import BootstrapController
    from moonmind.omnigent.harness_platform import host_classes
    from moonmind.omnigent import opencode_runtime_validation, production
    from moonmind.provider_profiles import maintenance
    from moonmind.workflows.temporal import client as temporal_client

    image_ref = "ghcr.io/example/opencode@sha256:" + "a" * 64
    qualified_model = "opencode-go/muse-spark-1.3-contributor"
    manager_sync_signals: list[tuple[str, dict]] = []

    class Guard:
        lease = SimpleNamespace(lease_id="lease-opencode-go-default")

        async def release(self) -> None:
            return None

    async def acquire_guard(**_kwargs):
        return Guard()

    class RuntimeValidation:
        def __init__(self, **_kwargs) -> None:
            pass

        async def validate(self, **_kwargs):
            if validation_error is not None:
                raise validation_error
            return {
                "credentialGeneration": 1,
                "imageRef": image_ref,
                "materializerRef": "opencode-auth-json@1",
                "runtimeVersions": {"opencode": "1.18.11"},
                "validatedAt": datetime.now(UTC).isoformat(),
                "models": [{"qualifiedId": qualified_model}],
            }

    class TemporalHandle:
        async def signal(self, signal_name: str, payload: dict) -> None:
            manager_sync_signals.append((signal_name, payload))

    class TemporalClient:
        async def start_workflow(self, *_args, **_kwargs) -> None:
            return None

        def get_workflow_handle(self, _workflow_id: str) -> TemporalHandle:
            return TemporalHandle()

    class TemporalAdapter:
        async def get_client(self) -> TemporalClient:
            return TemporalClient()

    monkeypatch.setattr(
        maintenance, "acquire_credential_maintenance_guard", acquire_guard
    )
    monkeypatch.setattr(host_classes, "get_opencode_host_image_ref", lambda: image_ref)
    monkeypatch.setattr(
        opencode_runtime_validation,
        "OpenCodeProviderRuntimeValidationService",
        RuntimeValidation,
    )
    monkeypatch.setattr(production, "build_omnigent_secret_resolver", object)
    monkeypatch.setattr(temporal_client, "TemporalClientAdapter", TemporalAdapter)

    await BootstrapController(
        session_factory=db_base.async_session_maker
    )._ensure_provider_profile(
        api_key="sk-opencode-test-key",
        qualified_model=qualified_model,
        effort="xhigh",
        resolved=SimpleNamespace(opencode_host_image_ref=image_ref),
    )
    return manager_sync_signals


@pytest.mark.asyncio
async def test_opencode_go_enrollment_preserves_the_seeded_zen_default(
    _module_db, monkeypatch
):
    """MoonLadderStudios/MoonMind#3877: configuration is not a default selection.

    A deployment ``OPENCODE_API_KEY`` enrolls a launch-ready Go profile, but it
    is not an explicit default selection, so runtime-default authority stays on
    the credentialless Zen seed until that seed is explicitly disabled.
    """

    from api_service.main import _auto_seed_provider_profiles

    await _auto_seed_provider_profiles()
    manager_sync_signals = await _enroll_opencode_go_with_pinned_runtime(monkeypatch)

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedAgentProviderProfile).where(
                ManagedAgentProviderProfile.runtime_id == "opencode"
            )
        )
        profiles = {profile.profile_id: profile for profile in result.scalars()}

    assert set(profiles) == {"opencode-zen-free", "opencode-go-default"}
    assert profiles["opencode-zen-free"].enabled is True
    assert profiles["opencode-zen-free"].is_default is True
    assert profiles["opencode-go-default"].enabled is True
    assert profiles["opencode-go-default"].is_default is False
    assert profiles["opencode-go-default"].default_model == (
        "opencode-go/muse-spark-1.3-contributor"
    )
    assert profiles["opencode-go-default"].model_tiers == [
        {
            "label": "Muse Spark 1.3 Contributor",
            "model": "opencode-go/muse-spark-1.3-contributor",
            "effort": "xhigh",
            "parameters": {},
            "annotations": {},
        }
    ]
    assert sum(profile.is_default for profile in profiles.values()) == 1

    assert len(manager_sync_signals) == 1
    signal_name, signal_payload = manager_sync_signals[0]
    assert signal_name == "sync_profiles"
    manager_profiles = signal_payload["profiles"]
    assert [profile["profile_id"] for profile in manager_profiles] == [
        "opencode-zen-free",
        "opencode-go-default",
    ]
    assert manager_profiles[0]["is_default"] is True
    assert manager_profiles[0]["launch_ready"] is True
    assert manager_profiles[1]["is_default"] is False
    assert manager_profiles[1]["launch_ready"] is True


@pytest.mark.asyncio
async def test_operator_disabled_zen_hands_the_runtime_default_to_opencode_go(
    _module_db, monkeypatch
):
    """An explicit operator disable is the documented way to release the default."""

    from api_service.main import _auto_seed_provider_profiles

    await _auto_seed_provider_profiles()
    async with db_base.async_session_maker() as session:
        zen = await session.get(ManagedAgentProviderProfile, "opencode-zen-free")
        assert zen is not None
        zen.enabled = False
        zen.disabled_reason = ProviderProfileDisabledReason.USER_DISABLED
        await session.commit()

    await _enroll_opencode_go_with_pinned_runtime(monkeypatch)

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedAgentProviderProfile).where(
                ManagedAgentProviderProfile.runtime_id == "opencode"
            )
        )
        profiles = {profile.profile_id: profile for profile in result.scalars()}

    assert profiles["opencode-zen-free"].is_default is False
    assert profiles["opencode-go-default"].enabled is True
    assert profiles["opencode-go-default"].is_default is True
    assert sum(profile.is_default for profile in profiles.values()) == 1


@pytest.mark.asyncio
async def test_auto_seed_upgrades_bootstrap_owned_opencode_go_model_tier(
    _module_db, monkeypatch
):
    from api_service.main import _auto_seed_provider_profiles

    await _auto_seed_provider_profiles()
    await _enroll_opencode_go_with_pinned_runtime(monkeypatch)

    async with db_base.async_session_maker() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "opencode-go-default"
        )
        assert profile is not None
        profile.default_model = "opencode-go/muse-spark-1.2-contributor"
        profile.model_tiers = [
            {
                "label": "Runtime default",
                "model": None,
                "effort": "xhigh",
                "parameters": {},
                "annotations": {},
            }
        ]
        await session.commit()

    assert await _auto_seed_provider_profiles() == []

    async with db_base.async_session_maker() as session:
        migrated = await session.get(
            ManagedAgentProviderProfile, "opencode-go-default"
        )
    assert migrated is not None
    assert migrated.default_model == "opencode-go/muse-spark-1.3-contributor"
    assert migrated.model_tiers == [
        {
            "label": "Muse Spark 1.3 Contributor",
            "model": "opencode-go/muse-spark-1.3-contributor",
            "effort": "xhigh",
            "parameters": {},
            "annotations": {},
        }
    ]


@pytest.mark.asyncio
async def test_auto_seed_preserves_operator_authored_opencode_go_tiers(
    _module_db, monkeypatch
):
    from api_service.main import _auto_seed_provider_profiles

    await _auto_seed_provider_profiles()
    await _enroll_opencode_go_with_pinned_runtime(monkeypatch)
    custom_tiers = [
        {
            "label": "Custom",
            "model": "opencode-go/operator-model",
            "effort": "high",
            "parameters": {},
            "annotations": {},
        }
    ]

    async with db_base.async_session_maker() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "opencode-go-default"
        )
        assert profile is not None
        profile.default_model = "opencode-go/muse-spark-1.2-contributor"
        profile.model_tiers = custom_tiers
        await session.commit()

    assert await _auto_seed_provider_profiles() == []

    async with db_base.async_session_maker() as session:
        preserved = await session.get(
            ManagedAgentProviderProfile, "opencode-go-default"
        )
    assert preserved is not None
    assert preserved.default_model == "opencode-go/muse-spark-1.2-contributor"
    assert preserved.model_tiers == custom_tiers


@pytest.mark.asyncio
async def test_failed_opencode_go_validation_preserves_seeded_zen_default(
    _module_db, monkeypatch
):
    """A rejected candidate never receives runtime-default authority."""

    from api_service.main import _auto_seed_provider_profiles

    await _auto_seed_provider_profiles()
    with pytest.raises(ValueError, match="credential rejected"):
        await _enroll_opencode_go_with_pinned_runtime(
            monkeypatch,
            validation_error=ValueError("credential rejected"),
        )

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedAgentProviderProfile).where(
                ManagedAgentProviderProfile.runtime_id == "opencode"
            )
        )
        profiles = {profile.profile_id: profile for profile in result.scalars()}

    assert profiles["opencode-zen-free"].enabled is True
    assert profiles["opencode-zen-free"].is_default is True
    assert profiles["opencode-go-default"].enabled is False
    assert profiles["opencode-go-default"].is_default is False


@pytest.mark.asyncio
async def test_auto_seed_preserves_operator_disabled_zen_profile(
    _module_db, monkeypatch
):
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.setenv("OPENCODE_API_KEY", "test-opencode-key")
    await _auto_seed_provider_profiles()

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "opencode-zen-free")
        assert profile is not None
        profile.enabled = False
        profile.auth_state = ProviderProfileAuthState.CONNECTED
        profile.disabled_reason = ProviderProfileDisabledReason.USER_DISABLED
        profile.command_behavior = {
            **profile.command_behavior,
            "auth_state": "connected",
            "auth_readiness": {
                "connected": True,
                "backing_secret_exists": False,
                "launch_ready": False,
            },
        }
        await session.commit()

    assert await _auto_seed_provider_profiles() == []

    async with db_base.async_session_maker() as session:
        preserved = await session.get(ManagedAgentProviderProfile, "opencode-zen-free")

    assert preserved is not None
    assert preserved.enabled is False
    assert preserved.auth_state == ProviderProfileAuthState.CONNECTED
    assert preserved.disabled_reason == ProviderProfileDisabledReason.USER_DISABLED
    assert preserved.command_behavior["auth_readiness"]["launch_ready"] is False


@pytest.mark.asyncio
async def test_auto_seed_migrates_the_zen_profile_to_the_exact_runtime_model(
    _module_db, monkeypatch
):
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.setenv("OPENCODE_API_KEY", "test-opencode-key")
    await _auto_seed_provider_profiles()

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "opencode-zen-free")
        assert profile is not None
        profile.provider_id = "opencode-zen"
        profile.default_model = "opencode-zen/muse-spark-1.2-free"
        profile.enabled = False
        profile.is_default = False
        profile.auth_state = ProviderProfileAuthState.NOT_CONFIGURED
        profile.disabled_reason = ProviderProfileDisabledReason.MISSING_CREDENTIALS
        profile.model_tiers = [
            {
                "label": "Muse Spark 1.2 Free",
                "model": "opencode-zen/muse-spark-1.2-free",
                "effort": "xhigh",
                "parameters": {},
                "annotations": {},
            }
        ]
        await session.commit()

    assert await _auto_seed_provider_profiles() == []

    async with db_base.async_session_maker() as session:
        migrated = await session.get(ManagedAgentProviderProfile, "opencode-zen-free")
    assert migrated is not None
    assert migrated.provider_id == "opencode"
    assert migrated.enabled is True
    assert migrated.is_default is True
    assert migrated.auth_state == ProviderProfileAuthState.CONNECTED
    assert migrated.disabled_reason is None
    assert migrated.default_model == "opencode/muse-spark-1.3-contributor-free"
    assert migrated.model_tiers[0]["model"] == (
        "opencode/muse-spark-1.3-contributor-free"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("enum_values", [False, True])
async def test_auto_seed_persists_legacy_codex_oauth_capacity_repair(
    _module_db, enum_values
):
    """Startup repairs both enum-shaped and string-shaped legacy ORM rows."""
    from api_service.main import _auto_seed_provider_profiles

    credential_source = (
        ProviderCredentialSource.OAUTH_VOLUME if enum_values else "oauth_volume"
    )
    materialization_mode = (
        RuntimeMaterializationMode.OAUTH_HOME if enum_values else "oauth_home"
    )
    async with db_base.async_session_maker() as session:
        await session.execute(text("PRAGMA ignore_check_constraints = ON"))
        session.add(
            ManagedAgentProviderProfile(
                profile_id=f"legacy-codex-{enum_values}",
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=credential_source,
                runtime_materialization_mode=materialization_mode,
                max_parallel_runs=3,
                enabled=False,
                auth_state=ProviderProfileAuthState.DISCONNECTED,
            )
        )
        session.add_all(
            [
                ManagedAgentProviderProfile(
                    profile_id=f"codex-api-control-{enum_values}",
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    max_parallel_runs=4,
                    enabled=False,
                    auth_state=ProviderProfileAuthState.DISCONNECTED,
                ),
                ManagedAgentProviderProfile(
                    profile_id=f"claude-oauth-control-{enum_values}",
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    max_parallel_runs=3,
                    enabled=False,
                    auth_state=ProviderProfileAuthState.DISCONNECTED,
                ),
            ]
        )
        await session.commit()
        await session.execute(text("PRAGMA ignore_check_constraints = OFF"))

    await _auto_seed_provider_profiles()

    async with db_base.async_session_maker() as session:
        repaired = await session.get(
            ManagedAgentProviderProfile, f"legacy-codex-{enum_values}"
        )
        assert repaired is not None
        assert repaired.max_parallel_runs == 1
        codex_api = await session.get(
            ManagedAgentProviderProfile, f"codex-api-control-{enum_values}"
        )
        claude_oauth = await session.get(
            ManagedAgentProviderProfile, f"claude-oauth-control-{enum_values}"
        )
        assert codex_api is not None
        assert codex_api.max_parallel_runs == 4
        assert claude_oauth is not None
        assert claude_oauth.max_parallel_runs == 3

        from api_service.services.provider_profile_service import (
            _manager_profile_payload,
        )

        assert _manager_profile_payload(repaired)["max_parallel_runs"] == 1


@pytest.mark.asyncio
async def test_auto_seed_includes_first_party_api_profiles_when_env_set(
    _module_db, monkeypatch
):
    """OpenAI and Anthropic env keys create enabled API-backed profiles."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    seeded = await _auto_seed_provider_profiles()
    assert seeded.count("codex_cli") == 2
    assert seeded.count("claude_code") == 2

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = {p.profile_id: p for p in result.scalars().all()}

    assert set(profiles) == BASE_PROFILE_IDS | FIRST_PARTY_API_PROFILE_IDS

    codex_api = profiles["codex_openai_api"]
    assert codex_api.runtime_id == "codex_cli"
    assert codex_api.provider_id == "openai"
    assert codex_api.account_label == "Codex OpenAI API"
    assert codex_api.enabled is True
    assert codex_api.auth_state == ProviderProfileAuthState.CONNECTED
    assert codex_api.disabled_reason is None
    assert codex_api.credential_source == ProviderCredentialSource.SECRET_REF
    assert codex_api.last_auth_method == ProviderProfileAuthMethod.SECRET_REF
    assert codex_api.secret_refs == {"openai_api_key": "env://OPENAI_API_KEY"}
    assert codex_api.env_template == {
        "OPENAI_API_KEY": {"from_secret_ref": "openai_api_key"}
    }
    assert codex_api.clear_env_keys == [
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT",
        "MINIMAX_API_KEY",
    ]

    claude_api = profiles["claude_anthropic_api"]
    assert claude_api.runtime_id == "claude_code"
    assert claude_api.provider_id == "anthropic"
    assert claude_api.account_label == "Claude Anthropic API"
    assert claude_api.enabled is True
    assert claude_api.auth_state == ProviderProfileAuthState.CONNECTED
    assert claude_api.disabled_reason is None
    assert claude_api.credential_source == ProviderCredentialSource.SECRET_REF
    assert claude_api.last_auth_method == ProviderProfileAuthMethod.SECRET_REF
    assert claude_api.secret_refs == {"anthropic_api_key": "env://ANTHROPIC_API_KEY"}
    assert claude_api.env_template == {
        "ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"}
    }
    assert claude_api.clear_env_keys == [
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_API_KEY",
        "OPENAI_API_KEY",
    ]

    for profile_id in FIRST_PARTY_SETUP_PROFILE_IDS:
        assert profiles[profile_id].enabled is False


@pytest.mark.asyncio
async def test_auto_seed_is_idempotent(_module_db, monkeypatch):
    """Calling auto-seed twice should not duplicate profiles."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    first = await _auto_seed_provider_profiles()
    assert len(first) == len(BASE_PROFILE_IDS)

    second = await _auto_seed_provider_profiles()
    assert second == []

    # The base provider profiles remain unique.
    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()
    assert len(profiles) == len(BASE_PROFILE_IDS)


@pytest.mark.asyncio
async def test_auto_seed_skipped_when_env_set(_module_db, monkeypatch):
    """Seeding should be skipped when MOONMIND_SKIP_PROVIDER_PROFILE_SEED is set."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.setenv("MOONMIND_SKIP_PROVIDER_PROFILE_SEED", "true")
    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()
    assert len(profiles) == 0


@pytest.mark.asyncio
async def test_auto_seed_includes_minimax_when_env_set(_module_db, monkeypatch):
    """When MINIMAX_API_KEY is set, MiniMax Claude and Codex profiles are seeded."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    seeded = await _auto_seed_provider_profiles()
    assert "claude_code" in seeded
    assert "codex_cli" in seeded

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    assert len(profiles) == len(BASE_PROFILE_IDS) + 2
    profile_ids = {p.profile_id for p in profiles}
    assert "claude_anthropic_oauth" in profile_ids
    assert "claude_minimax" in profile_ids
    assert "codex_minimax_m27" in profile_ids

    # Verify MiniMax profile details.
    mm_profile = next(p for p in profiles if p.profile_id == "claude_minimax")
    assert mm_profile.runtime_id == "claude_code"
    assert mm_profile.secret_refs is not None
    assert mm_profile.secret_refs.get("provider_api_key") == "env://MINIMAX_API_KEY"
    assert mm_profile.env_template is not None
    assert (
        mm_profile.env_template["ANTHROPIC_BASE_URL"]
        == "https://api.minimax.io/anthropic"
    )
    assert mm_profile.env_template["ANTHROPIC_AUTH_TOKEN"] == {
        "from_secret_ref": "provider_api_key"
    }
    assert mm_profile.env_template["ANTHROPIC_MODEL"] == "MiniMax-M2.7"
    assert mm_profile.env_template["API_TIMEOUT_MS"] == "3000000"
    assert mm_profile.clear_env_keys == [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "MINIMAX_API_KEY",
    ]
    assert mm_profile.default_model == "MiniMax-M2.7"
    assert mm_profile.volume_ref is None
    assert mm_profile.volume_mount_path is None
    assert mm_profile.is_default is True
    assert mm_profile.enabled is True
    assert mm_profile.auth_state == ProviderProfileAuthState.CONNECTED
    assert mm_profile.disabled_reason is None

    anthropic_profile = next(
        p for p in profiles if p.profile_id == "claude_anthropic_oauth"
    )
    assert anthropic_profile.is_default is False

    codex_mm_profile = next(p for p in profiles if p.profile_id == "codex_minimax_m27")
    assert codex_mm_profile.runtime_id == "codex_cli"
    assert (
        codex_mm_profile.runtime_materialization_mode
        == RuntimeMaterializationMode.COMPOSITE
    )
    assert codex_mm_profile.secret_refs == {"provider_api_key": "env://MINIMAX_API_KEY"}
    assert codex_mm_profile.env_template == {
        "MINIMAX_API_KEY": {"from_secret_ref": "provider_api_key"}
    }
    assert codex_mm_profile.clear_env_keys == [
        "MINIMAX_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    ]
    assert codex_mm_profile.file_templates[0]["merge_strategy"] == "deep_merge"
    assert codex_mm_profile.file_templates[0]["permissions"] == "0600"
    assert codex_mm_profile.file_templates[0]["content_template"]["profile"] == "m27"
    assert codex_mm_profile.model_overrides == {"codex_profile_name": "m27"}


@pytest.mark.asyncio
async def test_auto_seed_adds_minimax_after_initial_seed(_module_db, monkeypatch):
    """MINIMAX_API_KEY added after initial seed → claude_minimax is inserted on next call."""
    from api_service.main import _auto_seed_provider_profiles

    # First seed without MiniMax key.
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    first = await _auto_seed_provider_profiles()
    assert len(first) == len(BASE_PROFILE_IDS)

    # Now the key becomes available.
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    second = await _auto_seed_provider_profiles()
    assert "claude_code" in second  # minimax profile was added

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    assert len(profiles) == len(BASE_PROFILE_IDS) + 2
    profile_ids = {p.profile_id for p in profiles}
    assert "claude_anthropic_oauth" in profile_ids
    assert "claude_minimax" in profile_ids
    assert "codex_minimax_m27" in profile_ids


@pytest.mark.asyncio
async def test_auto_seed_preserves_user_default_model_on_oauth_profile(
    _module_db, monkeypatch
):
    """The reconciliation loop must not clear user-set default_model values."""
    from api_service.main import _auto_seed_provider_profiles

    await _auto_seed_provider_profiles()

    # Simulate a user setting an explicit model on the seeded profile.
    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "codex_openai_oauth")
        assert profile is not None
        profile.default_model = "gpt-user-custom"
        await session.commit()

    # Run auto-seed again — it must not overwrite the user-set value.
    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "codex_openai_oauth")
        assert profile is not None
        assert profile.default_model == "gpt-user-custom"


@pytest.mark.asyncio
async def test_auto_seed_repairs_legacy_codex_oauth_capacity_to_one(
    _module_db, monkeypatch
):
    """Startup reconciliation repairs unsafe pre-invariant OAuth rows."""
    from api_service.main import _auto_seed_provider_profiles

    await _auto_seed_provider_profiles()

    async with db_base.async_session_maker() as session:
        await session.execute(text("PRAGMA ignore_check_constraints = ON"))
        profile = await session.get(ManagedAgentProviderProfile, "codex_openai_oauth")
        assert profile is not None
        profile.credential_source = ProviderCredentialSource.OAUTH_VOLUME
        profile.runtime_materialization_mode = RuntimeMaterializationMode.OAUTH_HOME
        profile.volume_ref = "codex_auth_volume"
        profile.volume_mount_path = "/home/app/.codex"
        profile.max_parallel_runs = 7
        await session.commit()
        await session.execute(text("PRAGMA ignore_check_constraints = OFF"))

    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "codex_openai_oauth")
        assert profile is not None
        assert profile.max_parallel_runs == 1


@pytest.mark.asyncio
async def test_auto_seed_deletes_untouched_legacy_setup_profiles(
    _module_db, monkeypatch
):
    """All generated legacy setup stubs are removed during startup seeding."""
    from api_service.main import _auto_seed_provider_profiles

    async with db_base.async_session_maker() as session:
        for profile_id, (
            runtime_id,
            provider_id,
            account_label,
        ) in LEGACY_SETUP_PROFILE_SPECS.items():
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id=runtime_id,
                    provider_id=provider_id,
                    provider_label={
                        "anthropic": "Anthropic",
                        "openai": "OpenAI",
                        "google": "Google",
                    }[provider_id],
                    account_label=account_label,
                    credential_source=ProviderCredentialSource.NONE,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    enabled=False,
                    is_default=False,
                    auth_state=ProviderProfileAuthState.NOT_CONFIGURED,
                    disabled_reason=ProviderProfileDisabledReason.MISSING_CREDENTIALS,
                )
            )
        await session.commit()

    seeded = await _auto_seed_provider_profiles()
    assert set(seeded) == {"codex_cli", "claude_code", "opencode"}

    async with db_base.async_session_maker() as session:
        rows = (
            (await session.execute(select(ManagedAgentProviderProfile.profile_id)))
            .scalars()
            .all()
        )

    assert set(rows) == BASE_PROFILE_IDS


@pytest.mark.asyncio
@pytest.mark.parametrize("edit_field", ["secret_refs", "volume_ref", "default_model"])
async def test_auto_seed_preserves_edited_legacy_setup_profile(_module_db, edit_field):
    """A legacy ID with credential state is user data, not a generated stub."""
    from api_service.main import _auto_seed_provider_profiles

    values = {
        "profile_id": "claude_anthropic",
        "runtime_id": "claude_code",
        "provider_id": "anthropic",
        "account_label": "Claude Code (setup required)",
        "credential_source": ProviderCredentialSource.NONE,
        "runtime_materialization_mode": RuntimeMaterializationMode.API_KEY_ENV,
        "enabled": False,
        "is_default": False,
        "auth_state": ProviderProfileAuthState.NOT_CONFIGURED,
        "disabled_reason": ProviderProfileDisabledReason.MISSING_CREDENTIALS,
        edit_field: (
            {"anthropic_api_key": "secret://configured"}
            if edit_field == "secret_refs"
            else (
                "claude_oauth_volume"
                if edit_field == "volume_ref"
                else "operator-selected-model"
            )
        ),
    }
    async with db_base.async_session_maker() as session:
        session.add(ManagedAgentProviderProfile(**values))
        await session.commit()

    await _auto_seed_provider_profiles()

    async with db_base.async_session_maker() as session:
        preserved = await session.get(ManagedAgentProviderProfile, "claude_anthropic")
    assert preserved is not None


@pytest.mark.asyncio
async def test_auto_seed_excludes_minimax_when_env_unset(_module_db, monkeypatch):
    """When MINIMAX_API_KEY is absent, only first-party setup stubs are seeded."""
    from api_service.main import _auto_seed_provider_profiles

    seeded = await _auto_seed_provider_profiles()
    assert set(seeded) == {"codex_cli", "claude_code", "opencode"}

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    profile_ids = {p.profile_id for p in profiles}
    assert "claude_minimax" not in profile_ids
    assert "codex_minimax_m27" not in profile_ids
    assert "claude_anthropic_oauth" in profile_ids
    assert len(profiles) == len(BASE_PROFILE_IDS)


@pytest.mark.asyncio
async def test_auto_seed_includes_openrouter_codex_profile_when_env_set(
    _module_db, monkeypatch
):
    """OPENROUTER_API_KEY should seed a composite Codex provider profile."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    seeded = await _auto_seed_provider_profiles()
    assert "codex_cli" in seeded

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    assert len(profiles) == len(BASE_PROFILE_IDS) + 1
    profile_ids = {p.profile_id for p in profiles}
    assert "codex_openrouter_qwen36_plus" in profile_ids

    profile = next(
        p for p in profiles if p.profile_id == "codex_openrouter_qwen36_plus"
    )
    assert profile.runtime_id == "codex_cli"
    assert profile.provider_id == "openrouter"
    assert profile.is_default is True
    assert profile.default_model == "qwen/qwen3.6-plus"
    assert profile.secret_refs == {"provider_api_key": "env://OPENROUTER_API_KEY"}
    assert profile.env_template == {
        "OPENROUTER_API_KEY": {"from_secret_ref": "provider_api_key"}
    }
    assert profile.file_templates == [
        {
            "path": "{{runtime_support_dir}}/codex-home/config.toml",
            "format": "toml",
            "merge_strategy": "replace",
            "content_template": {
                "model_provider": "openrouter",
                "model_reasoning_effort": "high",
                "model": "qwen/qwen3.6-plus",
                "profile": "openrouter_qwen36_plus",
                "model_providers": {
                    "openrouter": {
                        "name": "OpenRouter",
                        "base_url": "https://openrouter.ai/api/v1",
                        "env_key": "OPENROUTER_API_KEY",
                        "wire_api": "responses",
                    }
                },
                "profiles": {
                    "openrouter_qwen36_plus": {
                        "model_provider": "openrouter",
                        "model": "qwen/qwen3.6-plus",
                    }
                },
            },
            "permissions": "0600",
        }
    ]
    assert profile.home_path_overrides == {
        "CODEX_HOME": "{{runtime_support_dir}}/codex-home"
    }
    assert profile.command_behavior == {"suppress_default_model_flag": True}
    assert profile.max_parallel_runs == 4
    assert profile.cooldown_after_429_seconds == 300

    codex_oauth = next(p for p in profiles if p.profile_id == "codex_openai_oauth")
    assert codex_oauth.is_default is False


@pytest.mark.asyncio
async def test_auto_seed_reconciles_openrouter_codex_config_template_for_existing_profile(
    _module_db, monkeypatch
):
    from api_service.main import (
        _auto_seed_provider_profiles,
        _legacy_codex_openrouter_qwen36_plus_file_templates,
    )

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    await _auto_seed_provider_profiles()

    async with db_base.async_session_maker() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "codex_openrouter_qwen36_plus"
        )
        profile.file_templates = _legacy_codex_openrouter_qwen36_plus_file_templates()
        await session.commit()

    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "codex_openrouter_qwen36_plus"
        )

    content_template = profile.file_templates[0]["content_template"]
    assert content_template["model_provider"] == "openrouter"
    assert content_template["model_reasoning_effort"] == "high"
    assert content_template["model"] == "qwen/qwen3.6-plus"


@pytest.mark.asyncio
async def test_auto_seed_reconciles_deprecated_openrouter_codex_seed_model(
    _module_db, monkeypatch
):
    from api_service.main import (
        _LEGACY_CODEX_OPENROUTER_QWEN36_PLUS_FREE_MODEL,
        _auto_seed_provider_profiles,
        _codex_openrouter_qwen36_plus_file_templates,
    )

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    await _auto_seed_provider_profiles()

    async with db_base.async_session_maker() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "codex_openrouter_qwen36_plus"
        )
        profile.default_model = _LEGACY_CODEX_OPENROUTER_QWEN36_PLUS_FREE_MODEL
        profile.file_templates = _codex_openrouter_qwen36_plus_file_templates(
            _LEGACY_CODEX_OPENROUTER_QWEN36_PLUS_FREE_MODEL
        )
        await session.commit()

    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "codex_openrouter_qwen36_plus"
        )

    assert profile.default_model == "qwen/qwen3.6-plus"
    content_template = profile.file_templates[0]["content_template"]
    assert content_template["model"] == "qwen/qwen3.6-plus"
    assert (
        content_template["profiles"]["openrouter_qwen36_plus"]["model"]
        == "qwen/qwen3.6-plus"
    )


@pytest.mark.asyncio
async def test_auto_seed_does_not_overwrite_custom_openrouter_codex_config_template(
    _module_db, monkeypatch
):
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    await _auto_seed_provider_profiles()

    custom_template = [
        {
            "path": "{{runtime_support_dir}}/codex-home/config.toml",
            "format": "toml",
            "merge_strategy": "replace",
            "content_template": {
                "model_provider": "openrouter",
                "model_reasoning_effort": "medium",
                "model": "openrouter/custom-model",
                "profile": "openrouter_qwen36_plus",
                "model_providers": {
                    "openrouter": {
                        "name": "OpenRouter",
                        "base_url": "https://openrouter.ai/api/v1",
                        "env_key": "OPENROUTER_API_KEY",
                        "wire_api": "responses",
                    }
                },
                "profiles": {
                    "openrouter_qwen36_plus": {
                        "model_provider": "openrouter",
                        "model": "openrouter/custom-model",
                    }
                },
            },
            "permissions": "0600",
        }
    ]

    async with db_base.async_session_maker() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "codex_openrouter_qwen36_plus"
        )
        profile.file_templates = custom_template
        await session.commit()

    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "codex_openrouter_qwen36_plus"
        )

    assert profile.file_templates == custom_template


@pytest.mark.asyncio
async def test_auto_seed_first_party_stubs_have_default_readiness_labels(
    _module_db, monkeypatch
):
    """First-party setup stubs carry the documented command_behavior labels."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    await _auto_seed_provider_profiles()

    expected_command_behavior = {
        "supported_auth_methods": ["oauth_volume", "secret_ref"],
        "auth_actions": ["connect_oauth", "use_api_key"],
        "auth_status_label": "Not connected",
        "auth_readiness": {
            "connected": False,
            "launch_ready": False,
        },
    }

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = {p.profile_id: p for p in result.scalars().all()}

    for profile_id in FIRST_PARTY_SETUP_PROFILE_IDS:
        profile = profiles[profile_id]
        assert profile.auth_state == ProviderProfileAuthState.OAUTH_PENDING
        assert profile.command_behavior == expected_command_behavior, profile_id
        # Stubs stay pre-OAuth: no home_path_overrides until setup succeeds.
        assert not profile.home_path_overrides

    readiness_ids = {
        id(profiles[profile_id].command_behavior["auth_readiness"])
        for profile_id in FIRST_PARTY_SETUP_PROFILE_IDS
    }
    assert len(readiness_ids) == len(FIRST_PARTY_SETUP_PROFILE_IDS)


@pytest.mark.asyncio
async def test_auto_seed_disables_and_reenables_env_api_profiles(
    _module_db, monkeypatch
):
    """Env-backed API profiles track whether their backing env key exists."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    await _auto_seed_provider_profiles()

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        codex_api = await session.get(ManagedAgentProviderProfile, "codex_openai_api")
        claude_api = await session.get(
            ManagedAgentProviderProfile, "claude_anthropic_api"
        )

    for profile, env_key in (
        (codex_api, "OPENAI_API_KEY"),
        (claude_api, "ANTHROPIC_API_KEY"),
    ):
        assert profile is not None
        assert profile.enabled is False
        assert profile.auth_state == ProviderProfileAuthState.NOT_CONFIGURED
        assert (
            profile.disabled_reason == ProviderProfileDisabledReason.MISSING_CREDENTIALS
        )
        readiness = profile.command_behavior["auth_readiness"]
        assert readiness["launch_ready"] is False
        assert readiness["backing_secret_exists"] is False
        assert readiness["failure_reason"] == f"{env_key} is not configured."

    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    await _auto_seed_provider_profiles()

    async with db_base.async_session_maker() as session:
        codex_api = await session.get(ManagedAgentProviderProfile, "codex_openai_api")
        claude_api = await session.get(
            ManagedAgentProviderProfile, "claude_anthropic_api"
        )

    for profile in (codex_api, claude_api):
        assert profile is not None
        assert profile.enabled is True
        assert profile.auth_state == ProviderProfileAuthState.CONNECTED
        assert profile.disabled_reason is None
        assert profile.command_behavior["auth_readiness"]["launch_ready"] is True
