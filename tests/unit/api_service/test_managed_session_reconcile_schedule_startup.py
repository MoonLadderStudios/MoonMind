from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import pytest

from api_service import main as api_main


@pytest.mark.asyncio
async def test_omnigent_bootstrap_retries_with_capped_backoff_and_maintains_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[int] = []
    reconciliations: list[bool] = []
    inventory_refreshes: list[bool] = []

    async def fake_sleep(delay_seconds: int) -> None:
        delays.append(delay_seconds)
        if len(delays) == 4:
            raise asyncio.CancelledError

    async def reconcile_once(*, refresh_images: bool):
        reconciliations.append(refresh_images)
        ready = len(reconciliations) > 1
        return api_main.OmnigentBootstrapReadiness(
            images_ready=ready,
            policies_ready=ready,
            agent_ready=ready,
            catalog_ready=ready,
            schedules_ready=ready,
            provider_ready=ready,
        )

    async def refresh_inventory() -> bool:
        inventory_refreshes.append(True)
        return True

    monkeypatch.setattr(api_main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(api_main, "_reconcile_omnigent_bootstrap_once", reconcile_once)
    monkeypatch.setattr(
        api_main,
        "_sync_omnigent_bootstrap_agent_profile",
        refresh_inventory,
    )

    with pytest.raises(asyncio.CancelledError):
        await api_main._maintain_omnigent_bootstrap_reconciliation(
            initial_ready=False
        )

    assert delays == [5, 10, 120, 120]
    assert reconciliations == [True, True, False]
    assert inventory_refreshes == []


@pytest.mark.asyncio
async def test_api_startup_bootstrap_uses_only_local_image_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api_service.services import omnigent_policies

    observed_resolver = None
    observed_env = None

    @asynccontextmanager
    async def session_context():
        yield object()

    async def seed(_session, *, image_resolver, env=None):
        nonlocal observed_resolver, observed_env
        observed_resolver = image_resolver
        observed_env = env

    async def ready(_session) -> bool:
        return True

    monkeypatch.setattr(api_main, "get_async_session_context", session_context)
    monkeypatch.setattr(omnigent_policies, "seed_bootstrap_policies", seed)
    monkeypatch.setattr(omnigent_policies, "bootstrap_policies_ready", ready)

    assert await api_main._sync_omnigent_bootstrap_policies(
        refresh_images=False
    )
    assert observed_resolver is omnigent_policies.resolve_local_bootstrap_image_ref
    # The registry-acquiring leg keys on the configured refs, so it must read the
    # operator's own configuration rather than a digest publication exported.
    assert observed_env is not None


@pytest.mark.asyncio
async def test_bootstrap_reconciliation_refreshes_recurring_schedule_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def policies(*, refresh_images: bool) -> bool:
        calls.append(f"policies:{refresh_images}")
        return True

    async def agent() -> bool:
        calls.append("agent")
        return True

    async def catalog() -> bool:
        calls.append("catalog")
        return True

    async def schedules() -> bool:
        calls.append("schedules")
        return True

    async def images() -> bool:
        calls.append("images")
        return True

    async def provider(*, allow_enrollment: bool) -> bool:
        calls.append(f"provider:{allow_enrollment}")
        return True

    monkeypatch.setattr(api_main, "_sync_omnigent_deployment_images", images)
    monkeypatch.setattr(api_main, "_sync_omnigent_bootstrap_policies", policies)
    monkeypatch.setattr(api_main, "_sync_omnigent_bootstrap_agent_profile", agent)
    monkeypatch.setattr(api_main, "_sync_omnigent_harness_catalog", catalog)
    monkeypatch.setattr(
        api_main,
        "_sync_managed_bootstrap_recurring_schedules",
        schedules,
    )
    monkeypatch.setattr(
        api_main,
        "_sync_omnigent_provider_readiness",
        provider,
    )

    assert (
        await api_main._reconcile_omnigent_bootstrap_once(refresh_images=True)
    ).ready
    # Image identities are exported before any leg that selects a Host Class or
    # validates a credential against one.
    assert calls == [
        "images",
        "policies:True",
        "agent",
        "catalog",
        "schedules",
        "provider:True",
    ]


@pytest.mark.asyncio
async def test_harness_catalog_sync_runs_on_bootstrap_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The readiness-blocking harness catalog must sync automatically."""

    syncs: list[bool] = []

    async def policies(*, refresh_images: bool) -> bool:
        del refresh_images
        return True

    async def agent() -> bool:
        return True

    async def catalog() -> bool:
        syncs.append(True)
        return True

    async def schedules() -> bool:
        return True

    async def images() -> bool:
        return True

    async def provider(*, allow_enrollment: bool) -> bool:
        del allow_enrollment
        return True

    monkeypatch.setattr(api_main, "_sync_omnigent_deployment_images", images)
    monkeypatch.setattr(api_main, "_sync_omnigent_bootstrap_policies", policies)
    monkeypatch.setattr(api_main, "_sync_omnigent_bootstrap_agent_profile", agent)
    monkeypatch.setattr(api_main, "_sync_omnigent_harness_catalog", catalog)
    monkeypatch.setattr(
        api_main,
        "_sync_managed_bootstrap_recurring_schedules",
        schedules,
    )
    monkeypatch.setattr(
        api_main,
        "_sync_omnigent_provider_readiness",
        provider,
    )

    assert (
        await api_main._reconcile_omnigent_bootstrap_once(refresh_images=False)
    ).ready
    assert syncs == [True]


@pytest.mark.asyncio
async def test_harness_catalog_sync_skips_without_runtime_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built_services = False

    async def fail_build(*args, **kwargs):
        nonlocal built_services
        built_services = True
        raise AssertionError("services must not be built without the runtime gate")

    monkeypatch.setenv("OMNIGENT_ENABLED", "false")
    monkeypatch.setattr(
        "moonmind.omnigent.production.build_generic_omnigent_execution_services",
        fail_build,
    )
    monkeypatch.delenv("MOONMIND_OMNIGENT_GENERIC_HOST_ENABLED", raising=False)

    assert await api_main._sync_omnigent_harness_catalog()
    assert built_services is False


@pytest.mark.asyncio
async def test_harness_catalog_sync_failure_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def session_context():
        yield object()

    class _Services:
        class catalog_service:  # noqa: N801 - mirrors attribute access
            @staticmethod
            async def synchronize():
                raise RuntimeError("endpoint unreachable")

    monkeypatch.setattr(api_main, "get_async_session_context", session_context)
    monkeypatch.setattr(
        "moonmind.omnigent.production.build_generic_omnigent_execution_services",
        lambda **kwargs: _Services(),
    )
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "http://omnigent:8000")

    with caplog.at_level(logging.WARNING):
        assert not await api_main._sync_omnigent_harness_catalog()

    assert "Omnigent harness catalog synchronization deferred" in caplog.text


@pytest.mark.asyncio
async def test_mm870_api_startup_ensures_managed_session_reconcile_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    class _Adapter:
        async def ensure_managed_session_reconcile_schedule(
            self,
            *,
            enabled: bool,
        ) -> str:
            calls.append(enabled)
            return "mm-operational:managed-session-reconcile"

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        _Adapter,
    )

    await api_main.ensure_managed_session_reconcile_schedule_started()

    assert calls == [True]


@pytest.mark.asyncio
async def test_mm870_api_startup_schedule_failure_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Adapter:
        async def ensure_managed_session_reconcile_schedule(
            self,
            *,
            enabled: bool,
        ) -> str:
            del enabled
            raise RuntimeError("temporal unavailable")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        _Adapter,
    )

    with caplog.at_level(logging.WARNING):
        await api_main.ensure_managed_session_reconcile_schedule_started()

    assert "Failed to ensure managed session reconcile schedule" in caplog.text


@pytest.mark.asyncio
async def test_api_startup_enables_workspace_cleanup_schedule_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    class _Adapter:
        async def ensure_managed_runtime_workspace_cleanup_schedule(
            self,
            *,
            enabled: bool,
        ) -> str:
            calls.append(enabled)
            return "mm-operational:managed-runtime-workspace-cleanup"

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        _Adapter,
    )

    await api_main.ensure_managed_runtime_workspace_cleanup_schedule_started()

    assert calls == [True]


@pytest.mark.asyncio
async def test_api_startup_persistently_disables_workspace_cleanup_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    class _Adapter:
        async def ensure_managed_runtime_workspace_cleanup_schedule(
            self,
            *,
            enabled: bool,
        ) -> str:
            calls.append(enabled)
            return "mm-operational:managed-runtime-workspace-cleanup"

    monkeypatch.setenv("MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED", "false")
    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        _Adapter,
    )

    await api_main.ensure_managed_runtime_workspace_cleanup_schedule_started()

    assert calls == [False]


@pytest.mark.asyncio
async def test_api_startup_dry_run_keeps_workspace_cleanup_schedule_enabled(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[bool] = []

    class _Adapter:
        async def ensure_managed_runtime_workspace_cleanup_schedule(
            self,
            *,
            enabled: bool,
        ) -> str:
            calls.append(enabled)
            return "mm-operational:managed-runtime-workspace-cleanup"

    monkeypatch.setenv("MOONMIND_MANAGED_RUNTIME_JANITOR_DRY_RUN", "true")
    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        _Adapter,
    )

    with caplog.at_level(logging.INFO):
        await api_main.ensure_managed_runtime_workspace_cleanup_schedule_started()

    assert calls == [True]
    assert "schedule_enabled=True dry_run=True" in caplog.text


@pytest.mark.asyncio
async def test_mm948_api_startup_cleanup_schedule_failure_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Adapter:
        async def ensure_managed_runtime_workspace_cleanup_schedule(
            self,
            *,
            enabled: bool,
        ) -> str:
            del enabled
            raise RuntimeError("temporal unavailable")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        _Adapter,
    )

    with caplog.at_level(logging.WARNING):
        await api_main.ensure_managed_runtime_workspace_cleanup_schedule_started()

    assert "Failed to ensure managed runtime workspace cleanup schedule" in caplog.text


@pytest.mark.asyncio
async def test_catalog_outage_does_not_retry_registry_image_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only image-policy readiness may re-acquire images from the registry."""

    delays: list[int] = []
    image_refreshes: list[bool] = []

    async def fake_sleep(delay_seconds: int) -> None:
        delays.append(delay_seconds)
        if len(delays) == 4:
            raise asyncio.CancelledError

    async def policies(*, refresh_images: bool) -> bool:
        image_refreshes.append(refresh_images)
        return True

    async def agent() -> bool:
        return True

    async def catalog() -> bool:
        # The catalog endpoint alone is unavailable for every retry.
        return False

    async def schedules() -> bool:
        return True

    monkeypatch.setattr(api_main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(api_main, "_sync_omnigent_bootstrap_policies", policies)
    monkeypatch.setattr(api_main, "_sync_omnigent_bootstrap_agent_profile", agent)
    monkeypatch.setattr(api_main, "_sync_omnigent_harness_catalog", catalog)
    monkeypatch.setattr(
        api_main,
        "_sync_managed_bootstrap_recurring_schedules",
        schedules,
    )

    with pytest.raises(asyncio.CancelledError):
        await api_main._maintain_omnigent_bootstrap_reconciliation(
            initial_ready=False
        )

    # Aggregate readiness stays false, so the loop keeps retrying with capped
    # backoff, but images are refreshed only on the first pass.
    assert delays == [5, 10, 20, 40]
    assert image_refreshes == [True, False, False]


@pytest.mark.asyncio
async def test_image_policy_outage_keeps_retrying_registry_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unready image policy must keep re-acquiring images."""

    delays: list[int] = []
    image_refreshes: list[bool] = []

    async def fake_sleep(delay_seconds: int) -> None:
        delays.append(delay_seconds)
        if len(delays) == 3:
            raise asyncio.CancelledError

    async def policies(*, refresh_images: bool) -> bool:
        image_refreshes.append(refresh_images)
        return False

    async def agent() -> bool:
        return True

    async def catalog() -> bool:
        return True

    async def schedules() -> bool:
        return True

    monkeypatch.setattr(api_main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(api_main, "_sync_omnigent_bootstrap_policies", policies)
    monkeypatch.setattr(api_main, "_sync_omnigent_bootstrap_agent_profile", agent)
    monkeypatch.setattr(api_main, "_sync_omnigent_harness_catalog", catalog)
    monkeypatch.setattr(
        api_main,
        "_sync_managed_bootstrap_recurring_schedules",
        schedules,
    )

    with pytest.raises(asyncio.CancelledError):
        await api_main._maintain_omnigent_bootstrap_reconciliation(
            initial_ready=False
        )

    assert image_refreshes == [True, True]


@pytest.mark.asyncio
async def test_enrollment_waits_for_image_and_catalog_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First-time enrollment gets one attempt, so it must not race the catalog."""

    observed: list[bool] = []

    async def policies(*, refresh_images: bool) -> bool:
        del refresh_images
        return True

    async def agent() -> bool:
        return True

    async def schedules() -> bool:
        return True

    async def images() -> bool:
        return True

    async def catalog() -> bool:
        return False

    async def provider(*, allow_enrollment: bool) -> bool:
        observed.append(allow_enrollment)
        return True

    monkeypatch.setattr(api_main, "_sync_omnigent_deployment_images", images)
    monkeypatch.setattr(api_main, "_sync_omnigent_bootstrap_policies", policies)
    monkeypatch.setattr(api_main, "_sync_omnigent_bootstrap_agent_profile", agent)
    monkeypatch.setattr(api_main, "_sync_omnigent_harness_catalog", catalog)
    monkeypatch.setattr(
        api_main, "_sync_managed_bootstrap_recurring_schedules", schedules
    )
    monkeypatch.setattr(api_main, "_sync_omnigent_provider_readiness", provider)

    outcome = await api_main._reconcile_omnigent_bootstrap_once(refresh_images=False)

    assert observed == [False]
    assert outcome.ready is False


@pytest.mark.asyncio
async def test_provider_readiness_is_skipped_without_resolved_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing validates a credential against an image the deployment lacks."""

    called = False

    async def policies(*, refresh_images: bool) -> bool:
        del refresh_images
        return True

    async def agent() -> bool:
        return True

    async def catalog() -> bool:
        return True

    async def schedules() -> bool:
        return True

    async def images() -> bool:
        return False

    async def provider(*, allow_enrollment: bool) -> bool:
        nonlocal called
        del allow_enrollment
        called = True
        return True

    monkeypatch.setattr(api_main, "_sync_omnigent_deployment_images", images)
    monkeypatch.setattr(api_main, "_sync_omnigent_bootstrap_policies", policies)
    monkeypatch.setattr(api_main, "_sync_omnigent_bootstrap_agent_profile", agent)
    monkeypatch.setattr(api_main, "_sync_omnigent_harness_catalog", catalog)
    monkeypatch.setattr(
        api_main, "_sync_managed_bootstrap_recurring_schedules", schedules
    )
    monkeypatch.setattr(api_main, "_sync_omnigent_provider_readiness", provider)

    outcome = await api_main._reconcile_omnigent_bootstrap_once(refresh_images=False)

    assert called is False
    assert outcome.provider_ready is False
    assert outcome.ready is False
