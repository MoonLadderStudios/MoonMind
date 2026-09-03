"""Unit/Integration tests for ManagedAgentProviderProfile CRUD API."""

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers import provider_profiles as provider_profiles_router
from api_service.api.routers.provider_profiles import ProviderProfileCreate
from api_service.auth_providers import get_current_user
from api_service.db import base as db_base
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ManagedAgentRateLimitPolicy,
    ManagedSecret,
    ProviderCredentialSource,
    ProviderProfileAuthMethod,
    ProviderProfileAuthState,
    ProviderProfileDisabledReason,
    RuntimeMaterializationMode,
    SecretStatus,
)
from api_service.main import app
from api_service.services import provider_profile_creation
from api_service.services.provider_profile_creation import (
    ExpertManualCredentialCapability,
    RuntimeProviderAuthenticationCapability,
)
from api_service.services.provider_profile_creation_presets import (
    ProviderProfileAuthenticationMethod,
    get_provider_profile_creation_preset,
)
from api_service.services.provider_profile_service import (
    _managed_secret_statuses_for_profiles,
    _manager_profile_payload,
    apply_oauth_connected_state,
    normalize_runtime_default_profile,
)
from api_service.services.provider_profile_readiness import (
    provider_profile_launch_ready,
    provider_profile_launch_ready_from_payload,
)


CODEX_OPENAI_API_KEY_PRESET_VERSION = get_provider_profile_creation_preset(
    runtime_id="codex_cli",
    provider_id="openai",
    authentication_method=ProviderProfileAuthenticationMethod.API_KEY,
).version
CODEX_OPENAI_OAUTH_PRESET_VERSION = get_provider_profile_creation_preset(
    runtime_id="codex_cli",
    provider_id="openai",
    authentication_method=ProviderProfileAuthenticationMethod.OAUTH,
).version

def test_codex_oauth_profile_rejects_parallel_capacity_above_one() -> None:
    with pytest.raises(ValueError, match="require max_parallel_runs=1"):
        ProviderProfileCreate(
            profile_id="codex-oauth-invalid-capacity",
            runtime_id="codex_cli",
            provider_id="openai",
            credential_source="oauth_volume",
            runtime_materialization_mode="oauth_home",
            volume_ref="codex_auth_volume",
            volume_mount_path="/home/app/.codex",
            max_parallel_runs=2,
        )


def test_provider_profile_create_rejects_incoherent_credential_contract() -> None:
    with pytest.raises(ValueError, match="Incoherent credential contract"):
        ProviderProfileCreate(
            profile_id="incoherent-provider-profile",
            runtime_id="codex_cli",
            provider_id="openai",
            credential_source="oauth_volume",
            runtime_materialization_mode="api_key_env",
        )


@pytest.fixture(scope="module")
def _module_db(tmp_path_factory):
    """Create a single SQLite engine and schema for the entire module."""
    import asyncio

    tmp = tmp_path_factory.mktemp("integration_db_auth")
    db_url = f"sqlite+aiosqlite:///{tmp}/shared.db"

    async def _setup():
        engine = create_async_engine(db_url, future=True)
        session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
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

@pytest.fixture
def client_app(_module_db) -> AsyncClient:
    async def _maintenance_guard_override():
        yield object()

    app.dependency_overrides[
        provider_profiles_router._credential_validation_guard
    ] = _maintenance_guard_override
    app.dependency_overrides[
        provider_profiles_router._credential_disconnect_guard
    ] = _maintenance_guard_override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture(autouse=True)
def _reset_dependency_overrides():
    """Prevent ``_override_current_user`` overrides from leaking across modules.

    These tests mutate the shared ``app.dependency_overrides`` to inject a
    current user. Without cleanup, the last override leaks into sibling test
    modules (e.g. the settings API tests) and changes their authenticated
    principal, causing spurious 403s.
    """
    yield
    app.dependency_overrides.clear()


def _override_current_user(
    *,
    user_id=None,
    is_superuser: bool = False,
    settings_permissions: set[str] | None = None,
):
    user = SimpleNamespace(
        id=user_id if user_id is not None else uuid4(),
        email="provider-profile-test@example.com",
        is_active=True,
        is_superuser=is_superuser,
        settings_permissions=(
            {"provider_profiles.read", "provider_profiles.write"}
            if settings_permissions is None
            else settings_permissions
        ),
    )
    dependencies = {
        dep.call
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/v1/provider-profiles")
        and getattr(route, "dependant", None) is not None
        for dep in route.dependant.dependencies
        if getattr(dep.call, "__name__", "") == "_current_user_fallback"
    } or {get_current_user()}
    for dependency in dependencies:
        app.dependency_overrides[dependency] = lambda user=user: user
    return user


def _advertise_expert_manual_contracts(
    monkeypatch: pytest.MonkeyPatch,
    *contracts: tuple[str, str, str, str],
) -> None:
    """Install typed runtime/provider authority for legacy CRUD test fixtures."""

    capabilities = tuple(
        RuntimeProviderAuthenticationCapability(
            runtime_id=runtime_id,
            provider_id=provider_id,
            expert_manual_credentials=(
                ExpertManualCredentialCapability(
                    authentication_method=(
                        "oauth"
                        if credential_source == "oauth_volume"
                        else "api_key"
                        if credential_source == "secret_ref"
                        else "none"
                    ),
                    label="Expert manual test contract",
                    credential_source=credential_source,
                    runtime_materialization_mode=materialization_mode,
                    launch_validator=lambda _profile: True,
                ),
            ),
        )
        for (
            runtime_id,
            provider_id,
            credential_source,
            materialization_mode,
        ) in contracts
    )
    monkeypatch.setattr(
        provider_profile_creation,
        "_RUNTIME_PROVIDER_AUTHENTICATION_CAPABILITIES",
        capabilities,
    )


@pytest.mark.asyncio
async def test_provider_profile_list_requires_read_permission(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user(settings_permissions=set())

    async with client_app as client:
        response = await client.get("/api/v1/provider-profiles")

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing required provider profile permission: provider_profiles.read."


@pytest.mark.asyncio
async def test_provider_profile_get_requires_read_permission_before_lookup(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user(settings_permissions=set())

    async with client_app as client:
        response = await client.get("/api/v1/provider-profiles/missing-profile")

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Missing required provider profile permission: provider_profiles.read."
    )


@pytest.mark.asyncio
async def test_creation_preset_exposes_only_backend_supported_authentication_methods(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user()

    async with client_app as client:
        response = await client.get(
            "/api/v1/provider-profiles/creation-capabilities",
            params={"runtime_id": "codex_cli", "provider_id": "openai"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "provider-profile-creation-v1"
    assert [method["id"] for method in payload["authentication_methods"]] == [
        "oauth",
        "api_key",
    ]
    api_key = payload["authentication_methods"][1]
    assert api_key["secret_roles"][0]["role"] == "openai_api_key"
    assert api_key["secret_roles"][0]["required"] is True


@pytest.mark.asyncio
async def test_guided_api_key_creation_uses_backend_preset_and_stays_disabled(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user()
    profile_id = "mm3820-guided-openai-api-key"

    async with client_app as client:
        response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": profile_id,
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "api_key",
                "preset_version": CODEX_OPENAI_API_KEY_PRESET_VERSION,
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["credential_source"] == "none"
    assert payload["runtime_materialization_mode"] == "api_key_env"
    assert payload["secret_refs"] == {}
    assert payload["auth_state"] == "api_key_pending"
    assert payload["enabled"] is False
    assert payload["launch_ready"] is False
    checks = {check["id"]: check for check in payload["readiness"]["checks"]}
    assert "openai_api_key" in checks["secret_refs"]["message"]


@pytest.mark.asyncio
async def test_guided_api_key_creation_activates_selected_managed_secret_ref(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user()
    profile_id = f"mm3820-guided-existing-ref-{uuid4().hex}"
    secret_slug = f"mm3820-existing-openai-{uuid4().hex}"
    async with db_base.async_session_maker() as session:
        session.add(
            ManagedSecret(
                slug=secret_slug,
                ciphertext="encrypted-test-value",
                status=SecretStatus.ACTIVE,
                details={},
            )
        )
        await session.commit()

    async with client_app as client:
        response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": profile_id,
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "api_key",
                "preset_version": CODEX_OPENAI_API_KEY_PRESET_VERSION,
                "secret_refs": {"openai_api_key": f"db://{secret_slug}"},
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["secret_refs"] == {"openai_api_key": f"db://{secret_slug}"}
    assert payload["credential_source"] == "secret_ref"
    assert payload["auth_state"] == "connected"
    assert payload["enabled"] is True
    assert payload["launch_ready"] is True


@pytest.mark.asyncio
async def test_guided_oauth_creation_persists_typed_pending_setup_state(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user()

    async with client_app as client:
        response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": "mm3820-guided-openai-oauth-pending",
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "oauth",
                "preset_version": CODEX_OPENAI_OAUTH_PRESET_VERSION,
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["authentication_method"] == "oauth"
    assert payload["credential_source"] == "none"
    assert payload["runtime_materialization_mode"] == "oauth_home"
    assert payload["auth_state"] == "oauth_pending"
    assert payload["enabled"] is False
    assert payload["launch_ready"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("credential_source", "materialization_mode"),
    [
        ("none", "api_key_env"),
        ("secret_ref", "config_bundle"),
        ("secret_ref", "api_key_env"),
    ],
)
async def test_known_provider_rejects_unadvertised_manual_credential_contracts(
    client_app: AsyncClient,
    _module_db,
    credential_source: str,
    materialization_mode: str,
) -> None:
    _override_current_user()
    profile_id = f"mm3820-reject-manual-{credential_source}-{materialization_mode}"

    async with client_app as client:
        response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": profile_id,
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "credential_source": credential_source,
                "runtime_materialization_mode": materialization_mode,
                "secret_refs": {"openai_api_key": "env://OPENAI_API_KEY"},
                "enabled": True,
                "auth_state": "connected",
                "disabled_reason": None,
            },
        )
        persisted = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 422
    assert "credential" in response.json()["detail"].lower()
    assert persisted.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("credential_source", "materialization_mode"),
    [
        ("none", "api_key_env"),
        ("secret_ref", "api_key_env"),
    ],
)
async def test_known_provider_rejects_unadvertised_manual_contract_updates(
    client_app: AsyncClient,
    _module_db,
    credential_source: str,
    materialization_mode: str,
) -> None:
    _override_current_user()
    profile_id = f"mm3820-reject-manual-update-{credential_source}"
    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=ProviderCredentialSource.SECRET_REF,
                runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                secret_refs={"openai_api_key": "env://OPENAI_API_KEY"},
                enabled=False,
                auth_state=ProviderProfileAuthState.CONNECTED,
                disabled_reason=ProviderProfileDisabledReason.USER_DISABLED,
            )
        )
        await session.commit()

    async with client_app as client:
        response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={
                "credential_source": credential_source,
                "runtime_materialization_mode": materialization_mode,
            },
        )

    assert response.status_code == 422
    assert "credential" in response.json()["detail"].lower()
    async with db_base.async_session_maker() as session:
        persisted = await session.get(ManagedAgentProviderProfile, profile_id)
        assert persisted is not None
        assert persisted.credential_source is ProviderCredentialSource.SECRET_REF
        assert (
            persisted.runtime_materialization_mode
            is RuntimeMaterializationMode.API_KEY_ENV
        )


@pytest.mark.asyncio
async def test_known_provider_stale_contract_cannot_be_enabled_or_launch_ready(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user()
    profile_id = "mm3820-stale-openai-contract"
    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=ProviderCredentialSource.NONE,
                runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                enabled=False,
                auth_state=ProviderProfileAuthState.NOT_CONFIGURED,
                disabled_reason=ProviderProfileDisabledReason.MISSING_CREDENTIALS,
            )
        )
        await session.commit()

    async with client_app as client:
        before = await client.get(f"/api/v1/provider-profiles/{profile_id}")
        update = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={
                "enabled": True,
                "auth_state": "connected",
                "disabled_reason": None,
            },
        )

    assert before.status_code == 200
    assert before.json()["authentication_method"] is None
    assert before.json()["launch_ready"] is False
    capability_check = next(
        check
        for check in before.json()["readiness"]["checks"]
        if check["id"] == "credential_capability"
    )
    assert capability_check["status"] == "error"
    assert update.status_code == 422
    assert "supported authentication preset" in update.json()["detail"]

    async with db_base.async_session_maker() as session:
        persisted = await session.get(ManagedAgentProviderProfile, profile_id)
        assert persisted is not None
        assert persisted.enabled is False
        assert persisted.auth_state is ProviderProfileAuthState.NOT_CONFIGURED


@pytest.mark.asyncio
async def test_explicit_credential_free_capability_creates_launch_ready_profile(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_current_user()
    capability_suffix = uuid4().hex
    capability = RuntimeProviderAuthenticationCapability(
        runtime_id=f"mm3820-local-runtime-{capability_suffix}",
        provider_id=f"mm3820-local-provider-{capability_suffix}",
        credential_free=True,
    )
    monkeypatch.setattr(
        provider_profile_creation,
        "_RUNTIME_PROVIDER_AUTHENTICATION_CAPABILITIES",
        (capability,),
    )
    created_profile_id = f"mm3820-none-created-{capability_suffix}"

    async with client_app as client:
        capabilities_response = await client.get(
            "/api/v1/provider-profiles/creation-capabilities",
            params={
                "runtime_id": capability.runtime_id,
                "provider_id": capability.provider_id,
            },
        )
        preset_response = await client.get(
            "/api/v1/provider-profiles/creation-preset",
            params={
                "runtime_id": capability.runtime_id,
                "provider_id": capability.provider_id,
                "authentication_method": "none",
            },
        )
        create_response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": created_profile_id,
                "runtime_id": capability.runtime_id,
                "provider_id": capability.provider_id,
                "authentication_method": "none",
                "preset_version": preset_response.json()["version"],
            },
        )

    assert [
        method["id"]
        for method in capabilities_response.json()["authentication_methods"]
    ] == ["none"]
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["credential_source"] == "none"
    assert created["runtime_materialization_mode"] == "composite"
    assert created["auth_state"] == "connected"
    assert created["enabled"] is True
    assert created["launch_ready"] is True


@pytest.mark.asyncio
async def test_mutable_profile_metadata_cannot_advertise_credential_free_support(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user()
    runtime_id = "mm3820-untrusted-runtime"
    provider_id = "mm3820-untrusted-provider"
    profile_id = "mm3820-untrusted-none-declaration"
    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id=runtime_id,
                provider_id=provider_id,
                credential_source=ProviderCredentialSource.NONE,
                runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
                enabled=False,
                auth_state=ProviderProfileAuthState.NOT_CONFIGURED,
                disabled_reason=ProviderProfileDisabledReason.MISSING_CREDENTIALS,
                command_behavior={"supported_auth_methods": ["none"]},
            )
        )
        await session.commit()

    async with client_app as client:
        preset_response = await client.get(
            "/api/v1/provider-profiles/creation-capabilities",
            params={"runtime_id": runtime_id, "provider_id": provider_id},
        )
        profile_response = await client.get(
            f"/api/v1/provider-profiles/{profile_id}"
        )
        unrelated_update = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={"account_label": "Inspectable legacy profile"},
        )
        manual_contract_update = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={
                "credential_source": "none",
                "runtime_materialization_mode": "composite",
            },
        )
        activation_response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={
                "enabled": True,
                "auth_state": "connected",
                "disabled_reason": None,
            },
        )

    assert preset_response.status_code == 200
    assert preset_response.json()["authentication_methods"] == []
    assert profile_response.status_code == 200
    profile_payload = profile_response.json()
    assert profile_payload["authentication_method"] is None
    assert profile_payload["launch_ready"] is False
    checks = {
        check["id"]: check for check in profile_payload["readiness"]["checks"]
    }
    assert checks["credential_capability"]["status"] == "error"
    assert unrelated_update.status_code == 200
    assert unrelated_update.json()["account_label"] == "Inspectable legacy profile"
    assert unrelated_update.json()["launch_ready"] is False
    assert manual_contract_update.status_code == 422
    assert "supported authentication preset" in manual_contract_update.json()["detail"]
    assert activation_response.status_code == 422
    assert "No authoritative authentication capability" in activation_response.json()[
        "detail"
    ]


@pytest.mark.asyncio
async def test_unrelated_update_preserves_enabled_unknown_profile_without_authority(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user()
    profile_id = "mm3820-enabled-unknown-inspection"
    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id="mm3820-legacy-runtime",
                provider_id="mm3820-legacy-provider",
                credential_source=ProviderCredentialSource.NONE,
                runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
                enabled=True,
                auth_state=ProviderProfileAuthState.CONNECTED,
                disabled_reason=None,
            )
        )
        await session.commit()

    async with client_app as client:
        response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={"account_label": "Legacy account"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["account_label"] == "Legacy account"
    assert payload["authentication_method"] is None
    assert payload["launch_ready"] is False


@pytest.mark.asyncio
async def test_imported_credential_volume_uses_derived_mount_and_validation(
    client_app: AsyncClient, _module_db, monkeypatch
) -> None:
    _override_current_user(is_superuser=True)
    verified: list[tuple[str, str, str]] = []

    async def _verify(
        *, runtime_id: str, volume_ref: str, volume_mount_path: str
    ) -> dict[str, object]:
        verified.append((runtime_id, volume_ref, volume_mount_path))
        return {"verified": True}

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.providers.volume_verifiers.verify_volume_credentials",
        _verify,
    )
    async with client_app as client:
        response = await client.post(
            "/api/v1/provider-profiles/credential-volume/validate",
            json={
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "volume_ref": "existing-codex-home",
            },
        )
        create_response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": "mm3820-imported-codex-home",
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "oauth",
                "preset_version": CODEX_OPENAI_OAUTH_PRESET_VERSION,
                "import_existing_credential_volume": True,
                "volume_ref": "existing-codex-home",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "validated",
        "volume_ref": "existing-codex-home",
        "volume_mount_path": "/home/app/.codex",
        "source": "validated_import",
    }
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["credential_source"] == "oauth_volume"
    assert created["runtime_materialization_mode"] == "oauth_home"
    assert created["volume_mount_path"] == "/home/app/.codex"
    assert created["enabled"] is True
    assert created["launch_ready"] is True
    assert verified == [
        ("codex_cli", "existing-codex-home", "/home/app/.codex"),
        ("codex_cli", "existing-codex-home", "/home/app/.codex"),
    ]

    # This module deliberately shares one database across tests.  Remove the
    # launch-ready Codex profile so its automatic default assignment does not
    # change the starting state of later default-selection regressions.
    async with db_base.async_session_maker() as session:
        imported_profile = await session.get(
            ManagedAgentProviderProfile,
            "mm3820-imported-codex-home",
        )
        assert imported_profile is not None
        await session.delete(imported_profile)
        await session.commit()


@pytest.mark.asyncio
async def test_imported_credential_volume_requires_superuser_authority(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user(is_superuser=False)

    async with client_app as client:
        response = await client.post(
            "/api/v1/provider-profiles/credential-volume/validate",
            json={
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "volume_ref": "another-users-home",
            },
        )

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Importing an existing credential volume requires superuser authority."
    )


@pytest.mark.asyncio
async def test_replacing_imported_volume_increments_and_reconciles_generation(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = f"mm3820-import-replacement-{uuid4().hex}"
    _override_current_user(is_superuser=True)
    reconciled: list[tuple[str, int]] = []

    async def _verify(
        *, runtime_id: str, volume_ref: str, volume_mount_path: str
    ) -> dict[str, object]:
        assert runtime_id == "codex_cli"
        assert volume_ref == "replacement-codex-home"
        assert volume_mount_path == "/home/app/.codex"
        return {"verified": True}

    async def _reconcile(profile: ManagedAgentProviderProfile) -> None:
        reconciled.append((profile.profile_id, profile.credential_generation))

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.providers.volume_verifiers.verify_volume_credentials",
        _verify,
    )
    monkeypatch.setattr(
        provider_profiles_router,
        "_reconcile_imported_credential_generation",
        _reconcile,
    )
    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                volume_ref="original-codex-home",
                volume_mount_path="/home/app/.codex",
                max_parallel_runs=1,
                enabled=True,
                auth_state=ProviderProfileAuthState.CONNECTED,
                disabled_reason=None,
                credential_generation=4,
                command_behavior={
                    "auth_readiness": {"connected": True, "launch_ready": True}
                },
            )
        )
        await session.commit()

    async with client_app as client:
        response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={
                "import_existing_credential_volume": True,
                "volume_ref": "replacement-codex-home",
            },
        )

    assert response.status_code == 200
    assert response.json()["volume_ref"] == "replacement-codex-home"
    assert response.json()["credential_generation"] == 5
    assert reconciled == [(profile_id, 5)]


@pytest.mark.asyncio
async def test_provider_profile_write_actions_require_write_permission(
    client_app: AsyncClient, _module_db
) -> None:
    user = _override_current_user(settings_permissions={"provider_profiles.read"})
    profile_id = "read_only_profile"

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="read_only_runtime",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.NONE,
                    runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
                    owner_user_id=user.id,
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        create_response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": "read_only_created",
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "credential_source": "none",
                "runtime_materialization_mode": "composite",
            },
        )
        update_response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={"enabled": False},
        )
        delete_response = await client.delete(f"/api/v1/provider-profiles/{profile_id}")
        api_key_response = await client.post(
            "/api/v1/provider-profiles/missing-api-key-profile/credentials/api-key",
            json={"api_key": "sk-mm875-read-only-token"},
        )

    assert create_response.status_code == 403
    assert update_response.status_code == 403
    assert delete_response.status_code == 403
    assert api_key_response.status_code == 403
    assert create_response.json()["detail"] == (
        "Missing required provider profile permission: provider_profiles.write."
    )


class _TrackedProfile:
    def __init__(
        self,
        *,
        profile_id: str,
        runtime_id: str,
        enabled: bool,
        priority: int,
        is_default: bool,
        events: list[tuple[object, ...]],
        auth_state: ProviderProfileAuthState = ProviderProfileAuthState.CONNECTED,
        disabled_reason: ProviderProfileDisabledReason | None = None,
        credential_source: ProviderCredentialSource = ProviderCredentialSource.NONE,
        runtime_materialization_mode: RuntimeMaterializationMode = (
            RuntimeMaterializationMode.COMPOSITE
        ),
        max_parallel_runs: int = 1,
        cooldown_after_429_seconds: int = 900,
        secret_refs: Any = None,
        volume_ref: str | None = None,
        volume_mount_path: str | None = None,
        command_behavior: dict | None = None,
        provider_id: str = "unknown",
    ) -> None:
        self.profile_id = profile_id
        self.runtime_id = runtime_id
        self.provider_id = provider_id
        self.enabled = enabled
        self.auth_state = auth_state
        self.disabled_reason = disabled_reason
        self.credential_source = credential_source
        self.runtime_materialization_mode = runtime_materialization_mode
        self.max_parallel_runs = max_parallel_runs
        self.cooldown_after_429_seconds = cooldown_after_429_seconds
        self.secret_refs = secret_refs or {}
        self.volume_ref = volume_ref
        self.volume_mount_path = volume_mount_path
        self.command_behavior = command_behavior or {}
        self.priority = priority
        self._is_default = is_default
        self._events = events

    @property
    def is_default(self) -> bool:
        return self._is_default

    @is_default.setter
    def is_default(self, value: bool) -> None:
        self._is_default = value
        self._events.append(("set", self.profile_id, value))


class _TrackedExecuteResult:
    def __init__(self, rows: list[_TrackedProfile]) -> None:
        self._rows = rows

    def scalars(self) -> "_TrackedExecuteResult":
        return self

    def all(self) -> list[_TrackedProfile]:
        return self._rows


class _TrackedDefaultSession:
    def __init__(
        self,
        rows: list[_TrackedProfile],
        events: list[tuple[object, ...]],
    ) -> None:
        self._rows = rows
        self._events = events

    async def execute(self, _statement):
        return _TrackedExecuteResult(self._rows)

    async def flush(self) -> None:
        self._events.append(
            (
                "flush",
                {
                    row.profile_id: row.is_default
                    for row in self._rows
                },
            )
        )


@pytest.mark.asyncio
async def test_runtime_default_switch_flushes_old_default_first():
    events: list[tuple[object, ...]] = []
    minimax = _TrackedProfile(
        profile_id="claude_minimax",
        runtime_id="claude_code",
        provider_id="anthropic",
        enabled=True,
        priority=200,
        is_default=True,
        credential_source=ProviderCredentialSource.SECRET_REF,
        runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
        secret_refs={"anthropic_api_key": "env://ANTHROPIC_API_KEY"},
        events=events,
    )
    anthropic = _TrackedProfile(
        profile_id="claude_anthropic",
        runtime_id="claude_code",
        provider_id="anthropic",
        enabled=True,
        priority=100,
        is_default=False,
        credential_source=ProviderCredentialSource.SECRET_REF,
        runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
        secret_refs={"anthropic_api_key": "env://ANTHROPIC_API_KEY"},
        events=events,
    )
    session = _TrackedDefaultSession([minimax, anthropic], events)

    selected = await normalize_runtime_default_profile(
        session=session,
        runtime_id="claude_code",
        preferred_profile_id="claude_anthropic",
    )

    assert selected == "claude_anthropic"
    assert events == [
        ("set", "claude_minimax", False),
        (
            "flush",
            {"claude_minimax": False, "claude_anthropic": False},
        ),
        ("set", "claude_anthropic", True),
        (
            "flush",
            {"claude_minimax": False, "claude_anthropic": True},
        ),
    ]


@pytest.mark.asyncio
async def test_runtime_default_normalization_skips_not_launch_ready_profiles():
    events: list[tuple[object, ...]] = []
    blocked_default = _TrackedProfile(
        profile_id="claude_blocked",
        runtime_id="claude_code",
        provider_id="anthropic",
        enabled=True,
        priority=500,
        is_default=True,
        auth_state=ProviderProfileAuthState.CONNECTED,
        credential_source=ProviderCredentialSource.SECRET_REF,
        runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
        secret_refs={"anthropic_api_key": "env://ANTHROPIC_API_KEY"},
        command_behavior={"auth_readiness": {"launch_ready": False}},
        events=events,
    )
    ready_fallback = _TrackedProfile(
        profile_id="claude_ready",
        runtime_id="claude_code",
        provider_id="anthropic",
        enabled=True,
        priority=100,
        is_default=False,
        auth_state=ProviderProfileAuthState.CONNECTED,
        credential_source=ProviderCredentialSource.SECRET_REF,
        runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
        secret_refs={"anthropic_api_key": "env://ANTHROPIC_API_KEY"},
        command_behavior={"auth_readiness": {"launch_ready": True}},
        events=events,
    )
    session = _TrackedDefaultSession([blocked_default, ready_fallback], events)

    selected = await normalize_runtime_default_profile(
        session=session,
        runtime_id="claude_code",
    )

    assert selected == "claude_ready"
    assert blocked_default.is_default is False
    assert ready_fallback.is_default is True


def test_launch_ready_rejects_malformed_secret_refs() -> None:
    profile = _TrackedProfile(
        profile_id="malformed_secret_refs",
        runtime_id="codex_cli",
        enabled=True,
        priority=100,
        is_default=False,
        events=[],
        auth_state=ProviderProfileAuthState.CONNECTED,
        credential_source=ProviderCredentialSource.SECRET_REF,
        secret_refs=["db://not-a-dict"],
    )

    assert provider_profile_launch_ready(profile) is False

    profile.secret_refs = {"provider_api_key": 123}

    assert provider_profile_launch_ready(profile) is False


def test_launch_ready_requires_backend_declared_secret_role() -> None:
    profile = _TrackedProfile(
        profile_id="missing_openai_role",
        runtime_id="codex_cli",
        provider_id="openai",
        enabled=True,
        priority=100,
        is_default=False,
        events=[],
        auth_state=ProviderProfileAuthState.CONNECTED,
        credential_source=ProviderCredentialSource.SECRET_REF,
        runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
        secret_refs={"unknown_role": "env://UNRELATED_TOKEN"},
    )

    assert provider_profile_launch_ready(profile) is False
    assert (
        provider_profile_launch_ready_from_payload(
            {
                "runtimeId": "codex_cli",
                "providerId": "openai",
                "credentialSource": "secret_ref",
                "runtimeMaterializationMode": "api_key_env",
                "secretRefs": {"unknown_role": "env://UNRELATED_TOKEN"},
            }
        )
        is False
    )

    profile.secret_refs["openai_api_key"] = "env://OPENAI_API_KEY"

    assert provider_profile_launch_ready(profile) is True


def test_launch_ready_rejects_capability_mismatched_known_provider_contract() -> None:
    profile = _TrackedProfile(
        profile_id="unsupported_openai_credential_free",
        runtime_id="codex_cli",
        provider_id="openai",
        enabled=True,
        priority=100,
        is_default=False,
        events=[],
        auth_state=ProviderProfileAuthState.CONNECTED,
        credential_source=ProviderCredentialSource.NONE,
        runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
    )

    assert provider_profile_launch_ready(profile) is False
    assert (
        provider_profile_launch_ready_from_payload(
            {
                "runtimeId": "codex_cli",
                "providerId": "openai",
                "credentialSource": "none",
                "runtimeMaterializationMode": "api_key_env",
                "enabled": True,
            }
        )
        is False
    )


def test_launch_ready_rejects_nonexclusive_codex_oauth_profile() -> None:
    profile = _TrackedProfile(
        profile_id="codex_oauth_legacy_capacity",
        runtime_id="codex_cli",
        enabled=True,
        priority=100,
        is_default=False,
        events=[],
        auth_state=ProviderProfileAuthState.CONNECTED,
        credential_source=ProviderCredentialSource.OAUTH_VOLUME,
        runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
        volume_ref="codex_auth_volume",
        volume_mount_path="/home/app/.codex",
        max_parallel_runs=2,
    )

    assert provider_profile_launch_ready(profile) is False

    assert provider_profile_launch_ready_from_payload(
        {
            "runtimeId": "codex_cli",
            "credentialSource": "oauth_volume",
            "runtimeMaterializationMode": "oauth_home",
            "maxParallelRuns": 2,
        }
    ) is False


@pytest.mark.asyncio
async def test_managed_secret_statuses_ignores_malformed_secret_refs() -> None:
    class _EmptySecretSession:
        async def execute(self, _stmt):
            raise AssertionError("malformed secret_refs should not query secrets")

    rows = [
        _TrackedProfile(
            profile_id="malformed_secret_refs",
            runtime_id="codex_cli",
            enabled=True,
            priority=100,
            is_default=False,
            events=[],
            secret_refs=["db://not-a-dict"],
        ),
        _TrackedProfile(
            profile_id="non_string_secret_ref",
            runtime_id="codex_cli",
            enabled=True,
            priority=100,
            is_default=False,
            events=[],
            secret_refs={"provider_api_key": 123},
        ),
    ]

    statuses = await _managed_secret_statuses_for_profiles(
        session=_EmptySecretSession(),
        rows=rows,
    )

    assert statuses == {}

async def get_or_create_sample_profile() -> ManagedAgentProviderProfile:
    """Helper to create a baseline profile in the test DB."""
    profile_id = "test_custom_profile"
    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing:
            return existing
            
        profile = ManagedAgentProviderProfile(
            profile_id=profile_id,
            runtime_id="custom_runtime",
            credential_source=ProviderCredentialSource.OAUTH_VOLUME,
            volume_ref="custom_auth_volume",
            account_label="test_account",
            max_parallel_runs=2,
            cooldown_after_429_seconds=120,
            rate_limit_policy=ManagedAgentRateLimitPolicy.BACKOFF,
            enabled=True,
            is_default=True,
        )
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
        return profile

@pytest.mark.asyncio
async def test_provider_profile_response_redacts_secret_like_runtime_fields(
    client_app: AsyncClient, _module_db
) -> None:
    """Browser-visible profile responses must not expose raw secret-like values."""
    profile_id = "profile_with_raw_runtime_secret"
    raw_secret = "sk-test-raw-secret-value"

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="redaction_runtime",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode="api_key_env",
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    env_template={"OPENAI_API_KEY": raw_secret},
                    file_templates=[
                        {"path": "/tmp/auth.json", "content": raw_secret},
                        {
                            "path": "/tmp/config.json",
                            "contentTemplate": {"token": raw_secret},
                        },
                        {
                            "path": "/tmp/config.toml",
                            "content_template": {"api_key": raw_secret},
                        },
                    ],
                    command_behavior={"authorization": f"Bearer {raw_secret}"},
                    secret_refs={"provider_api_key": "env://OPENAI_API_KEY"},
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    response_text = response.text
    assert raw_secret not in response_text
    assert "Bearer" not in response_text
    assert response.json()["volume_ref"] == "codex_auth_volume"
    assert response.json()["volume_mount_path"] == "/home/app/.codex"
    assert response.json()["env_template"]["OPENAI_API_KEY"] == "[REDACTED]"
    assert response.json()["file_templates"][0]["content"] == "[REDACTED]"
    assert response.json()["file_templates"][1]["contentTemplate"]["token"] == (
        "[REDACTED]"
    )
    assert (
        response.json()["file_templates"][2]["content_template"]["api_key"]
        == "[REDACTED]"
    )
    assert response.json()["secret_refs"] == {"provider_api_key": "env://OPENAI_API_KEY"}

def test_provider_profile_manager_payload_redacts_secret_like_runtime_fields() -> None:
    raw_secret = "sk-test-manager-payload-secret"
    row = ManagedAgentProviderProfile(
        profile_id="manager_payload_redaction",
        runtime_id="codex_cli",
        provider_id="openai",
        credential_source=ProviderCredentialSource.OAUTH_VOLUME,
        runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
        volume_ref="codex_auth_volume",
        volume_mount_path="/home/app/.codex",
        env_template={"OPENAI_API_KEY": raw_secret},
        file_templates=[
            {"path": "/tmp/auth.json", "content": raw_secret},
            {"path": "/tmp/config.json", "contentTemplate": raw_secret},
            {
                "path": "/tmp/config.toml",
                "content_template": {"api_key": raw_secret},
            },
        ],
        command_behavior={"authorization": f"Bearer {raw_secret}"},
        secret_refs={"provider_api_key": "env://OPENAI_API_KEY"},
        model_tiers=[
            {"label": "Plan", "model": "gpt-5-mini", "effort": "low"},
            {"label": "Build", "model": "gpt-5.5", "effort": "high"},
        ],
        default_model_tier=2,
        max_parallel_runs=2,
        cooldown_after_429_seconds=120,
        max_lease_duration_seconds=900,
        enabled=True,
    )

    payload = _manager_profile_payload(row)

    assert raw_secret not in repr(payload)
    assert payload["volume_ref"] == "codex_auth_volume"
    assert payload["volume_mount_path"] == "/home/app/.codex"
    assert payload["max_parallel_runs"] == 2
    assert payload["cooldown_after_429_seconds"] == 120
    assert payload["max_lease_duration_seconds"] == 900
    assert payload["model_tiers"] == [
        {
            "label": "Plan",
            "model": "gpt-5-mini",
            "effort": "low",
            "parameters": {},
            "annotations": {},
        },
        {
            "label": "Build",
            "model": "gpt-5.5",
            "effort": "high",
            "parameters": {},
            "annotations": {},
        },
    ]
    assert payload["default_model_tier"] == 2
    assert payload["env_template"]["OPENAI_API_KEY"] == "[REDACTED]"
    assert payload["file_templates"][0]["content"] == "[REDACTED]"
    assert payload["file_templates"][1]["contentTemplate"] == "[REDACTED]"
    assert payload["file_templates"][2]["content_template"]["api_key"] == "[REDACTED]"
    assert payload["command_behavior"]["authorization"] == "[REDACTED_AUTHORIZATION]"
    assert payload["secret_refs"] == {"provider_api_key": "env://OPENAI_API_KEY"}


def test_manager_profile_payload_redacts_model_tier_metadata() -> None:
    raw_secret = "Bearer sk-manager-tier-secret"
    row = ManagedAgentProviderProfile(
        profile_id="redacted_tier_metadata",
        runtime_id="codex_cli",
        provider_id="openai",
        model_tiers=[
            {
                "label": "Tier 1",
                "model": "gpt-test",
                "effort": "medium",
                "parameters": {"max_tokens": 4096},
                "annotations": {"note": raw_secret},
            }
        ],
        default_model_tier=1,
    )

    payload = _manager_profile_payload(row)

    assert raw_secret not in repr(payload)
    assert payload["model_tiers"] == [
        {
            "label": "Tier 1",
            "model": "gpt-test",
            "effort": "medium",
            "parameters": {"max_tokens": 4096},
            "annotations": {"note": "[REDACTED_AUTHORIZATION]"},
        }
    ]

@pytest.mark.asyncio
async def test_create_codex_oauth_profile_requires_volume_ref_and_mount_path(
    client_app: AsyncClient, _module_db
) -> None:
    payload = {
        "profile_id": "codex_oauth_missing_refs",
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "credential_source": "oauth_volume",
        "runtime_materialization_mode": "oauth_home",
        "enabled": True,
        "auth_state": "connected",
        "disabled_reason": None,
    }

    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)

    assert response.status_code == 422
    assert "volume_ref is required" in response.text
    assert "volume_mount_path is required" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "runtime_id",
        "provider_id",
        "authentication_method",
        "credential_source",
        "materialization_mode",
    ),
    [
        ("codex_cli", "openai", "oauth", "none", "oauth_home"),
        ("codex_cli", "openai", "api_key", "none", "api_key_env"),
        ("claude_code", "anthropic", "oauth", "none", "oauth_home"),
        (
            "claude_code",
            "anthropic",
            "api_key",
            "none",
            "api_key_env",
        ),
        ("opencode", "opencode", "none", "none", "composite"),
    ],
)
async def test_creation_preset_conformance_matrix(
    client_app: AsyncClient,
    _module_db,
    runtime_id: str,
    provider_id: str,
    authentication_method: str,
    credential_source: str,
    materialization_mode: str,
) -> None:
    async with client_app as client:
        response = await client.get(
            "/api/v1/provider-profiles/creation-preset",
            params={
                "runtime_id": runtime_id,
                "provider_id": provider_id,
                "authentication_method": authentication_method,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    assert payload["version"].startswith("provider-profile-create-v1-")
    assert payload["runtime_id"] == runtime_id
    assert payload["provider_id"] == provider_id
    assert payload["authentication_method"] == authentication_method
    assert payload["fields"]["credential_source"]["value"] == credential_source
    assert (
        payload["fields"]["runtime_materialization_mode"]["value"]
        == materialization_mode
    )
    required_metadata = {"value", "source", "editable", "required", "lock_reason"}
    for field_name in (
        "credential_source",
        "runtime_materialization_mode",
        "max_parallel_runs",
        "cooldown_after_429_seconds",
        "rate_limit_policy",
        "priority",
        "user_tags",
        "system_tags",
        "volume_ref",
        "volume_mount_path",
        "secret_ref_roles",
        "command_behavior",
        "clear_env_keys",
        "enabled",
        "auth_state",
        "first_authenticated_at",
        "last_validated_at",
        "may_become_runtime_default",
    ):
        assert required_metadata == set(payload["fields"][field_name])
    assert payload["fields"]["enabled"]["value"] is (
        authentication_method == "none"
    )
    assert payload["fields"]["enabled"]["editable"] is False
    if authentication_method == "none":
        assert payload["diagnostics"] == []
        assert payload["fields"]["command_behavior"]["value"]["auth_readiness"] == {
            "connected": True,
            "backing_secret_exists": False,
            "launch_ready": True,
        }
    else:
        assert payload["diagnostics"][0]["code"] == "credential_setup_required"


@pytest.mark.asyncio
async def test_creation_preset_reports_actionable_unsupported_combination(
    client_app: AsyncClient,
    _module_db,
) -> None:
    async with client_app as client:
        response = await client.get(
            "/api/v1/provider-profiles/creation-preset",
            params={
                "runtime_id": "custom_runtime",
                "provider_id": "custom_provider",
                "authentication_method": "api_key",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is False
    assert payload["manual_creation_allowed"] is True
    assert payload["required_manual_fields"] == [
        "credential_source",
        "runtime_materialization_mode",
        "clear_env_keys",
        "command_behavior",
    ]
    assert payload["diagnostics"] == [
        {
            "code": "no_safe_standard_creation_preset",
            "severity": "error",
            "message": (
                "No validated standard creation preset exists for this runtime, "
                "provider, and authentication method. Use the authorized manual "
                "profile path and supply every required launch field."
            ),
            "field": None,
            "action": "open_manual_profile",
        }
    ]


@pytest.mark.asyncio
async def test_create_applies_preset_to_omitted_advanced_fields_atomically(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = f"preset-omission-{uuid4().hex}"
    async with client_app as client:
        preset_response = await client.get(
            "/api/v1/provider-profiles/creation-preset",
            params={
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "api_key",
            },
        )
        preset = preset_response.json()
        response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": profile_id,
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "api_key",
                "preset_version": preset["version"],
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["credential_source"] == "none"
    assert payload["runtime_materialization_mode"] == "api_key_env"
    assert payload["max_parallel_runs"] == 1
    assert payload["cooldown_after_429_seconds"] == 300
    assert payload["rate_limit_policy"] == "backoff"
    assert payload["priority"] == 100
    assert payload["tags"] == ["api-key", "first-party"]
    assert payload["secret_refs"] == {}
    assert payload["env_template"] == {
        "OPENAI_API_KEY": {"from_secret_ref": "openai_api_key"}
    }
    assert "MINIMAX_API_KEY" in payload["clear_env_keys"]
    assert payload["command_behavior"]["auth_strategy"] == "api_key_env"
    assert payload["enabled"] is False
    assert payload["auth_state"] == "api_key_pending"
    assert payload["disabled_reason"] == "missing_credentials"
    assert payload["launch_ready"] is False
    assert payload["is_default"] is False


@pytest.mark.asyncio
async def test_create_accepts_editable_preset_override_and_preserves_system_tags(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = f"preset-override-{uuid4().hex}"
    async with client_app as client:
        preset = (
            await client.get(
                "/api/v1/provider-profiles/creation-preset",
                params={
                    "runtime_id": "claude_code",
                    "provider_id": "anthropic",
                    "authentication_method": "api_key",
                },
            )
        ).json()
        response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": profile_id,
                "runtime_id": "claude_code",
                "provider_id": "anthropic",
                "authentication_method": "api_key",
                "preset_version": preset["version"],
                "max_parallel_runs": 3,
                "cooldown_after_429_seconds": 42,
                "rate_limit_policy": "queue",
                "tags": ["team-a"],
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["max_parallel_runs"] == 3
    assert payload["cooldown_after_429_seconds"] == 42
    assert payload["rate_limit_policy"] == "queue"
    assert payload["tags"] == ["api-key", "first-party", "team-a"]


@pytest.mark.asyncio
async def test_create_rejects_locked_preset_override_before_persistence(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = f"locked-preset-{uuid4().hex}"
    async with client_app as client:
        preset = (
            await client.get(
                "/api/v1/provider-profiles/creation-preset",
                params={
                    "runtime_id": "codex_cli",
                    "provider_id": "openai",
                    "authentication_method": "api_key",
                },
            )
        ).json()
        response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": profile_id,
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "api_key",
                "preset_version": preset["version"],
                "credential_source": "secret_ref",
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "provider_profile_creation_preset_field_locked"
    assert detail["field"] == "credential_source"
    assert detail["expected_value"] == "none"
    assert "validated credential setup" in detail["lock_reason"]
    async with db_base.async_session_maker() as session:
        assert await session.get(ManagedAgentProviderProfile, profile_id) is None


@pytest.mark.asyncio
async def test_create_rejects_stale_preset_before_persistence(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = f"stale-preset-{uuid4().hex}"
    async with client_app as client:
        response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": profile_id,
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "api_key",
                "preset_version": "stale-browser-version",
            },
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "provider_profile_creation_preset_version_mismatch"
    assert detail["requested_version"] == "stale-browser-version"
    assert detail["current_version"].startswith("provider-profile-create-v1-")
    async with db_base.async_session_maker() as session:
        assert await session.get(ManagedAgentProviderProfile, profile_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timestamp_field",
    ["first_authenticated_at", "last_validated_at"],
)
async def test_create_rejects_guided_authentication_history_override(
    client_app: AsyncClient,
    _module_db,
    timestamp_field: str,
) -> None:
    profile_id = f"preset-auth-history-{timestamp_field}-{uuid4().hex}"
    async with client_app as client:
        preset = (
            await client.get(
                "/api/v1/provider-profiles/creation-preset",
                params={
                    "runtime_id": "codex_cli",
                    "provider_id": "openai",
                    "authentication_method": "api_key",
                },
            )
        ).json()
        response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": profile_id,
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "api_key",
                "preset_version": preset["version"],
                timestamp_field: "2026-08-30T20:00:00Z",
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "provider_profile_creation_preset_field_locked"
    assert detail["field"] == timestamp_field
    assert detail["expected_value"] is None
    async with db_base.async_session_maker() as session:
        assert await session.get(ManagedAgentProviderProfile, profile_id) is None


@pytest.mark.asyncio
async def test_create_guided_profile_persists_preset_normalized_identity(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = f"preset-normalized-identity-{uuid4().hex}"
    async with client_app as client:
        preset = (
            await client.get(
                "/api/v1/provider-profiles/creation-preset",
                params={
                    "runtime_id": "codex_cli",
                    "provider_id": "openai",
                    "authentication_method": "api_key",
                },
            )
        ).json()
        response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": profile_id,
                "runtime_id": " codex_cli ",
                "provider_id": " openai ",
                "authentication_method": "api_key",
                "preset_version": preset["version"],
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["runtime_id"] == "codex_cli"
    assert payload["provider_id"] == "openai"
    async with db_base.async_session_maker() as session:
        row = await session.get(ManagedAgentProviderProfile, profile_id)
        assert row is not None
        assert row.runtime_id == "codex_cli"
        assert row.provider_id == "openai"


@pytest.mark.asyncio
async def test_create_rejects_unsupported_standard_combination_before_persistence(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = f"unsupported-preset-{uuid4().hex}"
    async with client_app as client:
        preset = (
            await client.get(
                "/api/v1/provider-profiles/creation-preset",
                params={
                    "runtime_id": "custom_runtime",
                    "provider_id": "custom_provider",
                    "authentication_method": "api_key",
                },
            )
        ).json()
        response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": profile_id,
                "runtime_id": "custom_runtime",
                "provider_id": "custom_provider",
                "authentication_method": "api_key",
                "preset_version": preset["version"],
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "provider_profile_creation_preset_unsupported"
    assert detail["manual_creation_allowed"] is True
    assert detail["required_manual_fields"]
    async with db_base.async_session_maker() as session:
        assert await session.get(ManagedAgentProviderProfile, profile_id) is None


@pytest.mark.asyncio
async def test_create_guided_oauth_profile_is_disabled_until_enrollment(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = f"oauth-preset-{uuid4().hex}"
    async with client_app as client:
        preset = (
            await client.get(
                "/api/v1/provider-profiles/creation-preset",
                params={
                    "runtime_id": "codex_cli",
                    "provider_id": "openai",
                    "authentication_method": "oauth",
                },
            )
        ).json()
        response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": profile_id,
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "oauth",
                "preset_version": preset["version"],
                "is_default": True,
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["credential_source"] == "none"
    assert payload["runtime_materialization_mode"] == "oauth_home"
    assert payload["volume_ref"].startswith("moonmind_oauth_")
    assert payload["volume_mount_path"] == "/home/app/.codex"
    assert payload["max_parallel_runs"] == 1
    assert payload["enabled"] is False
    assert payload["launch_ready"] is False
    assert payload["is_default"] is False


@pytest.mark.asyncio
async def test_guided_oauth_profiles_reserve_distinct_credential_volumes(
    client_app: AsyncClient, _module_db
) -> None:
    first_id = f"oauth-preset-{uuid4().hex}"
    second_id = f"oauth-preset-{uuid4().hex}"
    async with client_app as client:
        first = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": first_id,
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "oauth",
                "preset_version": CODEX_OPENAI_OAUTH_PRESET_VERSION,
            },
        )
        second = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": second_id,
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "oauth",
                "preset_version": CODEX_OPENAI_OAUTH_PRESET_VERSION,
            },
        )

    assert first.status_code == second.status_code == 201
    assert first.json()["volume_ref"] != second.json()["volume_ref"]

@pytest.mark.asyncio
async def test_provider_profile_update_rejects_non_owner(
    client_app: AsyncClient, _module_db
) -> None:
    profile_id = "profile_owned_by_someone_else"
    owner_id = uuid4()

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="owner_runtime",
                    provider_id="openai",
                    owner_user_id=owner_id,
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode="oauth_home",
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    enabled=True,
                )
            )
            await session.commit()

    other_user = _override_current_user(user_id=uuid4(), is_superuser=False)
    try:
        async with client_app as client:
            response = await client.patch(
                f"/api/v1/provider-profiles/{profile_id}",
                json={"enabled": False},
            )
    finally:
        app.dependency_overrides.clear()

    assert str(other_user.id) != str(owner_id)
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to manage this provider profile."

@pytest.mark.asyncio
async def test_provider_profile_update_allows_ownerless_shared_profile(
    client_app: AsyncClient, _module_db
) -> None:
    profile_id = "ownerless_shared_profile"

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="shared_runtime",
                    provider_id="openai",
                    owner_user_id=None,
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode="oauth_home",
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    enabled=True,
                )
            )
            await session.commit()

    _override_current_user(user_id=uuid4(), is_superuser=False)
    try:
        async with client_app as client:
            response = await client.patch(
                f"/api/v1/provider-profiles/{profile_id}",
                json={"enabled": False},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False
    assert data["volume_mount_path"] == "/home/app/.codex"

@pytest.mark.asyncio
async def test_create_provider_profile(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test creating a new provider profile."""
    _advertise_expert_manual_contracts(
        monkeypatch,
        ("claude_v1", "unknown", "secret_ref", "api_key_env"),
    )
    payload = {
        "profile_id": "new_profile",
        "runtime_id": "claude_v1",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://secret_v1"},
        # #3821: unknown strategy needs an explicit shape-valid policy to
        # classify as legacy_custom (warning) instead of missing (error).
        "clear_env_keys": ["CUSTOM_LEGACY_KEY"],
        "max_parallel_runs": 5,
        "cooldown_after_429_seconds": 60,
        "rate_limit_policy": "queue",
        "default_model": "test-model-v2",
        "default_effort": "high",
        "model_overrides": {"smart": "test-model-v3"},
        "model_tiers": [
            {"label": "Plan", "model": "test-model-v1", "effort": "low"},
            {"label": "Build", "model": "test-model-v2", "effort": "high"},
        ],
        "default_model_tier": 2,
        "enabled": True,
        "auth_state": "connected",
        "last_auth_method": "secret_ref",
    }
    
    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)
    
    assert response.status_code == 201
    data = response.json()
    assert data["profile_id"] == "new_profile"
    assert data["credential_source"] == "secret_ref"
    assert data["rate_limit_policy"] == "queue"
    assert data["default_model"] == "test-model-v2"
    assert data["default_effort"] == "high"
    assert data["model_overrides"] == {"smart": "test-model-v3"}
    assert data["model_tiers"] == [
        {
            "label": "Plan",
            "model": "test-model-v1",
            "effort": "low",
            "parameters": {},
            "annotations": {},
        },
        {
            "label": "Build",
            "model": "test-model-v2",
            "effort": "high",
            "parameters": {},
            "annotations": {},
        },
    ]
    assert data["default_model_tier"] == 2
    assert data["is_default"] is True
    assert data["auth_state"] == "connected"
    assert data["disabled_reason"] is None
    assert data["last_auth_method"] == "secret_ref"


@pytest.mark.asyncio
async def test_provider_profile_tier_policy_round_trips_through_get_and_update(
    client_app: AsyncClient, _module_db
) -> None:
    profile_id = "tier_policy_roundtrip_profile"
    create_payload = {
        "profile_id": profile_id,
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "authentication_method": "api_key",
        "preset_version": CODEX_OPENAI_API_KEY_PRESET_VERSION,
        "model_tiers": [
            {"label": "Review", "model": "gpt-5-mini", "effort": "low"},
            {"label": "Implement", "model": "gpt-5.5", "effort": "high"},
        ],
        "default_model_tier": 1,
    }
    update_payload = {
        "model_tiers": [
            {"label": "Cheap", "model": "gpt-5-nano", "effort": "low"},
            {"label": "Deep", "model": "gpt-5.5", "effort": "xhigh"},
            {"label": "Fallback", "model": None, "effort": None},
        ],
        "default_model_tier": 2,
    }

    async with client_app as client:
        create_response = await client.post(
            "/api/v1/provider-profiles", json=create_payload
        )
        update_response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}", json=update_payload
        )
        get_response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert create_response.status_code == 201
    assert update_response.status_code == 200
    assert get_response.status_code == 200
    data = get_response.json()
    expected_tiers = [
        {
            "label": "Cheap",
            "model": "gpt-5-nano",
            "effort": "low",
            "parameters": {},
            "annotations": {},
        },
        {
            "label": "Deep",
            "model": "gpt-5.5",
            "effort": "xhigh",
            "parameters": {},
            "annotations": {},
        },
        {
            "label": "Fallback",
            "model": None,
            "effort": None,
            "parameters": {},
            "annotations": {},
        },
    ]
    assert data["model_tiers"] == expected_tiers
    assert data["default_model_tier"] == 2

    async with db_base.async_session_maker() as session:
        row = await session.get(ManagedAgentProviderProfile, profile_id)
        assert row is not None
        assert row.model_tiers == expected_tiers
        assert row.default_model_tier == 2


@pytest.mark.asyncio
async def test_provider_profile_model_tier_preview_returns_advisory_resolution(
    client_app: AsyncClient, _module_db
) -> None:
    profile_id = "tier_preview_profile_mm1172"
    payload = {
        "profile_id": profile_id,
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "authentication_method": "api_key",
        "preset_version": CODEX_OPENAI_API_KEY_PRESET_VERSION,
        "model_tiers": [
            {"label": "Plan", "model": "gpt-5-mini", "effort": "low"},
            {"label": "Implement", "model": "gpt-5.5", "effort": "xhigh"},
        ],
        "default_model_tier": 1,
    }

    async with client_app as client:
        create_response = await client.post("/api/v1/provider-profiles", json=payload)
        preview_response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/model-tiers:preview",
            json={
                "steps": [
                    {"id": "plan", "modelTier": 1},
                    {"id": "docs", "modelTier": 3},
                ]
            },
        )

    assert create_response.status_code == 201
    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert isinstance(preview_payload["profileVersion"], str)
    assert preview_payload["profileVersion"]
    assert preview_payload == {
        "profileId": profile_id,
        "profileVersion": preview_payload["profileVersion"],
        "advisory": True,
        "items": [
            {
                "stepId": "plan",
                "requestedTier": 1,
                "effectiveTier": 1,
                "model": "gpt-5-mini",
                "effort": "low",
                "fallbackReason": None,
            },
            {
                "stepId": "docs",
                "requestedTier": 3,
                "effectiveTier": 2,
                "model": "gpt-5.5",
                "effort": "xhigh",
                "fallbackReason": "requested_tier_above_configured_range",
            },
        ],
    }


@pytest.mark.asyncio
async def test_provider_profile_model_tier_preview_preserves_strict_error_code(
    client_app: AsyncClient, _module_db
) -> None:
    profile_id = "tier_preview_strict_error_code"
    payload = {
        "profile_id": profile_id,
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "authentication_method": "api_key",
        "preset_version": CODEX_OPENAI_API_KEY_PRESET_VERSION,
        "model_tiers": [
            {"label": "Plan", "model": "gpt-5-mini", "effort": "low"},
            {"label": "Implement", "model": "gpt-5.5", "effort": "xhigh"},
        ],
        "default_model_tier": 1,
    }

    async with client_app as client:
        create_response = await client.post("/api/v1/provider-profiles", json=payload)
        preview_response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/model-tiers:preview",
            json={
                "steps": [
                    {"id": "docs", "modelTier": 3, "tierFallback": "strict"}
                ]
            },
        )

    assert create_response.status_code == 201
    assert preview_response.status_code == 422
    assert preview_response.json()["detail"] == {
        "code": "requested_model_tier_unavailable",
        "message": (
            "Requested model tier 3 is unavailable; "
            "the selected profile defines 2 tiers."
        ),
        "requestedModelTier": 3,
        "configuredTierCount": 2,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("default_model_tier", [2, True])
async def test_provider_profile_rejects_invalid_default_model_tier(
    client_app: AsyncClient, _module_db, default_model_tier: object
) -> None:
    async with client_app as client:
        response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": f"tier_policy_invalid_default_{default_model_tier}",
                "runtime_id": "codex_cli",
                "credential_source": "none",
                "runtime_materialization_mode": "composite",
                "model_tiers": [
                    {"label": "Only", "model": "gpt-5-mini", "effort": "low"}
                ],
                "default_model_tier": default_model_tier,
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_provider_profile_rejects_overlong_default_effort(
    client_app: AsyncClient, _module_db
) -> None:
    payload = {
        "profile_id": "overlong_default_effort_create",
        "runtime_id": "claude_v1",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://overlong_default_effort_create"},
        "default_effort": "x" * 65,
    }

    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_provider_profile_rejects_default_tier_outside_configured_tiers(
    client_app: AsyncClient, _module_db
) -> None:
    payload = {
        "profile_id": "invalid_default_model_tier",
        "runtime_id": "codex_cli",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://invalid_default_model_tier"},
        "model_tiers": [
            {"label": "Only tier", "model": "gpt-5.5", "effort": "medium"},
        ],
        "default_model_tier": 2,
    }

    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_mm1169_create_provider_profile_persists_explicit_model_tiers(
    client_app: AsyncClient, _module_db
) -> None:
    payload = {
        "profile_id": "explicit_model_tier_profile",
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "authentication_method": "api_key",
        "preset_version": CODEX_OPENAI_API_KEY_PRESET_VERSION,
        "model_tiers": [
            {
                "label": "Plan",
                "model": "provider-model-one",
                "effort": "medium",
                "parameters": {"temperature": 0},
                "annotations": {"costClass": "standard"},
            },
            {
                "label": "Implement",
                "model": "provider-model-two",
                "effort": "xhigh",
                "parameters": {},
                "annotations": {"recommendedFor": ["implementation"]},
            },
        ],
        "default_model_tier": 2,
    }

    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["model_tiers"] == payload["model_tiers"]
    assert data["default_model_tier"] == 2

    async with db_base.async_session_maker() as session:
        row = await session.get(
            ManagedAgentProviderProfile,
            "explicit_model_tier_profile",
        )
        assert row is not None
        assert row.model_tiers == payload["model_tiers"]
        assert row.default_model_tier == 2


@pytest.mark.asyncio
async def test_mm1169_create_profile_rejects_empty_and_secret_like_tiers(
    client_app: AsyncClient, _module_db
) -> None:
    base_payload = {
        "profile_id": "invalid_model_tier_profile",
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "credential_source": "none",
        "runtime_materialization_mode": "composite",
    }

    async with client_app as client:
        empty_response = await client.post(
            "/api/v1/provider-profiles",
            json={**base_payload, "model_tiers": []},
        )
        secret_response = await client.post(
            "/api/v1/provider-profiles",
            json={
                **base_payload,
                "profile_id": "secret_model_tier_profile",
                "model_tiers": [
                    {
                        "label": "Unsafe",
                        "model": "opaque-model",
                        "effort": "opaque-effort",
                        "parameters": {"api_key": "raw"},
                        "annotations": {},
                    }
                ],
            },
        )

    assert empty_response.status_code == 422
    assert "model_tiers" in empty_response.text
    assert secret_response.status_code == 422
    assert "credential-like" in secret_response.text


@pytest.mark.asyncio
async def test_mm1169_create_profile_accepts_safe_token_parameter_names(
    client_app: AsyncClient, _module_db
) -> None:
    payload = {
        "profile_id": "safe_token_parameter_profile",
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "authentication_method": "api_key",
        "preset_version": CODEX_OPENAI_API_KEY_PRESET_VERSION,
        "model_tiers": [
            {
                "label": "Safe metadata",
                "model": "provider-model",
                "effort": "medium",
                "parameters": {
                    "max_tokens": 4096,
                    "prompt_tokens": 128,
                    "completion_tokens": 512,
                    "tokens_per_minute": 10000,
                    "refresh_interval": 60,
                    "auto_refresh": False,
                },
                "annotations": {"session_timeout": 300},
            }
        ],
    }

    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)

    assert response.status_code == 201
    assert response.json()["model_tiers"][0]["parameters"]["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_mm1169_reordering_model_tiers_persists_policy_order(
    client_app: AsyncClient, _module_db
) -> None:
    profile_id = "reordered_model_tier_profile"
    payload = {
        "profile_id": profile_id,
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "authentication_method": "api_key",
        "preset_version": CODEX_OPENAI_API_KEY_PRESET_VERSION,
        "model_tiers": [
            {"label": "Tier A", "model": "model-a", "effort": "low"},
            {"label": "Tier B", "model": "model-b", "effort": "high"},
        ],
        "default_model_tier": 1,
    }
    reordered = [
        {"label": "Tier B", "model": "model-b", "effort": "high"},
        {"label": "Tier A", "model": "model-a", "effort": "low"},
    ]

    async with client_app as client:
        create_response = await client.post("/api/v1/provider-profiles", json=payload)
        update_response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={"model_tiers": reordered, "default_model_tier": 2},
        )

    assert create_response.status_code == 201
    assert update_response.status_code == 200
    data = update_response.json()
    assert data["model_tiers"] == [
        {
            "label": "Tier B",
            "model": "model-b",
            "effort": "high",
            "parameters": {},
            "annotations": {},
        },
        {
            "label": "Tier A",
            "model": "model-a",
            "effort": "low",
            "parameters": {},
            "annotations": {},
        },
    ]
    assert data["default_model_tier"] == 2


@pytest.mark.asyncio
async def test_mm1169_update_legacy_default_refreshes_single_default_tier(
    client_app: AsyncClient, _module_db
) -> None:
    profile_id = "legacy_patch_refreshes_tier"
    payload = {
        "profile_id": profile_id,
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "authentication_method": "api_key",
        "preset_version": CODEX_OPENAI_API_KEY_PRESET_VERSION,
        "default_model": "old-model",
        "default_effort": "low",
    }

    async with client_app as client:
        create_response = await client.post("/api/v1/provider-profiles", json=payload)
        update_response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={"default_model": "new-model", "default_effort": "high"},
        )

    assert create_response.status_code == 201
    assert update_response.status_code == 200
    data = update_response.json()
    assert data["default_model"] == "new-model"
    assert data["default_effort"] == "high"
    assert data["model_tiers"] == [
        {
            "label": "Legacy default",
            "model": "new-model",
            "effort": "high",
            "parameters": {},
            "annotations": {},
        }
    ]
    assert data["default_model_tier"] == 1


@pytest.mark.asyncio
async def test_mm1169_update_legacy_default_preserves_explicit_tiers(
    client_app: AsyncClient, _module_db
) -> None:
    profile_id = "legacy_patch_preserves_explicit_tiers"
    payload = {
        "profile_id": profile_id,
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "authentication_method": "api_key",
        "preset_version": CODEX_OPENAI_API_KEY_PRESET_VERSION,
        "default_model": "old-model",
        "model_tiers": [
            {"label": "Tier A", "model": "tier-model", "effort": "medium"}
        ],
    }

    async with client_app as client:
        create_response = await client.post("/api/v1/provider-profiles", json=payload)
        update_response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={"default_model": "new-model"},
        )

    assert create_response.status_code == 201
    assert update_response.status_code == 200
    assert update_response.json()["model_tiers"] == [
        {
            "label": "Tier A",
            "model": "tier-model",
            "effort": "medium",
            "parameters": {},
            "annotations": {},
        }
    ]


@pytest.mark.asyncio
async def test_migrated_runtime_default_tier_refreshes_on_legacy_default_update(
    client_app: AsyncClient, _module_db
) -> None:
    """MoonLadderStudios/MoonMind#3793: migrated tiers stay legacy-default aware."""

    profile_id = "migrated_runtime_default_tier"
    payload = {
        "profile_id": profile_id,
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "authentication_method": "api_key",
        "preset_version": CODEX_OPENAI_API_KEY_PRESET_VERSION,
        "model_tiers": [
            {
                "label": "Runtime default",
                "model": None,
                "effort": None,
                "parameters": {},
                "annotations": {"migratedFrom": "runtime_default"},
            }
        ],
    }

    async with client_app as client:
        create_response = await client.post("/api/v1/provider-profiles", json=payload)
        update_response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={"default_model": "new-model", "default_effort": "high"},
        )

    assert create_response.status_code == 201
    assert create_response.json()["model_tiers"] == payload["model_tiers"]
    assert update_response.status_code == 200
    data = update_response.json()
    assert data["model_tiers"] == [
        {
            "label": "Legacy default",
            "model": "new-model",
            "effort": "high",
            "parameters": {},
            "annotations": {},
        }
    ]
    assert data["default_model_tier"] == 1


@pytest.mark.asyncio
async def test_migrated_legacy_default_tier_refreshes_on_legacy_default_update(
    client_app: AsyncClient, _module_db
) -> None:
    """MoonLadderStudios/MoonMind#3793: migrated tiers stay legacy-default aware."""

    profile_id = "migrated_legacy_default_tier"
    payload = {
        "profile_id": profile_id,
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "authentication_method": "api_key",
        "preset_version": CODEX_OPENAI_API_KEY_PRESET_VERSION,
        "default_model": "old-model",
        "default_effort": "low",
        "model_tiers": [
            {
                "label": "Legacy default",
                "model": "old-model",
                "effort": "low",
                "parameters": {},
                "annotations": {"migratedFrom": "default_model_default_effort"},
            }
        ],
    }

    async with client_app as client:
        create_response = await client.post("/api/v1/provider-profiles", json=payload)
        update_response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={"default_model": "new-model"},
        )

    assert create_response.status_code == 201
    assert update_response.status_code == 200
    data = update_response.json()
    assert data["default_model"] == "new-model"
    assert data["default_effort"] == "low"
    assert data["model_tiers"] == [
        {
            "label": "Legacy default",
            "model": "new-model",
            "effort": "low",
            "parameters": {},
            "annotations": {},
        }
    ]
    assert data["default_model_tier"] == 1


@pytest.mark.asyncio
async def test_operator_annotated_tier_survives_legacy_default_update(
    client_app: AsyncClient, _module_db
) -> None:
    """MoonLadderStudios/MoonMind#3793: only migration provenance is refreshable."""

    profile_id = "operator_annotated_tier"
    payload = {
        "profile_id": profile_id,
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "authentication_method": "api_key",
        "preset_version": CODEX_OPENAI_API_KEY_PRESET_VERSION,
        "default_model": "old-model",
        "model_tiers": [
            {
                "label": "Legacy default",
                "model": "old-model",
                "effort": None,
                "parameters": {},
                "annotations": {"owner": "platform"},
            }
        ],
    }

    async with client_app as client:
        create_response = await client.post("/api/v1/provider-profiles", json=payload)
        update_response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={"default_model": "new-model"},
        )

    assert create_response.status_code == 201
    assert update_response.status_code == 200
    assert update_response.json()["model_tiers"] == payload["model_tiers"]


@pytest.mark.asyncio
async def test_mm1169_orm_insert_uses_legacy_defaults_for_model_tiers(
    _module_db,
) -> None:
    profile_id = "orm_insert_legacy_default_tier"
    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id="codex_cli",
                provider_id="openai",
                default_model="seeded-model",
                default_effort="medium",
            )
        )
        await session.commit()

    async with db_base.async_session_maker() as session:
        row = await session.get(ManagedAgentProviderProfile, profile_id)

    assert row is not None
    assert row.model_tiers == [
        {
            "label": "Legacy default",
            "model": "seeded-model",
            "effort": "medium",
            "parameters": {},
            "annotations": {},
        }
    ]


@pytest.mark.asyncio
async def test_create_enabled_provider_profile_clears_default_disabled_reason(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _advertise_expert_manual_contracts(
        monkeypatch,
        (
            "enabled_profile_clear_runtime",
            "unknown",
            "secret_ref",
            "api_key_env",
        ),
    )
    payload = {
        "profile_id": "enabled_profile_clears_disabled_reason",
        "runtime_id": "enabled_profile_clear_runtime",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://enabled_profile_secret"},
        # #3821: unknown strategy needs explicit policy for legacy_custom.
        "clear_env_keys": ["CUSTOM_LEGACY_KEY"],
        "enabled": True,
        "auth_state": "connected",
        "last_auth_method": "secret_ref",
    }

    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["enabled"] is True
    assert data["auth_state"] == "connected"
    assert data["disabled_reason"] is None


@pytest.mark.asyncio
async def test_create_provider_profile_rejects_unadvertised_manual_contract(
    client_app: AsyncClient, _module_db
) -> None:
    payload = {
        "profile_id": "unconfigured_custom_profile",
        "runtime_id": "custom_runtime",
        "provider_id": "custom",
        "credential_source": "none",
        "runtime_materialization_mode": "composite",
    }

    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)
        persisted = await client.get(
            "/api/v1/provider-profiles/unconfigured_custom_profile"
        )

    assert response.status_code == 422
    assert "supported authentication preset" in response.json()["detail"]
    assert persisted.status_code == 404

@pytest.mark.asyncio
async def test_create_second_profile_can_become_runtime_default(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
):
    """Creating a second profile with is_default should move the runtime default."""
    _advertise_expert_manual_contracts(
        monkeypatch,
        ("create_runtime_default", "unknown", "secret_ref", "api_key_env"),
    )
    first_payload = {
        "profile_id": "runtime_default_first",
        "runtime_id": "create_runtime_default",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://first_secret"},
        "clear_env_keys": ["CUSTOM_LEGACY_KEY"],
        "enabled": True,
        "auth_state": "connected",
        "disabled_reason": None,
        "last_auth_method": "secret_ref",
    }
    second_payload = {
        "profile_id": "runtime_default_second",
        "runtime_id": "create_runtime_default",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://second_secret"},
        "clear_env_keys": ["CUSTOM_LEGACY_KEY"],
        "enabled": True,
        "auth_state": "connected",
        "disabled_reason": None,
        "last_auth_method": "secret_ref",
        "is_default": True,
    }

    async with client_app as client:
        first_response = await client.post("/api/v1/provider-profiles", json=first_payload)
        second_response = await client.post("/api/v1/provider-profiles", json=second_payload)
        listed = await client.get(
            "/api/v1/provider-profiles",
            params={"runtime_id": "create_runtime_default"},
        )

    assert first_response.status_code == 201
    assert first_response.json()["is_default"] is True
    assert second_response.status_code == 201
    assert second_response.json()["is_default"] is True
    assert listed.status_code == 200

    profiles = {profile["profile_id"]: profile for profile in listed.json()}
    assert profiles["runtime_default_first"]["is_default"] is False
    assert profiles["runtime_default_second"]["is_default"] is True

@pytest.mark.asyncio
async def test_update_profile_can_become_runtime_default(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
):
    _advertise_expert_manual_contracts(
        monkeypatch,
        ("patch_runtime_default", "unknown", "secret_ref", "api_key_env"),
    )
    first_payload = {
        "profile_id": "patch_runtime_default_first",
        "runtime_id": "patch_runtime_default",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://patch_first_secret"},
        "clear_env_keys": ["CUSTOM_LEGACY_KEY"],
        "enabled": True,
        "auth_state": "connected",
        "disabled_reason": None,
        "last_auth_method": "secret_ref",
        "is_default": True,
    }
    second_payload = {
        "profile_id": "patch_runtime_default_second",
        "runtime_id": "patch_runtime_default",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://patch_second_secret"},
        "clear_env_keys": ["CUSTOM_LEGACY_KEY"],
        "enabled": True,
        "auth_state": "connected",
        "disabled_reason": None,
        "last_auth_method": "secret_ref",
    }

    async with client_app as client:
        first_response = await client.post("/api/v1/provider-profiles", json=first_payload)
        second_response = await client.post("/api/v1/provider-profiles", json=second_payload)
        update_response = await client.patch(
            "/api/v1/provider-profiles/patch_runtime_default_second",
            json={"is_default": True},
        )
        listed = await client.get(
            "/api/v1/provider-profiles",
            params={"runtime_id": "patch_runtime_default"},
        )

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert update_response.status_code == 200
    assert update_response.json()["is_default"] is True
    assert listed.status_code == 200

    profiles = {profile["profile_id"]: profile for profile in listed.json()}
    assert profiles["patch_runtime_default_first"]["is_default"] is False
    assert profiles["patch_runtime_default_second"]["is_default"] is True


@pytest.mark.asyncio
async def test_update_profile_rejects_enabled_without_connected_auth_state(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _advertise_expert_manual_contracts(
        monkeypatch,
        ("patch_enabled_requires_connected", "unknown", "none", "composite"),
    )
    payload = {
        "profile_id": "patch_enabled_requires_connected_auth",
        "runtime_id": "patch_enabled_requires_connected",
        "credential_source": "none",
        "runtime_materialization_mode": "composite",
    }

    async with client_app as client:
        create_response = await client.post("/api/v1/provider-profiles", json=payload)
        update_response = await client.patch(
            "/api/v1/provider-profiles/patch_enabled_requires_connected_auth",
            json={"enabled": True},
        )

    assert create_response.status_code == 201
    assert update_response.status_code == 422
    assert update_response.json()["detail"] == (
        "Enabled profiles require auth_state=connected"
    )


@pytest.mark.asyncio
async def test_update_profile_clears_disabled_reason_when_enabled(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _advertise_expert_manual_contracts(
        monkeypatch,
        ("patch_enabled_clears", "unknown", "secret_ref", "api_key_env"),
    )
    payload = {
        "profile_id": "patch_enabled_clears_disabled_reason",
        "runtime_id": "patch_enabled_clears",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "env://patch_enabled_secret"},
        # #3821: unknown strategy needs explicit policy for later enable.
        "clear_env_keys": ["CUSTOM_LEGACY_KEY"],
        "auth_state": "connected",
        "disabled_reason": "missing_credentials",
    }

    async with client_app as client:
        create_response = await client.post("/api/v1/provider-profiles", json=payload)
        update_response = await client.patch(
            "/api/v1/provider-profiles/patch_enabled_clears_disabled_reason",
            json={"enabled": True},
        )

    assert create_response.status_code == 201
    assert update_response.status_code == 200
    data = update_response.json()
    assert data["enabled"] is True
    assert data["auth_state"] == "connected"
    assert data["disabled_reason"] is None


@pytest.mark.asyncio
async def test_update_profile_enabled_accepts_active_database_secret_ref(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = uuid4().hex
    profile_id = f"patch_enabled_db_secret_{suffix}"
    secret_slug = f"patch-enabled-db-secret-{suffix}"
    _advertise_expert_manual_contracts(
        monkeypatch,
        ("patch_enabled_db_secret", "unknown", "secret_ref", "api_key_env"),
    )
    async with db_base.async_session_maker() as session:
        session.add(
            ManagedSecret(
                slug=secret_slug,
                ciphertext="encrypted-test-value",
                status=SecretStatus.ACTIVE,
                details={},
            )
        )
        await session.commit()

    payload = {
        "profile_id": profile_id,
        "runtime_id": "patch_enabled_db_secret",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"OPENAI_API_KEY": f"db://{secret_slug}"},
        # #3821: unknown strategy needs explicit policy for later enable.
        "clear_env_keys": ["CUSTOM_LEGACY_KEY"],
        "auth_state": "connected",
        "disabled_reason": "missing_credentials",
    }

    async with client_app as client:
        create_response = await client.post("/api/v1/provider-profiles", json=payload)
        update_response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={"enabled": True},
        )

    assert create_response.status_code == 201
    assert update_response.status_code == 200
    data = update_response.json()
    assert data["enabled"] is True
    checks = {check["id"]: check for check in data["readiness"]["checks"]}
    assert checks["secret_refs"]["status"] == "pass"


@pytest.mark.asyncio
async def test_create_enabled_profile_rejects_missing_database_secret_ref(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _advertise_expert_manual_contracts(
        monkeypatch,
        (
            "create_enabled_missing_db_secret",
            "unknown",
            "secret_ref",
            "api_key_env",
        ),
    )
    payload = {
        "profile_id": f"create_enabled_missing_db_secret_{uuid4().hex}",
        "runtime_id": "create_enabled_missing_db_secret",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"OPENAI_API_KEY": "db://missing-provider-secret"},
        "enabled": True,
        "auth_state": "connected",
        "disabled_reason": None,
        "last_auth_method": "secret_ref",
    }

    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)

    assert response.status_code == 422
    assert (
        "OPENAI_API_KEY=[REDACTED] binding references managed secret"
        in response.text
    )
    assert "secret db://missing-provider-secret was not found" in response.text


@pytest.mark.asyncio
async def test_update_claude_anthropic_can_replace_minimax_runtime_default(
    client_app: AsyncClient,
    _module_db,
    monkeypatch,
) -> None:
    """Regression for switching the Claude Code runtime default on PostgreSQL."""

    async def _fake_sync_provider_profile_manager(
        *,
        session,
        runtime_id: str,
    ) -> None:
        assert runtime_id == "claude_code"

    monkeypatch.setattr(
        provider_profiles_router,
        "sync_provider_profile_manager",
        _fake_sync_provider_profile_manager,
    )

    async with db_base.async_session_maker() as session:
        session.add_all(
            [
                ManagedAgentProviderProfile(
                    profile_id="claude_minimax",
                    runtime_id="claude_code",
                    provider_id="minimax",
                    provider_label="MiniMax",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    secret_refs={"ANTHROPIC_AUTH_TOKEN": "env://MINIMAX_API_KEY"},
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    disabled_reason=None,
                    last_auth_method=ProviderProfileAuthMethod.SECRET_REF,
                    is_default=True,
                    priority=200,
                ),
                ManagedAgentProviderProfile(
                    profile_id="claude_anthropic",
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    provider_label="Anthropic",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="claude_auth_volume",
                    volume_mount_path="/home/app/.claude",
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    disabled_reason=None,
                    last_auth_method=ProviderProfileAuthMethod.OAUTH_VOLUME,
                    is_default=False,
                    priority=100,
                ),
            ]
        )
        await session.commit()

    async with client_app as client:
        update_response = await client.patch(
            "/api/v1/provider-profiles/claude_anthropic",
            json={"is_default": True},
        )
        listed = await client.get(
            "/api/v1/provider-profiles",
            params={"runtime_id": "claude_code"},
        )

    assert update_response.status_code == 200
    assert update_response.json()["is_default"] is True
    assert listed.status_code == 200
    profiles = {profile["profile_id"]: profile for profile in listed.json()}
    assert profiles["claude_anthropic"]["is_default"] is True
    assert profiles["claude_minimax"]["is_default"] is False
    assert sum(1 for profile in listed.json() if profile["is_default"]) == 1


@pytest.mark.asyncio
async def test_create_provider_profile_invalid_secret_refs(client_app: AsyncClient, _module_db):
    """Test that creating a profile with raw secrets fails."""
    payload = {
        "profile_id": "invalid_profile",
        "runtime_id": "claude_v1",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"API_KEY": "raw_secret_value"}, # not a valid ref
        "max_parallel_runs": 1,
    }
    
    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)
    
    assert response.status_code == 422
    assert "Invalid secret reference" in response.text

@pytest.mark.asyncio
async def test_provider_profile_response_includes_readiness_blockers(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = "oauth_missing_metadata_readiness"

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref=None,
                    volume_mount_path=None,
                    enabled=False,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    readiness = response.json()["readiness"]
    assert readiness["status"] == "blocked"
    assert readiness["launch_ready"] is False
    checks = {check["id"]: check for check in readiness["checks"]}
    assert checks["enabled"]["status"] == "error"
    assert checks["oauth_volume"]["status"] == "error"
    assert "volume_ref" in checks["oauth_volume"]["message"]
    assert "volume_mount_path" in checks["oauth_volume"]["message"]


@pytest.mark.asyncio
async def test_provider_profile_readiness_reports_managed_secret_status(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = "missing_db_secret_readiness"

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    secret_refs={"provider_api_key": "db://does-not-exist"},
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["launch_ready"] is False
    readiness = payload["readiness"]
    assert readiness["status"] == "blocked"
    checks = {check["id"]: check for check in readiness["checks"]}
    assert checks["secret_refs"]["status"] == "error"
    assert "provider_api_key" in checks["secret_refs"]["message"]
    assert "does-not-exist" in checks["secret_refs"]["message"]


@pytest.mark.asyncio
async def test_provider_profile_readiness_reports_invalid_stored_secret_ref(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = "invalid_stored_secret_ref_readiness"

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    secret_refs={"provider_api_key": "not-a-secret-ref"},
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    readiness = response.json()["readiness"]
    assert readiness["status"] == "blocked"
    checks = {check["id"]: check for check in readiness["checks"]}
    assert checks["secret_refs"]["status"] == "error"
    assert "provider_api_key" in checks["secret_refs"]["message"]
    assert "SecretRef (secret reference must use <backend>://<locator> format)" in checks[
        "secret_refs"
    ]["message"]


@pytest.mark.asyncio
async def test_provider_profile_readiness_redacts_provider_failure_text(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = "provider_failure_readiness_redaction"
    raw_token = "sk-ant-secret-readiness-token"

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    secret_refs={"anthropic_api_key": "env://ANTHROPIC_API_KEY"},
                    command_behavior={
                        "auth_readiness": {
                            "launch_ready": False,
                            "failure_reason": f"token={raw_token} expired",
                        }
                    },
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    response_text = response.text
    assert raw_token not in response_text
    readiness = response.json()["readiness"]
    checks = {check["id"]: check for check in readiness["checks"]}
    assert checks["provider_validation"]["status"] == "error"
    assert "[REDACTED]" in checks["provider_validation"]["message"]

@pytest.mark.asyncio
async def test_create_duplicate_profile(client_app: AsyncClient, _module_db):
    """Test creating a profile that already exists returns 409."""
    sample_profile = await get_or_create_sample_profile()
    payload = {
        "profile_id": sample_profile.profile_id,
        "runtime_id": "duplicate_runtime",
        "credential_source": "oauth_volume",
        "runtime_materialization_mode": "oauth_home",
    }
    async with client_app as client:
        response = await client.post("/api/v1/provider-profiles", json=payload)
    assert response.status_code == 409

@pytest.mark.asyncio
async def test_list_profiles(client_app: AsyncClient, _module_db):
    """Test retrieving lists of profiles."""
    sample_profile = await get_or_create_sample_profile()
    async with client_app as client:
        response = await client.get("/api/v1/provider-profiles")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert any(p["profile_id"] == sample_profile.profile_id for p in data)

@pytest.mark.asyncio
async def test_get_single_profile(client_app: AsyncClient, _module_db):
    """Test retrieving a single profile by ID."""
    sample_profile = await get_or_create_sample_profile()
    async with client_app as client:
        response = await client.get(f"/api/v1/provider-profiles/{sample_profile.profile_id}")
    assert response.status_code == 200
    assert response.json()["runtime_id"] == "custom_runtime"

@pytest.mark.asyncio
async def test_get_unknown_profile(client_app: AsyncClient, _module_db):
    """Test 404 on missing profile."""
    async with client_app as client:
        response = await client.get("/api/v1/provider-profiles/does_not_exist_xyz")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_update_profile(client_app: AsyncClient, _module_db):
    """Test patching an existing profile."""
    sample_profile = await get_or_create_sample_profile()
    payload = {
        "max_parallel_runs": 10,
        "enabled": False
    }
    async with client_app as client:
        response = await client.patch(f"/api/v1/provider-profiles/{sample_profile.profile_id}", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["max_parallel_runs"] == 10
    assert data["enabled"] is False


@pytest.mark.asyncio
async def test_update_provider_profile_rejects_overlong_default_effort(
    client_app: AsyncClient, _module_db
) -> None:
    sample_profile = await get_or_create_sample_profile()

    async with client_app as client:
        response = await client.patch(
            f"/api/v1/provider-profiles/{sample_profile.profile_id}",
            json={"default_effort": "x" * 65},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_delete_profile(client_app: AsyncClient, _module_db):
    """Test deleting a profile."""
    sample_profile = await get_or_create_sample_profile()
    async with client_app as client:
        response = await client.delete(f"/api/v1/provider-profiles/{sample_profile.profile_id}")
        assert response.status_code == 204
        
        # Verify it is gone
        check = await client.get(f"/api/v1/provider-profiles/{sample_profile.profile_id}")
        assert check.status_code == 404

@pytest.mark.asyncio
async def test_update_profile_syncs_provider_profile_manager(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
):
    sample_profile = await get_or_create_sample_profile()
    signals: list[tuple[str, dict]] = []
    started: list[dict] = []

    class _FakeHandle:
        async def signal(self, signal_name: str, payload: dict) -> None:
            signals.append((signal_name, payload))

    class _FakeTemporalClient:
        async def start_workflow(self, *args, **kwargs):
            started.append({"args": args, "kwargs": kwargs})

        def get_workflow_handle(self, workflow_id: str):
            assert workflow_id == f"provider-profile-manager:{sample_profile.runtime_id}"
            return _FakeHandle()

    class _FakeTemporalAdapter:
        async def get_client(self):
            return _FakeTemporalClient()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        _FakeTemporalAdapter,
    )

    payload = {
        "enabled": False,
    }
    async with client_app as client:
        response = await client.patch(
            f"/api/v1/provider-profiles/{sample_profile.profile_id}",
            json=payload,
        )
    assert response.status_code == 200
    assert started, "Expected manager ensure/start attempt before sync"
    assert signals, "Expected sync_profiles signal after update"
    signal_name, signal_payload = signals[-1]
    assert signal_name == "sync_profiles"
    assert signal_payload["profiles"] == []

@pytest.mark.asyncio
async def test_claude_manual_auth_commit_stores_secret_ref_only(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "claude-anthropic-manual-auth"
    submitted_token = "sk-ant-test-route-token"
    validated_tokens: list[str] = []
    synced_runtimes: list[str] = []

    async def _fake_validate(token: str) -> None:
        validated_tokens.append(token)

    async def _fake_sync(*, session: AsyncSession, runtime_id: str) -> None:
        synced_runtimes.append(runtime_id)

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_claude_manual_token",
        _fake_validate,
    )
    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.sync_provider_profile_manager",
        _fake_sync,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    provider_label="Anthropic",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="claude_auth_volume",
                    volume_mount_path="/home/app/.claude",
                    secret_refs={"custom_tool": "env://CUSTOM_TOOL_SECRET"},
                    clear_env_keys=["OPENAI_API_KEY", "CUSTOM_ENV"],
                    env_template={
                        "CUSTOM_ENV": {"from_secret_ref": "custom_tool"},
                    },
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/manual-auth/commit",
            json={"token": submitted_token},
        )
        profile_response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    response_text = response.text
    assert submitted_token not in response_text
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["status_label"] == "Anthropic API key ready"
    assert payload["readiness"]["connected"] is True
    assert payload["readiness"]["backing_secret_exists"] is True
    assert payload["readiness"]["launch_ready"] is True
    expected_secret_slug = provider_profiles_router._claude_manual_secret_slug(
        profile_id
    )
    expected_secret_ref = f"db://{expected_secret_slug}"
    assert payload["secret_ref"] == expected_secret_ref

    assert profile_response.status_code == 200
    profile_payload = profile_response.json()
    assert submitted_token not in profile_response.text
    assert profile_payload["credential_source"] == "secret_ref"
    assert profile_payload["runtime_materialization_mode"] == "api_key_env"
    assert profile_payload["volume_ref"] == "claude_auth_volume"
    assert profile_payload["volume_mount_path"] == "/home/app/.claude"
    assert profile_payload["secret_refs"] == {
        "custom_tool": "env://CUSTOM_TOOL_SECRET",
        "anthropic_api_key": expected_secret_ref,
    }
    assert profile_payload["env_template"] == {
        "CUSTOM_ENV": {"from_secret_ref": "custom_tool"},
        "ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"},
    }
    assert "ANTHROPIC_AUTH_TOKEN" in profile_payload["clear_env_keys"]
    assert "ANTHROPIC_BASE_URL" in profile_payload["clear_env_keys"]
    assert "CLAUDE_API_KEY" in profile_payload["clear_env_keys"]
    assert "OPENAI_API_KEY" in profile_payload["clear_env_keys"]
    assert "CUSTOM_ENV" in profile_payload["clear_env_keys"]
    assert profile_payload["clear_env_keys"].count("OPENAI_API_KEY") == 1
    assert profile_payload["command_behavior"]["auth_strategy"] == "claude_credential_methods"
    assert profile_payload["command_behavior"]["auth_state"] == "connected"
    assert profile_payload["command_behavior"]["auth_actions"] == [
        "connect_oauth",
        "use_api_key",
        "validate_oauth",
        "disconnect_oauth",
    ]
    assert profile_payload["command_behavior"]["auth_status_label"] == "Anthropic API key ready"

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedSecret).where(
                ManagedSecret.slug == expected_secret_slug
            )
        )
        secret = result.scalar_one()

    assert secret.ciphertext == submitted_token
    assert validated_tokens == [submitted_token]
    assert synced_runtimes == ["claude_code"]

def test_claude_manual_auth_secret_slug_is_collision_resistant() -> None:
    first = provider_profiles_router._claude_manual_secret_slug("claude.anthropic")
    second = provider_profiles_router._claude_manual_secret_slug("claude_anthropic")

    assert first != second
    assert first.startswith("claude-anthropic-")
    assert second.startswith("claude-anthropic-")
    assert first.endswith("-token")
    assert second.endswith("-token")

@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "profile_id",
        "runtime_id",
        "provider_id",
        "api_key",
        "secret_role",
        "env_key",
        "clear_env_key",
        "status_label",
    ),
    [
        (
            "mm-875-anthropic-api-key",
            "claude_code",
            "anthropic",
            "sk-ant-mm875-route-token",
            "anthropic_api_key",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "Anthropic API key ready",
        ),
        (
            "mm-875-openai-api-key",
            "codex_cli",
            "openai",
            "sk-mm875-openai-route-token",
            "openai_api_key",
            "OPENAI_API_KEY",
            "MINIMAX_API_KEY",
            "OpenAI API key ready",
        ),
    ],
)
async def test_provider_api_key_setup_stores_secret_ref_mappings_only(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
    profile_id: str,
    runtime_id: str,
    provider_id: str,
    api_key: str,
    secret_role: str,
    env_key: str,
    clear_env_key: str,
    status_label: str,
) -> None:
    validated: list[tuple[str, str]] = []
    synced_runtimes: list[str] = []

    async def _fake_validate(provider: str, key: str) -> None:
        validated.append((provider, key))

    async def _fake_sync(*, session: AsyncSession, runtime_id: str) -> None:
        synced_runtimes.append(runtime_id)

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_provider_api_key",
        _fake_validate,
    )
    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.sync_provider_profile_manager",
        _fake_sync,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id=runtime_id,
                    provider_id=provider_id,
                    provider_label=provider_id.title(),
                    credential_source=ProviderCredentialSource.NONE,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    secret_refs={"custom_tool": "env://CUSTOM_TOOL_SECRET"},
                    clear_env_keys=["CUSTOM_ENV"],
                    env_template={
                        "CUSTOM_ENV": {"from_secret_ref": "custom_tool"},
                    },
                    enabled=False,
                    is_default=False,
                )
            )
            await session.flush()
            existing = await session.get(ManagedAgentProviderProfile, profile_id)
        assert existing is not None
        existing.home_path_overrides = {
            "CLAUDE_HOME": "/oauth/claude",
            "CODEX_HOME": "/oauth/codex",
            "CUSTOM_HOME": "/custom/home",
        }
        await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/credentials/api-key",
            json={
                "api_key": api_key,
                "account_label": "MM-875 route test",
                "make_default": True,
                "enable_after_validation": True,
            },
        )
        profile_response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    assert api_key not in response.text
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["status_label"] == status_label
    assert payload["readiness"]["connected"] is True
    assert payload["readiness"]["launch_ready"] is True
    expected_secret_slug = provider_profiles_router._provider_api_key_secret_slug(
        profile_id,
        secret_role,
    )
    expected_secret_ref = f"db://{expected_secret_slug}"
    assert payload["secret_ref"] == expected_secret_ref

    assert profile_response.status_code == 200
    profile_payload = profile_response.json()
    assert api_key not in profile_response.text
    assert profile_payload["credential_source"] == "secret_ref"
    assert profile_payload["runtime_materialization_mode"] == "api_key_env"
    assert profile_payload["secret_refs"] == {
        "custom_tool": "env://CUSTOM_TOOL_SECRET",
        secret_role: expected_secret_ref,
    }
    assert profile_payload["env_template"] == {
        "CUSTOM_ENV": {"from_secret_ref": "custom_tool"},
        env_key: {"from_secret_ref": secret_role},
    }
    expected_home_path_overrides = {
        "CLAUDE_HOME": "/oauth/claude",
        "CODEX_HOME": "/oauth/codex",
        "CUSTOM_HOME": "/custom/home",
    }
    if runtime_id == "claude_code":
        expected_home_path_overrides.pop("CLAUDE_HOME")
    elif runtime_id == "codex_cli":
        expected_home_path_overrides.pop("CODEX_HOME")
    assert profile_payload["home_path_overrides"] == expected_home_path_overrides
    assert clear_env_key in profile_payload["clear_env_keys"]
    assert profile_payload["account_label"] == "MM-875 route test"
    assert profile_payload["enabled"] is True
    assert profile_payload["is_default"] is True
    assert profile_payload["auth_state"] == "connected"
    assert profile_payload["disabled_reason"] is None
    assert profile_payload["first_authenticated_at"] is not None
    assert profile_payload["last_validated_at"] is not None
    assert profile_payload["last_auth_method"] == "secret_ref"
    assert profile_payload["command_behavior"]["auth_strategy"] == "api_key_env"
    assert profile_payload["command_behavior"]["auth_status_label"] == status_label

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedSecret).where(ManagedSecret.slug == expected_secret_slug)
        )
        secret = result.scalar_one()

    assert secret.ciphertext == api_key
    assert validated == [(provider_id, api_key)]
    assert synced_runtimes == [runtime_id]


@pytest.mark.asyncio
async def test_zen_api_key_setup_is_rejected_without_mutating_profile(
    client_app: AsyncClient,
    _module_db,
) -> None:
    profile_id = "opencode-zen-reject-api-key"
    raw_key = "candidate-opencode-zen-key"
    owner = _override_current_user()
    command_behavior = {
        "auth_strategy": "none",
        "auth_state": "connected",
        "auth_actions": [],
        "auth_readiness": {
            "connected": True,
            "backing_secret_exists": False,
            "launch_ready": True,
        },
    }
    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id="opencode",
                provider_id="opencode",
                provider_label="OpenCode Zen",
                owner_user_id=owner.id,
                credential_source=ProviderCredentialSource.NONE,
                runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
                secret_refs={},
                command_behavior=command_behavior,
                enabled=True,
                auth_state=ProviderProfileAuthState.CONNECTED,
            )
        )
        await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/credentials/api-key",
            json={"api_key": raw_key},
        )

    assert response.status_code == 422
    assert raw_key not in response.text
    assert "OpenCode Go profiles" in response.text
    async with db_base.async_session_maker() as session:
        persisted = await session.get(ManagedAgentProviderProfile, profile_id)
    assert persisted is not None
    assert persisted.enabled is True
    assert persisted.auth_state is ProviderProfileAuthState.CONNECTED
    assert persisted.disabled_reason is None
    assert persisted.credential_source is ProviderCredentialSource.NONE
    assert persisted.secret_refs == {}
    assert persisted.command_behavior == command_behavior

@pytest.mark.asyncio
async def test_provider_api_key_setup_failed_validation_updates_state_without_secret(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "mm-875-openai-invalid-api-key"
    fallback_profile_id = "mm-875-openai-invalid-api-key-fallback"
    runtime_id = "codex_cli"
    raw_key = "sk-mm875-invalid-token"
    validated: list[tuple[str, str]] = []
    synced_runtimes: list[str] = []

    async def _fake_validate(provider: str, key: str) -> None:
        validated.append((provider, key))
        raise provider_profiles_router.HTTPException(
            status_code=401,
            detail="API key validation failed.",
        )

    async def _fake_sync(*, session: AsyncSession, runtime_id: str) -> None:
        synced_runtimes.append(runtime_id)

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_provider_api_key",
        _fake_validate,
    )
    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.sync_provider_profile_manager",
        _fake_sync,
    )

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedAgentProviderProfile).where(
                ManagedAgentProviderProfile.runtime_id == runtime_id
            )
        )
        for row in result.scalars():
            row.is_default = False
        await session.flush()
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id=runtime_id,
                    provider_id="openai",
                    provider_label="OpenAI",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    enabled=True,
                    is_default=True,
                    priority=10_100,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                )
            )
        fallback = await session.get(ManagedAgentProviderProfile, fallback_profile_id)
        if fallback is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=fallback_profile_id,
                    runtime_id=runtime_id,
                    provider_id="openai",
                    provider_label="OpenAI",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    secret_refs={"openai_api_key": "env://OPENAI_API_KEY"},
                    enabled=True,
                    is_default=False,
                    priority=10_000,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/credentials/api-key",
            json={"api_key": raw_key},
        )
        profile_response = await client.get(f"/api/v1/provider-profiles/{profile_id}")
        fallback_response = await client.get(
            f"/api/v1/provider-profiles/{fallback_profile_id}"
        )

    assert response.status_code == 401
    assert raw_key not in response.text
    assert response.json()["detail"] == "API key validation failed."
    profile_payload = profile_response.json()
    assert raw_key not in profile_response.text
    assert profile_payload["enabled"] is False
    assert profile_payload["is_default"] is False
    assert profile_payload["auth_state"] == "validation_failed"
    assert profile_payload["disabled_reason"] == "auth_invalid"
    assert profile_payload["secret_refs"] == {}
    assert profile_payload["env_template"] == {}
    assert profile_payload["command_behavior"]["auth_readiness"]["failure_reason"] == (
        "API key validation failed."
    )
    assert fallback_response.json()["is_default"] is True

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedSecret))
        secrets = result.scalars().all()

    assert all(secret.ciphertext != raw_key for secret in secrets)
    assert validated == [("openai", raw_key)]
    assert synced_runtimes == [runtime_id]

@pytest.mark.asyncio
async def test_provider_api_key_setup_transient_validation_error_preserves_profile(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "mm-875-openai-transient-api-key"
    raw_key = "sk-mm875-transient-token"
    synced_runtimes: list[str] = []

    async def _fake_validate(provider: str, key: str) -> None:
        assert (provider, key) == ("openai", raw_key)
        raise provider_profiles_router.HTTPException(
            status_code=502,
            detail="Provider validation temporarily unavailable.",
        )

    async def _fake_sync(*, session: AsyncSession, runtime_id: str) -> None:
        synced_runtimes.append(runtime_id)

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_provider_api_key",
        _fake_validate,
    )
    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.sync_provider_profile_manager",
        _fake_sync,
    )

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedAgentProviderProfile).where(
                ManagedAgentProviderProfile.runtime_id == "codex_cli"
            )
        )
        for row in result.scalars():
            row.is_default = False
        await session.flush()
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="codex_cli",
                    provider_id="openai",
                    provider_label="OpenAI",
                    credential_source=ProviderCredentialSource.NONE,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    enabled=True,
                    is_default=True,
                    priority=10_200,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/credentials/api-key",
            json={"api_key": raw_key},
        )
        profile_response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 502
    assert raw_key not in response.text
    profile_payload = profile_response.json()
    assert profile_payload["enabled"] is True
    assert profile_payload["is_default"] is True
    assert profile_payload["auth_state"] == "connected"
    assert profile_payload["disabled_reason"] is None
    assert profile_payload["secret_refs"] == {}
    assert profile_payload["env_template"] == {}
    assert synced_runtimes == []


@pytest.mark.asyncio
async def test_opencode_rotation_validation_failure_preserves_previous_authority(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.omnigent.opencode_runtime_validation import (
        OpenCodeProviderRuntimeValidationService,
    )

    profile_id = "opencode-atomic-rotation"
    owner = _override_current_user()
    previous_ref = "db://opencode-existing-secret"
    previous_evidence = {
        "credentialGeneration": 4,
        "imageRef": "registry.test/opencode@sha256:" + "a" * 64,
        "models": [{"qualifiedId": "opencode-go/model"}],
    }
    candidate_key = "candidate-opencode-key"

    async def _failing_validation(self, **kwargs):
        assert kwargs["candidate_secret"] == candidate_key
        assert kwargs["candidate_generation"] == 5
        raise RuntimeError("candidate rejected")

    async def _maintenance_guard_override():
        yield SimpleNamespace(lease=SimpleNamespace(lease_id="maintenance-1"))

    monkeypatch.setenv(
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "registry.test/opencode@sha256:" + "a" * 64,
    )
    monkeypatch.setattr(
        OpenCodeProviderRuntimeValidationService,
        "validate",
        _failing_validation,
    )
    app.dependency_overrides[
        provider_profiles_router._credential_validation_guard
    ] = _maintenance_guard_override

    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id="opencode",
                provider_id="opencode-go",
                provider_label="OpenCode Go",
                owner_user_id=owner.id,
                credential_source=ProviderCredentialSource.SECRET_REF,
                runtime_materialization_mode=RuntimeMaterializationMode.CONFIG_BUNDLE,
                secret_refs={"opencode_api_key": previous_ref},
                credential_generation=4,
                model_catalog_evidence_json=previous_evidence,
                enabled=True,
                auth_state=ProviderProfileAuthState.CONNECTED,
            )
        )
        await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/credentials/api-key",
            json={"api_key": candidate_key},
        )

    assert response.status_code == 502
    assert candidate_key not in response.text
    async with db_base.async_session_maker() as session:
        persisted = await session.get(ManagedAgentProviderProfile, profile_id)
        assert persisted is not None
        assert persisted.secret_refs == {"opencode_api_key": previous_ref}
        assert persisted.credential_generation == 4
        assert persisted.model_catalog_evidence_json == previous_evidence
        assert persisted.enabled is True
        assert persisted.auth_state is ProviderProfileAuthState.CONNECTED

@pytest.mark.asyncio
async def test_provider_api_key_setup_can_validate_without_enabling(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "mm-875-openai-validate-only"
    raw_key = "sk-mm875-openai-validate-only"

    async def _fake_validate(provider: str, key: str) -> None:
        assert (provider, key) == ("openai", raw_key)

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_provider_api_key",
        _fake_validate,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="codex_cli",
                    provider_id="openai",
                    provider_label="OpenAI",
                    credential_source=ProviderCredentialSource.NONE,
                    runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                    enabled=False,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/credentials/api-key",
            json={
                "api_key": raw_key,
                "enable_after_validation": False,
                "make_default": False,
            },
        )
        profile_response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    profile_payload = profile_response.json()
    assert raw_key not in profile_response.text
    assert profile_payload["credential_source"] == "secret_ref"
    assert profile_payload["auth_state"] == "connected"
    assert profile_payload["disabled_reason"] == "user_disabled"
    assert profile_payload["enabled"] is False
    assert profile_payload["is_default"] is False

@pytest.mark.asyncio
async def test_claude_oauth_lifecycle_actions_validate_and_disconnect(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "claude-anthropic-oauth-lifecycle"
    synced_runtimes: list[str] = []

    async def _fake_verify(
        *,
        runtime_id: str,
        volume_ref: str,
        volume_mount_path: str | None,
    ) -> dict[str, object]:
        assert runtime_id == "claude_code"
        assert volume_ref == "claude_auth_volume"
        assert volume_mount_path == "/home/app/.claude"
        return {"verified": True}

    async def _fake_sync(*, session: AsyncSession, runtime_id: str) -> None:
        synced_runtimes.append(runtime_id)

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.providers.volume_verifiers.verify_volume_credentials",
        _fake_verify,
    )
    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.sync_provider_profile_manager",
        _fake_sync,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    provider_label="Anthropic",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="claude_auth_volume",
                    volume_mount_path="/home/app/.claude",
                    enabled=True,
                    command_behavior={
                        "auth_strategy": "claude_credential_methods",
                        "auth_actions": [
                            "connect_oauth",
                            "use_api_key",
                            "validate_oauth",
                            "disconnect_oauth",
                        ],
                    },
                )
            )
            await session.commit()

    async with client_app as client:
        validate_response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/oauth/validate"
        )
        disconnect_response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/oauth/disconnect"
        )
        profile_response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert validate_response.status_code == 200
    assert validate_response.json()["status"] == "ready"
    assert disconnect_response.status_code == 200
    assert disconnect_response.json()["status"] == "disconnected"
    profile_payload = profile_response.json()
    assert profile_payload["credential_source"] == "none"
    assert profile_payload["volume_ref"] is None
    assert profile_payload["volume_mount_path"] is None
    assert profile_payload["command_behavior"]["auth_actions"] == ["use_api_key"]
    assert profile_payload["command_behavior"]["auth_status_label"] == "Claude OAuth disconnected"
    assert synced_runtimes == ["claude_code", "claude_code"]

@pytest.mark.asyncio
async def test_validate_claude_manual_token_reuses_shared_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_clients: list[object] = []
    requested_tokens: list[str] = []

    class _FakeResponse:
        status_code = 200

    class _FakeClient:
        is_closed = False

        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout
            created_clients.append(self)

        async def get(self, _url: str, *, headers: dict[str, str]) -> _FakeResponse:
            requested_tokens.append(headers["x-api-key"])
            return _FakeResponse()

    monkeypatch.setattr(
        provider_profiles_router,
        "_claude_manual_validation_client",
        None,
    )
    monkeypatch.setattr(provider_profiles_router.httpx, "AsyncClient", _FakeClient)

    await provider_profiles_router.validate_claude_manual_token("sk-ant-test-one")
    await provider_profiles_router.validate_claude_manual_token("sk-ant-test-two")

    assert len(created_clients) == 1
    assert requested_tokens == ["sk-ant-test-one", "sk-ant-test-two"]

@pytest.mark.asyncio
async def test_claude_manual_auth_commit_rejects_malformed_token_without_persisting(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "claude-anthropic-bad-manual-auth"
    raw_token = "not-a-claude-token-secret"

    async def _unexpected_validate(token: str) -> None:
        raise AssertionError("malformed tokens should fail before upstream validation")

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_claude_manual_token",
        _unexpected_validate,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="claude_auth_volume",
                    volume_mount_path="/home/app/.claude",
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/manual-auth/commit",
            json={"token": raw_token},
        )

    assert response.status_code == 422
    assert raw_token not in response.text
    assert response.json()["detail"] == "Claude token validation failed."

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedSecret).where(
                ManagedSecret.slug
                == provider_profiles_router._claude_manual_secret_slug(profile_id)
            )
        )
        assert result.scalar_one_or_none() is None

@pytest.mark.asyncio
async def test_claude_manual_auth_commit_rejects_non_owner_without_validating_or_persisting(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "claude-anthropic-owned-manual-auth"
    owner_id = uuid4()
    raw_token = "sk-ant-test-non-owner-token"

    async def _unexpected_validate(token: str) -> None:
        raise AssertionError("unauthorized callers must fail before token validation")

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_claude_manual_token",
        _unexpected_validate,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    owner_user_id=owner_id,
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="claude_auth_volume",
                    volume_mount_path="/home/app/.claude",
                    enabled=True,
                )
            )
            await session.commit()

    other_user = _override_current_user(user_id=uuid4(), is_superuser=False)
    try:
        async with client_app as client:
            response = await client.post(
                f"/api/v1/provider-profiles/{profile_id}/manual-auth/commit",
                json={"token": raw_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert str(other_user.id) != str(owner_id)
    assert response.status_code == 403
    assert raw_token not in response.text
    assert response.json()["detail"] == "Not authorized to manage this provider profile."

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedSecret).where(
                ManagedSecret.slug
                == provider_profiles_router._claude_manual_secret_slug(profile_id)
            )
        )
        assert result.scalar_one_or_none() is None

@pytest.mark.asyncio
async def test_claude_manual_auth_commit_rejects_unsupported_profile_without_persisting(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "codex-unsupported-manual-auth"
    raw_token = "sk-ant-test-unsupported-profile-token"

    async def _unexpected_validate(token: str) -> None:
        raise AssertionError("unsupported profiles must fail before token validation")

    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.validate_claude_manual_token",
        _unexpected_validate,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/manual-auth/commit",
            json={"token": raw_token},
        )

    assert response.status_code == 422
    assert raw_token not in response.text
    assert response.json()["detail"] == (
        "Manual Claude auth is only supported for claude_code Anthropic profiles."
    )

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(ManagedSecret).where(
                ManagedSecret.slug
                == provider_profiles_router._claude_manual_secret_slug(profile_id)
            )
        )
        assert result.scalar_one_or_none() is None

@pytest.mark.asyncio
async def test_claude_oauth_validate_failure_redacts_secret_like_reason(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_id = "claude-anthropic-oauth-validation-redaction"
    raw_secret = "sk-ant-test-validation-secret"
    raw_path = "/home/app/.claude/credentials.json"

    async def _fake_verify(
        *,
        runtime_id: str,
        volume_ref: str,
        volume_mount_path: str | None,
    ) -> dict[str, object]:
        assert runtime_id == "claude_code"
        assert volume_ref == "claude_auth_volume"
        assert volume_mount_path == "/home/app/.claude"
        return {
            "verified": False,
            "reason": f"token={raw_secret} in {raw_path}",
        }

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.providers.volume_verifiers.verify_volume_credentials",
        _fake_verify,
    )

    async with db_base.async_session_maker() as session:
        existing = await session.get(ManagedAgentProviderProfile, profile_id)
        if existing is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="claude_code",
                    provider_id="anthropic",
                    provider_label="Anthropic",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="claude_auth_volume",
                    volume_mount_path="/home/app/.claude",
                    enabled=True,
                    command_behavior={
                        "auth_strategy": "claude_credential_methods",
                        "auth_actions": [
                            "connect_oauth",
                            "use_api_key",
                            "validate_oauth",
                            "disconnect_oauth",
                        ],
                    },
                )
            )
            await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/oauth/validate"
        )

    assert response.status_code == 400
    assert raw_secret not in response.text
    assert raw_path not in response.text
    detail = response.json()["detail"]
    assert "Claude OAuth validation failed:" in detail
    assert "[REDACTED]" in detail
    assert "[REDACTED_AUTH_PATH]" in detail

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, profile_id)
        assert profile is not None
        readiness = (profile.command_behavior or {}).get("auth_readiness", {})
        assert raw_secret not in str(readiness)
        assert raw_path not in str(readiness)
        assert readiness["failure_reason"] == "token=[REDACTED] in [REDACTED_AUTH_PATH]"


@pytest.mark.asyncio
async def test_claude_oauth_validate_failure_uses_unknown_reason_fallback(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _override_current_user()
    profile_id = "claude-oauth-missing-failure-reason"

    async def _fake_verify(
        *,
        runtime_id: str,
        volume_ref: str,
        volume_mount_path: str | None,
    ) -> dict[str, object]:
        return {"verified": False}

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.providers.volume_verifiers.verify_volume_credentials",
        _fake_verify,
    )

    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id="claude_code",
                provider_id="anthropic",
                provider_label="Anthropic",
                credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                volume_ref="claude_auth_volume",
                volume_mount_path="/home/app/.claude",
                enabled=True,
            )
        )
        await session.commit()

    async with client_app as client:
        response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/oauth/validate"
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Claude OAuth validation failed: unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "profile_id",
        "runtime_id",
        "provider_id",
        "volume_ref",
        "mount_path",
        "label_prefix",
        "auth_strategy",
        "expected_home_overrides",
    ),
    [
        (
            "codex-openai-oauth-lifecycle",
            "codex_cli",
            "openai",
            "codex_auth_volume",
            "/home/app/.codex",
            "Codex",
            "codex_credential_methods",
            {"CODEX_HOME": "/home/app/.codex"},
        ),
        (
            "claude-anthropic-oauth-lifecycle",
            "claude_code",
            "anthropic",
            "claude_auth_volume",
            "/home/app/.claude",
            "Claude",
            "claude_credential_methods",
            {"CLAUDE_HOME": "/home/app/.claude"},
        ),
    ],
)
async def test_first_party_oauth_lifecycle_generalized_across_runtimes(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
    profile_id: str,
    runtime_id: str,
    provider_id: str,
    volume_ref: str,
    mount_path: str,
    label_prefix: str,
    auth_strategy: str,
    expected_home_overrides: dict[str, str],
) -> None:
    """validate + disconnect work for Codex and Gemini, not only Claude."""
    _override_current_user()
    synced_runtimes: list[str] = []

    async def _fake_verify(
        *,
        runtime_id: str,
        volume_ref: str,
        volume_mount_path: str | None,
    ) -> dict[str, object]:
        return {"verified": True}

    async def _fake_sync(*, session: AsyncSession, runtime_id: str) -> None:
        synced_runtimes.append(runtime_id)

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.providers.volume_verifiers.verify_volume_credentials",
        _fake_verify,
    )
    monkeypatch.setattr(
        "api_service.api.routers.provider_profiles.sync_provider_profile_manager",
        _fake_sync,
    )

    async with db_base.async_session_maker() as session:
        if await session.get(ManagedAgentProviderProfile, profile_id) is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id=runtime_id,
                    provider_id=provider_id,
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref=volume_ref,
                    volume_mount_path=mount_path,
                    enabled=True,
                )
            )
            await session.flush()
        profile = await session.get(ManagedAgentProviderProfile, profile_id)
        assert profile is not None
        profile.credential_source = ProviderCredentialSource.OAUTH_VOLUME
        profile.runtime_materialization_mode = RuntimeMaterializationMode.OAUTH_HOME
        profile.volume_ref = volume_ref
        profile.volume_mount_path = mount_path
        profile.home_path_overrides = {"CUSTOM_HOME": "/custom/home"}
        profile.enabled = True
        await session.commit()

    async with client_app as client:
        validate_response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/oauth/validate"
        )
        connected_payload = (
            await client.get(f"/api/v1/provider-profiles/{profile_id}")
        ).json()
        disconnect_response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/oauth/disconnect"
        )
        disconnected_payload = (
            await client.get(f"/api/v1/provider-profiles/{profile_id}")
        ).json()

    assert validate_response.status_code == 200
    assert validate_response.json()["status"] == "ready"
    assert validate_response.json()["status_label"] == f"{label_prefix} OAuth ready"

    assert connected_payload["enabled"] is True
    assert connected_payload["auth_state"] == "connected"
    assert connected_payload["disabled_reason"] is None
    assert connected_payload["last_auth_method"] == "oauth_volume"
    assert connected_payload["home_path_overrides"] == {
        "CUSTOM_HOME": "/custom/home",
        **expected_home_overrides,
    }
    behavior = connected_payload["command_behavior"]
    assert behavior["auth_strategy"] == auth_strategy
    assert behavior["auth_status_label"] == f"{label_prefix} OAuth ready"
    assert behavior["auth_readiness"]["connected"] is True

    assert disconnect_response.status_code == 200
    assert disconnect_response.json()["status"] == "disconnected"
    assert (
        disconnect_response.json()["status_label"]
        == f"{label_prefix} OAuth disconnected"
    )
    assert disconnected_payload["credential_source"] == "none"
    assert disconnected_payload["volume_ref"] is None
    assert disconnected_payload["volume_mount_path"] is None
    assert disconnected_payload["home_path_overrides"] == {
        "CUSTOM_HOME": "/custom/home"
    }
    assert disconnected_payload["auth_state"] == "disconnected"
    assert disconnected_payload["disabled_reason"] == "disconnected"
    assert disconnected_payload["enabled"] is False
    assert disconnected_payload["command_behavior"]["auth_actions"] == ["use_api_key"]
    assert synced_runtimes == [runtime_id, runtime_id]


@pytest.mark.asyncio
async def test_oauth_lifecycle_rejects_non_first_party_profile(
    client_app: AsyncClient,
    _module_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OAuth validate/disconnect are only available to first-party profiles."""
    _override_current_user()
    profile_id = "thirdparty-oauth-not-supported"

    async def _unexpected_verify(**_kwargs):
        raise AssertionError("non-first-party profiles must fail before verification")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.providers.volume_verifiers.verify_volume_credentials",
        _unexpected_verify,
    )

    async with db_base.async_session_maker() as session:
        if await session.get(ManagedAgentProviderProfile, profile_id) is None:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id="custom_runtime",
                    provider_id="acme",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="acme_auth_volume",
                    volume_mount_path="/home/app/.acme",
                    enabled=True,
                )
            )
            await session.commit()

    async with client_app as client:
        validate_response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/oauth/validate"
        )
        disconnect_response = await client.post(
            f"/api/v1/provider-profiles/{profile_id}/oauth/disconnect"
        )

        expected_detail = (
            "OAuth lifecycle actions are only supported for first-party "
            "Claude and Codex provider profiles."
        )
    assert validate_response.status_code == 422
    assert validate_response.json()["detail"] == expected_detail
    assert disconnect_response.status_code == 422
    assert disconnect_response.json()["detail"] == expected_detail


# ---------------------------------------------------------------------------
# MoonLadderStudios/MoonMind#3788 — runtime-scoped listing is what execution
# surfaces rely on to never offer a profile owned by another runtime.
# ---------------------------------------------------------------------------


def _mm3788_profile_payload(
    *,
    profile_id: str,
    runtime_id: str,
    enabled: bool = True,
) -> dict[str, Any]:
    # #3821: supply a superset isolation policy covering known minimax
    # strategies so both known (codex_cli/claude_code) and custom runtimes
    # classify as legacy_custom (warning, launchable) instead of
    # missing_or_stale (error). Omitted policies would fail closed for
    # enabled profiles.
    return {
        "profile_id": profile_id,
        "runtime_id": runtime_id,
        "provider_id": "minimax",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        # The same managed secret may back one profile per runtime.
        "secret_refs": {"MINIMAX_API_KEY": "env://mm3788_minimax_secret"},
        "clear_env_keys": [
            "MINIMAX_API_KEY",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
        ],
        "enabled": enabled,
        "auth_state": "connected" if enabled else "not_configured",
        "disabled_reason": None if enabled else "missing_credentials",
        "last_auth_method": "secret_ref",
    }


@pytest.mark.asyncio
async def test_mm3788_runtime_filter_excludes_other_runtime_profiles(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_current_user(is_superuser=True)
    _advertise_expert_manual_contracts(
        monkeypatch,
        ("codex_cli", "minimax", "secret_ref", "api_key_env"),
        ("claude_code", "minimax", "secret_ref", "api_key_env"),
    )

    async with client_app as client:
        codex_response = await client.post(
            "/api/v1/provider-profiles",
            json=_mm3788_profile_payload(
                profile_id="mm3788_codex_minimax", runtime_id="codex_cli"
            ),
        )
        claude_response = await client.post(
            "/api/v1/provider-profiles",
            json=_mm3788_profile_payload(
                profile_id="mm3788_claude_minimax", runtime_id="claude_code"
            ),
        )
        codex_listed = await client.get(
            "/api/v1/provider-profiles", params={"runtime_id": "codex_cli"}
        )
        claude_listed = await client.get(
            "/api/v1/provider-profiles", params={"runtime_id": "claude_code"}
        )
        unfiltered = await client.get("/api/v1/provider-profiles")

    assert codex_response.status_code == 201
    assert claude_response.status_code == 201

    assert codex_listed.status_code == 200
    codex_rows = codex_listed.json()
    assert {row["runtime_id"] for row in codex_rows} == {"codex_cli"}
    codex_ids = {row["profile_id"] for row in codex_rows}
    assert "mm3788_codex_minimax" in codex_ids
    assert "mm3788_claude_minimax" not in codex_ids

    assert claude_listed.status_code == 200
    claude_rows = claude_listed.json()
    assert {row["runtime_id"] for row in claude_rows} == {"claude_code"}
    claude_ids = {row["profile_id"] for row in claude_rows}
    assert "mm3788_claude_minimax" in claude_ids
    assert "mm3788_codex_minimax" not in claude_ids

    # Settings keeps the complete administrative view.
    unfiltered_ids = {row["profile_id"] for row in unfiltered.json()}
    assert {"mm3788_codex_minimax", "mm3788_claude_minimax"} <= unfiltered_ids


@pytest.mark.asyncio
async def test_mm3788_runtime_filter_composes_with_enabled_only(
    client_app: AsyncClient, _module_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_current_user(is_superuser=True)
    _advertise_expert_manual_contracts(
        monkeypatch,
        ("mm3788_compose_runtime", "minimax", "secret_ref", "api_key_env"),
        ("mm3788_compose_other", "minimax", "secret_ref", "api_key_env"),
    )

    async with client_app as client:
        enabled_response = await client.post(
            "/api/v1/provider-profiles",
            json=_mm3788_profile_payload(
                profile_id="mm3788_compose_enabled",
                runtime_id="mm3788_compose_runtime",
            ),
        )
        disabled_response = await client.post(
            "/api/v1/provider-profiles",
            json=_mm3788_profile_payload(
                profile_id="mm3788_compose_disabled",
                runtime_id="mm3788_compose_runtime",
                enabled=False,
            ),
        )
        other_runtime_response = await client.post(
            "/api/v1/provider-profiles",
            json=_mm3788_profile_payload(
                profile_id="mm3788_compose_other_runtime",
                runtime_id="mm3788_compose_other",
            ),
        )
        filtered = await client.get(
            "/api/v1/provider-profiles",
            params={"runtime_id": "mm3788_compose_runtime", "enabled_only": "true"},
        )
        runtime_only = await client.get(
            "/api/v1/provider-profiles",
            params={"runtime_id": "mm3788_compose_runtime"},
        )

    assert enabled_response.status_code == 201
    assert disabled_response.status_code == 201
    assert other_runtime_response.status_code == 201

    assert filtered.status_code == 200
    assert [row["profile_id"] for row in filtered.json()] == [
        "mm3788_compose_enabled"
    ]

    assert runtime_only.status_code == 200
    assert {row["profile_id"] for row in runtime_only.json()} == {
        "mm3788_compose_enabled",
        "mm3788_compose_disabled",
    }


@pytest.mark.asyncio
async def test_mm3788_runtime_filter_still_applies_profile_visibility(
    client_app: AsyncClient, _module_db
) -> None:
    owner = _override_current_user()
    other_owner_id = uuid4()

    async with db_base.async_session_maker() as session:
        for profile_id, owner_user_id in (
            ("mm3788_visible_owned", owner.id),
            ("mm3788_hidden_other_owner", other_owner_id),
        ):
            if await session.get(ManagedAgentProviderProfile, profile_id) is None:
                session.add(
                    ManagedAgentProviderProfile(
                        profile_id=profile_id,
                        runtime_id="mm3788_visibility_runtime",
                        provider_id="minimax",
                        credential_source=ProviderCredentialSource.NONE,
                        runtime_materialization_mode=(
                            RuntimeMaterializationMode.COMPOSITE
                        ),
                        owner_user_id=owner_user_id,
                        enabled=True,
                    )
                )
        await session.commit()

    async with client_app as client:
        listed = await client.get(
            "/api/v1/provider-profiles",
            params={"runtime_id": "mm3788_visibility_runtime"},
        )

    assert listed.status_code == 200
    listed_ids = {row["profile_id"] for row in listed.json()}
    assert "mm3788_visible_owned" in listed_ids
    # Runtime scoping narrows the result set; it never widens visibility.
    assert "mm3788_hidden_other_owner" not in listed_ids


# ---- MoonLadderStudios/MoonMind#3821 launch-safety isolation wiring ----

_MM3821_CODEX_API_KEY_DERIVED = [
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT",
    "MINIMAX_API_KEY",
]


@pytest.mark.asyncio
async def test_3821_guided_create_with_clear_env_keys_rejected(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user()
    profile_id = f"mm3821-locked-create-{uuid4().hex}"

    async with client_app as client:
        response = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": profile_id,
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "api_key",
                "preset_version": CODEX_OPENAI_API_KEY_PRESET_VERSION,
                "clear_env_keys": ["OPENAI_API_KEY"],
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "provider_profile_clear_env_keys_locked"
    assert detail["field"] == "clear_env_keys"
    async with db_base.async_session_maker() as session:
        assert await session.get(ManagedAgentProviderProfile, profile_id) is None


@pytest.mark.asyncio
async def test_3821_non_superuser_update_of_clear_env_keys_locked(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user()
    profile_id = f"mm3821-locked-update-{uuid4().hex}"
    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=ProviderCredentialSource.SECRET_REF,
                runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                secret_refs={"openai_api_key": "env://OPENAI_API_KEY"},
                clear_env_keys=list(_MM3821_CODEX_API_KEY_DERIVED),
                enabled=True,
                auth_state=ProviderProfileAuthState.CONNECTED,
            )
        )
        await session.commit()

    async with client_app as client:
        response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={"clear_env_keys": ["OPENAI_API_KEY"]},
        )

    assert response.status_code == 422
    assert (
        response.json()["detail"]["code"]
        == "provider_profile_clear_env_keys_locked"
    )
    async with db_base.async_session_maker() as session:
        persisted = await session.get(ManagedAgentProviderProfile, profile_id)
        assert persisted is not None
        assert sorted(persisted.clear_env_keys) == sorted(
            _MM3821_CODEX_API_KEY_DERIVED
        )


@pytest.mark.asyncio
async def test_3821_non_superuser_update_repair_to_derived_accepted(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user()
    profile_id = f"mm3821-repair-update-{uuid4().hex}"
    secret_slug = f"mm3821-repair-openai-{uuid4().hex}"
    async with db_base.async_session_maker() as session:
        session.add(
            ManagedSecret(
                slug=secret_slug,
                ciphertext="encrypted-test-value",
                status=SecretStatus.ACTIVE,
                details={},
            )
        )
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=ProviderCredentialSource.SECRET_REF,
                runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                secret_refs={"openai_api_key": f"db://{secret_slug}"},
                clear_env_keys=["OPENAI_API_KEY"],
                enabled=True,
                auth_state=ProviderProfileAuthState.CONNECTED,
            )
        )
        await session.commit()

    async with client_app as client:
        response = await client.patch(
            f"/api/v1/provider-profiles/{profile_id}",
            json={"clear_env_keys": list(reversed(_MM3821_CODEX_API_KEY_DERIVED))},
        )
        assert response.status_code == 200
        fetched = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert fetched.status_code == 200
    assert fetched.json()["clear_env_keys"] == _MM3821_CODEX_API_KEY_DERIVED
    isolation = fetched.json()["launch_isolation"]
    assert isolation["classification"] == "current"
    assert isolation["source"] == "runtime_provider_isolation_policy"


@pytest.mark.asyncio
async def test_3821_stale_isolation_policy_blocks_readiness(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user()
    profile_id = f"mm3821-stale-readiness-{uuid4().hex}"
    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=ProviderCredentialSource.SECRET_REF,
                runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                secret_refs={"openai_api_key": "env://OPENAI_API_KEY"},
                clear_env_keys=["OPENAI_API_KEY"],
                enabled=True,
                auth_state=ProviderProfileAuthState.CONNECTED,
            )
        )
        await session.commit()

    async with client_app as client:
        response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["launch_ready"] is False
    assert payload["readiness"]["status"] == "blocked"
    isolation_check = next(
        check
        for check in payload["readiness"]["checks"]
        if check["id"] == "launch_isolation"
    )
    assert isolation_check["status"] == "error"
    assert "repair" in isolation_check["message"].lower()


@pytest.mark.asyncio
async def test_3821_launch_isolation_shape_in_get(
    client_app: AsyncClient, _module_db
) -> None:
    _override_current_user()
    profile_id = f"mm3821-isolation-shape-{uuid4().hex}"

    async with client_app as client:
        created = await client.post(
            "/api/v1/provider-profiles",
            json={
                "profile_id": profile_id,
                "runtime_id": "codex_cli",
                "provider_id": "openai",
                "authentication_method": "api_key",
                "preset_version": CODEX_OPENAI_API_KEY_PRESET_VERSION,
            },
        )
        assert created.status_code == 201
        response = await client.get(f"/api/v1/provider-profiles/{profile_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["clear_env_keys"] == _MM3821_CODEX_API_KEY_DERIVED
    isolation = payload["launch_isolation"]
    assert isolation["effective_keys"] == _MM3821_CODEX_API_KEY_DERIVED
    assert isolation["source"] == "runtime_provider_isolation_policy"
    assert isolation["derived"] is True
    assert isolation["editable"] is False
    assert isolation["lock_reason"]
    assert isolation["strategy_id"] == "codex_cli/openai/api_key"
    assert set(isolation["explanations"]) == set(_MM3821_CODEX_API_KEY_DERIVED)
    assert isolation["classification"] == "current"


def test_3821_oauth_enrollment_merge_preserves_unknown_keys() -> None:
    from datetime import datetime, timezone

    profile = ManagedAgentProviderProfile(
        profile_id="mm3821-enrollment-merge",
        runtime_id="codex_cli",
        provider_id="openai",
        credential_source=ProviderCredentialSource.OAUTH_VOLUME,
        runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
        clear_env_keys=["CUSTOM_LEGACY_KEY"],
        command_behavior={},
        home_path_overrides={},
        auth_state=ProviderProfileAuthState.OAUTH_PENDING,
    )
    apply_oauth_connected_state(
        profile,
        mapping=None,
        validated_at=datetime.now(timezone.utc),
    )

    assert "CUSTOM_LEGACY_KEY" in profile.clear_env_keys
    assert "OPENAI_API_KEY" in profile.clear_env_keys


@pytest.mark.asyncio
async def test_3821_startup_reconciliation_normalizes_and_flags(
    _module_db,
) -> None:
    from api_service.services.provider_profile_service import (
        reconcile_provider_profile_isolation_policies,
    )

    normalize_id = f"mm3821-reconcile-normalize-{uuid4().hex}"
    preserve_id = f"mm3821-reconcile-preserve-{uuid4().hex}"
    repair_id = f"mm3821-reconcile-repair-{uuid4().hex}"
    async with db_base.async_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=normalize_id,
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=ProviderCredentialSource.SECRET_REF,
                runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                clear_env_keys=list(reversed(_MM3821_CODEX_API_KEY_DERIVED)),
                enabled=False,
                auth_state=ProviderProfileAuthState.CONNECTED,
            )
        )
        session.add(
            ManagedAgentProviderProfile(
                profile_id=preserve_id,
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=ProviderCredentialSource.SECRET_REF,
                runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                clear_env_keys=[*_MM3821_CODEX_API_KEY_DERIVED, "CUSTOM_LEGACY_KEY"],
                enabled=False,
                auth_state=ProviderProfileAuthState.CONNECTED,
            )
        )
        session.add(
            ManagedAgentProviderProfile(
                profile_id=repair_id,
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=ProviderCredentialSource.SECRET_REF,
                runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                clear_env_keys=["OPENAI_API_KEY"],
                enabled=False,
                auth_state=ProviderProfileAuthState.CONNECTED,
            )
        )
        await session.commit()

        counts = await reconcile_provider_profile_isolation_policies(session=session)
        await session.commit()

        normalized = await session.get(ManagedAgentProviderProfile, normalize_id)
        preserved = await session.get(ManagedAgentProviderProfile, preserve_id)
        repair = await session.get(ManagedAgentProviderProfile, repair_id)

    assert counts["normalized"] >= 1
    assert counts["preserve_custom"] >= 1
    assert counts["repair_required"] >= 1
    assert normalized is not None
    assert list(normalized.clear_env_keys) == _MM3821_CODEX_API_KEY_DERIVED
    assert preserved is not None
    assert "CUSTOM_LEGACY_KEY" in preserved.clear_env_keys
    assert repair is not None
    assert repair.clear_env_keys == ["OPENAI_API_KEY"]
