"""The live-host path of the endpoint runner and its gateway tunnel (#5440)."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from moonmind.schemas.repository_scanner_models import EndpointAssessment
from moonmind.security import endpoint_assessment_runner as runner
from moonmind.security.endpoint_assessments import (
    EndpointExecution,
    parse_results,
    summarize,
)
from moonmind.security.native_tcp_gateway import CONNECT_FAILURES, CONNECT_SUCCESS
from moonmind.security.repository_scanner_runner import BoundedProcesses
from tests.unit.security.test_endpoint_hosts import KEY, OTHER_KEY, assessment

ROOT = Path(__file__).resolve().parents[3]
ARF = (ROOT / "tests/fixtures/security/endpoint/hardened-arf.xml").read_bytes()
PRIVATE_KEY = (
    b"-----BEGIN OPENSSH PRIVATE KEY-----\nsecret-key-material\n"
    b"-----END OPENSSH PRIVATE KEY-----"
)
REMOTE = "/tmp/tmp.Fixture123"


@pytest.fixture
def content(tmp_path):
    directory = tmp_path / "content"
    directory.mkdir()
    stream = directory / "ssg-debian12-ds.xml"
    stream.write_bytes(b"<ds/>" * 100)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "release": "0.1.82",
                "datastreams": {
                    "ssg-debian12-ds.xml": hashlib.sha256(
                        stream.read_bytes()
                    ).hexdigest()
                },
            }
        )
    )
    return directory


@pytest.fixture
def credential(tmp_path):
    path = tmp_path / "credentials/host"
    path.parent.mkdir()
    path.write_bytes(PRIVATE_KEY)
    path.chmod(0o444)
    return path


class FakeHost:
    """An SSH server behind the gateway, as the SSH client would see it.

    It checks the pinned key before it looks at the identity file, as SSH
    key exchange does, and records every session the runner opened.
    """

    def __init__(self, *, server_key=KEY, unreachable=None, stall=None, fail=None):
        self.server_key = server_key
        self.unreachable = unreachable
        self.stall = stall
        self.fail = fail or {}
        self.sessions: list[str] = []
        self.argv: list[list[str]] = []
        self.identity_read = False
        self.uploaded = b""
        self.removed = False
        #: Each session's remaining time budget when it started.
        self.budgets: list[tuple[str, float]] = []

    def __call__(
        self, processes, argv, *, cwd, env, capture=None, errors=None, stdin=None, **_
    ):
        self.argv.append(argv)
        assert argv[0] == "ssh" and argv[-3] == "--"
        options = dict(
            argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "-o"
        )
        remote = argv[-1]
        step = next(
            (
                name
                for name, prefix in (
                    ("prepare", "umask 077"),
                    ("upload", "cat >"),
                    ("evaluate", "oscap xccdf eval"),
                    ("evaluate", "sudo -n oscap"),
                    ("fetch", "cat --"),
                    ("fetch", "sudo -n cat"),
                    ("cleanup", "rm -rf"),
                    ("cleanup", "sudo -n rm"),
                )
                if remote.startswith(prefix)
            ),
            None,
        )
        assert step, remote
        self.sessions.append(step)
        self.budgets.append((step, processes.deadline - time.monotonic()))
        if processes.stopped():
            return None, "canceled" if processes.canceled else "timed_out"

        def fail(text):
            if errors is not None:
                errors.write_text(text)
            return 255, "succeeded"

        if self.unreachable:
            return fail(f"moonmind-endpoint-tunnel: {self.unreachable}\n")
        known = Path(options["UserKnownHostsFile"]).read_text().splitlines()
        if known != [f"moonmind-endpoint {self.server_key}"]:
            return fail("Host key verification failed.\n")
        identity = Path(options["IdentityFile"])
        self.identity_read = True
        if identity.read_bytes().strip() != PRIVATE_KEY.strip():
            return fail("inspector@host: Permission denied (publickey).\n")
        if self.stall == step:
            processes.canceled = self.stall_cancels
            return None, "canceled" if self.stall_cancels else "timed_out"
        if step in self.fail:
            code, text = self.fail[step]
            if errors is not None:
                errors.write_text(text)
            if step == "prepare":
                # printf runs before oscap is looked up.
                capture.write_text(f"{REMOTE}\n")
            return code, "succeeded"
        if step == "prepare":
            capture.write_text(f"{REMOTE}\nOpenSCAP command line tool (oscap) 1.3.7\n")
        elif step == "upload":
            self.uploaded = stdin.read_bytes()
        elif step == "evaluate":
            return 2, "succeeded"
        elif step == "fetch":
            capture.write_bytes(ARF)
        else:
            self.removed = True
        return 0, "succeeded"

    stall_cancels = False

    def install(self, monkeypatch):
        monkeypatch.setattr(
            BoundedProcesses, "run", lambda p, *a, **k: self(p, *a, **k)
        )
        return self


def run(tmp_path, content, credential, fake, *, environ=None, **changes):
    model = EndpointAssessment.model_validate(assessment(**changes))
    output = tmp_path / "out"
    code = runner.run_live_host_assessment(
        model,
        output=output,
        content=content,
        credential=credential,
        environ={"MOONMIND_EGRESS_PROXY": "http://gateway:3128", **(environ or {})},
    )
    execution = EndpointExecution.model_validate_json(
        (output / "execution.json").read_text()
    )
    return code, model, execution, output


def retained(output: Path) -> bytes:
    return b"".join(
        path.read_bytes() for path in sorted(output.rglob("*")) if path.is_file()
    )


def test_success_evaluates_in_place_and_retains_the_offline_summary_shape(
    tmp_path, monkeypatch, content, credential
):
    host = FakeHost().install(monkeypatch)
    code, model, execution, output = run(tmp_path, content, credential, host)
    assert execution.state == "succeeded"
    assert execution.version == "1.3.7"
    assert execution.exit_code == 2
    assert execution.content_file == "ssg-debian12-ds.xml"
    assert host.sessions == ["prepare", "upload", "evaluate", "fetch", "cleanup"]
    assert host.uploaded == (content / "ssg-debian12-ds.xml").read_bytes()
    assert host.removed
    # Only the fixed evaluation runs, with the admitted profile and skips.
    evaluate = host.argv[2][-1]
    assert evaluate == (
        f"oscap xccdf eval --profile {model.profile} --skip-rule "
        f"{model.skip_rule_ids[0]} --results-arf {REMOTE}/results.xml "
        f"{REMOTE}/content.xml >/dev/null"
    )
    for argv in host.argv:
        options = [argv[i + 1] for i, a in enumerate(argv) if a == "-o"]
        assert "StrictHostKeyChecking=yes" in options
        assert "BatchMode=yes" in options
        assert "HostKeyAlgorithms=ssh-ed25519" in options
        assert "ForwardAgent=no" in options
        assert any(
            o.startswith("ProxyCommand=") and "endpoint_host_tunnel %h %p" in o
            for o in options
        )
        assert argv[-2] == "93.184.216.34" and argv[argv.index("-p") + 1] == "22"
        # The credential is a private file path, never a value in argv.
        assert not any(PRIVATE_KEY[:40].decode() in item for item in argv)
    native = parse_results((output / "results.xml").read_bytes(), 1 << 22)
    summary = summarize(model, execution, native, native_ref="art_results")
    assert summary.subject.observation == "live-host"
    assert b"secret-key-material" not in retained(output)
    assert code == 0 or execution.limitations


@pytest.mark.parametrize(
    "host,limitation,reached_identity",
    [
        (FakeHost(server_key=OTHER_KEY), "host_identity_unverified", False),
        (FakeHost(unreachable="refused"), "host_unreachable", False),
        (FakeHost(unreachable="unreachable"), "host_unreachable", False),
        (FakeHost(unreachable="denied"), "host_connection_denied", False),
        (FakeHost(unreachable="timed_out"), "host_connect_timed_out", False),
    ],
)
def test_unknown_identity_or_unreachable_host_stops_before_credentials(
    tmp_path, monkeypatch, content, credential, host, limitation, reached_identity
):
    host.install(monkeypatch)
    code, _, execution, output = run(tmp_path, content, credential, host)
    assert code == 2
    assert execution.state == "failed"
    assert limitation in execution.limitations
    assert host.identity_read is reached_identity
    # Nothing was placed on the host, so nothing needs removal.
    assert host.sessions == ["prepare"]
    assert not (output / "results.xml").exists()


def test_authentication_failure_is_reported_without_its_text(
    tmp_path, monkeypatch, content, tmp_path_factory
):
    wrong = tmp_path_factory.mktemp("wrong") / "host"
    wrong.write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\nother\n")
    host = FakeHost().install(monkeypatch)
    code, _, execution, output = run(tmp_path, content, wrong, host)
    assert execution.limitations == ["host_authentication_failed"]
    assert b"inspector@host" not in retained(output)


def test_missing_credential_never_connects(tmp_path, monkeypatch, content):
    host = FakeHost().install(monkeypatch)
    _, _, execution, _ = run(tmp_path, content, tmp_path / "absent", host)
    assert "host_credential_unavailable" in execution.limitations
    assert host.sessions == []


@pytest.mark.parametrize("cancels", [False, True])
def test_timeout_and_cancellation_still_remove_remote_files(
    tmp_path, monkeypatch, content, credential, cancels
):
    host = FakeHost(stall="evaluate")
    host.stall_cancels = cancels
    host.install(monkeypatch)
    code, _, execution, output = run(tmp_path, content, credential, host)
    assert code == 2
    assert execution.state == ("canceled" if cancels else "timed_out")
    assert host.sessions == ["prepare", "upload", "evaluate", "cleanup"]
    assert host.removed
    assert not (output / "results.xml").exists()


def _fits_before_gateway_closes(host: FakeHost, gateway_seconds: float) -> None:
    """Every evaluation step ends early enough for cleanup to still be routed."""
    cleanup = [budget for step, budget in host.budgets if step == "cleanup"]
    assert len(cleanup) == 1 and cleanup[0] > 0
    for step, budget in host.budgets:
        if step != "cleanup":
            assert budget > 0, step
            assert budget + cleanup[0] <= gateway_seconds + 1, (step, budget)


def test_evaluation_leaves_cleanup_time_inside_the_original_lease(
    tmp_path, monkeypatch, content, credential
):
    host = FakeHost().install(monkeypatch)
    # The lease started earlier; its original deadline is sooner than a fresh
    # native duration or the evaluation timeout would end.
    closes = int(time.time()) + 300
    code, _, execution, _ = run(
        tmp_path,
        content,
        credential,
        host,
        environ={"MOONMIND_EGRESS_DEADLINE": str(closes)},
    )
    assert code == 0 and execution.state == "succeeded"
    assert host.sessions == ["prepare", "upload", "evaluate", "fetch", "cleanup"]
    _fits_before_gateway_closes(host, closes - time.time())


def test_without_a_lease_deadline_the_native_duration_still_bounds_evaluation(
    tmp_path, monkeypatch, content, credential
):
    host = FakeHost().install(monkeypatch)
    limits = {
        "maxConnections": 8,
        "maxBytes": 256 * 1024 * 1024,
        "maxConnectionsPerSecond": 2,
        "maxConcurrentConnections": 1,
        "maxDurationSeconds": 120,
    }
    started = time.time()
    code, _, _, _ = run(tmp_path, content, credential, host, host={"limits": limits})
    assert code == 0
    _fits_before_gateway_closes(host, started + 120 - time.time())


def test_a_lease_without_room_for_cleanup_never_reaches_the_host(
    tmp_path, monkeypatch, content, credential
):
    host = FakeHost().install(monkeypatch)
    code, _, execution, _ = run(
        tmp_path,
        content,
        credential,
        host,
        environ={"MOONMIND_EGRESS_DEADLINE": str(int(time.time()) + 3)},
    )
    assert code == 2 and execution.state == "timed_out"
    assert not host.uploaded and "cleanup" not in host.sessions


def test_elevation_is_only_used_when_admitted_and_never_prompts(
    tmp_path, monkeypatch, content, credential
):
    host = FakeHost(fail={"evaluate": (1, "sudo: a password is required\n")})
    host.install(monkeypatch)
    _, _, execution, _ = run(
        tmp_path, content, credential, host, host={"elevated": True}
    )
    assert execution.limitations == ["elevation_refused"]
    assert host.argv[2][-1].startswith("sudo -n oscap xccdf eval ")
    assert host.argv[-1][-1] == f"sudo -n rm -rf -- {REMOTE}"


def test_missing_remote_scanner_and_failed_cleanup_are_recorded(
    tmp_path, monkeypatch, content, credential
):
    host = FakeHost(fail={"prepare": (127, ""), "cleanup": (1, "")})
    host.install(monkeypatch)
    _, _, execution, _ = run(tmp_path, content, credential, host)
    # The directory was named before oscap was found missing, so it is removed.
    assert execution.limitations == [
        "remote_cleanup_unconfirmed",
        "remote_scanner_unavailable",
    ]


def test_remote_directory_must_be_a_plain_absolute_path():
    model = EndpointAssessment.model_validate(assessment())
    for value in ("/tmp/x; rm -rf /", "relative", "/tmp/../etc", "/tmp/$(id)"):
        with pytest.raises(runner.InputError):
            runner.remote_commands(model, value)


def test_bounded_processes_feed_stdin_from_a_private_file(tmp_path):
    source = tmp_path / "in"
    source.write_bytes(b"datastream")
    capture = tmp_path / "out"
    code, state = BoundedProcesses(10, 1 << 20).run(
        ["cat"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        stdin=source,
        capture=capture,
    )
    assert (code, state, capture.read_bytes()) == (0, "succeeded", b"datastream")


# --- Tunnel ----------------------------------------------------------------


class FakeGateway:
    """A CONNECT gateway admitting one authority and echoing its stream."""

    def __init__(self, response: bytes):
        self.response = response
        self.server = socket.create_server(("127.0.0.1", 0))
        self.requests: list[bytes] = []
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self):
        return f"http://localhost:{self.server.getsockname()[1]}"

    def _serve(self):
        connection, _ = self.server.accept()
        with connection:
            header = b""
            while not header.endswith(b"\r\n\r\n"):
                header += connection.recv(1)
            self.requests.append(header)
            connection.sendall(self.response)
            if self.response != CONNECT_SUCCESS:
                return
            while chunk := connection.recv(65536):
                connection.sendall(chunk.upper())


def tunnel(gateway, stdin: bytes, *args):
    return subprocess.run(
        [sys.executable, "-m", "moonmind.security.endpoint_host_tunnel", *args],
        input=stdin,
        capture_output=True,
        env={
            **os.environ,
            "MOONMIND_EGRESS_PROXY": gateway.url,
            "PYTHONPATH": str(ROOT),
        },
        timeout=30,
        check=False,
    )


def test_tunnel_relays_one_admitted_endpoint():
    gateway = FakeGateway(CONNECT_SUCCESS)
    result = tunnel(gateway, b"ssh-2.0 hello", "93.184.216.34", "22")
    assert result.returncode == 0
    assert result.stdout == b"SSH-2.0 HELLO"
    assert gateway.requests == [
        b"CONNECT 93.184.216.34:22 HTTP/1.1\r\nHost: 93.184.216.34:22\r\n\r\n"
    ]


@pytest.mark.parametrize("outcome", ["refused", "unreachable", "timed_out", "denied"])
def test_tunnel_reports_only_the_gateway_outcome(outcome):
    gateway = FakeGateway(CONNECT_FAILURES[outcome])
    result = tunnel(gateway, b"", "93.184.216.34", "22")
    assert result.returncode == 1
    assert result.stderr == f"moonmind-endpoint-tunnel: {outcome}\n".encode()
    assert runner.ssh_failure(result.stderr) in {
        "host_unreachable",
        "host_connect_timed_out",
        "host_connection_denied",
    }


def test_tunnel_refuses_other_arguments_and_gateways():
    gateway = FakeGateway(CONNECT_SUCCESS)
    for args in (("bad host", "22"), ("93.184.216.34", "0"), ("93.184.216.34",)):
        result = tunnel(gateway, b"", *args)
        assert result.stderr == b"moonmind-endpoint-tunnel: configuration_error\n"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "moonmind.security.endpoint_host_tunnel",
            "h.test",
            "22",
        ],
        capture_output=True,
        env={
            **os.environ,
            "MOONMIND_EGRESS_PROXY": "http://u:p@gw:1",
            "PYTHONPATH": str(ROOT),
        },
        timeout=30,
        check=False,
    )
    assert result.stderr == b"moonmind-endpoint-tunnel: configuration_error\n"


def test_readiness_for_a_live_host_needs_the_ssh_client(monkeypatch, content):
    def oscap(_, argv, *, cwd, env, capture=None, **__):
        capture.write_text("OpenSCAP command line tool (oscap) 1.3.7\n")
        return 0, "succeeded"

    monkeypatch.setattr(BoundedProcesses, "run", oscap)
    model = EndpointAssessment.model_validate(assessment())
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    assert runner.readiness(model, content=content) == 2
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/ssh")
    assert runner.readiness(model, content=content) == 0
