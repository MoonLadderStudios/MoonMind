import os
import json
import threading
import time

import pytest
import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession

if not os.getenv("RUN_E2E_TESTS"):
    pytest.skip("E2E tests disabled", allow_module_level=True)
else:
    from playwright.sync_api import expect, sync_playwright

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


def _fill_queue_task_create_form(page):
    page.wait_for_selector("form#queue-submit-form")
    page.fill("textarea[data-step-field='instructions'][data-step-index='0']", "Ship regression coverage")
    page.fill("input[name='repository']", "moon/demo")
    return page.locator("form#queue-submit-form button[type='submit']")


def _mock_queue_runtime_capabilities(page):
    page.route("**/api/queue/workers/runtime-capabilities", lambda route: route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps({}),
    ))


def _mock_queue_create_job(page, job_id=None, should_fail=False):
    def handler(route):
        if route.request.method != "POST":
            route.continue_()
            return
        if should_fail:
            route.fulfill(
                status=500,
                content_type="application/json",
                body=json.dumps({"detail": "queue error"}),
            )
            return
        payload = json.dumps({"id": job_id or "123e4567-e89b-12d3-a456-426614174000"})
        time.sleep(0.25)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=payload,
        )

    page.route("**/api/queue/jobs", handler)


def test_submit_create_task_flow_successful_navigation(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        _mock_queue_runtime_capabilities(page)
        _mock_queue_create_job(page, job_id="123e4567-e89b-12d3-a456-426614174000")
        page.goto("http://127.0.0.1:8001/tasks/create")

        submit_button = _fill_queue_task_create_form(page)
        assert submit_button.inner_text().strip() == "Create"

        with page.expect_request("**/api/queue/jobs") as request_info:
            submit_button.click()
        assert (
            submit_button.evaluate("el => (el.textContent || '').trim()") == "Submitting..."
        )
        assert request_info.value.method == "POST"
        page.wait_for_url("**/tasks/queue/123e4567-e89b-12d3-a456-426614174000")

        assert page.url.endswith(
            "/tasks/queue/123e4567-e89b-12d3-a456-426614174000"
        )
        browser.close()


def test_submit_create_task_flow_error_restores_label(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        _mock_queue_runtime_capabilities(page)
        _mock_queue_create_job(page, should_fail=True)
        page.goto("http://127.0.0.1:8001/tasks/create")

        submit_button = _fill_queue_task_create_form(page)
        original_label = submit_button.inner_text().strip()
        submit_button.click()

        assert (
            submit_button.evaluate("el => (el.textContent || '').trim()") == "Submitting..."
        )
        expect(submit_button).to_have_text(original_label)
        expect(page.locator("#queue-submit-message")).to_contain_text("Failed to create queue task")
        assert page.url.endswith("/tasks/create")

        browser.close()
