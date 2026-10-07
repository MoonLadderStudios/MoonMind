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


def test_legacy_direct_is_refused_once_a_controller_owns_the_deployment(
    installed: Callable[..., tuple[Path, InProcessController]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, controller = installed()
    (repo / "deploy/state/controller/operations").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        update, "_submit_legacy_direct", lambda *a, **k: pytest.fail("second updater")
    )
    with pytest.raises(RuntimeError, match="Refusing --legacy-direct"):
        update._submit_release(
            {"project": "moonmind", "image": IMAGE, "inputs": {}, "context": {}},
            repo,
            controller_url=f"http://127.0.0.1:{controller.port}",
            secret_file=None,
            legacy_direct=True,
        )
    assert controller.applied == []
