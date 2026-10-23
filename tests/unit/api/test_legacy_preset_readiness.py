"""Recorded readiness retries resolve attested identities, not current slug winners."""

from copy import deepcopy
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from api_service.db.models import Preset, PresetScopeType
from api_service.services.presets.catalog import PresetCatalogService
from api_service.services.single_user_conversion import (
    SingleUserConversionRun,
    run_guarded_upgrade,
)
from moonmind.workflows.executions.preset_readiness import SavedPresetCapabilitiesInput
from tests.helpers.account_source import User, create_source_schema
from tests.unit.api.test_presets_service import template_db

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def preset_sessions(tmp_path):
    async with template_db(tmp_path) as sessions:
        async with sessions() as session:
            connection = await session.connection()
            await connection.run_sync(create_source_schema)
            await session.commit()
        yield sessions


async def converted(session):
    owner = User(id=uuid4(), email="retained@example.invalid")
    session.add(owner)
    await session.flush()
    common = {"slug": "shared", "title": "Saved", "description": "Saved", "steps": []}
    global_row = Preset(
        **common,
        scope_type=PresetScopeType.GLOBAL,
        seed_source="shared.yaml",
        required_capabilities=["git"],
    )
    personal = Preset(
        **common,
        scope_type=PresetScopeType.PERSONAL,
        scope_ref=str(owner.id),
        created_by=owner.id,
        required_capabilities=["docker"],
    )
    winner = Preset(**common, required_capabilities=[])
    session.add_all([global_row, personal, winner])
    await session.commit()
    identity = str(owner.id), global_row.id, personal.id
    result = await run_guarded_upgrade(session, operator_authorized=True)
    assert result.published
    return identity


def request(owner, scope="global", capabilities=None):
    return SavedPresetCapabilitiesInput(
        principal=owner,
        definition_id="retained-definition",
        presets=[{"slug": "shared", "scope": scope}],
        required_capabilities=capabilities or [],
    )


@pytest.mark.parametrize(
    "scope,capability", [("global", "git"), ("personal", "docker")]
)
async def test_recorded_retry_uses_stable_converted_id_after_restart(
    preset_sessions, scope, capability
):
    sessions = preset_sessions
    async with sessions() as session:
        owner, _, _ = await converted(session)
    async with sessions() as session:
        check = request(owner, scope, [capability])
        before = deepcopy(check.model_dump())
        assert await PresetCatalogService(session).check_saved_capabilities(check) == {
            "status": "ready"
        }
        assert check.model_dump() == before
        stale = await PresetCatalogService(session).check_saved_capabilities(
            request(owner, scope)
        )
        assert stale["missingCapabilities"] == [capability]


@pytest.mark.parametrize(
    "damage",
    [
        "unpublished",
        "incomplete",
        "missing",
        "conflict",
        "deleted",
        "inactive",
        "malformed_id",
        "malformed_revision",
        "ineligible",
        "custom_transform",
        "missing_identity_field",
        "conflicting_receipt",
        "unconverted",
    ],
)
async def test_legacy_retry_never_substitutes_current_slug_without_identity_proof(
    preset_sessions, damage
):
    async with preset_sessions() as session:
        owner, global_id, _ = await converted(session)
        ledger = (await session.scalars(select(SingleUserConversionRun))).one()
        proof = deepcopy(ledger.result_json)
        if damage == "unpublished":
            proof["published"] = False
        elif damage == "incomplete":
            ledger.status = "in_progress"
        elif damage == "missing":
            proof["transforms"]["presets"].pop("legacy_identities", None)
        elif damage == "conflict":
            proof["transforms"]["presets"]["legacy_identities"].append(
                {
                    "scope": "global",
                    "scopeRef": None,
                    "slug": "shared",
                    "presetId": str(uuid4()),
                }
            )
        elif damage == "malformed_revision":
            proof["schema_revision"] = []
        elif damage == "ineligible":
            proof["decision"]["eligible"] = False
        elif damage == "custom_transform":
            proof["production_transforms_completed"] = False
        elif damage in {
            "malformed_id",
            "missing_identity_field",
            "conflicting_receipt",
        }:
            entries = proof["transforms"]["presets"]["legacy_identities"]
            entry = next(item for item in entries if item["scope"] == "global")
            if damage == "malformed_id":
                entry["presetId"] = "not-a-uuid"
            elif damage == "missing_identity_field":
                entry.pop("scopeRef")
            else:
                alternate = deepcopy(proof)
                alternate_entries = alternate["transforms"]["presets"][
                    "legacy_identities"
                ]
                next(item for item in alternate_entries if item["scope"] == "global")[
                    "presetId"
                ] = str(uuid4())
                session.add(
                    SingleUserConversionRun(
                        preflight_digest=uuid4().hex,
                        status="complete",
                        result_json=alternate,
                    )
                )
        else:
            target = await session.get(Preset, global_id)
            if damage == "deleted":
                await session.delete(target)
            elif damage == "unconverted":
                target.scope_type = PresetScopeType.GLOBAL
            else:
                assert damage == "inactive"
                target.is_active = False
        ledger.result_json = proof
        await session.commit()
        result = await PresetCatalogService(session).check_saved_capabilities(
            request(owner, capabilities=["git"])
        )
        assert result["status"] == "refresh_required"


async def test_new_scoped_request_and_mismatched_personal_provenance_still_refuse(
    preset_sessions,
):
    async with preset_sessions() as session:
        owner, _, _ = await converted(session)
        catalog = PresetCatalogService(session)
        for check in [
            request(None, capabilities=["git"]),
            request(str(uuid4()), "personal", ["docker"]),
        ]:
            result = await catalog.check_saved_capabilities(check)
            assert result["status"] == "refresh_required"
        check = request(owner, "personal", ["docker"])
        check.presets[0]["scopeRef"] = str(uuid4())
        assert (await catalog.check_saved_capabilities(check))[
            "status"
        ] == "refresh_required"
