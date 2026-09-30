"""Production-built Operations journey through the real controller (MoonLadderStudios/MoonMind#4502).

A browser drives the compiled dashboard (``npm run ui:build``) that the real
``api_service.main`` app serves. The operator is the default local principal
resolved by the ordinary disabled-auth boundary from the database. The
Operations router reaches the shipped ``deploy/controller`` app through
``controller.sock`` in the mounted deployment-state layout, with no controller
URL or secret injected. Temporal is stopped: creating a workflow fails the
test, so every update is a controller operation.

Requires RUN_E2E_TESTS=1, Playwright (Chromium), and a built dashboard.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator

import pytest
import uvicorn

if not os.getenv("RUN_E2E_TESTS"):
    pytest.skip("E2E tests disabled", allow_module_level=True)

# Serve this checkout's compiled dashboard rather than an image-baked bundle.
# The app mounts its dist directory at import, as a deployment does at start.
os.environ.setdefault(
    "VITE_MANIFEST_PATH",
    str(
        Path(__file__).resolve().parents[2]
        / "api_service/static/workflow_console/dist/.vite/manifest.json"
    ),
)

from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from api_service.api.routers.deployment_operations import (
    _get_temporal_execution_service,
)
from api_service.auth import _DEFAULT_USER_ID
from api_service.db.base import get_async_session
from api_service.db.models import Base, User
from api_service.main import app as main_app
from api_service.ui_assets import resolve_dashboard_dist_root
from moonmind.workflows.skills.deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    DEPLOYMENT_UPDATE_TOOL_VERSION,
)
from tests.unit.api.routers.test_deployment_operations_controller import (
    SECRET,
    _Controller,
    _TemporalStopped,
)

REPOSITORY = "ghcr.io/moonladderstudios/moonmind"
INSTALLED = f"{REPOSITORY}:20260425.1234"
TARGET = f"{REPOSITORY}:latest"
HISTORICAL_WORKFLOW_ID = "mm:historical-update"
TIMEOUT_MS = 20_000
# Progress arrives through the page's bounded 5s polling.
expect.set_options(timeout=TIMEOUT_MS)
# One "Update history" row.
HISTORY_ENTRY = "div.rounded-2xl.bg-slate-50"


def _manifest_path() -> Path:
    return resolve_dashboard_dist_root() / ".vite" / "manifest.json"


class _StoppedTemporalWithHistory(_TemporalStopped):
    """Stopped Temporal whose retained projection holds one old update."""

    async def list_executions(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    workflow_id=HISTORICAL_WORKFLOW_ID,
                    run_id="11111111-2222-3333-4444-555555555555",
                    state="completed",
                    close_status="completed",
                    parameters={
                        "workflow": {
                            "operation": {"kind": "update"},
                            "plan": [
                                {
                                    "tool": {
                                        "name": DEPLOYMENT_UPDATE_TOOL_NAME,
                                        "version": DEPLOYMENT_UPDATE_TOOL_VERSION,
                                    },
                                    "inputs": {
                                        "stack": "moonmind",
                                        "image": {
                                            "repository": REPOSITORY,
                                            "reference": "20260425.1234",
                                        },
                                        "mode": "changed_services",
                                        "reason": "Workflow-era release",
                                    },
                                }
                            ],
                        }
                    },
                    memo={},
                    artifact_refs=[],
                    started_at="2026-04-25T18:00:00Z",
                    closed_at="2026-04-25T18:04:00Z",
                )
            ]
        )


class _Gate:
    """Hold an attempt in ``applying`` until the journey releases it."""

    def __init__(self, controller: _Controller) -> None:
        self.controller = controller
        self.release = threading.Event()

    def holding(self, outcome: Callable[[dict[str, Any]], None]):
        self.release.clear()

        def behavior(operation: dict[str, Any]) -> None:
            self.controller.store.mark_stage(operation["operationId"], stage="applying")
            assert self.release.wait(timeout=60), "journey never released the attempt"
            outcome(operation)

        return behavior


class _Api:
    """The real API app on a fixed loopback port; replaceable mid-journey."""

    def __init__(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.server = uvicorn.Server(
            uvicorn.Config(
                main_app,
                host="127.0.0.1",
                port=self.port,
                log_level="warning",
                lifespan="off",
            )
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            assert self.thread.is_alive(), "API did not start"
            assert time.monotonic() < deadline, "API did not start"
            time.sleep(0.05)

    def stop(self) -> None:
        if self.server is not None and self.thread is not None:
            self.server.should_exit = True
            self.thread.join(timeout=10)
        self.server = self.thread = None


def _default_operator_session():
    """Application schema holding only the default local operator row."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def seed() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as session:
            session.add(
                User(
                    id=uuid.UUID(_DEFAULT_USER_ID),
                    email="operator@localhost",
                    is_active=True,
                    is_verified=True,
                )
            )
            await session.commit()

    asyncio.run(seed())

    async def get_session():
        async with maker() as session:
            yield session

    return get_session


@pytest.fixture
def journey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[_Api, _Controller, _Gate]]:
    manifest = _manifest_path()
    assert (
        manifest.is_file()
    ), f"build the dashboard first (npm run ui:build): {manifest}"
    for name in (
        "MOONMIND_CONTROLLER_URL",
        "MOONMIND_CONTROLLER_SECRET",
        "MOONMIND_CONTROLLER_SECRET_FILE",
        "MOONMIND_UI_DEV_SERVER_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    # docker-compose.yaml mounts deploy/state at /workspace/deployment_state
    # and points the desired-state sidecar there; the API derives the
    # controller's socket and credential from that mount alone.
    mounted = tmp_path / "deployment_state"
    mounted.mkdir()
    sidecar = mounted / "desired-state.json"
    sidecar.write_text(
        json.dumps(
            {
                "stack": "moonmind",
                "imageRepository": REPOSITORY,
                "requestedReference": "20260425.1234",
            }
        )
    )
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(sidecar))
    # A short acknowledgment window lets the browser see the lost-response
    # path: the API reconciles the running operation instead of failing it.
    monkeypatch.setattr(
        "api_service.services.deployment_operations.CONTROLLER_SUBMIT_TIMEOUT_SECONDS",
        1,
    )
    controller = _Controller(
        tmp_path, monkeypatch, mounted_state=mounted / "controller"
    )
    gate = _Gate(controller)

    overrides = dict(main_app.dependency_overrides)
    main_app.dependency_overrides[get_async_session] = _default_operator_session()
    main_app.dependency_overrides[_get_temporal_execution_service] = (
        _StoppedTemporalWithHistory
    )
    api = _Api()
    api.start()
    try:
        yield api, controller, gate
    finally:
        gate.release.set()
        api.stop()
        main_app.dependency_overrides.clear()
        main_app.dependency_overrides.update(overrides)
        controller.close()


def _card(page: Page):
    return page.get_by_role("region", name="MoonMind update")


def _operation_entry(page: Page, operation_id: str):
    return (
        _card(page).locator(HISTORY_ENTRY).filter(has_text=f"Operation {operation_id}")
    )


def _status(entry):
    """The row's status header, apart from error text that may say "failed"."""
    return entry.locator("div.font-semibold").first


def _open_operations(page: Page, base: str) -> None:
    response = page.goto(f"{base}/settings/operations", wait_until="domcontentloaded")
    assert response is not None and response.ok, response and response.status
    # The page is the compiled bundle from the Vite manifest, not a dev server.
    manifest = json.loads(_manifest_path().read_text(encoding="utf-8"))
    compiled = {
        f"/static/workflow_console/dist/{entry['file']}" for entry in manifest.values()
    }
    scripts = page.locator("script[src]").evaluate_all(
        "nodes => nodes.map(node => new URL(node.src).pathname)"
    )
    assert scripts and set(scripts) <= compiled, scripts
    expect(_card(page)).to_be_visible()


def test_operations_journey_follows_one_controller_operation(
    journey: tuple[_Api, _Controller, _Gate],
) -> None:
    api, controller, gate = journey
    update_posts: list[str] = []
    retry_posts: list[str] = []
    page_errors: list[str] = []

    def track(request) -> None:
        if request.method != "POST":
            return
        if request.url.endswith("/api/v1/operations/deployment/update"):
            update_posts.append(request.url)
        elif request.url.endswith("/retry"):
            retry_posts.append(request.url)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_default_timeout(TIMEOUT_MS)
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            page.on("request", track)

            _open_operations(page, api.base)
            card = _card(page)
            expect(card.get_by_text("Running image").locator("..")).to_contain_text(
                INSTALLED
            )
            # Historical workflow-backed rows stay readable history with no
            # action that revives their engine.
            run_detail = card.get_by_role("link", name="Run detail")
            expect(run_detail).to_have_attribute(
                "href", f"/workflows/{HISTORICAL_WORKFLOW_ID}"
            )
            historical = card.locator(HISTORY_ENTRY).filter(
                has=page.get_by_role("link", name="Run detail")
            )
            expect(_status(historical)).to_have_text(re.compile(r"^Completed\b", re.I))
            expect(historical.get_by_role("button", name="Retry")).to_have_count(0)

            # Submit: the controller holds the attempt, the API's
            # acknowledgment window lapses, and the same operation is shown.
            controller.behavior = gate.holding(controller.fail)
            card.get_by_role("button", name="Update MoonMind").click()
            expect(card).to_contain_text(
                re.compile(
                    r"accepted by the deployment controller: operation (\S+) \(running\)",
                    re.I,
                )
            )
            assert len(controller.applied) == 1
            operation_id = controller.applied[0]
            entry = _operation_entry(page, operation_id)
            expect(_status(entry)).to_have_text(re.compile(r"^Running\b", re.I))
            expect(entry).to_contain_text("Installed: not confirmed")

            # API replacement and a browser reload (no server or client
            # state survives) reconnect to the controller's record without
            # submitting again.
            api.stop()
            api.start()
            page.reload(wait_until="domcontentloaded")
            expect(_card(page)).to_be_visible()
            entry = _operation_entry(page, operation_id)
            expect(_status(entry)).to_have_text(re.compile(r"^Running\b", re.I))
            assert len(update_posts) == 1

            # Duplicate submission of the same target reattaches.
            _card(page).get_by_role("button", name="Update MoonMind").click()
            expect(_card(page)).to_contain_text(
                re.compile(
                    rf"accepted by the deployment controller: operation {re.escape(operation_id)} \(running\)",
                    re.I,
                )
            )
            assert len(update_posts) == 2
            assert controller.applied == [operation_id]

            # The controller exhausts its automatic attempts; ordinary
            # bounded reads show the original failure, redacted.
            gate.release.set()
            expect(_status(entry)).to_have_text(re.compile(r"^Failed\b", re.I))
            expect(entry).to_contain_text("Original error: pull failed for attempt 1")
            expect(entry).to_contain_text("Installed: not confirmed")
            entry.get_by_role("button", name="Show logs").click()
            expect(entry).to_contain_text("attempt 1: pull failed for attempt 1")
            expect(entry).to_contain_text("attempt 3: pull failed for attempt 3")
            exhausted = len(controller.applied)
            assert exhausted == controller.record.MAX_AUTO_ATTEMPTS

            # Explicit Retry asks the same controller operation for a fresh
            # bounded attempt; the first failure stays visible.
            controller.behavior = gate.holding(controller._succeed)
            entry.get_by_role("button", name="Retry").click()
            expect(_card(page)).to_contain_text(
                re.compile(
                    rf"Retry accepted for operation {re.escape(operation_id)} \(running\)",
                    re.I,
                )
            )
            expect(_status(entry)).to_have_text(re.compile(r"^Running\b", re.I))
            expect(entry).to_contain_text("Original error: pull failed for attempt 1")
            assert len(retry_posts) == 1
            assert len(update_posts) == 2

            gate.release.set()
            expect(_status(entry)).to_have_text(re.compile(r"^Succeeded\b", re.I))
            expect(entry).to_contain_text(f"Installed: {TARGET}")
            expect(entry.get_by_role("button", name="Retry")).to_have_count(0)
            assert controller.applied == [operation_id] * (exhausted + 1)

            body = page.content()
            assert SECRET not in body
            assert "abc123" not in body
            assert not page_errors, page_errors
        finally:
            browser.close()
