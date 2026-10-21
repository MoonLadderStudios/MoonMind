"""Real account-free instance catalog journeys."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from api_service.db.models import Preset, PresetFavorite, PresetRecent, PresetScopeType
from api_service.services.presets.catalog import (
    PresetCatalogService,
    PresetConflictError,
    PresetNotFoundError,
)
from api_service.services.presets.save import PresetSaveService
from tests.unit.api.test_presets_service import template_db

pytestmark = pytest.mark.asyncio


async def test_instance_catalog_roundtrip_without_person_or_scope(tmp_path):
    async with template_db(tmp_path) as maker:
        async with maker() as session:
            catalog = PresetCatalogService(session)
            created = await catalog.create_template(
                slug="instance-demo",
                title="Demo",
                description="Demo",
                tags=[],
                inputs_schema=[
                    {
                        "name": "flag",
                        "label": "Flag",
                        "type": "boolean",
                        "default": True,
                    },
                    {
                        "name": "text",
                        "label": "Text",
                        "type": "text",
                        "default": "fallback",
                    },
                ],
                steps=[
                    {"instructions": "Flag={{ inputs.flag }} text={{ inputs.text }}"}
                ],
            )
            assert "scope" not in created and "scopeRef" not in created
            await catalog.set_favorite(slug="instance-demo")
            await catalog.set_favorite(slug="instance-demo")
            expanded = await catalog.expand_template(
                slug="instance-demo", inputs={"flag": False, "text": ""}
            )
            assert expanded["appliedTemplate"]["inputs"] == {"flag": False, "text": ""}
            assert (await catalog.get_template(slug="instance-demo"))["isFavorite"]
            assert len(await catalog.list_templates(favorites_only=True)) == 1
            assert (
                len((await session.execute(select(PresetFavorite))).scalars().all())
                == 1
            )
            recent = (await session.execute(select(PresetRecent))).scalar_one()
            assert recent.user_id is None
            await catalog.clear_favorite(slug="instance-demo")
            assert await catalog.list_templates(favorites_only=True) == []
            with pytest.raises(PresetConflictError):
                await catalog.create_template(
                    slug="instance-demo",
                    title="Other",
                    description="Other",
                    tags=[],
                    inputs_schema=[],
                    steps=[{"instructions": "other"}],
                )


async def test_instance_catalog_excludes_unconverted_legacy_rows(tmp_path):
    async with template_db(tmp_path) as maker:
        async with maker() as session:
            session.add(
                Preset(
                    id=uuid4(),
                    slug="legacy",
                    title="Private retained",
                    description="Retained",
                    scope_type=PresetScopeType.PERSONAL,
                    scope_ref=str(uuid4()),
                    steps=[{"instructions": "retained"}],
                )
            )
            await session.commit()
            catalog = PresetCatalogService(session)
            assert await catalog.list_templates() == []
            with pytest.raises(PresetNotFoundError):
                await catalog.get_template(slug="legacy")


async def test_save_uses_instance_preferences_without_identity(tmp_path):
    async with template_db(tmp_path) as maker:
        async with maker() as session:
            saved = await PresetSaveService(session).save_from_workflow(
                title="Saved", description="Saved", steps=[{"instructions": "work"}]
            )
            detail = await PresetCatalogService(session).get_template(
                slug=saved["slug"]
            )
            assert detail["isFavorite"] and detail["recentAppliedAt"]


async def test_admitted_http_journey_does_not_load_person(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api_service.api.routers.presets import router
    from api_service.db.base import get_async_session
    from moonmind.config.settings import settings

    monkeypatch.delenv("MOONMIND_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr(settings.feature_flags, "preset_catalog", True)
    monkeypatch.setattr(settings.feature_flags, "disable_preset_catalog", False)
    async with template_db(tmp_path) as maker:

        async def session_dependency():
            async with maker() as session:
                yield session

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_async_session] = session_dependency
        async with AsyncClient(
            transport=ASGITransport(app=app, client=("127.0.0.1", 12345)),
            base_url="http://localhost",
        ) as client:
            response = await client.post(
                "/api/presets",
                json={
                    "slug": "http-demo",
                    "title": "HTTP",
                    "description": "Real HTTP",
                    "steps": [{"instructions": "work"}],
                    "releaseStatus": "active",
                },
            )
            assert response.status_code == 201, response.text
            assert response.json()["releaseStatus"] == "active"
            assert "scope" not in response.json()
            assert (
                await client.post("/api/presets/http-demo:favorite", json={})
            ).status_code == 204
            expanded = await client.post(
                "/api/presets/http-demo:expand", json={"inputs": {}}
            )
            assert expanded.status_code == 200, expanded.text
            listed = await client.get("/api/presets", params={"favoritesOnly": "true"})
            assert [i["slug"] for i in listed.json()["items"]] == ["http-demo"]
            assert listed.json()["items"][0]["recentAppliedAt"]
        async with AsyncClient(
            transport=ASGITransport(app=app, client=("10.1.2.3", 12345)),
            base_url="http://localhost",
        ) as denied:
            assert (await denied.get("/api/presets")).status_code == 401


async def test_concurrent_slug_conflict_is_an_actionable_conflict(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    async with template_db(tmp_path) as maker, maker() as session:
        catalog = PresetCatalogService(session)
        fields = dict(
            slug="collision",
            title="Original",
            description="Original",
            tags=[],
            inputs_schema=[],
            steps=[{"instructions": "original"}],
        )
        await catalog.create_template(**fields)
        original_execute = session.execute

        async def stale_precheck(statement, *args, **kwargs):
            # The writer observes no row, then the database unique constraint
            # observes the other writer. The insert and constraint are real.
            if str(statement).startswith("SELECT presets.id"):
                return SimpleNamespace(scalar_one_or_none=lambda: None)
            return await original_execute(statement, *args, **kwargs)

        monkeypatch.setattr(session, "execute", stale_precheck)
        with pytest.raises(PresetConflictError):
            await catalog.create_template(**{**fields, "title": "Competing"})
        monkeypatch.setattr(session, "execute", original_execute)
        assert (await catalog.get_template(slug="collision"))["title"] == "Original"


async def test_expiring_sessions_preserve_create_review_expand_responses(tmp_path):
    from api_service.db.models import PresetReleaseStatus

    async with template_db(tmp_path) as maker, maker() as session:
        session.sync_session.expire_on_commit = True
        catalog = PresetCatalogService(session)
        created = await catalog.create_template(
            slug="expiry",
            title="Expiry",
            description="Expiry",
            tags=[],
            inputs_schema=[],
            steps=[{"instructions": "work"}],
        )
        assert created["slug"] == "expiry"
        reviewed = await catalog.set_release_status(
            slug="expiry", release_status=PresetReleaseStatus.ACTIVE
        )
        assert reviewed["releaseStatus"] == "active"
        assert (await catalog.expand_template(slug="expiry", inputs={}))["steps"]


async def test_instance_recents_sort_by_latest_use(tmp_path):
    async with template_db(tmp_path) as maker, maker() as session:
        catalog = PresetCatalogService(session)
        from datetime import UTC, datetime

        for index, slug in enumerate(("older", "newer"), start=1):
            await catalog.create_template(
                slug=slug,
                title=slug,
                description=slug,
                tags=[],
                inputs_schema=[],
                steps=[{"instructions": "work"}],
            )
            await catalog.expand_template(slug=slug, inputs={})
            recent = (
                await session.execute(
                    select(PresetRecent).join(Preset).where(Preset.slug == slug)
                )
            ).scalar_one()
            recent.applied_at = datetime(2026, 10, index, tzinfo=UTC)
            await session.commit()
        assert [row["slug"] for row in await catalog.list_templates()] == [
            "newer",
            "older",
        ]


async def test_seed_sync_waits_for_guarded_conversion_without_duplicate(tmp_path):
    import yaml

    from api_service.services.presets.catalog import PresetValidationError

    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    (seed_dir / "seed.yaml").write_text(
        yaml.safe_dump(
            {
                "slug": "shipped",
                "title": "Fresh",
                "description": "Fresh",
                "steps": [{"instructions": "fresh"}],
            }
        )
    )
    async with template_db(tmp_path) as maker, maker() as session:
        old_id = uuid4()
        session.add(
            Preset(
                id=old_id,
                slug="shipped",
                title="Old",
                description="Old",
                scope_type=PresetScopeType.GLOBAL,
                seed_source="seed-catalog",
                steps=[{"instructions": "old"}],
            )
        )
        await session.commit()
        catalog = PresetCatalogService(session)
        for operation in (catalog.sync_seed_templates, catalog.import_seed_templates):
            with pytest.raises(PresetValidationError, match="guarded conversion"):
                await operation(seed_dir=seed_dir)
        rows = (await session.execute(select(Preset))).scalars().all()
        assert len(rows) == 1 and rows[0].id == old_id and rows[0].title == "Old"
