"""Unit tests for production hook factories used by the SettingsChangePublisher
default subscribers (MM-710).

These hooks turn the subscriber-recorded intents (e.g. ProviderProfileRefreshIntent)
into concrete cross-process side effects so that ``worker_reload``,
``next_launch``, and ``manual_operation`` apply modes drive observable behavior
beyond UI query invalidation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from api_service.services.settings_change_hooks import (
    make_operational_mode_hook,
    make_provider_profile_refresh_hook,
    make_worker_reload_broadcast_hook,
)
from api_service.services.settings_change_publisher import SettingsChangePublisher
from api_service.services.settings_change_subscribers import (
    OperationalControlsSubscriber,
    OperationalRefreshIntent,
    ProviderProfileManagerSubscriber,
    ProviderProfileRefreshIntent,
    WorkerReloadIntent,
    WorkerReloadSubscriber,
    register_default_subscribers,
)


def _provider_intent(
    *,
    key: str = "workflow.default_provider_profile_ref",
    apply_mode: str = "next_launch",
    affected_systems: tuple[str, ...] = (
        "task_creation",
        "workflow_runtime",
        "provider_profiles",
    ),
) -> ProviderProfileRefreshIntent:
    return ProviderProfileRefreshIntent(
        key=key,
        scope="workspace",
        apply_mode=apply_mode,
        refresh_target="provider_profile_manager",
        affected_systems=affected_systems,
    )


def _worker_intent(
    *,
    apply_mode: str = "worker_reload",
    intent: str = "soft_reload",
) -> WorkerReloadIntent:
    return WorkerReloadIntent(
        intent=intent,
        key="skills.policy_mode",
        scope="workspace",
        apply_mode=apply_mode,
        refresh_target="worker_reloaders",
        affected_systems=("workflow_runtime", "skills"),
    )


def _operational_intent(
    *,
    apply_mode: str = "manual_operation",
) -> OperationalRefreshIntent:
    return OperationalRefreshIntent(
        key="workflow.operation_mode",
        scope="workspace",
        apply_mode=apply_mode,
        refresh_target="operational_controls",
        affected_systems=("operations",),
    )


# ---------------------------------------------------------------------------
# make_provider_profile_refresh_hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_profile_refresh_hook_signals_every_known_runtime():
    """The on_refresh hook signals sync_profiles on every registered runtime
    family workflow so any one of them can re-pull from the DB."""

    handle_a = AsyncMock()
    handle_b = AsyncMock()
    client = MagicMock()
    handle_lookup = {
        "provider-profile-manager:claude_code": handle_a,
        "provider-profile-manager:codex_cli": handle_b,
    }
    client.get_workflow_handle = MagicMock(side_effect=handle_lookup.__getitem__)
    temporal_client_provider = AsyncMock(return_value=client)
    runtime_ids_provider = AsyncMock(return_value=["claude_code", "codex_cli"])

    hook = make_provider_profile_refresh_hook(
        temporal_client_provider=temporal_client_provider,
        runtime_ids_provider=runtime_ids_provider,
    )

    await hook(_provider_intent())

    runtime_ids_provider.assert_awaited_once()
    temporal_client_provider.assert_awaited_once()
    handle_a.signal.assert_awaited_once_with("sync_profiles", {"profiles": []})
    handle_b.signal.assert_awaited_once_with("sync_profiles", {"profiles": []})


@pytest.mark.asyncio
async def test_provider_profile_refresh_hook_isolates_failed_runtime_signal(caplog):
    """A signal failure for one runtime must not stop signaling the others."""

    failing = AsyncMock()
    failing.signal.side_effect = RuntimeError("temporal unreachable")
    ok = AsyncMock()
    client = MagicMock()
    client.get_workflow_handle = MagicMock(
        side_effect={
            "provider-profile-manager:claude_code": failing,
            "provider-profile-manager:codex_cli": ok,
        }.__getitem__
    )
    hook = make_provider_profile_refresh_hook(
        temporal_client_provider=AsyncMock(return_value=client),
        runtime_ids_provider=AsyncMock(return_value=["claude_code", "codex_cli"]),
    )

    with caplog.at_level(logging.WARNING, logger="api_service.services.settings_change_hooks"):
        await hook(_provider_intent())

    ok.signal.assert_awaited_once()
    failing.signal.assert_awaited_once()
    assert any(
        "claude_code" in record.getMessage() for record in caplog.records
    ), "expected the failing-runtime warning to be logged"


@pytest.mark.asyncio
async def test_provider_profile_refresh_hook_skips_when_no_runtimes_known():
    """If no runtime families are registered yet, the hook is a no-op."""

    temporal_client_provider = AsyncMock()
    hook = make_provider_profile_refresh_hook(
        temporal_client_provider=temporal_client_provider,
        runtime_ids_provider=AsyncMock(return_value=[]),
    )

    await hook(_provider_intent())

    temporal_client_provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_profile_refresh_hook_handles_temporal_provider_failure(caplog):
    """If the Temporal client itself cannot be reached, the hook must swallow
    the exception (publisher would also isolate, but defense-in-depth)."""

    hook = make_provider_profile_refresh_hook(
        temporal_client_provider=AsyncMock(side_effect=RuntimeError("no client")),
        runtime_ids_provider=AsyncMock(return_value=["claude_code"]),
    )

    with caplog.at_level(logging.WARNING, logger="api_service.services.settings_change_hooks"):
        await hook(_provider_intent())  # must not raise

    assert any(
        "no client" in record.getMessage() or "ProviderProfileManager" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# make_worker_reload_broadcast_hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_reload_broadcast_hook_invokes_broadcaster_with_intent_payload():
    seen: list[dict[str, Any]] = []

    async def fake_broadcaster(payload: dict[str, Any]) -> None:
        seen.append(payload)

    hook = make_worker_reload_broadcast_hook(broadcaster=fake_broadcaster)

    await hook(_worker_intent())

    assert len(seen) == 1
    payload = seen[0]
    assert payload["intent"] == "soft_reload"
    assert payload["key"] == "skills.policy_mode"
    assert payload["apply_mode"] == "worker_reload"
    assert payload["scope"] == "workspace"
    assert "workflow_runtime" in payload["affected_systems"]


@pytest.mark.asyncio
async def test_worker_reload_broadcast_hook_distinguishes_process_restart_payload():
    seen: list[dict[str, Any]] = []

    async def fake_broadcaster(payload: dict[str, Any]) -> None:
        seen.append(payload)

    hook = make_worker_reload_broadcast_hook(broadcaster=fake_broadcaster)

    await hook(_worker_intent(apply_mode="process_restart", intent="process_restart"))

    assert seen[0]["intent"] == "process_restart"
    assert seen[0]["apply_mode"] == "process_restart"


@pytest.mark.asyncio
async def test_worker_reload_broadcast_hook_isolates_broadcaster_failure(caplog):
    async def angry_broadcaster(payload: dict[str, Any]) -> None:
        raise RuntimeError("control plane gone")

    hook = make_worker_reload_broadcast_hook(broadcaster=angry_broadcaster)

    with caplog.at_level(logging.WARNING, logger="api_service.services.settings_change_hooks"):
        await hook(_worker_intent())  # must not raise

    assert any("control plane gone" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# make_operational_mode_hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_operational_mode_hook_invokes_handler_with_intent_payload():
    seen: list[dict[str, Any]] = []

    async def fake_handler(payload: dict[str, Any]) -> None:
        seen.append(payload)

    hook = make_operational_mode_hook(handler=fake_handler)

    await hook(_operational_intent())

    assert len(seen) == 1
    payload = seen[0]
    assert payload["key"] == "workflow.operation_mode"
    assert payload["apply_mode"] == "manual_operation"
    assert payload["affected_systems"] == ("operations",)


@pytest.mark.asyncio
async def test_operational_mode_hook_isolates_handler_failure(caplog):
    async def angry_handler(payload: dict[str, Any]) -> None:
        raise RuntimeError("ops endpoint down")

    hook = make_operational_mode_hook(handler=angry_handler)

    with caplog.at_level(logging.WARNING, logger="api_service.services.settings_change_hooks"):
        await hook(_operational_intent())  # must not raise

    assert any("ops endpoint down" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# register_default_subscribers hook injection contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_default_subscribers_wires_provider_profile_on_refresh():
    """register_default_subscribers must support injecting a concrete
    on_refresh hook so production wiring can drive cross-process side effects.
    """

    received: list[ProviderProfileRefreshIntent] = []

    async def captured_hook(intent: ProviderProfileRefreshIntent) -> None:
        received.append(intent)

    publisher = SettingsChangePublisher()
    register_default_subscribers(
        publisher,
        provider_profile_on_refresh=captured_hook,
    )

    profile_subs = publisher.subscribers_for("provider_profile_manager")
    assert profile_subs, "expected provider_profile_manager subscriber registered"
    profile_sub = profile_subs[0]
    assert isinstance(profile_sub, ProviderProfileManagerSubscriber)
    assert profile_sub.on_refresh is captured_hook


@pytest.mark.asyncio
async def test_register_default_subscribers_wires_worker_reload_hook():
    received: list[WorkerReloadIntent] = []

    async def captured_hook(intent: WorkerReloadIntent) -> None:
        received.append(intent)

    publisher = SettingsChangePublisher()
    register_default_subscribers(
        publisher,
        worker_on_reload=captured_hook,
    )

    worker_subs = publisher.subscribers_for("worker_reloaders")
    assert worker_subs, "expected worker_reloaders subscriber registered"
    worker_sub = worker_subs[0]
    assert isinstance(worker_sub, WorkerReloadSubscriber)
    assert worker_sub.on_reload is captured_hook


@pytest.mark.asyncio
async def test_register_default_subscribers_wires_operational_hook():
    received: list[OperationalRefreshIntent] = []

    async def captured_hook(intent: OperationalRefreshIntent) -> None:
        received.append(intent)

    publisher = SettingsChangePublisher()
    register_default_subscribers(
        publisher,
        operational_on_refresh=captured_hook,
    )

    operational_subs = publisher.subscribers_for("operational_controls")
    assert operational_subs, "expected operational_controls subscriber registered"
    operational_sub = operational_subs[0]
    assert isinstance(operational_sub, OperationalControlsSubscriber)
    assert operational_sub.on_refresh is captured_hook


@pytest.mark.asyncio
async def test_register_default_subscribers_invokes_provider_hook_on_event(caplog):
    """End-to-end through the publisher: when a provider-profile-affecting event
    is published, the wired on_refresh hook must run."""

    received: list[ProviderProfileRefreshIntent] = []

    async def captured_hook(intent: ProviderProfileRefreshIntent) -> None:
        received.append(intent)

    publisher = SettingsChangePublisher()
    register_default_subscribers(
        publisher,
        provider_profile_on_refresh=captured_hook,
    )

    from api_service.services.settings_catalog import SettingsChangeEvent

    event = SettingsChangeEvent(
        key="workflow.default_provider_profile_ref",
        scope="workspace",
        source="workspace_override",
        apply_mode="next_launch",
        actor_user_id=uuid4(),
        changed_at=datetime.now(timezone.utc),
        affected_systems=["task_creation", "workflow_runtime", "provider_profiles"],
        refresh_targets=[
            "provider_profile_manager",
            "settings_catalog",
            "task_creation_defaults",
        ],
    )

    await publisher.publish([event])

    assert len(received) == 1
    assert received[0].key == "workflow.default_provider_profile_ref"
    assert received[0].apply_mode == "next_launch"
