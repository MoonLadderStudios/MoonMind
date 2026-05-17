"""In-process publisher for SettingsChangeEvent fan-out (MM-710).

This module owns the `SettingsChangePublisher` and the subscriber protocol.
Subscribers register against one or more ``refresh_target`` strings and receive
events emitted by ``SettingsCatalogService.apply_overrides`` after the database
transaction commits.

Per ``specs/360-setting-change-subscribers/contracts/settings_change_publisher.md``:

- registration enforces unique subscriber names and non-empty refresh_targets;
- dispatch deduplicates per-(event, subscriber) pair across overlapping
  refresh_targets;
- subscriber failures are caught and logged structurally, never propagated;
- events with empty refresh_targets are a no-op dispatch.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Iterable
from typing import Protocol, runtime_checkable

from api_service.services.settings_catalog import SettingsChangeEvent

logger = logging.getLogger(__name__)


@runtime_checkable
class SettingsChangeSubscriber(Protocol):
    """Callable that consumes ``SettingsChangeEvent`` instances."""

    name: str
    refresh_targets: frozenset[str]

    async def __call__(  # pragma: no cover - protocol stub
        self, event: SettingsChangeEvent
    ) -> Awaitable[None] | None:
        """Protocol stub; implementations consume the event."""


class SettingsChangePublisher:
    """In-process pub/sub keyed by ``refresh_target``."""

    def __init__(self) -> None:
        self._by_target: dict[str, dict[str, SettingsChangeSubscriber]] = {}
        self._by_name: dict[str, SettingsChangeSubscriber] = {}

    def register(self, subscriber: SettingsChangeSubscriber) -> None:
        name = getattr(subscriber, "name", "")
        if not isinstance(name, str) or not name:
            raise ValueError("SettingsChangeSubscriber requires a non-empty name")
        targets = getattr(subscriber, "refresh_targets", None)
        if not isinstance(targets, frozenset) or not targets:
            raise ValueError(
                "SettingsChangeSubscriber requires a non-empty frozenset refresh_targets"
            )
        if name in self._by_name:
            raise ValueError(
                f"SettingsChangeSubscriber with name '{name}' is already registered"
            )
        self._by_name[name] = subscriber
        for target in targets:
            self._by_target.setdefault(target, {})[name] = subscriber

    def unregister(self, subscriber_name: str) -> None:
        if subscriber_name not in self._by_name:
            return
        self._by_name.pop(subscriber_name, None)
        empty_targets: list[str] = []
        for target, name_map in self._by_target.items():
            name_map.pop(subscriber_name, None)
            if not name_map:
                empty_targets.append(target)
        for target in empty_targets:
            self._by_target.pop(target, None)

    def subscribers_for(self, refresh_target: str) -> list[SettingsChangeSubscriber]:
        return list(self._by_target.get(refresh_target, {}).values())

    async def publish(self, events: Iterable[SettingsChangeEvent]) -> None:
        for event in events:
            matched: dict[str, SettingsChangeSubscriber] = {}
            for target in event.refresh_targets:
                for name, subscriber in self._by_target.get(target, {}).items():
                    matched.setdefault(name, subscriber)
            for name, subscriber in matched.items():
                await self._invoke(subscriber, event)

    async def _invoke(
        self, subscriber: SettingsChangeSubscriber, event: SettingsChangeEvent
    ) -> None:
        try:
            result = subscriber(event)
            if hasattr(result, "__await__"):
                await result  # type: ignore[func-returns-value]
        except Exception as exc:  # noqa: BLE001 — structured isolation
            structured = {
                "subscriber_name": getattr(subscriber, "name", "<unknown>"),
                "exception_class": exc.__class__.__name__,
                "exception_message": str(exc)[:512],
                "key": event.key,
                "scope": event.scope,
                "source": event.source,
                "apply_mode": event.apply_mode,
                "refresh_targets": sorted(event.refresh_targets),
            }
            logger.error(
                "settings change subscriber '%s' failed: %s",
                structured["subscriber_name"],
                structured["exception_class"],
                extra={"settings_change_publish": structured},
            )


_default_publisher: SettingsChangePublisher | None = None


def get_settings_change_publisher() -> SettingsChangePublisher:
    """Return the process-wide default publisher singleton."""

    global _default_publisher
    if _default_publisher is None:
        _default_publisher = SettingsChangePublisher()
    return _default_publisher


def reset_default_settings_change_publisher() -> None:
    """Replace the default publisher singleton (test helper)."""

    global _default_publisher
    _default_publisher = None


__all__ = [
    "SettingsChangePublisher",
    "SettingsChangeSubscriber",
    "get_settings_change_publisher",
    "reset_default_settings_change_publisher",
]
