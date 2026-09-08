"""Removal gate: legacy application-login routes stay unmounted (#4129).

The bundled Keycloak integration and its fastapi-users login/register/reset
routes were removed. No /api/v1/auth/* application-login route may remain
reachable through the app in any AUTH_PROVIDER mode. One-profile-per-user for
the surviving modes is owned by the #4124-era contracts, not by registration.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from api_service.main import app

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


def test_no_legacy_auth_route_mounted():
    legacy_prefixes = ("/api/v1/auth", "/auth/jwt", "/auth/register")
    mounted = sorted({route.path for route in app.routes if hasattr(route, "path")})
    for path in mounted:
        assert not path.startswith(legacy_prefixes), (
            f"legacy auth route still mounted: {path}"
        )


@pytest.mark.asyncio
async def test_legacy_register_route_not_reachable():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for path in (
            "/api/v1/auth/register",
            "/api/v1/auth/jwt/login",
            "/api/v1/auth/reset-password",
        ):
            resp = await client.post(
                path, json={"email": "user@example.com", "password": "pass"}
            )
            assert resp.status_code == 404, f"{path} unexpectedly reachable"
