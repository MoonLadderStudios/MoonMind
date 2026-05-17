"""Unit tests for the SettingsChangePublisher (MM-710)."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from api_service.services.settings_catalog import SettingsChangeEvent
from api_service.services.settings_change_publisher import (
    SettingsChangePublisher,
    SettingsChangeSubscriber,
    get_settings_change_publisher,
    reset_default_settings_change_publisher,
)


def _make_event(
    *,
    key: str = "workflow.default_task_runtime",
    scope: str = "workspace",
    source: str | None = None,
    apply_mode: str = "next_task",
    affected_systems: Iterable[str] = ("task_creation", "workflow_runtime"),
    refresh_targets: Iterable[str] = (
        "settings_catalog",
        "task_creation_defaults",
    ),
) -> SettingsChangeEvent:
    return SettingsChangeEvent(
        key=key,
        scope=scope,
        source=source or f"{scope}_override",
        apply_mode=apply_mode,
        actor_user_id=uuid4(),
        changed_at=datetime.now(timezone.utc),
        affected_systems=list(affected_systems),
        refresh_targets=list(refresh_targets),
    )


class _RecordingSubscriber:
    def __init__(
        self,
        *,
        name: str,
        refresh_targets: Iterable[str],
        side_effect: Exception | None = None,
    ) -> None:
        self.name = name
        self.refresh_targets = frozenset(refresh_targets)
        self.received: list[SettingsChangeEvent] = []
        self._side_effect = side_effect

    async def __call__(self, event: SettingsChangeEvent) -> None:
        self.received.append(event)
        if self._side_effect is not None:
            raise self._side_effect


@pytest.fixture(autouse=True)
def _reset_default_publisher_around_test():
    reset_default_settings_change_publisher()
    yield
    reset_default_settings_change_publisher()


def test_subscribers_for_unknown_target_returns_empty_list():
    publisher = SettingsChangePublisher()

    assert publisher.subscribers_for("unknown_target") == []


def test_register_rejects_duplicate_name():
    publisher = SettingsChangePublisher()
    subscriber_a = _RecordingSubscriber(name="task_creation_defaults", refresh_targets={"task_creation_defaults"})
    subscriber_b = _RecordingSubscriber(name="task_creation_defaults", refresh_targets={"task_creation_defaults"})

    publisher.register(subscriber_a)

    with pytest.raises(ValueError):
        publisher.register(subscriber_b)


def test_register_requires_non_empty_refresh_targets():
    publisher = SettingsChangePublisher()
    subscriber = _RecordingSubscriber(name="empty", refresh_targets=())

    with pytest.raises(ValueError):
        publisher.register(subscriber)


def test_unregister_is_idempotent():
    publisher = SettingsChangePublisher()
    subscriber = _RecordingSubscriber(name="solo", refresh_targets={"settings_catalog"})
    publisher.register(subscriber)

    publisher.unregister("solo")
    publisher.unregister("solo")

    assert publisher.subscribers_for("settings_catalog") == []


@pytest.mark.asyncio
async def test_publish_fans_out_to_each_matching_subscriber():
    publisher = SettingsChangePublisher()
    task_sub = _RecordingSubscriber(
        name="task_creation_defaults",
        refresh_targets={"task_creation_defaults"},
    )
    catalog_sub = _RecordingSubscriber(
        name="settings_catalog_listener",
        refresh_targets={"settings_catalog"},
    )
    other_sub = _RecordingSubscriber(
        name="provider_profile_manager",
        refresh_targets={"provider_profile_manager"},
    )
    publisher.register(task_sub)
    publisher.register(catalog_sub)
    publisher.register(other_sub)

    event = _make_event(
        refresh_targets=("settings_catalog", "task_creation_defaults"),
    )

    await publisher.publish([event])

    assert task_sub.received == [event]
    assert catalog_sub.received == [event]
    assert other_sub.received == []


@pytest.mark.asyncio
async def test_publish_dedupes_subscriber_when_event_lists_multiple_matching_targets():
    publisher = SettingsChangePublisher()
    multi_sub = _RecordingSubscriber(
        name="multi_target",
        refresh_targets={
            "task_creation_defaults",
            "provider_profile_manager",
            "settings_catalog",
        },
    )
    publisher.register(multi_sub)

    event = _make_event(
        refresh_targets=(
            "settings_catalog",
            "task_creation_defaults",
            "provider_profile_manager",
        ),
    )

    await publisher.publish([event])

    assert multi_sub.received == [event]


@pytest.mark.asyncio
async def test_publish_no_subscribers_for_target_is_a_no_op():
    publisher = SettingsChangePublisher()

    event = _make_event(
        refresh_targets=("task_creation_defaults",),
    )

    await publisher.publish([event])


@pytest.mark.asyncio
async def test_publish_with_empty_refresh_targets_is_a_no_op():
    publisher = SettingsChangePublisher()
    sub = _RecordingSubscriber(
        name="task_creation_defaults",
        refresh_targets={"task_creation_defaults"},
    )
    publisher.register(sub)

    event = _make_event(refresh_targets=())

    await publisher.publish([event])

    assert sub.received == []


@pytest.mark.asyncio
async def test_publish_isolates_subscriber_failures(caplog: pytest.LogCaptureFixture):
    publisher = SettingsChangePublisher()
    failing_sub = _RecordingSubscriber(
        name="failing",
        refresh_targets={"task_creation_defaults"},
        side_effect=RuntimeError("boom"),
    )
    real_sub = _RecordingSubscriber(
        name="real",
        refresh_targets={"task_creation_defaults"},
    )
    publisher.register(failing_sub)
    publisher.register(real_sub)

    event = _make_event(refresh_targets=("task_creation_defaults",))

    with caplog.at_level(logging.ERROR, logger="api_service.services.settings_change_publisher"):
        await publisher.publish([event])

    assert failing_sub.received == [event]
    assert real_sub.received == [event]
    failure_records = [
        record
        for record in caplog.records
        if "subscriber" in record.getMessage().lower() and "boom" not in record.getMessage()
        or "failing" in record.getMessage()
    ]
    assert failure_records, "expected at least one structured failure log entry"
    failure_record = next(
        record for record in caplog.records if "failing" in record.getMessage()
    )
    payload: dict[str, Any] = getattr(failure_record, "settings_change_publish", {})
    assert payload.get("subscriber_name") == "failing"
    assert payload.get("exception_class") == "RuntimeError"
    assert payload.get("key") == event.key
    assert payload.get("scope") == event.scope
    assert payload.get("apply_mode") == event.apply_mode


def test_get_settings_change_publisher_returns_stable_singleton():
    a = get_settings_change_publisher()
    b = get_settings_change_publisher()

    assert a is b


def test_reset_default_settings_change_publisher_replaces_singleton():
    first = get_settings_change_publisher()
    reset_default_settings_change_publisher()
    second = get_settings_change_publisher()

    assert first is not second


def test_subscriber_protocol_runtime_checkable():
    sub = _RecordingSubscriber(
        name="task_creation_defaults",
        refresh_targets={"task_creation_defaults"},
    )

    assert isinstance(sub, SettingsChangeSubscriber)


@pytest.mark.asyncio
async def test_secret_ref_event_carries_only_documented_fields():
    """FR-010: the published event must not contain raw secret values."""

    event = _make_event(
        key="integrations.github.token_ref",
        scope="user",
        apply_mode="next_launch",
        affected_systems=("github", "integrations"),
        refresh_targets=("settings_catalog",),
    )

    dumped = event.model_dump()

    assert set(dumped.keys()) == {
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
    for value in dumped.values():
        text = repr(value)
        assert "ghp_" not in text
        assert "github_pat_" not in text
