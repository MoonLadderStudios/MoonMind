from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from api_service.api.routers import settings as settings_router
from api_service.main import app
from api_service.services.settings_catalog import (
    SettingRegistryEntry,
    SettingsCatalogService,
)


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.asyncio
async def test_effective_values_contract_reports_metadata_and_operator_lock(monkeypatch):
    configured_entry = SettingRegistryEntry(
        key="test.configured",
        title="Configured",
        category="Test",
        section="user-workspace",
        value_type="string",
        ui="input",
        scopes=("workspace",),
        default_value="built-in",
        settings_path=("settings", "configured"),
        apply_mode="worker_reload",
        requires_reload=True,
        applies_to=("worker", "task_creation"),
        order=1,
    )
    locked_entry = SettingRegistryEntry(
        key="test.locked",
        title="Locked",
        category="Test",
        section="user-workspace",
        value_type="string",
        ui="input",
        scopes=("workspace",),
        default_value="built-in",
        operator_locked_value="operator-value",
        operator_lock_reason="Controlled by operator policy.",
        order=2,
    )

    def _factory(*args, **kwargs):
        kwargs["registry"] = (configured_entry, locked_entry)
        kwargs["settings"] = SimpleNamespace(
            settings=SimpleNamespace(configured="from-config")
        )
        kwargs["env"] = {}
        return SettingsCatalogService(*args, **kwargs)

    monkeypatch.setattr(settings_router, "SettingsCatalogService", _factory)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        effective = await client.get(
            "/api/v1/settings/effective",
            params={"scope": "workspace"},
        )
        diagnostics = await client.get(
            "/api/v1/settings/diagnostics",
            params={"scope": "workspace", "key": "test.locked"},
        )

    assert effective.status_code == 200
    values = effective.json()["values"]
    assert values["test.configured"]["source"] == "config_file"
    assert values["test.configured"]["default_value"] == "built-in"
    assert values["test.configured"]["inheritance_state"] == "inherited"
    assert values["test.configured"]["requires_reload"] is True
    assert values["test.configured"]["applies_to"] == ["worker", "task_creation"]
    assert values["test.locked"]["source"] == "operator_lock"
    assert values["test.locked"]["read_only"] is True
    assert values["test.locked"]["read_only_reason"] == "Controlled by operator policy."
    assert diagnostics.status_code == 200
    locked = diagnostics.json()["values"]["test.locked"]
    assert locked["source"] == "operator_lock"
    assert locked["read_only"] is True
