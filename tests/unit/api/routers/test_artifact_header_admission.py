"""Real ingress admission composes with artifact recovery without a person."""

import httpx
import pytest

from api_service.services import single_user_conversion as conversion
from tests.unit.api.routers.test_artifact_conversion_recovery import mounted_app
from tests.unit.services.test_artifact_conversion import database, seed


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["X-Moonmind-User", "X-Verified-Subject"])
async def test_ingress_artifact_admission_keeps_transport_and_recovery_scopes(tmp_path, monkeypatch, header):
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "https://operator.example")
    monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", "127.0.0.1")
    monkeypatch.setenv("MOONMIND_TRUSTED_INGRESS", "1")
    monkeypatch.setenv("MOONMIND_TRUSTED_PROXIES", "10.20.0.5")
    monkeypatch.setenv("MOONMIND_PROXY_IDENTITY_HEADER", header)
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, _owner = await seed(session, tmp_path)
            assert (await conversion.run_guarded_upgrade(session, operator_authorized=True)).published
        async with maker() as session:
            app = mounted_app(session, tmp_path)
            transport = httpx.ASGITransport(app=app, client=("10.20.0.5", 1234))
            async with httpx.AsyncClient(transport=transport, base_url="https://operator.example") as client:
                # Different ingress assertions are boolean permission, never local people.
                for assertion in ("ingress-operator", "other-valid-assertion"):
                    response = await client.get(f"/api/artifacts/{artifact_id}/download", headers={header: assertion})
                    assert response.status_code == 200, response.text
                    assert response.content == b"retained bytes"
                for denied in ({}, {header: ""}, {header: "local"},
                    {header: "ingress-operator", "Authorization": "Bearer retired-login"},
                    {header: "ingress-operator", "X-MoonMind-Execution-Fanout": "v1"}):
                    response = await client.get(f"/api/artifacts/{artifact_id}/download", headers=denied)
                    assert response.status_code == 401, response.text
            direct = httpx.ASGITransport(app=app, client=("10.20.0.9", 1234))
            async with httpx.AsyncClient(transport=direct, base_url="https://operator.example") as client:
                response = await client.get(f"/api/artifacts/{artifact_id}/download", headers={header: "forged", "X-Forwarded-For": "10.20.0.5"})
                assert response.status_code == 401, response.text
