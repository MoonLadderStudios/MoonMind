"""Shared catalog and admitted API coverage for ISO readiness."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api_service.api.routers.presets import router
from api_service.db.base import get_async_session
from api_service.services.presets.catalog import (
    PresetCatalogService,
    PresetValidationError,
)
from api_service.services.single_user_conversion import run_guarded_upgrade
from moonmind.config.settings import settings
from moonmind.security.assessments import AssessmentRequest
from tests.unit.api.test_presets_service import template_db
from tests.unit.security.test_iso_27001 import selection


def fixture():
    return {"framework": selection().model_dump(mode="json", by_alias=True)}


ROOT = Path(__file__).resolve().parents[3]
SLUG = "security-iso-27001"
pytestmark = pytest.mark.asyncio


async def seed(session, tmp_path):
    await run_guarded_upgrade(session, operator_authorized=True)
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir(exist_ok=True)
    source = ROOT / "api_service/data/presets/security-iso-27001.yaml"
    (seed_dir / source.name).write_text(source.read_text())
    await PresetCatalogService(session).import_seed_templates(seed_dir=seed_dir)


async def test_iso_catalog_api_and_saved_system_inputs(tmp_path, monkeypatch):
    monkeypatch.delenv("MOONMIND_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr(settings.feature_flags, "preset_catalog", True)
    monkeypatch.setattr(settings.feature_flags, "disable_preset_catalog", False)
    async with template_db(tmp_path) as maker:
        async with maker() as session:
            await seed(session, tmp_path)

        async def sessions():
            async with maker() as session:
                yield session

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_async_session] = sessions
        async with AsyncClient(
            transport=ASGITransport(app=app, client=("127.0.0.1", 12345)),
            base_url="http://localhost",
        ) as client:
            listed = await client.get("/api/presets")
            assert listed.status_code == 200
            assert SLUG in {p["slug"] for p in listed.json()["items"]}
            detail = (await client.get(f"/api/presets/{SLUG}")).json()
            assert "system_inputs" in detail["inputSchema"]["properties"]
            assert "requirement_choices" in detail["inputSchema"]["properties"]
            values = {
                "system_inputs": fixture()["framework"]["systemInputs"],
                "period": fixture()["framework"]["period"],
                "assets": ["isms"],
                "requirement_choices": {
                    "9.2": {
                        "systems": ["isms"],
                        "period": fixture()["framework"]["period"],
                    }
                },
            }
            response = await client.post(
                f"/api/presets/{SLUG}:expand", json={"inputs": values}
            )
            assert response.status_code == 200, response.text
            expanded = response.json()
            payload = expanded["steps"][0]["tool"]["inputs"]
            parsed = AssessmentRequest.model_validate(payload)
            assert parsed.framework.system_inputs == values["system_inputs"]
            assert parsed.framework.requirement_choices["9.2"].systems == ["isms"]
            assert parsed.framework.audit_ref is None
            assert (
                expanded["appliedTemplate"]["inputs"]["system_inputs"]
                == values["system_inputs"]
            )
            # Save a normal instance preset with these defaults, then expand with
            # omitted inputs. The same shared path owns saved system choices.
            saved = await client.post(
                "/api/presets",
                json={
                    "slug": "saved-iso",
                    "title": "Saved audit system",
                    "description": "Saved inputs",
                    "steps": detail["steps"],
                    "annotations": {
                        "inputSchema": detail["inputSchema"],
                        "uiSchema": detail["uiSchema"],
                        "defaults": dict(detail["defaults"], **values),
                    },
                    "releaseStatus": "active",
                },
            )
            assert saved.status_code == 201, saved.text
            again = await client.post(
                "/api/presets/saved-iso:expand", json={"inputs": {}}
            )
            assert again.status_code == 200, again.text
            assert again.json()["steps"][0]["tool"]["inputs"] == payload


@pytest.mark.parametrize("assets", ["isms", [], [42], ["ghp_" + "x" * 36]])
async def test_catalog_rejects_invalid_or_sensitive_asset_arrays(tmp_path, assets):
    async with template_db(tmp_path) as maker, maker() as session:
        await seed(session, tmp_path)
        with pytest.raises(PresetValidationError):
            await PresetCatalogService(session).expand_template(
                slug=SLUG,
                inputs={
                    "assets": assets,
                    "system_inputs": {},
                    "period": fixture()["framework"]["period"],
                },
            )
