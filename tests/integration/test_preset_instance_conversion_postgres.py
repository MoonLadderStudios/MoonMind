"""Real PostgreSQL coverage for guarded instance-preset conversion."""

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from tests.helpers.account_source import create_source_schema
from tests.integration import (
    test_single_user_conversion_postgres_4346 as postgres_fixtures,
)

conversion_postgres_url = postgres_fixtures.conversion_postgres_url
from tests.unit.api import test_legacy_preset_readiness as readiness_scenarios
from tests.unit.services import test_preset_instance_conversion as scenarios

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest_asyncio.fixture
async def preset_sessions(conversion_postgres_url):
    schema = f"preset_test_{uuid4().hex}"
    admin = create_async_engine(conversion_postgres_url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        conversion_postgres_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(create_source_schema)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        scenarios.test_real_guarded_conversion_preserves_ids_and_resolves_only_collisions,
        scenarios.test_multi_person_source_is_not_exposed,
        scenarios.test_alias_preferences_deduplicate_keep_latest_and_earliest,
        scenarios.test_interruption_rolls_back_then_retry_converges,
        scenarios.test_identity_evidence_publication_failure_rolls_back,
        scenarios.test_same_count_content_edit_invalidates_preflight,
        scenarios.test_missing_composition_target_refuses_without_substitution,
        scenarios.test_schedule_current_references_rebind_without_reexpanding_snapshot,
        scenarios.test_published_completion_does_not_skip_later_legacy_row,
        scenarios.test_real_zero_user_is_not_a_deployment_alias,
        scenarios.test_direct_transform_does_not_invent_real_zero_eligibility,
        scenarios.test_personal_scope_evidence_preserved_without_creator,
        scenarios.test_unknown_personal_scope_is_not_inferred_from_creator,
        scenarios.test_synthetic_local_scope_converts_without_creating_person,
        scenarios.test_ambiguous_omitted_schedule_scope_refuses_without_rebinding,
        scenarios.test_prior_admission_does_not_merge_new_human_owners,
        scenarios.test_revoked_admission_invalidates_preflight_without_conversion,
    ],
    ids=lambda fn: fn.__name__,
)
async def test_postgres_conversion_boundaries(preset_sessions, scenario):
    await scenario(preset_sessions)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,capability", [("global", "git"), ("personal", "docker")]
)
async def test_postgres_retained_readiness_resolves_stable_id(
    preset_sessions, scope, capability
):
    await readiness_scenarios.test_recorded_retry_uses_stable_converted_id_after_restart(
        preset_sessions, scope, capability
    )


@pytest.mark.asyncio
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
async def test_postgres_retained_readiness_refuses_invalid_evidence(
    preset_sessions, damage
):
    await readiness_scenarios.test_legacy_retry_never_substitutes_current_slug_without_identity_proof(
        preset_sessions, damage
    )


@pytest.mark.asyncio
async def test_postgres_new_readiness_never_grants_legacy_authority(preset_sessions):
    await readiness_scenarios.test_new_scoped_request_and_mismatched_personal_provenance_still_refuse(
        preset_sessions
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema_revision",
    [
        "396_artifact_operator_recovery",
        "397_instance_preset_catalog",
        "398_instance_settings",
    ],
)
@pytest.mark.parametrize(
    "scenario",
    [
        scenarios.test_certified_operator_global_preset_converts,
        scenarios.test_certified_ownerless_schedule_survives_preset_conversion,
    ],
    ids=lambda fn: fn.__name__,
)
async def test_postgres_certified_predecessor_resources(
    preset_sessions, schema_revision, scenario
):
    await scenario(preset_sessions, schema_revision)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence",
    ["none", "custom", "incomplete", "old", "unpublished", "ineligible", "malformed"],
)
async def test_postgres_uncertified_null_creator_preset_refuses(
    preset_sessions, evidence
):
    await scenarios.test_uncertified_null_creator_preset_still_refuses(
        preset_sessions, evidence
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("other_owner", ["unknown", "second_person"])
async def test_postgres_ownerless_schedule_preserves_human_attribution(
    preset_sessions, other_owner
):
    await scenarios.test_ownerless_schedule_does_not_bypass_human_attribution(
        preset_sessions, other_owner
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,scope_ref,reason",
    [
        (scenarios.PresetScopeType.PERSONAL, None, "unowned_rows"),
        (scenarios.PresetScopeType.GLOBAL, "unknown-scope", "unowned_rows"),
        (
            scenarios.PresetScopeType.PERSONAL,
            str(scenarios.UUID(int=999)),
            "unknown_principal",
        ),
    ],
)
async def test_postgres_certification_does_not_infer_scope(
    preset_sessions, scope, scope_ref, reason
):
    await scenarios.test_prior_admission_does_not_infer_ambiguous_preset_scope(
        preset_sessions, scope, scope_ref, reason
    )


@pytest.mark.asyncio
async def test_postgres_schema_upgrade_is_additive_and_downgrade_protects_data(
    preset_sessions,
):
    import importlib
    from unittest.mock import patch

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import select

    from api_service.db.models import Preset, PresetFavorite, PresetRecent
    from api_service.services.single_user_conversion import run_guarded_upgrade

    migration = importlib.import_module(
        "api_service.migrations.versions.397_instance_preset_catalog"
    )
    async with preset_sessions() as session:
        _, builtin_id, custom_id, _ = await scenarios.seed_collision(session)
        for table, column in migration._COLUMNS:
            await session.execute(
                text(f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL")
            )
        for _table, name, _column, _legacy in migration._INDEXES:
            await session.execute(text(f"DROP INDEX {name}"))
        await session.commit()
        connection = await session.connection()

        def upgrade(conn):
            with patch.object(
                migration, "op", Operations(MigrationContext.configure(conn))
            ):
                migration.upgrade()

        await connection.run_sync(upgrade)
        await session.commit()
        session.expire_all()
        assert (await session.get(Preset, custom_id)).scope_type is not None
        assert len((await session.execute(select(PresetFavorite))).scalars().all()) == 1
        assert len((await session.execute(select(PresetRecent))).scalars().all()) == 1
        await session.rollback()
        assert (await run_guarded_upgrade(session, operator_authorized=True)).published
        connection = await session.connection()

        def downgrade(conn):
            with patch.object(
                migration, "op", Operations(MigrationContext.configure(conn))
            ):
                migration.downgrade()

        with pytest.raises(RuntimeError, match="Cannot downgrade instance"):
            await connection.run_sync(downgrade)
        await session.rollback()
        assert (await session.get(Preset, custom_id)).scope_type is None
        assert (await session.get(Preset, builtin_id)).slug == "shared"


@pytest.mark.asyncio
async def test_postgres_conversion_excludes_concurrent_writers_and_converters(
    preset_sessions,
):
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from api_service.services.single_user_conversion import (
        DEFAULT_SUBSYSTEM_TRANSFORMS,
        ConcurrentConversionError,
        presets_transform,
        run_guarded_upgrade,
    )

    async with preset_sessions() as setup:
        _, _, custom_id, _ = await scenarios.seed_collision(setup)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def paused_transform(session, eligible):
        result = await presets_transform(session, eligible)
        entered.set()
        await release.wait()
        return result

    async def convert():
        async with preset_sessions() as session:
            return await run_guarded_upgrade(
                session,
                operator_authorized=True,
                transforms={
                    **DEFAULT_SUBSYSTEM_TRANSFORMS,
                    "presets": paused_transform,
                },
            )

    pending = asyncio.create_task(convert())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        for statement, params in (
            (
                "UPDATE presets SET description='unfenced' WHERE id=:id",
                {"id": custom_id},
            ),
            ("UPDATE single_user_conversion_runs SET status='in_progress'", {}),
        ):
            async with preset_sessions() as writer:
                await writer.execute(text("SET LOCAL lock_timeout = '150ms'"))
                with pytest.raises(DBAPIError) as exc:
                    await writer.execute(text(statement), params)
                assert getattr(exc.value.orig, "sqlstate", None) == "55P03"
                await writer.rollback()
        async with preset_sessions() as contender:
            with pytest.raises(ConcurrentConversionError):
                await run_guarded_upgrade(contender, operator_authorized=True)
    finally:
        release.set()
    assert (await asyncio.wait_for(pending, 10)).published
    async with preset_sessions() as session:
        from api_service.db.models import Preset

        assert (await session.get(Preset, custom_id)).description == "c"


@pytest.mark.asyncio
async def test_postgres_instance_uniqueness_is_database_enforced(preset_sessions):
    from sqlalchemy.exc import IntegrityError

    from api_service.db.models import Preset, PresetFavorite, PresetRecent
    from api_service.services.single_user_conversion import run_guarded_upgrade

    async with preset_sessions() as session:
        _, builtin_id, custom_id, _ = await scenarios.seed_collision(session)
        assert (await run_guarded_upgrade(session, operator_authorized=True)).published
        for duplicate in (
            Preset(slug="shared", title="No overwrite", description="d"),
            PresetFavorite(template_id=custom_id),
            PresetRecent(template_id=custom_id),
        ):
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    session.add(duplicate)
                    await session.flush()
        assert (await session.get(Preset, builtin_id)).title == "Builtin"


@pytest.mark.asyncio
async def test_postgres_seed_sync_waits_for_conversion_and_retains_ids(
    preset_sessions, tmp_path
):
    from sqlalchemy import select

    from api_service.db.models import Preset
    from api_service.services.presets.catalog import (
        PresetCatalogService,
        PresetValidationError,
    )
    from api_service.services.single_user_conversion import run_guarded_upgrade

    (tmp_path / "shared.yaml").write_text(
        "slug: shared\ntitle: Refreshed builtin\ndescription: Builtin refresh\nsteps:\n  - instructions: Updated builtin\n",
        encoding="utf-8",
    )
    async with preset_sessions() as session:
        _, builtin_id, custom_id, _ = await scenarios.seed_collision(session)
        catalog = PresetCatalogService(session)
        for method in (catalog.import_seed_templates, catalog.sync_seed_templates):
            with pytest.raises(PresetValidationError, match="guarded conversion"):
                await method(seed_dir=tmp_path)
            assert len((await session.execute(select(Preset))).scalars().all()) == 3
        await session.rollback()
        assert (await run_guarded_upgrade(session, operator_authorized=True)).published
        await catalog.sync_seed_templates(seed_dir=tmp_path)
        await catalog.sync_seed_templates(seed_dir=tmp_path)
        session.expire_all()
        assert (await session.get(Preset, builtin_id)).title == "Refreshed builtin"
        assert (await session.get(Preset, custom_id)).steps == [
            {"instructions": "custom"}
        ]
        assert len((await session.execute(select(Preset))).scalars().all()) == 3


@pytest.mark.asyncio
async def test_postgres_concurrent_instance_preference_upserts(preset_sessions):
    import asyncio

    from sqlalchemy import select

    from api_service.db.models import Preset, PresetFavorite, PresetRecent
    from api_service.services.presets.catalog import PresetCatalogService

    async with preset_sessions() as session:
        preset = Preset(slug="concurrent", title="Concurrent", description="d")
        session.add(preset)
        await session.commit()
        template_id = preset.id

    async def write_preferences():
        async with preset_sessions() as session:
            service = PresetCatalogService(session)
            await service.set_favorite(slug="concurrent")
            await service.record_recent(template_id=template_id)

    await asyncio.gather(*(write_preferences() for _ in range(4)))
    await asyncio.gather(*(write_preferences() for _ in range(4)))
    async with preset_sessions() as session:
        assert len((await session.execute(select(PresetFavorite))).scalars().all()) == 1
        assert len((await session.execute(select(PresetRecent))).scalars().all()) == 1
