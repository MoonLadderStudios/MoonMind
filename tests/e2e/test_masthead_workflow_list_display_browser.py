"""Browser checks for MM-1114 masthead workflow list display control."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

import pytest
import uvicorn

if not os.getenv("RUN_E2E_TESTS"):
    pytest.skip("E2E tests disabled", allow_module_level=True)

from playwright.sync_api import expect, sync_playwright

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.main import app as main_app


@contextmanager
def _playwright_page(playwright_obj, *, width: int = 1366, height: int = 900):
    browser = playwright_obj.chromium.launch()
    try:
        page = browser.new_page(viewport={"width": width, "height": height})
        try:
            yield page
        finally:
            page.close()
    finally:
        browser.close()


@pytest.fixture(scope="module")
def server():
    test_user = User(id=uuid4(), email="mm-1114@example.com")
    main_app.dependency_overrides[get_current_user] = lambda: test_user
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    main_app.dependency_overrides[get_async_session] = lambda: async_session_maker()

    config = uvicorn.Config(main_app, host="127.0.0.1", port=8013, log_level="warning")
    server_inst = uvicorn.Server(config)
    thread = threading.Thread(target=server_inst.run, daemon=True)
    thread.start()
    time.sleep(1.5)
    yield
    server_inst.should_exit = True
    thread.join(timeout=5)
    main_app.dependency_overrides.clear()


@pytest.mark.parametrize("path", ["/workflows", "/workflows/new", "/workflows/mm%3A1114"])
def test_mm1114_desktop_masthead_order_and_radio_semantics(server, path: str) -> None:
    with sync_playwright() as p:
        with _playwright_page(p) as page:
            page.goto(f"http://127.0.0.1:8013{path}", wait_until="domcontentloaded")
            group = page.get_by_role("radiogroup", name="Workflow list display")
            expect(group).to_be_visible(timeout=15_000)

            brand_box = page.get_by_role("link", name="MoonMind workflows").bounding_box()
            group_box = group.bounding_box()
            nav_box = page.get_by_role("navigation", name="MoonMind navigation").bounding_box()
            assert brand_box is not None
            assert group_box is not None
            assert nav_box is not None
            assert brand_box["x"] < group_box["x"] < nav_box["x"]

            expect(page.get_by_role("radio", name="No list")).to_be_visible()
            expect(page.get_by_role("radio", name="Sidebar list")).to_be_visible()
            expect(page.get_by_role("radio", name="Full screen table")).to_be_visible()


def test_mm1114_keyboard_changes_options_and_restores_route_focus(server) -> None:
    with sync_playwright() as p:
        with _playwright_page(p) as page:
            page.goto("http://127.0.0.1:8013/workflows/new", wait_until="domcontentloaded")
            expect(page.get_by_role("radiogroup", name="Workflow list display")).to_be_visible(timeout=15_000)

            page.get_by_role("link", name="MoonMind workflows").focus()
            page.keyboard.press("Tab")
            expect(page.get_by_role("radio", name="Sidebar list")).to_be_focused()

            page.keyboard.press("ArrowLeft")
            expect(page.get_by_role("radio", name="No list")).to_be_checked()
            expect(page.get_by_role("radio", name="No list")).to_be_focused()

            page.get_by_role("radio", name="Full screen table").click()
            page.wait_for_url("**/workflows")
            expect(page.get_by_role("radio", name="Full screen table")).to_be_checked()
            expect(page.get_by_role("radio", name="Full screen table")).to_be_focused()


def test_mm1114_non_participating_and_mobile_surfaces_hide_control(server) -> None:
    with sync_playwright() as p:
        with _playwright_page(p) as page:
            page.goto("http://127.0.0.1:8013/settings", wait_until="domcontentloaded")
            expect(page.get_by_role("radiogroup", name="Workflow list display")).to_have_count(0)

        with _playwright_page(p, width=390, height=844) as page:
            page.goto("http://127.0.0.1:8013/workflows", wait_until="domcontentloaded")
            expect(page.get_by_role("radiogroup", name="Workflow list display")).to_have_count(0)
