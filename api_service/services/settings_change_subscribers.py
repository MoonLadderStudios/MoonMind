"""Default per-family settings change subscribers (MM-710).

Each subscriber refreshes one consumer family (task creation defaults, worker
reloaders, the provider profile manager, or operational controls) in response
to the ``setting_changed`` events emitted by ``SettingsCatalogService``.

The subscribers maintain bounded in-process ledgers so that operators and tests
can observe that a refresh occurred. Real cross-process side effects
(worker drains, Temporal signals to the ``ProviderProfileManager`` workflow,
etc.) are delegated to optional ``on_*`` hooks that downstream wiring can
attach without modifying the subscriber implementations.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from api_service.services.settings_catalog import (
    SettingApplyMode,
    SettingScope,
    SettingsChangeEvent,
)
from api_service.services.settings_change_publisher import (
    SettingsChangePublisher,
    get_settings_change_publisher,
)

_LEDGER_MAXLEN = 128


@dataclass
class WorkerReloadIntent:
    intent: Literal["soft_reload", "process_restart"]
    key: str
    scope: SettingScope
    apply_mode: SettingApplyMode
    refresh_target: str
    affected_systems: tuple[str, ...]
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ProviderProfileRefreshIntent:
    key: str
    scope: SettingScope
    apply_mode: SettingApplyMode
    refresh_target: str
    affected_systems: tuple[str, ...]
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class OperationalRefreshIntent:
    key: str
    scope: SettingScope
    apply_mode: SettingApplyMode
    refresh_target: str
    affected_systems: tuple[str, ...]
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class TaskCreationDefaultsSubscriber:
    """Tracks task-creation default refreshes triggered by setting changes."""

    name = "task_creation_defaults"
    refresh_targets = frozenset({"task_creation_defaults"})

    def __init__(self) -> None:
        self.version: int = 0
        self.recent_events: deque[SettingsChangeEvent] = deque(maxlen=_LEDGER_MAXLEN)

    async def __call__(self, event: SettingsChangeEvent) -> None:
        self.version += 1
        self.recent_events.append(event)


class WorkerReloadSubscriber:
    """Records reload intents the worker fleet should observe."""

    name = "worker_reloaders"
    refresh_targets = frozenset({"worker_reloaders"})

    def __init__(
        self,
        *,
        on_reload: Callable[[WorkerReloadIntent], Awaitable[None]] | None = None,
    ) -> None:
        self.pending_intents: deque[WorkerReloadIntent] = deque(maxlen=_LEDGER_MAXLEN)
        self.on_reload = on_reload

    async def __call__(self, event: SettingsChangeEvent) -> None:
        kind: Literal["soft_reload", "process_restart"] = (
            "process_restart" if event.apply_mode == "process_restart" else "soft_reload"
        )
        intent = WorkerReloadIntent(
            intent=kind,
            key=event.key,
            scope=event.scope,
            apply_mode=event.apply_mode,
            refresh_target="worker_reloaders",
            affected_systems=tuple(event.affected_systems),
        )
        self.pending_intents.append(intent)
        if self.on_reload is not None:
            await self.on_reload(intent)


class ProviderProfileManagerSubscriber:
    """Records provider-profile-manager refresh intents."""

    name = "provider_profile_manager"
    refresh_targets = frozenset({"provider_profile_manager"})

    def __init__(
        self,
        *,
        on_refresh: Callable[[ProviderProfileRefreshIntent], Awaitable[None]]
        | None = None,
    ) -> None:
        self.version: int = 0
        self.pending_intents: deque[ProviderProfileRefreshIntent] = deque(
            maxlen=_LEDGER_MAXLEN
        )
        self.on_refresh = on_refresh

    async def __call__(self, event: SettingsChangeEvent) -> None:
        intent = ProviderProfileRefreshIntent(
            key=event.key,
            scope=event.scope,
            apply_mode=event.apply_mode,
            refresh_target="provider_profile_manager",
            affected_systems=tuple(event.affected_systems),
        )
        self.pending_intents.append(intent)
        self.version += 1
        if self.on_refresh is not None:
            await self.on_refresh(intent)


class OperationalControlsSubscriber:
    """Records operational-controls refresh intents."""

    name = "operational_controls"
    refresh_targets = frozenset({"operational_controls"})

    def __init__(
        self,
        *,
        on_refresh: Callable[[OperationalRefreshIntent], Awaitable[None]] | None = None,
    ) -> None:
        self.version: int = 0
        self.pending_intents: deque[OperationalRefreshIntent] = deque(
            maxlen=_LEDGER_MAXLEN
        )
        self.on_refresh = on_refresh

    async def __call__(self, event: SettingsChangeEvent) -> None:
        intent = OperationalRefreshIntent(
            key=event.key,
            scope=event.scope,
            apply_mode=event.apply_mode,
            refresh_target="operational_controls",
            affected_systems=tuple(event.affected_systems),
        )
        self.pending_intents.append(intent)
        self.version += 1
        if self.on_refresh is not None:
            await self.on_refresh(intent)


def register_default_subscribers(
    publisher: SettingsChangePublisher | None = None,
    *,
    worker_on_reload: Callable[[WorkerReloadIntent], Awaitable[None]] | None = None,
    provider_profile_on_refresh: Callable[
        [ProviderProfileRefreshIntent], Awaitable[None]
    ]
    | None = None,
    operational_on_refresh: Callable[
        [OperationalRefreshIntent], Awaitable[None]
    ]
    | None = None,
) -> None:
    """Register the four default subscribers against *publisher* once.

    Optional hooks let production wiring drive observable cross-process side
    effects (e.g. Temporal signals, worker drain broadcasts, operational
    alerting) when ``worker_reload`` / ``process_restart`` / ``next_launch`` /
    ``manual_operation`` apply modes are triggered. The hooks default to
    ``None`` so tests can run with the bare intent-ledger contract.
    """

    target_publisher = publisher or get_settings_change_publisher()
    factories: tuple[Callable[[], object], ...] = (
        TaskCreationDefaultsSubscriber,
        lambda: WorkerReloadSubscriber(on_reload=worker_on_reload),
        lambda: ProviderProfileManagerSubscriber(
            on_refresh=provider_profile_on_refresh
        ),
        lambda: OperationalControlsSubscriber(on_refresh=operational_on_refresh),
    )
    for factory in factories:
        instance = factory()
        name = getattr(instance, "name", None)
        refresh_targets = getattr(instance, "refresh_targets", frozenset())
        if not isinstance(name, str) or not name or not refresh_targets:
            continue
        already_registered = any(
            sub.name == name
            for target in refresh_targets
            for sub in target_publisher.subscribers_for(target)
        )
        if already_registered:
            continue
        target_publisher.register(instance)


__all__ = [
    "OperationalControlsSubscriber",
    "OperationalRefreshIntent",
    "ProviderProfileManagerSubscriber",
    "ProviderProfileRefreshIntent",
    "TaskCreationDefaultsSubscriber",
    "WorkerReloadIntent",
    "WorkerReloadSubscriber",
    "register_default_subscribers",
]
