"""The host update entrypoint and Settings Operations share one controller operation.

The portable ``update_release.py`` entrypoint and the API's controller
client run against an in-process ``deploy/controller`` endpoint (production
threaded server, bearer check, operation store, reattach rules). Only Git,
the registry/Docker CLI calls, and the Compose applier are replaced.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.deployment_controller import (
    InProcessController,
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


def test_no_argument_host_refreshes_a_capable_controller_before_submission(
    installed, monkeypatch
):
    repo, controller = installed()
    revision = _git_checkout(repo)
    commands = _stub_host_docker(monkeypatch, revision)
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
        lambda *args, **kwargs: (200, {"capabilities": ["active-journal-transition"]}),
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


def test_explicit_host_controller_keeps_its_release_choice(installed, monkeypatch):
    repo, controller = installed()
    revision = _git_checkout(repo)
    commands = _stub_host_docker(monkeypatch, revision)
    monkeypatch.chdir(repo)

    assert update.main(["--controller-url", controller.url]) == 0

    assert not any(command[1] == "run" for command in commands)
    assert len(controller.applied) == 1


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
