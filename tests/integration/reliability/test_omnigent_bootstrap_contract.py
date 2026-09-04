"""Replay startup and cleanup authority boundaries from the failed host launch."""

from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_services.cleanup import DockerOmnigentHostCleanupService
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.host_services.registration import OmnigentHostRegistrationService
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from tests.integration.reliability.helpers import load_replay

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.reliability_journey]


@pytest.mark.parametrize("targeted", [True, False])
@pytest.mark.parametrize("harness", ["opencode-native", "codex-native", "claude-native"])
async def test_exited_host_never_spends_registration_budget(targeted, harness):
    manifest = load_replay("omnigent-bootstrap-image-contract", "manifest.json")
    calls = []

    class Backend:
        async def run(self, argv, **kwargs):
            assert argv[:3] == ["docker", "container", "inspect"]
            calls.append(argv)
            return 0, f"exited|{manifest['hostExitCode']}", ""

    async def handler(request):
        pytest.fail("dead hosts must fail before further provider lookups")

    service = OmnigentHostRegistrationService(
        client=OmnigentHttpClient(base_url="https://omnigent.test", transport=httpx.MockTransport(handler)),
        expected_owner="local", backend=Backend(),
    )
    with pytest.raises(HarnessPlatformError, match="exitCode=1") as error:
        await service.wait_for_registration(
            correlation_name="moonmind-test-host", harness_id=harness,
            expected_host_id="a" * 32 if targeted else None,
        )
    assert str(error.value.code) == "OMNIGENT_HOST_LAUNCH_FAILED"
    assert len(calls) == 1


async def test_real_docker_cli_cleanup_does_not_fall_through_proxy(monkeypatch):
    """Run the actual Docker CLI against a restricted Engine HTTP fixture.

    Untyped inspect probes images/volumes/networks after a container 404 and
    eventually returns 403. No real Docker daemon or credentials are used.
    """
    assert shutil.which("docker"), "required reliability image must contain Docker CLI"
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            paths.append(self.path)
            if self.path.endswith("/_ping"):
                self.send_response(200)
                self.send_header("API-Version", "1.45")
                self.end_headers()
                return
            if "/containers/" in self.path:
                code, message = 404, "No such container: moonmind-test-host"
            elif "/volumes/" in self.path:
                code, message = 404, "no such volume"
            else:
                code, message = 403, "Request forbidden by administrative rules"
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"message": message}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("DOCKER_HOST", f"tcp://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("DOCKER_API_VERSION", "1.45")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.delenv("DOCKER_TLS_VERIFY", raising=False)
    try:
        result = await DockerOmnigentHostCleanupService(DockerCommandBackend()).cleanup(
            container_name="moonmind-test-host", host_lease_ref="lease-replay",
            host_lease_generation=1, state_volume_ref="moonmind-test-state",
            control_volume_ref="moonmind-test-control",
        )
        assert result["containerRemoved"] and result["stateVolumeRemoved"]
        assert not any("/images/" in path or "/networks/" in path for path in paths)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
