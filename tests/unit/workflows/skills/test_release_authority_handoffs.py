import json
from unittest.mock import AsyncMock

import pytest

from moonmind.release_identity import build_release
from moonmind.workflows.skills import deployment_release as release
from moonmind.workflows.skills.deployment_surface import operator_urls


@pytest.mark.parametrize(
    "name",
    [
        "pyproject.toml",
        "poetry.lock",
        "package.json",
        "package-lock.json",
        "api_service/Dockerfile",
    ],
)
def test_release_identity_changes_with_semantic_build_input(tmp_path, name):
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("before")
    before = build_release(tmp_path, "source-revision")
    target.write_text("after")
    assert build_release(tmp_path, "source-revision")["digest"] != before["digest"]


def test_source_revision_distinguishes_external_build_inputs(tmp_path):
    (tmp_path / "poetry.lock").write_text("unchanged")
    assert (
        build_release(tmp_path, "one")["digest"]
        != build_release(tmp_path, "two")["digest"]
    )


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", "http://127.0.0.1:7000"),
        ("192.0.2.4", "http://192.0.2.4:7000"),
        ("::1", "http://[::1]:7000"),
    ],
)
def test_operator_origin_comes_from_preserved_fixed_binding(host, expected):
    config = {
        "services": {
            "api": {"ports": [{"host_ip": host, "published": "7000", "target": 8000}]}
        }
    }
    assert operator_urls(config) == [expected]
    config["services"]["api"]["environment"] = {"MOONMIND_PUBLIC_BASE_URL": ""}
    assert operator_urls(config) == [expected]
    config["services"]["api"]["environment"][
        "MOONMIND_PUBLIC_BASE_URL"
    ] = "https://moonmind.example.invalid"
    assert operator_urls(config) == ["https://moonmind.example.invalid"]


@pytest.mark.parametrize("host", ["", "0.0.0.0", "::"])
def test_wildcard_never_invents_an_operator_host(host):
    with pytest.raises(ValueError, match="operator URL"):
        operator_urls(
            {
                "services": {
                    "api": {
                        "ports": [
                            {"host_ip": host, "published": "7000", "target": 8000}
                        ]
                    }
                }
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "platform,network,transport",
    [
        ("Ubuntu 24.04", "host", "host-network"),
        ("Docker Desktop", "bridge", "docker-host-gateway"),
    ],
)
async def test_operator_probe_uses_host_namespace_and_verifies_receipt(
    tmp_path, monkeypatch, platform, network, transport
):
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(tmp_path / "desired.json")
    )
    url = "http://127.0.0.1:7000"
    (tmp_path / "operator-http-headers.json").write_text(
        json.dumps({url: {"Authorization": "Bearer explicitly-supplied-test-session"}})
    )
    probe = AsyncMock(
        side_effect=[
            platform,
            json.dumps(
                {
                    "status": "verified",
                    "baseUrl": url,
                    "checks": ["healthz", "dashboard", "assets", "api/ui/info"],
                    "transport": transport,
                }
            ),
        ]
    )
    monkeypatch.setattr(release, "docker", probe)
    result = await release.verify_operator_access(
        "example/image@sha256:pinned", [url], "owner"
    )
    assert result["status"] == "verified"
    command = probe.call_args.args
    assert command[command.index("--network") + 1] == network
    assert url in command
    assert ("--docker-host-gateway" in command) is (network == "bridge")
    assert "example/image@sha256:pinned" in command
    assert "explicitly-supplied-test-session" not in " ".join(command)
    assert json.loads(probe.call_args.kwargs["input_bytes"]) == {
        "Authorization": "Bearer explicitly-supplied-test-session"
    }
    probe.side_effect = [platform, json.dumps({"status": "unknown", "baseUrl": url})]
    with pytest.raises(RuntimeError, match="terminal evidence"):
        await release.verify_operator_access(
            "example/image@sha256:pinned", [url], "owner"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["0.0.0.0", "127.0.0.1"])
async def test_release_cannot_replace_api_before_operator_path_is_verified(
    tmp_path, monkeypatch, host
):
    from moonmind.workflows.skills.deployment_execution import (
        DeploymentUpdateExecutor,
        DeploymentUpdateLockManager,
        HostDockerComposeRunner,
        InMemoryDesiredStateStore,
        InMemoryEvidenceWriter,
    )
    from moonmind.workflows.temporal import worker_runtime
    from moonmind import release_identity

    runner = HostDockerComposeRunner(project_dir=str(tmp_path))
    executor = DeploymentUpdateExecutor(
        DeploymentUpdateLockManager(),
        InMemoryDesiredStateStore(),
        InMemoryEvidenceWriter(),
        runner,
    )
    monkeypatch.setattr(
        worker_runtime, "_build_deployment_update_executor", lambda: executor
    )
    monkeypatch.setattr(
        release_identity, "installed_release", lambda: {"sourceRevision": "source"}
    )
    render = {
        "services": {
            "api": {"ports": [{"host_ip": host, "published": "7000", "target": 8000}]}
        }
    }
    monkeypatch.setattr(
        HostDockerComposeRunner,
        "_run_compose_command",
        AsyncMock(return_value={"exitCode": 0, "stdout": json.dumps(render)}),
    )
    execute = AsyncMock()
    monkeypatch.setattr(DeploymentUpdateExecutor, "execute", execute)
    monkeypatch.setattr(
        release,
        "docker",
        AsyncMock(side_effect=RuntimeError("operator route unreachable")),
    )
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "authored": {
                    "owner": "owner",
                    "context": {},
                    "inputs": {"sourceRevision": "source"},
                },
                "image": "example/image@sha256:pinned",
            }
        )
    )
    with pytest.raises((ValueError, RuntimeError)):
        await release._run_job_body(request)
    execute.assert_not_awaited()
    assert not (tmp_path / "deployment-result.json").exists()
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize(
    "host,endpoint",
    [
        ("127.0.0.1", "host.docker.internal"),
        ("localhost", "host.docker.internal"),
        ("moonmind.example", "moonmind.example"),
    ],
)
def test_gateway_preserves_http_host_and_verified_tls_identity(
    monkeypatch, host, endpoint
):
    from unittest.mock import MagicMock
    from moonmind.workflows.skills import deployment_surface as surface

    connection = MagicMock()
    connection.getresponse.return_value.status = 200
    connection.getresponse.return_value.read.return_value = b"{}"
    create_socket = MagicMock()
    context = MagicMock()
    monkeypatch.setattr(
        surface.http.client, "HTTPConnection", lambda *args, **kwargs: connection
    )
    monkeypatch.setattr(surface.socket, "create_connection", create_socket)
    monkeypatch.setattr(surface.ssl, "create_default_context", lambda: context)
    assert surface.fetch_surface(
        f"https://{host}:7443/healthz?probe=1", docker_host_gateway=True
    ) == (200, b"{}")
    assert connection.request.call_args.args[1] == "/healthz?probe=1"
    create_socket.assert_called_once_with((endpoint, 7443), timeout=30)
    assert context.minimum_version == surface.ssl.TLSVersion.TLSv1_2
    context.wrap_socket.assert_called_once_with(
        create_socket.return_value, server_hostname=host
    )
    assert connection.request.call_args.kwargs["headers"]["Host"] == f"{host}:7443"


def test_surface_receipt_stdout_is_pure_json_with_real_http_server(capsys):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from moonmind.workflows.skills.deployment_surface import verify_surface

    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append((self.path, self.headers["Host"]))
            response = {
                "/healthz": b"{}",
                "/workflows": b'<script src="/assets/app-test.js"></script>',
                "/assets/app-test.js": b"void 0",
                "/api/ui/info": b'{"buildId":"fixture"}',
            }[self.path]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            receipt = verify_surface(url)
        finally:
            server.shutdown()
            thread.join()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Server build id: fixture" in captured.err
    assert receipt["baseUrl"] == url
    assert {path for path, _ in paths} == {
        "/healthz",
        "/workflows",
        "/assets/app-test.js",
        "/api/ui/info",
    }
    assert all(host == url.removeprefix("http://") for _, host in paths)


@pytest.mark.asyncio
async def test_verified_primary_resume_does_not_repeat_operator_admission(
    tmp_path, monkeypatch
):
    from moonmind.workflows.skills.deployment_execution import (
        DeploymentUpdateExecutor,
        DeploymentUpdateLockManager,
        HostDockerComposeRunner,
        InMemoryDesiredStateStore,
        InMemoryEvidenceWriter,
    )
    from moonmind.workflows.temporal import worker_runtime
    from moonmind import release_identity

    executor = DeploymentUpdateExecutor(
        DeploymentUpdateLockManager(),
        InMemoryDesiredStateStore(),
        InMemoryEvidenceWriter(),
        HostDockerComposeRunner(project_dir=str(tmp_path)),
    )
    monkeypatch.setattr(
        worker_runtime, "_build_deployment_update_executor", lambda: executor
    )
    monkeypatch.setattr(
        release_identity, "installed_release", lambda: {"sourceRevision": "source"}
    )
    preflight = AsyncMock(side_effect=RuntimeError("operator route now unavailable"))
    monkeypatch.setattr(release, "prepare_operator_access", preflight)
    cleanup = AsyncMock()
    monkeypatch.setattr(release.ReleaseCohort, "cleanup", cleanup)
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "authored": {
                    "owner": "owner",
                    "context": {},
                    "inputs": {"sourceRevision": "source"},
                },
                "image": "example/image@sha256:pinned",
            }
        )
    )
    primary = {
        "owner": "owner",
        "result": {
            "status": "COMPLETED",
            "outputs": {"releaseReadinessArtifactRef": "artifact://verified"},
            "progress": {},
        },
    }
    (tmp_path / "deployment-result.json").write_text(json.dumps(primary))
    await release._run_job_body(request)
    preflight.assert_not_awaited()
    cleanup.assert_awaited_once()
    assert json.loads((tmp_path / "result.json").read_text()) == primary


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "other"},
        {"X-Forwarded-User": "admin"},
        {"Cookie": "bad\r\nHost: other"},
        {"Authorization": "one", "authorization": "two"},
    ],
)
def test_operator_credentials_cannot_change_request_authority(headers):
    from moonmind.workflows.skills.deployment_surface import validate_headers

    with pytest.raises(ValueError, match="authentication header"):
        validate_headers(headers)


@pytest.mark.parametrize(
    "destination,expected",
    [
        ("https://operator.test/asset.js", True),
        ("https://external.test/asset.js", False),
        ("http://operator.test/asset.js", False),
        ("https://operator.test:444/asset.js", False),
    ],
)
def test_operator_credentials_never_cross_origin(monkeypatch, destination, expected):
    from unittest.mock import MagicMock
    from moonmind.workflows.skills import deployment_surface as surface

    connection = MagicMock()
    monkeypatch.setattr(
        surface.http.client, "HTTPConnection", lambda *args, **kwargs: connection
    )
    monkeypatch.setattr(surface.socket, "create_connection", MagicMock())
    monkeypatch.setattr(surface.ssl, "create_default_context", MagicMock())
    surface.fetch_surface(
        destination,
        headers={"Cookie": "authorized-test-session"},
        credential_origin="https://operator.test",
    )
    assert (
        connection.request.call_args.kwargs["headers"].get("Cookie")
        == "authorized-test-session"
    ) is expected


@pytest.mark.parametrize(
    "identity",
    [
        {},
        {"status": "healthy", "startupDigest": "old", "currentDigest": "old"},
        {"status": "unknown", "startupDigest": "selected", "currentDigest": "selected"},
        {"status": "healthy", "startupDigest": "selected", "currentDigest": "selected"},
    ],
)
def test_operator_origin_must_serve_selected_release(monkeypatch, identity):
    from moonmind.workflows.skills import deployment_surface as surface
    from tools import verify_deployed_ui_assets as assets

    monkeypatch.setattr(
        surface,
        "fetch_surface",
        lambda *args, **kwargs: (
            200,
            json.dumps({"workerCodeFreshness": {"api": identity}}).encode(),
        ),
    )
    monkeypatch.setattr(assets, "verify_deployed_ui_assets", lambda *args, **kwargs: [])
    if (
        identity.get("status") == "healthy"
        and identity.get("currentDigest") == "selected"
    ):
        assert (
            surface.verify_surface("http://operator.test", expected_release="selected")[
                "releaseDigest"
            ]
            == "selected"
        )
    else:
        with pytest.raises(RuntimeError, match="selected immutable release"):
            surface.verify_surface("http://operator.test", expected_release="selected")
