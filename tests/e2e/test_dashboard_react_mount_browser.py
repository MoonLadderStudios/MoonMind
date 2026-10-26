"""Browser smoke: React islands mount (not shell-only blank content).

Requires RUN_E2E_TESTS=1 and Playwright. Complements manifest/HMTL checks in unit tests.
"""

from __future__ import annotations

import asyncio
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

from api_service.auth_providers import get_instance_access
from api_service.db import base as db_base
from api_service.db.base import get_async_session
from api_service.db.models import Base
from api_service.main import app as main_app
from tests.helpers.account_source import User


@contextmanager
def _playwright_page(playwright_obj):
    browser = playwright_obj.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHROMIUM_CHANNEL") or None)
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
    original_overrides = dict(main_app.dependency_overrides)
    original_session_maker = db_base.async_session_maker
    test_user = User(id=uuid4(), email="mc-smoke@example.com")
    main_app.dependency_overrides[get_instance_access] = lambda: test_user
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async def create_tables() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())

    async_session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    main_app.dependency_overrides[get_async_session] = lambda: async_session_maker()
    db_base.async_session_maker = async_session_maker

    config = uvicorn.Config(main_app, host="127.0.0.1", port=8012, log_level="warning")
    server_inst = uvicorn.Server(config)
    thread = threading.Thread(target=server_inst.run, daemon=True)
    try:
        thread.start()
        deadline = time.monotonic() + 5
        while not server_inst.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server_inst.started, "Browser fixture HTTP server failed to start"
        yield
    finally:
        server_inst.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive(), "Browser fixture HTTP server failed to stop"
        db_base.async_session_maker = original_session_maker
        main_app.dependency_overrides = original_overrides
        asyncio.run(engine.dispose())

@pytest.mark.parametrize(
    "path,expected_text",
    [
        ("/workflows", "Workflows"),
        ("/settings/operations", "Operations"),
        ("/settings/providers-secrets", "Provider Profiles"),
    ],
)
def test_react_page_shows_app_content(server, path: str, expected_text: str) -> None:
    with sync_playwright() as p:
        with _playwright_page(p) as page:
            page.goto(f"http://127.0.0.1:8012{path}", wait_until="domcontentloaded")
            root = page.locator("#dashboard-app-root")
            expect(root).to_be_visible()
            expect(root).to_contain_text(expected_text, timeout=15_000)
