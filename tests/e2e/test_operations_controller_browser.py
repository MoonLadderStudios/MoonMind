"""Browser journey: Settings Operations drives the standalone controller.

MoonLadderStudios/MoonMind#4502 (AC-04). The production-built dashboard
(``npm run ui:build``) is served by the real API application; updates go
through the real Operations router to the shipped ``deploy/controller``
server started through its own entrypoint with its production applier.
No workflow engine runs: creating an execution fails the test. The only
stand-in is the ``docker`` CLI the controller shells out to, which answers
the deployment's Compose config and fails every image pull, as an
unpublished tag does, so no container is touched.

Requires RUN_E2E_TESTS=1, Playwright's Chromium, and a built dashboard.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

if not os.getenv("RUN_E2E_TESTS"):
    pytest.skip("E2E tests disabled", allow_module_level=True)

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_MANIFEST = (
    REPO_ROOT
    / "api_service"
    / "static"
    / "workflow_console"
    / "dist"
    / ".vite"
    / "manifest.json"
)
# Serve this checkout's production build, never an image-baked bundle the
# host happens to carry (read when the application module is imported).
os.environ["VITE_MANIFEST_PATH"] = str(DASHBOARD_MANIFEST)

import uvicorn
from playwright.sync_api import expect, sync_playwright

from api_service.api.routers.deployment_operations import (
    _get_temporal_execution_service,
)
from api_service.auth_providers import get_current_user, get_current_user_optional
from api_service.db.base import get_async_session
from api_service.db.models import Base, User
from api_service.main import app as main_app
from moonmind.workflows.skills.deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    DEPLOYMENT_UPDATE_TOOL_VERSION,
)

CONTROLLER_DIR = REPO_ROOT / "deploy" / "controller"
REPOSITORY = "ghcr.io/moonladderstudios/moonmind"
INSTALLED_IMAGE = f"{REPOSITORY}:20260901.0001"
TARGET_REFERENCE = "20260927.4502"
TARGET_IMAGE = f"{REPOSITORY}:{TARGET_REFERENCE}"
HISTORICAL_WORKFLOW_ID = "mm:deployment-update-history"
CONTROLLER_SECRET = "journey-controller-bearer-4502"
REGISTRY_TOKEN = "ghp_journeyRegistryToken4502"

# The `docker` CLI the controller runs. Pulls fail like an unpublished tag;
# the credential in the daemon output must be redacted before display.
DOCKER_SHIM = textwrap.dedent(
    """\
    #!{python}
    import json, sys
    args = sys.argv[1:]
    with open({log!r}, "a", encoding="utf-8") as log:
        log.write(json.dumps(args) + "\\n")
    if args[:1] == ["ps"]:
        sys.exit(0)
    if args[:1] == ["compose"] and "config" in args:
        print(json.dumps({{"name": "moonmind-e2e-4502",
                          "services": {{"api": {{}}, "docker-proxy": {{}}}}}}))
        sys.exit(0)
    if args[:1] == ["compose"] and "pull" in args:
        sys.stderr.write(
            "Error response from daemon: manifest for {target} not found: "
            "manifest unknown (registry token={token})\\n"
        )
        sys.exit(1)
    sys.stderr.write("unexpected docker call: " + " ".join(args) + "\\n")
    sys.exit(2)
    """
)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _NoWorkflowEngine:
    """Application execution index with no workflow engine behind it.

    It still lists one historical workflow-owned update so the page shows
    it read-only; creating an execution would revive the old engine.
    """

    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    async def create_execution(self, **kwargs: object) -> object:
        self.created.append(kwargs)
        raise AssertionError("a controller-owned update must not create a workflow")

    async def list_executions(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    workflow_id=HISTORICAL_WORKFLOW_ID,
                    run_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    owner_id="operator@example.com",
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
                                            "reference": "20260801.0001",
                                        },
                                        "mode": "changed_services",
                                    },
                                }
                            ],
                        }
                    },
                    memo={},
                    artifact_refs=[],
                    started_at="2026-08-01T00:00:00Z",
                    closed_at="2026-08-01T00:04:00Z",
                )
            ]
        )


class _Controller:
    """The shipped controller, installed and started as bootstrap would."""

    def __init__(self) -> None:
        # Short path: AF_UNIX socket paths are length-limited.
        self.root = Path(tempfile.mkdtemp(prefix="mmctl-e2e", dir="/tmp"))
        self.state_dir = self.root / "state"
        self.docker_log = self.root / "docker-calls.jsonl"
        project_dir = self.root / "deployment"
        shim_dir = self.root / "bin"
        for directory in (self.state_dir / "secrets", project_dir, shim_dir):
            directory.mkdir(parents=True)
        (project_dir / "docker-compose.yaml").write_text("services: {}\n")
        secret_file = self.state_dir / "secrets" / "controller-bearer"
        secret_file.write_text(f"{CONTROLLER_SECRET}\n")
        secret_file.chmod(0o600)
        shim = shim_dir / "docker"
        shim.write_text(
            DOCKER_SHIM.format(
                python=sys.executable,
                log=str(self.docker_log),
                target=TARGET_IMAGE,
                token=REGISTRY_TOKEN,
            )
        )
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
        env = {
            **os.environ,
            "PATH": f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "MOONMIND_CONTROLLER_TARGET_DIR": str(project_dir),
        }
        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; from pathlib import Path; import bootstrap; "
                    "bootstrap.record_controller_image(Path(sys.argv[1]), "
                    "requested=sys.argv[2], pinned=sys.argv[2] + '@sha256:' + '0' * 64)"
                ),
                str(self.state_dir),
                "ghcr.io/moonladderstudios/moonmind-controller:latest",
            ],
            cwd=CONTROLLER_DIR,
            check=True,
        )
        self.port = _free_port()
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(CONTROLLER_DIR / "server.py"),
                "--state-dir",
                str(self.state_dir),
                "--port",
                str(self.port),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 30
        while not (self.state_dir / "controller.sock").exists():
            if self.process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"controller did not start: {self.output()}")
            time.sleep(0.1)

    def records(self) -> list[dict]:
        return [
            json.loads(path.read_text())
            for path in sorted((self.state_dir / "operations").glob("*.json"))
        ]

    def docker_calls(self) -> list[list[str]]:
        if not self.docker_log.exists():
            return []
        return [json.loads(line) for line in self.docker_log.read_text().splitlines()]

    def output(self) -> str:
        if self.process.poll() is None:
            return ""
        return self.process.stdout.read() if self.process.stdout else ""

    def close(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        shutil.rmtree(self.root, ignore_errors=True)


@contextmanager
def _page(playwright_obj):
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
def journey():
    if not DASHBOARD_MANIFEST.exists():
        pytest.fail(
            "The production dashboard is not built; run `npm run ui:build` first.",
            pytrace=False,
        )
    controller = _Controller()
    engine_stand_in = _NoWorkflowEngine()
    # The API reads this deployment's controller state and installed image,
    # never the host running the test.
    deployment_env = {
        "MOONMIND_CONTROLLER_STATE_DIR": str(controller.state_dir),
        "MOONMIND_IMAGE": INSTALLED_IMAGE,
        "MOONMIND_CONTROLLER_URL": None,
        "MOONMIND_CONTROLLER_SECRET_FILE": None,
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE": None,
        "MOONMIND_IMAGE_REQUESTED": None,
    }
    previous_env = {key: os.environ.get(key) for key in deployment_env}
    for key, value in deployment_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    database = controller.root / "app.sqlite"
    schema_engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(schema_engine)
    schema_engine.dispose()

    operator = User(
        id=uuid4(), email="operator@example.com", is_active=True, is_superuser=True
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    sessions = async_sessionmaker(bind=engine, expire_on_commit=False)
    # The operator this single-user instance resolves (auth itself is
    # covered elsewhere); every page and API route shares these principals.
    for principal in (get_current_user(), get_current_user_optional()):
        main_app.dependency_overrides[principal] = lambda: operator

    async def session_override():
        async with sessions() as session:
            yield session

    main_app.dependency_overrides[get_async_session] = session_override
    main_app.dependency_overrides[_get_temporal_execution_service] = lambda: (
        engine_stand_in
    )

    # Startup hooks provision host resources (session keys, Temporal
    # clients) owned by the deployment journeys; the routes need none.
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            main_app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("API server did not start")
        time.sleep(0.1)
    try:
        yield SimpleNamespace(
            base=f"http://127.0.0.1:{port}",
            controller=controller,
            engine=engine_stand_in,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        main_app.dependency_overrides.clear()
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        asyncio.run(engine.dispose())
        controller.close()


def test_operations_page_submits_observes_and_retries_one_controller_operation(
    journey,
) -> None:
    controller = journey.controller
    with sync_playwright() as p, _page(p) as page:
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(f"{journey.base}/settings/operations", wait_until="domcontentloaded")

        update = page.get_by_role("region", name="MoonMind update")
        # Observed installed state, before any request.
        expect(update).to_contain_text(INSTALLED_IMAGE, timeout=30_000)
        # The workflow-era update stays readable history: a plain link,
        # no retry, and nothing that submits to the old engine.
        historical = update.locator("div.rounded-2xl", has_text="20260801.0001").last
        expect(historical.get_by_role("link", name="Run detail")).to_have_attribute(
            "href", f"/workflows/{HISTORICAL_WORKFLOW_ID}"
        )
        expect(historical.get_by_role("button", name="Retry update")).to_have_count(0)

        page.get_by_label("Update to").fill(TARGET_REFERENCE)
        confirmations: list[str] = []

        def confirm(dialog) -> None:
            confirmations.append(dialog.message)
            dialog.accept()

        page.once("dialog", confirm)
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/v1/operations/deployment/update")
                and response.request.method == "POST"
            )
        ) as submitted:
            update.get_by_role("button", name="Update MoonMind").click()
        assert TARGET_IMAGE in confirmations[0]
        assert submitted.value.status == 202, submitted.value.text()
        accepted = submitted.value.json()
        operation_id = accepted["operationId"]
        assert accepted["owner"] == "controller"
        assert accepted["workflowId"] is None
        expect(update).to_contain_text(
            f"accepted by the deployment controller: operation {operation_id}"
        )

        # Bounded progress reads reach the controller's terminal failure:
        # requested target, original error, and redacted controller log.
        action = update.locator(
            "div.rounded-2xl", has_text=f"Controller operation {operation_id}"
        ).last
        expect(action).to_contain_text("FAILED", timeout=60_000)
        expect(action).to_contain_text(TARGET_REFERENCE)
        expect(action).to_contain_text("attempt 1: staging failed: pull failed")
        expect(action).to_contain_text("manifest unknown")
        action.get_by_text("Controller log").click()
        expect(action.locator("pre")).to_contain_text("attempt 3: staging failed")
        assert REGISTRY_TOKEN not in page.content()
        assert CONTROLLER_SECRET not in page.content()

        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/controller-operations/{operation_id}/retry")
                and response.request.method == "POST"
            )
        ) as retried:
            action.get_by_role("button", name="Retry update").click()
        assert retried.value.status == 202, retried.value.text()
        expect(update).to_contain_text(
            f"Retry requested for controller operation {operation_id}"
        )
        # The fresh bounded attempt ran and the first failure is kept.
        expect(action).to_contain_text("latest attempt 6", timeout=60_000)
        expect(action).to_contain_text("attempt 1: staging failed")

        # A browser reload reconnects to the same operation without
        # launching another updater.
        page.reload(wait_until="domcontentloaded")
        reloaded = (
            page.get_by_role("region", name="MoonMind update")
            .locator("div.rounded-2xl", has_text=f"Controller operation {operation_id}")
            .last
        )
        expect(reloaded).to_contain_text("latest attempt 6", timeout=30_000)
        expect(reloaded.get_by_role("button", name="Retry update")).to_be_visible()
        assert page_errors == []

    [record] = controller.records()
    assert record["operationId"] == operation_id
    assert record["desired"]["image"] == TARGET_IMAGE
    assert record["status"] == "failed"
    assert [attempt["attempt"] for attempt in record["attempts"]] == [1, 2, 3, 4, 5, 6]
    calls = controller.docker_calls()
    assert sum("pull" in call for call in calls) == 6
    # A failed pull leaves the running deployment untouched.
    assert not any("up" in call for call in calls)
    assert journey.engine.created == []
