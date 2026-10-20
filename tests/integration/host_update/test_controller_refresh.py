"""Host-owned refresh through real Docker, root-owned state and private ingress."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = [pytest.mark.integration]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(args, *, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def test_host_refreshes_capable_controller_and_preserves_root_owned_work(
    tmp_path, monkeypatch
):
    if os.environ.get("MOONMIND_TEST_JOURNAL_TRANSITION") != "1":
        pytest.skip("Real Docker journey is enabled in controller-journey CI")
    candidate = os.environ["MOONMIND_TEST_JOURNAL_APP_IMAGE"]
    candidate_id = command(
        ["docker", "image", "inspect", "--format", "{{.Id}}", candidate]
    )
    project = f"moonmind-test-host-refresh-{uuid4().hex[:10]}"
    previous = f"{project}-previous:local"
    network = f"{project}_deployment-controller-network"
    repo = tmp_path / "deployment"
    repo.mkdir()
    state = repo / "deploy/state/controller"
    state.mkdir(parents=True)
    dockerfile = tmp_path / "Dockerfile"
    legacy_bootstrap = tmp_path / "bootstrap-legacy.py"
    legacy_bootstrap.write_text(
        (ROOT / "deploy/controller/bootstrap.py")
        .read_text()
        .replace("cmd_recover_identity", "cmd_legacy_recover_identity")
        .replace("cmd_describe_installation", "cmd_legacy_describe_installation")
    )
    dockerfile.write_text(
        f"FROM {candidate}\nLABEL moonmind.test.previous={project}\n"
        "COPY bootstrap-legacy.py /app/deploy/controller/bootstrap.py\n"
    )
    command(["docker", "build", "--network=none", "-t", previous, str(tmp_path)])
    previous_id = command(
        ["docker", "image", "inspect", "--format", "{{.Id}}", previous]
    )
    assert previous_id != candidate_id
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    bootstrap = load("refresh_bootstrap", ROOT / "deploy/controller/bootstrap.py")
    update = load(
        "refresh_update",
        ROOT / ".agents/skills/update-moonmind/scripts/update_release.py",
    )
    secret_file = bootstrap.ensure_secret(state)
    identity = bootstrap.ensure_identity(
        state, repo, port, target_project=project, target_network=network
    )
    identity["project"] = f"{project}-controller"
    (state / "controller-identity.json").write_text(json.dumps(identity))
    compose_file = bootstrap.render_compose_file(
        state_dir=state,
        repo=repo,
        image=previous_id,
        port=port,
        project=identity["project"],
        target_project=project,
        target_network=network,
    )
    compose = [
        "docker",
        "compose",
        "--project-name",
        identity["project"],
        "-f",
        str(compose_file),
    ]
    record = {"project": project, "image": candidate_id}
    url = f"http://127.0.0.1:{port}"
    secret = secret_file.read_text().strip()

    def container():
        return command([*compose, "ps", "-q", "controller"])

    def seed(operation_id, *, failed):
        command(
            [
                "docker",
                "exec",
                container(),
                "python",
                "-c",
                "import sys; sys.path.insert(0,'/app/deploy/controller'); "
                "import record; store=record.OperationStore('/var/lib/moonmind-controller'); "
                "op=store.begin(stack='moonmind', desired_image=sys.argv[2], "
                "source_revision='', operation_id=sys.argv[1]); "
                "[store.record_attempt_error(op['operationId'],error='original failure') "
                "for _ in range(record.MAX_AUTO_ATTEMPTS)] "
                "if sys.argv[3]=='failed' else None",
                operation_id,
                previous_id,
                "failed" if failed else "pending",
            ]
        )

    def saved_digest(operation_id):
        return command(
            [
                "docker",
                "exec",
                container(),
                "python",
                "-c",
                "import hashlib,sys; from pathlib import Path; "
                "p=Path('/var/lib/moonmind-controller/operations')/(sys.argv[1]+'.json'); "
                "print(hashlib.sha256(p.read_bytes()).hexdigest())",
                operation_id,
            ]
        )

    command(["docker", "network", "create", "--internal", network])
    source = None
    try:
        # The MoonMind host's project override must not reinterpret the
        # separately installed controller's project during metadata inspection.
        monkeypatch.setenv("COMPOSE_PROJECT_NAME", project)
        description = json.loads(
            command(
                [
                    "python3",
                    str(ROOT / "deploy/controller/bootstrap.py"),
                    "describe-installation",
                    "--state-dir",
                    str(state),
                    "--repo",
                    str(repo),
                ]
            )
        )
        assert description["identity"]["project"] == identity["project"]
        # An inert installed API container supplies observed repair sources;
        # no API health, application credentials or application apply is used.
        source = command(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--network=none",
                "--label",
                f"com.docker.compose.project={project}",
                "--label",
                "com.docker.compose.service=api",
                "--entrypoint",
                "python",
                candidate,
                "-c",
                "import time; time.sleep(240)",
            ]
        )
        command([*compose, "up", "-d", "--pull", "never", "--wait"])
        before = container()
        _, health = update._controller_call(url, secret, "GET", "/v1/healthz")
        assert "active-journal-transition" in health["capabilities"]
        seed("preserved-failure", failed=True)
        assert (state / "operations/preserved-failure.json").stat().st_uid == 0
        failed_digest = saved_digest("preserved-failure")
        (state / "controller-identity.json").unlink()

        # Exercise default endpoint discovery and its real root-owned metadata
        # repair before ordinary refresh; the application apply is a separate
        # journey, so this fixture observes the prepared controller endpoint.
        prepared = []
        monkeypatch.setattr(
            update,
            "_submit_via_controller",
            lambda _record, _repo, **kwargs: prepared.append(
                (_record["image"], kwargs["controller_url"])
            )
            or 0,
        )
        assert (
            update._submit_release(
                record, repo, controller_url="http://127.0.0.1:8472", secret_file=None
            )
            == 0
        )
        assert prepared == [(candidate_id, url)]
        restored_identity = json.loads((state / "controller-identity.json").read_text())
        assert {
            key: value
            for key, value in restored_identity.items()
            if key != "recordedAt"
        } == {key: value for key, value in identity.items() if key != "recordedAt"}
        identity = restored_identity
        assert (state / "controller-identity.json").stat().st_uid == 0
        assert (state / "controller-identity.json").stat().st_mode & 0o044 == 0o044

        after = container()
        assert before != after
        assert (
            command(["docker", "inspect", "--format", "{{.Image}}", after])
            == candidate_id
        )
        assert saved_digest("preserved-failure") == failed_digest
        assert json.loads((state / "controller-identity.json").read_text()) == identity
        assert secret_file.read_text().strip() == secret
        update._ensure_controller_journal_transition(
            record, repo, url, secret, refresh=True
        )
        assert container() == after

        # A rollback target still has journal support but lacks the metadata
        # lifecycle methods. Its application selection stays requested while
        # the controller retains the independently selected installed source.
        (state / "controller-identity.json").unlink()
        assert (
            update._submit_release(
                {"project": project, "image": previous_id},
                repo,
                controller_url="http://127.0.0.1:8472",
                secret_file=None,
            )
            == 0
        )
        assert prepared == [(candidate_id, url), (previous_id, url)]
        assert container() == after
        assert (
            command(["docker", "inspect", "--format", "{{.Image}}", after])
            == candidate_id
        )
        assert saved_digest("preserved-failure") == failed_digest

        # Failed-operation recovery uses the same privileged bootstrap path.
        # An unchanged live image cannot earn another application attempt.
        changed, detail = update._refresh_controller_to_requested(record, repo, url)
        assert changed is False and "already runs the requested release" in detail
        assert container() == after
        changed, detail = update._refresh_controller_to_requested(
            {"project": project, "image": previous_id}, repo, url
        )
        assert changed is True and detail == ""
        repaired = container()
        assert repaired != after
        assert (
            command(["docker", "inspect", "--format", "{{.Image}}", repaired])
            == previous_id
        )
        changed, detail = update._refresh_controller_to_requested(
            {"project": project, "image": previous_id}, repo, url
        )
        assert changed is False and "already runs the requested release" in detail
        assert container() == repaired
        assert saved_digest("preserved-failure") == failed_digest
        update._ensure_controller_journal_transition(
            record, repo, url, secret, refresh=True
        )
        after = container()
        assert (
            command(["docker", "inspect", "--format", "{{.Image}}", after])
            == candidate_id
        )

        # An open record belongs to the capable controller, even when a
        # later caller asks bootstrap for a different release.
        seed("active-work", failed=False)
        active_digest = saved_digest("active-work")
        (state / "controller-identity.json").unlink()
        assert (
            update._submit_release(
                {"project": project, "image": previous_id},
                repo,
                controller_url="http://127.0.0.1:8472",
                secret_file=None,
            )
            == 0
        )
        assert prepared == [(candidate_id, url), (previous_id, url), (previous_id, url)]
        assert container() == after
        assert saved_digest("active-work") == active_digest
        assert saved_digest("preserved-failure") == failed_digest
    finally:
        # Only this disposable test project and its fixture image are removed.
        if container():
            command(
                [
                    "docker",
                    "exec",
                    container(),
                    "chown",
                    "-R",
                    f"{os.getuid()}:{os.getgid()}",
                    "/var/lib/moonmind-controller",
                ],
                check=False,
            )
        command([*compose, "down", "--remove-orphans"], check=False)
        if source:
            command(["docker", "rm", "--force", source], check=False)
        command(["docker", "network", "rm", network], check=False)
        command(["docker", "image", "rm", previous], check=False)


@pytest.mark.parametrize("endpoint_kind", ["private", "remote", "alias-looking", "loopback"])
def test_helper_routes_private_and_host_endpoints_across_real_docker_networks(
    tmp_path, monkeypatch, endpoint_kind
):
    """Exercise real ensure/HTTP/TLS through the generated helper command.

    A bridge fixture stands in for a host/LAN endpoint. Its synthetic DNS
    mapping is test-only; Docker Desktop's actual host gateway is not qualified
    by this Linux Docker journey.
    """
    if os.environ.get("MOONMIND_TEST_JOURNAL_TRANSITION") != "1":
        pytest.skip("Real Docker journey is enabled in controller-journey CI")
    candidate = os.environ["MOONMIND_TEST_JOURNAL_APP_IMAGE"]
    bootstrap = load("route_bootstrap", ROOT / "deploy/controller/bootstrap.py")
    update = load(
        "route_update",
        ROOT / ".agents/skills/update-moonmind/scripts/update_release.py",
    )
    private = endpoint_kind == "private"
    host = {
        "private": "moonmind-controller",
        "remote": "controller.example",
        "alias-looking": "moonmind-controller",
        "loopback": "localhost",
    }[endpoint_kind]
    project = f"moonmind-test-helper-route-{uuid4().hex[:10]}"
    network = f"{project}_deployment-controller-network"
    repo = tmp_path / "deployment"
    state = repo / "deploy/state/controller"
    state.mkdir(parents=True)
    secret = bootstrap.ensure_secret(state).read_text().strip()
    identity = bootstrap.ensure_identity(
        state, repo, 8443, target_project=project, target_network=network
    )
    identity["project"] = f"{project}-controller"
    (state / "controller-identity.json").write_text(json.dumps(identity))
    bootstrap.render_compose_file(
        state_dir=state, repo=repo, image=candidate, port=8443,
        project=identity["project"], target_project=project, target_network=network,
    )
    # A capable active owner makes ensure probe and return without recreating
    # any deployment. Preserve and check that record across the real helper.
    operation = bootstrap.record_mod.OperationStore(state).begin(
        stack="moonmind", desired_image=candidate, source_revision="",
        operation_id="preserved-owner",
    )
    operation_path = state / "operations" / f"{operation['operationId']}.json"
    original = operation_path.read_bytes()
    certificate = tmp_path / "leaf.pem"
    command(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-days", "1", "-subj", f"/CN={host}",
            "-addext", f"subjectAltName=DNS:{host}",
            "-keyout", str(tmp_path / "leaf-key.pem"), "-out", str(certificate),
        ]
    )
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    monkeypatch.setenv("SSL_CERT_DIR", "")
    server_script = tmp_path / "endpoint.py"
    server_script.write_text(
        f"""import json, ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args): pass
    def do_GET(self):
        assert self.headers["Authorization"] == {f"Bearer {secret}"!r}
        print(json.dumps({{
            "path": self.path, "host": self.headers["Host"],
            "sni": getattr(self.connection, "requested_name", None),
            "tls": self.connection.version() if {not private!r} else None,
        }}), flush=True)
        self.send_response(200)
        self.end_headers()
        self.wfile.write({json.dumps({"capabilities": list(update._REQUIRED_CONTROLLER_CAPABILITIES)}).encode()!r})
server = ThreadingHTTPServer(("0.0.0.0", 8443), Handler)
if {not private!r}:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain("/fixture/leaf.pem", "/fixture/leaf-key.pem")
    context.set_servername_callback(
        lambda connection, name, _context: setattr(connection, "requested_name", name)
    )
    server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
"""
    )
    command(["docker", "network", "create", "--internal", network])
    server = None
    try:
        assert json.loads(command(["docker", "network", "inspect", network]))[0]["Internal"]
        server = command(
            [
                "docker", "run", "--detach", "--rm", "--user", "0:0",
                "--network", network if private else "bridge",
                *(["--network-alias", host] if private else []),
                "--mount", f"type=bind,source={tmp_path},target=/fixture,readonly",
                "--entrypoint", "python", candidate, "/fixture/endpoint.py",
            ]
        )
        command(
            [
                "docker", "exec", server, "python", "-c",
                (
                    "import socket,time\n"
                    "for attempt in range(50):\n"
                    "    try:\n"
                    "        socket.create_connection(('127.0.0.1',8443),.2).close(); break\n"
                    "    except OSError: time.sleep(.1)\n"
                    "else: raise RuntimeError('fixture did not start')"
                ),
            ]
        )
        server_network = network if private else "bridge"
        server_ip = json.loads(command(["docker", "inspect", server]))[0][
            "NetworkSettings"
        ]["Networks"][server_network]["IPAddress"]
        if not private:
            # Reproduce the isolation the old command mistakenly selected.
            result = command(
                [
                    "docker", "run", "--rm", "--network", network,
                    "--entrypoint", "python", candidate, "-c",
                    (
                        "import socket,sys\n"
                        "try: socket.create_connection((sys.argv[1],8443),2)\n"
                        "except OSError: print('isolated')\n"
                        "else: raise AssertionError('private link reached external fixture')"
                    ),
                    server_ip,
                ]
            )
            assert result == "isolated"
        bundle = {
            path.name: path.read_text()
            for path in (ROOT / "deploy/controller").glob("*.py")
        }
        with update._materialized_controller(bundle) as materialized:
            monkeypatch.setattr(
                update, "os", SimpleNamespace(**{**vars(update.os), "name": "nt"})
            )
            endpoint = f"{'http' if private else 'https'}://{host}:8443"
            helper = update._controller_bootstrap_command(
                materialized, {"project": project, "image": candidate}, repo,
                endpoint, source_image=candidate, privileged=private,
            )
            assert helper.count("--network") == 1
            assert helper[helper.index("--network") + 1] == server_network
            if not private:
                # Do not replace the selected URL: verify its HTTP Host,
                # certificate hostname and SNI through a test DNS mapping.
                destination = "host.docker.internal" if endpoint_kind == "loopback" else host
                helper[2:2] = ["--add-host", f"{destination}:{server_ip}"]
            command(helper)
        assert operation_path.read_bytes() == original
        assert json.loads(command(["docker", "network", "inspect", network]))[0]["Internal"]
        observations = [
            json.loads(line) for line in command(["docker", "logs", server]).splitlines()
        ]
        assert observations == [
            {
                "path": "/v1/healthz", "host": f"{host}:8443",
                "sni": None if private else host,
                "tls": None if private else "TLSv1.2",
            }
        ]
    finally:
        if server:
            command(["docker", "rm", "--force", server], check=False)
        command(["docker", "network", "rm", network], check=False)
