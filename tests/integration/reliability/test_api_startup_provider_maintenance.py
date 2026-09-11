"""Replay a blocked bootstrap dependency through real Uvicorn startup and HTTP."""

from __future__ import annotations

import asyncio
import re
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import uvicorn
import yaml
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service import main as api_main
from tests.integration.reliability.helpers import load_replay

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.reliability_journey]


@pytest.mark.parametrize(
    ("blocked_stage", "enabled_setting", "release_wait"),
    [
        ("provider", None, True),
        ("provider", "true", False),
        ("qualification", None, False),
        ("images", "true", False),
        ("provider", "false", False),
    ],
)
async def test_http_serves_while_bootstrap_waits_and_lifespan_owns_cleanup(
    monkeypatch, tmp_path, blocked_stage, enabled_setting, release_wait
):
    replay = load_replay("api-startup-provider-maintenance", "manifest.json")
    # Exercise Compose's omitted-value default and its explicit equivalent
    # through the actual runtime gate, rather than forcing the gate open.
    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[3] / "docker-compose.yaml").read_text()
    )
    declaration = next(
        value
        for value in compose["services"]["api"]["environment"]
        if value.startswith("OMNIGENT_ENABLED=")
    )
    match = re.fullmatch(r"OMNIGENT_ENABLED=\$\{OMNIGENT_ENABLED:-(.+)\}", declaration)
    assert match is not None
    monkeypatch.setenv("OMNIGENT_ENABLED", enabled_setting or match[1])
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "http://unused.invalid")
    monkeypatch.setattr(api_main.settings.oidc, "AUTH_PROVIDER", "oidc")
    monkeypatch.delattr(
        api_main.app.state, "omnigent_bootstrap_reconciliation_task", raising=False
    )
    monkeypatch.delattr(api_main.app.state, "omnigent_inventory_task", raising=False)
    from api_service.services import omnigent_agent_profile_service as inventory_service

    inventory_calls = []
    inventory_refreshed = asyncio.Event()

    async def refresh_inventory():
        inventory_calls.append(True)
        if len(inventory_calls) > 1:
            inventory_refreshed.set()

    monkeypatch.setattr(inventory_service, "refresh_upstream_inventory", refresh_inventory)
    monkeypatch.setattr(api_main, "_OMNIGENT_INVENTORY_REFRESH_INTERVAL_SECONDS", 0.05)

    # MoonLadderStudios/MoonMind#4192: native RAG retrieval is retired, so
    # startup no longer touches a retrieval service; no stub is needed.

    # Keep unrelated startup integrations hermetic. The production startup,
    # lifespan, reconciliation ordering, health route and TCP server all run.
    for name in (
        "_initialize_oidc_provider",
        "_sync_preset_seed_catalog",
        "_auto_seed_provider_profiles",
        "_sync_env_managed_secrets",
        "ensure_provider_profile_managers_started",
        "ensure_managed_session_reconcile_schedule_started",
        "ensure_managed_runtime_workspace_cleanup_schedule_started",
        "ensure_omnigent_oauth_host_janitor_schedule_started",
        "ensure_recurring_workflow_schedules_reconciled",
    ):
        monkeypatch.setattr(api_main, name, AsyncMock())
    monkeypatch.setattr(api_main, "_register_settings_change_subscribers", lambda: None)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/health.db")
    sessions = async_sessionmaker(engine)

    @asynccontextmanager
    async def db_session():
        async with sessions() as session:
            yield session

    monkeypatch.setattr(api_main, "get_async_session_context", db_session)
    entered = asyncio.Event()
    available = asyncio.Event()
    exited = asyncio.Event()
    completed = asyncio.Event()
    cancelled = False

    async def ready(*_args, **_kwargs):
        return True

    async def blocked(*_args, **_kwargs):
        nonlocal cancelled
        entered.set()
        try:
            # Deterministic replay of the external maintenance lease wait.
            # Availability belongs to the existing workflow, never startup.
            await available.wait()
            return True
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            exited.set()

    async def qualify():
        completed.set()
        return True

    for name in (
        "_sync_omnigent_deployment_images",
        "_sync_omnigent_bootstrap_policies",
        "_sync_omnigent_bootstrap_agent_profile",
        "_sync_omnigent_harness_catalog",
        "_sync_managed_bootstrap_recurring_schedules",
        "_sync_omnigent_provider_readiness",
    ):
        monkeypatch.setattr(api_main, name, ready)
    monkeypatch.setattr(api_main, "_sync_omnigent_deployment_qualification", qualify)
    monkeypatch.setattr(
        api_main,
        {
            "provider": "_sync_omnigent_provider_readiness",
            "qualification": "_sync_omnigent_deployment_qualification",
            "images": "_sync_omnigent_deployment_images",
        }[blocked_stage],
        blocked,
    )

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(
        uvicorn.Config(api_main.app, lifespan="on", log_level="error")
    )
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(3):
            while not server.started:
                if server_task.done():
                    server_task.result()
                    pytest.fail("API stopped before opening its HTTP listener")
                await asyncio.sleep(0.01)

        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{listener.getsockname()[1]}", trust_env=False
        ) as client:
            assert (await client.get("/healthz")).status_code == 200
            if enabled_setting == "false":
                assert not hasattr(
                    api_main.app.state, "omnigent_bootstrap_reconciliation_task"
                )
                assert not entered.is_set()
                return
            await asyncio.wait_for(entered.wait(), timeout=8)
            task = api_main.app.state.omnigent_bootstrap_reconciliation_task
            assert not task.done()
            assert not available.is_set(), "Startup must preserve the active lease"
            await asyncio.wait_for(inventory_refreshed.wait(), timeout=1)
            inventory_task = api_main.app.state.omnigent_inventory_task
            assert not inventory_task.done()
            response = await client.get("/healthz")
            assert response.status_code == replay["expected"]["healthStatus"]
            assert response.json()["db"] == "connected"
            assert (await client.get("/")).status_code == 307
            if release_wait:
                available.set()
                await asyncio.wait_for(completed.wait(), timeout=2)
                assert not task.done(), "Periodic reconciliation must remain active"
    finally:
        if server.started:
            server.should_exit = True
        else:
            server_task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(server_task, return_exceptions=True), 3
            )
        finally:
            listener.close()
            await engine.dispose()

    assert task.done(), "API shutdown must own reconciliation cleanup"
    assert inventory_task.done(), "API shutdown must also stop inventory refresh"
    assert exited.is_set()
    assert cancelled == (not release_wait)
