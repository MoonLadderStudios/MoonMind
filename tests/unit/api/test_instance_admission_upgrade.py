"""Retained operator access survives an incomplete account-free conversion."""

import ipaddress
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api_service import auth_providers as auth
from api_service.db.models import User
from moonmind.security import auth_modes_4120
from moonmind.security.operator_admission import OperatorAdmission

pytestmark = pytest.mark.asyncio


@pytest.fixture
def retained_instance(monkeypatch):
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "")
    monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", "0.0.0.0")
    monkeypatch.setenv("MOONMIND_TRUSTED_INGRESS", "1")
    monkeypatch.delenv("MOONMIND_TRUSTED_PROXIES", raising=False)
    user = User(id=uuid4(), email="retained@example.invalid", is_active=True)
    monkeypatch.setattr(auth.settings.oidc, "DEFAULT_USER_ID", str(user.id))
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = user
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.execute.return_value = result
    return session, user, result


def request(peer="192.168.65.1"):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/workflows",
            "query_string": b"",
            "headers": [(b"host", b"localhost:7000")],
            "client": (peer, 43210),
            "server": ("api", 8000),
        }
    )


@pytest.mark.parametrize(
    "dependency", [auth.get_instance_access, auth.get_instance_access_optional]
)
@pytest.mark.parametrize("conversion_status", [None, "blocked", "running"])
async def test_incomplete_conversion_preserves_retained_access(
    retained_instance,
    dependency,
    conversion_status,
):
    session, user, result = retained_instance
    if conversion_status:
        result.scalars.return_value.all.return_value = [
            SimpleNamespace(status=conversion_status, result_json={"published": False})
        ]
    incoming = request()

    assert await dependency(incoming, session) is user
    session.get.assert_awaited_once_with(User, user.id)
    assert auth.get_operator_recovery_principal(incoming) is None


@pytest.mark.parametrize("failure", [None, ConnectionError("unavailable store")])
async def test_unconverted_instance_never_invents_an_operator(
    retained_instance, failure
):
    session, _user, _result = retained_instance
    session.get.return_value = None
    session.get.side_effect = failure
    with pytest.raises(HTTPException) as exc:
        await auth.get_instance_access(request(), session)
    assert exc.value.status_code == 503
    assert exc.value.detail == ("setup_required" if failure is None else "unavailable")


@pytest.mark.parametrize("published", [False, True])
async def test_only_published_conversion_switches_admission(
    retained_instance, published
):
    session, user, result = retained_instance
    result.scalars.return_value.all.return_value = [
        SimpleNamespace(
            status="complete",
            result_json={
                "published": published,
                "decision": {"disposition": "eligible_conversion"},
            },
        )
    ]
    if not published:
        assert await auth.get_instance_access(request(), session) is user
        return
    # Publishing conversion must not revoke the approved non-loopback ingress.
    assert isinstance(
        await auth.get_instance_access(request(), session), OperatorAdmission
    )
    session.get.assert_not_awaited()


@pytest.mark.parametrize(
    ("peer", "admitted"),
    [("192.168.0.4", True), ("172.19.0.1", True), ("172.19.0.7", False)],
)
async def test_approved_publication_rejects_bridge_peers_but_not_its_gateway(
    retained_instance, monkeypatch, peer, admitted
):
    from api_service import operator_admission as adapter

    session, _user, result = retained_instance
    result.scalars.return_value.all.return_value = [
        SimpleNamespace(
            status="complete",
            result_json={"published": True, "decision": {"disposition": "fresh_init"}},
        )
    ]
    monkeypatch.setattr(
        adapter,
        "_container_routes",
        lambda: (
            "172.19.0.1",
            (ipaddress.ip_network("172.19.0.0/16"), ipaddress.ip_network("172.20.0.0/16")),
        ),
    )
    if admitted:
        assert isinstance(
            await auth.get_instance_access(request(peer), session), OperatorAdmission
        )
        return
    with pytest.raises(HTTPException) as exc:
        await auth.get_instance_access(request(peer), session)
    assert exc.value.status_code == 401


async def test_published_conversion_keeps_loopback_publication_local(
    retained_instance, monkeypatch
):
    session, _user, result = retained_instance
    monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", "127.0.0.1")
    result.scalars.return_value.all.return_value = [
        SimpleNamespace(
            status="complete",
            result_json={"published": True, "decision": {"disposition": "fresh_init"}},
        )
    ]
    with pytest.raises(HTTPException) as exc:
        await auth.get_instance_access(request(), session)
    assert exc.value.status_code == 401
    session.get.assert_not_awaited()


async def test_converted_loopback_needs_no_retained_account(retained_instance):
    session, _user, result = retained_instance
    result.scalars.return_value.all.return_value = [
        SimpleNamespace(
            status="complete",
            result_json={
                "published": True,
                "decision": {"disposition": "fresh_init"},
            },
        )
    ]
    incoming = request("127.0.0.1")
    assert isinstance(
        await auth.get_instance_access(incoming, session), OperatorAdmission
    )
    assert auth.get_operator_recovery_principal(incoming) == "operator"
    session.get.assert_not_awaited()


@pytest.mark.parametrize("publish_host", ["127.0.0.1", "0.0.0.0"])
@pytest.mark.parametrize("header", [b"origin", b"referer"])
@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
async def test_retained_account_cannot_bypass_foreign_browser_origin(
    retained_instance,
    monkeypatch,
    publish_host,
    header,
    method,
):
    session, _user, _result = retained_instance
    monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", publish_host)
    incoming = request("127.0.0.1")
    incoming.scope["method"] = method
    incoming.scope["headers"].append((header, b"https://foreign.example"))
    with pytest.raises(HTTPException) as exc:
        await auth.get_instance_access(incoming, session)
    assert exc.value.status_code == 403
    assert exc.value.detail == {"code": "origin_forbidden"}
    session.get.assert_not_awaited()


@pytest.mark.parametrize("peer", ["172.21.0.9", "192.168.65.1"])
async def test_retained_local_account_does_not_admit_a_direct_backend_peer(
    retained_instance,
    monkeypatch,
    peer,
):
    session, _user, _result = retained_instance
    monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", "127.0.0.1")
    monkeypatch.delenv("MOONMIND_TRUSTED_INGRESS")
    incoming = request(peer)
    incoming.scope["headers"].extend(
        [
            (b"x-forwarded-for", b"127.0.0.1"),
            (b"x-moonmind-user", b"operator"),
        ]
    )
    with pytest.raises(HTTPException) as exc:
        await auth.get_instance_access(incoming, session)
    assert exc.value.status_code == 401
    session.get.assert_not_awaited()


async def test_retained_loopback_account_rejects_browser_rebinding(
    retained_instance, monkeypatch
):
    session, _user, _result = retained_instance
    monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", "127.0.0.1")
    incoming = request("127.0.0.1")
    incoming.scope["method"] = "POST"
    incoming.scope["headers"] = [
        (b"host", b"foreign.example"),
        (b"origin", b"http://foreign.example"),
    ]
    with pytest.raises(HTTPException) as exc:
        await auth.get_instance_access(incoming, session)
    assert exc.value.status_code == 401
    session.get.assert_not_awaited()


async def test_retained_remote_account_checks_configured_host(
    retained_instance, monkeypatch
):
    session, _user, _result = retained_instance
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "https://operator.example")
    with pytest.raises(HTTPException) as exc:
        await auth.get_instance_access(request(), session)
    assert exc.value.status_code == 403
    assert exc.value.detail == {"code": "host_forbidden"}
    session.get.assert_not_awaited()


async def test_retained_wildcard_account_requires_approved_ingress(
    retained_instance, monkeypatch
):
    session, _user, _result = retained_instance
    monkeypatch.delenv("MOONMIND_TRUSTED_INGRESS")
    with pytest.raises(HTTPException) as exc:
        await auth.get_instance_access(request(), session)
    assert exc.value.status_code == 401
    session.get.assert_not_awaited()


@pytest.mark.parametrize("trusted", ["1", "true", "yes"])
@pytest.mark.parametrize("configured_base", [False, True])
async def test_retained_approved_ingress_preserves_existing_browser_url(
    retained_instance,
    monkeypatch,
    trusted,
    configured_base,
):
    session, user, _result = retained_instance
    monkeypatch.setenv("MOONMIND_TRUSTED_INGRESS", trusted)
    if configured_base:
        monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "https://operator.example")
    incoming = request()
    incoming.scope["scheme"] = "http" if configured_base else "https"
    incoming.scope["method"] = "POST"
    incoming.scope["headers"] = [
        (b"host", b"operator.example"),
        (b"origin", b"https://operator.example"),
    ]
    assert await auth.get_instance_access(incoming, session) is user
    assert auth.get_operator_recovery_principal(incoming) is None


async def test_retained_local_operator_keeps_its_real_principal(
    retained_instance,
    monkeypatch,
):
    session, user, _result = retained_instance
    monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", "127.0.0.1")
    incoming = request("127.0.0.1")
    assert await auth.get_instance_access(incoming, session) is user
    assert auth.get_operator_recovery_principal(incoming) is None
