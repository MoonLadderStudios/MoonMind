"""Browser journey: Settings Operations drives the real controller operation.

MoonLadderStudios/MoonMind#4502. The compiled dashboard and the real API
application run with Temporal stopped; the Operations page submits to the
real ``deploy/controller`` WSGI endpoint (a recording applier stands in for
Docker). The journey shows the current target, the observed installed
state, the original error, logs, and Retry; a reload reattaches to the same
operation without reapplying; and a historical workflow-backed update stays
readable as history.

Requires RUN_E2E_TESTS=1, Playwright, and a production dashboard build
(``vite build --config frontend/vite.config.ts``).
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

if not os.getenv("RUN_E2E_TESTS"):
    pytest.skip("E2E tests disabled", allow_module_level=True)

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_MANIFEST = (
    REPO_ROOT / "api_service/static/workflow_console/dist/.vite/manifest.json"
)
if not os.getenv("VITE_MANIFEST_PATH") and LOCAL_MANIFEST.is_file():
    # Serve this checkout's production build, not an older bundled one.
    os.environ["VITE_MANIFEST_PATH"] = str(LOCAL_MANIFEST)

import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

from api_service.api.routers.deployment_operations import (
    _get_temporal_execution_service,
)
from api_service.auth_providers import (
    get_current_user,
    get_current_user_optional,
)
from api_service.db.models import User
from api_service.main import app as main_app
from tests.unit.api.routers.test_deployment_controller_operations import (
    IMAGE_A,
    SECRET,
    _Controller,
    _TemporalHistoryOnly,
)

PORT = 8014
# One Update history entry (the history container shares the rounded style).
ACTION_CARD = "div.rounded-2xl.bg-slate-50"
BASE = f"http://127.0.0.1:{PORT}"
USER_DEPENDENCY_NAMES = {
    "_current_user_fallback",
    "_strict_current_user",
    "_optional_current_user",
}


def _user_dependencies() -> set[object]:
    found: set[object] = {get_current_user(), get_current_user_optional()}
    for route in main_app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        stack = list(dependant.dependencies)
        while stack:
            dependency = stack.pop()
            if getattr(dependency.call, "__name__", "") in USER_DEPENDENCY_NAMES:
                found.add(dependency.call)
            stack.extend(dependency.dependencies)
    return found


@pytest.fixture
def journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Controller]:
    controller = _Controller(tmp_path)
    monkeypatch.setenv("MOONMIND_CONTROLLER_URL", controller.url)
    monkeypatch.setenv("MOONMIND_CONTROLLER_SECRET", SECRET)
    monkeypatch.setenv("MOONMIND_CONTROLLER_STATE_DIR", str(controller.state_dir))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv(
        "MOONMIND_SESSION_KEY_PATH", str(tmp_path / "session-keys" / "key")
    )
    # Hermetic: the journey needs no database, so every session points at an
    # unreachable one and startup seeding (lifespan) never runs. A configured
    # deployment database must never receive writes from this test.
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    import api_service.db.base as db_base

    isolated = create_async_engine("postgresql+asyncpg://journey@127.0.0.1:9/none")
    monkeypatch.setattr(db_base, "engine", isolated)
    monkeypatch.setattr(
        db_base,
        "async_session_maker",
        sessionmaker(isolated, class_=AsyncSession, expire_on_commit=False),
    )

    operator = User(
        id=uuid4(),
        email="operator@example.com",
        is_active=True,
        is_superuser=True,
    )
    for dependency in _user_dependencies():
        main_app.dependency_overrides[dependency] = lambda: operator
    main_app.dependency_overrides[get_current_user] = lambda: operator
    # Temporal is stopped for submission; it can still list one historical
    # workflow-backed update, which must stay readable as history.
    main_app.dependency_overrides[_get_temporal_execution_service] = (
        _TemporalHistoryOnly
    )

    server = uvicorn.Server(
        uvicorn.Config(
            main_app,
            host="127.0.0.1",
            port=PORT,
            log_level="warning",
            lifespan="off",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started and time.time() < deadline:
        time.sleep(0.1)
    assert server.started, "API server did not start"
    try:
        yield controller
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        main_app.dependency_overrides.clear()
        controller.close()


def _open_operations(page: Page) -> None:
    page.goto(f"{BASE}/settings/operations", wait_until="domcontentloaded")
    expect(page.get_by_role("button", name="Update MoonMind")).to_be_enabled(
        timeout=30_000
    )


def test_operations_update_journey_through_the_real_controller(
    journey: _Controller,
) -> None:
    journey.behavior = journey.fail
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())

            _open_operations(page)
            update = page.get_by_role("region", name="MoonMind update")
            expect(update.get_by_text("Running image")).to_be_visible()
            update.get_by_label("Update to").fill("20260425.1234")
            update.get_by_role("button", name="Update MoonMind").click()

            # The exhausted controller operation shows its target, observed
            # (unconfirmed) installed state, original error, logs, and Retry.
            expect(update).to_contain_text("Original error", timeout=30_000)
            operation_id = journey.applied[0]
            action = update.locator(ACTION_CARD, has_text=f"Operation {operation_id}")
            expect(action).to_contain_text("20260425.1234")
            expect(action).to_contain_text("Installed: not confirmed")
            expect(action).to_contain_text(
                "Original error: attempt 1: apply failed on attempt 1"
            )
            action.get_by_role("button", name="View logs").click()
            expect(action).to_contain_text("apply failed on attempt 1")
            attempts_before_retry = len(journey.applied)

            journey.behavior = journey.succeed
            action.get_by_role("button", name="Retry").click()
            expect(action).to_contain_text(f"Installed: {IMAGE_A}", timeout=30_000)
            # The first failure survives the successful fresh attempt.
            expect(action).to_contain_text("Original error: attempt 1")
            expect(action.get_by_role("button", name="Retry")).to_have_count(0)
            assert len(journey.applied) == attempts_before_retry + 1

            # A reload reattaches to the same operation without reapplying.
            applied_before_reload = list(journey.applied)
            page.reload(wait_until="domcontentloaded")
            reloaded = page.get_by_role("region", name="MoonMind update").locator(
                ACTION_CARD, has_text=f"Operation {operation_id}"
            )
            expect(reloaded).to_contain_text(f"Installed: {IMAGE_A}", timeout=30_000)
            assert journey.applied == applied_before_reload

            # The historical workflow-backed update is readable history with
            # its run detail link, and offers no executable retry.
            historical = page.get_by_role("region", name="MoonMind update").locator(
                ACTION_CARD, has=page.get_by_role("link", name="Run detail")
            )
            expect(historical).to_contain_text("20260401.0001")
            expect(historical.get_by_role("link", name="Run detail")).to_have_attribute(
                "href", re.compile(r"/workflows/mm:historical-update$")
            )
            expect(historical.get_by_role("button", name="Retry")).to_have_count(0)
            assert page_errors == []
        finally:
            browser.close()
