"""Operator-only consumers retain real sessions without machine fallback."""

import time

import pytest
from fastapi import HTTPException

from api_service.auth_providers import get_instance_operator_access
from moonmind.security import omnigent_auth_qualification as qual
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
        user = await get_instance_operator_access(request, session)
        assert user.id == user_id
        assert user.is_superuser is False
    else:
        with pytest.raises(HTTPException) as exc:
            await get_instance_operator_access(request, session)
        assert exc.value.status_code == (403 if state == "inactive" else 401)


@pytest.mark.parametrize(
    "credential",
    [
        (b"authorization", b"Bearer invalid"),
        (b"x-moonmind-worker-token", b"invalid"),
        (b"x-moonmind-execution-fanout", b"v1"),
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
        await get_instance_operator_access(request, session)
    assert exc.value.status_code == 401
    session.get.assert_not_awaited()
