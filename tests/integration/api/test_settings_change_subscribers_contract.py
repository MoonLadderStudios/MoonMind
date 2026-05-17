"""Integration coverage for the SettingsChangePublisher and its four default
subscribers (MM-710).

These tests drive the same Settings PATCH endpoint that Mission Control uses,
then assert observable side effects on the per-family subscriber instances.
The publisher is isolated per test by resetting the process-wide singleton and
registering a fresh set of default subscribers against it.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.auth_providers import get_current_user
from api_service.db import base as db_base
from api_service.db.models import Base
from api_service.main import app
from api_service.services.settings_change_publisher import (
    get_settings_change_publisher,
    reset_default_settings_change_publisher,
)
from api_service.services.settings_change_subscribers import (
    OperationalControlsSubscriber,
    ProviderProfileManagerSubscriber,
    TaskCreationDefaultsSubscriber,
    WorkerReloadSubscriber,
    register_default_subscribers,
)


pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

SETTINGS_USER_DEP = get_current_user()


@pytest_asyncio.fixture
async def settings_subscriber_env(tmp_path):
    """Provision a hermetic settings DB, swap in an isolated publisher with the
    four default subscribers registered, and yield a handle to the subscribers
    for assertion."""

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/settings-subscribers.db"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    original_session_maker = db_base.async_session_maker
    db_base.async_session_maker = session_maker

    user = SimpleNamespace(
        id=uuid4(),
        email="settings-subscribers@example.com",
        is_superuser=True,
        settings_permissions={
            "settings.catalog.read",
            "settings.effective.read",
            "settings.user.write",
            "settings.workspace.write",
        },
        workspace_id=uuid4(),
    )
    app.dependency_overrides[SETTINGS_USER_DEP] = lambda: user

    reset_default_settings_change_publisher()
    publisher = get_settings_change_publisher()
    register_default_subscribers(publisher)

    handle = SimpleNamespace(
        session_maker=session_maker,
        publisher=publisher,
        task_creation=next(
            sub
            for sub in publisher.subscribers_for("task_creation_defaults")
            if isinstance(sub, TaskCreationDefaultsSubscriber)
        ),
        worker_reload=next(
            sub
            for sub in publisher.subscribers_for("worker_reloaders")
            if isinstance(sub, WorkerReloadSubscriber)
        ),
        provider_profile=next(
            sub
            for sub in publisher.subscribers_for("provider_profile_manager")
            if isinstance(sub, ProviderProfileManagerSubscriber)
        ),
        operational=next(
            sub
            for sub in publisher.subscribers_for("operational_controls")
            if isinstance(sub, OperationalControlsSubscriber)
        ),
    )

    try:
        yield handle
    finally:
        app.dependency_overrides.pop(SETTINGS_USER_DEP, None)
        db_base.async_session_maker = original_session_maker
        reset_default_settings_change_publisher()
        await engine.dispose()


async def test_task_creation_defaults_subscriber_fires_on_next_task_setting(
    settings_subscriber_env,
):
    start_version = settings_subscriber_env.task_creation.version

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.default_task_runtime": "claude_code"},
                "expected_versions": {"workflow.default_task_runtime": 1},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["change_events"][0]["refresh_targets"].count("task_creation_defaults") == 1
    assert (
        settings_subscriber_env.task_creation.version == start_version + 1
    ), "task_creation_defaults subscriber should fire exactly once for a next_task change"
    latest = settings_subscriber_env.task_creation.recent_events[-1]
    assert latest.key == "workflow.default_task_runtime"
    assert latest.apply_mode == "next_task"


async def test_worker_reload_subscriber_records_soft_reload_intent_for_worker_reload_mode(
    settings_subscriber_env,
):
    start_count = len(settings_subscriber_env.worker_reload.pending_intents)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"skills.policy_mode": "allowlist"},
                "expected_versions": {"skills.policy_mode": 1},
            },
        )

    assert response.status_code == 200
    intents = list(settings_subscriber_env.worker_reload.pending_intents)
    assert len(intents) == start_count + 1
    intent = intents[-1]
    assert intent.intent == "soft_reload"
    assert intent.apply_mode == "worker_reload"
    assert intent.key == "skills.policy_mode"
    assert intent.refresh_target == "worker_reloaders"
    assert "workflow_runtime" in intent.affected_systems


async def test_provider_profile_manager_subscriber_fires_on_default_profile_change(
    settings_subscriber_env,
):
    start_version = settings_subscriber_env.provider_profile.version
    start_count = len(settings_subscriber_env.provider_profile.pending_intents)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.default_provider_profile_ref": None},
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
            },
        )

    assert response.status_code == 200
    intents = list(settings_subscriber_env.provider_profile.pending_intents)
    assert len(intents) == start_count + 1
    intent = intents[-1]
    assert intent.key == "workflow.default_provider_profile_ref"
    assert intent.apply_mode == "next_launch"
    assert intent.refresh_target == "provider_profile_manager"
    assert settings_subscriber_env.provider_profile.version == start_version + 1


async def test_operational_controls_subscriber_fires_on_operation_mode_change(
    settings_subscriber_env,
):
    start_version = settings_subscriber_env.operational.version
    start_count = len(settings_subscriber_env.operational.pending_intents)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.operation_mode": "maintenance"},
                "expected_versions": {"workflow.operation_mode": 1},
                "confirmation": "I confirm this operation mode change.",
            },
        )

    assert response.status_code == 200, response.text
    intents = list(settings_subscriber_env.operational.pending_intents)
    assert len(intents) == start_count + 1
    intent = intents[-1]
    assert intent.key == "workflow.operation_mode"
    assert intent.apply_mode == "manual_operation"
    assert intent.refresh_target == "operational_controls"
    assert settings_subscriber_env.operational.version == start_version + 1


async def test_multi_key_patch_fans_out_one_event_per_key(settings_subscriber_env):
    start_version = settings_subscriber_env.task_creation.version

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {
                    "workflow.default_task_runtime": "claude_code",
                    "workflow.default_publish_mode": "branch",
                },
                "expected_versions": {
                    "workflow.default_task_runtime": 1,
                    "workflow.default_publish_mode": 1,
                },
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["change_events"]) == 2
    assert settings_subscriber_env.task_creation.version == start_version + 2
    recorded = list(settings_subscriber_env.task_creation.recent_events)[-2:]
    keys = {event.key for event in recorded}
    assert keys == {"workflow.default_task_runtime", "workflow.default_publish_mode"}


async def test_subscriber_failure_does_not_change_http_response(
    settings_subscriber_env,
):
    """SCN-6 + SC-003: a failing subscriber does not break the API response and
    does not prevent other subscribers from running."""

    class _FailingSubscriber:
        name = "failing_for_test"
        refresh_targets = frozenset({"task_creation_defaults"})

        def __init__(self) -> None:
            self.invoked = False

        async def __call__(self, event) -> None:
            self.invoked = True
            raise RuntimeError("intentional subscriber failure")

    failing = _FailingSubscriber()
    settings_subscriber_env.publisher.register(failing)
    start_version = settings_subscriber_env.task_creation.version

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.default_task_runtime": "gemini_cli"},
                "expected_versions": {"workflow.default_task_runtime": 1},
            },
        )

    assert response.status_code == 200
    assert failing.invoked is True
    assert (
        settings_subscriber_env.task_creation.version == start_version + 1
    ), "real subscriber must still fire when a sibling subscriber raised"


async def test_secret_ref_setting_change_does_not_publish_secret_payload(
    settings_subscriber_env,
):
    """FR-010: secret values must not appear in published event payloads."""

    captured: list[dict] = []

    class _SnifferSubscriber:
        name = "secret_sniffer"
        refresh_targets = frozenset({"settings_catalog"})

        async def __call__(self, event) -> None:
            captured.append(event.model_dump())

    settings_subscriber_env.publisher.register(_SnifferSubscriber())

    # Use an env-ref name that passes the unsafe-payload heuristics (no secret
    # prefix); FR-010 covers what fields the published event carries, not how
    # the validation layer rejects sensitive payloads.
    env_alias = "MM710_TEST_ENV_TOKEN_REF"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings/user",
            json={
                "changes": {"integrations.github.token_ref": f"env://{env_alias}"},
                "expected_versions": {"integrations.github.token_ref": 1},
            },
        )

    assert response.status_code == 200, response.text
    assert captured, "expected the sniffer subscriber to receive the event"
    payload = captured[-1]
    assert payload["key"] == "integrations.github.token_ref"
    assert set(payload.keys()) == {
        "event_type",
        "key",
        "scope",
        "source",
        "apply_mode",
        "actor_user_id",
        "changed_at",
        "affected_systems",
        "refresh_targets",
    }
    # The event payload must not include the value being set.
    assert env_alias not in repr(payload.get("source"))
    assert env_alias not in repr(payload.get("key"))


@pytest_asyncio.fixture
async def settings_subscriber_env_with_hooks(tmp_path):
    """Same hermetic env as ``settings_subscriber_env`` but with the production
    hook factories wired in. The Temporal client provider is mocked so we can
    assert observable signaling without touching a live Temporal server.
    """

    from unittest.mock import AsyncMock, MagicMock

    from api_service.services.settings_change_hooks import (
        make_provider_profile_refresh_hook,
        make_worker_reload_broadcast_hook,
    )

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/settings-subscribers-hooks.db"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    original_session_maker = db_base.async_session_maker
    db_base.async_session_maker = session_maker

    user = SimpleNamespace(
        id=uuid4(),
        email="settings-subscribers-hooks@example.com",
        is_superuser=True,
        settings_permissions={
            "settings.catalog.read",
            "settings.effective.read",
            "settings.user.write",
            "settings.workspace.write",
        },
        workspace_id=uuid4(),
    )
    app.dependency_overrides[SETTINGS_USER_DEP] = lambda: user

    reset_default_settings_change_publisher()
    publisher = get_settings_change_publisher()

    signaled_workflows: list[str] = []
    signaled_calls: list[tuple[str, dict]] = []
    fake_client = MagicMock()

    def _handle_for(workflow_id: str):
        signaled_workflows.append(workflow_id)
        handle = AsyncMock()

        async def _signal(name: str, payload: dict) -> None:
            signaled_calls.append((name, payload))

        handle.signal.side_effect = _signal
        return handle

    fake_client.get_workflow_handle = MagicMock(side_effect=_handle_for)

    runtime_ids = ["claude_code", "codex_cli"]

    provider_hook = make_provider_profile_refresh_hook(
        temporal_client_provider=AsyncMock(return_value=fake_client),
        runtime_ids_provider=AsyncMock(return_value=runtime_ids),
    )

    worker_broadcasts: list[dict] = []

    async def _record_broadcast(payload: dict) -> None:
        worker_broadcasts.append(payload)

    worker_hook = make_worker_reload_broadcast_hook(
        broadcaster=_record_broadcast,
    )

    register_default_subscribers(
        publisher,
        worker_on_reload=worker_hook,
        provider_profile_on_refresh=provider_hook,
    )

    handle = SimpleNamespace(
        publisher=publisher,
        signaled_workflows=signaled_workflows,
        signaled_calls=signaled_calls,
        worker_broadcasts=worker_broadcasts,
        runtime_ids=runtime_ids,
    )
    try:
        yield handle
    finally:
        app.dependency_overrides.pop(SETTINGS_USER_DEP, None)
        db_base.async_session_maker = original_session_maker
        reset_default_settings_change_publisher()
        await engine.dispose()


async def test_provider_profile_refresh_hook_signals_each_known_runtime_on_patch(
    settings_subscriber_env_with_hooks,
):
    """End-to-end: patching ``workflow.default_provider_profile_ref`` must reach
    every registered ProviderProfileManager workflow via ``sync_profiles``."""

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"workflow.default_provider_profile_ref": None},
                "expected_versions": {"workflow.default_provider_profile_ref": 1},
            },
        )

    assert response.status_code == 200
    assert sorted(settings_subscriber_env_with_hooks.signaled_workflows) == sorted(
        f"provider-profile-manager:{rid}"
        for rid in settings_subscriber_env_with_hooks.runtime_ids
    )
    assert all(
        name == "sync_profiles"
        for name, _ in settings_subscriber_env_with_hooks.signaled_calls
    )
    assert len(settings_subscriber_env_with_hooks.signaled_calls) == len(
        settings_subscriber_env_with_hooks.runtime_ids
    )


async def test_worker_reload_hook_broadcasts_intent_on_patch(
    settings_subscriber_env_with_hooks,
):
    """End-to-end: patching a worker_reload-mode setting must reach the
    configured broadcaster with the normalized intent payload."""

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.patch(
            "/api/v1/settings/workspace",
            json={
                "changes": {"skills.policy_mode": "allowlist"},
                "expected_versions": {"skills.policy_mode": 1},
            },
        )

    assert response.status_code == 200
    broadcasts = settings_subscriber_env_with_hooks.worker_broadcasts
    assert len(broadcasts) == 1
    payload = broadcasts[0]
    assert payload["intent"] == "soft_reload"
    assert payload["apply_mode"] == "worker_reload"
    assert payload["key"] == "skills.policy_mode"
    assert "workflow_runtime" in payload["affected_systems"]
    # The Temporal client provider must not be exercised for worker_reload-only
    # settings: refresh hook is keyed off the provider_profile_manager refresh
    # target which only fires when applies_to includes provider_profiles.
    assert settings_subscriber_env_with_hooks.signaled_workflows == []
