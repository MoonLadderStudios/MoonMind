import asyncio
from copy import deepcopy
from pathlib import Path
import json
import os
import socket
import threading
import time
import uuid
from contextlib import closing

import pytest
import uvicorn

from tests.helpers.route_dependencies import _resolve_user_dependency_overrides
from api_service.db import base as db_base
from api_service.db.base import get_async_session
from api_service.db.models import Base
from api_service.main import app as main_app
from moonmind.config.settings import settings
from tests.helpers.account_source import User

if not os.getenv("RUN_E2E_TESTS"):
    pytest.skip("E2E tests disabled", allow_module_level=True)
else:
    from playwright.sync_api import expect, sync_playwright

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
    original_session_maker = db_base.async_session_maker

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    for dependency in _resolve_user_dependency_overrides():
        main_app.dependency_overrides[dependency] = (
            lambda test_user=test_user: test_user
        )

    in_memory_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False
    )
    in_memory_session_maker = async_sessionmaker(
        bind=in_memory_engine,
        expire_on_commit=False,
    )
    
    async def _init_db():
        async with in_memory_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            
    try:
        asyncio.run(_init_db())
    except Exception as e:
        pytest.fail(f"Schema init failed: {e}")

    main_app.dependency_overrides[get_async_session] = lambda: in_memory_session_maker()
    db_base.async_session_maker = in_memory_session_maker

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
        thread.join(timeout=5)
        assert not thread.is_alive(), "Browser fixture HTTP server failed to stop"
        db_base.async_session_maker = original_session_maker
        main_app.dependency_overrides = original_overrides
        asyncio.run(in_memory_engine.dispose())

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
                        "claude_code": {"models": [], "efforts": []},
                        "claude": {"models": [], "efforts": []},
                    },
                }
            ),
        )

    def _mock_skills(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"items": {"worker": ["speckit-orchestrate"]}}),
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

    create_requests = []
    def _mock_create(route):
        calls["create"] += 1
        create_requests.append(route.request.post_data_json)
        submit = page.locator("#queue-submit-form button[type='submit']")
        expect(submit).to_have_attribute("aria-busy", "true")
        calls["pendingState"] = {
            "label": _read_submit_label(page),
            "disabled": submit.is_disabled(),
        }
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

    def _mock_presets(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"items": []}),
        )

    fixture = json.loads(
        (Path(__file__).resolve().parents[2]
         / "frontend/src/runtime/fixtures/profile-first-authoring.json").read_text()
    )
    profiles = [fixture["provider"]]
    configurations = [fixture["configuration"]]
    for suffix, label in (("default", "Codex Default"), ("secondary", "Codex Secondary")):
        provider = deepcopy(fixture["provider"])
        provider.update(
            profile_id=f"profile:codex-{suffix}", runtime_id="codex_cli",
            provider_id="openai", account_label=label, is_default=False,
            default_model=None,
        )
        selection = provider["execution_selection"]
        selection.update(
            profileId=f"configuration-codex-{suffix}",
            providerProfileRef=provider["profile_id"], harnessId="codex-native",
        )
        configuration = deepcopy(fixture["configuration"])
        configuration.update(profileId=selection["profileId"], displayName=label)
        document = configuration["versions"][0]["document"]
        document["harness"]["id"] = "codex-native"
        document["credentialSlots"][0]["acceptedProviderIds"] = ["openai"]
        profiles.append(provider)
        configurations.append(configuration)

    def _mock_provider_profiles(route):
        route.fulfill(status=200, json=profiles)

    # Every API dependency is explicit; an unknown fixture cannot reach a real
    # workflow, provider, artifact store or external integration through the app.
    page.route(f"{base_url}/api/**", lambda route: route.fulfill(
        status=404, json={"detail": "Unconfigured browser test API"},
    ))
    # The real shell configuration reads only this server's fixture database.
    page.route(f"{base_url}/api/ui/info*", lambda route: route.continue_())
    page.route(f"{base_url}/api/omnigent/agent-profiles", lambda route: route.fulfill(
        status=200, json=configurations,
    ))
    page.route(f"{base_url}/api/omnigent/execution-readiness", lambda route: route.fulfill(
        status=200, json={"executionTargets": []},
    ))
    page.route(f"{base_url}/api/omnigent/codex-catalog-readiness", lambda route: route.fulfill(
        status=200, json={
            "available": True, "executionProfiles": [],
            "eligibleProviderProfiles": [], "ineligibleProviderProfiles": [],
            "gateReasons": [],
        },
    ))

    page.route(
        f"{base_url}/api/workers/runtime-capabilities", _mock_runtime_capabilities
    )
    page.route(f"{base_url}/api/workflows/skills", _mock_skills)
    page.route(f"{base_url}/api/system/worker-pause", _mock_worker_pause)
    page.route(f"{base_url}/api/presets*", _mock_presets)
    page.route(f"{base_url}/api/v1/provider-profiles*", _mock_provider_profiles)
    page.route(f"{base_url}/api/executions", _mock_create)
    page.route(f"{base_url}/api/executions/*", _mock_detail)
    return calls, create_requests

def _read_submit_label(page):
    submit = page.locator("#queue-submit-form button[type='submit']")
    return (submit.get_attribute("aria-label") or submit.text_content() or "").strip()

def test_submit_create_flows_to_task_detail(server):
    base_url = server
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHROMIUM_CHANNEL") or None)
        page = browser.new_page()
        workflow_id = "mm:workflow-123"
        calls, create_requests = _route_handlers(
            page,
            base_url=base_url,
            create_status=201,
            create_body=json.dumps(
                {
                    "workflowId": workflow_id,
                    "runId": "run-123",
                    "namespace": "moonmind",
                    "redirectPath": f"/workflows/{workflow_id}?source=temporal",
                }
            ),
            create_delay_seconds=0.25,
        )

        page.goto(f"{base_url}/workflows/new")
        page.wait_for_selector("#queue-submit-form")
        page.fill(
            'textarea[data-step-field="instructions"][data-step-index="0"]',
            "Run end-to-end regression flow.",
        )
        page.fill('input[name="repository"]', "MoonLadderStudios/MoonMind")
        page.fill(
            'input[data-step-field="skillId"][data-step-index="0"]',
            "speckit-orchestrate",
        )

        submit_button = page.locator("#queue-submit-form button[type='submit']")
        original_label = _read_submit_label(page)
        assert original_label == "Start Workflow"

        submit_button.click()

        page.wait_for_url(f"**/workflows/{workflow_id}?source=temporal")
        assert page.url.endswith(f"/workflows/{workflow_id}?source=temporal")
        assert calls["create"] == 1
        assert calls["pendingState"] == {"label": original_label, "disabled": True}
        
        create_payload = create_requests[0]
        assert create_payload["payload"]["task"]["tool"] == {
            "type": "skill",
            "name": "speckit-orchestrate",
        }

        browser.close()

def test_submit_error_restores_label(server):
    base_url = server
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHROMIUM_CHANNEL") or None)
        page = browser.new_page()
        calls, _ = _route_handlers(
            page,
            base_url=base_url,
            create_status=500,
            create_body=json.dumps({"detail": {"message": "service unavailable"}}),
            create_delay_seconds=0.1,
        )

        page.goto(f"{base_url}/workflows/new")
        page.wait_for_selector("#queue-submit-form")
        page.fill(
            'textarea[data-step-field="instructions"][data-step-index="0"]',
            "Run end-to-end regression flow.",
        )
        page.fill('input[name="repository"]', "MoonLadderStudios/MoonMind")

        submit_button = page.locator("#queue-submit-form button[type='submit']")
        original_label = _read_submit_label(page)
        assert original_label == "Start Workflow"

        submit_button.click()
        expect(submit_button).to_have_attribute("aria-busy", "false")
        expect(submit_button).to_be_enabled()
        assert _read_submit_label(page) == original_label
        assert page.url.endswith("/workflows/new")
        assert calls["create"] == 1
        assert calls["pendingState"] == {"label": original_label, "disabled": True}
        browser.close()

def test_temporal_detail_resolves_source_and_fetches_latest_run_artifacts(server):
    base_url = server
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHROMIUM_CHANNEL") or None)
        page = browser.new_page()
        _route_handlers(
            page,
            base_url=base_url,
            create_status=201,
            create_body=json.dumps(
                {
                    "workflowId": "mm:unused",
                    "runId": "run-unused",
                    "namespace": "moonmind",
                    "redirectPath": "/workflows/mm:unused?source=temporal",
                }
            ),
        )
        ordered_calls = []

        page.route(
            f"{base_url}/api/executions/mm%3Aworkflow-123*",
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
                            "workflowType": "MoonMind.UserWorkflow",
                            "source": "temporal",
                            "title": "Temporal task",
                            "summary": "Waiting on approval.",
                            "status": "running",
                            "createdAt": "2026-03-06T10:00:00Z",
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
            f"{base_url}/api/executions/moonmind/mm%3Aworkflow-123/run-123/artifacts",
            lambda route: (
                ordered_calls.append("artifacts"),
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"artifacts": []}),
                ),
            )[1],
        )

        page.goto(f"{base_url}/workflows/mm:workflow-123")
        page.get_by_role("link", name="Evidence", exact=True).click()
        expect(page.get_by_role("heading", name="Workflow Artifacts", exact=True)).to_be_visible()
        assert page.url.split("?", 1)[0].endswith("/workflows/mm%3Aworkflow-123/evidence")
        assert ordered_calls[:2] == ["detail", "artifacts"]
        browser.close()

def test_temporal_detail_shows_edit_button_when_local_editing_enabled(server):
    base_url = server
    previous_enabled = settings.temporal_dashboard.temporal_workflow_editing_enabled
    previous_actions_enabled = settings.temporal_dashboard.actions_enabled
    settings.temporal_dashboard.temporal_workflow_editing_enabled = True
    settings.temporal_dashboard.actions_enabled = True
    try:
        with sync_playwright() as p:
            with p.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHROMIUM_CHANNEL") or None) as browser:
                page = browser.new_page()
                _route_handlers(
                    page, base_url=base_url, create_status=201,
                    create_body=json.dumps({"workflowId": "mm:unused"}),
                )

                def _mock_editable_detail(route):
                    url = route.request.url
                    if url.endswith("/artifacts"):
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps({"artifacts": []}),
                        )
                        return
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "taskId": "mm:editable",
                                "workflowId": "mm:editable",
                                "runId": "run-editable",
                                "temporalRunId": "run-editable",
                                "namespace": "moonmind",
                                "workflowType": "MoonMind.UserWorkflow",
                                "source": "temporal",
                                "title": "Editable Temporal task",
                                "summary": "Ready for edit.",
                                "status": "running",
                                "state": "executing",
                                "rawState": "executing",
                                "temporalStatus": "running",
                                "createdAt": "2026-04-14T10:00:00Z",
                                "updatedAt": "2026-04-14T10:01:00Z",
                                "actions": {
                                    "canSetTitle": True,
                                    "canUpdateInputs": True,
                                    "canRerun": False,
                                },
                            }
                        ),
                    )

                page.route(
                    f"{base_url}/api/executions/mm%3Aeditable*",
                    _mock_editable_detail,
                )
                page.route(
                    f"{base_url}/api/executions/moonmind/mm%3Aeditable/run-editable/artifacts",
                    _mock_editable_detail,
                )

                page.goto(f"{base_url}/workflows/mm:editable?source=temporal")
                page.wait_for_selector("text=Editable Temporal task")

                page.get_by_role("button", name="Workflow actions", exact=True).click()
                edit_link = page.get_by_role("menuitem", name="Edit", exact=True)
                expect(edit_link).to_be_visible()
                assert (
                    edit_link.get_attribute("href")
                    == "/workflows/new?editExecutionId=mm%3Aeditable"
                )
    finally:
        settings.temporal_dashboard.temporal_workflow_editing_enabled = previous_enabled
        settings.temporal_dashboard.actions_enabled = previous_actions_enabled

def test_create_page_shows_provider_profiles_for_selected_harness(server):
    base_url = server
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHROMIUM_CHANNEL") or None)
        page = browser.new_page()
        _route_handlers(
            page, base_url=base_url, create_status=201,
            create_body=json.dumps({"workflowId": "mm:unused"}),
        )
        page.goto(f"{base_url}/workflows/new")
        provider_select = page.get_by_role("combobox", name="Provider Profile", exact=True)
        expect(provider_select).to_have_value("opencode-go")
        expect(provider_select.locator("option")).to_have_count(1)
        expect(provider_select.locator("option")).to_contain_text("OpenCode Go")

        page.get_by_role("combobox", name="Harness", exact=True).select_option("codex_cli")
        expect(provider_select.locator("option[value^='profile:codex-']")).to_have_count(2)
        expect(provider_select.locator("option[value='opencode-go']")).to_have_count(0)
        provider_select.select_option("profile:codex-secondary")
        expect(provider_select).to_have_value("profile:codex-secondary")
        page.get_by_role("combobox", name="Harness", exact=True).select_option("opencode")
        provider_select.select_option("opencode-go")
        expect(provider_select).to_have_value("opencode-go")
        browser.close()

def test_create_page_shows_runtime_command_previews(server):
    base_url = server
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHROMIUM_CHANNEL") or None)
        page = browser.new_page()
        _route_handlers(
            page,
            base_url=base_url,
            create_status=201,
            create_body=json.dumps(
                {
                    "workflowId": "mm:unused",
                    "runId": "run-unused",
                    "namespace": "moonmind",
                    "redirectPath": "/workflows/mm:unused?source=temporal",
                }
            ),
        )

        page.goto(f"{base_url}/workflows/new")
        page.wait_for_selector("#queue-submit-form")
        page.get_by_role("combobox", name="Harness", exact=True).select_option("codex_cli")
        page.get_by_role("combobox", name="Provider Profile", exact=True).select_option("profile:codex-default")
        page.get_by_label("Advanced mode", exact=True).click()
        page.get_by_role("combobox", name="Backend", exact=True).select_option("codex_cli")
        instructions = page.locator(
            'textarea[data-step-field="instructions"][data-step-index="0"]'
        )
        instructions.fill("/review\nCheck this branch.")
        page.wait_for_selector("text=Runtime command: /review")
        assert instructions.input_value() == "/review\nCheck this branch."

        instructions.fill("/foo\nUse provider behavior.")
        page.wait_for_selector("text=Runtime command: /foo")
        page.wait_for_selector("text=Pass-through runtime command.")

        page.get_by_role("combobox", name="Backend", exact=True).select_option("omnigent")
        page.wait_for_selector("text=Unsupported runtime command: /foo")
        assert instructions.input_value() == "/foo\nUse provider behavior."

        instructions.fill("\\/review\nTreat as text.")
        page.wait_for_selector("text=Literal text: /review")
        assert page.locator("text=Runtime command: /review").count() == 0

        browser.close()

def test_temporal_submit_redirects_without_exposing_runtime_picker(server):
    base_url = server
    original_submit_enabled = settings.temporal_dashboard.submit_enabled
    settings.temporal_dashboard.submit_enabled = True
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHROMIUM_CHANNEL") or None)
            page = browser.new_page()
            _route_handlers(
                page,
                base_url=base_url,
                create_status=201,
                create_body=json.dumps(
                    {
                        "workflowId": "mm:unused",
                        "runId": "run-unused",
                        "namespace": "moonmind",
                        "redirectPath": "/workflows/mm:unused?source=temporal",
                    }
                ),
            )
            artifact_calls = {"create": 0, "upload": 0, "complete": 0, "link": 0, "execution": 0}

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
                                    "mode": "single_put",
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
                f"{base_url}/api/artifacts/art_01ARZ3NDEKTSV4RRFFQ69G5FAV/complete",
                lambda route: (
                    artifact_calls.__setitem__("complete", artifact_calls["complete"] + 1),
                    route.fulfill(status=200, json={
                        "artifact_id": "art_01ARZ3NDEKTSV4RRFFQ69G5FAV",
                    }),
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
                                "workflowType": "MoonMind.UserWorkflow",
                                "source": "temporal",
                                "title": "Temporal task",
                                "summary": "Execution initialized.",
                                "status": "running",
                                "createdAt": "2026-03-06T10:00:00Z",
                                "state": "initializing",
                                "dashboardStatus": "queued",
                                "temporalStatus": "running",
                                "memo": {
                                    "title": "Temporal task",
                                    "summary": "Execution initialized.",
                                },
                                "redirectPath": "/workflows/mm:workflow-123?source=temporal",
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
                f"{base_url}/api/executions/mm%3Aworkflow-123",
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
                            "workflowType": "MoonMind.UserWorkflow",
                            "source": "temporal",
                            "title": "Temporal task",
                            "summary": "Execution initialized.",
                            "status": "running",
                            "createdAt": "2026-03-06T10:00:00Z",
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
                f"{base_url}/api/executions/moonmind/mm%3Aworkflow-123/run-123/artifacts",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"artifacts": []}),
                ),
            )

            page.goto(f"{base_url}/workflows/new")
            page.wait_for_selector("#queue-submit-form")
            expect(page.locator('select[name="runtime"]')).to_have_count(0)
            expect(page.get_by_role("combobox", name="Provider Profile", exact=True)).to_be_visible()
            page.fill(
                'textarea[data-step-field="instructions"][data-step-index="0"]',
                "Implement Temporal submit redirect coverage. " * 200,
            )
            page.fill('input[name="repository"]', "MoonLadderStudios/MoonMind")
            page.locator("#queue-submit-form button[type='submit']").click()
            page.wait_for_url("**/workflows/mm:workflow-123?source=temporal")
            assert artifact_calls == {
                "create": 1,
                "upload": 1,
                "complete": 1,
                "link": 1,
                "execution": 1,
            }
            browser.close()
    finally:
        settings.temporal_dashboard.submit_enabled = original_submit_enabled
