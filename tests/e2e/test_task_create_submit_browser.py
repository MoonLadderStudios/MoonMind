import json
import os
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from api_service.auth_providers import get_current_user
from api_service.db.models import User
from api_service.main import app as main_app

if not os.getenv("RUN_E2E_TESTS"):
    pytest.skip("E2E tests disabled", allow_module_level=True)


@pytest.fixture(scope="module")
def server():
    test_user = User(id=__import__("uuid").uuid4(), email="test@example.com")

    main_app.dependency_overrides[get_current_user] = lambda: test_user
    config = uvicorn.Config(main_app, host="127.0.0.1", port=8001, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(1)
    yield
    server.should_exit = True
    thread.join()


def _route_handlers(page, *, create_status, create_body, create_delay_seconds=0.15):
    calls = {"create": 0}
    base_url = "http://127.0.0.1:8001"

    def _mock_runtime_capabilities(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "items": {
                    "codex": {"models": ["gpt-5.3-codex"], "efforts": ["high"]},
                    "gemini": {"models": [], "efforts": []},
                    "claude": {"models": [], "efforts": []},
                },
            }),
        )

    def _mock_skills(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"items": [{"id": "speckit-orchestrate"}]}),
        )

    def _mock_worker_pause(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "system": {"workersPaused": False, "mode": "running"},
                "metrics": {"isDrained": True},
            }),
        )

    def _mock_create(route):
        calls["create"] += 1
        time.sleep(create_delay_seconds)
        route.fulfill(
            status=create_status,
            content_type="application/json",
            body=create_body,
        )

    def _mock_detail(route):
        if route.request.method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"id": "not-found"}),
            )
            return
        route.continue_()

    page.route(f"{base_url}/api/queue/workers/runtime-capabilities", _mock_runtime_capabilities)
    page.route(f"{base_url}/api/tasks/skills", _mock_skills)
    page.route(f"{base_url}/api/system/worker-pause", _mock_worker_pause)
    page.route(f"{base_url}/api/queue/jobs", _mock_create)
    page.route(f"{base_url}/api/queue/jobs/*", _mock_detail)
    return calls


def _read_submit_label(page):
    return (page.locator("#queue-submit-form button[type='submit']").text_content() or "").strip()


def _assert_inflight_label(page, expected_label):
    page.wait_for_function(
        "(expected) => {\n      const button = document.querySelector('#queue-submit-form button[type=\"submit\"]');\n      return button && button.textContent.trim() === expected;\n    }",
        expected=expected_label,
    )


def test_submit_create_flows_to_task_detail(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        job_id = "11111111-1111-4111-8111-111111111111"
        calls = _route_handlers(
            page,
            create_status=201,
            create_body=json.dumps({"id": job_id}),
            create_delay_seconds=0.25,
        )

        page.goto("http://127.0.0.1:8001/tasks/create")
        page.wait_for_selector("#queue-submit-form")
        page.fill('textarea[data-step-field="instructions"][data-step-index="0"]', "Run end-to-end regression flow.")
        page.fill('input[name="repository"]', "MoonLadderStudios/MoonMind")

        submit_button = page.locator("#queue-submit-form button[type='submit']")
        original_label = _read_submit_label(page)
        assert original_label == "Create"

        submit_button.click()
        _assert_inflight_label(page, "Submitting...")
        assert _read_submit_label(page) == "Submitting..."

        page.wait_for_url(f"**/tasks/queue/{job_id}")
        assert page.url.endswith(f"/tasks/queue/{job_id}")
        assert calls["create"] == 1
        browser.close()


def test_submit_error_restores_label(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        calls = _route_handlers(
            page,
            create_status=500,
            create_body=json.dumps({"detail": {"message": "service unavailable"}}),
            create_delay_seconds=0.1,
        )

        page.goto("http://127.0.0.1:8001/tasks/create")
        page.wait_for_selector("#queue-submit-form")
        page.fill('textarea[data-step-field="instructions"][data-step-index="0"]', "Run end-to-end regression flow.")
        page.fill('input[name="repository"]', "MoonLadderStudios/MoonMind")

        submit_button = page.locator("#queue-submit-form button[type='submit']")
        original_label = _read_submit_label(page)
        assert original_label == "Create"

        submit_button.click()
        _assert_inflight_label(page, "Submitting...")
        page.wait_for_function(
            f"(expected) => {\n      const button = document.querySelector('#queue-submit-form button[type=\"submit\"]');\n      return button && button.textContent.trim() === expected;\n    }",
            original_label,
        )
        assert _read_submit_label(page) == original_label
        assert page.url.endswith("/tasks/create")
        assert calls["create"] == 1
        browser.close()
