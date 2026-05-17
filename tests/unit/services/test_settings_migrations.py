"""Unit tests for the settings migration orchestrator.

Covers the FRs in docs/Security/SettingsSystem.md §24 (Migration and
Deprecation) — that the rename workflow preserves effective values across the
cutover, that type-change requires an explicit coercion (no ambiguous JSON
reinterpretation), and that removal events stamp an audit trail without
losing operator intent. Also covers the §23 backup-redaction policy by
asserting sensitive descriptors flow through the orchestrator's
``redact_keys`` parameter without storing plaintext.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import (
    Base,
    SettingsAuditEvent,
    SettingsOverride,
)
from api_service.services.settings_catalog import (
    SettingMigrationRule,
    SettingRegistryEntry,
    SettingsCatalogService,
)
from api_service.services.settings_migrations import (
    SettingsMigrationCoercionError,
    SettingsMigrationCollisionError,
    SettingsMigrationError,
    SettingsMigrationOrchestrator,
    TypeChangeCoercion,
)


_DEFAULT_SUBJECT_ID = UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture
def settings_session_maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/settings.db")

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    yield session_maker
    asyncio.run(engine.dispose())


def _new_runtime_registry() -> tuple[SettingRegistryEntry, ...]:
    return (
        SettingRegistryEntry(
            key="test.new_runtime",
            title="New Runtime",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value="default-runtime",
            order=1,
            apply_mode="next_task",
            applies_to=("task_creation",),
        ),
    )


def _retry_registry() -> tuple[SettingRegistryEntry, ...]:
    return (
        SettingRegistryEntry(
            key="test.retry_limit",
            title="Retry Limit",
            category="Test",
            section="user-workspace",
            value_type="integer",
            ui="number",
            scopes=("workspace",),
            default_value=0,
            order=1,
            apply_mode="next_task",
            applies_to=("task_creation",),
        ),
    )


def _seed_override(
    session,
    *,
    key: str,
    value: object,
    scope: str = "workspace",
    user_id: UUID = _DEFAULT_SUBJECT_ID,
    workspace_id: UUID = _DEFAULT_SUBJECT_ID,
    schema_version: int = 1,
    value_version: int = 1,
) -> SettingsOverride:
    row = SettingsOverride(
        scope=scope,
        workspace_id=workspace_id,
        user_id=user_id,
        key=key,
        value_json=value,
        schema_version=schema_version,
        value_version=value_version,
    )
    session.add(row)
    return row


@pytest.mark.asyncio
async def test_rename_orchestrator_copies_override_to_new_key_and_emits_event(
    settings_session_maker,
):
    rule = SettingMigrationRule(
        old_key="test.old_runtime",
        new_key="test.new_runtime",
        state="renamed",
        message="test.old_runtime was renamed to test.new_runtime.",
    )

    async with settings_session_maker() as session:
        _seed_override(
            session,
            key="test.old_runtime",
            value="preserved-runtime",
            schema_version=2,
            value_version=3,
        )
        await session.commit()

        orchestrator = SettingsMigrationOrchestrator(
            session=session,
            migration_rules=(rule,),
        )
        outcomes = await orchestrator.apply_rename(rule)
        await session.commit()

        assert len(outcomes) == 1
        outcome = outcomes[0]
        assert outcome.applied is True
        assert outcome.rule_new_key == "test.new_runtime"
        assert outcome.event_type == "settings.migration.renamed"
        assert outcome.old_value == "preserved-runtime"
        assert outcome.new_value == "preserved-runtime"

        rows = (await session.execute(select(SettingsOverride))).scalars().all()
        assert [row.key for row in rows] == ["test.new_runtime"]
        migrated = rows[0]
        assert migrated.value_json == "preserved-runtime"
        assert migrated.schema_version == 2
        assert migrated.value_version == 3

        events = (await session.execute(select(SettingsAuditEvent))).scalars().all()
        assert [event.event_type for event in events] == [
            "settings.migration.renamed",
        ]
        event = events[0]
        assert event.key == "test.old_runtime"
        assert event.old_value_json == "preserved-runtime"
        assert event.new_value_json == "preserved-runtime"
        assert event.redacted is False


@pytest.mark.asyncio
async def test_rename_orchestrator_preserves_effective_value_across_cutover(
    settings_session_maker,
):
    """Effective value remains stable before and after the rename migration.

    This is the §24.1 acceptance criterion — the rename workflow must
    preserve effective values across the cutover with explicit test coverage.
    """

    registry = _new_runtime_registry()
    rule = SettingMigrationRule(
        old_key="test.old_runtime",
        new_key="test.new_runtime",
        state="renamed",
        message="test.old_runtime was renamed to test.new_runtime.",
    )

    async with settings_session_maker() as session:
        _seed_override(session, key="test.old_runtime", value="preserved-runtime")
        await session.commit()

        catalog_before = SettingsCatalogService(
            env={},
            registry=registry,
            migration_rules=(rule,),
            session=session,
        )
        before = await catalog_before.effective_value_async(
            "test.new_runtime", scope="workspace"
        )
        assert before.value == "preserved-runtime"
        assert before.source == "workspace_override"
        assert any(
            diag.code == "setting_renamed_override" for diag in before.diagnostics
        )

        orchestrator = SettingsMigrationOrchestrator(
            session=session,
            migration_rules=(rule,),
        )
        await orchestrator.apply_rename(rule)
        await session.commit()

        catalog_after = SettingsCatalogService(
            env={},
            registry=registry,
            migration_rules=(rule,),
            session=session,
        )
        after = await catalog_after.effective_value_async(
            "test.new_runtime", scope="workspace"
        )
        assert after.value == "preserved-runtime"
        assert after.source == "workspace_override"
        # Diagnostic must disappear once the row has been migrated to the
        # new key — the renamed-override warning is only meaningful while
        # the legacy row is still being read.
        assert all(
            diag.code != "setting_renamed_override" for diag in after.diagnostics
        )


@pytest.mark.asyncio
async def test_rename_orchestrator_preserves_user_and_workspace_scoped_rows(
    settings_session_maker,
):
    user_a = uuid4()
    user_b = uuid4()
    rule = SettingMigrationRule(
        old_key="test.old_runtime",
        new_key="test.new_runtime",
        state="renamed",
        message="renamed",
    )

    async with settings_session_maker() as session:
        _seed_override(
            session, key="test.old_runtime", value="workspace-value", scope="workspace"
        )
        _seed_override(
            session,
            key="test.old_runtime",
            value="user-a-value",
            scope="user",
            user_id=user_a,
        )
        _seed_override(
            session,
            key="test.old_runtime",
            value="user-b-value",
            scope="user",
            user_id=user_b,
        )
        await session.commit()

        orchestrator = SettingsMigrationOrchestrator(
            session=session,
            migration_rules=(rule,),
        )
        outcomes = await orchestrator.apply_rename(rule)
        await session.commit()

        assert len(outcomes) == 3
        rows = (
            await session.execute(
                select(SettingsOverride).order_by(SettingsOverride.value_json)
            )
        ).scalars().all()
        assert [row.key for row in rows] == ["test.new_runtime"] * 3
        # Every original row should now exist on the new key with its
        # original scope and subject preserved.
        triples = sorted((row.scope, str(row.user_id), row.value_json) for row in rows)
        assert triples == sorted(
            [
                ("workspace", str(_DEFAULT_SUBJECT_ID), "workspace-value"),
                ("user", str(user_a), "user-a-value"),
                ("user", str(user_b), "user-b-value"),
            ]
        )


@pytest.mark.asyncio
async def test_rename_orchestrator_raises_on_collision(settings_session_maker):
    rule = SettingMigrationRule(
        old_key="test.old_runtime",
        new_key="test.new_runtime",
        state="renamed",
        message="renamed",
    )

    async with settings_session_maker() as session:
        _seed_override(session, key="test.old_runtime", value="from-old")
        _seed_override(session, key="test.new_runtime", value="from-new")
        await session.commit()

        orchestrator = SettingsMigrationOrchestrator(
            session=session,
            migration_rules=(rule,),
        )
        with pytest.raises(SettingsMigrationCollisionError):
            await orchestrator.apply_rename(rule)


@pytest.mark.asyncio
async def test_rename_orchestrator_can_skip_collisions(settings_session_maker):
    rule = SettingMigrationRule(
        old_key="test.old_runtime",
        new_key="test.new_runtime",
        state="renamed",
        message="renamed",
    )

    async with settings_session_maker() as session:
        _seed_override(session, key="test.old_runtime", value="from-old")
        _seed_override(session, key="test.new_runtime", value="from-new")
        await session.commit()

        orchestrator = SettingsMigrationOrchestrator(
            session=session,
            migration_rules=(rule,),
        )
        outcomes = await orchestrator.apply_rename(rule, on_collision="skip")
        await session.commit()

        assert len(outcomes) == 1
        assert outcomes[0].applied is False
        rows = (
            await session.execute(
                select(SettingsOverride).order_by(SettingsOverride.key)
            )
        ).scalars().all()
        # Both rows are preserved — no silent overwrite.
        assert sorted((row.key, row.value_json) for row in rows) == [
            ("test.new_runtime", "from-new"),
            ("test.old_runtime", "from-old"),
        ]


@pytest.mark.asyncio
async def test_type_change_orchestrator_requires_explicit_coercion(
    settings_session_maker,
):
    rule = SettingMigrationRule(
        old_key="test.retry_limit",
        state="type_changed",
        message="test.retry_limit type changed from string to integer.",
        expected_schema_version=2,
    )

    async with settings_session_maker() as session:
        _seed_override(
            session,
            key="test.retry_limit",
            value="3",
            schema_version=1,
        )
        await session.commit()

        orchestrator = SettingsMigrationOrchestrator(
            session=session,
            migration_rules=(rule,),
        )

        # run_all without coercions records the rule as skipped rather than
        # silently reinterpreting the JSON value.
        report = await orchestrator.run_all()
        assert report.skipped_rules == ["test.retry_limit"]
        rows = (await session.execute(select(SettingsOverride))).scalars().all()
        assert rows[0].value_json == "3"
        assert rows[0].schema_version == 1


@pytest.mark.asyncio
async def test_type_change_orchestrator_coerces_explicitly_and_bumps_schema(
    settings_session_maker,
):
    registry = _retry_registry()
    rule = SettingMigrationRule(
        old_key="test.retry_limit",
        state="type_changed",
        message="test.retry_limit type changed from string to integer.",
        expected_schema_version=2,
    )

    async with settings_session_maker() as session:
        _seed_override(
            session,
            key="test.retry_limit",
            value="3",
            schema_version=1,
            value_version=4,
        )
        await session.commit()

        catalog_before = SettingsCatalogService(
            env={},
            registry=registry,
            migration_rules=(rule,),
            session=session,
        )
        before = await catalog_before.effective_value_async(
            "test.retry_limit", scope="workspace"
        )
        # Pre-migration diagnostics MUST flag the schema mismatch so we
        # never silently reinterpret the stale string value.
        assert any(
            diag.code == "post_migration_invalid" for diag in before.diagnostics
        )

        coercion = TypeChangeCoercion(coerce=int, target_schema_version=2)
        orchestrator = SettingsMigrationOrchestrator(
            session=session,
            migration_rules=(rule,),
        )
        outcomes = await orchestrator.apply_type_change(rule, coercion)
        await session.commit()

        assert len(outcomes) == 1
        outcome = outcomes[0]
        assert outcome.old_value == "3"
        assert outcome.new_value == 3
        assert outcome.old_schema_version == 1
        assert outcome.new_schema_version == 2

        row = (await session.execute(select(SettingsOverride))).scalars().one()
        assert row.value_json == 3
        assert row.schema_version == 2
        assert row.value_version == 5

        events = (await session.execute(select(SettingsAuditEvent))).scalars().all()
        assert [event.event_type for event in events] == [
            "settings.migration.type_changed",
        ]

        catalog_after = SettingsCatalogService(
            env={},
            registry=registry,
            migration_rules=(rule,),
            session=session,
        )
        after = await catalog_after.effective_value_async(
            "test.retry_limit", scope="workspace"
        )
        assert after.value == 3
        assert all(
            diag.code != "post_migration_invalid" for diag in after.diagnostics
        )


@pytest.mark.asyncio
async def test_type_change_orchestrator_rejects_mismatched_target_schema(
    settings_session_maker,
):
    rule = SettingMigrationRule(
        old_key="test.retry_limit",
        state="type_changed",
        message="bumped to schema 2",
        expected_schema_version=2,
    )

    async with settings_session_maker() as session:
        _seed_override(session, key="test.retry_limit", value="3", schema_version=1)
        await session.commit()

        orchestrator = SettingsMigrationOrchestrator(
            session=session,
            migration_rules=(rule,),
        )
        bad_coercion = TypeChangeCoercion(coerce=int, target_schema_version=3)
        with pytest.raises(SettingsMigrationCoercionError):
            await orchestrator.apply_type_change(rule, bad_coercion)


@pytest.mark.asyncio
async def test_removal_orchestrator_preserves_row_and_records_audit_event(
    settings_session_maker,
):
    rule = SettingMigrationRule(
        old_key="test.deprecated_runtime",
        state="removed",
        message="test.deprecated_runtime was removed.",
    )

    async with settings_session_maker() as session:
        _seed_override(
            session,
            key="test.deprecated_runtime",
            value="legacy-value",
        )
        await session.commit()

        orchestrator = SettingsMigrationOrchestrator(
            session=session,
            migration_rules=(rule,),
        )
        outcomes = await orchestrator.apply_removal(rule)
        await session.commit()

        assert len(outcomes) == 1
        assert outcomes[0].event_type == "settings.migration.removed"

        # Row is preserved for diagnostic continuity — operators must not
        # lose intent silently. Writes are rejected by the registry path
        # (covered elsewhere); the orchestrator only stamps audit history.
        rows = (await session.execute(select(SettingsOverride))).scalars().all()
        assert len(rows) == 1
        assert rows[0].value_json == "legacy-value"

        events = (await session.execute(select(SettingsAuditEvent))).scalars().all()
        assert [event.event_type for event in events] == [
            "settings.migration.removed",
        ]


@pytest.mark.asyncio
async def test_migration_audit_events_redact_sensitive_keys(settings_session_maker):
    """Sensitive descriptors must not leak plaintext into backup data."""

    rule = SettingMigrationRule(
        old_key="test.legacy_api_token",
        new_key="test.api_token",
        state="renamed",
        message="renamed",
    )

    async with settings_session_maker() as session:
        _seed_override(
            session, key="test.legacy_api_token", value="env:LEGACY_TOKEN"
        )
        await session.commit()

        orchestrator = SettingsMigrationOrchestrator(
            session=session,
            migration_rules=(rule,),
            redact_keys=frozenset({"test.legacy_api_token"}),
        )
        await orchestrator.apply_rename(rule)
        await session.commit()

        events = (await session.execute(select(SettingsAuditEvent))).scalars().all()
        assert [event.event_type for event in events] == [
            "settings.migration.renamed",
        ]
        event = events[0]
        assert event.redacted is True
        assert event.old_value_json is None
        assert event.new_value_json is None
        # The override row itself still carries the SecretRef so the
        # restore path can recover the reference; only the audit-event
        # values are blanked.
        row = (await session.execute(select(SettingsOverride))).scalars().one()
        assert row.key == "test.api_token"
        assert row.value_json == "env:LEGACY_TOKEN"


@pytest.mark.asyncio
async def test_run_all_combines_rename_type_change_and_removal(settings_session_maker):
    rename_rule = SettingMigrationRule(
        old_key="test.old_runtime",
        new_key="test.new_runtime",
        state="renamed",
        message="renamed",
    )
    type_rule = SettingMigrationRule(
        old_key="test.retry_limit",
        state="type_changed",
        message="type changed",
        expected_schema_version=2,
    )
    remove_rule = SettingMigrationRule(
        old_key="test.deprecated_runtime",
        state="removed",
        message="removed",
    )

    async with settings_session_maker() as session:
        _seed_override(session, key="test.old_runtime", value="preserved")
        _seed_override(session, key="test.retry_limit", value="3", schema_version=1)
        _seed_override(session, key="test.deprecated_runtime", value="legacy")
        await session.commit()

        orchestrator = SettingsMigrationOrchestrator(
            session=session,
            migration_rules=(rename_rule, type_rule, remove_rule),
        )
        report = await orchestrator.run_all(
            type_coercions={
                "test.retry_limit": TypeChangeCoercion(
                    coerce=int, target_schema_version=2
                ),
            }
        )
        await session.commit()

        assert report.skipped_rules == []
        outcome_keys = sorted(outcome.rule_old_key for outcome in report.outcomes)
        assert outcome_keys == [
            "test.deprecated_runtime",
            "test.old_runtime",
            "test.retry_limit",
        ]

        events = (
            await session.execute(
                select(SettingsAuditEvent.event_type).order_by(
                    SettingsAuditEvent.event_type
                )
            )
        ).scalars().all()
        assert sorted(events) == [
            "settings.migration.removed",
            "settings.migration.renamed",
            "settings.migration.type_changed",
        ]


@pytest.mark.asyncio
async def test_orchestrator_rejects_state_mismatch(settings_session_maker):
    rule = SettingMigrationRule(
        old_key="test.old_runtime",
        new_key="test.new_runtime",
        state="renamed",
        message="renamed",
    )

    async with settings_session_maker() as session:
        orchestrator = SettingsMigrationOrchestrator(
            session=session,
            migration_rules=(rule,),
        )
        with pytest.raises(SettingsMigrationError):
            await orchestrator.apply_type_change(
                rule,
                TypeChangeCoercion(coerce=int, target_schema_version=1),
            )
        with pytest.raises(SettingsMigrationError):
            await orchestrator.apply_removal(rule)


@pytest.mark.asyncio
async def test_orchestrator_rejects_duplicate_rules():
    rule_a = SettingMigrationRule(
        old_key="test.old_runtime",
        new_key="test.new_runtime",
        state="renamed",
        message="renamed",
    )
    rule_b = SettingMigrationRule(
        old_key="test.old_runtime",
        state="removed",
        message="removed",
    )

    class _DummySession:
        def add(self, *_args, **_kwargs):  # pragma: no cover - never reached
            raise AssertionError("session should not be touched")

    with pytest.raises(SettingsMigrationError):
        SettingsMigrationOrchestrator(
            session=_DummySession(),  # type: ignore[arg-type]
            migration_rules=(rule_a, rule_b),
        )
