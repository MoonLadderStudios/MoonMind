"""Unit tests for the per-family setting change subscribers (MM-710)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

import pytest

from api_service.services.settings_catalog import SettingsChangeEvent
from api_service.services.settings_change_publisher import (
    SettingsChangePublisher,
    reset_default_settings_change_publisher,
)
from api_service.services.settings_change_subscribers import (
    OperationalControlsSubscriber,
    OperationalRefreshIntent,
    ProviderProfileManagerSubscriber,
    ProviderProfileRefreshIntent,
    TaskCreationDefaultsSubscriber,
    WorkerReloadIntent,
    WorkerReloadSubscriber,
    register_default_subscribers,
)


def _event(
    *,
    key: str,
    apply_mode: str,
    affected_systems: Iterable[str],
    refresh_targets: Iterable[str],
    scope: str = "workspace",
) -> SettingsChangeEvent:
    return SettingsChangeEvent(
        key=key,
        scope=scope,
        source=f"{scope}_override",
        apply_mode=apply_mode,
        actor_user_id=uuid4(),
        changed_at=datetime.now(timezone.utc),
        affected_systems=list(affected_systems),
        refresh_targets=list(refresh_targets),
    )


@pytest.fixture(autouse=True)
def _reset_default_publisher_around_test():
    reset_default_settings_change_publisher()
    yield
    reset_default_settings_change_publisher()


@pytest.mark.asyncio
async def test_task_creation_defaults_subscriber_increments_and_records():
    subscriber = TaskCreationDefaultsSubscriber()
    start_version = subscriber.version

    event = _event(
        key="workflow.default_task_runtime",
        apply_mode="next_task",
        affected_systems=("task_creation", "workflow_runtime"),
        refresh_targets=("settings_catalog", "task_creation_defaults"),
    )

    await subscriber(event)
    await subscriber(event)

    assert subscriber.version == start_version + 2
    assert list(subscriber.recent_events)[-2:] == [event, event]
    assert subscriber.name == "task_creation_defaults"
    assert subscriber.refresh_targets == frozenset({"task_creation_defaults"})


@pytest.mark.asyncio
async def test_worker_reload_subscriber_infers_soft_reload_for_worker_reload_mode():
    subscriber = WorkerReloadSubscriber()

    event = _event(
        key="skills.policy_mode",
        apply_mode="worker_reload",
        affected_systems=("workflow_runtime", "skills"),
        refresh_targets=("settings_catalog", "worker_reloaders"),
    )

    await subscriber(event)

    assert len(subscriber.pending_intents) == 1
    intent = subscriber.pending_intents[-1]
    assert isinstance(intent, WorkerReloadIntent)
    assert intent.intent == "soft_reload"
    assert intent.key == "skills.policy_mode"
    assert intent.scope == "workspace"
    assert intent.apply_mode == "worker_reload"
    assert intent.refresh_target == "worker_reloaders"
    assert intent.affected_systems == ("workflow_runtime", "skills")


@pytest.mark.asyncio
async def test_worker_reload_subscriber_records_process_restart_for_restart_mode():
    subscriber = WorkerReloadSubscriber()

    event = _event(
        key="hypothetical.process_restart_setting",
        apply_mode="process_restart",
        affected_systems=("workflow_runtime",),
        refresh_targets=("worker_reloaders",),
    )

    await subscriber(event)

    intent = subscriber.pending_intents[-1]
    assert intent.intent == "process_restart"


@pytest.mark.asyncio
async def test_worker_reload_subscriber_invokes_on_reload_hook_when_set():
    seen: list[WorkerReloadIntent] = []

    async def hook(intent: WorkerReloadIntent) -> None:
        seen.append(intent)

    subscriber = WorkerReloadSubscriber(on_reload=hook)

    event = _event(
        key="skills.policy_mode",
        apply_mode="worker_reload",
        affected_systems=("workflow_runtime", "skills"),
        refresh_targets=("worker_reloaders",),
    )

    await subscriber(event)

    assert len(seen) == 1
    assert seen[0].intent == "soft_reload"


@pytest.mark.asyncio
async def test_provider_profile_manager_subscriber_records_intent_and_version():
    subscriber = ProviderProfileManagerSubscriber()
    start_version = subscriber.version

    event = _event(
        key="workflow.default_provider_profile_ref",
        apply_mode="next_launch",
        affected_systems=("task_creation", "workflow_runtime", "provider_profiles"),
        refresh_targets=(
            "provider_profile_manager",
            "settings_catalog",
            "task_creation_defaults",
        ),
    )

    await subscriber(event)

    assert subscriber.version == start_version + 1
    intent = subscriber.pending_intents[-1]
    assert isinstance(intent, ProviderProfileRefreshIntent)
    assert intent.key == "workflow.default_provider_profile_ref"
    assert intent.apply_mode == "next_launch"
    assert intent.refresh_target == "provider_profile_manager"


@pytest.mark.asyncio
async def test_operational_controls_subscriber_increments_and_records():
    subscriber = OperationalControlsSubscriber()
    start_version = subscriber.version

    event = _event(
        key="workflow.operation_mode",
        apply_mode="manual_operation",
        affected_systems=("operations",),
        refresh_targets=("operational_controls", "settings_catalog"),
    )

    await subscriber(event)

    assert subscriber.version == start_version + 1
    intent = subscriber.pending_intents[-1]
    assert isinstance(intent, OperationalRefreshIntent)
    assert intent.key == "workflow.operation_mode"
    assert intent.apply_mode == "manual_operation"
    assert intent.refresh_target == "operational_controls"


def test_register_default_subscribers_attaches_four_subscribers_to_publisher():
    publisher = SettingsChangePublisher()

    register_default_subscribers(publisher)

    assert {sub.name for sub in publisher.subscribers_for("task_creation_defaults")} == {
        "task_creation_defaults"
    }
    assert {sub.name for sub in publisher.subscribers_for("worker_reloaders")} == {
        "worker_reloaders"
    }
    assert {sub.name for sub in publisher.subscribers_for("provider_profile_manager")} == {
        "provider_profile_manager"
    }
    assert {sub.name for sub in publisher.subscribers_for("operational_controls")} == {
        "operational_controls"
    }


def test_register_default_subscribers_is_idempotent():
    publisher = SettingsChangePublisher()

    register_default_subscribers(publisher)
    register_default_subscribers(publisher)

    assert len(publisher.subscribers_for("task_creation_defaults")) == 1
    assert len(publisher.subscribers_for("worker_reloaders")) == 1
    assert len(publisher.subscribers_for("provider_profile_manager")) == 1
    assert len(publisher.subscribers_for("operational_controls")) == 1


def test_subscriber_ledgers_are_bounded():
    """Bounded deques must prevent unbounded memory growth in long-lived processes."""

    task_sub = TaskCreationDefaultsSubscriber()
    worker_sub = WorkerReloadSubscriber()
    profile_sub = ProviderProfileManagerSubscriber()
    ops_sub = OperationalControlsSubscriber()

    for subscriber, ledger_attr in (
        (task_sub, "recent_events"),
        (worker_sub, "pending_intents"),
        (profile_sub, "pending_intents"),
        (ops_sub, "pending_intents"),
    ):
        ledger = getattr(subscriber, ledger_attr)
        assert ledger.maxlen is not None
        assert ledger.maxlen >= 32
