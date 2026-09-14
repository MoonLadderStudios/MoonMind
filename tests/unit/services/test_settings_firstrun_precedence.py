"""Precedence and authority-boundary coverage for first-run configuration.

Source issue: MoonLadderStudios/MoonMind#3941 (acceptance REQ-03 and REQ-06).

Resolution follows ``docs/Security/SettingsSystem.md``: built-in defaults,
deployment (environment/config) values, persisted scope overrides, explicit
request values, with operator locks winning over everything. These tests pin
the truthful behavior of that chain:

* unset vs empty vs false vs zero stay distinguishable;
* operator locks win and make the key read-only for ordinary writers;
* scope authorization rejects writes at scopes the setting does not allow;
* reset deletes the override and reveals the inherited value;
* invalid values are rejected with structured issues;
* an unavailable settings store fails closed instead of falling back to a
  different credential or a more permissive policy;
* raw secrets never land in generic overrides and never leak into serialized
  responses; workspace ceilings (operator policy) bound workspace values.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base
from api_service.services.settings_catalog import (
    SettingConstraints,
    SettingRegistryEntry,
    SettingsCatalogService,
)

_BOOL_ENTRY = SettingRegistryEntry(
    key="test.feature_enabled",
    title="Feature Enabled",
    description="Tristate-checked feature toggle.",
    category="Test",
    section="user-workspace",
    value_type="boolean",
    ui="toggle",
    scopes=("workspace",),
    default_value=True,
    env_aliases=("TEST_FIRSTRUN_FEATURE_ENABLED",),
    order=1,
)

_INT_ENTRY = SettingRegistryEntry(
    key="test.retry_budget",
    title="Retry Budget",
    description="Retry budget that treats zero as a real value.",
    category="Test",
    section="user-workspace",
    value_type="integer",
    ui="number",
    scopes=("workspace",),
    default_value=8,
    env_aliases=("TEST_FIRSTRUN_RETRY_BUDGET",),
    constraints=SettingConstraints(minimum=0, maximum=100),
    order=2,
)

_LOCKED_ENTRY = SettingRegistryEntry(
    key="test.locked_ceiling",
    title="Locked Ceiling",
    description="Operator-locked ceiling ordinary writers cannot move.",
    category="Test",
    section="user-workspace",
    value_type="integer",
    ui="number",
    scopes=("workspace",),
    default_value=10,
    env_aliases=("TEST_FIRSTRUN_LOCKED_CEILING",),
    operator_locked_value=5,
    operator_lock_reason="operator deployment ceiling",
    order=3,
)

_USER_SCOPED_ENTRY = SettingRegistryEntry(
    key="test.personal_default",
    title="Personal Default",
    description="User-scoped preference.",
    category="Test",
    section="user-workspace",
    value_type="string",
    ui="input",
    scopes=("user",),
    default_value="personal",
    order=4,
)

_TEST_REGISTRY = (_BOOL_ENTRY, _INT_ENTRY, _LOCKED_ENTRY, _USER_SCOPED_ENTRY)


def _service(env: dict[str, str], **kwargs: Any) -> SettingsCatalogService:
    return SettingsCatalogService(env=env, registry=_TEST_REGISTRY, **kwargs)


def _session_maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/firstrun.db")

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    asyncio.run(engine.dispose())


@pytest.fixture()
def session_maker(tmp_path):
    yield from _session_maker(tmp_path)


def test_unset_inherits_default_while_zero_false_and_empty_stay_distinct():
    assert _service({}).effective_value(
        "test.feature_enabled", scope="workspace"
    ).source == "default"
    assert _service({}).effective_value(
        "test.retry_budget", scope="workspace"
    ).source == "default"

    assert _service({"TEST_FIRSTRUN_FEATURE_ENABLED": "false"}).effective_value(
        "test.feature_enabled", scope="workspace"
    ).value is False
    assert _service({"TEST_FIRSTRUN_FEATURE_ENABLED": "0"}).effective_value(
        "test.feature_enabled", scope="workspace"
    ).value is False
    assert _service({"TEST_FIRSTRUN_FEATURE_ENABLED": "1"}).effective_value(
        "test.feature_enabled", scope="workspace"
    ).value is True

    budgeted = _service({"TEST_FIRSTRUN_RETRY_BUDGET": "0"}).effective_value(
        "test.retry_budget", scope="workspace"
    )
    assert budgeted.value == 0
    assert isinstance(budgeted.value, int)
    assert budgeted.source == "environment"

    # Empty is neither unset (it records an environment source) nor a
    # successful false/zero coercion: the distinction is preserved instead of
    # being silently normalized.
    empty_bool = _service({"TEST_FIRSTRUN_FEATURE_ENABLED": ""}).effective_value(
        "test.feature_enabled", scope="workspace"
    )
    assert empty_bool.source == "environment"
    assert empty_bool.value == ""
    empty_int = _service({"TEST_FIRSTRUN_RETRY_BUDGET": ""}).effective_value(
        "test.retry_budget", scope="workspace"
    )
    assert empty_int.source == "environment"
    assert empty_int.value == ""


def test_operator_lock_wins_over_environment_and_is_read_only():
    service = _service({"TEST_FIRSTRUN_LOCKED_CEILING": "99"})
    effective = service.effective_value("test.locked_ceiling", scope="workspace")
    assert effective.value == 5
    assert effective.source == "operator_lock"
    assert effective.read_only is True
    assert effective.read_only_reason == "operator deployment ceiling"

    with pytest.raises(PermissionError):
        service.ensure_write_allowed("test.locked_ceiling", scope="workspace")
    assert service.write_lock_error_code("test.locked_ceiling") == "operator_locked"


@pytest.mark.asyncio
async def test_operator_locked_write_is_rejected_with_structured_issue():
    service = _service({})
    response = await service.validate_changes(
        scope="workspace", changes={"test.locked_ceiling": 99}
    )
    assert response.accepted is False
    assert response.issues_by_key["test.locked_ceiling"][0].code == "operator_locked"


@pytest.mark.asyncio
async def test_scope_authorization_rejects_disallowed_scope():
    service = _service({})
    response = await service.validate_changes(
        scope="workspace", changes={"test.personal_default": "x"}
    )
    assert response.accepted is False
    assert (
        response.issues_by_key["test.personal_default"][0].code
        == "unsupported_scope"
    )
    with pytest.raises(ValueError):
        service.effective_value("test.personal_default", scope="workspace")


@pytest.mark.asyncio
async def test_reset_reveals_the_inherited_value(session_maker):
    async with session_maker() as session:
        service = SettingsCatalogService(
            env={"TEST_FIRSTRUN_RETRY_BUDGET": "3"},
            registry=_TEST_REGISTRY,
            session=session,
        )
        await service.apply_overrides(
            scope="workspace",
            changes={"test.retry_budget": 7},
            expected_versions={"test.retry_budget": 1},
        )
        assert (
            await service.effective_value_async(
                "test.retry_budget", scope="workspace"
            )
        ).value == 7
        reset = await service.reset_override(
            "test.retry_budget", scope="workspace"
        )
        assert reset.value == 3
        assert reset.source == "environment"


@pytest.mark.asyncio
async def test_invalid_values_are_rejected():
    service = _service({})
    response = await service.validate_changes(
        scope="workspace", changes={"test.retry_budget": 101}
    )
    assert response.accepted is False
    assert any(
        issue.code == "numeric_constraint_failed"
        for issue in response.issues_by_key["test.retry_budget"]
    )


class _UnavailableStoreSession:
    """A settings store that is down: every access raises."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("settings store unavailable")


@pytest.mark.asyncio
async def test_unavailable_store_fails_closed_without_fallback():
    service = SettingsCatalogService(
        env={},
        registry=_TEST_REGISTRY,
        session=_UnavailableStoreSession(),  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="settings store unavailable"):
        await service.effective_value_async(
            "test.retry_budget", scope="workspace"
        )


@pytest.mark.asyncio
async def test_raw_secret_never_lands_in_generic_overrides(session_maker):
    raw_secret = "gh" + "p_raw_plaintext_candidate"
    async with session_maker() as session:
        service = SettingsCatalogService(env={}, session=session)
        response = await service.validate_changes(
            scope="workspace",
            changes={"integrations.github.token_ref": raw_secret},
            expected_versions={"integrations.github.token_ref": 1},
        )
    assert response.accepted is False
    assert raw_secret not in response.model_dump_json()


@pytest.mark.asyncio
async def test_workspace_ceiling_bounds_workspace_values():
    service = SettingsCatalogService(
        env={},
        workspace_policy={"max_canary_percent": 10},
    )
    response = await service.validate_changes(
        scope="workspace", changes={"skills.canary_percent": 50}
    )
    assert response.accepted is False
    assert (
        response.issues_by_key["skills.canary_percent"][0].code
        == "max_canary_percent_exceeded"
    )
    allowed = await service.validate_changes(
        scope="workspace", changes={"skills.canary_percent": 10}
    )
    assert allowed.accepted is True
