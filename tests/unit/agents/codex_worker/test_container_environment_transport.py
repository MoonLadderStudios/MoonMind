"""Container launch values cross the daemon boundary without becoming host settings."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from moonmind.agents.codex_worker.handlers import CodexExecHandler, CommandResult
from moonmind.agents.codex_worker.worker import CodexWorker, CodexWorkerConfig, JobCancellationRequested
from tests.unit.agents.codex_worker.test_worker import (
    FakeQueueClient,
    _build_execute_stage_payloads,
    _build_execute_stage_workspace,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("pull_mode", ["always", "if-missing"])
@pytest.mark.parametrize("success_log_failure", [False, True])
async def test_container_launch_keeps_values_out_of_argv_and_client_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    pull_mode: str, success_log_failure: bool,
) -> None:
    authored = {
        "BUILD_CREDENTIAL": "fixture-sensitive-build-value",
        "PATH": "/untrusted/bin",
        "LD_PRELOAD": "/untrusted/load.so",
        "DOCKER_HOST": "tcp://untrusted.example:2375\ncontainer-only",
        "DOCKER_CONTEXT": "container-context",
        "DOCKER_CONFIG": "/untrusted/config",
        "MULTILINE": "first line\r\nsecond line=still data\n",
        "EMPTY": "",
        "SPACES": "  preserved  ",
    }
    monkeypatch.setenv("DOCKER_HOST", "unix:///trusted/docker.sock")
    monkeypatch.delenv("DOCKER_API_VERSION", raising=False)
    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:8000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
        ),
        queue_client=FakeQueueClient(),
        codex_exec_handler=CodexExecHandler(workdir_root=tmp_path),
    )
    job_id = uuid4()
    prepared = _build_execute_stage_workspace(tmp_path=tmp_path, job_id=job_id)
    payload, _ = _build_execute_stage_payloads()
    payload["workflow"]["container"] = {
        "enabled": True,
        "image": "arbitrary-image:fixture",
        "command": ["custom-program", "--argument"],
        "env": authored,
        "pull": pull_mode,
        "resources": {"cpus": "1.5", "memory": "128m"},
        "cacheVolumes": [{"name": "build-cache", "target": "/cache"}],
    }
    spec = worker._extract_container_task_spec(payload)
    observed = []
    requests = []

    class Process:
        returncode = 0

        def __init__(self, args):
            self.args = args
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            body = b'{"Id":"owned-container-id","Warnings":[]}'
            if args[1:3] == ("system", "dial-stdio"):
                self.stdout.feed_data(
                    b"HTTP/1.1 201 Created\r\nContent-Length: "
                    + str(len(body)).encode() + b"\r\n\r\n" + body
                )
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.stdin = self

        def write(self, data):
            requests.append(data)

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait(self):
            return self.returncode

        async def communicate(self, data=None):
            if self.args[1:3] == ("system", "dial-stdio"):
                requests.append(data)
                body = b'{"Id":"owned-container-id","Warnings":[]}'
                return (
                    b"HTTP/1.1 201 Created\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body,
                    b"",
                )
            return b"", b""

    async def spawn(*args, **kwargs):
        observed.append((args, kwargs))
        return Process(args)

    if success_log_failure:
        append_log = worker._append_stage_log

        def log_with_failed_success_record(path, line):
            if line.startswith("docker container created:"):
                raise OSError("fixture log unavailable")
            append_log(path, line)

        monkeypatch.setattr(worker, "_append_stage_log", log_with_failed_success_record)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    result = await worker._run_container_execute_stage(
        job_id=job_id, canonical_payload=payload, prepared=prepared, container_spec=spec
    )

    assert result.succeeded
    for args, options in observed:
        for value in authored.values():
            if value:
                assert value not in " ".join(args)
        assert options["env"] is None or options["env"] == dict(os.environ)
    assert len(requests) == 1
    headers, body = requests[0].split(b"\r\n\r\n", 1)
    assert headers.startswith(f"POST /containers/create?name=mm-task-{job_id} ".encode())
    config = json.loads(body)
    received_env = dict(item.split("=", 1) for item in config["Env"])
    assert {key: received_env[key] for key in authored} == authored
    assert received_env["JOB_ID"] == str(job_id)
    assert received_env["REPOSITORY"] == payload["repository"]
    assert config["Image"] == spec.image
    assert config["Cmd"] == list(spec.command)
    assert "Entrypoint" not in config
    assert config["HostConfig"]["AutoRemove"] is True
    assert config["HostConfig"]["NanoCpus"] == 1_500_000_000
    assert config["HostConfig"]["Memory"] == 128 * 1024 * 1024
    assert {"Type": "volume", "Source": "build-cache", "Target": "/cache"} in config["HostConfig"]["Mounts"]
    assert any(args[1:3] == ("start", "--attach") for args, _ in observed)
    image_actions = [args[1] for args, _ in observed if args[1] in {"image", "pull"}]
    assert image_actions == (["pull"] if pull_mode == "always" else ["image"])
    log = prepared.execute_log_path.read_text()
    assert authored["BUILD_CREDENTIAL"] not in log


@pytest.mark.parametrize(
    "failure",
    ["daemon_refusal", "lost_response_owned", "lost_response_other", "oversize", "create_timeout", "create_cancel", "attach_nonzero", "attach_timeout", "attach_cancel"],
)
async def test_container_transport_failure_preserves_diagnostics_and_owned_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    secret = "fixture-sensitive-container-setting"
    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:8000", worker_id="worker-1",
            worker_token=None, poll_interval_ms=1500, lease_seconds=120,
            workdir=tmp_path,
        ),
        queue_client=FakeQueueClient(),
        codex_exec_handler=CodexExecHandler(workdir_root=tmp_path),
    )
    worker._active_cancel_event = asyncio.Event()
    job_id = uuid4()
    prepared = _build_execute_stage_workspace(tmp_path=tmp_path, job_id=job_id)
    payload, _ = _build_execute_stage_payloads()
    payload["workflow"]["container"] = {
        "enabled": True, "image": "fixture-image", "command": ["run-build"],
        "env": {"BUILD_SETTING": secret}, "timeoutSeconds": 1,
    }
    spec = worker._extract_container_task_spec(payload)
    commands = []
    create_config = {}
    killed = []

    class Process:
        def __init__(self):
            self.returncode = None
            self.stdin = self
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.done = asyncio.Event()

        def write(self, request):
            create_config.update(json.loads(request.split(b"\r\n\r\n", 1)[1]))
            if failure in {"create_timeout", "create_cancel"}:
                if failure == "create_cancel":
                    worker._active_cancel_event.set()
                return
            if failure == "daemon_refusal":
                body = json.dumps({"message": f"refused {secret}"}).encode()
                status = b"400 Bad Request"
            else:
                body, status = b'{"Id":"owned-id"}', b"201 Created"
            response = b"HTTP/1.1 " + status + b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
            if failure.startswith("lost_response"):
                response = b"lost response"
            elif failure == "oversize":
                response = b"x" * (1024 * 1024 + 1)
            self.stdout.feed_data(response)
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.returncode = 0
            self.done.set()

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait(self):
            await self.done.wait()
            return self.returncode

        def kill(self):
            killed.append(True)
            self.returncode = -9
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.done.set()

    async def spawn(*args, **kwargs):
        assert args[1:] == ("system", "dial-stdio")
        assert kwargs["env"] == dict(os.environ)
        return Process()

    async def run_stage(command, **kwargs):
        commands.append(command)
        action = command[1]
        if action == "image":
            return CommandResult(tuple(command), 1, "", "missing image")
        if action == "inspect":
            # A refusal did not create ours; an old same-name launch is retained.
            labels = dict(create_config["Labels"])
            if failure in {"lost_response_other", "daemon_refusal"}:
                labels["moonmind.container_launch"] = "older-launch"
            if worker._active_cancel_event.is_set():
                assert not kwargs["cancel_event"].is_set()
            return CommandResult(tuple(command), 0, "reconciled-owned-id " + json.dumps(labels), "")
        if action == "start":
            if failure == "attach_timeout":
                raise asyncio.TimeoutError()
            if failure == "attach_cancel":
                worker._active_cancel_event.set()
                raise JobCancellationRequested("cancelled")
            return CommandResult(tuple(command), 7, "", secret)
        return CommandResult(tuple(command), 0, "", "")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(worker, "_run_stage_command", run_stage)
    result = await worker._run_container_execute_stage(
        job_id=job_id, canonical_payload=payload, prepared=prepared, container_spec=spec
    )
    assert not result.succeeded
    assert commands[0][1:3] == ["image", "inspect"]
    assert commands[1][1] == "pull"
    starts = [command for command in commands if command[1] == "start"]
    removals = [command for command in commands if command[1] == "rm"]
    if failure.startswith("attach"):
        assert starts == [["docker", "start", "--attach", "owned-id"]]
        assert removals == [["docker", "rm", "--force", "owned-id"]]
    else:
        assert not starts
        assert bool(removals) == (failure not in {"lost_response_other", "daemon_refusal"})
        if removals:
            assert removals == [["docker", "rm", "--force", "reconciled-owned-id"]]
    if failure == "attach_nonzero":
        assert result.error_message == "container command failed (7)"
    if failure in {"attach_timeout", "create_timeout"}:
        metadata = json.loads((prepared.artifacts_dir / "container/metadata/run.json").read_text())
        assert metadata["timedOut"] is True
        assert metadata["exitCode"] == 124
    if failure == "attach_timeout":
        assert ["docker", "stop", "owned-id"] in commands
    if failure in {"create_cancel", "create_timeout"}:
        assert killed
    for artifact in (prepared.execute_log_path, prepared.artifacts_dir / "container/metadata/run.json"):
        if artifact.exists():
            assert secret not in artifact.read_text()
    assert secret not in str(result.error_message)


@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.slow
async def test_container_real_docker_cli_transfers_json_and_half_closes_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chunked: bool
) -> None:
    """Exercise the installed Docker CLI against a run-local fake daemon only."""
    binary = shutil.which("docker")
    if os.name != "posix" or not binary:
        pytest.skip("requires POSIX Unix sockets and the Docker CLI")
    socket_path = tmp_path / "d.sock"
    docker_config = tmp_path / "client-config"
    docker_config.mkdir()
    monkeypatch.setenv("DOCKER_HOST", f"unix://{socket_path}")
    monkeypatch.setenv("DOCKER_CONFIG", str(docker_config))
    monkeypatch.setenv("DOCKER_API_VERSION", "1.47")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.delenv("DOCKER_TLS", raising=False)
    monkeypatch.delenv("DOCKER_TLS_VERIFY", raising=False)
    received = []
    half_closed = []
    server_errors = []
    expected_id = "a" * 64
    container_env = ["BUILD_SETTING=fixture-private-value", "DOCKER_HOST=inside\ncontainer", "MULTILINE=first\nsecond\n"]

    async def daemon(reader, writer):
        try:
            method, path, _ = (await reader.readline()).decode().strip().split(" ", 2)
            headers = {}
            while (line := await reader.readline()) not in {b"\r\n", b""}:
                key, _, value = line.decode().partition(":")
                headers[key.lower()] = value.strip()
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            if path.startswith("/v1.47/containers/create?"):
                received.append(json.loads(body))
                half_closed.append(await asyncio.wait_for(reader.read(1), timeout=3.0))
                response_body = json.dumps({"Id": expected_id, "Warnings": []}).encode()
                if chunked:
                    response = (
                        b"HTTP/1.1 201 Created\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                        + f"{len(response_body):x}\r\n".encode() + response_body + b"\r\n0\r\n\r\n"
                    )
                else:
                    response = (
                        b"HTTP/1.1 201 Created\r\nContent-Length: "
                        + str(len(response_body)).encode() + b"\r\nConnection: close\r\n\r\n" + response_body
                    )
            elif path.endswith("/_ping"):
                response = b"HTTP/1.1 200 OK\r\nAPI-Version: 1.47\r\nOSType: linux\r\nContent-Length: 2\r\nConnection: close\r\n\r\n"
                if method != "HEAD":
                    response += b"OK"
            else:
                raise AssertionError(f"unexpected fake-daemon request: {method} {path}")
            writer.write(response)
            await writer.drain()
        except Exception as exc:
            server_errors.append(str(exc))
        finally:
            writer.close()
            await writer.wait_closed()

    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:8000", worker_id="worker-1",
            worker_token=None, poll_interval_ms=1500, lease_seconds=120,
            workdir=tmp_path, docker_binary=binary,
        ),
        queue_client=FakeQueueClient(),
        codex_exec_handler=CodexExecHandler(workdir_root=tmp_path),
    )
    config = {
        "Image": "fixture-image", "Cmd": ["fixture-command"],
        "Env": container_env, "Labels": {"moonmind.container_launch": "fixture-launch"},
    }
    async with await asyncio.start_unix_server(daemon, path=str(socket_path)):
        container_id = await worker._create_task_container(
            config=config, name=f"mm-task-{uuid4()}", cwd=tmp_path,
            log_path=tmp_path / "execute.log", timeout_seconds=10.0,
        )
    assert container_id == expected_id
    assert received == [config]
    assert half_closed == [b""]
    assert not server_errors
    assert "fixture-private-value" not in (tmp_path / "execute.log").read_text()
