"""Real shared product composition after account retirement (GH4354)."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import Request

from api_service import auth_providers as auth
from api_service import main
from api_service.db.base import get_async_session
from api_service.db.models import Base
from moonmind.security.operator_admission import OperatorAdmission


@pytest.fixture
def local_operator(monkeypatch):
    for name in (
        "AUTH_PROVIDER",
        "MOONMIND_PUBLIC_BASE_URL",
        "MOONMIND_API_PUBLISH_HOST",
        "MOONMIND_TRUSTED_INGRESS",
        "MOONMIND_DEPLOYMENT_PROJECT_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    from api_service.services import single_user_conversion

    monkeypatch.setattr(
        single_user_conversion,
        "completed_guarded_upgrade",
        AsyncMock(return_value=object()),
    )
    session = AsyncMock()
    session.get.side_effect = AssertionError(
        "ordinary requests cannot resolve a person"
    )
    ledger = MagicMock()
    ledger.scalars.return_value.all.return_value = [
        type(
            "Completion",
            (),
            {
                "status": "complete",
                "result_json": {
                    "published": True,
                    "decision": {"disposition": "fresh_init"},
                },
            },
        )()
    ]
    session.execute.return_value = ledger
    return session


@pytest.mark.asyncio
async def test_default_request_uses_admission_without_a_person(local_operator):
    incoming = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/workflows",
            "scheme": "http",
            "query_string": b"",
            "headers": [(b"host", b"localhost")],
            "client": ("127.0.0.1", 1234),
            "server": ("localhost", 80),
        }
    )
    assert isinstance(
        await auth.get_instance_access(incoming, local_operator), OperatorAdmission
    )
    local_operator.get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/me", "/api/v1/accounts/me", "/api/v1/oidc/login", "/api/v1/auth/oidc/login"],
)
async def test_retired_login_and_profile_http_paths_are_absent(local_operator, path):
    async def db():
        yield local_operator

    main.app.dependency_overrides[get_async_session] = db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://localhost"
        ) as client:
            response = await client.get(
                path, headers={"authorization": "Bearer old-login"}
            )
        assert response.status_code == 404, response.text
        local_operator.get.assert_not_awaited()
    finally:
        main.app.dependency_overrides.pop(get_async_session, None)


def test_fresh_product_schema_has_no_account_authority():
    # These exact retired storage surfaces supplied login and profile authority.
    assert {
        "user",
        "user_profile",
        "user_external_identities",
        "moonmind_sessions",
        "moonmind_user_session_generations",
        "moonmind_account_nonces",
    }.isdisjoint(Base.metadata.tables)
    assert not any(
        fk.target_fullname == "user.id"
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    ["authorization", "x-moonmind-worker-token", "x-moonmind-integration-token"],
)
async def test_presented_credentials_cannot_fall_back_to_operator(
    local_operator, header
):
    incoming = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/workflows",
            "scheme": "http",
            "query_string": b"",
            "headers": [
                (b"host", b"localhost"),
                (header.encode(), b"retired-or-machine-credential"),
            ],
            "client": ("127.0.0.1", 1234),
            "server": ("localhost", 80),
        }
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await auth.get_instance_access(incoming, local_operator)
    assert exc.value.status_code == 401
    local_operator.get.assert_not_awaited()
