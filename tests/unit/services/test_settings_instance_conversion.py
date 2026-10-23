"""Guarded conversion of retained settings into the instance configuration.

MoonLadderStudios/MoonMind#4348: retained personal and workspace overrides
become one instance configuration through the shared guarded owner. The
operator's effective choices, explicit nulls, SecretRefs and sparse
inheritance survive; distinct choices need an explicit disposition, and
interrupted or stale applies leave the retained rows untouched.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import SettingsAuditEvent, SettingsOverride
from api_service.services import single_user_conversion as suc
from api_service.services.settings_catalog import SettingsCatalogService
from tests.helpers.account_source import User, create_source_schema

TOKEN = "integrations.github.token_ref"
PROFILE = "workflow.default_provider_profile_ref"
PUBLISH = "workflow.default_publish_mode"
CANARY = "skills.canary_percent"
POLICY = "skills.policy_mode"

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/settings-conv.db")
    async with engine.begin() as conn:
        await conn.run_sync(create_source_schema)
    yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _user(session) -> User:
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@example.com")
    session.add(user)
    await session.flush()
    return user


def _personal(user: User, key: str, value, **kw) -> SettingsOverride:
    return SettingsOverride(
        scope="user", user_id=user.id, key=key, value_json=value, **kw
    )


def _workspace(key: str, value, **kw) -> SettingsOverride:
    return SettingsOverride(
        scope="workspace", workspace_id=uuid.uuid4(), key=key, value_json=value, **kw
    )


async def _rows(session):
    return (
        (await session.execute(select(SettingsOverride).order_by(SettingsOverride.key)))
        .scalars()
        .all()
    )


async def test_conversion_preserves_effective_values_nulls_refs_and_inheritance(
    factory,
):
    async with factory() as session:
        operator = await _user(session)
        session.add_all(
            [
                # Personal choice overrode the instance value in the operator's
                # sessions; it is the operator's effective value.
                SettingsOverride(key=TOKEN, value_json="env://SHARED", value_version=4),
                _personal(operator, TOKEN, "db://operator-token", value_version=2),
                # An explicit personal null is a choice, not an absent row.
                _workspace(PUBLISH, "branch"),
                _personal(operator, PUBLISH, None),
                # A workspace-only choice becomes the instance value.
                _workspace(CANARY, 25, value_version=3),
            ]
        )
        await session.commit()

        result = await suc.run_guarded_upgrade(session, operator_authorized=True)

        assert result.published is True
        assert result.transforms["settings"] == {
            "converted_keys": 3,
            "merged_rows": 2,
            "resolved_collisions": 0,
        }
        rows = {row.key: row for row in await _rows(session)}
        assert set(rows) == {TOKEN, PUBLISH, CANARY}
        assert all(
            (row.scope, row.workspace_id, row.user_id) == (None, None, None)
            for row in rows.values()
        )
        assert rows[TOKEN].value_json == "db://operator-token"
        # Merged rows advance past every pre-conversion version.
        assert rows[TOKEN].value_version == 5
        assert rows[PUBLISH].value_json is None
        assert (rows[CANARY].value_json, rows[CANARY].value_version) == (25, 3)

        service = SettingsCatalogService(env={}, session=session)
        token = await service.effective_value_async(TOKEN)
        publish = await service.effective_value_async(PUBLISH)
        profile = await service.effective_value_async(PROFILE)
        # A managed SecretRef reports its canonical source; it is still the override.
        assert (token.value, token.source) == ("db://operator-token", "secret_ref")
        assert token.inheritance_state == "overridden"
        assert publish.value is None
        assert publish.inheritance_state == "intentionally_null"
        assert profile.source != "instance_override"

        events = {
            event.key: event
            for event in (await session.execute(select(SettingsAuditEvent))).scalars()
            if event.event_type == "settings.override.converted"
        }
        # The superseded shared value stays recoverable in instance history.
        assert events[TOKEN].old_value_json == ["env://SHARED"]
        assert events[TOKEN].new_value_json == "db://operator-token"
        assert events[TOKEN].user_id is None
        history = await service.list_audit_events(
            key=TOKEN
        )
        assert [item.event_type for item in history] == ["settings.override.converted"]

        replay = await suc.run_guarded_upgrade(session, operator_authorized=True)
        assert replay.published is True
        assert {row.key: row.value_json for row in await _rows(session)} == {
            key: row.value_json for key, row in rows.items()
        }


async def test_distinct_choices_refuse_without_mutation_until_resolved(factory):
    async with factory() as session:
        first = await _user(session)
        alias = await _user(session)
        session.add_all(
            [
                _personal(first, TOKEN, "env://FIRST"),
                _personal(alias, TOKEN, "env://ALIAS"),
            ]
        )
        await session.commit()
        aliases = [{str(first.id), str(alias.id)}]

        refused = await suc.run_guarded_upgrade(
            session, operator_authorized=True, alias_groups=aliases
        )
        assert refused.published is False
        assert refused.decision.reason_code == "settings_collision"
        assert TOKEN in refused.decision.detail
        assert "env://" not in str(refused.to_sanitized_dict())
        assert {row.scope for row in await _rows(session)} == {"user"}

        # A disposition must name one of the retained choices; it can never
        # substitute a different credential.
        with pytest.raises(suc.StaleAttributionError):
            await suc.run_guarded_upgrade(
                session,
                operator_authorized=True,
                alias_groups=aliases,
                collision_resolutions={TOKEN: "env://SOMETHING_ELSE"},
            )
        assert {row.value_json for row in await _rows(session)} == {
            "env://FIRST",
            "env://ALIAS",
        }

        resolved = await suc.run_guarded_upgrade(
            session,
            operator_authorized=True,
            alias_groups=aliases,
            collision_resolutions={TOKEN: "env://ALIAS"},
        )
        assert resolved.published is True
        assert resolved.transforms["settings"]["resolved_collisions"] == 1
        [row] = await _rows(session)
        assert (row.scope, row.value_json) == (None, "env://ALIAS")


async def test_interrupted_conversion_leaves_retained_rows_and_retry_converges(
    factory, monkeypatch
):
    async with factory() as session:
        operator = await _user(session)
        session.add_all(
            [_workspace(CANARY, 40), _personal(operator, TOKEN, "env://OPERATOR")]
        )
        await session.commit()

        def interrupted(*_args, **_kwargs):
            raise RuntimeError("host lost during conversion")

        with monkeypatch.context() as patch:
            patch.setattr(SettingsCatalogService, "_audit_event", interrupted)
            with pytest.raises(RuntimeError):
                await suc.run_guarded_upgrade(session, operator_authorized=True)

        assert [(row.scope, row.value_json) for row in await _rows(session)] == [
            ("user", "env://OPERATOR"),
            ("workspace", 40),
        ]
        assert (await session.execute(select(SettingsAuditEvent))).first() is None

        retried = await suc.run_guarded_upgrade(session, operator_authorized=True)
        assert retried.published is True
        assert [(row.scope, row.value_json) for row in await _rows(session)] == [
            (None, "env://OPERATOR"),
            (None, 40),
        ]


async def test_concurrent_settings_edit_after_preflight_is_stale(factory):
    async with factory() as session:
        operator = await _user(session)
        row = _personal(operator, TOKEN, "env://BEFORE")
        session.add(row)
        await session.commit()
        bound = await suc.preflight(session)

        row.value_json = "env://EDITED"
        await session.commit()

        with pytest.raises(suc.StaleAttributionError):
            await suc.apply_conversion(
                session,
                bound,
                operator_authorized=True,
                transforms=dict(suc.DEFAULT_SUBSYSTEM_TRANSFORMS),
            )
        [kept] = await _rows(session)
        assert (kept.scope, kept.value_json) == ("user", "env://EDITED")


async def test_multi_person_settings_stay_protected(factory):
    async with factory() as session:
        first = await _user(session)
        second = await _user(session)
        session.add_all(
            [
                _personal(first, TOKEN, "env://FIRST"),
                _personal(second, TOKEN, "env://SECOND"),
            ]
        )
        await session.commit()

        result = await suc.run_guarded_upgrade(session, operator_authorized=True)

        assert result.published is False
        assert result.decision.disposition == "multi_person_refusal"
        assert sorted(row.value_json for row in await _rows(session)) == [
            "env://FIRST",
            "env://SECOND",
        ]
        # Retained personal rows are never read as instance configuration.
        effective = await SettingsCatalogService(
            env={}, session=session
        ).effective_value_async(TOKEN)
        assert effective.source != "instance_override"


async def test_instance_edit_during_refused_conversion_is_not_replaced(factory):
    async with factory() as session:
        operator = await _user(session)
        session.add_all(
            [
                _personal(operator, TOKEN, "env://STALE_PERSONAL"),
                _personal(operator, PUBLISH, "branch"),
                _personal(operator, POLICY, "allowlist"),
                # An unrelated collision keeps the conversion refused.
                _workspace(CANARY, 25),
                _workspace(CANARY, 40),
            ]
        )
        await session.commit()
        refused = await suc.run_guarded_upgrade(session, operator_authorized=True)
        assert refused.published is False
        assert refused.decision.reason_code == "settings_collision"

        # While refused, the operator changes the instance configuration.
        service = SettingsCatalogService(env={}, session=session)
        await service.apply_overrides(
            changes={TOKEN: "env://OPERATOR_NEW", PUBLISH: "branch"}
        )
        await service.apply_overrides(changes={POLICY: "permissive"})
        await service.reset_override(POLICY)

        # Resolving only the unrelated key must not publish the stale personal
        # value over the operator's confirmed writes.
        still_refused = await suc.run_guarded_upgrade(
            session, operator_authorized=True, collision_resolutions={CANARY: 40}
        )
        assert still_refused.published is False
        assert still_refused.decision.reason_code == "settings_collision"
        assert TOKEN in still_refused.decision.detail
        assert POLICY in still_refused.decision.detail
        # An equal instance write agrees with the personal choice.
        assert PUBLISH not in still_refused.decision.detail
        assert "env://" not in str(still_refused.to_sanitized_dict())
        effective = await service.effective_value_async(TOKEN)
        assert (effective.value, effective.source) == (
            "env://OPERATOR_NEW",
            "instance_override",
        )

        # The disposition may keep the operator's instance write; a reset
        # cannot be named, so the operator confirms the retained value.
        resolved = await suc.run_guarded_upgrade(
            session,
            operator_authorized=True,
            collision_resolutions={
                CANARY: 40,
                TOKEN: "env://OPERATOR_NEW",
                POLICY: "allowlist",
            },
        )
        assert resolved.published is True
        rows = {row.key: row for row in await _rows(session)}
        assert {key: (row.scope, row.value_json) for key, row in rows.items()} == {
            TOKEN: (None, "env://OPERATOR_NEW"),
            PUBLISH: (None, "branch"),
            POLICY: (None, "allowlist"),
            CANARY: (None, 40),
        }
        converted = {
            event.key: event
            for event in (await session.execute(select(SettingsAuditEvent))).scalars()
            if event.event_type == "settings.override.converted"
        }
        assert converted[TOKEN].old_value_json == ["env://STALE_PERSONAL"]
        assert converted[TOKEN].new_value_json == "env://OPERATOR_NEW"
