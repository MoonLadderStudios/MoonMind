import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import api_service.auth_providers as auth_providers
from api_service.auth_providers import (
    get_auth_router,
    get_current_user,
    get_current_user_optional,
    get_default_user_from_db,
    is_planned_auth_provider,
    normalize_auth_provider,
    validate_auth_provider,
)
from api_service.db.models import User
from moonmind.config.settings import settings

@pytest.mark.asyncio
async def test_get_default_user_happy_path(monkeypatch):
    user_id = str(uuid.uuid4())
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_ID", user_id)
    default_user = User(
        id=uuid.UUID(user_id),
        email="default@example.com",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        hashed_password="x",
    )
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.get.return_value = default_user

    result = await get_default_user_from_db(mock_session)

    assert result.id == uuid.UUID(user_id)
    mock_session.get.assert_called_once_with(User, uuid.UUID(user_id))

@pytest.mark.asyncio
async def test_get_default_user_invalid_id(monkeypatch):
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_ID", None)
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.get.return_value = None
    with pytest.raises(HTTPException) as exc:
        await get_default_user_from_db(mock_session)
    assert exc.value.status_code == 500
    assert exc.value.detail == "Default user not found"


@pytest.mark.asyncio
async def test_disabled_auth_fallback_user_has_default_id(monkeypatch):
    user_id = str(uuid.uuid4())
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled")
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_ID", user_id)
    monkeypatch.setattr(settings.workflow, "test_mode", False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("MOONMIND_DISABLE_DEFAULT_USER_DB_LOOKUP", "1")
    monkeypatch.setattr(auth_providers, "_cached_current_user_dependency", None)

    dependency = get_current_user()
    user = await dependency()

    assert user.id == uuid.UUID(user_id)
    assert user.is_superuser is True
    monkeypatch.setattr(auth_providers, "_cached_current_user_dependency", None)


@pytest.mark.parametrize(
    "provider", ["disabled", "default", "keycloak", "google", "accounts", "oidc", "header"]
)
def test_validate_auth_provider_accepts_known_modes(provider):
    assert validate_auth_provider(provider) == provider


@pytest.mark.parametrize("provider", ["none", "keycloakx", "", "disabledx"])
def test_validate_auth_provider_rejects_unknown_modes(provider):
    with pytest.raises(RuntimeError, match="Unknown AUTH_PROVIDER"):
        validate_auth_provider(provider)


def test_validate_auth_provider_rejects_unknown_settings_value(monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "sso-magic")
    with pytest.raises(RuntimeError, match="Refusing to start"):
        validate_auth_provider()


def _non_test_runtime(monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled")
    monkeypatch.setattr(settings.workflow, "test_mode", False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("MOONMIND_DISABLE_DEFAULT_USER_DB_LOOKUP", raising=False)
    monkeypatch.setattr(auth_providers, "_cached_current_user_dependency", None)
    monkeypatch.setattr(auth_providers, "_is_test_runtime", lambda: False)


@pytest.mark.asyncio
async def test_disabled_auth_db_outage_fails_closed(monkeypatch):
    """MoonLadderStudios/MoonMind#4116 K4: DB outage must not yield an admin stub."""
    import api_service.db.base as db_base

    _non_test_runtime(monkeypatch)

    class _FailingCtx:
        async def __aenter__(self):
            raise ConnectionRefusedError("db down")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(db_base, "get_async_session_context", lambda: _FailingCtx())

    dependency = get_current_user()
    with pytest.raises(HTTPException) as exc:
        await dependency()
    assert exc.value.status_code == 503
    monkeypatch.setattr(auth_providers, "_cached_current_user_dependency", None)


@pytest.mark.asyncio
async def test_disabled_auth_missing_row_fails_closed(monkeypatch):
    """MoonLadderStudios/MoonMind#4116 K4: missing row must not yield an admin stub."""
    import api_service.db.base as db_base

    _non_test_runtime(monkeypatch)

    class _EmptySession:
        async def get(self, *args, **kwargs):
            return None

    class _EmptyCtx:
        async def __aenter__(self):
            return _EmptySession()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(db_base, "get_async_session_context", lambda: _EmptyCtx())

    dependency = get_current_user()
    with pytest.raises(HTTPException) as exc:
        await dependency()
    assert exc.value.status_code == 503
    monkeypatch.setattr(auth_providers, "_cached_current_user_dependency", None)


@pytest.mark.parametrize("provider", ["accounts", "oidc", "header"])
def test_planned_modes_are_recognized_but_have_no_behavior(provider):
    """MoonLadderStudios/MoonMind#4116 K3: planned names validate but flag planned."""
    assert validate_auth_provider(provider) == provider
    assert is_planned_auth_provider(provider) is True
    assert is_planned_auth_provider("disabled") is False


@pytest.mark.parametrize("provider", ["accounts", "oidc", "header"])
@pytest.mark.asyncio
async def test_planned_modes_fail_closed_at_request_boundary(monkeypatch, provider):
    """Planned modes must never resolve to a user via a substitute authority."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", provider)
    monkeypatch.setattr(auth_providers, "_cached_current_user_dependency", None)

    dependency = get_current_user()
    with pytest.raises(HTTPException) as exc:
        await dependency()
    assert exc.value.status_code == 503

    optional_dependency = get_current_user_optional()
    with pytest.raises(HTTPException) as exc:
        await optional_dependency()
    assert exc.value.status_code == 503
    monkeypatch.setattr(auth_providers, "_cached_current_user_dependency", None)


@pytest.mark.parametrize("provider", ["accounts", "oidc", "header"])
def test_planned_modes_mount_no_legacy_auth_routes(monkeypatch, provider):
    """Planned modes must not mount legacy FastAPI Users issuance paths."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", provider)
    router = get_auth_router()
    assert [route.path for route in router.routes] == []


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("disabled", "disabled"),
        ("Disabled", "disabled"),
        (" DISABLED ", "disabled"),
        ("ACCOUNTS", "accounts"),
        (" Oidc ", "oidc"),
        ("HEADER", "header"),
        ("Keycloak", "keycloak"),
        ("sso-magic", "sso-magic"),
        ("", ""),
    ],
)
def test_normalize_auth_provider_case_and_whitespace(raw, expected):
    """MoonLadderStudios/MoonMind#4116: selector comparisons must be case-insensitive."""
    assert normalize_auth_provider(raw) == expected


@pytest.mark.parametrize("provider", ["Disabled", " DISABLED ", "DISABLED"])
def test_disabled_mode_case_variants_take_disabled_path(monkeypatch, provider):
    """Case/whitespace variants of 'disabled' must not fall through to FastAPI Users."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", provider)
    monkeypatch.setattr(settings.workflow, "test_mode", True)
    monkeypatch.setattr(auth_providers, "_cached_current_user_dependency", None)
    try:
        dependency = get_current_user()
        assert dependency is not auth_providers.current_active_user
    finally:
        monkeypatch.setattr(auth_providers, "_cached_current_user_dependency", None)


@pytest.mark.parametrize("provider", ["ACCOUNTS", " Oidc ", "HEADER"])
def test_planned_mode_case_variants_fail_closed(monkeypatch, provider):
    """Planned-mode variants must fail closed, never use a substitute authority."""
    assert is_planned_auth_provider(provider) is True
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", provider)
    monkeypatch.setattr(auth_providers, "_cached_current_user_dependency", None)
    try:
        router = get_auth_router()
        assert [route.path for route in router.routes] == []
    finally:
        monkeypatch.setattr(auth_providers, "_cached_current_user_dependency", None)


@pytest.mark.parametrize("provider", ["sso-magic", "SSO-MAGIC", " keycloakx "])
@pytest.mark.asyncio
async def test_unknown_modes_fail_closed_at_request_boundary(monkeypatch, provider):
    """Unknown selectors must 503 at request boundaries, never resolve via FastAPI Users."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", provider)
    monkeypatch.setattr(auth_providers, "_cached_current_user_dependency", None)
    try:
        dependency = get_current_user()
        assert dependency is not auth_providers.current_active_user
        with pytest.raises(HTTPException) as exc:
            await dependency()
        assert exc.value.status_code == 503

        optional_dependency = get_current_user_optional()
        assert optional_dependency is not auth_providers.current_active_user_optional
        with pytest.raises(HTTPException) as exc:
            await optional_dependency()
        assert exc.value.status_code == 503

        router = get_auth_router()
        assert [route.path for route in router.routes] == []
    finally:
        monkeypatch.setattr(auth_providers, "_cached_current_user_dependency", None)
