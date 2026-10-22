"""Lossless conversion of the existing preset catalog into instance resources."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    Preset,
    PresetFavorite,
    PresetRecent,
    PresetScopeType,
    User,
)
from api_service.services.single_user_conversion import (
    SingleUserConversionRun,
    run_guarded_upgrade,
)


@pytest_asyncio.fixture
async def preset_sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/presets.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def seed_collision(session):
    owner = User(id=uuid4(), email="one@example.invalid")
    session.add(owner)
    await session.flush()
    builtin = Preset(
        id=UUID(int=1),
        slug="shared",
        title="Builtin",
        description="b",
        scope_type=PresetScopeType.GLOBAL,
        seed_source="builtin.yaml",
        steps=[{"instructions": "builtin"}],
    )
    custom = Preset(
        id=UUID(int=2),
        slug="shared",
        title="Custom",
        description="c",
        scope_type=PresetScopeType.PERSONAL,
        scope_ref=str(owner.id),
        created_by=owner.id,
        steps=[{"instructions": "custom"}],
    )
    parent = Preset(
        id=UUID(int=3),
        slug="parent",
        title="Parent",
        description="p",
        scope_type=PresetScopeType.PERSONAL,
        scope_ref=str(owner.id),
        created_by=owner.id,
        steps=[
            {
                "kind": "include",
                "slug": "shared",
                "alias": "child",
                "inputMapping": {"empty": "", "false": False},
            }
        ],
    )
    session.add_all([builtin, custom, parent])
    await session.flush()
    session.add(PresetFavorite(user_id=owner.id, template_id=custom.id))
    old = datetime(2026, 1, 1, tzinfo=UTC)
    session.add(PresetRecent(user_id=owner.id, template_id=custom.id, applied_at=old))
    await session.commit()
    return owner.id, builtin.id, custom.id, parent.id


@pytest.mark.asyncio
async def test_real_guarded_conversion_preserves_ids_and_resolves_only_collisions(
    preset_sessions,
):
    async with preset_sessions() as session:
        owner, builtin_id, custom_id, parent_id = await seed_collision(session)
        result = await run_guarded_upgrade(session, operator_authorized=True)
        assert result.published, result.decision.to_sanitized_dict()
        identities = [
            {
                "scope": "global",
                "scopeRef": None,
                "slug": "shared",
                "presetId": str(builtin_id),
            },
            {
                "scope": "personal",
                "scopeRef": str(owner),
                "slug": "shared",
                "presetId": str(custom_id),
            },
            {
                "scope": "personal",
                "scopeRef": str(owner),
                "slug": "parent",
                "presetId": str(parent_id),
            },
        ]
        assert result.transforms["presets"]["legacy_identities"] == identities
        ledger = (await session.execute(select(SingleUserConversionRun))).scalar_one()
        assert ledger.result_json["production_transforms_completed"] is True
        assert (
            ledger.result_json["transforms"]["presets"]["legacy_identities"]
            == identities
        )
        session.expire_all()
        builtin = await session.get(Preset, builtin_id)
        custom = await session.get(Preset, custom_id)
        parent = await session.get(Preset, parent_id)
        assert builtin.slug == "shared"
        assert custom.slug != builtin.slug
        assert custom.steps == [{"instructions": "custom"}]
        assert custom.created_by == owner  # history, never an admission key
        assert parent.slug == "parent"
        assert parent.steps[0]["slug"] == custom.slug
        assert parent.steps[0]["inputMapping"] == {"empty": "", "false": False}
        assert all(
            p.scope_type is None and p.scope_ref is None
            for p in (builtin, custom, parent)
        )
        favorite = (await session.execute(select(PresetFavorite))).scalar_one()
        recent = (await session.execute(select(PresetRecent))).scalar_one()
        assert favorite.template_id == recent.template_id == custom.id
        assert favorite.user_id is None and recent.user_id is None
        custom_slug = custom.slug
        await session.rollback()
    async with preset_sessions() as restarted:
        again = await run_guarded_upgrade(restarted, operator_authorized=True)
        assert again.published
        assert again.digest == result.digest
        assert again.transforms["presets"]["legacy_identities"] == identities
        assert (
            len(
                (await restarted.execute(select(SingleUserConversionRun)))
                .scalars()
                .all()
            )
            == 1
        )
        assert len((await restarted.execute(select(Preset))).scalars().all()) == 3
        assert (await restarted.get(Preset, custom_id)).slug == custom_slug


@pytest.mark.asyncio
async def test_multi_person_source_is_not_exposed(preset_sessions):
    async with preset_sessions() as session:
        _, _, custom_id, _ = await seed_collision(session)
        second = User(id=uuid4(), email="two@example.invalid")
        session.add(second)
        await session.flush()
        session.add(PresetFavorite(user_id=second.id, template_id=custom_id))
        await session.commit()
        result = await run_guarded_upgrade(session, operator_authorized=True)
        assert not result.published
        assert result.decision.reason_code == "multi_person"
        assert (
            await session.get(Preset, custom_id)
        ).scope_type is PresetScopeType.PERSONAL
        assert all(
            x.user_id is not None
            for x in (await session.execute(select(PresetFavorite))).scalars()
        )


@pytest.mark.asyncio
async def test_alias_preferences_deduplicate_keep_latest_and_earliest(preset_sessions):
    async with preset_sessions() as session:
        owner, _, custom_id, _ = await seed_collision(session)
        alias = User(id=uuid4(), email="alias@example.invalid")
        session.add(alias)
        await session.flush()
        latest = datetime(2026, 2, 1, tzinfo=UTC)
        first = datetime(2025, 1, 1, tzinfo=UTC)
        session.add(
            PresetFavorite(user_id=alias.id, template_id=custom_id, created_at=first)
        )
        session.add(
            PresetRecent(user_id=alias.id, template_id=custom_id, applied_at=latest)
        )
        await session.commit()
        result = await run_guarded_upgrade(
            session,
            operator_authorized=True,
            alias_groups=[{str(owner), str(alias.id)}],
        )
        assert result.published
        favorite = (await session.execute(select(PresetFavorite))).scalar_one()
        recent = (await session.execute(select(PresetRecent))).scalar_one()
        assert favorite.user_id is None and recent.user_id is None
        assert favorite.created_at.replace(tzinfo=UTC) == first
        assert recent.applied_at.replace(tzinfo=UTC) == latest


@pytest.mark.asyncio
async def test_interruption_rolls_back_then_retry_converges(preset_sessions):
    from api_service.services.single_user_conversion import (
        DEFAULT_SUBSYSTEM_TRANSFORMS,
        apply_conversion,
        preflight,
        presets_transform,
    )

    async with preset_sessions() as session:
        _, _, custom_id, _ = await seed_collision(session)
        bound = await preflight(session)

        async def interrupted(db, eligible):
            await presets_transform(db, eligible)
            raise RuntimeError("simulated interruption before publication")

        with pytest.raises(RuntimeError, match="simulated interruption"):
            await apply_conversion(
                session,
                bound,
                operator_authorized=True,
                transforms={**DEFAULT_SUBSYSTEM_TRANSFORMS, "presets": interrupted},
            )
    async with preset_sessions() as session:
        custom = await session.get(Preset, custom_id)
        assert custom.scope_type is PresetScopeType.PERSONAL
        assert custom.slug == "shared"
        assert (
            list((await session.execute(select(SingleUserConversionRun))).scalars())
            == []
        )
        await session.rollback()
        assert (await run_guarded_upgrade(session, operator_authorized=True)).published


@pytest.mark.asyncio
async def test_same_count_content_edit_invalidates_preflight(preset_sessions):
    from api_service.services.single_user_conversion import (
        DEFAULT_SUBSYSTEM_TRANSFORMS,
        StaleAttributionError,
        apply_conversion,
        preflight,
    )

    async with preset_sessions() as session:
        _, _, custom_id, _ = await seed_collision(session)
        bound = await preflight(session)
    async with preset_sessions() as writer:
        row = await writer.get(Preset, custom_id)
        row.steps = [{"instructions": "changed by concurrent writer"}]
        await writer.commit()
    async with preset_sessions() as session:
        with pytest.raises(StaleAttributionError):
            await apply_conversion(
                session,
                bound,
                operator_authorized=True,
                transforms=DEFAULT_SUBSYSTEM_TRANSFORMS,
            )
        assert (
            await session.get(Preset, custom_id)
        ).scope_type is PresetScopeType.PERSONAL


@pytest.mark.asyncio
async def test_missing_composition_target_refuses_without_substitution(preset_sessions):
    from api_service.services.single_user_conversion import StaleAttributionError

    async with preset_sessions() as session:
        _, _, custom_id, parent_id = await seed_collision(session)
        parent = await session.get(Preset, parent_id)
        parent.steps = [{"kind": "include", "slug": "missing", "alias": "x"}]
        await session.commit()
        with pytest.raises(StaleAttributionError, match="without substitution"):
            await run_guarded_upgrade(session, operator_authorized=True)
        assert (
            await session.get(Preset, custom_id)
        ).scope_type is PresetScopeType.PERSONAL


@pytest.mark.asyncio
async def test_schedule_current_references_rebind_without_reexpanding_snapshot(
    preset_sessions,
):
    from copy import deepcopy

    from api_service.db.models import RecurringWorkflowDefinition, RecurringWorkflowRun

    async with preset_sessions() as session:
        owner, _, custom_id, _ = await seed_collision(session)
        applied = {
            "slug": "shared",
            "scope": "personal",
            "scopeRef": str(owner),
            "digest": "recorded-digest",
            "inputs": {"false": False, "empty": ""},
            "stepIds": ["frozen-1"],
            "composition": {
                "slug": "shared",
                "includes": [],
            },
        }
        task = {
            "appliedStepTemplates": [applied],
            "steps": [
                {"id": "frozen-1", "instructions": "recorded, not current definition"}
            ],
        }
        schedule = RecurringWorkflowDefinition(
            name="kept",
            cron="0 * * * *",
            timezone="UTC",
            owner_user_id=owner,
            target={"initialParameters": {"workflow": deepcopy(task)}},
        )
        session.add(schedule)
        await session.flush()
        run = RecurringWorkflowRun(
            definition_id=schedule.id,
            scheduled_for=datetime(2026, 1, 1, tzinfo=UTC),
            message="historical dispatch",
        )
        session.add(run)
        await session.commit()
        schedule_id, run_id = schedule.id, run.id
        # Production defaults preserve the owner-independent schedule and its
        # accepted work while the concrete preset transform rebinds references.
        result = await run_guarded_upgrade(session, operator_authorized=True)
        assert result.published
        session.expire_all()
        new_slug = (await session.get(Preset, custom_id)).slug
        current = (await session.get(RecurringWorkflowDefinition, schedule_id)).target[
            "initialParameters"
        ]["workflow"]
        assert current["steps"] == task["steps"]
        saved = current["appliedStepTemplates"][0]
        assert saved["slug"] == saved["composition"]["slug"] == new_slug
        assert "scope" not in saved and "scopeRef" not in saved
        assert "scope" not in saved["composition"]
        assert saved["inputs"] == applied["inputs"]
        assert saved["digest"] == applied["digest"]
        assert saved["stepIds"] == applied["stepIds"]
        assert (
            await session.get(RecurringWorkflowRun, run_id)
        ).message == "historical dispatch"
        # A later conversion must also accept these now-scopeless references
        # while retaining the legacy schedule owner as historical provenance.
        from api_service.services.single_user_conversion import presets_transform

        await presets_transform(session, [str(owner)])
        assert (await session.get(RecurringWorkflowDefinition, schedule_id)).target[
            "initialParameters"
        ]["workflow"] == current
        await session.rollback()


@pytest.mark.asyncio
async def test_published_completion_does_not_skip_later_legacy_row(preset_sessions):
    async with preset_sessions() as session:
        owner, _, _, _ = await seed_collision(session)
        first = await run_guarded_upgrade(session, operator_authorized=True)
        assert first.published
        first_identities = first.transforms["presets"]["legacy_identities"]
        row = Preset(
            slug="later",
            title="Later",
            description="retained",
            created_by=owner,
            scope_type=PresetScopeType.PERSONAL,
            scope_ref=str(owner),
            steps=[],
        )
        session.add(row)
        await session.commit()
        row_id = row.id
        later = await run_guarded_upgrade(session, operator_authorized=True)
        assert later.published
        later_identities = [
            {
                "scope": "personal",
                "scopeRef": str(owner),
                "slug": "later",
                "presetId": str(row_id),
            }
        ]
        assert later.transforms["presets"]["legacy_identities"] == later_identities
        session.expire_all()
        assert (await session.get(Preset, row_id)).scope_type is None
        receipts = list(
            (await session.execute(select(SingleUserConversionRun))).scalars()
        )
        assert len(receipts) == 2
        persisted = [
            row.result_json["transforms"]["presets"]["legacy_identities"]
            for row in receipts
        ]
        assert first_identities in persisted and later_identities in persisted


@pytest.mark.asyncio
async def test_real_zero_user_is_not_a_deployment_alias(preset_sessions):
    async with preset_sessions() as session:
        _, _, custom_id, _ = await seed_collision(session)
        zero = User(id=UUID(int=0), email="real-zero@example.invalid")
        session.add(zero)
        await session.flush()
        session.add(PresetFavorite(user_id=zero.id, template_id=custom_id))
        await session.commit()
        result = await run_guarded_upgrade(session, operator_authorized=True)
        assert not result.published
        assert result.decision.reason_code == "multi_person"
        assert (
            await session.get(Preset, custom_id)
        ).scope_type is PresetScopeType.PERSONAL


@pytest.mark.asyncio
async def test_direct_transform_does_not_invent_real_zero_eligibility(preset_sessions):
    from api_service.services.single_user_conversion import (
        StaleAttributionError,
        presets_transform,
    )

    async with preset_sessions() as session:
        zero = User(id=UUID(int=0), email="real-zero@example.invalid")
        session.add(zero)
        row = Preset(
            slug="zero",
            title="Zero",
            description="kept",
            created_by=zero.id,
            scope_type=PresetScopeType.PERSONAL,
            scope_ref=str(zero.id),
        )
        session.add(row)
        await session.commit()
        with pytest.raises(StaleAttributionError, match="not proven eligible"):
            await presets_transform(session, [])
        await session.rollback()


@pytest.mark.asyncio
async def test_personal_scope_evidence_preserved_without_creator(preset_sessions):
    async with preset_sessions() as session:
        user = User(id=uuid4(), email="scope@example.invalid")
        session.add(user)
        await session.flush()
        row = Preset(
            slug="scope-only",
            title="Kept",
            description="d",
            scope_type=PresetScopeType.PERSONAL,
            scope_ref=str(user.id),
        )
        session.add(row)
        await session.commit()
        row_id = row.id
        assert (await run_guarded_upgrade(session, operator_authorized=True)).published
        session.expire_all()
        assert (await session.get(Preset, row_id)).scope_type is None


@pytest.mark.asyncio
async def test_unknown_personal_scope_is_not_inferred_from_creator(preset_sessions):
    async with preset_sessions() as session:
        _owner, _, custom_id, _ = await seed_collision(session)
        row = await session.get(Preset, custom_id)
        row.scope_ref = str(uuid4())
        await session.commit()
        result = await run_guarded_upgrade(session, operator_authorized=True)
        assert not result.published
        assert result.decision.reason_code == "unknown_principal"
        assert (
            await session.get(Preset, custom_id)
        ).scope_type is PresetScopeType.PERSONAL


@pytest.mark.asyncio
async def test_synthetic_local_scope_converts_without_creating_person(preset_sessions):
    async with preset_sessions() as session:
        row = Preset(
            slug="local",
            title="Local",
            description="d",
            created_by=UUID(int=0),
            scope_type=PresetScopeType.PERSONAL,
            scope_ref=str(UUID(int=0)),
        )
        session.add(row)
        await session.flush()
        session.add(PresetFavorite(user_id=UUID(int=0), template_id=row.id))
        await session.commit()
        row_id = row.id
        result = await run_guarded_upgrade(session, operator_authorized=True)
        assert result.published, result.decision.to_sanitized_dict()
        session.expire_all()
        assert (await session.get(Preset, row_id)).scope_type is None
        assert not list((await session.execute(select(User))).scalars())


@pytest.mark.asyncio
async def test_ambiguous_omitted_schedule_scope_refuses_without_rebinding(
    preset_sessions,
):
    from api_service.db.models import RecurringWorkflowDefinition
    from api_service.services.single_user_conversion import StaleAttributionError

    async with preset_sessions() as session:
        owner, builtin_id, _, _ = await seed_collision(session)
        instance = Preset(slug="shared", title="New instance", description="d")
        schedule = RecurringWorkflowDefinition(
            name="ambiguous",
            cron="0 * * * *",
            timezone="UTC",
            owner_user_id=owner,
            target={
                "initialParameters": {"task": {"taskTemplate": {"slug": "shared"}}}
            },
        )
        session.add_all([instance, schedule])
        await session.commit()
        with pytest.raises(
            StaleAttributionError, match="ambiguous omitted preset scope"
        ):
            await run_guarded_upgrade(session, operator_authorized=True)
        await session.rollback()
        assert (
            await session.get(Preset, builtin_id)
        ).scope_type is PresetScopeType.GLOBAL


async def seed_instance_admission(session, schema_revision):
    """Persist the real completion shape emitted by the account-free predecessor."""
    from copy import deepcopy

    result = await run_guarded_upgrade(session, operator_authorized=True)
    assert result.published
    row = (await session.execute(select(SingleUserConversionRun))).scalar_one()
    if schema_revision != "397_instance_preset_catalog":
        stored = deepcopy(row.result_json)
        stored["schema_revision"] = schema_revision
        # Revision 396 predates the preset transform but certifies admission.
        stored["transforms"].pop("presets")
        row.result_json = stored
        await session.commit()
    return row.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema_revision", ["396_artifact_operator_recovery", "397_instance_preset_catalog"]
)
async def test_certified_operator_global_preset_converts(
    preset_sessions, schema_revision
):
    async with preset_sessions() as session:
        await seed_instance_admission(session, schema_revision)
        # OperatorAdmission has no id. The predecessor route persisted exactly
        # this global, unseeded shape after account-free admission was established.
        row = Preset(
            slug="operator-authored",
            title="Keep me",
            description="custom",
            scope_type=PresetScopeType.GLOBAL,
            created_by=None,
            seed_source=None,
            steps=[
                {
                    "instructions": "operator content",
                    "inputs": {"empty": "", "false": False},
                }
            ],
        )
        session.add(row)
        await session.flush()
        row_id = row.id
        recent_at = datetime(2026, 1, 1, tzinfo=UTC)
        session.add(PresetFavorite(user_id=UUID(int=0), template_id=row_id))
        session.add(
            PresetRecent(user_id=UUID(int=0), template_id=row_id, applied_at=recent_at)
        )
        await session.commit()

        result = await run_guarded_upgrade(session, operator_authorized=True)

        assert result.published, result.decision.to_sanitized_dict()
        assert result.transforms["presets"]["converted"] == 1
        session.expire_all()
        converted = await session.get(Preset, row_id)
        assert converted.scope_type is None and converted.scope_ref is None
        assert converted.created_by is None and converted.seed_source is None
        assert converted.slug == "operator-authored"
        assert converted.steps == [
            {
                "instructions": "operator content",
                "inputs": {"empty": "", "false": False},
            }
        ]
        assert (
            await session.execute(select(PresetFavorite.user_id))
        ).scalar_one() is None
        recent = (await session.execute(select(PresetRecent))).scalar_one()
        assert (
            recent.user_id is None
            and recent.applied_at.replace(tzinfo=UTC) == recent_at
        )
        assert not list((await session.execute(select(User))).scalars())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema_revision", ["396_artifact_operator_recovery", "397_instance_preset_catalog"]
)
async def test_certified_ownerless_schedule_survives_preset_conversion(
    preset_sessions,
    schema_revision,
):
    from copy import deepcopy

    from api_service.db.models import RecurringWorkflowDefinition

    async with preset_sessions() as session:
        await seed_instance_admission(session, schema_revision)
        preset = Preset(
            slug="seeded",
            title="Seed",
            description="d",
            scope_type=PresetScopeType.GLOBAL,
            seed_source="seeded.yaml",
            steps=[{"instructions": "new definition"}],
        )
        target = {
            "initialParameters": {
                "workflow": {
                    "taskTemplate": {
                        "slug": "seeded",
                        "scope": "global",
                        "inputs": {"false": False},
                    },
                    "steps": [{"instructions": "frozen accepted work"}],
                }
            }
        }
        schedule = RecurringWorkflowDefinition(
            name="account-free",
            cron="0 * * * *",
            timezone="UTC",
            owner_user_id=None,
            target=deepcopy(target),
        )
        session.add_all([preset, schedule])
        await session.commit()
        preset_id, schedule_id = preset.id, schedule.id

        result = await run_guarded_upgrade(session, operator_authorized=True)

        assert result.published, result.decision.to_sanitized_dict()
        session.expire_all()
        assert (await session.get(Preset, preset_id)).scope_type is None
        saved = await session.get(RecurringWorkflowDefinition, schedule_id)
        assert saved.owner_user_id is None
        assert (
            saved.target["initialParameters"]["workflow"]["steps"]
            == target["initialParameters"]["workflow"]["steps"]
        )
        assert saved.target["initialParameters"]["workflow"]["taskTemplate"] == {
            "slug": "seeded",
            "inputs": {"false": False},
        }
        assert not list((await session.execute(select(User))).scalars())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence",
    ["none", "custom", "incomplete", "old", "unpublished", "ineligible", "malformed"],
)
async def test_uncertified_null_creator_preset_still_refuses(preset_sessions, evidence):
    from copy import deepcopy

    async with preset_sessions() as session:
        if evidence != "none":
            ledger_id = await seed_instance_admission(
                session, "396_artifact_operator_recovery"
            )
            ledger = await session.get(SingleUserConversionRun, ledger_id)
            stored = deepcopy(ledger.result_json)
            if evidence == "custom":
                stored.pop("production_transforms_completed")
            elif evidence == "incomplete":
                ledger.status = "in_progress"
            elif evidence == "old":
                stored["schema_revision"] = "386_single_user_conversion"
            elif evidence == "unpublished":
                stored["published"] = False
            elif evidence == "ineligible":
                stored["decision"]["eligible"] = False
            elif evidence == "malformed":
                stored["transforms"]["artifacts"]["preserved"] = True
            ledger.result_json = stored
        row = Preset(
            slug="unproven",
            title="Unproven",
            description="d",
            scope_type=PresetScopeType.GLOBAL,
        )
        session.add(row)
        await session.commit()
        row_id = row.id
        result = await run_guarded_upgrade(session, operator_authorized=True)
        assert not result.published
        assert result.decision.reason_code == "unowned_rows"
        assert (await session.get(Preset, row_id)).scope_type is PresetScopeType.GLOBAL


@pytest.mark.asyncio
@pytest.mark.parametrize("other_owner", ["unknown", "second_person"])
async def test_ownerless_schedule_does_not_bypass_human_attribution(
    preset_sessions, other_owner
):
    from api_service.db.models import RecurringWorkflowDefinition

    async with preset_sessions() as session:
        _, _, custom_id, _ = await seed_collision(session)
        owner_id = uuid4()
        if other_owner == "second_person":
            session.add(User(id=owner_id, email="other@example.invalid"))
            await session.flush()
        else:
            # Unknown principals can persist in text scope references; schedule
            # owners are foreign-keyed to real users on PostgreSQL.
            preset = await session.get(Preset, custom_id)
            preset.scope_ref = str(owner_id)
        session.add_all(
            [
                RecurringWorkflowDefinition(
                    name="Instance", cron="0 * * * *", timezone="UTC"
                ),
                RecurringWorkflowDefinition(
                    name="Retained",
                    cron="0 * * * *",
                    timezone="UTC",
                    owner_user_id=(
                        owner_id if other_owner == "second_person" else None
                    ),
                ),
            ]
        )
        await session.commit()
        result = await run_guarded_upgrade(session, operator_authorized=True)
        assert not result.published
        assert result.decision.reason_code == (
            "unknown_principal" if other_owner == "unknown" else "multi_person"
        )
        assert (
            await session.get(Preset, custom_id)
        ).scope_type is PresetScopeType.PERSONAL
        if other_owner == "unknown":
            assert (await session.get(Preset, custom_id)).scope_ref == str(owner_id)


@pytest.mark.asyncio
async def test_prior_admission_does_not_merge_new_human_owners(preset_sessions):
    async with preset_sessions() as session:
        await seed_instance_admission(session, "396_artifact_operator_recovery")
        _, _, custom_id, _ = await seed_collision(session)
        second = User(id=uuid4(), email="second-after-admission@example.invalid")
        session.add(second)
        await session.flush()
        session.add(PresetFavorite(user_id=second.id, template_id=custom_id))
        await session.commit()
        result = await run_guarded_upgrade(session, operator_authorized=True)
        assert not result.published and result.decision.reason_code == "multi_person"
        assert (
            await session.get(Preset, custom_id)
        ).scope_type is PresetScopeType.PERSONAL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "scope_ref", "reason"),
    [
        (PresetScopeType.PERSONAL, None, "unowned_rows"),
        (PresetScopeType.GLOBAL, "unknown-scope", "unowned_rows"),
        (PresetScopeType.PERSONAL, str(UUID(int=999)), "unknown_principal"),
    ],
)
async def test_prior_admission_does_not_infer_ambiguous_preset_scope(
    preset_sessions,
    scope,
    scope_ref,
    reason,
):
    async with preset_sessions() as session:
        await seed_instance_admission(session, "396_artifact_operator_recovery")
        row = Preset(
            slug="unproven",
            title="Unproven",
            description="d",
            scope_type=scope,
            scope_ref=scope_ref,
        )
        session.add(row)
        await session.commit()
        row_id = row.id
        result = await run_guarded_upgrade(session, operator_authorized=True)
        assert not result.published and result.decision.reason_code == reason
        preserved = await session.get(Preset, row_id)
        assert preserved.scope_type is scope and preserved.scope_ref == scope_ref


@pytest.mark.asyncio
async def test_revoked_admission_invalidates_preflight_without_conversion(
    preset_sessions,
):
    from copy import deepcopy

    from api_service.services.single_user_conversion import (
        DEFAULT_SUBSYSTEM_TRANSFORMS,
        StaleAttributionError,
        apply_conversion,
        preflight,
    )

    async with preset_sessions() as session:
        ledger_id = await seed_instance_admission(
            session, "396_artifact_operator_recovery"
        )
        row = Preset(
            slug="kept",
            title="Kept",
            description="d",
            scope_type=PresetScopeType.GLOBAL,
        )
        session.add(row)
        await session.commit()
        row_id = row.id
        bound = await preflight(session)
        assert bound.decision.eligible
        await session.rollback()
    async with preset_sessions() as writer:
        ledger = await writer.get(SingleUserConversionRun, ledger_id)
        stored = deepcopy(ledger.result_json)
        stored["production_transforms_completed"] = False
        ledger.result_json = stored
        await writer.commit()
    async with preset_sessions() as session:
        with pytest.raises(StaleAttributionError):
            await apply_conversion(
                session,
                bound,
                operator_authorized=True,
                transforms=DEFAULT_SUBSYSTEM_TRANSFORMS,
            )
        assert (await session.get(Preset, row_id)).scope_type is PresetScopeType.GLOBAL
        assert (
            len(
                (await session.execute(select(SingleUserConversionRun))).scalars().all()
            )
            == 1
        )


@pytest.mark.parametrize("payload", [None, [], "complete", True])
def test_malformed_completion_payload_has_no_authority(payload):
    from api_service.services.single_user_conversion import _is_production_completion

    assert not _is_production_completion(payload)


@pytest.mark.asyncio
async def test_identity_evidence_publication_failure_rolls_back(preset_sessions):
    from unittest.mock import patch

    async with preset_sessions() as session:
        owner, builtin_id, custom_id, parent_id = await seed_collision(session)
        attempted = []

        async def fail_publication():
            ledger = next(
                row for row in session.new if isinstance(row, SingleUserConversionRun)
            )
            outcome = ledger.result_json["transforms"]["presets"]
            identities = outcome["legacy_identities"]
            assert len(identities) == 3
            assert {entry["presetId"] for entry in identities} == {
                str(builtin_id),
                str(custom_id),
                str(parent_id),
            }
            assert all(
                set(entry) == {"scope", "scopeRef", "slug", "presetId"}
                for entry in identities
            )
            assert ledger.result_json["production_transforms_completed"] is True
            attempted.append(identities)
            await session.flush()  # Even an inserted receipt must roll back.
            assert ledger.id is not None
            raise RuntimeError("interrupted identity evidence publication")

        with patch.object(session, "commit", side_effect=fail_publication):
            with pytest.raises(RuntimeError, match="identity evidence publication"):
                await run_guarded_upgrade(session, operator_authorized=True)

    async with preset_sessions() as restarted:
        custom = await restarted.get(Preset, custom_id)
        assert custom.scope_type is PresetScopeType.PERSONAL
        assert custom.scope_ref == str(owner) and custom.slug == "shared"
        assert not list(
            (await restarted.execute(select(SingleUserConversionRun))).scalars()
        )
        await restarted.rollback()
        retried = await run_guarded_upgrade(restarted, operator_authorized=True)
        assert retried.published and len(attempted) == 1
        assert retried.transforms["presets"]["legacy_identities"] == attempted[0]
        ledger = (await restarted.execute(select(SingleUserConversionRun))).scalar_one()
        assert (
            ledger.result_json["transforms"]["presets"]["legacy_identities"]
            == attempted[0]
        )
