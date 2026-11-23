"""SOC 2 discoverability, saved inputs and API expansion use the shared catalog."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api_service.api.routers.presets import router
from api_service.db.base import get_async_session
from api_service.services.presets.catalog import PresetCatalogService
from api_service.services.single_user_conversion import run_guarded_upgrade
from moonmind.config.settings import settings
from moonmind.security.assessments import AssessmentRequest
from tests.unit.api.test_presets_service import template_db
from tests.unit.security.test_soc2_readiness import documentary_request, selection

ROOT = Path(__file__).resolve().parents[3]
SLUG = "security-soc2-readiness"
pytestmark = pytest.mark.asyncio


async def seed(session, tmp_path):
    await run_guarded_upgrade(session, operator_authorized=True)
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir(exist_ok=True)
    source = ROOT / "api_service/data/presets/security-soc2-readiness.yaml"
    (seed_dir / source.name).write_text(source.read_text())
    await PresetCatalogService(session).import_seed_templates(seed_dir=seed_dir)


async def test_soc2_catalog_api_and_saved_system_inputs(tmp_path, monkeypatch):
    monkeypatch.delenv("MOONMIND_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr(settings.feature_flags, "preset_catalog", True)
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
            # Typed profile fields replace the free-form JSON editor.
            assert "system_inputs" not in (detail["uiSchema"] or {})
            system = detail["inputSchema"]["properties"]["system_inputs"]
            assert system["additionalProperties"] is False
            assert {
                "assessmentType",
                "requirementSamples",
                "subserviceOrganisations",
            } <= set(system["properties"])
            assert "requirement_choices" in detail["inputSchema"]["properties"]
            values = {
                "system_inputs": {
                    **selection().system_inputs,
                    "requirementEvidence": {"CC6.1": [documentary_request()]},
                },
                "period": selection().period.model_dump(mode="json"),
                "assets": ["api"],
                "requirement_choices": {
                    "CC6.1": {
                        "systems": ["api"],
                        "period": selection().period.model_dump(mode="json"),
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
            # The saved framework.packId shape normalizes once to the installed
            # pack: system inputs become its profile and saved documentary
            # evidence moves to its requirement's choice, without loss.
            assert parsed.framework is None
            [chosen] = parsed.selected_packs
            saved_system = dict(values["system_inputs"])
            saved_evidence = saved_system.pop("requirementEvidence")
            assert chosen.profile == saved_system
            choice = chosen.requirement_choices["CC6.1"]
            assert choice.systems == ["api"]
            assert [
                item.model_dump(mode="json", by_alias=True, exclude_none=True)
                for item in choice.organisational_evidence
            ] == saved_evidence["CC6.1"]
            assert chosen.audit_ref is None
            assert (
                expanded["appliedTemplate"]["inputs"]["system_inputs"]
                == values["system_inputs"]
            )
            # Save a normal instance preset with these defaults, then expand with
            # omitted inputs. The same shared path owns saved system choices.
            saved = await client.post(
                "/api/presets",
                json={
                    "slug": "saved-soc2",
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
                "/api/presets/saved-soc2:expand", json={"inputs": {}}
            )
            assert again.status_code == 200, again.text
            assert again.json()["steps"][0]["tool"]["inputs"] == payload
