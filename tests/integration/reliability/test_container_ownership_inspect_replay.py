"""Exercise the real Docker CLI against a hermetic restricted-proxy replay."""

from __future__ import annotations

import json
import re
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from temporalio.exceptions import ApplicationError

from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from tests.integration.reliability.helpers import load_replay

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci, pytest.mark.asyncio]


@pytest.fixture
def proxy_replay(tmp_path, monkeypatch):
    replay = load_replay("container-ownership-inspect", "manifest.json")
    state = {"container_status": 404, "paths": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_HEAD(self):
            assert self.path == "/_ping"
            self.send_response(200)
            self.send_header("API-Version", "1.45")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            path = re.sub(r"^/v[0-9.]+", "", self.path)
            state["paths"].append(path)
            if path.startswith("/containers/"):
                status = state["container_status"]
                name = path.split("/")[2]
                body = json.dumps(
                    {
                        "message": replay["missingContainerMessage"].format(name=name)
                        if status == 404
                        else "ownership service unavailable"
                    }
                ).encode()
            elif path.startswith(("/images/", "/networks/", "/volumes/")):
                status = replay["missingObjectStatus"]
                body = b'{"message":"No such object"}'
            elif path == "/info":
                status = 200
                body = b'{"Swarm":{"LocalNodeState":"inactive"}}'
            else:
                status = replay["forbiddenStatus"]
                body = replay["forbiddenBody"].encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    # The production test image includes Docker CLI; no socket or daemon is
    # needed. A missing CLI is a test-environment failure, never a silent skip.
    assert shutil.which("docker"), "Docker CLI is required for the proxy replay"
    for key in (
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        "MOONMIND_DOCKER_ACTIVATION_COMMAND",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "docker-config"))
    monkeypatch.setenv("DOCKER_API_VERSION", "1.45")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        docker_host=f"tcp://127.0.0.1:{server.server_port}",
    )
    payload = {
        "jobId": replay["containerJobId"],
        "ownershipToken": replay["containerJobId"] + ":v1",
        "request": {
            "idempotencyKey": "ownership-inspect-replay",
            "source": {
                "source": "workflow",
                "workflowId": replay["incidentWorkflowId"],
            },
            "spec": {
                "image": "python:3.13",
                "workspaceRef": {"kind": "sandbox", "workspaceId": "workspace"},
                "command": ["python", "-V"],
                "resources": {"cpuMillis": 1000, "memoryMiB": 512},
            },
        },
    }
    try:
        yield (
            TemporalAgentRuntimeActivities(container_job_backend=backend),
            payload,
            state,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "operation", ["reconcile_container", "stop_container", "remove_container"]
)
async def test_missing_container_crosses_real_cli_without_probing_plugins(
    proxy_replay, operation
):
    runtime, payload, state = proxy_replay
    result = await getattr(runtime, f"container_job_{operation}")(payload)
    if operation == "reconcile_container":
        assert "containerRef" not in result
    if operation != "remove_container":
        assert result["running"] is False
    assert len(state["paths"]) == 1
    assert state["paths"][0].startswith("/containers/")


@pytest.mark.parametrize("status", [403, 500])
@pytest.mark.parametrize(
    "operation", ["reconcile_container", "stop_container", "remove_container"]
)
async def test_unknown_ownership_still_fails_closed(proxy_replay, operation, status):
    runtime, payload, state = proxy_replay
    state["container_status"] = status
    with pytest.raises(ApplicationError) as raised:
        await getattr(runtime, f"container_job_{operation}")(payload)
    assert raised.value.type == "infrastructure"
    assert "ownership could not be read" in str(raised.value)
    assert len(state["paths"]) == 1
    assert state["paths"][0].startswith("/containers/")
