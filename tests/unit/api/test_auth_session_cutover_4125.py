"""Cutover tests: production API resolves principals via the #4121 session authority.

MoonLadderStudios/MoonMind#4125. Every surviving user-facing API path must
resolve through the qualified shared authentication boundary
(``get_current_user()`` / ``get_current_user_optional()`` backed by
``session_authority_4121`` + the durable session/revocation stores), with
no direct legacy JWT bypass and no duplicate public register/reset route.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

import api_service.auth as auth_module
import api_service.auth_providers as providers_module
import api_service.services.session_store as session_store_module
from moonmind.security import omnigent_auth_qualification as qual


def _test_config():
    import moonmind.security.auth_modes_4120 as auth_modes_4120

    return auth_modes_4120.resolve_moonmind_auth_config(
        mode="accounts",
        cookie_secret=b"4125-cutover-test-secret-32bytes!",
        require_secure_cookies=False,
        environ={},
    )


def _enrolled():
    store = qual.InMemoryAsyncAccountStore()
    revocation = qual.InMemoryRevocationStore()
    user_id = uuid.uuid4()
    identity = qual.ValidatedIdentity(
        issuer="moonmind-accounts", subject="cutover@example.com"
    )
    store.enroll(
        identity,
        qual.AccountRecord(
            user_id=user_id,
            is_active=True,
            is_superuser=False,
            email="cutover@example.com",
        ),
    )
    return store, revocation, user_id, identity


def _request(
    *, bearer: str | None = None, cookie: str | None = None
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if bearer is not None:
        headers.append((b"authorization", f"Bearer {bearer}".encode()))
    if cookie is not None:
        from moonmind.security.session_authority_4121 import MOONMIND_DEV_COOKIE

        headers.append(
            (b"cookie", f"{MOONMIND_DEV_COOKIE}={cookie}".encode())
        )
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "query_string": b"",
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def _wire_session_boundary(monkeypatch, *, store, revocation, config) -> None:
    """Route the request-time boundary at the real session authority."""
    import moonmind.security.auth_modes_4120 as auth_modes

    monkeypatch.setattr(auth_modes, "_ACTIVE_PRODUCTION_MODE", "accounts")
    monkeypatch.setattr(
        providers_module,
        "build_moonmind_control_plane_config",
        lambda *args, **kwargs: config,
    )
    monkeypatch.setattr(
        session_store_module, "DbAccountStore", lambda session: store
    )
    monkeypatch.setattr(
        session_store_module, "DbRevocationStore", lambda session: revocation
    )


def _db_session_for(user_id: uuid.UUID, *, active: bool, admin: bool) -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = SimpleNamespace(
        id=user_id,
        email="cutover@example.com",
        is_active=active,
        is_superuser=admin,
    )
    return session


# ---------------------------------------------------------------------------
# Route inventory: shared boundary, no legacy bypass, no hidden login stack
# ---------------------------------------------------------------------------


def _mounted_paths(app) -> set[str]:
    """Expand production routes including lazily included routers.

    Newer FastAPI keeps ``include_router`` entries as placeholders that
    materialize through ``effective_candidates()``; a naive
    ``route.path`` scan would only see docs/static mounts. Duck-typing
    keeps this helper independent of FastAPI's private class names.
    """
    paths: set[str] = set()

    def _visit(routes) -> None:
        for route in routes or []:
            expand = getattr(route, "effective_candidates", None)
            if callable(expand):
                _visit(expand())
                continue
            path = getattr(route, "path", None)
            if isinstance(path, str) and path:
                paths.add(path)

    _visit(getattr(app, "routes", []))
    return paths


def test_production_routing_uses_shared_session_boundary() -> None:
    from api_service.main import app

    mounted = _mounted_paths(app)
    assert len(mounted) > 50, f"route expansion found too few paths: {len(mounted)}"
    assert "/auth/jwt/login" not in mounted
    assert "/auth/jwt/logout" not in mounted
    assert not any(path.startswith("/api/v1/auth") for path in mounted)
    assert not any(path.startswith("/auth/register") for path in mounted)
    # Needed User/profile capabilities stay mounted through the new boundary.
    assert any(path.endswith("/me") for path in mounted), sorted(mounted)[:10]
    # Representative user-facing surfaces resolve through the shared boundary.
    assert "/api/artifacts" in mounted

    strict = providers_module.get_current_user()
    optional = providers_module.get_current_user_optional()
    assert strict.__name__ == "_strict_current_user"
    assert optional.__name__ == "_optional_current_user"
    # Stable shared-boundary identity: routes and overrides share one object,
    # and no per-call cached foreign resolver exists.
    assert providers_module.get_current_user() is strict
    assert providers_module.get_current_user_optional() is optional
    assert not hasattr(providers_module, "_cached_current_user_dependency")
    assert not hasattr(providers_module, "_disabled_auth_test_user")
    # No legacy JWT acceptance remains on the request path.
    for retired in (
        "current_active_user",
        "current_active_user_optional",
        "auth_backend",
        "get_jwt_strategy",
        "fastapi_users",
    ):
        assert not hasattr(auth_module, retired), retired
    # bearer_transport stays as OpenAPI docs metadata with no issuance route.
    assert getattr(auth_module.bearer_transport, "tokenUrl", "") == ""


# ---------------------------------------------------------------------------
# Strict boundary through the real session authority
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strict_bearer_session_resolves_persisted_user(monkeypatch) -> None:
    config = _test_config()
    store, revocation, user_id, identity = _enrolled()
    _wire_session_boundary(
        monkeypatch, store=store, revocation=revocation, config=config
    )
    token, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    session = _db_session_for(user_id, active=True, admin=False)

    user = await providers_module.get_current_user()(_request(bearer=token), session)

    assert user.id == user_id
    assert user.is_superuser is False


@pytest.mark.asyncio
async def test_strict_cookie_session_resolves_persisted_user(monkeypatch) -> None:
    config = _test_config()
    store, revocation, user_id, identity = _enrolled()
    _wire_session_boundary(
        monkeypatch, store=store, revocation=revocation, config=config
    )
    token, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    session = _db_session_for(user_id, active=True, admin=True)

    user = await providers_module.get_current_user()(_request(cookie=token), session)

    assert user.id == user_id
    assert user.is_superuser is True


@pytest.mark.asyncio
async def test_strict_missing_credential_is_auth_required(monkeypatch) -> None:
    config = _test_config()
    store, revocation, _, _ = _enrolled()
    _wire_session_boundary(
        monkeypatch, store=store, revocation=revocation, config=config
    )
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(HTTPException) as exc:
        await providers_module.get_current_user()(_request(), session)

    assert exc.value.status_code == 401
    assert exc.value.detail == {"code": "auth_required"}


@pytest.mark.asyncio
async def test_strict_legacy_application_jwt_is_rejected(monkeypatch) -> None:
    """Old application credentials die at the migrated boundary."""
    config = _test_config()
    store, revocation, _, _ = _enrolled()
    _wire_session_boundary(
        monkeypatch, store=store, revocation=revocation, config=config
    )
    legacy = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iss": "https://keycloak-retired.example.invalid/realms/moonmind",
            "aud": "account",
        },
        "legacy-jwt-secret",
        algorithm="HS256",
    )
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(HTTPException) as exc:
        await providers_module.get_current_user()(_request(bearer=legacy), session)

    assert exc.value.status_code == 401
    assert exc.value.detail == {"code": "auth_invalid"}


@pytest.mark.asyncio
async def test_strict_conflicting_cookie_and_bearer_is_conflict(
    monkeypatch,
) -> None:
    config = _test_config()
    store, revocation, _, _ = _enrolled()
    second_id = uuid.uuid4()
    second_identity = qual.ValidatedIdentity(
        issuer="moonmind-accounts", subject="other@example.com"
    )
    store.enroll(
        second_identity,
        qual.AccountRecord(user_id=second_id, is_active=True),
    )
    _wire_session_boundary(
        monkeypatch, store=store, revocation=revocation, config=config
    )
    first_identity = qual.ValidatedIdentity(
        issuer="moonmind-accounts", subject="cutover@example.com"
    )
    cookie_token, _ = await qual.mint_moonmind_session(
        first_identity, store, config, revocation=revocation
    )
    bearer_token, _ = await qual.mint_moonmind_session(
        second_identity, store, config, revocation=revocation
    )
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(HTTPException) as exc:
        await providers_module.get_current_user()(
            _request(bearer=bearer_token, cookie=cookie_token), session
        )

    assert exc.value.status_code == 401
    assert exc.value.detail == {"code": "auth_conflict"}


@pytest.mark.asyncio
async def test_strict_inactive_persisted_user_is_forbidden(monkeypatch) -> None:
    config = _test_config()
    store, revocation, user_id, identity = _enrolled()
    _wire_session_boundary(
        monkeypatch, store=store, revocation=revocation, config=config
    )
    token, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    session = _db_session_for(user_id, active=False, admin=False)

    with pytest.raises(HTTPException) as exc:
        await providers_module.get_current_user()(_request(bearer=token), session)

    assert exc.value.status_code == 403
    assert exc.value.detail == {"code": "inactive"}


@pytest.mark.asyncio
async def test_strict_user_store_outage_is_unavailable(monkeypatch) -> None:
    config = _test_config()
    store, revocation, _, identity = _enrolled()
    _wire_session_boundary(
        monkeypatch, store=store, revocation=revocation, config=config
    )
    token, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    session = AsyncMock(spec=AsyncSession)
    session.get.side_effect = ConnectionError("db down")

    with pytest.raises(HTTPException) as exc:
        await providers_module.get_current_user()(_request(bearer=token), session)

    assert exc.value.status_code == 503
    assert exc.value.detail == "unavailable"


# ---------------------------------------------------------------------------
# Optional boundary: missing proceeds, invalid never becomes anonymous
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_optional_missing_credential_returns_none(monkeypatch) -> None:
    config = _test_config()
    store, revocation, _, _ = _enrolled()
    _wire_session_boundary(
        monkeypatch, store=store, revocation=revocation, config=config
    )
    session = AsyncMock(spec=AsyncSession)

    assert await providers_module.get_current_user_optional()(_request(), session) is None


@pytest.mark.asyncio
async def test_optional_invalid_credential_is_not_anonymous(monkeypatch) -> None:
    config = _test_config()
    store, revocation, _, _ = _enrolled()
    _wire_session_boundary(
        monkeypatch, store=store, revocation=revocation, config=config
    )
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(HTTPException) as exc:
        await providers_module.get_current_user_optional()(_request(bearer="bogus"), session)

    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# WebSocket principal resolution honors the same authority
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_websocket_session_token_resolves_and_legacy_fails(
    monkeypatch,
) -> None:
    from api_service.api.websockets import get_current_user_ws

    config = _test_config()
    store, revocation, user_id, identity = _enrolled()
    _wire_session_boundary(
        monkeypatch, store=store, revocation=revocation, config=config
    )
    token, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    session = _db_session_for(user_id, active=True, admin=False)

    user = await get_current_user_ws(token, None, session)
    assert user.id == user_id

    legacy = jwt.encode({"sub": "x"}, "legacy-jwt-secret", algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        await get_current_user_ws(legacy, None, AsyncMock(spec=AsyncSession))
    assert exc.value.status_code in {401, 503}
