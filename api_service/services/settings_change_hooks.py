"""Production hook factories for SettingsChangePublisher default subscribers (MM-710).

The subscribers in ``settings_change_subscribers`` expose optional ``on_reload``
and ``on_refresh`` callables so that consumers can plug in concrete cross-process
side effects. This module provides the production-grade implementations:

- ``make_provider_profile_refresh_hook`` -- pushes a ``sync_profiles`` Temporal
  signal to every registered ``ProviderProfileManager`` workflow so the running
  manager re-pulls the authoritative profile snapshot from the database when a
  profile-affecting setting changes.
- ``make_worker_reload_broadcast_hook`` -- forwards worker reload intents to a
  caller-supplied broadcaster so apply modes ``worker_reload`` and
  ``process_restart`` drive observable behavior beyond the in-process intent
  ledger.
- ``make_operational_mode_hook`` -- forwards operational refresh intents to a
  caller-supplied handler so ``manual_operation`` apply mode changes can drive
  observable operations behavior (alerts, banners, runbooks).

All hooks are defensive: failures are logged structurally but never propagate,
mirroring the isolation guarantee that ``SettingsChangePublisher._invoke``
already provides.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from api_service.services.settings_change_subscribers import (
    OperationalRefreshIntent,
    ProviderProfileRefreshIntent,
    WorkerReloadIntent,
)

logger = logging.getLogger(__name__)


_PROVIDER_PROFILE_WORKFLOW_ID_PREFIX = "provider-profile-manager"


async def _list_runtime_ids_from_db() -> list[str]:
    """Default runtime-id provider that queries the managed-agent profile table.

    Wrapped in a try/except so callers can use this in startup contexts where
    the DB may not yet be reachable.
    """

    try:
        from sqlalchemy import select

        from api_service.db.base import get_async_session_context
        from api_service.db.models import ManagedAgentProviderProfile

        async with get_async_session_context() as session:
            stmt = select(ManagedAgentProviderProfile.runtime_id).distinct()
            result = await session.execute(stmt)
            return [str(rid) for rid in result.scalars().all() if rid]
    except Exception as exc:  # noqa: BLE001 -- intentional defense-in-depth
        logger.warning(
            "Failed to list provider-profile runtime ids for refresh hook: %s",
            exc,
        )
        return []


async def _default_temporal_client_provider() -> Any:
    """Default Temporal client provider used in production wiring."""

    from moonmind.workflows.temporal.client import TemporalClientAdapter

    adapter = TemporalClientAdapter()
    return await adapter.get_client()


def make_provider_profile_refresh_hook(
    *,
    temporal_client_provider: Callable[[], Awaitable[Any]] | None = None,
    runtime_ids_provider: Callable[[], Awaitable[list[str]]] | None = None,
) -> Callable[[ProviderProfileRefreshIntent], Awaitable[None]]:
    """Build an ``on_refresh`` hook that signals every running
    ``ProviderProfileManager`` workflow with ``sync_profiles`` so the manager
    re-pulls profile state from the authoritative DB.
    """

    resolve_client = temporal_client_provider or _default_temporal_client_provider
    resolve_runtime_ids = runtime_ids_provider or _list_runtime_ids_from_db

    async def _hook(intent: ProviderProfileRefreshIntent) -> None:
        try:
            runtime_ids = await resolve_runtime_ids()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ProviderProfileManager refresh hook could not resolve runtime ids: %s",
                exc,
            )
            return

        if not runtime_ids:
            logger.debug(
                "ProviderProfileManager refresh hook found no registered runtimes; "
                "skipping signal for setting %s",
                intent.key,
            )
            return

        try:
            client = await resolve_client()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ProviderProfileManager refresh hook could not reach Temporal: %s",
                exc,
            )
            return

        for runtime_id in runtime_ids:
            workflow_id = f"{_PROVIDER_PROFILE_WORKFLOW_ID_PREFIX}:{runtime_id}"
            try:
                handle = client.get_workflow_handle(workflow_id)
                await handle.signal("sync_profiles", {"profiles": []})
                logger.info(
                    "Signaled ProviderProfileManager sync_profiles "
                    "(runtime_id=%s key=%s apply_mode=%s)",
                    runtime_id,
                    intent.key,
                    intent.apply_mode,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to signal ProviderProfileManager runtime_id=%s "
                    "for setting %s: %s",
                    runtime_id,
                    intent.key,
                    exc,
                )

    return _hook


def make_worker_reload_broadcast_hook(
    *,
    broadcaster: Callable[[dict[str, Any]], Awaitable[None]],
) -> Callable[[WorkerReloadIntent], Awaitable[None]]:
    """Build an ``on_reload`` hook that forwards a normalized payload to the
    given broadcaster.

    Production wiring may publish to a Redis pub/sub topic, a Temporal control
    workflow, or another out-of-process channel. The hook itself only owns
    intent-to-payload normalization plus failure isolation, so the publisher
    contract stays observable even when the broadcaster is degraded.
    """

    async def _hook(intent: WorkerReloadIntent) -> None:
        payload = {
            "intent": intent.intent,
            "key": intent.key,
            "scope": intent.scope,
            "apply_mode": intent.apply_mode,
            "refresh_target": intent.refresh_target,
            "affected_systems": tuple(intent.affected_systems),
            "recorded_at": intent.recorded_at.isoformat(),
        }
        try:
            await broadcaster(payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Worker reload broadcaster failed for setting %s (intent=%s): %s",
                intent.key,
                intent.intent,
                exc,
            )

    return _hook


def make_operational_mode_hook(
    *,
    handler: Callable[[dict[str, Any]], Awaitable[None]],
) -> Callable[[OperationalRefreshIntent], Awaitable[None]]:
    """Build a callable that forwards a normalized operational refresh payload
    to the given handler. Production wiring may post alerts, update banners,
    or trigger a runbook activity.
    """

    async def _hook(intent: OperationalRefreshIntent) -> None:
        payload = {
            "key": intent.key,
            "scope": intent.scope,
            "apply_mode": intent.apply_mode,
            "refresh_target": intent.refresh_target,
            "affected_systems": tuple(intent.affected_systems),
            "recorded_at": intent.recorded_at.isoformat(),
        }
        try:
            await handler(payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Operational mode hook handler failed for setting %s "
                "(apply_mode=%s): %s",
                intent.key,
                intent.apply_mode,
                exc,
            )

    return _hook


__all__ = [
    "make_operational_mode_hook",
    "make_provider_profile_refresh_hook",
    "make_worker_reload_broadcast_hook",
]
