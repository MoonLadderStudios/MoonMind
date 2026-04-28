import pytest
from httpx import ASGITransport, AsyncClient

from api_service.main import app


@pytest.mark.asyncio
async def test_settings_catalog_endpoint_returns_grouped_descriptors():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/catalog",
            params={"section": "user-workspace", "scope": "workspace"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["section"] == "user-workspace"
    assert body["scope"] == "workspace"
    descriptor = next(
        item
        for item in body["categories"]["Workflow"]
        if item["key"] == "workflow.default_task_runtime"
    )
    assert descriptor["type"] == "enum"
    assert descriptor["ui"] == "select"
    assert descriptor["source"] in {"config_or_default", "environment"}
    assert descriptor["audit"] == {
        "store_old_value": True,
        "store_new_value": True,
        "redact": False,
    }


@pytest.mark.asyncio
async def test_effective_setting_endpoint_returns_structured_unknown_key_error():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/effective/workflow.github_token",
            params={"scope": "workspace"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": "unknown_setting",
        "message": "Unknown setting: workflow.github_token.",
        "key": "workflow.github_token",
        "scope": "workspace",
        "details": {},
    }


@pytest.mark.asyncio
async def test_settings_write_to_unexposed_key_returns_setting_not_exposed():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.github_token": "raw-token"},
                "expected_versions": {},
                "reason": "attempt to mutate an unexposed field",
            },
        )

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "setting_not_exposed"
    assert body["key"] == "workflow.github_token"
    assert body["scope"] == "workspace"
    assert "raw-token" not in response.text


@pytest.mark.asyncio
async def test_effective_settings_endpoint_filters_by_scope():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/settings/effective",
            params={"scope": "user"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "user"
    assert list(body["values"]) == ["integrations.github.token_ref"]
