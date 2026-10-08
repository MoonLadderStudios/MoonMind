"""Fail-closed evidence and isolation for the hosted, provider-free recovery row."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from tools.ci import run_credential_free_host_recovery as driver

SHA = "1" * 40
IMAGE_ID = "sha256:" + "2" * 64
HOST_REF = "127.0.0.1:5000/moonmind-test-host@" + IMAGE_ID


def receipt():
    before = {
        "containerId": "a" * 64,
        "hostImageId": IMAGE_ID,
        "stateVolume": "mm-omnigent-state-fixture",
        "hostId": "fixture-host",
        "sessionId": "fixture-session",
        "bridgeId": "fixture-bridge",
        "runnerId": "old-runner",
        "messageItemIds": [],
    }
    return {
        "schemaVersion": 1,
        "sourceCommit": SHA,
        "hostImageRef": HOST_REF,
        "before": before,
        "after": {**before, "containerId": "b" * 64, "runnerId": "new-runner"},
        "runnerReconnected": True,
        "inputReplayed": False,
        "workspaceDigest": "sha256:" + "3" * 64,
    }


def write_evidence(root, row=None, *, skipped=False):
    (root / driver.RECEIPT).write_text(json.dumps(row or receipt()))
    skip = '<skipped message="missing Docker"/>' if skipped else ""
    (root / driver.JUNIT).write_text(
        f'<testsuites><testsuite><testcase name="{driver.TEST_NAME}">{skip}'
        "</testcase></testsuite></testsuites>"
    )


def validate(root):
    return driver.validate_evidence(
        root, source_commit=SHA, host_image_ref=HOST_REF, host_image_id=IMAGE_ID
    )


def test_real_identity_transition_is_accepted(tmp_path):
    write_evidence(tmp_path)
    validate(tmp_path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sourceCommit", "f" * 40),
        ("hostImageRef", "other@" + IMAGE_ID),
        ("inputReplayed", True),
        ("runnerReconnected", False),
        ("workspaceDigest", ""),
    ],
)
def test_receipt_cannot_assert_another_candidate_or_weaken_recovery(
    tmp_path, field, value
):
    row = receipt()
    row[field] = value
    write_evidence(tmp_path, row)
    with pytest.raises(driver.RecoveryError):
        validate(tmp_path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("containerId", "a" * 64),
        ("hostImageId", "sha256:" + "f" * 64),
        ("stateVolume", "different"),
        ("hostId", "different"),
        ("sessionId", "different"),
        ("bridgeId", "different"),
        ("runnerId", "old-runner"),
        ("runnerId", ""),
        ("messageItemIds", ["sent-input"]),
    ],
)
def test_receipt_checks_observed_values_not_boolean_claims(tmp_path, field, value):
    row = receipt()
    row["after"][field] = value
    write_evidence(tmp_path, row)
    with pytest.raises(driver.RecoveryError):
        validate(tmp_path)


def test_skipped_row_is_not_passing_evidence(tmp_path):
    write_evidence(tmp_path, skipped=True)
    with pytest.raises(driver.RecoveryError, match="executed"):
        validate(tmp_path)


def test_missing_receipt_is_unavailable(tmp_path):
    with pytest.raises(driver.RecoveryError, match="unavailable"):
        validate(tmp_path)


def test_invocation_clears_old_receipt_and_cannot_pass_without_new_evidence(
    tmp_path, monkeypatch
):
    write_evidence(tmp_path)

    def skip_test(command, **kwargs):
        assert not (tmp_path / driver.RECEIPT).exists()
        assert not (tmp_path / driver.JUNIT).exists()
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(driver.subprocess, "run", skip_test)
    with pytest.raises(driver.RecoveryError, match="unavailable"):
        driver.run_test(
            ["docker", "run"],
            tmp_path,
            source_commit=SHA,
            host_image_ref=HOST_REF,
            host_image_id=IMAGE_ID,
        )


def test_compose_reuses_canonical_owners_with_test_only_network_and_credentials():
    root = Path(__file__).resolve().parents[3]
    source = yaml.safe_load((root / "docker-compose.yaml").read_text())
    document = driver.compose_document(
        source, root, "test-server@" + IMAGE_ID, moonmind_image=IMAGE_ID
    )
    assert set(document["services"]) == {
        "postgres",
        "omnigent-db-init",
        "omnigent-agent-init",
        "omnigent",
        "sandbox-egress-proxy",
    }
    assert document["networks"] == {"test": {"internal": True}}
    for name, service in document["services"].items():
        if name != "sandbox-egress-proxy":
            assert service["networks"] == ["test"]
        assert "env_file" not in service
        assert "container_name" not in service
        assert service["restart"] == "no"
    proxy = document["services"]["sandbox-egress-proxy"]
    canonical_proxy = source["services"]["sandbox-egress-proxy"]
    assert proxy["image"] == IMAGE_ID
    assert proxy["entrypoint"] == canonical_proxy["entrypoint"]
    assert proxy["healthcheck"] == canonical_proxy["healthcheck"]
    assert proxy["networks"] == {"test": {"aliases": ["omnigent-egress-proxy"]}}
    assert proxy["volumes"] == []
    assert proxy["ports"] == []
    assert proxy["environment"] == {"MOONMIND_PACKAGE_REGISTRY_EGRESS_ENABLED": "false"}
    init = document["services"]["omnigent-db-init"]
    assert init["command"] == source["services"]["omnigent-db-init"]["command"]
    agent = document["services"]["omnigent-agent-init"]
    assert agent["command"] == source["services"]["omnigent-agent-init"]["command"]
    server = document["services"]["omnigent"]
    assert server["image"] == agent["image"] == "test-server@" + IMAGE_ID
    assert server["ports"] == []
    # The stock upstream host cannot present the control credential, so the
    # fixture uses the single-user owner a deployment's host registers as.
    assert server["environment"]["OMNIGENT_AUTH_ENABLED"] == "0"
    assert not any(
        key.startswith("OMNIGENT_AUTH_") and key != "OMNIGENT_AUTH_ENABLED"
        for key in server["environment"]
    )


def test_compose_render_does_not_modify_canonical_document():
    root = Path(__file__).resolve().parents[3]
    source = yaml.safe_load((root / "docker-compose.yaml").read_text())
    original = copy.deepcopy(source)
    driver.compose_document(source, root, "test-server@" + IMAGE_ID, moonmind_image=IMAGE_ID)
    assert source == original


def test_candidate_command_does_not_overlay_application_source(tmp_path):
    command = driver.test_command(
        image=IMAGE_ID,
        network="moonmind-test-fixture_test",
        name="moonmind-test-fixture-driver",
        work=tmp_path,
        dependencies=tmp_path / "deps",
        source_commit=SHA,
        host_image_ref=HOST_REF,
        token="ephemeral-fixture",
        project="moonmind-test-fixture",
    )
    assert command[command.index("--entrypoint") + 1] == "python"
    assert IMAGE_ID in command
    assert not any("dst=/app" in arg for arg in command)
    assert not any(
        "OPENAI_API_KEY" in arg or "ANTHROPIC_API_KEY" in arg for arg in command
    )
    assert "--confcutdir=/test-driver" in command
    assert any(arg.endswith("::" + driver.TEST_NAME) for arg in command)
    assert "MOONMIND_OMNIGENT_EXPECTED_HOST_OWNER=local" in command


def test_existing_exact_artifact_job_owns_required_recovery_invocation():
    root = Path(__file__).resolve().parents[3]
    workflow = yaml.safe_load(
        (root / ".github/workflows/pytest-unit-tests.yml").read_text()
    )
    job = workflow["jobs"]["omnigent-exact-artifact"]
    steps = job["steps"]
    owning = [
        step
        for step in steps
        if "tools/ci/run_credential_free_host_recovery.py" in step.get("run", "")
    ]
    assert len(owning) == 1
    assert job["runs-on"] == "ubuntu-latest"
    assert "continue-on-error" not in owning[0]
    assert "if" not in owning[0]
    assert '--moonmind-image "${{ steps.digest.outputs.runnable }}"' in owning[0]["run"]
    assert (
        '--host-image "${{ steps.recovery-images.outputs.host }}"' in owning[0]["run"]
    )
    assert (
        '--server-image "${{ steps.recovery-images.outputs.server }}"'
        in owning[0]["run"]
    )
    assert "omnigent-exact-artifact" in workflow["jobs"]["ci-required"]["needs"]


@pytest.mark.parametrize("location", ["root", "before", "after"])
def test_raw_inspect_or_unknown_fields_are_not_accepted(tmp_path, location):
    row = receipt()
    target = row if location == "root" else row[location]
    target["Config"] = {"Env": ["TOKEN=must-not-be-published"]}
    write_evidence(tmp_path, row)
    with pytest.raises(driver.RecoveryError, match="unrecognized"):
        validate(tmp_path)


@pytest.mark.parametrize(
    "path",
    [
        "tools/ci/run_credential_free_host_recovery.py",
        "tests/unit/tools/test_run_credential_free_host_recovery.py",
        "tests/integration/omnigent/test_exact_docker_n_way_concurrency.py",
        "moonmind/omnigent/oauth_host_runtime.py",
        "moonmind/omnigent/bridge_store.py",
        "moonmind/omnigent/workspace_publication.py",
        "moonmind/omnigent/host_services/launcher.py",
        "tools/register_omnigent_agent.py",
        "services/omnigent/agents/opencode-native-ui/config.yaml",
    ],
)
def test_recovery_boundary_changes_select_the_real_artifact_owner(path):
    from tools.select_test_suites import is_exact_artifact_owned

    assert is_exact_artifact_owned(path)


def test_readiness_uses_exact_candidate_inside_the_private_test_network():
    command = driver.readiness_command(
        image=IMAGE_ID,
        network="moonmind-test-fixture_test",
        token="ephemeral-fixture",
    )
    assert command[command.index("--network") + 1] == "moonmind-test-fixture_test"
    assert command[command.index("--entrypoint") + 1] == "python"
    assert IMAGE_ID in command
    assert "--read-only" in command
    assert not {"--publish", "-p", "--mount", "--privileged"}.intersection(command)
    assert "OMNIGENT_API_TOKEN=ephemeral-fixture" in command
    assert command[-1] == driver.READINESS_SCRIPT


@pytest.mark.parametrize("ready", [False, True, "invalid_json", "wrong_shape"])
def test_actual_readiness_script_checks_authenticated_internal_http(
    monkeypatch, tmp_path, ready
):
    import runpy
    import time
    import urllib.error
    import urllib.request
    from types import SimpleNamespace

    requests = []
    clock = [0.0]
    monkeypatch.setenv("OMNIGENT_API_TOKEN", "ephemeral-fixture")
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + 90)
    )

    class Response:
        status = 200

        def read(self, limit):
            assert limit == 1024 * 1024
            if ready == "invalid_json":
                return b"<html>proxy status page</html>"
            if ready == "wrong_shape":
                return b'{"status": "ok"}'
            return b'{"data": []}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def open_request(request, *, timeout):
        requests.append(request)
        assert timeout == 2
        assert request.full_url == "http://omnigent:8000/v1/agents"
        assert request.host == "omnigent-egress-proxy:3129"
        assert request.selector == "http://omnigent:8000/v1/agents"
        assert request.get_header("Authorization") == "Bearer ephemeral-fixture"
        if ready and len(requests) > 1:
            return Response()
        raise urllib.error.URLError("still starting")

    def opener(proxy_handler):
        assert proxy_handler.proxies == {}
        return SimpleNamespace(open=open_request)

    monkeypatch.setattr(urllib.request, "build_opener", opener)
    script = tmp_path / "readiness.py"
    script.write_text(driver.READINESS_SCRIPT)
    if ready is True:
        runpy.run_path(str(script))
        assert len(requests) == 2
    else:
        with pytest.raises(SystemExit, match="internal service did not become ready"):
            runpy.run_path(str(script))
        assert len(requests) == 2


def test_readiness_sends_authenticated_absolute_request_through_proxy(
    tmp_path, monkeypatch
):
    import http.client
    import http.server
    import runpy
    import socket
    import threading

    observed = []

    class Proxy(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data": []}')

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def connect(connection):
        assert connection.host == "omnigent-egress-proxy"
        assert connection.port == 3129
        connection.sock = socket.create_connection(server.server_address, timeout=2)

    monkeypatch.setattr(http.client.HTTPConnection, "connect", connect)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("http_proxy", "http://unused.invalid:1")
    monkeypatch.setenv("OMNIGENT_API_TOKEN", "ephemeral-fixture")
    script = tmp_path / "readiness.py"
    script.write_text(driver.READINESS_SCRIPT)
    try:
        runpy.run_path(str(script))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert observed == [
        ("http://omnigent:8000/v1/agents", "Bearer ephemeral-fixture")
    ]


@pytest.mark.parametrize("proxy_image_matches", [False, True])
def test_main_checks_exact_server_then_probes_before_recovery(
    tmp_path, monkeypatch, proxy_image_matches
):
    from types import SimpleNamespace

    pin = "f" * 40
    server_ref = "127.0.0.1:5000/moonmind-test-server@" + IMAGE_ID
    observed = []

    def image(ref):
        return {
            "Id": IMAGE_ID,
            "RepoDigests": [ref],
            "Config": {
                "Labels": {
                    "org.opencontainers.image.revision": (
                        SHA if ref == IMAGE_ID else pin
                    ),
                    "moonmind.source.revision": SHA,
                }
            },
        }

    def command(args, **_kwargs):
        output = ""
        if args[0] == "git":
            output = (
                pin if "HEAD:omnigent" in args or args[2].endswith("/omnigent") else SHA
            )
        elif args[:2] == ["docker", "compose"]:
            assert "port" not in args
            if "ps" in args:
                output = "proxy-container" if args[-1] == "sandbox-egress-proxy" else "server-container"
            if "up" in args:
                assert args[-2:] == ["omnigent", "sandbox-egress-proxy"]
        elif args[:2] == ["docker", "inspect"]:
            container = args[-1]
            assert container in {"server-container", "proxy-container"}
            observed.append("proxy-image" if container == "proxy-container" else "server-image")
            image_id = (
                "sha256:" + "f" * 64
                if container == "proxy-container" and not proxy_image_matches else IMAGE_ID
            )
            output = json.dumps([{"Id": container, "Image": image_id}])
        elif args[-1] == driver.READINESS_SCRIPT:
            assert observed == ["server-image", "proxy-image"]
            assert IMAGE_ID in args
            assert args[args.index("--network") + 1].startswith(
                "moonmind-test-recovery-"
            )
            observed.append("readiness")
        return SimpleNamespace(stdout=output, stderr="", returncode=0)

    def run_test(args, _root, **_identity):
        assert observed == ["server-image", "proxy-image", "readiness"]
        assert IMAGE_ID in args
        assert args[args.index("--network") + 1].startswith("moonmind-test-recovery-")
        observed.append("recovery")
        return receipt()

    monkeypatch.setattr(driver.shutil, "which", lambda _name: "/test/docker")
    monkeypatch.setattr(driver, "_image", image)
    monkeypatch.setattr(driver, "_command", command)
    monkeypatch.setattr(driver, "run_test", run_test)
    monkeypatch.setattr(driver, "_cleanup", lambda *_args: None)
    assert (
        driver.main(
            [
                "--moonmind-image",
                IMAGE_ID,
                "--server-image",
                server_ref,
                "--host-image",
                HOST_REF,
                "--pr-head",
                SHA,
                "--base-commit",
                SHA,
                "--dependencies",
                str(tmp_path / "deps"),
                "--output-dir",
                str(tmp_path / "evidence"),
            ]
        )
        == (0 if proxy_image_matches else 1)
    )
    assert observed == ["server-image", "proxy-image"] + (
        ["readiness", "recovery"] if proxy_image_matches else []
    )
    if not proxy_image_matches:
        report = json.loads((tmp_path / "evidence/credential-recovery-result.json").read_text())
        assert report["status"] == "unavailable"
        assert "running proxy differs from the built artifact" in report["detail"]


def test_failed_recovery_row_reports_the_pytest_failure(tmp_path, monkeypatch):
    def failing_test(command, **kwargs):
        (tmp_path / driver.JUNIT).write_text(
            f'<testsuites><testsuite><testcase name="{driver.TEST_NAME}">'
            '<failure message="AssertionError: runner did not reconnect">'
            "test_exact_docker_n_way_concurrency.py:1031: AssertionError"
            "</failure></testcase></testsuite></testsuites>"
        )
        return type("Result", (), {"returncode": 1})()

    monkeypatch.setattr(driver.subprocess, "run", failing_test)
    with pytest.raises(driver.RecoveryError) as raised:
        driver.run_test(
            ["docker", "run"],
            tmp_path,
            source_commit=SHA,
            host_image_ref=HOST_REF,
            host_image_id=IMAGE_ID,
        )
    assert "exit 1" in str(raised.value)
    assert "runner did not reconnect" in str(raised.value)
    assert "concurrency.py:1031" in str(raised.value)


def test_actions_failure_is_annotated_without_the_test_owner_token(
    tmp_path, monkeypatch, capsys
):
    from types import SimpleNamespace

    token = "moonmind-test-" + "f" * 32

    def command(args, **_kwargs):
        if args[:2] == ["docker", "info"]:
            raise driver.RecoveryError(f"daemon refused {token}\n100% unavailable")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(driver.shutil, "which", lambda _name: "/test/docker")
    monkeypatch.setattr(driver.uuid, "uuid4", lambda: SimpleNamespace(hex="f" * 32))
    monkeypatch.setattr(driver, "_command", command)
    monkeypatch.setattr(driver, "_cleanup", lambda *_args: None)
    assert (
        driver.main(
            [
                "--moonmind-image",
                IMAGE_ID,
                "--server-image",
                HOST_REF,
                "--host-image",
                HOST_REF,
                "--pr-head",
                SHA,
                "--base-commit",
                SHA,
                "--dependencies",
                str(tmp_path / "deps"),
                "--output-dir",
                str(tmp_path / "evidence"),
            ]
        )
        == 1
    )
    annotations = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("::error")
    ]
    assert annotations == [
        "::error title=Credential-free recovery setup failed::"
        "daemon refused [test-owner]%0A100%25 unavailable"
    ]
