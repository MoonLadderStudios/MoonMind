import json
import os
import socket
import threading
import time
import uuid
from contextlib import closing

import pytest
import uvicorn

from api_service.api.routers.task_dashboard import _resolve_user_dependency_overrides
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.main import app as main_app
from moonmind.config.settings import settings

if not os.getenv("RUN_E2E_TESTS"):
    pytest.skip("E2E tests disabled", allow_module_level=True)
else:
    from playwright.sync_api import sync_playwright


def _reserve_free_port(host="127.0.0.1"):
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def _wait_for_tcp_host(host, port, timeout_seconds=3.0):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.1)
    pytest.fail(f"Server did not start in time on {host}:{port}")


@pytest.fixture(scope="module")
def server():
    test_user = User(id=uuid.uuid4(), email="test@example.com")
    original_overrides = dict(main_app.dependency_overrides)

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    for dependency in _resolve_user_dependency_overrides():
        main_app.dependency_overrides[dependency] = (
            lambda test_user=test_user: test_user
        )

    in_memory_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    in_memory_session_maker = async_sessionmaker(
        bind=in_memory_engine,
        expire_on_commit=False,
    )
    main_app.dependency_overrides[get_async_session] = lambda: in_memory_session_maker()

    port = _reserve_free_port()
    config = uvicorn.Config(main_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    try:
        thread.start()
        _wait_for_tcp_host("127.0.0.1", port)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join()
        main_app.dependency_overrides = original_overrides
        in_memory_engine.dispose()


def _route_handlers(
    page,
    *,
    base_url,
    create_status,
    create_body,
    create_delay_seconds=0.15,
):
    calls = {"create": 0}
    base_url = base_url.rstrip("/")

    def _mock_runtime_capabilities(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "items": {
                        "codex": {"models": ["gpt-5.3-codex"], "efforts": ["high"]},
                        "gemini": {"models": [], "efforts": []},
                        "claude": {"models": [], "efforts": []},
                    },
                }
            ),
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
            body=json.dumps(
                {
                    "system": {"workersPaused": False, "mode": "running"},
                    "metrics": {"isDrained": True},
                }
            ),
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

    def _mock_task_step_templates(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"items": []}),
        )

    page.route(
        f"{base_url}/api/queue/workers/runtime-capabilities", _mock_runtime_capabilities
    )
    page.route(f"{base_url}/api/tasks/skills", _mock_skills)
    page.route(f"{base_url}/api/system/worker-pause", _mock_worker_pause)
    page.route(f"{base_url}/api/task-step-templates*", _mock_task_step_templates)
    page.route(f"{base_url}/api/queue/jobs", _mock_create)
    page.route(f"{base_url}/api/queue/jobs/*", _mock_detail)
    return calls


def _read_submit_label(page):
    return (
        page.locator("#queue-submit-form button[type='submit']").text_content() or ""
    ).strip()


def _assert_inflight_label(page, expected_label):
    script = """
        (expectedLabel) => {
            const button = document.querySelector(
                '#queue-submit-form button[type="submit"]'
            );
            return button && button.textContent.trim() === expectedLabel;
        }
    """
    page.wait_for_function(
        script,
        arg=expected_label,
    )


def test_submit_create_flows_to_task_detail(server):
    base_url = server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        job_id = "11111111-1111-4111-8111-111111111111"
        calls = _route_handlers(
            page,
            base_url=base_url,
            create_status=201,
            create_body=json.dumps({"id": job_id}),
            create_delay_seconds=0.25,
        )

        page.goto(f"{base_url}/tasks/create")
        page.wait_for_selector("#queue-submit-form")
        page.fill(
            'textarea[data-step-field="instructions"][data-step-index="0"]',
            "Run end-to-end regression flow.",
        )
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
    base_url = server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        calls = _route_handlers(
            page,
            base_url=base_url,
            create_status=500,
            create_body=json.dumps({"detail": {"message": "service unavailable"}}),
            create_delay_seconds=0.1,
        )

        page.goto(f"{base_url}/tasks/create")
        page.wait_for_selector("#queue-submit-form")
        page.fill(
            'textarea[data-step-field="instructions"][data-step-index="0"]',
            "Run end-to-end regression flow.",
        )
        page.fill('input[name="repository"]', "MoonLadderStudios/MoonMind")

        submit_button = page.locator("#queue-submit-form button[type='submit']")
        original_label = _read_submit_label(page)
        assert original_label == "Create"

        submit_button.click()
        _assert_inflight_label(page, "Submitting...")
        _assert_inflight_label(page, original_label)
        assert _read_submit_label(page) == original_label
        assert page.url.endswith("/tasks/create")
        assert calls["create"] == 1
        browser.close()


def test_temporal_detail_resolves_source_and_fetches_latest_run_artifacts(server):
    base_url = server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        _route_handlers(
            page,
            base_url=base_url,
            create_status=201,
            create_body=json.dumps({"id": "unused"}),
        )
        ordered_calls = []

        page.route(
            f"{base_url}/api/tasks/mm:workflow-123/source",
            lambda route: (
                ordered_calls.append("source"),
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "taskId": "mm:workflow-123",
                            "source": "temporal",
                            "sourceLabel": "Temporal",
                            "detailPath": "/tasks/mm:workflow-123?source=temporal",
                        }
                    ),
                ),
            )[1],
        )
        page.route(
            f"{base_url}/api/executions/mm:workflow-123",
            lambda route: (
                ordered_calls.append("detail"),
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "taskId": "mm:workflow-123",
                            "workflowId": "mm:workflow-123",
                            "runId": "run-123",
                            "temporalRunId": "run-123",
                            "namespace": "moonmind",
                            "workflowType": "MoonMind.Run",
                            "state": "awaiting_external",
                            "dashboardStatus": "awaiting_action",
                            "temporalStatus": "running",
                            "waitingReason": "Waiting on approval.",
                            "attentionRequired": True,
                            "memo": {
                                "title": "Temporal task",
                                "summary": "Waiting on approval.",
                            },
                            "startedAt": "2026-03-06T10:00:00Z",
                            "updatedAt": "2026-03-06T11:00:00Z",
                            "actions": {
                                "canApprove": True,
                                "canPause": True,
                                "canResume": True,
                                "canCancel": True,
                            },
                        }
                    ),
                ),
            )[1],
        )
        page.route(
            f"{base_url}/api/executions/moonmind/mm:workflow-123/run-123/artifacts",
            lambda route: (
                ordered_calls.append("artifacts"),
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"artifacts": []}),
                ),
            )[1],
        )

        page.goto(f"{base_url}/tasks/mm:workflow-123")
        page.wait_for_selector("text=Temporal Task Detail")
        assert page.url.endswith("/tasks/mm:workflow-123")
        assert ordered_calls[:3] == ["source", "detail", "artifacts"]
        browser.close()


def test_temporal_submit_redirects_without_exposing_runtime_picker(server):
    base_url = server
    original_submit_enabled = settings.temporal_dashboard.submit_enabled
    settings.temporal_dashboard.submit_enabled = True
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            _route_handlers(
                page,
                base_url=base_url,
                create_status=201,
                create_body=json.dumps({"id": "unused"}),
            )
            artifact_calls = {"create": 0, "upload": 0, "link": 0, "execution": 0}

            page.route(
                f"{base_url}/api/artifacts",
                lambda route: (
                    artifact_calls.__setitem__("create", artifact_calls["create"] + 1),
                    route.fulfill(
                        status=201,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "artifact_ref": {
                                    "artifact_id": "art_01ARZ3NDEKTSV4RRFFQ69G5FAV"
                                },
                                "upload": {
                                    "mode": "single",
                                    "upload_url": f"{base_url}/api/artifacts/art_01ARZ3NDEKTSV4RRFFQ69G5FAV/content",
                                    "expires_at": "2026-03-06T12:00:00Z",
                                    "max_size_bytes": 100000,
                                    "required_headers": {},
                                },
                            }
                        ),
                    ),
                )[1],
            )
            page.route(
                f"{base_url}/api/artifacts/art_01ARZ3NDEKTSV4RRFFQ69G5FAV/content",
                lambda route: (
                    artifact_calls.__setitem__("upload", artifact_calls["upload"] + 1),
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(
                            {"artifact_id": "art_01ARZ3NDEKTSV4RRFFQ69G5FAV"}
                        ),
                    ),
                )[1],
            )
            page.route(
                f"{base_url}/api/executions",
                lambda route: (
                    artifact_calls.__setitem__(
                        "execution", artifact_calls["execution"] + 1
                    ),
                    route.fulfill(
                        status=201,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "taskId": "mm:workflow-123",
                                "workflowId": "mm:workflow-123",
                                "runId": "run-123",
                                "temporalRunId": "run-123",
                                "namespace": "moonmind",
                                "workflowType": "MoonMind.Run",
                                "state": "initializing",
                                "dashboardStatus": "queued",
                                "temporalStatus": "running",
                                "memo": {
                                    "title": "Temporal task",
                                    "summary": "Execution initialized.",
                                },
                                "redirectPath": "/tasks/mm:workflow-123?source=temporal",
                            }
                        ),
                    ),
                )[1],
            )
            page.route(
                f"{base_url}/api/artifacts/art_01ARZ3NDEKTSV4RRFFQ69G5FAV/links",
                lambda route: (
                    artifact_calls.__setitem__("link", artifact_calls["link"] + 1),
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(
                            {"artifact_id": "art_01ARZ3NDEKTSV4RRFFQ69G5FAV"}
                        ),
                    ),
                )[1],
            )
            page.route(
                f"{base_url}/api/executions/mm:workflow-123",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "taskId": "mm:workflow-123",
                            "workflowId": "mm:workflow-123",
                            "runId": "run-123",
                            "temporalRunId": "run-123",
                            "namespace": "moonmind",
                            "workflowType": "MoonMind.Run",
                            "state": "initializing",
                            "dashboardStatus": "queued",
                            "temporalStatus": "running",
                            "memo": {
                                "title": "Temporal task",
                                "summary": "Execution initialized.",
                            },
                        }
                    ),
                ),
            )
            page.route(
                f"{base_url}/api/executions/moonmind/mm:workflow-123/run-123/artifacts",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"artifacts": []}),
                ),
            )

            page.goto(f"{base_url}/tasks/create")
            page.wait_for_selector("#queue-submit-form")
            runtime_options = page.locator('select[name="runtime"] option')
            assert "temporal" not in [
                runtime_options.nth(i).get_attribute("value")
                for i in range(runtime_options.count())
            ]
            page.fill(
                'textarea[data-step-field="instructions"][data-step-index="0"]',
                "Implement Temporal submit redirect coverage. " * 200,
            )
            page.fill('input[name="repository"]', "MoonLadderStudios/MoonMind")
            page.locator("#queue-submit-form button[type='submit']").click()
            page.wait_for_url("**/tasks/mm:workflow-123?source=temporal")
            assert artifact_calls == {
                "create": 1,
                "upload": 1,
                "link": 1,
                "execution": 1,
            }
            browser.close()
    finally:
        settings.temporal_dashboard.submit_enabled = original_submit_enabled
