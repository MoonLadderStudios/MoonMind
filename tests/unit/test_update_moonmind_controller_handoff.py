"""The host update entrypoint and Settings Operations share one controller operation.

The portable ``update_release.py`` entrypoint and the API's controller
client run against an in-process ``deploy/controller`` endpoint (production
threaded server, bearer check, operation store, reattach rules). Only Git,
the registry/Docker CLI calls, and the Compose applier are replaced.
"""

from __future__ import annotations

import importlib.util
import json
import runpy
import socket
import ssl
import subprocess
import sys
import threading
import urllib.error
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.deployment_controller import (
    InProcessController,
    failing_until,
    forget_controller_modules,
    install_controller_state,
    load_controller_modules,
    slow_applier,
)

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "update_release_handoff",
    ROOT / ".agents/skills/update-moonmind/scripts/update_release.py",
)
update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update)

SECRET = "handoff-test-secret-value"
DIGEST = "sha256:" + "a" * 64
IMAGE = f"ghcr.io/moonladderstudios/moonmind@{DIGEST}"


@pytest.fixture
def installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., tuple[Path, InProcessController]]]:
    load_controller_modules(monkeypatch)
    for name in (
        "MOONMIND_CONTROLLER_URL",
        "MOONMIND_CONTROLLER_SECRET_FILE",
        "MOONMIND_CONTROLLER_SECRET",
        "COMPOSE_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    started: list[InProcessController] = []

    def start(
        applier: Callable[[InProcessController, dict], object] | None = None,
    ) -> tuple[Path, InProcessController]:
        repo = tmp_path / "installed"
        state = repo / "deploy" / "state" / "controller"
        state.mkdir(parents=True, exist_ok=True)
        controller = InProcessController(state, secret=SECRET, applier=applier)
        started.append(controller)
        install_controller_state(state, port=controller.port, secret=SECRET)
        return repo, controller

    yield start
    for controller in started:
        controller.close()
    forget_controller_modules()


def _git_checkout(repo: Path) -> str:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], text=True
        ).strip()

    git("init", "-b", "main")
    git("config", "user.email", "qualification@example.invalid")
    git("config", "user.name", "Qualification")
    (repo / "docker-compose.yaml").write_text("services: {}\n")
    (repo / ".gitignore").write_text("deploy/state/\n")
    git("add", ".")
    git("commit", "-m", "source")
    git("remote", "add", "origin", str(repo))
    return git("rev-parse", "HEAD")


def _stub_host_docker(
    monkeypatch: pytest.MonkeyPatch, revision: str
) -> list[list[str]]:
    original_run = subprocess.run
    commands: list[list[str]] = []

    def command(args, **kwargs):
        if "describe-installation" in args:
            repo = args[args.index("--repo") + 1]
            return SimpleNamespace(
                returncode=0,
                stderr="",
                stdout=json.dumps(
                    {
                        "bindSources": {
                            "repo": repo,
                            "state": f"{repo}/deploy/state/controller",
                        },
                    }
                ),
            )
        if args[0] != "docker":
            return original_run(args, **kwargs)
        commands.append(list(args))
        if args[1:3] == ["image", "inspect"]:
            output = json.dumps(
                [
                    {
                        "RepoDigests": [IMAGE],
                        "Config": {
                            "Labels": {"org.opencontainers.image.revision": revision}
                        },
                    }
                ]
            )
        elif args[1:3] == ["compose", "config"]:
            output = json.dumps(
                {"name": "moonmind", "services": {"api": {}, "docker-proxy": {}}}
            )
        elif args[1] == "pull":
            output = ""
        elif args[1] == "ps":
            output = ""
        elif args[1] == "run":
            if update._CONTROLLER_SOURCE_EXPORT in args:
                output = json.dumps(
                    {
                        "bootstrap.py": "# requested release bootstrap",
                        "mounts.py": (ROOT / "deploy/controller/mounts.py").read_text(),
                    }
                )
            else:
                assert "ensure" in args
                assert "--user" in args and "0:0" in args
                assert "--network" in args
                assert "moonmind_deployment-controller-network" in args
                bootstrap_mount = next(
                    part for part in args if "target=/controller-bootstrap," in part
                )
                source = bootstrap_mount.split("source=", 1)[1].split(",", 1)[0]
                assert (Path(source) / "bootstrap.py").read_text() == (
                    "# requested release bootstrap"
                )
                assert (
                    "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock"
                    in args
                )
                output = ""
        else:
            raise AssertionError(f"unexpected host docker command: {args}")
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(update.subprocess, "run", command)
    return commands


@pytest.mark.parametrize("host_os", ["posix", "nt"])
def test_no_argument_host_refreshes_a_capable_controller_before_submission(
    installed, monkeypatch, host_os
):
    repo, controller = installed()
    revision = _git_checkout(repo)
    commands = _stub_host_docker(monkeypatch, revision)
    monkeypatch.setattr(
        update, "os", SimpleNamespace(**{**vars(update.os), "name": host_os})
    )
    monkeypatch.chdir(repo)

    assert update.main([]) == 0

    refreshes = [command for command in commands if "ensure" in command]
    assert len(refreshes) == 1
    refresh = refreshes[0]
    assert IMAGE in refresh
    assert f"http://moonmind-controller:{controller.port}" in refresh
    assert f"type=bind,source={repo},target=/deployment,readonly" in refresh
    state = repo / "deploy/state/controller"
    assert f"type=bind,source={state},target=/var/lib/moonmind-controller" in refresh
    assert refresh[refresh.index("--repo") + 1] == "/deployment"
    assert refresh[refresh.index("--state-dir") + 1] == "/var/lib/moonmind-controller"
    assert len(controller.applied) == 1


@pytest.mark.parametrize(
    "checkout,daemon_source",
    [
        ("/mnt/c/MoonMind", "/mnt/c/operator-installed-moonmind"),
        ("C:\\MoonMind", "/run/desktop/mnt/host/c/MoonMind"),
    ],
)
def test_refresh_preserves_installed_bind_sources_and_uses_linux_container_paths(
    monkeypatch, checkout, daemon_source
):
    repo = Path(checkout)
    commands = []
    bundle = {
        "bootstrap.py": "# requested release bootstrap",
        "mounts.py": (ROOT / "deploy/controller/mounts.py").read_text(),
    }
    monkeypatch.setattr(
        update,
        "_controller_identity",
        lambda _repo: {"project": "controller", "port": 8521, "targetNetwork": "link"},
    )
    monkeypatch.setattr(
        update,
        "_controller_call",
        lambda *args, **kwargs: (200, {"capabilities": list(update._REQUIRED_CONTROLLER_CAPABILITIES)}),
    )

    def run(command, **kwargs):
        commands.append(command)
        if "describe-installation" in command:
            return json.dumps(
                {
                    "bindSources": {
                        "repo": daemon_source,
                        "state": f"{daemon_source}/deploy/state/controller",
                    },
                }
            )
        if command[:2] == ["docker", "ps"]:
            return ""
        if update._CONTROLLER_SOURCE_EXPORT in command:
            return json.dumps(bundle)
        assert "ensure" in command
        mounts = [part for part in command if part.startswith("type=bind,source=")]
        assert f"type=bind,source={daemon_source},target=/deployment,readonly" in mounts
        assert (
            f"type=bind,source={daemon_source}/deploy/state/controller,"
            "target=/var/lib/moonmind-controller"
        ) in mounts
        bootstrap_mount = next(
            part for part in mounts if "target=/controller-bootstrap," in part
        )
        source = bootstrap_mount.split("source=", 1)[1].split(",", 1)[0]
        assert (Path(source) / "bootstrap.py").read_text() == bundle["bootstrap.py"]
        assert command[command.index("--repo") + 1] == "/deployment"
        assert (
            command[command.index("--state-dir") + 1] == "/var/lib/moonmind-controller"
        )
        return ""

    monkeypatch.setattr(update, "run", run)
    update._ensure_controller_journal_transition(
        {"project": "moonmind", "image": IMAGE},
        repo,
        "http://127.0.0.1:8521",
        SECRET,
        refresh=True,
    )
    assert len([command for command in commands if "ensure" in command]) == 1


@pytest.mark.parametrize("recovery_source", ["requested", "installed"])
def test_default_host_recovers_missing_identity_before_discovering_endpoint(
    installed, monkeypatch, recovery_source
):
    repo, controller = installed()
    revision = _git_checkout(repo)
    state = repo / "deploy/state/controller"
    saved = json.loads((state / "controller-identity.json").read_text())
    saved.update(targetNetwork="bespoke-link", targetProject="moonmind", repo=str(repo))
    (state / "controller-identity.json").unlink()
    (state / "controller-compose.yaml").write_text(
        "# Existing controller installation\n"
    )
    commands = _stub_host_docker(monkeypatch, revision)
    original_run = update.subprocess.run

    def run(args, **kwargs):
        if recovery_source == "installed" and args[:2] == ["docker", "ps"]:
            commands.append(list(args))
            return SimpleNamespace(returncode=0, stdout="installed-api", stderr="")
        if recovery_source == "installed" and args[:2] == ["docker", "inspect"]:
            commands.append(list(args))
            return SimpleNamespace(
                returncode=0,
                stderr="",
                stdout=json.dumps(
                    [
                        {
                            "Image": "sha256:installed-source",
                            "Config": {"Labels": {"com.docker.compose.service": "api"}},
                        }
                    ]
                ),
            )
        if (
            recovery_source == "installed"
            and update._CONTROLLER_SOURCE_EXPORT in args
            and ("identity-recovery" in args or "bind-discovery" in args)
            and IMAGE in args
        ):
            commands.append(list(args))
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        if args[0] == "docker" and "recover-identity" in args:
            commands.append(list(args))
            assert (
                IMAGE if recovery_source == "requested" else "sha256:installed-source"
            ) in args
            assert args[args.index("--network") + 1] == "none"
            assert args[args.index("--state-dir") + 1] == "/var/lib/moonmind-controller"
            (state / "controller-identity.json").write_text(json.dumps(saved))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[0] == "docker" and "ensure" in args:
            commands.append(list(args))
            assert "bespoke-link" in args
            assert f"http://moonmind-controller:{controller.port}" in args
            assert args[args.index("--image") + 1] == (
                IMAGE if recovery_source == "requested" else "sha256:installed-source"
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return original_run(args, **kwargs)

    monkeypatch.setattr(update.subprocess, "run", run)
    monkeypatch.chdir(repo)

    assert update.main([]) == 0
    assert len([command for command in commands if "recover-identity" in command]) == 1
    assert len([command for command in commands if "ensure" in command]) == 1
    assert len(controller.applied) == 1
    assert controller.store.load(controller.applied[0])["desired"]["image"] == IMAGE
    assert json.loads((state / "controller-identity.json").read_text()) == saved


def test_refresh_uses_the_shipped_adapter_for_the_new_bootstrap_bind(
    tmp_path, monkeypatch
):
    bundle = {
        "bootstrap.py": "# image-owned bootstrap",
        "mounts.py": "def resolve_bind_source(source):\n    return '/daemon/private-bootstrap'\n",
    }
    with update._materialized_controller(bundle) as bootstrap:
        monkeypatch.setattr(
            update,
            "run",
            lambda *args, **kwargs: json.dumps(
                {
                    "bindSources": {
                        "repo": "/installed-repo",
                        "state": "/installed-state",
                    },
                }
            ),
        )
        command = update._controller_bootstrap_container(
            bootstrap, tmp_path, IMAGE, network="link"
        )
    assert "type=bind,source=/installed-repo,target=/deployment,readonly" in command
    assert (
        "type=bind,source=/installed-state,target=/var/lib/moonmind-controller"
        in command
    )
    assert (
        "type=bind,source=/daemon/private-bootstrap,target=/controller-bootstrap,readonly"
        in command
    )


@pytest.mark.parametrize("host_os", ["posix", "nt"])
def test_explicit_host_controller_keeps_its_release_choice(
    installed, monkeypatch, host_os
):
    repo, controller = installed()
    revision = _git_checkout(repo)
    commands = _stub_host_docker(monkeypatch, revision)
    monkeypatch.setattr(
        update, "os", SimpleNamespace(**{**vars(update.os), "name": host_os})
    )
    monkeypatch.chdir(repo)

    assert update.main(["--controller-url", controller.url]) == 0

    assert not any(command[1] == "run" for command in commands)
    assert len(controller.applied) == 1


@pytest.mark.parametrize(
    "selection", ["endpoint", "endpoint-equals", "endpoint-env", "secret", "secret-env"]
)
def test_windows_explicit_controller_prerequisite_runs_in_linux_from_main(
    installed, monkeypatch, selection
):
    """Native Windows must not run the bounded POSIX lifecycle on host Python."""
    repo, controller = installed()
    revision = _git_checkout(repo)
    commands = _stub_host_docker(monkeypatch, revision)
    original_run = update.subprocess.run
    request = update._controller_call
    state = repo / "deploy/state/controller"
    identity = json.loads((state / "controller-identity.json").read_text())
    identity["targetNetwork"] = "operator-controller-link"
    (state / "controller-identity.json").write_text(json.dumps(identity))
    saved_state = {
        path: path.read_bytes() for path in state.rglob("*") if path.is_file()
    }
    selected_secret = repo / "selected-controller-bearer"
    selected_secret.write_text(SECRET + "\n")
    daemon_repo = "/run/desktop/mnt/host/c/operator-installed-moonmind"
    ensured = []
    health_calls = []

    def old_health(url, secret, method, path, *args, **kwargs):
        assert url == controller.url
        assert secret == SECRET
        if path == "/v1/healthz":
            health_calls.append(url)
            if not ensured:
                return 200, {"capabilities": ["active-journal-transition"]}
        if method == "POST":
            assert ensured
        return request(url, secret, method, path, *args, **kwargs)

    def command(args, **kwargs):
        if args[:2] == ["docker", "ps"]:
            commands.append(list(args))
            return SimpleNamespace(returncode=0, stdout="installed-api", stderr="")
        if args[:2] == ["docker", "inspect"]:
            commands.append(list(args))
            return SimpleNamespace(
                returncode=0,
                stderr="",
                stdout=json.dumps(
                    [
                        {
                            "Image": "sha256:installed-source",
                            "Config": {"Labels": {"com.docker.compose.service": "api"}},
                        }
                    ]
                ),
            )
        if "describe-installation" in args:
            return SimpleNamespace(
                returncode=0,
                stderr="",
                stdout=json.dumps(
                    {
                        "bindSources": {
                            "repo": daemon_repo,
                            "state": f"{daemon_repo}/deploy/state/controller",
                        },
                    }
                ),
            )
        if update._CONTROLLER_SOURCE_EXPORT in args:
            assert "sha256:installed-source" in args
        if "ensure" in args:
            # A host invocation would hit bootstrap._run_owned's POSIX guard.
            assert args[:2] == ["docker", "run"], args
            assert args[args.index("--user") + 1] == "0:0"
            assert args[args.index("--network") + 1] == "operator-controller-link"
            assert args[args.index("--entrypoint") + 2] == (
                "sha256:installed-source"
            )
            assert args[args.index("--image") + 1] == IMAGE
            assert "--prefer-requested" not in args
            assert args[args.index("--controller-url") + 1] == controller.url
            launcher = args.index(update._CONTROLLER_LOOPBACK_CONNECT)
            assert args[launcher + 1 : launcher + 3] == [
                "127.0.0.1", str(controller.port)
            ]
            assert args[args.index("--repo") + 1] == "/deployment"
            assert args[args.index("--state-dir") + 1] == (
                "/var/lib/moonmind-controller"
            )
            assert (
                f"type=bind,source={daemon_repo},target=/deployment,readonly" in args
            )
            assert (
                f"type=bind,source={daemon_repo}/deploy/state/controller,"
                "target=/var/lib/moonmind-controller"
            ) in args
            assert (
                "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock"
                in args
            )
            bootstrap_mount = next(
                part for part in args if "target=/controller-bootstrap," in part
            )
            source = bootstrap_mount.split("source=", 1)[1].split(",", 1)[0]
            assert (Path(source) / "bootstrap.py").is_file()
            assert SECRET not in " ".join(args)
            assert str(selected_secret) not in args
            ensured.append(list(args))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return original_run(args, **kwargs)

    monkeypatch.setattr(update, "_controller_call", old_health)
    monkeypatch.setattr(update.subprocess, "run", command)
    monkeypatch.setattr(
        update, "os", SimpleNamespace(**{**vars(update.os), "name": "nt"})
    )
    monkeypatch.chdir(repo)
    arguments = []
    if selection == "endpoint":
        arguments = ["--controller-url", controller.url]
    elif selection == "endpoint-equals":
        arguments = [f"--controller-url={controller.url}"]
    elif selection == "endpoint-env":
        monkeypatch.setenv("MOONMIND_CONTROLLER_URL", controller.url)
        monkeypatch.setattr(update, "_DEFAULT_CONTROLLER_URL", controller.url)
    elif selection == "secret":
        arguments = ["--controller-secret-file", str(selected_secret)]
    else:
        monkeypatch.setenv("MOONMIND_CONTROLLER_SECRET_FILE", str(selected_secret))

    assert update.main(arguments) == 0

    assert len(ensured) == 1
    assert all(
        "bind-discovery" in command
        for command in commands
        if update._CONTROLLER_SOURCE_EXPORT in command
    )
    assert len(health_calls) == 2
    assert len(controller.applied) == 1
    assert controller.store.load(controller.applied[0])["desired"]["image"] == IMAGE
    assert all(path.read_bytes() == content for path, content in saved_state.items())
    assert selected_secret.read_text() == SECRET + "\n"


@pytest.mark.parametrize(
    ("endpoint", "routed"),
    [
        ("http://127.0.0.1:8521", True),
        ("http://127.0.0.2:8521", True),
        ("http://localhost:9521/controller", True),
        ("http://localhost.:9521/controller", True),
        ("http://LOCALHOST/controller", True),
        ("http://localhost:0/controller", True),
        ("http://[::1]:9521/controller/", True),
        ("https://localhost/controller", True),
        ("https://controller.example:9521/api", False),
        ("http://192.168.1.2:9521/controller", False),
    ],
)
@pytest.mark.parametrize("first_source_failure", [None, "unsupported", "safe", "unsafe"])
def test_windows_explicit_prerequisite_preserves_selected_endpoint(
    installed, monkeypatch, endpoint, routed, first_source_failure
):
    repo, controller = installed()
    commands = []
    request = update._controller_call
    ensured = False
    identity = {"project": "operator-controller", "port": 8521, "targetNetwork": "link"}
    (repo / "deploy/state/controller/controller-identity.json").write_text(
        json.dumps(identity)
    )

    def call(url, secret, method, path, *args, **kwargs):
        assert url == endpoint
        assert secret == SECRET
        if path == "/v1/healthz" and not ensured:
            return 200, {"capabilities": []}
        return request(controller.url, secret, method, path, *args, **kwargs)

    def run(command, **kwargs):
        nonlocal ensured
        commands.append(command)
        if command[:3] == ["docker", "compose", "config"]:
            return json.dumps({"services": {"api": {}}})
        if command[:2] == ["docker", "ps"]:
            return "installed-api"
        if command[:2] == ["docker", "inspect"]:
            return json.dumps(
                [
                    {
                        "Image": "sha256:installed-source",
                        "Config": {"Labels": {"com.docker.compose.service": "api"}},
                    }
                ]
            )
        if update._CONTROLLER_SOURCE_EXPORT in command:
            if first_source_failure == "unsupported" and "sha256:installed-source" in command:
                return "{}"
            return json.dumps(
                {
                    "bootstrap.py": "# image-owned lifecycle",
                    "mounts.py": (ROOT / "deploy/controller/mounts.py").read_text(),
                }
            )
        if "describe-installation" in command:
            return json.dumps(
                {
                    "bindSources": {
                        "repo": str(repo),
                        "state": str(repo / "deploy/state/controller"),
                    }
                }
            )
        assert command[:2] == ["docker", "run"]
        assert "ensure" in command
        assert command[command.index("--controller-url") + 1] == endpoint
        assert (update._CONTROLLER_LOOPBACK_CONNECT in command) == routed
        helper_environment = dict(
            command[index + 1].split("=", 1)
            for index, value in enumerate(command)
            if value == "--env"
        )
        assert set(helper_environment) == (
            {"SSL_CERT_FILE", "SSL_CERT_DIR"} if endpoint.startswith("https:") else set()
        )
        assert all(
            value.startswith("/controller-bootstrap/host-trust/")
            for value in helper_environment.values()
        )
        assert command.count("--mount") == 4
        if routed:
            selected = update.urllib.parse.urlsplit(endpoint)
            launcher = command.index(update._CONTROLLER_LOOPBACK_CONNECT)
            assert command[launcher + 1 : launcher + 3] == [
                selected.hostname,
                str(
                    selected.port
                    if selected.port is not None
                    else (443 if selected.scheme == "https" else 80)
                ),
            ]
        assert command[command.index("--network") + 1] == "link"
        if first_source_failure in {"safe", "unsafe"} and "sha256:installed-source" in command:
            raise RuntimeError(
                "MOONMIND_CONTROLLER_RETRY_UNSAFE=cleanup unverified"
                if first_source_failure == "unsafe"
                else "Controller prerequisite recreation failed (exit 1)."
            )
        ensured = True
        return ""

    monkeypatch.setattr(update, "_controller_call", call)
    monkeypatch.setattr(update, "run", run)
    monkeypatch.setattr(
        update, "os", SimpleNamespace(**{**vars(update.os), "name": "nt"})
    )
    record = {
        "project": "moonmind",
        "image": IMAGE,
        "inputs": {},
        "context": {"idempotency_key": "host-update:windows-explicit"},
    }
    def submit():
        return update._submit_release(
            record,
            repo,
            controller_url=endpoint,
            controller_url_explicit=True,
            secret_file=None,
        )

    if first_source_failure == "unsafe":
        with pytest.raises(RuntimeError, match="RETRY_UNSAFE"):
            submit()
        assert len([command for command in commands if "ensure" in command]) == 1
        assert controller.applied == []
        return
    assert submit() == 0
    assert len([command for command in commands if "ensure" in command]) == (
        2 if first_source_failure == "safe" else 1
    )
    assert ensured
    assert controller.applied == ["host-windows-explicit"]


@pytest.mark.parametrize(
    ("scheme", "hostname", "certificate_host", "route_available", "trust_source"),
    [
        ("http", "LOCALHOST", None, True, "file"),
        ("https", "localhost", "localhost", True, "file"),
        ("https", "localhost", "localhost", True, "directory"),
        ("https", "localhost", "localhost", True, "wrong-hash"),
        ("https", "localhost", "localhost", True, "rejected"),
        ("https", "localhost", "localhost", True, "windows"),
        ("https", "localhost", "localhost", True, "windows-client-only"),
        ("https", "localhost", "host.docker.internal", True, "file"),
        ("https", "controller.example", "controller.example", True, "file"),
        ("http", "localhost", None, False, "file"),
    ],
)
def test_windows_loopback_launcher_preserves_http_and_tls_identity(
    tmp_path, monkeypatch, scheme, hostname, certificate_host, route_available,
    trust_source,
):
    """Execute the launcher and real bootstrap HTTP probe, not an argv mock."""
    requests = []
    server_names = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers["Host"], self.headers["Authorization"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"capabilities": ["test-route"]}')

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if scheme == "https":
        from tests.helpers.git_transport import _write_certificates

        ca, certificate, key = _write_certificates(tmp_path, certificate_host)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certificate, key)
        context.set_servername_callback(
            lambda _socket, name, _context: server_names.append(name)
        )
        server.socket = context.wrap_socket(server.socket, server_side=True)
        if trust_source == "rejected":
            rejected = tmp_path / "rejected.pem"
            subprocess.run(
                [
                    "openssl", "x509", "-in", str(ca), "-addreject", "serverAuth",
                    "-trustout", "-out", str(rejected),
                ],
                check=True,
            )
            ca = rejected
        capath = ""
        if trust_source in {"directory", "wrong-hash"}:
            directory = tmp_path / "host-ca-directory"
            directory.mkdir()
            hashed = subprocess.check_output(
                ["openssl", "x509", "-in", str(ca), "-hash", "-noout"], text=True
            ).strip()
            (directory / f"{hashed if trust_source == 'directory' else '00000000'}.0").write_bytes(
                ca.read_bytes()
            )
            capath = str(directory)
        monkeypatch.setattr(
            update.ssl, "get_default_verify_paths",
            lambda: SimpleNamespace(
                cafile=str(ca) if trust_source in {"file", "rejected"} else None,
                openssl_capath_env="SSL_CERT_DIR", openssl_capath="",
            ),
        )
        encoded = ssl.PEM_cert_to_DER_cert(ca.read_text()) if trust_source.startswith("windows") else None
        monkeypatch.setattr(
            update.ssl, "enum_certificates",
            lambda store: [
                (
                    encoded,
                    "x509_asn",
                    {
                        ssl.Purpose.CLIENT_AUTH.oid
                        if trust_source == "windows-client-only"
                        else ssl.Purpose.SERVER_AUTH.oid
                    },
                )
            ] if encoded is not None and store == "ROOT" else [],
            raising=False,
        )
        monkeypatch.setenv("SSL_CERT_DIR", capath)
        options = update._controller_ca_options(tmp_path / "bootstrap.py")
        for option in options[1::2]:
            name, value = option.split("=", 1)
            monkeypatch.setenv(
                name, value.replace("/controller-bootstrap", str(tmp_path))
            )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    endpoint = f"{scheme}://{hostname}:{port}/controller"
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('bootstrap_transport', "
        f"{str(ROOT / 'deploy/controller/bootstrap.py')!r})\n"
        "bootstrap = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(bootstrap)\n"
        "assert bootstrap.controller_capabilities(sys.argv[1], sys.argv[2]) "
        "== {'test-route'}\n"
    )
    launcher = tmp_path / "loopback-launcher.py"
    launcher.write_text(update._CONTROLLER_LOOPBACK_CONNECT)
    # Restore bootstrap's sibling-module import path after executing real code.
    monkeypatch.syspath_prepend(str(ROOT / "deploy/controller"))
    connect = socket.create_connection
    destinations = []

    def desktop_connect(address, *args, **kwargs):
        destinations.append(address)
        if address[0] == "host.docker.internal" and not route_available:
            raise OSError("Docker Desktop host route unavailable")
        assert address[0] in {"host.docker.internal", "controller.example"}
        return connect(("127.0.0.1", address[1]), *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", desktop_connect)
    monkeypatch.setattr(
        sys,
        "argv",
        ["-c", "localhost", str(port), str(probe), endpoint, SECRET],
    )
    try:
        if not route_available:
            with pytest.raises(urllib.error.URLError, match="host route unavailable"):
                runpy.run_path(str(launcher), run_name="__main__")
        elif certificate_host == "host.docker.internal" or trust_source in {
            "wrong-hash", "rejected", "windows-client-only"
        }:
            with pytest.raises(urllib.error.URLError) as failure:
                runpy.run_path(str(launcher), run_name="__main__")
            assert isinstance(failure.value.reason, ssl.SSLCertVerificationError)
        else:
            runpy.run_path(str(launcher), run_name="__main__")
            assert requests == [
                ("/controller/v1/healthz", f"{hostname}:{port}", f"Bearer {SECRET}")
            ]
        assert destinations == [
            (
                "controller.example"
                if hostname == "controller.example"
                else "host.docker.internal",
                port,
            )
        ]
        if scheme == "https":
            assert server_names == [hostname]
        if (
            not route_available
            or certificate_host == "host.docker.internal"
            or trust_source in {"wrong-hash", "rejected", "windows-client-only"}
        ):
            assert requests == []
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()


def test_windows_loopback_launcher_scopes_destination_and_preserves_socket_arguments(
    tmp_path, monkeypatch
):
    observed = []

    def connect(address, *args, **kwargs):
        observed.append((address, args, kwargs))
        return "connected"

    monkeypatch.setattr(socket, "create_connection", connect)
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import socket, sys\n"
        "assert sys.argv == [__file__, 'unchanged']\n"
        "for address in [('LOCALHOST', 443), ('localhost', 80), ('remote.example', 443)]:\n"
        "    assert socket.create_connection(address, 5, source_address=('127.0.0.1', 0), "
        "all_errors=True) == 'connected'\n"
    )
    monkeypatch.setattr(
        sys, "argv", ["-c", "localhost", "443", str(probe), "unchanged"]
    )
    launcher = tmp_path / "loopback-launcher.py"
    launcher.write_text(update._CONTROLLER_LOOPBACK_CONNECT)

    runpy.run_path(str(launcher), run_name="__main__")

    assert observed == [
        (address, (5,), {"source_address": ("127.0.0.1", 0), "all_errors": True})
        for address in [
            ("host.docker.internal", 443),
            ("localhost", 80),
            ("remote.example", 443),
        ]
    ]


def test_windows_helper_carries_only_effective_public_ca_trust(tmp_path, monkeypatch):
    from tests.helpers.git_transport import _write_certificates

    ca, _certificate, key = _write_certificates(tmp_path, "localhost")
    trusted = tmp_path / "trusted.pem"
    subprocess.run(
        [
            "openssl", "x509", "-in", str(ca), "-addreject", "serverAuth",
            "-trustout", "-out", str(trusted),
        ],
        check=True,
    )
    original = trusted.read_bytes()
    legacy = ca.read_bytes().replace(b"CERTIFICATE", b"X509 CERTIFICATE")
    configured = tmp_path / "configured.pem"
    configured.write_bytes(original + key.read_bytes() + legacy)
    source_dirs = [tmp_path / "first", tmp_path / "second"]
    for directory in source_dirs:
        directory.mkdir()
        (directory / "ABCDEF01.0").write_bytes(original + key.read_bytes())
        (directory / "ignored.pem").write_bytes(ca.read_bytes())
    monkeypatch.setenv("SSL_CERT_DIR", ";".join(map(str, source_dirs)))
    monkeypatch.setattr(
        update, "os", SimpleNamespace(**{**vars(update.os), "name": "nt", "pathsep": ";"})
    )
    monkeypatch.setattr(
        update.ssl,
        "get_default_verify_paths",
        lambda: SimpleNamespace(
            cafile=str(configured), openssl_capath_env="SSL_CERT_DIR", openssl_capath=""
        ),
    )
    encoded = ssl.PEM_cert_to_DER_cert(ca.read_text())
    stores = []

    def enumerate_certificates(store):
        stores.append(store)
        return [
            (encoded, "x509_asn", {ssl.Purpose.SERVER_AUTH.oid}),
            (encoded, "x509_asn", {ssl.Purpose.CLIENT_AUTH.oid}),
            (encoded, "pkcs_7_asn", True),
        ] if store == "ROOT" else []

    monkeypatch.setattr(update.ssl, "enum_certificates", enumerate_certificates, raising=False)
    bootstrap = tmp_path / "materialized" / "bootstrap.py"
    bootstrap.parent.mkdir()

    options = update._controller_ca_options(bootstrap)

    assert stores == ["CA", "ROOT"]
    assert options[::2] == ["--env", "--env"]
    settings = dict(option.split("=", 1) for option in options[1::2])
    trust_file = Path(
        settings["SSL_CERT_FILE"].replace("/controller-bootstrap", str(bootstrap.parent))
    )
    assert trust_file.read_bytes() == ca.read_bytes() + original + legacy
    directories = [
        Path(path.replace("/controller-bootstrap", str(bootstrap.parent)))
        for path in settings["SSL_CERT_DIR"].split(":")
    ]
    assert len(directories) == 2
    for directory in directories:
        assert [path.name for path in directory.iterdir()] == ["abcdef01.0"]
        assert (directory / "abcdef01.0").read_bytes() == original
    assert all(
        b"PRIVATE KEY" not in path.read_bytes()
        for path in bootstrap.parent.rglob("*")
        if path.is_file()
    )


@pytest.mark.parametrize("selection", ["endpoint", "secret"])
@pytest.mark.parametrize("host_os", ["posix", "nt"])
def test_explicit_host_controller_keeps_its_release_after_a_failed_operation(
    installed, monkeypatch, selection, host_os
):
    repo, controller = installed(failing_until(lambda: False))
    monkeypatch.setattr(
        update, "os", SimpleNamespace(**{**vars(update.os), "name": host_os})
    )
    monkeypatch.setattr(
        update, "run", lambda *args, **kwargs: json.dumps({"services": {"api": {}}})
    )
    monkeypatch.setattr(
        update,
        "_refresh_controller_to_requested",
        lambda *args: pytest.fail("Explicit controller release was replaced"),
    )
    record = {
        "project": "moonmind",
        "image": IMAGE,
        "inputs": {},
        "context": {"idempotency_key": "host-update:explicit-failed"},
    }
    with pytest.raises(RuntimeError, match="Expecting value"):
        update._submit_release(
            record,
            repo,
            controller_url=controller.url,
            controller_url_explicit=selection == "endpoint",
            secret_file=(
                str(update._default_controller_secret_file(repo))
                if selection == "secret"
                else None
            ),
        )
    recorded = controller.store.load("host-explicit-failed")
    assert recorded["attemptGroup"] == 1
    assert recorded["desired"]["image"] == IMAGE
    assert set(controller.applied) == {"host-explicit-failed"}


def test_no_argument_host_command_submits_the_controller_operation(
    installed: Callable[..., tuple[Path, InProcessController]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, controller = installed()
    revision = _git_checkout(repo)
    (repo / "docker-compose.yaml").write_text("services: {api: {}}\n")  # dirty
    commands = _stub_host_docker(monkeypatch, revision)
    monkeypatch.chdir(repo)

    assert update.main([]) == 0

    submission = next((repo / "deploy/state/release-submissions").glob("*.json"))
    operation_id = f"host-{submission.stem}"
    assert controller.applied == [operation_id]
    recorded = controller.store.load(operation_id)
    assert recorded["status"] == "succeeded"
    assert recorded["desired"]["image"] == IMAGE
    assert recorded["desired"]["sourceRevision"] == revision
    assert recorded["target"]["projectDir"] == str(repo)
    assert recorded["target"]["services"] == ["api"]
    # One-offs only export/bootstrap the standalone controller; none
    # launches an application-owned deployment updater.
    assert all(
        update._CONTROLLER_SOURCE_EXPORT in item or "ensure" in item
        for item in commands
        if item[1] == "run"
    )
    # Resuming the same submission observes the same operation; nothing reapplies.
    assert update.main(["--resume", submission.stem]) == 0
    assert controller.applied == [operation_id]
    # The dirty checkout is untouched.
    assert (repo / "docker-compose.yaml").read_text() == "services: {api: {}}\n"


@pytest.mark.parametrize("reachable", [True, False])
def test_host_refreshes_old_controller_before_submitting_without_api_or_worker(
    installed, monkeypatch, reachable
):
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_WORKER_IMAGE", "ghcr.io/org/moonmind:explicit-old"
    )
    repo, controller = installed()
    request = update._controller_call
    bootstrapped = False
    commands = []

    def old_health(url, secret, method, path, *args, **kwargs):
        if path == "/v1/healthz" and not bootstrapped:
            if not reachable:
                raise update.ControllerUnreachableError(
                    "controller recreation interrupted"
                )
            return 200, {"status": "ok"}
        if method == "POST":
            assert bootstrapped
        return request(url, secret, method, path, *args, **kwargs)

    def run(command, **kwargs):
        nonlocal bootstrapped
        commands.append(command)
        if command[:3] == ["docker", "compose", "config"]:
            return json.dumps({"services": {"api": {}}})
        if command[:2] == ["docker", "ps"]:
            return "installed-api"
        if command[:2] == ["docker", "inspect"]:
            return json.dumps(
                [
                    {
                        "Image": "sha256:source",
                        "Config": {"Labels": {"com.docker.compose.service": "api"}},
                    }
                ]
            )
        if command[:2] == ["docker", "run"]:
            assert "sha256:source" in command
            assert "--network=none" in command
            return json.dumps({"bootstrap.py": "# shipped host lifecycle source"})
        assert "ensure" in command
        assert controller.applied == []
        assert Path(command[1]).read_text() == "# shipped host lifecycle source"
        bootstrapped = True
        return ""

    monkeypatch.setattr(update, "_controller_call", old_health)
    monkeypatch.setattr(update, "run", run)
    record = {
        "project": "moonmind",
        "image": IMAGE,
        "inputs": {},
        "context": {"idempotency_key": "host-update:bootstrap-test"},
    }
    assert (
        update._submit_via_controller(
            record, repo, controller_url=controller.url, secret_file=None
        )
        == 0
    )
    assert bootstrapped
    assert len(controller.applied) == 1


@pytest.mark.parametrize(
    ("controller_changed", "first_retry_arrives"),
    [(True, True), (True, False), (False, True)],
)
def test_host_retries_with_the_requested_release_controller_after_installed_one_fails(
    installed, monkeypatch, controller_changed, first_retry_arrives
):
    """A capable installed controller can still carry the bug its release fixes."""
    refreshed = False
    repo, controller = installed(failing_until(lambda: refreshed))
    monkeypatch.setattr(update, "_sleep", lambda _seconds: None)
    ensures = []
    retry_posts = []
    request = update._controller_call

    def recreated_controller(url, secret, method, path, *args, **kwargs):
        if path.endswith("/retry"):
            retry_posts.append(path)
            if not first_retry_arrives and len(retry_posts) == 1:
                # The just-recreated controller refused the connection.
                raise update.ControllerUnreachableError("connection refused")
        return request(url, secret, method, path, *args, **kwargs)

    monkeypatch.setattr(update, "_controller_call", recreated_controller)

    def run(command, **kwargs):
        nonlocal refreshed
        if "describe-installation" in command:
            return json.dumps(
                {
                    "bindSources": {
                        "repo": str(repo),
                        "state": str(repo / "deploy/state/controller"),
                    },
                }
            )
        if command[:3] == ["docker", "compose", "config"]:
            return json.dumps({"services": {"api": {}}})
        if command[:2] == ["docker", "ps"]:
            return "installed-api"
        if command[:2] == ["docker", "inspect"]:
            return json.dumps(
                [
                    {
                        "Image": "sha256:installed",
                        "Config": {"Labels": {"com.docker.compose.service": "api"}},
                    }
                ]
            )
        if update._CONTROLLER_SOURCE_EXPORT in command:
            return json.dumps(
                {
                    "bootstrap.py": "# shipped host lifecycle source",
                    "mounts.py": (ROOT / "deploy/controller/mounts.py").read_text(),
                }
            )
        assert command[:2] == ["docker", "run"]
        assert "ensure" in command and "--user" in command and "0:0" in command
        assert (
            command[command.index("--state-dir") + 1] == "/var/lib/moonmind-controller"
        )
        assert command[command.index("--repo") + 1] == "/deployment"
        assert f"http://moonmind-controller:{controller.port}" in command
        assert command[command.index("--image") + 1] == IMAGE
        assert "--prefer-requested" in command
        # Only a terminal failure is handed to the requested release.
        assert controller.store.load("host-refresh-test")["status"] == "failed"
        ensures.append(command)
        refreshed = controller_changed
        receipt = {"refreshed": controller_changed, "image": "sha256:requested"}
        return f"Controller ready.\nMOONMIND_CONTROLLER_REFRESH={json.dumps(receipt)}\n"

    monkeypatch.setattr(update, "run", run)
    record = {
        "project": "moonmind",
        "image": IMAGE,
        "inputs": {},
        "context": {"idempotency_key": "host-update:refresh-test"},
    }
    submit = lambda: update._submit_via_controller(  # noqa: E731
        record, repo, controller_url=controller.url, secret_file=None
    )
    if not controller_changed:
        with pytest.raises(RuntimeError, match="Expecting value") as failure:
            submit()
        assert "already runs the requested release" in str(failure.value)
        assert len(ensures) == 1
        assert retry_posts == []
        assert controller.store.load("host-refresh-test")["attemptGroup"] == 1
        return
    assert submit() == 0
    assert len(ensures) == 1
    assert len(retry_posts) == (1 if first_retry_arrives else 2)
    recorded = controller.store.load("host-refresh-test")
    assert (recorded["status"], recorded["attemptGroup"]) == ("succeeded", 2)
    # The original failure stays in the operation record.
    assert "Expecting value" in recorded["attempts"][0]["error"]


def test_host_repairs_the_controller_with_the_requested_source_when_installed_fails(
    installed, monkeypatch
):
    """An installed source that cannot repair the controller is not the last word.

    A newer release carries repairs the installed one lacks, such as
    replacing a stopped controller whose link network was recreated.
    """
    repo, controller = installed()
    request = update._controller_call
    bootstrapped = False
    attempted = []

    def old_health(url, secret, method, path, *args, **kwargs):
        if path == "/v1/healthz" and not bootstrapped:
            raise update.ControllerUnreachableError("controller is stopped")
        return request(url, secret, method, path, *args, **kwargs)

    def run(command, **kwargs):
        nonlocal bootstrapped
        if command[:3] == ["docker", "compose", "config"]:
            return json.dumps({"services": {"api": {}}})
        if command[:2] == ["docker", "ps"]:
            return "installed-api"
        if command[:2] == ["docker", "inspect"]:
            return json.dumps(
                [
                    {
                        "Image": "sha256:installed",
                        "Config": {"Labels": {"com.docker.compose.service": "api"}},
                    }
                ]
            )
        if command[:2] == ["docker", "run"]:
            source = "sha256:installed" if "sha256:installed" in command else IMAGE
            assert source in command
            return json.dumps({"bootstrap.py": f"# {source} lifecycle"})
        assert "ensure" in command
        source = Path(command[1]).read_text()
        attempted.append(source)
        if "sha256:installed" in source:
            raise RuntimeError("Controller prerequisite recreation failed (exit 1).")
        bootstrapped = True
        return ""

    monkeypatch.setattr(update, "_controller_call", old_health)
    monkeypatch.setattr(update, "run", run)
    record = {
        "project": "moonmind",
        "image": IMAGE,
        "inputs": {},
        "context": {"idempotency_key": "host-update:fallback-test"},
    }
    assert (
        update._submit_via_controller(
            record, repo, controller_url=controller.url, secret_file=None
        )
        == 0
    )
    assert attempted == ["# sha256:installed lifecycle", f"# {IMAGE} lifecycle"]
    assert len(controller.applied) == 1


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_explicit_controller_without_request_identity_is_refreshed(
    installed, monkeypatch, platform
):
    # A controller predating request identity answers a new `latest` request
    # with an earlier success, so journal transition alone is not enough.
    repo, controller = installed()
    refreshed = []
    sources = []
    monkeypatch.setattr(
        update, "os", SimpleNamespace(**{**vars(update.os), "name": "nt" if platform == "win32" else "posix"})
    )
    monkeypatch.setattr(
        update, "_controller_source_images", lambda *a, **k: ["sha256:installed-source"]
    )

    def health(url, secret, method, path, *args, **kwargs):
        if refreshed:
            return 200, {"capabilities": [
                "active-journal-transition", "request-identity",
                "unversioned-routing-cutover",
            ]}
        return 200, {"capabilities": ["active-journal-transition"]}

    def run(command, **kwargs):
        if "describe-installation" in command:
            return json.dumps(
                {"bindSources": {"repo": "/installed", "state": "/state"}}
            )
        if command[:2] == ["docker", "run"] and "ensure" not in command:
            sources.append(command)
            return json.dumps(
                {
                    "bootstrap.py": "# lifecycle",
                    "mounts.py": "def resolve_bind_source(source):\n    return '/bootstrap'\n",
                }
            )
        assert "ensure" in command
        refreshed.append(command)
        return ""

    monkeypatch.setattr(update, "_controller_call", health)
    monkeypatch.setattr(update, "run", run)
    update._ensure_controller_journal_transition(
        {"project": "moonmind", "image": IMAGE}, repo, controller.url, SECRET
    )
    assert len(refreshed) == 1
    command = refreshed[0]
    assert command[command.index("--image") + 1] == IMAGE
    if platform == "win32":
        assert "bind-discovery" in sources[0]
        assert command[:2] == ["docker", "run"]
        assert "sha256:installed-source" in command
        assert "type=bind,source=/installed,target=/deployment,readonly" in command
        assert "type=bind,source=/state,target=/var/lib/moonmind-controller" in command
        assert command[command.index("--repo") + 1] == "/deployment"
        assert command[command.index("--controller-url") + 1] == controller.url
        assert update._CONTROLLER_LOOPBACK_CONNECT in command
    else:
        assert command[:2] != ["docker", "run"]
        assert command[command.index("--controller-url") + 1] == controller.url
    assert controller.applied == []


def test_host_reports_the_prerequisite_failure_when_no_source_repairs_it(
    installed, monkeypatch
):
    repo, controller = installed()

    def unreachable(url, secret, method, path, *args, **kwargs):
        raise update.ControllerUnreachableError("controller is stopped")

    def run(command, **kwargs):
        if command[:2] == ["docker", "ps"]:
            return ""
        if command[:2] == ["docker", "run"]:
            return json.dumps({"bootstrap.py": "# lifecycle"})
        assert "ensure" in command
        raise RuntimeError("Controller prerequisite recreation failed (exit 1).")

    monkeypatch.setattr(update, "_controller_call", unreachable)
    monkeypatch.setattr(update, "run", run)
    with pytest.raises(RuntimeError, match="recreation failed"):
        update._ensure_controller_journal_transition(
            {"project": "moonmind", "image": IMAGE}, repo, controller.url, SECRET
        )
    assert controller.applied == []


def test_host_lost_acknowledgment_observes_instead_of_resubmitting(
    installed: Callable[..., tuple[Path, InProcessController]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    repo, controller = installed(slow_applier(release))
    monkeypatch.setattr(update, "_CONTROLLER_SUBMIT_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(update, "_sleep", lambda _seconds: release.set())
    monkeypatch.setattr(
        update,
        "run",
        lambda args, **kwargs: json.dumps(
            {"name": "moonmind", "services": {"api": {}}}
        ),
    )
    record = {
        "project": "moonmind",
        "image": IMAGE,
        "inputs": {"sourceRevision": "rev1", "reason": "test"},
        "context": {"idempotency_key": "host-update:sub-1"},
    }
    try:
        assert (
            update._submit_via_controller(
                record,
                repo,
                controller_url=f"http://127.0.0.1:{controller.port}",
                secret_file=None,
            )
            == 0
        )
    finally:
        release.set()
    assert controller.applied == ["host-sub-1"]


def test_host_and_settings_operations_duplicates_have_one_mutation_owner(
    installed: Callable[..., tuple[Path, InProcessController]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.workflows.skills import deployment_controller as operations

    release = threading.Event()
    repo, controller = installed(slow_applier(release))
    endpoint = operations.ControllerEndpoint(
        base_url=f"http://127.0.0.1:{controller.port}", secret=SECRET
    )
    monkeypatch.setattr(operations, "CONTROLLER_SUBMIT_TIMEOUT_SECONDS", 0.3)
    try:
        ui = operations.submit_controller_update(
            endpoint, operation_id="ui-1", stack="moonmind", desired_image=IMAGE
        )
        assert (ui["operationId"], ui["status"]) == ("ui-1", "applying")

        observed: list[str] = []
        monkeypatch.setattr(update, "_sleep", lambda _seconds: release.set())
        monkeypatch.setattr(
            update,
            "run",
            lambda args, **kwargs: json.dumps(
                {"name": "moonmind", "services": {"api": {}}}
            ),
        )
        original_print = print

        def capture(*args, **kwargs):
            observed.append(" ".join(str(arg) for arg in args))
            original_print(*args, **kwargs)

        monkeypatch.setattr("builtins.print", capture)
        rc = update._submit_via_controller(
            {
                "project": "moonmind",
                "image": IMAGE,
                "inputs": {"sourceRevision": "", "reason": "host"},
                "context": {"idempotency_key": "host-update:sub-2"},
            },
            repo,
            controller_url=f"http://127.0.0.1:{controller.port}",
            secret_file=None,
        )
    finally:
        release.set()
    assert rc == 0
    # The host reattached to the operation Settings Operations started.
    assert "Controller operation: ui-1" in observed
    assert controller.applied == ["ui-1"]
    assert [op["operationId"] for op in controller.store.list_terminal()] == ["ui-1"]


def test_host_controller_calls_never_traverse_an_ambient_proxy(
    installed: Callable[..., tuple[Path, InProcessController]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deployment-owned bearer goes only to the controller endpoint."""
    _repo, controller = installed()
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)

    status, health = update._controller_call(
        controller.url, SECRET, "GET", "/v1/healthz", timeout=5
    )

    assert status == 200
    assert "active-journal-transition" in health["capabilities"]


@pytest.mark.parametrize("operation", ["ensure", "refresh"])
def test_uncertain_controller_state_refuses_alternate_source_retry(
    installed, monkeypatch, operation
):
    repo, controller = installed()
    attempted = []
    monkeypatch.setattr(
        update, "_controller_bootstrap_command", lambda *a, **k: ["bootstrap", "ensure"]
    )
    monkeypatch.setattr(update, "_controller_call", lambda *a, **k: (200, {}))
    monkeypatch.setattr(
        update, "_controller_source_images", lambda *a, **k: ["first", "second"]
    )
    monkeypatch.setattr(
        update, "_controller_sources", lambda *a, **k: {"bootstrap.py": "# lifecycle"}
    )

    def run(command, **kwargs):
        attempted.append(command)
        raise RuntimeError(
            "prerequisite failed: MOONMIND_CONTROLLER_RETRY_UNSAFE=state unverified"
        )

    monkeypatch.setattr(update, "run", run)
    record = {"project": "moonmind", "image": IMAGE}
    if operation == "ensure":
        with pytest.raises(RuntimeError, match="RETRY_UNSAFE"):
            update._ensure_controller_journal_transition(
                record, repo, controller.url, SECRET
            )
    else:
        changed, reason = update._refresh_controller_to_requested(
            record, repo, controller.url
        )
        assert not changed
        assert "RETRY_UNSAFE" in reason
    assert len(attempted) == 1
