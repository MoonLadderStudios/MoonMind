"""Preset journey contracts on both sides of the account-era upgrade."""

from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest

from tools import single_user_journey_checks as journey


def _legacy_digest(template):
    """The catalog digest contract of the journey's 2c67aeb9 source release."""
    payload = {
        "slug": template.slug,
        "scope": "global",
        "scopeRef": None,
        "inputs": template.inputs_schema,
        "steps": template.steps,
        "annotations": template.annotations,
        "requiredCapabilities": template.required_capabilities,
        "maxStepCount": template.max_step_count,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


class PresetJourneyApi:
    """Strict predecessor routing; unrelated populate outcomes are already covered."""

    def __init__(self, *, legacy=True):
        self.legacy = legacy
        self.preset = {}
        self.expanded = {}
        self.calls = []
        self.request_id = ""
        self.template = None

    def json(self, method, path, *, body=None, headers=None, **_kwargs):
        self.calls.append((method, path, copy.deepcopy(body)))
        route = urlsplit(path)
        if route.path == "/api/ui/info":
            return {"dashboardConfig": {"system": {"defaultRepository": "o/r"}}}
        if route.path == "/api/omnigent/agent-profiles":
            return [
                {
                    "defaultForRuntime": True,
                    "state": "active",
                    "activeVersion": 1,
                    "versions": [{"version": 1, "validationResult": {"ready": True}}],
                }
            ]
        if route.path == "/api/v1/settings/catalog":
            return {"sections": ["ok"]}
        if route.path == "/api/presets" and method == "GET":
            return {"items": []}
        if route.path == "/api/executions":
            return {"workflowId": "mm:journey", "runId": "run-1"}
        if route.path.endswith("/artifacts") and method == "GET":
            return {"items": [{"artifactId": "artifact-1"}]}
        if route.path.startswith("/api/executions/"):
            return {
                "workflowId": "mm:journey",
                "runId": "run-1",
                "status": "scheduled",
                "scheduledFor": "2026-12-01T00:00:00+00:00",
            }
        if route.path == "/api/artifacts":
            return {"artifact_ref": {"artifact_id": "artifact-1"}}
        if route.path == "/api/recurring-workflows":
            return {"id": "recurring-1"}
        if route.path.endswith("/run"):
            self.request_id = headers["Idempotency-Key"]
            return {}
        if route.path.endswith("/runs"):
            return {
                "items": [{"id": self.request_id, "temporalWorkflowId": "mm:recurring"}]
            }
        if route.path == "/api/presets" and method == "POST":
            from api_service.api.schemas import (
                PresetCreateRequestSchema,
                PresetResponseSchema,
            )
            from api_service.services.presets.catalog import (
                PresetCatalogService,
                _preset_digest,
            )

            # Source creation defaults to personal. The journey must explicitly
            # request global, rather than accidentally change the saved fixture.
            source_scope = body.get("scope", "personal")
            payload = PresetCreateRequestSchema.model_validate(
                {key: value for key, value in body.items() if key != "scope"}
            )
            service = PresetCatalogService(None)
            self.template = SimpleNamespace(
                slug=payload.slug,
                inputs_schema=service._validate_inputs_schema(
                    [item.model_dump(by_alias=True) for item in payload.inputs]
                ),
                steps=service._validate_template_steps(
                    [item.model_dump(by_alias=True) for item in payload.steps]
                ),
                annotations={},
                required_capabilities=[],
                max_step_count=25,
            )
            self.preset = PresetResponseSchema.model_validate(
                {
                    **body,
                    "steps": self.template.steps,
                    "presetDigest": _legacy_digest(self.template)
                    if self.legacy
                    else _preset_digest(self.template),
                    "releaseStatus": "active" if self.legacy else "draft",
                }
            ).model_dump(by_alias=True)
            if self.legacy:
                self.preset.update(scope=source_scope, scopeRef=None)
                for step in self.preset["steps"]:
                    step["scope"] = None
            return copy.deepcopy(self.preset)
        if route.path.startswith("/api/presets/"):
            assert parse_qs(route.query) == (
                {"scope": ["global"]} if self.legacy else {}
            )
            if method == "GET":
                return copy.deepcopy(self.preset)
            if route.path.endswith(":expand"):
                from api_service.services.presets.catalog import _build_step_id

                inputs = body["inputs"]
                step_id = _build_step_id(
                    slug=self.preset["slug"], index=1, inputs=inputs
                )
                return copy.deepcopy(
                    self.expanded
                    or {
                        "steps": [
                            {
                                "id": step_id,
                                "instructions": f"Write a report for {inputs['audience']}.",
                            }
                        ],
                        "appliedTemplate": {
                            "slug": self.preset["slug"],
                            "presetDigest": self.preset["presetDigest"],
                            "inputs": inputs,
                            "stepIds": [step_id],
                        },
                    }
                )
        raise AssertionError((method, path, body))

    def request(self, method, path, **_kwargs):
        assert method == "PUT" and path == "/api/artifacts/artifact-1/content"
        return 200, b""


def _populate(api):
    state = {}
    journey.populate(
        api,
        state,
        label="before-upgrade",
        timeout=1,
        defer_seconds=150,
        pre_upgrade_release=api.legacy,
    )
    return state


def test_source_populate_and_verify_use_only_legacy_global_contract():
    api = PresetJourneyApi()
    state = _populate(api)
    source_snapshot = copy.deepcopy(state)

    journey.verify_preset(api, state, pre_upgrade_release=True)

    assert state == source_snapshot
    assert (
        next(
            body
            for method, path, body in api.calls
            if method == "POST" and path == "/api/presets"
        )["scope"]
        == "global"
    )
    assert state["presetApplication"]["stepIds"]


@pytest.mark.asyncio
async def test_real_conversion_preserves_content_and_ids_with_exact_digest_transition(
    tmp_path,
):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from api_service.api.schemas import PresetExpandResponseSchema, PresetResponseSchema
    from api_service.db.models import Preset, PresetReleaseStatus, PresetScopeType
    from api_service.services.presets.catalog import (
        PresetCatalogService,
        _preset_digest,
    )
    from api_service.services.single_user_conversion import run_guarded_upgrade
    from tests.helpers.account_source import User, create_source_schema

    api = PresetJourneyApi()
    state = _populate(api)
    saved = copy.deepcopy(state)
    template_id = UUID("00000000-0000-0000-0000-000000000123")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/preset-upgrade.db")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(create_source_schema)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            owner = User(id=UUID(int=321), email="journey@example.invalid")
            session.add(owner)
            await session.flush()
            template = Preset(
                created_by=owner.id,
                id=template_id,
                slug=api.template.slug,
                scope_type=PresetScopeType.GLOBAL,
                title=api.preset["title"],
                description=api.preset["description"],
                inputs_schema=api.template.inputs_schema,
                steps=api.template.steps,
                annotations={},
                required_capabilities=[],
                tags=[],
                max_step_count=25,
                release_status=PresetReleaseStatus.ACTIVE,
            )
            session.add(template)
            await session.commit()
            assert _legacy_digest(template) == saved["preset"]["presetDigest"]
            assert (
                await run_guarded_upgrade(session, operator_authorized=True)
            ).published
            session.expire_all()
            migrated = (await session.execute(select(Preset))).scalar_one()
            assert migrated.id == template_id
            assert migrated.scope_type is None and migrated.scope_ref is None
            assert state["presetInstanceDigest"] == _preset_digest(migrated)
            assert state["presetInstanceDigest"] != saved["preset"]["presetDigest"]
            service = PresetCatalogService(session)
            api.legacy = False
            api.preset = PresetResponseSchema.model_validate(
                await service.get_template(slug=migrated.slug)
            ).model_dump(by_alias=True)
            api.expanded = PresetExpandResponseSchema.model_validate(
                await service.expand_template(
                    slug=migrated.slug, inputs=state["presetApplication"]["inputs"]
                )
            ).model_dump(by_alias=True)
            journey.verify_preset(api, state)
            # Source evidence and already-admitted application IDs are not rewritten.
            assert state == saved
    finally:
        await engine.dispose()


@pytest.mark.parametrize("stage", ["fresh", "upgraded"])
@pytest.mark.parametrize("location", ["preset", "step", "application"])
@pytest.mark.parametrize("key", ["scope", "scopeRef", "scope_ref"])
def test_candidate_never_accepts_retired_fields(stage, location, key):
    api = PresetJourneyApi(legacy=stage == "upgraded")
    state = _populate(api)
    if api.legacy:
        api.legacy = False
        api.preset.pop("scope")
        api.preset.pop("scopeRef")
        for step in api.preset["steps"]:
            step.pop("scope")
        api.preset["presetDigest"] = state["presetInstanceDigest"]
    if location == "application":
        api.expanded = api.json(
            "POST",
            f"/api/presets/{api.preset['slug']}:expand",
            body={"inputs": state["presetApplication"]["inputs"]},
        )
        api.expanded["appliedTemplate"][key] = None
    elif location == "step":
        api.preset["steps"][0][key] = None
    else:
        api.preset[key] = None
    with pytest.raises(journey.JourneyFailure, match="retired human scope"):
        journey.verify_preset(api, state)


@pytest.mark.parametrize(
    "field",
    [
        "presetDigest",
        "title",
        "description",
        "steps",
        "inputs",
        "annotations",
        "requiredCapabilities",
        "stepIds",
    ],
)
def test_migrated_preset_never_rebaselines_changed_content_or_identity(field):
    api = PresetJourneyApi()
    state = _populate(api)
    api.legacy = False
    api.preset.pop("scope")
    api.preset.pop("scopeRef")
    for step in api.preset["steps"]:
        step.pop("scope")
    api.preset["presetDigest"] = state["presetInstanceDigest"]
    saved = copy.deepcopy(state)
    if field == "stepIds":
        api.expanded = api.json(
            "POST",
            f"/api/presets/{api.preset['slug']}:expand",
            body={"inputs": state["presetApplication"]["inputs"]},
        )
        api.expanded["steps"][0]["id"] = "replacement-step"
        api.expanded["appliedTemplate"]["stepIds"] = ["replacement-step"]
    elif field == "steps":
        api.preset["steps"][0]["skill"]["args"] = {"lost": "constraint"}
    elif field == "inputs":
        api.preset["inputs"][0]["default"] = "changed default"
    elif field == "annotations":
        api.preset[field] = {"changed": True}
    elif field == "requiredCapabilities":
        api.preset[field] = ["changed"]
    else:
        api.preset[field] = "changed"
    with pytest.raises(journey.JourneyFailure, match="changed"):
        journey.verify_preset(api, state)
    assert state == saved


@pytest.mark.asyncio
async def test_fresh_journey_digest_matches_real_catalog_create_and_expansion(tmp_path):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from api_service.api.schemas import (
        PresetCreateRequestSchema,
        PresetExpandResponseSchema,
        PresetResponseSchema,
    )
    from api_service.db.models import Base, PresetReleaseStatus
    from api_service.services.presets.catalog import PresetCatalogService

    api = PresetJourneyApi(legacy=False)
    state = _populate(api)
    request = next(
        body
        for method, path, body in api.calls
        if method == "POST" and path == "/api/presets"
    )
    assert "scope" not in request
    payload = PresetCreateRequestSchema.model_validate(request)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/preset-fresh.db")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            service = PresetCatalogService(session)
            created = await service.create_template(
                slug=payload.slug,
                title=payload.title,
                description=payload.description,
                tags=payload.tags,
                inputs_schema=[
                    item.model_dump(by_alias=True) for item in payload.inputs
                ],
                steps=[item.model_dump(by_alias=True) for item in payload.steps],
                annotations=payload.annotations,
                required_capabilities=payload.required_capabilities,
                release_status=PresetReleaseStatus(payload.release_status),
            )
            api.preset = PresetResponseSchema.model_validate(created).model_dump(
                by_alias=True
            )
            api.expanded = PresetExpandResponseSchema.model_validate(
                await service.expand_template(
                    slug=payload.slug, inputs=state["presetApplication"]["inputs"]
                )
            ).model_dump(by_alias=True)
            assert created["presetDigest"] == journey.journey_preset_digest(request)
            journey.verify_preset(api, state)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("mismatch", ["personal", "missing", "scopeRef", "step"])
def test_source_phase_requires_its_known_global_contract(mismatch):
    api = PresetJourneyApi()
    state = _populate(api)
    if mismatch == "personal":
        api.preset["scope"] = "personal"
    elif mismatch == "missing":
        api.preset.pop("scope")
    elif mismatch == "scopeRef":
        api.preset["scopeRef"] = "someone"
    else:
        api.preset["steps"][0]["scope"] = "personal"
    with pytest.raises(journey.JourneyFailure, match="global scope"):
        journey.verify_preset(api, state, pre_upgrade_release=True)


def test_pre_upgrade_flag_never_weakens_candidate_verification():
    api = PresetJourneyApi(legacy=False)
    state = _populate(api)
    # Calling the wrong stage also fails, rather than accepting either shape.
    api.legacy = True  # Let the request reach the response-shape assertion.
    with pytest.raises(journey.JourneyFailure, match="global scope"):
        journey.verify_preset(api, state, pre_upgrade_release=True)
