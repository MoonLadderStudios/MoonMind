"""Sanitized pre-change baseline + frozen-contract guardrails for #4117.

Covers MoonLadderStudios/MoonMind#4117 (parent epic #4116):
caller/disposition inventory acceptance contract for Keycloak removal.

These tests are hermetic (no DB, no network, no credentials) and use synthetic
fixtures only: owner / non-owner / admin / zero-UUID / service principals.
They exercise genuine public authorization boundaries (artifact owner checks,
worker-auth gate, auth-router mounting, identity-model constraints), not
provider-string assertions.

Pre-change baselines asserted here are owned for replacement by the K4/K5
cutover children (see docs/tmp/KeycloakRemovalInventory-4117.md); the frozen
contract assertions (canonical UUID principal, no email linking, error
semantics) are durable and move with docs/Security/AuthenticationContracts.md.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api_service import auth as auth_module
from api_service import auth_providers as auth_providers_module
from api_service.api.routers import worker_auth as worker_auth_module
from api_service.db import models as db_models
from moonmind.config.settings import settings
from moonmind.workflows.temporal import artifacts as artifact_module

# ---------------------------------------------------------------------------
# Sanitized fixtures (synthetic only — no secrets, no production data)
# ---------------------------------------------------------------------------

ZERO_UUID = "00000000-0000-0000-0000-000000000000"
OWNER_PRINCIPAL = "owner-user-4117"
NON_OWNER_PRINCIPAL = "non-owner-user-4117"
ADMIN_PRINCIPAL = "admin-user-4117"
SERVICE_PRINCIPAL = "service:worker-4117"


def _artifact_owned_by(owner: str):
    return SimpleNamespace(artifact_id="artifact-4117", created_by_principal=owner)


def _service():
    # Bypass __init__ (needs repository/session); the access helpers under test
    # only touch settings + _owner_principal.
    return object.__new__(artifact_module.TemporalArtifactService)


def _user(principal: str, *, is_superuser: bool = False):
    return SimpleNamespace(
        id=uuid.uuid4(), email=f"{principal}@example.invalid", is_superuser=is_superuser
    )


# ---------------------------------------------------------------------------
# Frozen identity contract (durable)
# ---------------------------------------------------------------------------


def test_canonical_principal_is_uuid_backed_user_id():
    assert db_models.User.__tablename__ == "user"
    id_col = db_models.User.__table__.c["id"]
    # fastapi-users SQLAlchemyBaseUserTableUUID supplies a GUID/UUID id column;
    # assert the contract (UUID principal), not one vendor type class.
    assert "uuid" in type(id_col.type).__name__.lower() or "guid" in type(
        id_col.type
    ).__name__.lower()


def test_external_identity_pair_has_unique_pair_constraint():
    table = db_models.User.__table__
    # Pre-change baseline (owned for replacement by the K3 identity/migration
    # child): the legacy `oidc_provider` column is 32 chars and cannot hold a
    # full issuer URI. The frozen contract is uniqueness + full preservation
    # of the `(issuer, subject)` pair, independently of the legacy layout —
    # K3 replaces this with one external-identity relation when the field
    # cannot hold issuer URIs, never two competing mappings.
    constraints = list(table.constraints)
    assert any(
        getattr(c, "name", "") == "uq_oidc_identity" for c in constraints
    ), "expected uq_oidc_identity unique pair constraint on (oidc_provider, oidc_subject)"
    assert "oidc_subject" in table.c and table.c["oidc_subject"].type.length == 255


def test_zero_uuid_default_is_reserved_single_user_principal():
    assert auth_module._DEFAULT_USER_ID == ZERO_UUID
    uuid.UUID(auth_module._DEFAULT_USER_ID)  # parses as a UUID


def test_target_modes_are_frozen_and_documented():
    inventory = Path("docs/tmp/KeycloakRemovalInventory-4117.md").read_text(encoding="utf-8")
    contracts = Path("docs/Security/AuthenticationContracts.md").read_text(encoding="utf-8")
    # Supported modes: asserted as structured table rows in the canonical
    # contract (`| `accounts` |`), not incidental prose mentions — a removed
    # mode row or an added retired-mode row fails even when the word appears
    # elsewhere in the document.
    for mode in ("accounts", "oidc", "header", "disabled"):
        assert f"| `{mode}` |" in contracts, f"missing structured mode row for {mode}"
    # Retired selectors: frozen as rejected-at-startup with migration guidance
    # in the canonical contract, with dispositions tracked in the inventory.
    for retired in ("keycloak", "default", "google"):
        assert retired in contracts
    assert "Rejected selectors" in contracts or "retired selectors" in contracts.lower()
    for retired in ("keycloak", "default", "google"):
        assert retired in inventory  # retired literals tracked with disposition


# ---------------------------------------------------------------------------
# Sanitized pre-change baseline: artifact authorization boundary
# ---------------------------------------------------------------------------


def test_disabled_mode_bypasses_artifact_authorization_for_any_principal(monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled")
    service = _service()
    artifact = _artifact_owned_by(OWNER_PRINCIPAL)
    for principal in (
        OWNER_PRINCIPAL,
        NON_OWNER_PRINCIPAL,
        ADMIN_PRINCIPAL,
        SERVICE_PRINCIPAL,
        "",
    ):
        service._assert_read_access(artifact, principal=principal)
        service._assert_mutation_access(artifact, principal=principal)


def test_enabled_mode_owner_passes_non_owner_fails(monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    service = _service()
    artifact = _artifact_owned_by(OWNER_PRINCIPAL)
    service._assert_read_access(artifact, principal=OWNER_PRINCIPAL)
    service._assert_mutation_access(artifact, principal=OWNER_PRINCIPAL)
    with pytest.raises(artifact_module.TemporalArtifactAuthorizationError):
        service._assert_read_access(artifact, principal=NON_OWNER_PRINCIPAL)
    with pytest.raises(artifact_module.TemporalArtifactAuthorizationError):
        service._assert_mutation_access(artifact, principal=NON_OWNER_PRINCIPAL)


def test_enabled_mode_service_principal_passes_owner_checks(monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "accounts")
    service = _service()
    artifact = _artifact_owned_by(OWNER_PRINCIPAL)
    service._assert_read_access(artifact, principal=SERVICE_PRINCIPAL)
    service._assert_mutation_access(artifact, principal=SERVICE_PRINCIPAL)


# ---------------------------------------------------------------------------
# Sanitized pre-change baseline: worker-auth gate error semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_auth_rejects_legacy_token_with_gone(monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    with pytest.raises(HTTPException) as exc_info:
        await worker_auth_module._require_worker_auth(
            worker_token="legacy-token", user=_user(OWNER_PRINCIPAL)
        )
    assert exc_info.value.status_code == 410
    assert exc_info.value.detail["code"] == "worker_token_deprecated"


@pytest.mark.asyncio
async def test_worker_auth_resolves_oidc_principal(monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    resolved = await worker_auth_module._require_worker_auth(
        worker_token=None, user=_user(OWNER_PRINCIPAL)
    )
    assert resolved.auth_source == "oidc"


@pytest.mark.asyncio
async def test_worker_auth_missing_credential_is_unauthorized(monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    with pytest.raises(HTTPException) as exc_info:
        await worker_auth_module._require_worker_auth(worker_token=None, user=None)
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Sanitized pre-change baseline: router mounting branches
# ---------------------------------------------------------------------------


def test_no_mode_mounts_legacy_auth_routes(monkeypatch):
    # Post-#4129: the legacy helper router is empty in every mode; no
    # login/register/reset route remains reachable through this path.
    for mode in ("disabled", "accounts", "oidc", "header"):
        monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", mode)
        router = auth_providers_module.get_auth_router()
        assert router.routes == []


def test_retired_selectors_rejected_with_migration_guidance():
    for retired in ("keycloak", "default", "google", "local"):
        settings.oidc.AUTH_PROVIDER = retired
        try:
            with pytest.raises(RuntimeError, match="removed|Unknown"):
                settings.oidc.validate_auth_provider()
        finally:
            settings.oidc.AUTH_PROVIDER = "disabled"
    for supported in ("accounts", "oidc", "header", "disabled"):
        settings.oidc.AUTH_PROVIDER = supported
        try:
            assert settings.oidc.validate_auth_provider() == supported
        finally:
            settings.oidc.AUTH_PROVIDER = "disabled"


def test_central_integration_points_exist():
    assert callable(auth_providers_module.get_current_user)
    assert callable(auth_providers_module.get_current_user_optional)
    assert callable(auth_providers_module.get_default_user_from_db)
