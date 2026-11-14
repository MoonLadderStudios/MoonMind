"""Fresh host submission through real bootstrap and controller HTTP lifecycle.

Docker transport and the application applier are simulated, not application
readiness: these tests do not establish that a live full MoonMind stack boots.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.deployment_controller import (
    InProcessController, forget_controller_modules, load_controller_modules,
)

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "ghcr.io/moonladderstudios/moonmind@sha256:" + "a" * 64


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    load_controller_modules(monkeypatch)
    bootstrap = load("first_install_bootstrap", ROOT / "deploy/controller/bootstrap.py")
    update = load("first_install_update", ROOT / ".agents/skills/update-moonmind/scripts/update_release.py")
    for name in ("MOONMIND_CONTROLLER_URL", "MOONMIND_CONTROLLER_SECRET_FILE",
                 "MOONMIND_CONTROLLER_SECRET", "MOONMIND_CONTROLLER_MANAGED", "COMPOSE_FILE"):
        monkeypatch.delenv(name, raising=False)
    repo = tmp_path / "deployment"
    repo.mkdir()
    (repo / "docker-compose.yaml").write_text("services:\n  api:\n    image: release\n")
    state = repo / "deploy/state/controller"
    events, controllers = [], []
    networks = set()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(update, "_sleep", lambda _: None)
    monkeypatch.setattr(bootstrap.mounts_mod, "resolve_bind_source", lambda value: value)
    server = importlib.import_module("server")
    make_server = server.make_http_server

    def make_bound_server(host, port, app):
        return make_server(host, bootstrap.load_identity(state)["port"], app)

    monkeypatch.setattr(server, "make_http_server", make_bound_server)

    def compose_up(directory, project, *args):
        identity = bootstrap.load_identity(directory)
        assert identity["targetNetwork"] in networks
        events.append("controller-up")
        secret = (directory / "secrets/controller-bearer").read_text().strip()
        controllers.append(InProcessController(directory, secret=secret))
        return 0

    monkeypatch.setattr(bootstrap, "_compose_up", compose_up)

    def docker(args, **kwargs):
        events.append(list(args))
        assert args[:2] == ["docker", "network"]
        if args[2] == "inspect":
            return subprocess.CompletedProcess(args, 0 if args[-1] in networks else 1, "[]", "")
        assert args[2] == "create" and "--internal" in args
        networks.add(args[-1])
        return subprocess.CompletedProcess(args, 0, "network-id", "")

    monkeypatch.setattr(bootstrap.subprocess, "run", docker)

    def host_run(args, **kwargs):
        if args[0] == sys.executable and str(args[1]).endswith("bootstrap.py"):
            # Execute the shipped lifecycle rather than pretending install/start
            # succeeded. Source acquisition is stubbed separately below.
            events.append(args[2])
            assert bootstrap.main(args[2:], env={}) == 0
            return ""
        events.append(list(args))
        if args[:2] == ["docker", "pull"]:
            return ""
        if args[:3] == ["docker", "image", "inspect"]:
            return json.dumps([{"RepoDigests": [IMAGE], "Config": {"Labels": {}}}])
        if args[:3] == ["docker", "compose", "config"]:
            return json.dumps({"name": "fresh-app", "services": {"api": {}}})
        if args[:2] == ["docker", "ps"]:
            return ""
        raise AssertionError(f"unexpected host command: {args}")

    monkeypatch.setattr(update, "run", host_run)
    monkeypatch.setattr(update, "_controller_sources", lambda *a, **k: {
        "bootstrap.py": (ROOT / "deploy/controller/bootstrap.py").read_text(),
    })
    yield update, bootstrap, repo, state, events, controllers, networks
    for controller in controllers:
        controller.close()
    forget_controller_modules()


def test_fresh_host_installs_network_and_controller_before_submission(lifecycle):
    update, bootstrap, repo, state, events, controllers, networks = lifecycle
    assert not state.exists()
    assert not (repo / ".env").exists()
    assert not networks

    assert update.main([]) == 0

    assert events.index("install") < events.index("start") < events.index("controller-up")
    created = next(i for i, event in enumerate(events)
                   if isinstance(event, list) and event[:3] == ["docker", "network", "create"])
    assert events.index("start") < created < events.index("controller-up")
    assert len(controllers) == 1
    controller = controllers[0]
    assert len(controller.applied) == 1
    operation = controller.store.load(controller.applied[0])
    assert operation["status"] == "succeeded"
    assert operation["installed"]["image"] == IMAGE
    assert operation["target"]["project"] == "fresh-app"
    assert operation["target"]["services"] == ["api"]
    assert "envFile" not in operation["target"]
    assert not (repo / ".env").exists()
    identity = bootstrap.load_identity(state)
    assert identity["targetProject"] == "fresh-app"
    assert identity["targetNetwork"] in networks
    assert IMAGE in (state / "controller-compose.yaml").read_text()
    assert bootstrap.load_controller_image(state)["verified"] is True
    submissions = list((repo / "deploy/state/release-submissions").glob("*.json"))
    assert len(submissions) == 1
    submission = json.loads(submissions[0].read_text())
    assert operation["operationId"] == update._controller_operation_id(submission["context"])


def test_stopped_controller_with_pending_work_keeps_recovery_authority(lifecycle):
    """No reachable controller does not mean an existing deployment is fresh."""
    update, bootstrap, repo, state, events, controllers, networks = lifecycle
    options = ["--repo", str(repo), "--state-dir", str(state),
               "--target-project", "fresh-app", "--image", IMAGE]
    assert bootstrap.main(["install", *options], env={}) == 0
    store = bootstrap.record_mod.OperationStore(state)
    operation = store.begin(stack="moonmind", desired_image=IMAGE,
                            source_revision="retained", operation_id="retained-operation")
    before = {path: path.read_bytes() for path in state.rglob("*") if path.is_file()}
    events.clear()

    # Execute the actual host recovery owner without a listening endpoint.
    # Refusal must occur before network creation or a controller Compose up.
    with pytest.raises(bootstrap.ActiveOperationError, match="current deployment operation"):
        bootstrap.main(["ensure", *options], env={})

    assert not controllers
    assert not networks
    assert "controller-up" not in events
    assert all(path.read_bytes() == content for path, content in before.items())
    assert store.load(operation["operationId"])["status"] == "pending"
