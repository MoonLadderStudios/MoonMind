"""Browser smoke: React islands mount (not shell-only blank content).

Requires RUN_E2E_TESTS=1 and Playwright. Complements manifest/HMTL checks in unit tests.
"""

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
def _playwright_page(playwright_obj):
    browser = playwright_obj.chromium.launch()
    try:
        page = browser.new_page()
        try:
            yield page
        finally:
            page.close()
    finally:
        browser.close()


@pytest.fixture(scope="module")
def server():
    test_user = User(id=uuid4(), email="mc-smoke@example.com")
    main_app.dependency_overrides[get_current_user] = lambda: test_user
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    main_app.dependency_overrides[get_async_session] = lambda: async_session_maker()

    config = uvicorn.Config(main_app, host="127.0.0.1", port=8012, log_level="warning")
    server_inst = uvicorn.Server(config)
    thread = threading.Thread(target=server_inst.run, daemon=True)
    thread.start()
    time.sleep(1.5)
    yield
    server_inst.should_exit = True
    thread.join(timeout=5)
    main_app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "path,expected_text",
    [
        ("/tasks/list", "Tasks List"),
        ("/tasks/settings?section=operations", "Operations"),
        ("/tasks/settings?section=providers-secrets", "Provider Profiles"),
    ],
)
def test_react_page_shows_app_content(server, path: str, expected_text: str) -> None:
    with sync_playwright() as p:
        with _playwright_page(p) as page:
            page.goto(f"http://127.0.0.1:8012{path}", wait_until="domcontentloaded")
            root = page.locator("#mission-control-root")
            expect(root).to_be_visible()
            expect(root).to_contain_text(expected_text, timeout=15_000)
