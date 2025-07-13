import os
import threading
import time

import pytest
import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession

if not os.getenv("RUN_E2E_TESTS"):
    pytest.skip("E2E tests disabled", allow_module_level=True)
else:
    from playwright.sync_api import sync_playwright

from api_service.api.routers.profile import get_profile_service
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.main import app as main_app


class DummyProfileService:
    def __init__(self):
        self.update_called_with = None

    async def get_or_create_profile(self, db_session: AsyncSession, user_id):
        class Obj:
            openai_api_key = None
            google_api_key = None

        return Obj()

    async def update_profile(self, db_session: AsyncSession, user_id, profile_data):
        self.update_called_with = profile_data
        return None


@pytest.fixture(scope="module")
def server():
    test_user = User(id=__import__("uuid").uuid4(), email="test@example.com")
    dummy_service = DummyProfileService()

    main_app.dependency_overrides[get_current_user] = lambda: test_user
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    main_app.dependency_overrides[get_async_session] = lambda: async_session_maker()
    main_app.dependency_overrides[get_profile_service] = lambda: dummy_service

    config = uvicorn.Config(main_app, host="127.0.0.1", port=8001, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(1)
    yield dummy_service
    server.should_exit = True
    thread.join()


def test_submit_key(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://127.0.0.1:8001/settings")
        page.fill("input[name='openai_api_key']", "browser-key")
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")
        browser.close()

    assert server.update_called_with is not None
