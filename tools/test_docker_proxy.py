#!/usr/bin/env python3
"""Exercise the production proxy on a disposable Docker/Compose host.

The host owns setup/cleanup and controller calls. API assertions run in a
socket-free client on the proxy's internal network. No installed stack is used.
"""

from __future__ import annotations

import argparse
import copy
import http.client
import io
import json
import os
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
LABEL = "moonmind.test.docker-proxy"
WORKLOAD_IMAGE = "busybox:1.36"


def api(method, path, body=None, *, status=200, headers=None):
    headers = dict(headers or {})
    if isinstance(body, dict):
        body = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    connection = http.client.HTTPConnection("docker-proxy", 2375, timeout=90)
    try:
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        data = response.read()
        assert response.status == status, (method, path, response.status, data[:1000])
        return data
    finally:
        connection.close()


def hijack(path, body=None, *, headers=None):
    body = json.dumps(body).encode() if body is not None else b""
    request_headers = {
        "Host": "docker-proxy",
        "Connection": "Upgrade",
        "Upgrade": "tcp",
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        **(headers or {}),
    }
    stream = socket.create_connection(("docker-proxy", 2375), timeout=15)
    request = f"POST {path} HTTP/1.1\r\n"
    request += "".join(f"{key}: {value}\r\n" for key, value in request_headers.items())
    stream.sendall(request.encode() + b"\r\n" + body)
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = stream.recv(4096)
        assert chunk, response
        response += chunk
    head, _, initial = response.partition(b"\r\n\r\n")
    assert head.split()[1] == b"101", head
    return stream, initial


def drain(stream, initial):
    data = bytearray(initial)
    with stream:
        while chunk := stream.recv(4096):
            data.extend(chunk)
    return bytes(data)


def exec_output(prefix, container, command):
    execution = json.loads(
        api(
            "POST",
            f"{prefix}/containers/{container}/exec",
            {
                "AttachStdout": True,
                "AttachStderr": True,
                "Tty": True,
                "Cmd": ["sh", "-c", command],
            },
            status=201,
        )
    )["Id"]
    stream, initial = hijack(
        f"{prefix}/exec/{execution}/start", {"Detach": False, "Tty": True}
    )
    return drain(stream, initial), execution


def check_json_stream(data):
    rows = [json.loads(line) for line in data.splitlines() if line.strip()]
    assert rows and not any(
        row.get("error") or row.get("errorDetail") for row in rows
    ), rows


def client_checks(phase, project):
    assert not Path("/var/run/docker.sock").exists()
    if phase == "isolated":
        try:
            socket.create_connection(("docker-proxy", 2375), timeout=2)
        except OSError:
            print("Socket-free, network-isolated client cannot reach the Docker proxy.")
            return
        raise AssertionError("isolated client reached the Docker proxy")
    for attempt in range(30):
        try:
            version = json.loads(api("GET", "/version"))
            break
        except OSError:
            if attempt == 29:
                raise
            time.sleep(0.5)
    prefix = "/v" + version["ApiVersion"]
    api("GET", "/_ping")
    api("GET", prefix + "/info")
    if phase == "readonly":
        api("GET", prefix + "/containers/json")
        api("POST", prefix + "/containers/create", {}, status=403)
        api("DELETE", prefix + "/containers/absent", status=403)
        print("Read-only method policy passed.")
        return

    containers = []
    network = None
    volume = None
    built_image = None
    try:

        def create(command, **extra):
            result = json.loads(
                api(
                    "POST",
                    prefix + "/containers/create",
                    {
                        "Image": WORKLOAD_IMAGE,
                        "Cmd": ["sh", "-c", command],
                        "Labels": {LABEL: project},
                        **extra,
                    },
                    status=201,
                )
            )
            containers.append(result["Id"])
            return result["Id"]

        if phase == "matrix":
            check_json_stream(
                api(
                    "POST",
                    prefix
                    + "/images/create?"
                    + urlencode(
                        {
                            "fromImage": WORKLOAD_IMAGE,
                        }
                    ),
                )
            )
        api("GET", prefix + "/images/" + WORKLOAD_IMAGE + "/json")
        container = create("sleep 120")
        api("POST", f"{prefix}/containers/{container}/start", status=204)
        api("GET", f"{prefix}/containers/{container}/json")
        command = "printf 'begun\\n'; sleep 4; printf 'finished\\n'"
        if phase == "timeouts":
            command = "for i in 1 2 3 4 5 6; do echo tick; sleep 1; done; echo finished"
        output, execution = exec_output(prefix, container, command)
        assert b"finished" in output, output
        observed = json.loads(api("GET", f"{prefix}/exec/{execution}/json"))
        assert not observed["Running"] and observed["ExitCode"] == 0, observed
        if phase == "timeouts":
            started = time.monotonic()
            output, _ = exec_output(
                prefix, container, "echo begun; sleep 9; echo overdue"
            )
            elapsed = time.monotonic() - started
            assert b"begun" in output and b"overdue" not in output, output
            assert 2 <= elapsed < 8, elapsed
            print(
                f"Active hijack survived 6s; idle tunnel closed after {elapsed:.2f}s (3s policy)."
            )
            return
        if phase == "smoke":
            print("Proxy reconnect and exec passed.")
            return

        for method, path in (
            ("GET", "/secrets"),
            ("POST", "/secrets/create"),
            ("DELETE", "/configs/absent"),
            ("GET", "/services"),
            ("GET", "/swarm"),
            ("GET", "/system/df"),
            ("GET", "/plugins"),
            ("GET", "/unknown"),
            ("GET", "/%73ecrets"),
            ("POST", "/%61uth"),
        ):
            api(method, prefix + path, status=403)
        api("GET", prefix + "/%63ontainers/json")

        attached = create(
            "read -r value; printf 'attached:%s\\n' \"$value\"",
            OpenStdin=True,
            Tty=True,
        )
        stream, initial = hijack(
            f"{prefix}/containers/{attached}/attach?stream=1&stdin=1&stdout=1&stderr=1"
        )
        try:
            api("POST", f"{prefix}/containers/{attached}/start", status=204)
            stream.sendall(b"probe\n")
            assert b"attached:probe" in drain(stream, initial)
        finally:
            stream.close()

        network = json.loads(
            api(
                "POST",
                prefix + "/networks/create",
                {
                    "Name": project + "-workload",
                    "Internal": True,
                    "Labels": {LABEL: project},
                },
                status=201,
            )
        )["Id"]
        api("POST", f"{prefix}/networks/{network}/connect", {"Container": container})
        api("POST", f"{prefix}/networks/{network}/disconnect", {"Container": container})
        volume = project + "-data"
        api(
            "POST",
            prefix + "/volumes/create",
            {"Name": volume, "Labels": {LABEL: project}},
            status=201,
        )
        api("GET", prefix + "/volumes/" + volume)

        # Use the real BuildKit upload API, including a hijacked session.
        stream, _ = hijack(
            prefix + "/session",
            headers={
                "Upgrade": "h2c",
                "X-Docker-Expose-Session-Uuid": project,
            },
        )
        stream.close()
        context = io.BytesIO()
        with tarfile.open(fileobj=context, mode="w") as archive:
            for name, data in (
                ("Dockerfile", b"FROM scratch\nCOPY probe /probe\n"),
                ("probe", b"proxy-build-session\n"),
            ):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        built_image = project + ":probe"
        check_json_stream(
            api(
                "POST",
                prefix
                + "/build?"
                + urlencode(
                    {
                        "t": built_image,
                        "version": "2",
                        "nocache": "1",
                    }
                ),
                context.getvalue(),
                headers={"Content-Type": "application/x-tar"},
            )
        )
        api("GET", prefix + "/images/" + built_image + "/json")
        print(
            "Container/image/exec/attach/build/session/network/volume and deny matrix passed."
        )
    finally:
        original_error = sys.exc_info()[0]
        cleanup_errors = []

        def cleanup(*args, **kwargs):
            try:
                api(*args, **kwargs)
            except Exception as error:
                cleanup_errors.append(str(error))

        for container in containers:
            cleanup(
                "DELETE", f"{prefix}/containers/{container}?force=1&v=1", status=204
            )
        if network:
            cleanup("DELETE", f"{prefix}/networks/{network}", status=204)
        if volume:
            cleanup("DELETE", f"{prefix}/volumes/{volume}", status=204)
        if built_image:
            cleanup("DELETE", f"{prefix}/images/{built_image}")
        if cleanup_errors:
            print("API cleanup errors: " + "; ".join(cleanup_errors), file=sys.stderr)
            if not original_error:
                raise AssertionError(cleanup_errors)


def docker(*args, check=True, timeout=180):
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )
    if check and result.returncode:
        raise RuntimeError(f"docker {args} failed:\n{result.stdout}\n{result.stderr}")
    return result


def host_checks():
    docker("compose", "version")
    docker("info")
    project = "moonmind-test-proxy-" + uuid4().hex[:10]
    client_image = os.environ.get("MOONMIND_PYTHON_TEST_IMAGE", "python:3.12-alpine")
    if docker("image", "inspect", client_image, check=False).returncode:
        docker("pull", client_image, timeout=300)
    sys.path.insert(0, str(ROOT / "deploy/controller"))
    import engine

    with tempfile.TemporaryDirectory(prefix=project) as directory:
        directory = Path(directory)
        (directory / ".env").touch()
        render = [
            "compose",
            "--env-file",
            "/dev/null",
            "-p",
            project,
            "--project-directory",
            str(directory),
        ]
        rendered = None
        for files in (
            ("docker-compose.yaml",),
            ("docker-compose.yaml", "docker-compose.test.yaml"),
        ):
            args = [*render]
            for file in files:
                args += ["-f", str(ROOT / file)]
            config = json.loads(docker(*args, "config", "--format", "json").stdout)
            rendered = config if len(files) == 1 else rendered
            print(f"Compose render passed: {', '.join(files)}", flush=True)
        proxy = copy.deepcopy(rendered["services"]["docker-proxy"])
        assert not proxy.get("ports")
        assert rendered["networks"]["docker-proxy-network"]["internal"]
        for name, service in rendered["services"].items():
            for mount in service.get("volumes", []):
                if mount.get("source") == "/var/run/docker.sock":
                    assert name == "docker-proxy", (name, mount)
        template = ROOT / "docker/docker-proxy/haproxy.cfg.template"
        mounts = [
            mount
            for mount in proxy["volumes"]
            if mount["target"] == "/usr/local/etc/haproxy/haproxy.cfg.template"
        ]
        assert len(mounts) == 1 and mounts[0]["read_only"]
        # Render uses a test-owned .env/project directory, then restores this
        # one repository bind source for actual isolated container startup.
        mounts[0]["source"] = str(template)
        original = copy.deepcopy(proxy)
        stack = directory / "compose.json"

        def write_stack(service):
            stack.write_text(
                json.dumps(
                    {
                        "services": {"docker-proxy": service},
                        "networks": {"docker-proxy-network": {"internal": True}},
                    }
                )
            )

        write_stack(proxy)
        base = ("compose", "-p", project, "-f", str(stack))
        client_name = project + "-client"

        def client(phase, *, isolated=False):
            args = [
                "run",
                "--rm",
                "--name",
                client_name,
                "--network",
                "none" if isolated else project + "_docker-proxy-network",
                "--mount",
                f"type=bind,src={Path(__file__).resolve()},dst=/tests/test_docker_proxy.py,readonly",
                "--entrypoint",
                "python",
                client_image,
                "/tests/test_docker_proxy.py",
                "--client",
                phase,
                "--project",
                project,
            ]
            print(docker(*args).stdout, flush=True)

        def check_effective(expected):
            actual = docker(
                *base, "exec", "-T", "docker-proxy", "cat", "/tmp/haproxy.cfg"
            ).stdout
            assert actual == expected
            docker(
                *base,
                "exec",
                "-T",
                "docker-proxy",
                "haproxy",
                "-c",
                "-f",
                "/tmp/haproxy.cfg",
            )
            command = docker(
                *base, "exec", "-T", "docker-proxy", "cat", "/proc/1/cmdline"
            ).stdout
            assert "haproxy" in command and "/tmp/haproxy.cfg" in command, command
            ident = docker(*base, "ps", "-q", "docker-proxy").stdout.strip()
            inspected = json.loads(docker("inspect", ident).stdout)[0]
            assert not inspected["HostConfig"]["PortBindings"]
            assert len(inspected["NetworkSettings"]["Networks"]) == 1
            print(
                "Effective generated config, process, internal network and unpublished port passed.",
                flush=True,
            )

        try:
            docker(*base, "up", "-d", "docker-proxy")
            check_effective(template.read_text())
            client("matrix")
            client("isolated", isolated=True)
            docker(*base, "restart", "docker-proxy")
            client("smoke")

            # The portable controller's actual Compose owner repairs a stopped
            # proxy with no application/API/Temporal services in the fixture.
            docker(*base, "stop", "docker-proxy")
            result = engine.apply(
                engine.subprocess_runner(),
                project=project,
                project_dir=str(directory),
                compose_files=[str(stack)],
                services=["docker-proxy"],
                images=[proxy["image"]],
            )
            assert result["recreated"] == ["docker-proxy"], result
            client("smoke")
            docker(*base, "up", "-d", "--force-recreate", "docker-proxy")
            check_effective(template.read_text())
            client("smoke")

            proxy["environment"]["POST"] = "0"
            write_stack(proxy)
            docker(*base, "up", "-d", "docker-proxy")
            client("readonly")
            short = template.read_text()
            for kind in ("client", "server", "tunnel"):
                assert f"timeout {kind} 75m" in short
                short = short.replace(f"timeout {kind} 75m", f"timeout {kind} 3s")
            short_template = directory / "short.cfg.template"
            short_template.write_text(short)
            proxy = copy.deepcopy(original)
            for mount in proxy["volumes"]:
                if mount["target"] == "/usr/local/etc/haproxy/haproxy.cfg.template":
                    mount["source"] = str(short_template)
            write_stack(proxy)
            docker(*base, "up", "-d", "docker-proxy")
            check_effective(short)
            client("timeouts")
        finally:
            original_error = sys.exc_info()[0]
            cleanup_errors = []

            def cleanup(*args):
                try:
                    result = docker(*args, check=False, timeout=15)
                    if result.returncode:
                        print(result.stderr, file=sys.stderr)
                    return result.stdout
                except Exception as error:
                    cleanup_errors.append(str(error))
                    return ""

            print(cleanup(*base, "logs", "--no-color"))
            cleanup("rm", "-f", client_name)
            # Reconcile owned API effects even if a client was interrupted.
            for kind, remove in (
                ("container", ["rm", "-f", "-v"]),
                ("network", ["network", "rm"]),
                ("volume", ["volume", "rm"]),
            ):
                args = [kind, "ls", "-q", "--filter", f"label={LABEL}={project}"]
                if kind == "container":
                    args.append("-a")
                ids = cleanup(*args).split()
                if ids:
                    cleanup(*remove, *ids)
            cleanup("image", "rm", project + ":probe")
            cleanup(*base, "down", "--volumes", "--remove-orphans")
            if cleanup_errors:
                print(
                    "Host cleanup errors: " + "; ".join(cleanup_errors), file=sys.stderr
                )
                if not original_error:
                    raise AssertionError(cleanup_errors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--client", choices=("matrix", "smoke", "readonly", "timeouts", "isolated")
    )
    parser.add_argument("--project")
    args = parser.parse_args()
    if args.client:
        client_checks(args.client, args.project)
    else:
        host_checks()
