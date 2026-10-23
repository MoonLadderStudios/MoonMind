"""Operator-only consumers retain real sessions without machine fallback."""

import time
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import api_service.auth_providers as providers
from moonmind.security import auth_modes_4120 as auth_modes
from moonmind.security import omnigent_auth_qualification as qual
from moonmind.security.operator_admission import OperatorAdmission
from tests.unit.api.test_auth_session_cutover_4125 import (
    _db_session_for,
    _enrolled,
    _request,
    _test_config,
    _wire_session_boundary,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("transport", ["bearer", "cookie"])
@pytest.mark.parametrize("state", ["valid", "expired", "revoked", "inactive"])
async def test_real_session_authority_is_preserved(monkeypatch, transport, state):
    config = _test_config()
    store, revocation, user_id, identity = _enrolled()
    _wire_session_boundary(
        monkeypatch, store=store, revocation=revocation, config=config
    )
    token, _ = await qual.mint_moonmind_session(
        identity,
        store,
        config,
        now=int(time.time()) - config.session_ttl_seconds - 60
        if state == "expired"
        else None,
        revocation=revocation,
    )
    if state == "revoked":
        await revocation.revoke_all_for_user(user_id)
    session = _db_session_for(user_id, active=state != "inactive", admin=False)
    request = _request(**{transport: token})
    if state == "valid":
        user = await providers.get_instance_operator_access(request, session)
        assert user.id == user_id
        assert user.is_superuser is False
    else:
        with pytest.raises(HTTPException) as exc:
            await providers.get_instance_operator_access(request, session)
        assert exc.value.status_code == (403 if state == "inactive" else 401)


@pytest.mark.parametrize(
    "credential",
    [
        (b"authorization", b"Bearer invalid"),
        (b"x-moonmind-worker-token", b"invalid"),
        (b"x-moonmind-execution-fanout", b"v1"),
        (b"x-moonmind-mcp-tools", b"v1"),
        (b"x-moonmind-integration-token", b"invalid"),
    ],
)
async def test_machine_marker_cannot_fall_back_to_valid_operator_cookie(
    monkeypatch, credential
):
    config = _test_config()
    store, revocation, user_id, identity = _enrolled()
    _wire_session_boundary(
        monkeypatch, store=store, revocation=revocation, config=config
    )
    token, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    session = _db_session_for(user_id, active=True, admin=True)
    request = _request(cookie=token)
    request.scope["headers"].append(credential)
    with pytest.raises(HTTPException) as exc:
        await providers.get_instance_operator_access(request, session)
    assert exc.value.status_code == 401
    session.get.assert_not_awaited()


@pytest.mark.parametrize("mode", ["accounts", "oidc"])
@pytest.mark.parametrize("transport", ["bearer", "cookie"])
async def test_integration_token_cannot_fall_back_to_valid_session(
    monkeypatch, mode, transport
):
    config = _test_config()
    store, revocation, user_id, identity = _enrolled()
    _wire_session_boundary(
        monkeypatch, store=store, revocation=revocation, config=config
    )
    monkeypatch.setattr(auth_modes, "_ACTIVE_PRODUCTION_MODE", mode)
    token, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    session = _db_session_for(user_id, active=True, admin=False)
    allowed = await providers.get_instance_operator_access(
        _request(**{transport: token}), session
    )
    assert allowed.id == user_id
    assert allowed.is_superuser is False
    session.get.reset_mock()

    request = _request(**{transport: token})
    request.scope["headers"].append((b"x-moonmind-integration-token", b"invalid"))
    with pytest.raises(HTTPException) as exc:
        await providers.get_instance_operator_access(request, session)
    assert exc.value.status_code == 401
    assert exc.value.detail == {"code": "auth_invalid"}
    session.get.assert_not_awaited()


@pytest.mark.parametrize("mode", ["disabled", "accounts", "oidc", "header"])
@pytest.mark.parametrize("token", [b"", b"invalid"])
async def test_integration_header_presence_precedes_every_operator_authority(
    monkeypatch, mode, token
):
    monkeypatch.setattr(auth_modes, "_ACTIVE_PRODUCTION_MODE", mode)
    fallback = AsyncMock()
    monkeypatch.setattr(providers, "get_instance_access", fallback)
    request = _request()
    request.scope["headers"].append((b"x-moonmind-integration-token", token))
    with pytest.raises(HTTPException) as exc:
        await providers.get_instance_operator_access(request, AsyncMock())
    assert exc.value.status_code == 401
    assert exc.value.detail == {"code": "auth_invalid"}
    fallback.assert_not_awaited()


@pytest.mark.parametrize("via", ["loopback", "trusted_ingress"])
async def test_integration_token_cannot_fall_back_to_transport_admission(
    monkeypatch, via
):
    monkeypatch.setattr(auth_modes, "_ACTIVE_PRODUCTION_MODE", "disabled")
    monkeypatch.setattr(
        providers, "_disabled_account_free_allowed", AsyncMock(return_value=True)
    )
    for key in (
        "MOONMIND_PUBLIC_BASE_URL",
        "MOONMIND_TRUSTED_INGRESS",
        "MOONMIND_TRUSTED_PROXIES",
        "MOONMIND_PROXY_IDENTITY_HEADER",
        "MOONMIND_DEPLOYMENT_PROJECT_NAME",
    ):
        monkeypatch.delenv(key, raising=False)
    headers = [(b"host", b"localhost")]
    if via == "trusted_ingress":
        monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "https://operator.example")
        monkeypatch.setenv("MOONMIND_TRUSTED_INGRESS", "1")
        monkeypatch.setenv("MOONMIND_TRUSTED_PROXIES", "127.0.0.1/32")
        headers = [
            (b"host", b"operator.example"),
            (b"x-moonmind-user", b"verified-operator"),
        ]
    session = AsyncMock()
    request = _request()
    request.scope.update(client=("127.0.0.1", 12345), headers=headers)
    assert await providers.get_instance_operator_access(
        request, session
    ) == OperatorAdmission(via=via)

    denied = _request()
    denied.scope.update(
        client=("127.0.0.1", 12345),
        headers=[*headers, (b"x-moonmind-integration-token", b"invalid")],
    )
    with pytest.raises(HTTPException) as exc:
        await providers.get_instance_operator_access(denied, session)
    assert exc.value.status_code == 401
    assert exc.value.detail == {"code": "auth_invalid"}
    assert providers.get_operator_recovery_principal(denied) is None
    session.get.assert_not_awaited()
