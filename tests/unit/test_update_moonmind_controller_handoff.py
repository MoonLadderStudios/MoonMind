"""The host update entrypoint and Settings Operations share one controller operation.

The portable ``update_release.py`` entrypoint and the API's controller
client run against an in-process ``deploy/controller`` endpoint (production
threaded server, bearer check, operation store, reattach rules). Only Git,
the registry/Docker CLI calls, and the Compose applier are replaced.
"""

from __future__ import annotations

import importlib
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
        else:
            raise AssertionError(f"unexpected host docker command: {args}")
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(update.subprocess, "run", command)
    return commands


def test_no_argument_host_command_submits_the_controller_operation(
    installed: Callable[..., tuple[Path, InProcessController]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, controller = installed()
    revision = _git_checkout(repo)
    (repo / "docker-compose.yaml").write_text("services: {api: {}}\n")  # dirty
    commands = _stub_host_docker(monkeypatch, revision)
    monkeypatch.chdir(repo)
    # An operator HTTP(S) proxy never carries the controller's bearer secret.
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)

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
    # No application-owned updater is launched from the target image.
    assert not any(item[1] == "run" for item in commands)
    # Resuming the same submission observes the same operation; nothing reapplies.
    assert update.main(["--resume", submission.stem]) == 0
    assert controller.applied == [operation_id]
    # The dirty checkout is untouched.
    assert (repo / "docker-compose.yaml").read_text() == "services: {api: {}}\n"


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


CONTROLLER_MANIFEST = '{"schemaVersion": 2, "mediaType": "index"}'


def _stub_controller_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    revision: str,
    *,
    publishes_controller: bool = True,
    applier: Callable[[InProcessController, dict], object] | None = None,
) -> tuple[list[list[str]], list[str], list[InProcessController]]:
    """Run the shipped bootstrap with only Docker replaced.

    The host entrypoint launches ``<repo>/deploy/controller/bootstrap.py``;
    the stub runs the same shipped module in process. Registry resolution
    answers for the published controller image, and ``compose up`` starts
    the real controller endpoint on the port bootstrap recorded.
    """
    import sys

    commands = _stub_host_docker(monkeypatch, revision)
    docker_stub = update.subprocess.run
    resolved: list[str] = []
    started: list[InProcessController] = []
    bootstrap_path = repo / "deploy" / "controller" / "bootstrap.py"
    bootstrap_path.parent.mkdir(parents=True, exist_ok=True)
    bootstrap_path.write_text("# shipped with the checkout\n")

    def command(args, **kwargs):
        args = [str(part) for part in args]
        if args[:2] == [sys.executable, str(bootstrap_path)]:
            bootstrap = importlib.import_module("bootstrap")
            try:
                code = bootstrap.main(args[2:], env={})
            except Exception as exc:  # the real CLI exits non-zero
                print(f"bootstrap: {exc}")
                code = 1
            return SimpleNamespace(returncode=code, stdout="", stderr="")
        if args[:5] == ["docker", "buildx", "imagetools", "inspect", "--raw"]:
            commands.append(args)
            resolved.append(args[5])
            if not publishes_controller:
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="ERROR: manifest unknown"
                )
            return SimpleNamespace(returncode=0, stdout=CONTROLLER_MANIFEST, stderr="")
        if args[:3] == ["docker", "network", "inspect"]:
            commands.append(args)
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")
        if args[:2] == ["docker", "compose"] and "up" in args:
            commands.append(args)
            state = repo / "deploy" / "state" / "controller"
            identity = json.loads((state / "controller-identity.json").read_text())
            secret = (state / "secrets" / "controller-bearer").read_text().strip()
            started.append(
                InProcessController(
                    state, secret=secret, applier=applier, port=identity["port"]
                )
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:3] == ["docker", "ps", "-a"]:
            commands.append(args)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return docker_stub(args, **kwargs)

    monkeypatch.setattr(update.subprocess, "run", command)
    return commands, resolved, started


@pytest.fixture
def fresh_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    load_controller_modules(monkeypatch)
    for name in (
        "MOONMIND_CONTROLLER_URL",
        "MOONMIND_CONTROLLER_SECRET_FILE",
        "MOONMIND_CONTROLLER_SECRET",
        "MOONMIND_CONTROLLER_IMAGE",
        "MOONMIND_DEPLOYMENT_CONTROLLER_NETWORK",
        "COMPOSE_PROJECT_NAME",
        "COMPOSE_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    sys_path = str(ROOT / "deploy" / "controller")
    monkeypatch.syspath_prepend(sys_path)
    import sys

    sys.modules.pop("bootstrap", None)
    repo = tmp_path / "installed"
    repo.mkdir()
    yield repo
    sys.modules.pop("bootstrap", None)
    forget_controller_modules()


@pytest.mark.parametrize("explicit_image", [None, "registry.example/site-ctl:pinned"])
def test_bare_host_command_installs_the_release_controller_and_submits_through_it(
    fresh_install: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit_image: str | None,
) -> None:
    """A default install with no controller ends with one installed controller.

    The no-argument entrypoint installs and starts the controller published
    with the selected release (or the deployment's explicit image), then
    submits through it. No application-owned updater is ever launched.
    """
    repo = fresh_install
    revision = _git_checkout(repo)
    if explicit_image:
        monkeypatch.setenv("MOONMIND_CONTROLLER_IMAGE", explicit_image)
    commands, resolved, started = _stub_controller_bootstrap(
        monkeypatch, repo, revision
    )
    monkeypatch.chdir(repo)
    try:
        assert update.main([]) == 0

        requested = explicit_image or (
            f"ghcr.io/moonladderstudios/moonmind-controller:sha-{revision}"
        )
        assert resolved == [requested]
        state = repo / "deploy" / "state" / "controller"
        rendered = (state / "controller-compose.yaml").read_text()
        repository = requested.rsplit(":", 1)[0]
        assert f"image: {repository}@sha256:" in rendered
        identity = json.loads((state / "controller-identity.json").read_text())
        # Bootstrap derived this deployment's endpoint; nothing was declared.
        assert identity["targetProject"] == "moonmind"
        assert len(started) == 1 and started[0].port == identity["port"]
        controller = started[0]
        submission = next((repo / "deploy/state/release-submissions").glob("*.json"))
        operation_id = f"host-{submission.stem}"
        assert controller.applied == [operation_id]
        assert controller.store.load(operation_id)["desired"]["image"] == IMAGE
        # No application-owned updater is launched from the target image.
        assert not any(
            item[1] == "run" or "deployment_release" in " ".join(item)
            for item in commands
        )

        # The installed controller is reused: a resume neither reinstalls
        # nor reapplies.
        installs = len([item for item in commands if "up" in item])
        assert update.main(["--resume", submission.stem]) == 0
        assert len([item for item in commands if "up" in item]) == installs
        assert controller.applied == [operation_id]
    finally:
        for controller in started:
            controller.close()


@pytest.mark.parametrize("resume", [False, True])
def test_unavailable_controller_image_fails_without_another_updater(
    fresh_install: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    resume: bool,
) -> None:
    """A controller that cannot be installed is a truthful failure.

    Absence never becomes permission to launch the application-owned
    updater, including when an earlier submission is resumed.
    """
    repo = fresh_install
    revision = _git_checkout(repo)
    commands, _resolved, started = _stub_controller_bootstrap(
        monkeypatch, repo, revision, publishes_controller=False
    )
    monkeypatch.chdir(repo)
    args: list[str] = []
    if resume:
        submissions = repo / "deploy" / "state" / "release-submissions"
        submissions.mkdir(parents=True)
        submission_id = "00000000-0000-0000-0000-000000000000"
        (submissions / f"{submission_id}.json").write_text(
            json.dumps(
                {
                    "repo": str(repo.resolve()),
                    "project": "moonmind",
                    "image": IMAGE,
                    "inputs": {
                        "image": {
                            "repository": "ghcr.io/moonladderstudios/moonmind",
                            "reference": DIGEST,
                        },
                        "sourceRevision": revision,
                        "reason": "legacy-era submission",
                    },
                    "context": {"idempotency_key": f"host-update:{submission_id}"},
                }
            )
        )
        args = ["--resume", submission_id]

    assert update.cli(args) == 1

    err = capsys.readouterr().err
    assert "controller" in err.lower()
    assert "no other updater was started" in err
    assert started == []
    assert not any(
        item[1] == "run" or "deployment_release" in " ".join(item)
        for item in commands
    )
    assert not any("up" in item for item in commands)


def test_dry_run_reports_the_controller_route_without_installing(
    fresh_install: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = fresh_install
    calls: list[list[str]] = []

    def inspect(args, **kwargs):
        calls.append(list(args))
        return ""

    monkeypatch.setattr(update, "run", inspect)
    assert update.main(["--repo", str(repo), "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["executionOwner"] == "standalone-controller"
    assert plan["controllerInstalled"] is False
    assert plan["controllerImageSource"] == (
        "ghcr.io/moonladderstudios/moonmind-controller:sha-<fetched-commit>"
    )
    assert calls == [["git", "check-ref-format", "--branch", "main"]]
    assert not (repo / "deploy").exists()


def test_legacy_direct_option_is_retired(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        update.main(["--repo", str(tmp_path), "--legacy-direct", "--dry-run"])
    assert exc_info.value.code == 2
